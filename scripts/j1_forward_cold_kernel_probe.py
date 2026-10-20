"""Offline private alpha.11 protocol probe; no credentials or model turns.

This is a native protocol capability rehearsal, not authenticated preparation
or release acceptance. It cannot satisfy a production kernel v2 proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import j1_forward_cold_kernel as kernel
from scripts import j1_forward_cold_kernel_rpc as transport
from scripts import j1_forward_cold_native as native


def _write(path, body):
    raw = transport.canonical(body)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def _private_rollout(path, codex, thread_id):
    if path is None:
        return None
    path = Path(path)
    transport.require(
        path.is_relative_to(codex / "sessions") and ".." not in path.parts,
        "probe_rollout_outside_home",
    )
    try:
        physical = path.resolve(strict=True)
    except FileNotFoundError:
        return None
    transport.require(
        physical == path and path.stat().st_uid == os.getuid(), "probe_rollout_custody"
    )
    before = kernel.file_digest(path, limit=2 * 1024**2)
    raw = path.read_bytes()
    transport.require(
        hashlib.sha256(raw).hexdigest() == before["sha256"]
        and kernel.file_digest(path, limit=2 * 1024**2) == before,
        "probe_rollout_changed",
    )
    lines = [transport.decode(line) for line in raw.splitlines()]
    transport.require(
        len(lines) == 1
        and lines[0].get("type") == "session_meta"
        and lines[0].get("payload", {}).get("id") == thread_id,
        "probe_rollout_not_metadata_only",
    )
    return raw


def run(binary, output):
    started = time.monotonic()
    binary, output = Path(binary).resolve(strict=True), Path(output)
    transport.require(
        sys.platform.startswith("linux") and hasattr(os, "pidfd_open"), "probe_linux_required"
    )
    transport.require(
        kernel.file_digest(binary)["sha256"] == kernel.BINARY_SHA256, "probe_binary_pin"
    )
    transport.require(
        output.is_absolute() and not output.exists() and not output.is_symlink(),
        "probe_output_fresh",
    )
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    output.mkdir(mode=0o700)
    # The namespace is mandatory, rather than an assertion in the report.
    test = subprocess.run(
        ["/usr/bin/unshare", "--net", "--", "/usr/bin/true"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=3,
        check=False,
    )
    transport.require(test.returncode == 0, "probe_network_isolation_unavailable")
    with tempfile.TemporaryDirectory(prefix="autostop-cold-kernel-probe-", dir="/tmp") as temporary:
        root = Path(temporary)
        home, cwd = root / "home", root / "source"
        home.mkdir(mode=0o700)
        cwd.mkdir(mode=0o700)
        codex = home / ".codex"
        codex.mkdir(mode=0o700)
        (codex / "config.toml").write_text(
            'cli_auth_credentials_store="file"\n[analytics]\nenabled=false\n'
        )
        endpoint = root / "app.sock"
        env = {
            "HOME": str(home),
            "CODEX_HOME": str(codex),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        }
        argv = [
            str(binary),
            "app-server",
            "--remote-control",
            "--listen",
            "unix://" + str(endpoint),
            "--managed-daemon",
            "-c",
            "analytics.enabled=false",
            "-c",
            'cli_auth_credentials_store="file"',
        ]
        _write(
            output / "kernel-start-intent.json", {"argv": argv, "home": str(home), "model_turns": 0}
        )
        child = subprocess.Popen(
            ["/usr/bin/unshare", "--net", "--", *argv],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        descriptor = os.pidfd_open(child.pid, 0)
        rpc = None
        receipt = {
            "scope": "private_offline_protocol_only",
            "production_release_accepted": False,
            "authenticated_preparation": False,
            "model_turns_sent": 0,
            "auth_copied": False,
            "binary_ref": kernel.file_digest(binary),
            "network_namespace_isolated": True,
        }
        try:
            deadline = time.monotonic() + 20
            while not endpoint.is_symlink():
                transport.require(
                    time.monotonic() < deadline and child.poll() is None, "probe_listener_timeout"
                )
                time.sleep(0.02)
            actor = native.actor(child.pid)
            rpc = transport.RPC(endpoint, deadline)
            transport.require(
                list(rpc.peer) == [child.pid, actor["uid"], actor["gid"]], "probe_peer"
            )
            transport.require(rpc.initialized.get("codexHome") == str(codex), "probe_fresh_home")
            account = rpc.call("account/read", {"refreshToken": False})
            transport.require(
                account.get("account") is None and account.get("requiresOpenaiAuth") is True,
                "probe_unexpected_auth",
            )
            receipt["account"] = {"account": None, "requiresOpenaiAuth": True}
            # Diagnostic-only one-shot. This function is never the production
            # kernel preparation path, which rejects absent authentication.
            params = {
                "cwd": str(cwd),
                "ephemeral": False,
                "sandbox": "read-only",
                "approvalPolicy": "never",
            }
            intent_ref = _write(
                output / "thread-start-intent.json",
                {"actor": actor, "method": "thread/start", "params": params},
            )
            created = rpc._exchange("thread/start", params)
            thread = kernel._thread(created, None, str(cwd))
            _write(output / "thread-created.json", created)
            loaded = rpc.call("thread/loaded/list", {"limit": 100})
            kernel._loaded(loaded, thread["id"])
            _write(output / "thread-loaded.json", loaded)
            summary = rpc.call("thread/read", {"threadId": thread["id"], "includeTurns": False})
            kernel._thread(summary, thread["id"], str(cwd))
            _write(output / "thread-summary.json", summary)
            raw = _private_rollout(thread.get("path"), codex, thread["id"])
            lines = [] if raw is None else [transport.decode(line) for line in raw.splitlines()]
            receipt.update(
                {
                    "actor": actor,
                    "socket": {**kernel.socket_identity(endpoint), "peer": list(rpc.peer)},
                    "intent_ref": intent_ref,
                    "owned_thread_id": thread["id"],
                    "capability": kernel.CAPABILITY,
                    "metadata_file_exists": raw is not None,
                    "history_persistence_guaranteed": False,
                    "rollout_records": len(lines),
                    "rollout_types": [row["type"] for row in lines],
                    "full_history_api_verified": False,
                }
            )
            transport.require(native.actor(child.pid) == actor, "probe_actor_changed")
        finally:
            if rpc is not None:
                rpc.close()
            if child.poll() is None:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL, None, 0)
            transport.require(
                bool(select.select([descriptor], [], [], 5)[0]), "probe_child_cleanup"
            )
            child.wait(timeout=5)
            os.close(descriptor)
            receipt["appserver_reaped"] = True
            receipt["wall_seconds"] = time.monotonic() - started
            _write(output / "receipt.json", receipt)
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    receipt = run(args.binary, args.output)
    print(
        json.dumps(
            {
                key: receipt[key]
                for key in (
                    "scope",
                    "production_release_accepted",
                    "authenticated_preparation",
                    "appserver_reaped",
                    "wall_seconds",
                )
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
