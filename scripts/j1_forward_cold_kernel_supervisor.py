"""Fixed future-window supervisor for one fresh, code-pinned Codex kernel.

This entry point is written for a future authorized production window. Local
tests use private boundaries and never call its fixed production main().
Failure stops only the child owned through a held pidfd. An accepted runtime
keeps the same PID and thread; there is no automatic restart or old adoption.
"""

from __future__ import annotations

import argparse
import importlib
import os
import select
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

BOOTSTRAP_SHA256 = "dff9c7f34d4945cc64dfa5dd2d6297f0ce96b743b870c0fdb6407f4c061fbeda"


def _bootstrap():
    import hashlib
    import importlib.util

    here = Path(__file__).absolute()
    if (
        here
        != Path(
            "/usr/local/libexec/autostop-crm-forward-cold/v1/j1_forward_cold_kernel_supervisor.py"
        )
        or not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.dont_write_bytecode
        or os.geteuid() != 0
    ):
        raise ValueError("kernel_bootstrap_boundary")
    path = here.with_name("j1_forward_cold_kernel_bootstrap.py")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or info.st_size > 2 * 1024**2
        ):
            raise ValueError("kernel_bootstrap_file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(2 * 1024**2 + 1)
        if hashlib.sha256(raw).hexdigest() != BOOTSTRAP_SHA256:
            raise ValueError("kernel_bootstrap_source_changed")
        spec = importlib.util.spec_from_loader("cold_kernel_bootstrap", loader=None)
        module = importlib.util.module_from_spec(spec)
        exec(compile(raw, str(path), "exec"), module.__dict__)
        module.install(here, sys.argv[1:])
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    _bootstrap()

cold = importlib.import_module("scripts.j1_forward_cold_authority")
kernel = importlib.import_module("scripts.j1_forward_cold_kernel")
native = importlib.import_module("scripts.j1_forward_cold_native")
io = importlib.import_module("scripts.j1_forward_io")

SEED_FIELDS = {
    "schema",
    "scope",
    "binding",
    "policy",
    "deployment_controller",
    "deployment_descriptor",
    "authorization_ref",
    "cache_view_ref",
    "source_ref",
    "rpc_ref",
    "protocol_index_ref",
    "context_ref",
}
AUTH_FIELDS = {"schema", "scope", "binding", "auth_sha256", "expires_ns"}
ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
CONFIG = b'cli_auth_credentials_store = "file"\n[analytics]\nenabled = false\n'


def paths(binding):
    return {
        "directory": native.STATE / "packages" / binding["attempt_id"] / "kernel",
        "home": native.STATE / "packages" / binding["attempt_id"] / "kernel/home",
        "socket": Path("/run/autostop-manager-cold")
        / binding["generation"]
        / "wake/app-server.sock",
    }


def environment(binding):
    home = paths(binding)["home"]
    return {
        **ENVIRONMENT,
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
    }


def appserver_argv(binding, executable):
    return [
        executable,
        "app-server",
        "--remote-control",
        "--listen",
        "unix://" + str(paths(binding)["socket"]),
        "--managed-daemon",
        "-c",
        "analytics.enabled=false",
        "-c",
        'cli_auth_credentials_store="file"',
    ]


def _directory(path, *, fresh=False):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        io.protected_directory(path.parent, private=True)
        path.mkdir(mode=0o700)
        io.fsync_directory(path.parent)
    elif fresh:
        raise io.Refusal("cold_kernel_home_already_exists")
    io.protected_directory(path, private=True)
    cold.need(stat.S_IMODE(path.lstat().st_mode) == 0o700, "kernel_directory_mode")


def validate_seed(reference, *, provider):
    seed = kernel._read(provider, reference)
    cold.fields(seed, SEED_FIELDS, "kernel_seed_fields")
    binding = seed["binding"]
    cold._binding(binding, now_ns=time.monotonic_ns())
    cold.need(
        seed["schema"] == "autostop.j1.cold-kernel-supervisor-seed.v1"
        and seed["scope"] == "production_native"
        and provider.scope == "production_native"
        and reference["path"] == str(paths(binding)["directory"] / "supervisor-seed.json")
        and socket.gethostname() == native.HOST
        and os.geteuid() == 0,
        "kernel_fixed_supervisor_boundary",
    )
    native.held_descriptor(
        seed["deployment_controller"],
        seed["deployment_descriptor"],
        binding["deployment_lock_identity"],
    )
    from scripts import j1_forward_cold_controller_native as parent

    _store, snapshot, context = parent.validate(binding["attempt_id"])
    cold.need(
        snapshot["state"]["execution_possible"] is True
        and snapshot["state"]["acceptance"]["pid"] == seed["deployment_controller"]["pid"]
        and snapshot["state"]["acceptance"]["start_ticks"]
        == seed["deployment_controller"]["start_ticks"]
        and context["deployment_lock_identity"] == binding["deployment_lock_identity"],
        "kernel_parent_entry_required",
    )
    cold.need(
        seed["deployment_controller"]["pid"] != os.getpid(), "kernel_separate_supervisor_required"
    )
    policy = seed["policy"]
    cold.need(
        policy["source_ref"] == seed["source_ref"]
        and policy["rpc_ref"] == seed["rpc_ref"]
        and policy["protocol_index_ref"] == seed["protocol_index_ref"]
        and policy["appserver_ref"]["sha256"] == kernel.BINARY_SHA256
        and policy["appserver_argv"] == appserver_argv(binding, policy["appserver_ref"]["path"])
        and policy["cwd"]
        == "/opt/autostop-manager-cold-releases/" + binding["candidate_manager_sha"] + "/source",
        "kernel_seed_fixed_profile",
    )
    for ref in (
        seed["source_ref"],
        seed["rpc_ref"],
        seed["protocol_index_ref"],
        policy["appserver_ref"],
    ):
        cold.need(kernel.file_digest(ref["path"]) == ref, "kernel_seed_source_changed")
    cold.need(
        Path(seed["source_ref"]["path"]).resolve(strict=True) == Path(__file__).resolve(strict=True)
        and Path(seed["rpc_ref"]["path"]).resolve(strict=True)
        == Path(kernel.transport.__file__).resolve(strict=True),
        "kernel_seed_unreviewed_entrypoint",
    )
    return seed


def fresh_home(seed, *, provider):
    """Copy only separately staged authorization; create all state from empty."""
    binding = seed["binding"]
    authorization = kernel._read(provider, seed["authorization_ref"])
    cold.fields(authorization, AUTH_FIELDS, "kernel_authorization_fields")
    credential_dir = native.CONFIG / "credentials" / binding["attempt_id"]
    cold.need(
        authorization["schema"] == "autostop.j1.cold-kernel-authorization.v1"
        and authorization["scope"] == "production_native"
        and authorization["binding"] == binding
        and type(authorization["expires_ns"]) is int
        and time.monotonic_ns() < authorization["expires_ns"] <= binding["deadline_ns"]
        and seed["authorization_ref"]["path"] == str(credential_dir / "authorization.json")
        and type(authorization["auth_sha256"]) is str
        and cold.HEX.fullmatch(authorization["auth_sha256"]),
        "kernel_authorization_scope",
    )
    # The old /root/.codex auth/session/config are never candidates here.
    auth = io.read_protected(credential_dir / "auth.json", limit=2 * 1024**2)
    cold.need(io.sha(auth) == authorization["auth_sha256"], "kernel_authorization_digest")
    cold.need(0 < len(auth) <= 2 * 1024**2, "kernel_authorization_size")
    parsed = io.document(auth)
    cold.need(type(parsed) is dict and bool(parsed), "kernel_authorization_json")
    selected = paths(binding)
    home = selected["home"]
    _directory(home, fresh=True)
    for relative in (".codex", ".cache", ".config"):
        _directory(home / relative, fresh=True)
    kernel._write_bytes(home / ".codex/config.toml", CONFIG)
    kernel._write_bytes(home / ".codex/auth.json", auth)
    home_ref = io.write_new(
        selected["directory"] / "fresh-home.json",
        {
            "schema": "autostop.j1.cold-kernel-home.v1",
            "scope": "production_native",
            "binding": binding,
            "home": str(home),
            "codex_home": str(home / ".codex"),
            "cwd": seed["policy"]["cwd"],
            "prestart_files": {"config.toml": io.sha(CONFIG), "auth.json": io.sha(auth)},
            "authorization_ref": seed["authorization_ref"],
            "created_ns": time.monotonic_ns(),
        },
    )
    cold.need(
        sorted(path.name for path in (home / ".codex").iterdir()) == ["auth.json", "config.toml"],
        "kernel_home_not_fresh",
    )
    return home_ref


def _notify_ready():
    address = os.environ.get("NOTIFY_SOCKET")
    cold.need(type(address) is str and address, "kernel_notify_socket_missing")
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.connect(address)
        client.sendall(b"READY=1")


def stop_owned(process, pidfd):
    if process.poll() is None:
        signal.pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)
    cold.need(bool(select.select([pidfd], [], [], 5)[0]), "kernel_child_cleanup_timeout")
    process.wait(timeout=5)


