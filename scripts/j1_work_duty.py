#!/usr/bin/env python3
"""Restore the committed Work duty through its fixed local RPC, without app imports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import stat
import struct
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

HOST = "vps26457.mnogoweb.in"
SCOPE = "production_quiet_window"
MANAGER_REVISION = "89af29082e0a6f72c5d85ae448acb6d4deec5d94"
GUARD_SHA256 = "d5275167987434dffab4f81f0671c381c7a32f69e281fd8d4f8644715f3427c6"
INDEX_SHA256 = "1cfd4aff5ba303e00c12f8b70d9408c77de1c08313586acd49cb96a20a524e3c"
CONTROLLER = Path("/usr/local/libexec/autostop-crm-j1-duty/restore.py")
INTENT = Path("/etc/autostop-crm-j1-duty/intent.json")
COMMIT = Path("/var/lib/autostop-crm-j1-duty/committed.json")
PENDING = Path("/var/lib/autostop-crm-j1-duty/commit-pending.json")
POLICY = Path("/etc/autostop-j1-candidate-guard/policy.json")
STATE = Path("/var/lib/autostop-j1-candidate-guard/armed.json")
GUARD_PENDING = STATE.parent / "arming-pending.json"
GUARD = Path("/usr/local/libexec/autostop-j1-candidate-guard/guard.py")
INDEX = GUARD.parent / "source-index.json"
DROPIN = Path("/etc/systemd/system/autostop-work-telegram.service.d/98-j1-work-duty-restore.conf")
SOCKET = Path("/run/autostop-work-telegram/bridge.sock")
BRIDGE_USER = "autostop-work-telegram"
WORK_UNIT = "autostop-work-telegram.service"
TIMEOUT_SECONDS = 15.0
MAX_BYTES = 32 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
INTENT_FIELDS = {
    "schema",
    "scope",
    "attempt_id",
    "preparation_path",
    "preparation_sha256",
    "closure_sha256",
    "crm_revision",
    "manager_revision",
    "inbound_enabled",
    "maintenance_marker",
    "deploy_lock",
    "controller_sha256",
}


class DutyError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise DutyError(code)


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def invalid_number(_value):
    raise DutyError("nonfinite_json_number")


def owner(info):
    """Host ownership checks are separate from path, kind and byte validation."""
    require(info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o022, "unprotected_path")


def physical(path):
    require(path.is_absolute() and ".." not in path.parts, "absolute_physical_path_required")
    for candidate in (*reversed(path.parents), path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            require(candidate == path, "missing_parent")
            return
        require(not stat.S_ISLNK(info.st_mode), "symlink_denied")


def protected(path):
    physical(path)
    for candidate in (*reversed(path.parents), path):
        owner(candidate.lstat())


def exists(path):
    physical(path)
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_file(path):
    protected(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "regular_single_link_required"
        )
        require(before.st_size <= MAX_BYTES, "file_too_large")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, "file_too_large")
        after, named = os.fstat(fd), path.lstat()
        require(identity(before) == identity(after), "file_changed")
        # Windows fstat/lstat expose different ctime meanings. Linux production
        # still checks ctime as well as inode, size and mtime across both APIs.
        require(identity(before)[:4] == identity(named)[:4], "file_changed")
        if os.name == "posix":
            require(before.st_ctime_ns == named.st_ctime_ns, "file_changed")
        return raw
    finally:
        os.close(fd)


def document(path, expected=None):
    raw = read_file(path)
    if expected is not None:
        require(
            type(expected) is str and HEX.fullmatch(expected) and digest(raw) == expected,
            "hash_mismatch",
        )
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_number)
    require(type(value) is dict, "object_required")
    return value


def reference(record):
    require(type(record) is dict and set(record) == {"path", "sha256"}, "reference_invalid")
    return document(Path(record["path"]), record["sha256"])


def context(value, schema, intent):
    require(
        value.get("schema") == schema
        and value.get("scope") == SCOPE
        and value.get("attempt_id") == intent["attempt_id"],
        "context_mismatch",
    )


def platform_required():
    require(sys.platform == "linux" and os.geteuid() == 0, "linux_root_required")
    require(socket.gethostname() == HOST, "host_mismatch")


def load_intent(expected):
    require(type(expected) is str and HEX.fullmatch(expected), "intent_hash_required")
    require(Path(__file__).absolute() == CONTROLLER, "fixed_controller_required")
    value = document(INTENT, expected)
    require(set(value) == INTENT_FIELDS, "intent_fields_invalid")
    context(value, "autostop.crm.j1-work-duty-intent.v1", value)
    require(str(uuid.UUID(value["attempt_id"])) == value["attempt_id"], "attempt_invalid")
    require(type(value["inbound_enabled"]) is bool, "duty_boolean_required")
    require(
        value["manager_revision"] == MANAGER_REVISION
        and type(value["crm_revision"]) is str
        and REVISION.fullmatch(value["crm_revision"]),
        "revision_mismatch",
    )
    for name in ("preparation_sha256", "closure_sha256", "controller_sha256"):
        require(type(value[name]) is str and HEX.fullmatch(value[name]), "intent_binding_invalid")
    for name in ("preparation_path", "maintenance_marker", "deploy_lock"):
        physical(Path(value[name]))
    require(digest(read_file(CONTROLLER)) == value["controller_sha256"], "controller_changed")
    require(stat.S_IMODE(INTENT.stat().st_mode) == 0o444, "immutable_intent_required")
    return value


def verify_sources(sources):
    require(
        type(sources) is dict and set(sources) == {"manager", "work-telegram", "personal-telegram"},
        "source_set_invalid",
    )
    for record in sources.values():
        root = Path(record["path"])
        protected(root)
        info = root.stat()
        require([info.st_dev, info.st_ino] == record["identity"], "source_identity_changed")
        require(
            read_file(root / "REVISION") == (MANAGER_REVISION + "\n").encode(),
            "source_revision_changed",
        )
        manifest = read_file(root / "MANIFEST.sha256")
        require(digest(manifest) == record["manifest_sha256"], "source_manifest_changed")
        names = []
        for line in manifest.decode().splitlines():
            expected, name = line.split("  ./", 1)
            require(
                HEX.fullmatch(expected)
                and name
                and not name.startswith("/")
                and ".." not in name.split("/")
                and "\\" not in name,
                "source_entry_invalid",
            )
            require(digest(read_file(root / name)) == expected, "source_content_changed")
            names.append(name)
        require(names == sorted(set(names)) and "REVISION" in names, "source_inventory_invalid")
        require(
            read_file(root / "MANIFEST.files0")
            == b"".join(f"./{name}".encode() + b"\0" for name in names),
            "source_inventory_changed",
        )
        actual = set()
        for folder, directories, files in os.walk(root, followlinks=False):
            for name in directories:
                protected(Path(folder) / name)
            actual.update((Path(folder) / name).relative_to(root).as_posix() for name in files)
        require(
            actual == set(names) | {"MANIFEST.sha256", "MANIFEST.files0"}, "unknown_source_file"
        )


def verify_files(records, required):
    require(type(records) is list and records, "bound_files_required")
    observed = {}
    for record in records:
        require(type(record) is dict and set(record) == {"path", "sha256"}, "reference_invalid")
        require(record["path"] not in observed, "duplicate_bound_file")
        require(
            type(record["sha256"]) is str and HEX.fullmatch(record["sha256"]), "file_hash_invalid"
        )
        require(digest(read_file(Path(record["path"]))) == record["sha256"], "bound_file_changed")
        observed[record["path"]] = record["sha256"]
    require(
        all(observed.get(path) == expected for path, expected in required.items()),
        "controller_inputs_not_bound",
    )


def dropin_bytes(intent_hash):
    return (
        "[Service]\nExecStartPost=\nExecStartPost=+/usr/bin/python3 -I -S "
        f"{CONTROLLER} restore --intent-sha256 {intent_hash}\n"
    ).encode()


def expected_commit(intent, intent_hash):
    preparation_path = Path(intent["preparation_path"])
    root = preparation_path.parent
    preparation = document(preparation_path, intent["preparation_sha256"])
    require(
        preparation.get("schema") == "autostop.crm.j1-release-preparation.v1"
        and preparation.get("attempt_id") == intent["attempt_id"]
        and preparation["crm"]["revision"] == intent["crm_revision"]
        and preparation["manager"]["revision"] == MANAGER_REVISION,
        "preparation_changed",
    )
    verify_sources(preparation["sources"])
    closed = document(root / "closure-acceptance.json", intent["closure_sha256"])
    context(closed, "autostop.crm.j1-closure-acceptance.v1", intent)
    require(
        closed.get("preparation_sha256") == intent["preparation_sha256"]
        and type(closed.get("work_inbound_before")) is bool
        and closed["work_inbound_before"] == intent["inbound_enabled"],
        "closure_changed",
    )
    admitted = document(root / "admitted.json")
    require(
        set(admitted)
        == {
            "schema",
            "scope",
            "attempt_id",
            "preparation_sha256",
            "handoff_sha256",
            "acceptance_sha256",
        },
        "admission_fields_invalid",
    )
    context(admitted, "autostop.crm.j1-admission-receipt.v1", intent)
    require(admitted.get("preparation_sha256") == intent["preparation_sha256"], "admission_changed")
    handoff = document(root / "handoff.json", admitted["handoff_sha256"])
    context(handoff, "autostop.crm.j1-activation-handoff.v1", intent)
    require(
        handoff.get("host") == HOST
        and handoff.get("preparation_sha256") == intent["preparation_sha256"]
        and handoff.get("closure_sha256") == intent["closure_sha256"]
        and handoff.get("crm") == preparation["crm"]
        and handoff.get("manager") == preparation["manager"]
        and handoff.get("sources") == preparation["sources"]
        and handoff.get("work_duty_controller_sha256") == intent["controller_sha256"]
        and handoff.get("work_duty_intent") == {"path": str(INTENT), "sha256": intent_hash},
        "handoff_changed",
    )
    response = document(root / "activation-acceptance.json", admitted["acceptance_sha256"])
    context(response, "autostop.crm.j1-activation-acceptance.v1", intent)
    require(response.get("handoff_sha256") == admitted["handoff_sha256"], "acceptance_changed")
    refs = response["evidence"]
    require(
        Path(refs["policy"]["path"]) == POLICY and Path(refs["state"]["path"]) == STATE,
        "fixed_guard_paths_required",
    )
    require(not exists(GUARD_PENDING), "guard_arm_pending")
    policy = reference(refs["policy"])
    state = reference(refs["state"])
    require(
        policy.get("schema") == "autostop.j1.candidate-policy.v2"
        and policy.get("attempt_id") == intent["attempt_id"]
        and policy.get("host") == HOST
        and policy.get("revision") == MANAGER_REVISION
        and policy.get("guard_sha256") == GUARD_SHA256
        and policy.get("index_sha256") == INDEX_SHA256,
        "guard_policy_changed",
    )
    context(state, "autostop.j1.candidate-state.v2", intent)
    require(
        state.get("revision") == MANAGER_REVISION
        and state.get("policy_sha256") == refs["policy"]["sha256"]
        and state.get("guard_sha256") == GUARD_SHA256
        and state.get("index_sha256") == INDEX_SHA256
        and state.get("old_writer_allowed") is False
        and state.get("candidate_may_have_written") is True
        and state.get("prerequisite_sha256") == refs["quiet_window"]["sha256"],
        "guard_state_changed",
    )
    reference(refs["quiet_window"])
    require(
        digest(read_file(GUARD)) == GUARD_SHA256 and digest(read_file(INDEX)) == INDEX_SHA256,
        "guard_package_changed",
    )
    binding = policy["bindings"][WORK_UNIT]
    source = preparation["sources"]["work-telegram"]
    require(
        binding["source_root"] == source["path"]
        and binding["source_identity"] == source["identity"]
        and binding["manifest_sha256"] == source["manifest_sha256"]
        and binding["module"] == "autostop_manager.telegram_bridge"
        and binding["arguments"] == ["--account", "work", "daemon"],
        "work_profile_changed",
    )
    runtime_ref = next(
        (ref for ref in binding["runtime_files"] if ref["path"] == binding["runtime_receipt"]), None
    )
    require(runtime_ref is not None, "work_runtime_required")
    runtime = reference(runtime_ref)
    environment = runtime.get("stable_input_environment")
    require(
        type(environment) is dict
        and all(type(key) is str and type(value) is str for key, value in environment.items())
        and digest(canonical(environment)) == binding["stable_environment_sha256"]
        and runtime.get("stable_input_environment_sha256") == binding["stable_environment_sha256"]
        and environment.get("AUTOSTOP_WORK_TELEGRAM_MONITOR_INCOMING") == "0"
        and environment.get("AUTOSTOP_WORK_TELEGRAM_DURABLE_INBOX") == "1",
        "work_startup_changed",
    )
    require(read_file(DROPIN) == dropin_bytes(intent_hash), "fixed_restore_hook_required")
    required = {
        str(INTENT): intent_hash,
        str(CONTROLLER): intent["controller_sha256"],
        str(DROPIN): digest(dropin_bytes(intent_hash)),
    }
    verify_files(handoff["files"], required)
    verify_files(binding["unit_files"], required)
    return {
        "schema": "autostop.crm.j1-work-duty-committed.v1",
        "scope": SCOPE,
        "attempt_id": intent["attempt_id"],
        "intent_sha256": intent_hash,
        "preparation_sha256": intent["preparation_sha256"],
        "handoff_sha256": admitted["handoff_sha256"],
        "acceptance_sha256": admitted["acceptance_sha256"],
        "policy_sha256": refs["policy"]["sha256"],
        "crm_revision": intent["crm_revision"],
        "inbound_enabled": intent["inbound_enabled"],
    }


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_exclusive(path, raw):
    protected(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
        os.fchmod(fd, 0o444)
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_directory(path.parent)


def committed_matches(expected):
    require(stat.S_IMODE(COMMIT.stat().st_mode) == 0o444, "immutable_commit_required")
    actual = document(COMMIT)
    require(canonical(actual) == canonical(expected), "committed_binding_mismatch")


def commit(intent_hash):
    platform_required()
    intent = load_intent(intent_hash)
    require(not exists(PENDING), "commit_pending")
    expected = expected_commit(intent, intent_hash)
    require(not exists(Path(intent["maintenance_marker"])), "maintenance_active")
    if exists(COMMIT):
        committed_matches(expected)
        return "already_committed"
    publish_exclusive(PENDING, canonical({"intent_sha256": intent_hash}))
    # Preserve pending and any partial final file on every unknown outcome.
    publish_exclusive(COMMIT, canonical(expected))
    PENDING.unlink()
    fsync_directory(COMMIT.parent)
    return "committed"


def remaining(deadline):
    seconds = deadline - time.monotonic()
    require(seconds > 0, "deadline_exceeded")
    return seconds


@contextmanager
def shared_lock(path, deadline):
    import fcntl

    protected(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "deploy_lock_invalid")
        while True:
            remaining(deadline)
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.05, remaining(deadline)))
        require(
            (info.st_dev, info.st_ino) == (path.stat().st_dev, path.stat().st_ino),
            "deploy_lock_replaced",
        )
        yield
    finally:
        os.close(fd)


def local_request(payload, deadline):
    import pwd

    physical(SOCKET)
    before = SOCKET.lstat()
    expected_uid = pwd.getpwnam(BRIDGE_USER).pw_uid
    require(
        stat.S_ISSOCK(before.st_mode) and before.st_uid == expected_uid, "bridge_socket_invalid"
    )
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(remaining(deadline))
        connection.connect(str(SOCKET))
        _pid, uid, _gid = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        require(
            uid == expected_uid
            and (before.st_dev, before.st_ino) == (SOCKET.stat().st_dev, SOCKET.stat().st_ino),
            "bridge_peer_changed",
        )
        connection.sendall(canonical(payload))
        raw = bytearray()
        while b"\n" not in raw:
            connection.settimeout(remaining(deadline))
            chunk = connection.recv(4096)
            require(chunk, "rpc_truncated")
            raw.extend(chunk)
            require(len(raw) <= 64 * 1024, "rpc_response_too_large")
    require(raw.endswith(b"\n") and raw.count(b"\n") == 1, "rpc_framing_invalid")
    response = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_number)
    require(type(response) is dict and response.get("ok") is True, "rpc_rejected")
    return response


def wait_ready(deadline):
    while True:
        remaining(deadline)
        try:
            status = local_request({"operation": "status"}, deadline)
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        else:
            require(type(status.get("transport_ready")) is bool, "readiness_response_invalid")
            if status["transport_ready"]:
                return
        time.sleep(min(0.05, remaining(deadline)))


def restore(intent_hash):
    platform_required()
    deadline = time.monotonic() + TIMEOUT_SECONDS
    intent = load_intent(intent_hash)
    require(not exists(PENDING), "commit_pending")
    if not exists(COMMIT):
        return "closed_uncommitted"
    expected = expected_commit(intent, intent_hash)
    committed_matches(expected)
    marker = Path(intent["maintenance_marker"])
    if exists(marker):
        return "closed_maintenance"
    with shared_lock(Path(intent["deploy_lock"]), deadline):
        # Re-read everything after lock acquisition: a deploy may have completed
        # while we waited. No accepted writer can race the ensuing local RPC.
        require(load_intent(intent_hash) == intent, "intent_changed")
        require(not exists(PENDING), "commit_pending")
        committed_matches(expected_commit(intent, intent_hash))
        if exists(marker):
            return "closed_maintenance"
        wait_ready(deadline)
        local_request({"operation": "duty_set", "enabled": intent["inbound_enabled"]}, deadline)
        status = local_request({"operation": "status"}, deadline)
        require(
            status.get("transport_ready") is True
            and type(status.get("inbound_enabled")) is bool
            and status["inbound_enabled"] == intent["inbound_enabled"],
            "duty_readback_failed",
        )
    return "restored"


def timeout_handler(_signum, _frame):
    raise DutyError("deadline_exceeded")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("operation", choices=("restore", "commit"))
    parser.add_argument("--intent-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        platform_required()
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, TIMEOUT_SECONDS)
        try:
            result = (restore if args.operation == "restore" else commit)(args.intent_sha256)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except DutyError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Never echo socket payloads, account details, paths or credential bytes.
        print(json.dumps({"ok": False, "error": "work_duty_validation_failed"}))
        return 1
    print(json.dumps({"ok": True, "state": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
