"""Read-only preservation proofs for the accepted warm Manager89 authority.

This observer never calls guard authorize/check/launch, imports Manager, opens a
database, or changes services. A new CRM image needs separate release evidence.
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
import stat
import subprocess
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from scripts import j1_forward_io
from scripts.j1_forward_io import (
    canonical,
    document,
    read_protected,
    reference,
    referenced,
    require,
    sha,
)

REVISION = "89af29082e0a6f72c5d85ae448acb6d4deec5d94"
HOST = "vps26457.mnogoweb.in"
SCOPE = "production_quiet_window"
SCHEMA = "autostop.crm.j1-forward-native-baseline.v1"
GUARD = Path("/usr/local/libexec/autostop-j1-candidate-guard/guard.py")
ORIGINAL = GUARD.with_name("guard.original15-d5275167987434df.py")
CAPSULE = Path("/etc/autostop-j1-candidate-guard/forward21.json")
DUTY = Path("/usr/local/libexec/autostop-crm-j1-duty/restore.py")
COMMIT = Path("/var/lib/autostop-crm-j1-duty/committed.json")
CACHE_POLICY = Path("/etc/autostop-j1-cache-view/policy.json")
CACHE = Path("/var/cache/autostop-j1/research.sqlite3")
WARM_CODEX = Path(
    "/root/.codex/packages/standalone/releases/0.163.0-alpha.11-x86_64-unknown-linux-musl/bin/codex"
)
SYSTEMD = Path("/etc/systemd/system")
PROC = Path("/proc")
PINS = {
    GUARD: "c5de603f10ba2cbe6c72249c9f38e662a5e812b9d6f84442277eaea4147c2975",
    ORIGINAL: "d5275167987434dffab4f81f0671c381c7a32f69e281fd8d4f8644715f3427c6",
    CAPSULE: "432e097d7dfd50ab795fe508c3e84317cc9c80596bb4fbc8432decd7944d33ee",
    DUTY: "48cc13f80e3e3cb217a8068bf3a93882259e3b308a613ce3be436bcb5609708d",
    COMMIT: "a4ba90f7a418d37a2b9bffa4e4555b63f3e4d7961e7ff4d29a59fcf7d8667d85",
}
NATIVE_UNITS = tuple(
    sorted(
        (
            "autostop-j1.service",
            "autostop-manager-mcp.service",
            "autostop-manager-scheduler.service",
            "autostop-work-telegram.service",
            "autostop-telegram.service",
            "autostop-codex-wake.service",
        )
    )
)
ALL_UNITS = tuple(
    sorted((*NATIVE_UNITS, "autostop-codex-start.service", "autostop-j1-browser.service"))
)
DENY_NAME = "zz-j1-forward-closed.conf"
DENY_BYTES = (
    b"[Service]\nExecCondition=/usr/bin/false\nExecStartPre=\nExecStartPost=\n"
    b"ExecStop=\nExecStopPost=\nExecReload=\n"
)
SHOW_FIELDS = (
    "Id,Names,LoadState,Type,NotifyAccess,FragmentPath,SourcePath,NeedDaemonReload,"
    "ActiveState,SubState,UnitFileState,MainPID,ControlGroup,ExecCondition,ExecStart,"
    "ExecStartPre,ExecStartPost,ExecStop,ExecStopPost,ExecReload"
)
COMMAND_FIELDS = frozenset(
    (
        "ExecCondition",
        "ExecStart",
        "ExecStartPre",
        "ExecStartPost",
        "ExecStop",
        "ExecStopPost",
        "ExecReload",
    )
)


def _expired(deadline: float) -> None:
    require(time.monotonic() < deadline, "native_observation_deadline")


def _module(path: Path, expected: str) -> ModuleType:
    raw = read_protected(path)
    require(sha(raw) == expected, "native_code_pin_changed")
    module = ModuleType("_verified_" + path.stem.replace("-", "_"))
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    require(sha(read_protected(path)) == expected, "native_code_changed_during_load")
    return module


def _absent(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def _load_authority(deadline: float) -> dict[str, Any]:
    require(
        os.name == "posix" and os.geteuid() == 0 and socket.gethostname() == HOST,
        "native_host_required",
    )
    for path, digest in PINS.items():
        require(sha(read_protected(path)) == digest, "native_predecessor_pin_changed")
    wrapper = _module(GUARD, PINS[GUARD])
    guard = _module(ORIGINAL, PINS[ORIGINAL])
    wrapper.END = guard.END = deadline
    capsule = wrapper.load_forward_capsule(PINS[CAPSULE], require_activation=True)
    policy = guard.policy_document(
        referenced(capsule["policy"]), capsule["policy"]["sha256"], PINS[ORIGINAL]
    )
    require(
        policy["revision"] == REVISION and set(policy["bindings"]) == set(NATIVE_UNITS),
        "native_profile_scope",
    )
    require(
        _absent(Path(capsule["armed"]["path"]).with_name("arming-pending.json")),
        "native_arm_pending",
    )
    intent = document(referenced(capsule["original_duty_intent"]))
    require(
        intent["manager_revision"] == REVISION and type(intent["inbound_enabled"]) is bool,
        "native_intent_scope",
    )
    preparation = Path(intent["preparation_path"])
    require(
        sha(read_protected(preparation)) == intent["preparation_sha256"],
        "native_preparation_changed",
    )
    closure_ref = {
        "path": str(preparation.with_name("closure-acceptance.json")),
        "sha256": intent["closure_sha256"],
    }
    closure = document(referenced(closure_ref))
    require(closure["daemon_mode"] == "accepted_warm", "native_warm_mode_required")
    lifecycle = document(referenced(closure["lifecycle"]))
    require(lifecycle["daemon_mode"] == "accepted_warm", "native_warm_lifecycle_required")
    warm_ref = lifecycle["daemon_identity_receipt"]
    warm = document(referenced(warm_ref))
    require(
        warm["actual_cache_read_only"] is True and warm["host_cache_read_only"] is True,
        "native_warm_cache_proof",
    )
    refs = {str(path): reference(path) for path in PINS}
    for ref in (
        *wrapper.REFS.values(),
        closure_ref,
        closure["lifecycle"],
        warm_ref,
        warm["cache_view_state"],
    ):
        referenced(ref)
        refs[ref["path"]] = ref
    refs[str(CACHE_POLICY)] = reference(CACHE_POLICY)
    duty_ref = capsule["original_duty_controller"]
    duty = _module(Path(duty_ref["path"]), duty_ref["sha256"])
    duty.__file__ = str(DUTY)
    original_read = duty.read_file
    relocation = {GUARD: ORIGINAL, DUTY: Path(duty_ref["path"])}
    duty.read_file = lambda path: original_read(relocation.get(path, path))
    require(_absent(COMMIT.with_name("commit-pending.json")), "native_duty_commit_pending")
    checked_intent = duty.load_intent(capsule["original_duty_intent"]["sha256"])
    require(checked_intent == intent, "native_duty_intent_changed")
    duty.committed_matches(duty.expected_commit(intent, capsule["original_duty_intent"]["sha256"]))
    for name in ("preparation.json", "handoff.json"):
        refs[str(preparation.with_name(name))] = reference(preparation.with_name(name))
    _expired(deadline)
    return {
        "wrapper": wrapper,
        "guard": guard,
        "capsule": capsule,
        "policy": policy,
        "intent": intent,
        "warm": warm,
        "refs": refs,
        "index": guard.source_index(referenced(capsule["index"])),
    }


def _systemd(unit: str, deadline: float) -> dict[str, str]:
    _expired(deadline)
    result = subprocess.run(
        ["/usr/bin/systemctl", "show", unit, "--no-pager", "--property=" + SHOW_FIELDS],
        capture_output=True,
        check=False,
        timeout=min(8, max(0.001, deadline - time.monotonic())),
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    require(
        result.returncode == 0 and len(result.stdout) < 65536, "native_systemd_observation_failed"
    )
    rows = [line.split("=", 1) for line in result.stdout.decode("utf-8", "strict").splitlines()]
    require(all(len(row) == 2 for row in rows), "native_systemd_shape")
    fields = {}
    for key, value in rows:
        if key in COMMAND_FIELDS:
            _commands({key: value}, key)
        if key in fields:
            require(key in COMMAND_FIELDS and fields[key] and value, "native_systemd_shape")
            value = fields[key] + " " + value
        fields[key] = value
    require(
        fields.get("Id") == unit
        and fields.get("LoadState") == "loaded"
        and fields.get("NeedDaemonReload") == "no",
        "native_unit_not_loaded_exactly",
    )
    return fields


def _commands(fields: dict[str, str], key: str) -> list[str]:
    raw = fields.get(key, "")
    commands = re.findall(r"argv\[\]=(.*?) ;", raw)
    require(bool(commands) == bool(raw), "native_loaded_command_shape")
    return commands


def _closure_record(owned: dict | None) -> dict[str, dict]:
    if owned is None:
        return {}
    expected = {
        "dropins": [
            {
                "unit": unit,
                "path": str(SYSTEMD / (unit + ".d") / DENY_NAME),
                "sha256": sha(DENY_BYTES),
            }
            for unit in ALL_UNITS
        ],
        "bytes_sha256": sha(DENY_BYTES),
    }
    require(owned == expected, "native_owned_closure_shape")
    for item in owned["dropins"]:
        require(
            referenced({k: item[k] for k in ("path", "sha256")}) == DENY_BYTES,
            "native_owned_closure_changed",
        )
    return {item["unit"]: item for item in owned["dropins"]}


def _units_with_closure(guard: ModuleType, binding: dict, unit: str, policy_hash: str) -> None:
    """Check the original inventory plus exactly one fixed owned deny file."""
    for record in binding["unit_files"]:
        require(
            sha(read_protected(Path(record["path"]))) == record["sha256"],
            "native_unit_file_changed",
        )
    own = Path(binding["guard_dropin"])
    require(
        own.name == "99-j1-candidate-guard.conf" and own.parent == SYSTEMD / (unit + ".d"),
        "native_guard_dropin_path",
    )
    require(
        read_protected(own) == guard.unit_dropin(unit, policy_hash).encode(),
        "native_guard_dropin_changed",
    )
    require(
        any(record["path"] == str(own.parent) for record in binding["dropin_directories"]),
        "native_dropin_inventory_missing",
    )
    for record in binding["dropin_directories"]:
        folder = Path(record["path"])
        if record["files"] is None:
            require(_absent(folder), "native_unknown_dropin_directory")
            continue
        guard.directory(folder)
        expected = set(record["files"])
        if folder == own.parent:
            expected.add(DENY_NAME)
        actual = {path.name for path in folder.iterdir() if path.suffix == ".conf" and path != own}
        require(actual == expected, "native_unknown_dropin")
        for name, digest in record["files"].items():
            require(sha(read_protected(folder / name)) == digest, "native_dropin_changed")


def _unsupported_units(authority: dict, closed: bool) -> None:
    """The two denied roles have no policy binding but retain exact drop-ins."""
    guard = authority["guard"]
    pinned = {
        record["path"]: record["sha256"]
        for binding in authority["policy"]["bindings"].values()
        for record in binding["unit_files"]
    }
    for unit in set(ALL_UNITS) - set(NATIVE_UNITS):
        for root in (
            SYSTEMD,
            Path("/run/systemd/system"),
            Path("/usr/lib/systemd/system"),
            Path("/usr/local/lib/systemd/system"),
        ):
            folder = root / (unit + ".d")
            expected = {
                Path(path).name: digest
                for path, digest in pinned.items()
                if Path(path).parent == folder
            }
            if root != SYSTEMD:
                require(not expected and _absent(folder), "native_denied_role_overlay")
                continue
            guard.directory(folder)
            own = folder / "99-j1-candidate-guard.conf"
            require(
                read_protected(own)
                == guard.unit_dropin(unit, authority["capsule"]["policy"]["sha256"]).encode(),
                "native_denied_role_guard_changed",
            )
            names = set(expected) | {own.name}
            if closed:
                names.add(DENY_NAME)
            require(
                {path.name for path in folder.iterdir() if path.suffix == ".conf"} == names,
                "native_denied_role_dropin_changed",
            )
            for name, digest in expected.items():
                require(
                    sha(read_protected(folder / name)) == digest, "native_denied_role_file_changed"
                )


def _loaded(unit: str, fields: dict, authority: dict, closed: bool) -> None:
    policy_hash = authority["capsule"]["policy"]["sha256"]
    prefix = f"/usr/bin/python3 -I -S {GUARD}"
    conditions = [f"{prefix} check --policy-sha256 {policy_hash} --unit {unit}"]
    if closed:
        conditions.append("/usr/bin/false")
    require(
        _commands(fields, "ExecCondition") == conditions
        and _commands(fields, "ExecStart")
        == [f"{prefix} launch --policy-sha256 {policy_hash} --unit {unit}"],
        "native_loaded_guard_changed",
    )
    for key in ("ExecStartPre", "ExecStop", "ExecStopPost", "ExecReload"):
        require(not fields.get(key), "native_loaded_hook_changed")
    post = []
    if unit == "autostop-work-telegram.service" and not closed:
        intent_hash = authority["capsule"]["original_duty_intent"]["sha256"]
        post = [f"/usr/bin/python3 -I -S {DUTY} restore --intent-sha256 {intent_hash}"]
    require(_commands(fields, "ExecStartPost") == post, "native_duty_hook_changed")
    if unit in NATIVE_UNITS:
        context = authority["capsule"]["profiles"][unit]["unit_context"]
        require({key: fields.get(key) for key in context} == context, "native_unit_context_changed")
    if closed or unit not in NATIVE_UNITS:
        require(
            fields.get("ActiveState") == "inactive"
            and fields.get("SubState") == "dead"
            and fields.get("MainPID") == "0",
            "native_closed_role_still_active",
        )


def _proc(pid: str, name: str, limit: int = 2 * 1024 * 1024) -> bytes:
    require(pid in {"1", "self"} or re.fullmatch(r"[1-9][0-9]*", pid), "native_pid_invalid")
    with (PROC / pid / name).open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "native_process_observation_too_large")
    return raw


def _start(pid: str) -> str:
    raw = _proc(pid, "stat").decode()
    require(") " in raw, "native_process_stat_shape")
    fields = raw.rsplit(") ", 1)[1].split()
    require(len(fields) >= 20 and fields[19].isdigit(), "native_process_stat_shape")
    return fields[19]


def _identity(path: Path) -> list[int]:
    info = path.lstat()
    return [info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]


def _metadata(info: os.stat_result) -> list[int]:
    return [
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    ]


def _file_digest(path: Path, deadline: float, *, owner_uid: int | None = None) -> str:
    before = path.lstat()
    require(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == (j1_forward_io.ROOT_UID if owner_uid is None else owner_uid)
        and before.st_nlink == 1
        and not before.st_mode & 0o022,
        "native_hash_file_unprotected",
    )
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    with os.fdopen(fd, "rb") as stream:
        require(
            _metadata(os.fstat(stream.fileno())) == _metadata(before), "native_hash_file_replaced"
        )
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            _expired(deadline)
            digest.update(block)
        require(
            _metadata(os.fstat(stream.fileno())) == _metadata(before), "native_hash_file_changed"
        )
    require(_metadata(path.lstat()) == _metadata(before), "native_hash_path_replaced")
    return digest.hexdigest()


def _cache_mount(pid: str, expected: str) -> dict:
    matches = []
    for line in _proc(pid, "mountinfo").decode().splitlines():
        before, separator, after = line.partition(" - ")
        fields = before.split()
        if len(fields) >= 6 and fields[4] == CACHE.parent.as_posix():
            options = set(fields[5].split(","))
            require(separator and len(options & {"ro", "rw"}) == 1, "native_cache_mount_mode")
            matches.append(
                {
                    "id": fields[0],
                    "parent": fields[1],
                    "device": fields[2],
                    "root": fields[3],
                    "mountpoint": fields[4],
                    "mode": "ro" if "ro" in options else "rw",
                    "filesystem": after.split()[0],
                }
            )
    if expected == "native":
        require(len(matches) == 2, "native_cache_mount_ambiguous")
        lower = next((item for item in matches if item["mode"] == "ro"), None)
        upper = next((item for item in matches if item["mode"] == "rw"), None)
        require(lower and upper and upper["parent"] == lower["id"], "native_cache_mount_stack")
        require(
            all(lower[key] == upper[key] for key in ("device", "root", "mountpoint", "filesystem")),
            "native_cache_mount_origin_changed",
        )
        require(
            _identity(PROC / pid / "root" / CACHE.relative_to("/")) == _identity(CACHE),
            "native_process_cache_identity_changed",
        )
        # The original, hash-bound BindPaths overlays the host RO view with
        # the private RW view in each service namespace, including clients.
        return {
            "layers": [
                {k: v for k, v in item.items() if k not in {"id", "parent"}}
                for item in (lower, upper)
            ]
        }
    require(len(matches) == 1, "native_cache_mount_ambiguous")
    require(matches[0]["mode"] == expected, "native_cache_mount_mode")
    return {key: value for key, value in matches[0].items() if key not in {"id", "parent"}}


def _cache(authority: dict) -> dict:
    policy = document(read_protected(CACHE_POLICY))
    require(
        policy.get("schema") == "autostop.j1.cache-view-policy.v1" and policy.get("host") == HOST,
        "native_cache_policy_scope",
    )
    require(
        _identity(CACHE) == policy["database_identity"]
        and _identity(CACHE.parent) == policy["cache_identity"],
        "native_cache_identity_changed",
    )
    proof = {"path": policy["native_proof_path"], "sha256": policy["native_proof_sha256"]}
    referenced(proof)
    return {
        "policy": reference(CACHE_POLICY),
        "native_proof": proof,
        "database_identity": _identity(CACHE),
        "parent_identity": _identity(CACHE.parent),
        "pid1": _cache_mount("1", "ro"),
        "self": _cache_mount("self", "ro"),
        "state": authority["warm"]["cache_view_state"],
    }


def _warm_executable(path: Path, deadline: float) -> dict:
    owner = j1_forward_io.ROOT_UID
    if path == WARM_CODEX:
        # The accepted archive retains UID1001 for bin/ and codex. The root
        # home is the existing access boundary; do not rewrite its ownership.
        for parent in reversed(path.parents):
            info = parent.lstat()
            expected_uid = 1001 if parent == path.parent else j1_forward_io.ROOT_UID
            require(
                stat.S_ISDIR(info.st_mode)
                and info.st_uid == expected_uid
                and not info.st_mode & 0o022,
                "native_warm_executable_ancestor_changed",
            )
            if parent == Path("/root"):
                require(stat.S_IMODE(info.st_mode) == 0o700, "native_warm_private_root_changed")
            if parent == path.parent:
                require(stat.S_IMODE(info.st_mode) == 0o755, "native_warm_bin_mode_changed")
        require(stat.S_IMODE(path.lstat().st_mode) == 0o755, "native_warm_exe_mode_changed")
        owner = 1001
    digest = _file_digest(path, deadline, owner_uid=owner)
    return {"sha256": digest, "identity": _identity(path), "metadata": _metadata(path.lstat())}


def _mapped_executable(pid: str, expected: dict, deadline: float) -> None:
    # Follow only this kernel-owned magic link after checking the PID birth and
    # exact accepted readlink target. Its open descriptor must be the same file.
    digest = hashlib.sha256()
    with (PROC / pid / "exe").open("rb") as stream:
        require(
            _metadata(os.fstat(stream.fileno())) == expected["metadata"],
            "native_warm_mapped_executable_changed",
        )
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            _expired(deadline)
            digest.update(block)
        require(
            _metadata(os.fstat(stream.fileno())) == expected["metadata"]
            and digest.hexdigest() == expected["sha256"],
            "native_warm_mapped_executable_changed",
        )


def _warm_actors(authority: dict, deadline: float) -> list[dict]:
    require(
        (PROC / "sys/kernel/random/boot_id").read_text().strip() == authority["warm"]["boot_id"],
        "native_warm_boot_changed",
    )
    actors = []
    executables = {}
    for accepted in authority["warm"]["actors"]:
        pid = str(accepted["pid"])
        start = _start(pid)
        actual = {
            "pid": int(pid),
            "start_ticks": int(start),
            "argv_sha256": sha(_proc(pid, "cmdline")),
            "cgroup": _proc(pid, "cgroup").decode().strip(),
            "exe": os.readlink(PROC / pid / "exe"),
            "mount_namespace": os.readlink(PROC / pid / "ns/mnt"),
        }
        require(actual == {key: accepted[key] for key in actual}, "native_warm_actor_changed")
        executable = Path(actual["exe"])
        if actual["exe"] not in executables:
            executables[actual["exe"]] = _warm_executable(executable, deadline)
        _mapped_executable(pid, executables[actual["exe"]], deadline)
        status = dict(
            line.split(":", 1) for line in _proc(pid, "status").decode().splitlines() if ":" in line
        )
        actual.update(
            {
                "exe_sha256": executables[actual["exe"]]["sha256"],
                "exe_identity": executables[actual["exe"]]["identity"][:2],
                "exe_file_identity": executables[actual["exe"]]["identity"],
                "exe_metadata": executables[actual["exe"]]["metadata"],
                "uid": int(status["Uid"].split()[1]),
            }
        )
        require(actual["uid"] == 0, "native_warm_actor_credentials_changed")
        _cache_mount(pid, "ro")
        require(_start(pid) == start, "native_warm_actor_replaced")
        actors.append(actual)
    require(
        actors and len({item["pid"] for item in actors}) == len(actors),
        "native_warm_actors_required",
    )
    return actors


def _libraries(pid: str, binding: dict, environment: dict) -> list[dict]:
    inventory = {item["path"]: item["sha256"] for item in binding["runtime_files"]}
    observed = {}
    for line in _proc(pid, "maps").decode().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or not Path(fields[5]).name.startswith(("_sqlite3.", "libsqlite3.")):
            continue
        path = Path(fields[5])
        info = path.stat()
        require(
            path.is_absolute()
            and fields[4] == str(info.st_ino)
            and tuple(int(value, 16) for value in fields[3].split(":"))
            == (os.major(info.st_dev), os.minor(info.st_dev)),
            "native_sqlite_mapping_identity_changed",
        )
        digest = sha(read_protected(path, limit=32 * 1024 * 1024))
        if path.name.startswith("libsqlite3.") and binding["module"] in {
            "autostop_manager.j1_research",
            "autostop_manager.mcp_server",
        }:
            require(
                str(path.parent) in environment.get("LD_LIBRARY_PATH", "").split(":"),
                "native_sqlite_loader_changed",
            )
        if binding["module"] in {"autostop_manager.j1_research", "autostop_manager.mcp_server"}:
            require(inventory.get(str(path)) == digest, "native_sqlite_not_accepted")
        observed[str(path)] = {
            "path": str(path),
            "sha256": digest,
            "identity": [info.st_dev, info.st_ino],
        }
    require(
        any(Path(path).name.startswith("_sqlite3.") for path in observed)
        and any(Path(path).name.startswith("libsqlite3.") for path in observed),
        "native_sqlite_not_ready",
    )
    return [observed[key] for key in sorted(observed)]


def _running(unit: str, binding: dict, fields: dict, guard: ModuleType, deadline: float) -> dict:
    require(
        fields.get("ActiveState") == "active" and fields.get("SubState") == "running",
        "native_role_not_running",
    )
    pid = fields["MainPID"]
    start = _start(pid)
    require(
        _proc(pid, "cgroup").decode().strip() == "0::/system.slice/" + unit
        and fields.get("ControlGroup") == "/system.slice/" + unit,
        "native_process_cgroup_changed",
    )
    require(
        os.readlink(PROC / pid / "exe") == binding["interpreter_realpath"]
        and os.readlink(PROC / pid / "cwd") == binding["cwd"],
        "native_process_runtime_changed",
    )
    expected = [binding["interpreter"], "-P", "-m", binding["module"], *binding["arguments"]]
    require(
        _proc(pid, "cmdline").split(b"\0") == [item.encode() for item in expected] + [b""],
        "native_process_argv_changed",
    )
    status = dict(
        line.split(":", 1) for line in _proc(pid, "status").decode().splitlines() if ":" in line
    )
    credentials = [
        int(status["Uid"].split()[1]),
        int(status["Gid"].split()[1]),
        sorted(map(int, status["Groups"].split())),
    ]
    require(credentials == binding["credentials"], "native_process_credentials_changed")
    rows = _proc(pid, "environ").split(b"\0")
    require(
        rows[-1] == b"" and all(b"=" in row for row in rows[:-1]),
        "native_process_environment_shape",
    )
    pairs = [row.decode().split("=", 1) for row in rows[:-1]]
    require(len({row[0] for row in pairs}) == len(pairs), "native_process_environment_duplicate")
    environment = dict(pairs)
    require(
        environment.get("PYTHONPATH") == binding["source_root"], "native_process_source_changed"
    )
    guard.verify_environment({**environment, "PYTHONPATH": binding["selector"]}, binding)
    libraries = _libraries(pid, binding, environment)
    mount = _cache_mount(pid, "native")
    after = _systemd(unit, deadline)
    require(
        after["MainPID"] == pid
        and after["ActiveState"] == "active"
        and after["SubState"] == "running"
        and _start(pid) == start,
        "native_process_changed_during_observation",
    )
    return {
        "pid": int(pid),
        "start_ticks": int(start),
        "libraries": libraries,
        "cache_mount": mount,
    }


def _runtime_inventory(authority: dict, full: bool, deadline: float) -> list[dict]:
    refs = {
        item["path"]: item
        for binding in authority["policy"]["bindings"].values()
        for item in binding["runtime_files"]
        if "/autostop-runtime-inventories-" in item["path"]
    }
    results = []
    for ref in refs.values():
        raw = read_protected(Path(ref["path"]), limit=8 * 1024 * 1024)
        require(sha(raw) == ref["sha256"], "native_runtime_inventory_changed")
        inventory = document(raw)
        require(
            inventory.get("schema") == "autostop.public-runtime-tree-inventory.v1",
            "native_inventory_schema",
        )
        root = Path(inventory["root"])
        authority["guard"].directory(root)
        expected = {record["relative"]: record for record in inventory["records"]}
        require(len(expected) == len(inventory["records"]), "native_inventory_duplicate")
        found, metadata = set(), []
        for folder, dirs, files in os.walk(root, followlinks=False):
            for name in sorted((*dirs, *files)):
                _expired(deadline)
                path = Path(folder) / name
                relative = path.relative_to(root).as_posix()
                require(relative in expected, "native_runtime_unexpected_file")
                record, info = expected[relative], path.lstat()
                kind = (
                    "symlink"
                    if stat.S_ISLNK(info.st_mode)
                    else "directory"
                    if stat.S_ISDIR(info.st_mode)
                    else "file"
                )
                require(
                    kind == record["kind"] and (kind != "file" or stat.S_ISREG(info.st_mode)),
                    "native_runtime_kind_changed",
                )
                require(
                    info.st_uid == j1_forward_io.ROOT_UID
                    and (kind == "symlink" or not info.st_mode & 0o022),
                    "native_runtime_unprotected",
                )
                current = _metadata(info)
                if kind == "symlink":
                    require(
                        os.readlink(path) == record["target"]
                        and str(path.resolve(strict=True)) == record["physical_target"],
                        "native_runtime_symlink_changed",
                    )
                elif kind == "file" and (full or current != record["metadata"]):
                    require(
                        _file_digest(path, deadline) == record["sha256"],
                        "native_runtime_content_changed",
                    )
                    require(
                        _metadata(path.lstat()) == current, "native_runtime_changed_during_hash"
                    )
                metadata.append([relative, current])
                found.add(relative)
        require(found == set(expected), "native_runtime_missing_file")
        results.append(
            {
                "inventory": ref,
                "root": str(root),
                "metadata_sha256": sha(canonical(sorted(metadata))),
                "entries": len(found),
            }
        )
    return sorted(results, key=lambda item: item["root"])


def _observe(*, running: bool, owned_closure: dict | None, full_runtime: bool) -> dict:
    started = time.monotonic()
    deadline = started + (180 if full_runtime else 60)
    authority = _load_authority(deadline)
    guard, wrapper, policy = (authority[key] for key in ("guard", "wrapper", "policy"))
    owned = _closure_record(owned_closure)
    require(not (running and owned), "native_running_and_closed_conflict")
    _unsupported_units(authority, bool(owned))
    profiles, units, processes = {}, {}, {}
    for unit in ALL_UNITS:
        fields = _systemd(unit, deadline)
        _loaded(unit, fields, authority, bool(owned))
        require(fields["UnitFileState"] in {"enabled", "disabled"}, "native_enablement_unknown")
        units[unit] = {
            "enabled": fields["UnitFileState"] == "enabled",
            "active": fields["ActiveState"] == "active",
        }
        if unit not in NATIVE_UNITS:
            continue
        binding = policy["bindings"][unit]
        guard.verify_source(binding, authority["index"])
        guard.verify_runtime(binding, SCOPE)
        environment = wrapper.accepted_input(authority["capsule"], policy, unit)
        guard.verify_environment(environment, binding)
        relocated = wrapper.relocated_binding(binding, authority["capsule"])
        if owned:
            _units_with_closure(guard, relocated, unit, authority["capsule"]["policy"]["sha256"])
        else:
            guard.verify_units(relocated, unit, authority["capsule"]["policy"]["sha256"])
        profiles[unit] = {
            key: binding[key]
            for key in (
                "source_root",
                "source_identity",
                "selector",
                "manifest_sha256",
                "module",
                "arguments",
                "access",
                "cwd",
                "cwd_identity",
                "interpreter",
                "interpreter_realpath",
                "interpreter_identity",
                "interpreter_sha256",
                "stable_environment_sha256",
                "credentials",
                "ordinary_database",
            )
        }
        profiles[unit]["binding_sha256"] = sha(canonical(binding))
        if running:
            processes[unit] = _running(unit, binding, fields, guard, deadline)
    intent = authority["intent"]
    lock = Path(intent["deploy_lock"])
    read_protected(lock)
    require(
        str(lock) == "/opt/autostopcrm/.autostop-deploy.lock"
        and intent["maintenance_marker"] == "/opt/autostopcrm/data/.agent-gateway-maintenance",
        "native_deploy_paths_changed",
    )
    base = {
        "manager_revision": REVISION,
        "daemon_mode": "accepted_warm",
        "authority": sorted(authority["refs"].values(), key=lambda item: item["path"]),
    }
    immutable = {
        "schema": SCHEMA,
        **base,
        "base_sha256": sha(canonical(base)),
        "profiles": profiles,
        "manager_root": profiles["autostop-j1.service"]["source_root"],
        "work_root": profiles["autostop-work-telegram.service"]["source_root"],
        "legacy_crm_revision": intent["crm_revision"],
        "deployment_lock": str(lock),
        "deployment_lock_identity": _identity(lock),
        "maintenance_marker": intent["maintenance_marker"],
        "desired_inbound": intent["inbound_enabled"],
        "cache": _cache(authority),
        "warm_actors": _warm_actors(authority, deadline),
        "runtime_inventories": _runtime_inventory(authority, full_runtime, deadline),
    }
    _expired(deadline)
    return {
        "immutable": immutable,
        "units": units,
        "processes": processes,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def snapshot_native(*, full_runtime: bool = True) -> dict:
    """Capture a running, hash-bound baseline; values contain no live secrets."""
    observed = _observe(running=True, owned_closure=None, full_runtime=full_runtime)
    baseline = {
        **observed["immutable"],
        "desired_units": observed["units"],
        "libraries": {unit: value["libraries"] for unit, value in observed["processes"].items()},
        "cache_mounts": {
            unit: value["cache_mount"] for unit, value in observed["processes"].items()
        },
    }
    return {**baseline, "sha256": sha(canonical(baseline))}


def verify_preserved(
    baseline: dict, *, running: bool = False, owned_closure: dict | None = None
) -> dict:
    """Reobserve preserved inputs; closure permits only the exact owned barrier."""
    require(type(baseline) is dict and baseline.get("schema") == SCHEMA, "native_baseline_schema")
    unsigned = {key: value for key, value in baseline.items() if key != "sha256"}
    require(sha(canonical(unsigned)) == baseline.get("sha256"), "native_baseline_digest")
    observed = _observe(running=running, owned_closure=owned_closure, full_runtime=False)
    expected = {
        key: value
        for key, value in unsigned.items()
        if key not in {"desired_units", "libraries", "cache_mounts"}
    }
    require(observed["immutable"] == expected, "native_immutable_inputs_changed")
    require(
        {unit: value["enabled"] for unit, value in observed["units"].items()}
        == {unit: value["enabled"] for unit, value in baseline["desired_units"].items()},
        "native_enablement_changed",
    )
    if running:
        require(observed["units"] == baseline["desired_units"], "native_desired_state_changed")
        require(
            {unit: value["libraries"] for unit, value in observed["processes"].items()}
            == baseline["libraries"],
            "native_loaded_libraries_changed",
        )
        require(
            {unit: value["cache_mount"] for unit, value in observed["processes"].items()}
            == baseline["cache_mounts"],
            "native_loaded_cache_mount_changed",
        )
    return {
        "ok": True,
        "baseline_sha256": baseline["sha256"],
        "running": running,
        "closed": owned_closure is not None,
        "processes": observed["processes"],
        "elapsed_ms": observed["elapsed_ms"],
    }