def verify_custody(reference, seed, measured, *, provider):
    """Only actual durable completion can transfer this already-running actor."""
    body = kernel._read(provider, reference)
    cold.fields(
        body,
        {
            "schema",
            "scope",
            "binding",
            "package_ref",
            "kernel_ref",
            "commit_ref",
            "cold_completion_ref",
        },
        "kernel_custody_fields",
    )
    binding = seed["binding"]
    cold.need(
        body["schema"] == "autostop.j1.cold-kernel-runtime-custody.v1"
        and body["scope"] == "production_native"
        and body["binding"] == binding
        and body["package_ref"]["path"] == str(native.package_path(binding["attempt_id"])),
        "kernel_custody_binding",
    )
    from scripts import j1_forward_cold_state as cold_state

    commit = kernel._read(provider, body["commit_ref"])
    complete = kernel._read(provider, body["cold_completion_ref"])
    package = kernel._read(provider, body["package_ref"])
    kernel_doc = kernel._read(provider, body["kernel_ref"])
    plan = commit["plan"]
    store = cold_state.ColdStore(Path("/var/lib/autostop-crm-forward/state"), plan["base_sha256"])
    cold.fields(
        complete,
        {"ok", "plan_ref", "commit_ref", "epoch_ref", "old_hold_released", "old_barriers_opened"},
        "kernel_completion_fields",
    )
    cold.need(
        store.head()["sha256"] == body["commit_ref"]["sha256"]
        and store.head()["record"] == commit
        and plan["attempt_id"] == binding["attempt_id"]
        and commit["schema"] == "autostop.j1.crm-forward-commit.v2"
        and package["binding"] == binding
        and package["kernel_ref"] == body["kernel_ref"]
        and kernel_doc["ready_ref"] == measured
        and complete["ok"] is True
        and complete["old_hold_released"] is False
        and complete["old_barriers_opened"] is False
        and complete["commit_ref"] == body["commit_ref"]
        and complete["plan_ref"]["sha256"] == io.sha(io.canonical(plan))
        and complete["epoch_ref"] == plan["dispatch_epoch_ref"]
        and plan["cold_authority_package_ref"] == body["package_ref"]
        and store.pending() is None
        and store._active_recovery() is None,
        "kernel_custody_durable_completion",
    )
    return body


def run(reference):
    provider = native.NativeProvider()
    seed = validate_seed(reference, provider=provider)
    binding, selected = seed["binding"], paths(seed["binding"])
    home_ref = fresh_home(seed, provider=provider)
    argv = seed["policy"]["appserver_argv"]
    _directory(selected["socket"].parent)
    cold.need(
        not selected["socket"].exists() and not selected["socket"].is_symlink(),
        "kernel_socket_preexists",
    )
    intent = selected["directory"] / "kernel-start-intent.json"
    io.write_new(
        intent,
        {
            "schema": "autostop.j1.cold-kernel-start-intent.v1",
            "binding": binding,
            "argv": argv,
            "home_ref": home_ref,
            "created_ns": time.monotonic_ns(),
        },
    )
    process = subprocess.Popen(
        argv,
        cwd=seed["policy"]["cwd"],
        env=environment(binding),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    pidfd = os.pidfd_open(process.pid, 0)
    adopted = False
    try:
        deadline = min(time.monotonic() + 20, binding["deadline_ns"] / 1e9)
        while not selected["socket"].is_symlink():
            cold.need(
                time.monotonic() < deadline and process.poll() is None, "kernel_listener_startup"
            )
            time.sleep(0.02)
        actor = native.actor(process.pid)
        supervisor = native.actor(os.getpid())
        cold.need(
            actor["exe_ref"] == seed["policy"]["appserver_ref"], "kernel_started_wrong_binary"
        )
        result = kernel.prepare_owned_thread(
            binding,
            seed["policy"],
            home_ref,
            provider=provider,
            directory=selected["directory"],
            socket_path=selected["socket"],
            appserver_actor=actor,
            supervisor_actor=supervisor,
            context_ref=seed["context_ref"],
        )
        ready_ref = io.write_new(
            selected["directory"] / "ready.json",
            {
                "schema": "autostop.j1.cold-kernel-ready.v2",
                "binding": binding,
                "phase": "ready",
                "actor": actor,
                "supervisor_actor": supervisor,
                **result,
            },
        )
        _notify_ready()
        custody = selected["directory"] / "runtime-custody.json"
        while True:
            cold.need(
                process.poll() is None and not select.select([pidfd], [], [], 0)[0],
                "kernel_child_exited",
            )
            if not adopted:
                if custody.exists():
                    verify_custody(io.reference(custody), seed, ready_ref, provider=provider)
                    adopted = True
                else:
                    cold.need(
                        time.monotonic_ns() < binding["deadline_ns"],
                        "kernel_not_adopted_before_deadline",
                    )
                    native.held_descriptor(
                        seed["deployment_controller"],
                        seed["deployment_descriptor"],
                        binding["deployment_lock_identity"],
                    )
            time.sleep(0.1)
    finally:
        stop_owned(process, pidfd)
        os.close(pidfd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args(argv)
    cold.need(cold._uuid(args.attempt), "kernel_attempt_uuid")
    path = native.STATE / "packages" / args.attempt / "kernel/supervisor-seed.json"
    try:
        run(io.reference(path))
    except (io.Refusal, kernel.transport.Deny, OSError, ValueError):
        print("cold_kernel_preparation_denied", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
