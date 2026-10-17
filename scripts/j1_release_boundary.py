#!/usr/bin/env python3
"""Source-only J1 preparation and the fixed CRM/Manager activation rendezvous.

No application imports, executable hooks, service control, restore or disarm.
The Manager operator publishes acceptance only after the quiet-window evidence
and immutable candidate guard have been accepted. Native launch remains the
guard's responsibility inside each actual systemd unit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

MANAGER_REVISION = "89af29082e0a6f72c5d85ae448acb6d4deec5d94"
MANAGER_TREE = "c16c03e349d793bea742c0288d397eceb650682e"
GUARD_SHA256 = "d5275167987434dffab4f81f0671c381c7a32f69e281fd8d4f8644715f3427c6"
INDEX_SHA256 = "1cfd4aff5ba303e00c12f8b70d9408c77de1c08313586acd49cb96a20a524e3c"
GUARD_ROOT = Path("/usr/local/libexec/autostop-j1-candidate-guard")
GUARD_STATE = Path("/var/lib/autostop-j1-candidate-guard")
GUARD_POLICY = Path("/etc/autostop-j1-candidate-guard/policy.json")
SYSTEMD_ROOT = Path("/etc/systemd/system")
WORK_RELEASE_ROOT = Path("/opt/autostop-work-telegram-releases")
WORK_VENV_LINK = Path("/opt/autostop-work-telegram-venv")
WORK_MODEL_LINK = Path("/opt/autostop-work-telegram-models/faster-whisper-small")
DUTY_CONTROLLER = Path("/usr/local/libexec/autostop-crm-j1-duty/restore.py")
DUTY_INTENT = Path("/etc/autostop-crm-j1-duty/intent.json")
DUTY_STATE = Path("/var/lib/autostop-crm-j1-duty")
DUTY_DROPIN = "98-j1-work-duty-restore.conf"
REQUIRED_UNITS = {
    "autostop-j1.service",
    "autostop-manager-mcp.service",
    "autostop-manager-scheduler.service",
    "autostop-work-telegram.service",
    "autostop-codex-wake.service",
}
NATIVE_UNITS = (
    "autostop-j1.service",
    "autostop-manager-mcp.service",
    "autostop-manager-scheduler.service",
    "autostop-work-telegram.service",
    "autostop-telegram.service",
    "autostop-codex-wake.service",
)
UNSUPPORTED_UNITS = {"autostop-codex-start.service", "autostop-j1-browser.service"}
ALL_UNITS = REQUIRED_UNITS | UNSUPPORTED_UNITS | {"autostop-telegram.service"}
CLOSURE_DROPIN = "zz-j1-release-closed.conf"
CLOSURE_BYTES = (
    b"[Service]\nExecCondition=\nExecCondition=/usr/bin/false\n"
    b"ExecStartPre=\nExecStartPost=\nExecReload=\nExecStop=\nExecStopPost=\n"
)
DEPENDENCY_INPUTS = {
    "pyproject.toml",
    "deploy/telegram/requirements-py312-linux-x86_64.lock",
    "deploy/telegram/build-requirements-py312-linux-x86_64.lock",
    "deploy/telegram/pyaes-source-py312-linux-x86_64.lock",
    "deploy/telegram/pyaes-wheel-py312-linux-x86_64.lock",
    "deploy/telegram/faster-whisper-small.sha256",
}
SCOPE = "production_quiet_window"
HOST = "vps26457.mnogoweb.in"
INSTRUCTION_REVISION = "1b87158a7912af35ffddd45edd2df9089eaa8375"
INSTRUCTION_ROOT = "/opt/autostop-work-telegram-releases/20261014T162134Z-1b87158a7912"
HEX = re.compile(r"[0-9a-f]{64}\Z")
CACHE_PATH = Path("/var/cache/autostop-j1/research.sqlite3")
PROC_ROOT = Path("/proc")
GUARD_PYTHON = Path("/usr/bin/python3")
NATIVE_READY_SECONDS = 20.0
KERNEL_MODE = "kernel_ro_cold_v1"
KERNEL_CONSUMER = DUTY_CONTROLLER.with_name("kernel.py")
KERNEL_GATE_DROPIN = "z1-j1-kernel-readiness.conf"
KERNEL_RECOVERY_UNIT = "autostop-j1-native-recover.service"
KERNEL_RECOVERY_DROPIN = "z1-j1-native-recovery.conf"


class BoundaryError(ValueError):
    pass


def require(value: Any, reason: str) -> None:
    if not value:
        raise BoundaryError(reason)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def invalid_number(value: str) -> None:
    raise BoundaryError(f"nonfinite_json_number: {value}")


def physical(path: Path) -> None:
    require(
        path.is_absolute()
        and ".." not in path.parts
        and not any(character in str(path) for character in "\n\r\0"),
        "absolute_path_required",
    )
    for parent in (path, *path.parents):
        require(not parent.is_symlink(), "symlink_path_denied")


def protected(path: Path) -> None:
    physical(path)
    for parent in (path, *path.parents):
        if parent.exists() and os.name == "posix":
            info = parent.stat()
            require(info.st_uid == 0 and not info.st_mode & 0o022, "root_protected_path_required")


def read_file(path: Path, limit: int = 16 * 1024 * 1024) -> bytes:
    physical(path)
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "regular_single_link_required")
    require(before.st_size <= limit, "file_too_large")
    raw = path.read_bytes()
    after = path.stat()
    require(
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, len(raw), after.st_mtime_ns),
        "file_changed",
    )
    return raw


def document(path: Path, expected: str | None = None) -> dict[str, Any]:
    raw = read_file(path)
    require(
        expected is None or (HEX.fullmatch(expected) and digest(raw) == expected), "hash_mismatch"
    )
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_number)
    require(type(value) is dict, "object_required")
    return value


def file_hash(path: Path) -> str:
    physical(path)
    before = path.stat()
    require(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and before.st_size <= 1024 * 1024 * 1024,
        "bounded_regular_artifact_required",
    )
    hasher = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            require(total <= 1024 * 1024 * 1024, "artifact_grew")
            hasher.update(chunk)
    after = path.stat()
    require(
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, total, after.st_mtime_ns),
        "artifact_changed",
    )
    return hasher.hexdigest()


def publish(path: Path, value: Any) -> str:
    physical(path)
    raw = canonical(value)
    fd, name = tempfile.mkstemp(prefix=".publication-", dir=path.parent)
    temporary = Path(name)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    # Exclusive atomic publication. Leave a failed private temporary artifact
    # for diagnosis; never replace an existing attempt or partially expose JSON.
    if os.name == "nt":
        temporary.rename(path)  # Windows rename refuses an existing target.
        path.chmod(0o444)
    else:
        temporary.chmod(0o444)
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    if os.name == "posix":
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return digest(raw)


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            "-C",
            str(root),
            *arguments,
        ],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout


def checkout(root: Path) -> dict[str, str]:
    physical(root)
    require(
        Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve() == root,
        "git_root_required",
    )
    require(
        not git(root, "status", "--porcelain", "--untracked-files=all").strip(),
        "clean_checkout_required",
    )
    return {
        "revision": git(root, "rev-parse", "HEAD").decode().strip(),
        "tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
    }


def source_files(root: Path, revision: str) -> dict[str, tuple[str, str]]:
    result = {}
    for record in git(root, "ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
        if not record:
            continue
        metadata, raw_name = record.split(b"\t", 1)
        mode, kind, blob = metadata.decode().split()
        name = raw_name.decode("utf-8")
        require(
            kind == "blob"
            and mode in {"100644", "100755"}
            and name not in {"REVISION", "MANIFEST.sha256", "MANIFEST.files0"}
            and not any(character in name for character in "\n\r\\")
            and not name.startswith("/")
            and ".." not in name.split("/"),
            "unsupported_source_entry",
        )
        result[name] = (mode, blob)
    require(result, "empty_source_denied")
    return result


def seal(
    root: Path, target: Path, revision: str, files: dict[str, tuple[str, str]]
) -> dict[str, Any]:
    physical(target)
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    archive = git(root, "archive", "--format=tar", revision)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for item in source:
            if item.isdir():
                continue
            require(item.isfile() and item.name in files, "archive_entry_denied")
            stream = source.extractfile(item)
            require(stream is not None, "archive_file_unreadable")
            raw = stream.read()
            mode, blob = files[item.name]
            require(
                hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == blob,
                "git_blob_mismatch",
            )
            path = target / item.name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            with path.open("xb") as output:
                output.write(raw)
            path.chmod(0o755 if mode == "100755" else 0o644)
    (target / "REVISION").write_bytes((revision + "\n").encode())
    names = sorted([*files, "REVISION"])
    manifest = "".join(
        f"{digest((target / name).read_bytes())}  ./{name}\n" for name in names
    ).encode()
    (target / "MANIFEST.sha256").write_bytes(manifest)
    (target / "MANIFEST.files0").write_bytes(
        b"".join(f"./{name}".encode() + b"\0" for name in names)
    )
    for folder, _, _ in os.walk(target):
        Path(folder).chmod(0o755)
    # Docker overlays live Manager data at this already existing mountpoint.
    (target / "data").mkdir(exist_ok=True, mode=0o755)
    (target / "data").chmod(0o755)
    for name in ("REVISION", "MANIFEST.sha256", "MANIFEST.files0"):
        (target / name).chmod(0o644)
    info = target.stat()
    return {
        "path": str(target),
        "identity": [info.st_dev, info.st_ino],
        "manifest_sha256": digest(manifest),
    }


def verify_source(
    record: dict[str, Any], revision: str, accepted_files: dict[str, tuple[str, str]] | None = None
) -> None:
    root = Path(record["path"])
    physical(root)
    info = root.stat()
    require([info.st_dev, info.st_ino] == record["identity"], "source_identity_changed")
    require(read_file(root / "REVISION") == (revision + "\n").encode(), "source_revision_changed")
    raw = read_file(root / "MANIFEST.sha256")
    require(digest(raw) == record["manifest_sha256"], "source_manifest_changed")
    names = []
    for line in raw.decode().splitlines():
        expected, name = line.split("  ./", 1)
        require(
            HEX.fullmatch(expected) and not name.startswith("/") and ".." not in name.split("/"),
            "manifest_entry_invalid",
        )
        content = read_file(root / name)
        require(digest(content) == expected, "source_content_changed")
        if accepted_files is not None and name != "REVISION":
            require(name in accepted_files, "source_not_in_accepted_git_tree")
            mode, blob = accepted_files[name]
            require(
                hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
                == blob,
                "accepted_git_blob_mismatch",
            )
            if os.name == "posix":
                require(
                    stat.S_IMODE((root / name).stat().st_mode) == int(mode[-3:], 8),
                    "accepted_git_mode_mismatch",
                )
        names.append(name)
    require(names == sorted(set(names)) and "REVISION" in names, "manifest_inventory_invalid")
    if accepted_files is not None:
        require(set(names) == set(accepted_files) | {"REVISION"}, "accepted_git_inventory_mismatch")
    require(
        read_file(root / "MANIFEST.files0")
        == b"".join(f"./{name}".encode() + b"\0" for name in names),
        "source_inventory_changed",
    )
    actual = set()
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            physical(Path(folder) / name)
        for name in files:
            actual.add((Path(folder) / name).relative_to(root).as_posix())
    require(actual == set(names) | {"MANIFEST.sha256", "MANIFEST.files0"}, "unknown_source_file")


def prepare(crm: Path, manager: Path, output: Path) -> dict[str, str]:
    crm_state, manager_state = checkout(crm), checkout(manager)
    require(
        manager_state == {"revision": MANAGER_REVISION, "tree": MANAGER_TREE},
        "accepted_manager_required",
    )
    physical(output)
    require(output.parent.is_dir(), "existing_preparation_parent_required")
    output.mkdir(mode=0o755)
    output.chmod(0o755)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    files = source_files(manager, MANAGER_REVISION)
    # Never clean up partial preparations: a failed attempt is diagnostic data.
    receipt = {
        "schema": "autostop.crm.j1-release-preparation.v1",
        "attempt_id": str(uuid.uuid4()),
        "release_id": f"{stamp}-{crm_state['revision'][:12]}-{os.getpid()}",
        "crm": crm_state,
        "manager": manager_state,
        "manager_checkout": str(manager),
        "sources": {
            name: seal(manager, output / name, MANAGER_REVISION, files)
            for name in ("manager", "work-telegram", "personal-telegram")
        },
    }
    require(
        checkout(crm) == crm_state and checkout(manager) == manager_state, "source_checkout_changed"
    )
    path = output / "preparation.json"
    return {"receipt": str(path), "sha256": publish(path, receipt)}


def verify_preparation(path: Path, expected: str, crm: Path) -> dict[str, Any]:
    value = document(path, expected)
    require(
        value["schema"] == "autostop.crm.j1-release-preparation.v1", "preparation_schema_invalid"
    )
    require(checkout(crm) == value["crm"], "prepared_crm_changed")
    require(
        value["manager"] == {"revision": MANAGER_REVISION, "tree": MANAGER_TREE},
        "prepared_manager_changed",
    )
    manager_checkout = Path(value["manager_checkout"])
    require(checkout(manager_checkout) == value["manager"], "prepared_manager_checkout_changed")
    accepted_files = source_files(manager_checkout, MANAGER_REVISION)
    require(str(uuid.UUID(value["attempt_id"])) == value["attempt_id"], "attempt_invalid")
    require(
        re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9]+", value["release_id"]),
        "release_id_invalid",
    )
    require(
        set(value["sources"]) == {"manager", "work-telegram", "personal-telegram"},
        "source_set_invalid",
    )
    for name, record in value["sources"].items():
        require(Path(record["path"]) == path.parent / name, "source_location_invalid")
        verify_source(record, MANAGER_REVISION, accepted_files)
    return value


def evidence(record: dict[str, str]) -> dict[str, Any]:
    require(set(record) == {"path", "sha256"}, "evidence_reference_invalid")
    protected(Path(record["path"]))
    return document(Path(record["path"]), record["sha256"])


def linked_evidence(value: dict[str, Any], names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    files = value.get("files")
    require(type(files) is list and files, "substantive_evidence_files_required")
    inventory = {}
    for record in files:
        require(
            set(record) == {"path", "sha256"} and HEX.fullmatch(record["sha256"]),
            "evidence_file_invalid",
        )
        require(record["path"] not in inventory, "duplicate_evidence_file")
        protected(Path(record["path"]))
        require(file_hash(Path(record["path"])) == record["sha256"], "evidence_file_changed")
        inventory[record["path"]] = record["sha256"]
    result = {}
    for name in names:
        ref = value[name]
        require(inventory.get(ref["path"]) == ref["sha256"], "primary_proof_not_in_files")
        result[name] = evidence(ref)
    if "reviewer_manifest" in result:
        reviewer = result["reviewer_manifest"]
        require(
            reviewer.get("schema") == "autostop.manager.j1-independent-acceptance.v3"
            and reviewer.get("scope") == SCOPE
            and reviewer.get("accepted") is True
            and reviewer.get("attempt_id") == value["attempt_id"],
            "independent_reviewer_required",
        )
        for binding in ("preparation_sha256", "handoff_sha256"):
            if binding in value:
                require(reviewer.get(binding) == value[binding], "reviewer_context_mismatch")
        require(
            all(
                ref == value["reviewer_manifest"] or ref in reviewer.get("accepted_refs", [])
                for ref in files
            ),
            "reviewer_does_not_accept_exact_inputs",
        )
    return result


def kernel_module() -> Any:
    path = Path(__file__).with_name("j1_kernel_bootstrap.py")
    protected(path)
    spec = importlib.util.spec_from_file_location("crm_kernel_consumer", path)
    require(spec is not None and spec.loader is not None, "fixed_kernel_consumer_required")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lifecycle_proofs(
    value: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
    evidence_root: Path | None = None,
    guard_refs: dict[str, Any] | None = None,
) -> None:
    cold = value.get("daemon_mode") == KERNEL_MODE
    require(
        value.get("instruction_revision") == INSTRUCTION_REVISION
        and value.get("instruction_root") == INSTRUCTION_ROOT
        and value.get("history_preserved") is True
        and set(value.get("denied_units", []))
        == ({"autostop-j1-browser.service"} if cold else UNSUPPORTED_UNITS)
        and value.get("daemon_mode") in {"accepted_warm", KERNEL_MODE},
        "explicit_warm_lifecycle_required",
    )
    refs = linked_evidence(
        value,
        (
            "wake_config",
            "thread_identity_receipt",
            "daemon_identity_receipt",
            "writer_admission_closure_receipt",
            "bootstrap_preservation_receipt",
            "browser_hold_receipt",
            "reviewer_manifest",
        ),
    )
    closure = refs["writer_admission_closure_receipt"]
    classes = {
        "native_units",
        "detached_daemons",
        "active_turns",
        "direct_cli",
        "crm_gateway",
        "containers",
        "scheduled_triggers",
        "browser",
    }
    require(
        closure.get("closed_writer_classes") == sorted(classes)
        and closure.get("accepted") is True
        and closure.get("files"),
        "all_writer_admission_closure_required",
    )
    linked_evidence(closure, ())
    require(
        refs["wake_config"].get("instruction_root") == INSTRUCTION_ROOT
        and refs["wake_config"].get("instruction_revision") == INSTRUCTION_REVISION,
        "wake_instruction_pin_changed",
    )
    if cold:
        require(context is not None and evidence_root is not None, "kernel_context_required")
        kernel = kernel_module()
        try:
            if guard_refs is None:
                eligible = kernel.eligibility(
                    value["bootstrap_preservation_receipt"], context, evidence_root
                )
                require(
                    eligible["instruction_ref"] == value["wake_config"],
                    "kernel_instruction_ref_changed",
                )
            else:
                preliminary = evidence(context["pre_effect_lifecycle"])
                eligibility_ref = preliminary["bootstrap_preservation_receipt"]
                native = kernel.native_acceptance(
                    value["bootstrap_preservation_receipt"],
                    eligibility_ref,
                    context,
                    evidence_root,
                    guard_refs["policy"]["sha256"],
                    guard_refs["state"]["sha256"],
                )
                require(
                    native["global_writer_closure_ref"] == value["writer_admission_closure_receipt"]
                    and refs["bootstrap_preservation_receipt"]["preserved_thread_ref"]
                    == value["thread_identity_receipt"],
                    "kernel_primary_lifecycle_refs_changed",
                )
                kernel.observe(native, context, evidence_root, initial=True)
        except kernel.KernelError as exc:
            raise BoundaryError(str(exc)) from exc
    else:
        require(
            refs["bootstrap_preservation_receipt"].get("start_or_restart_performed") is False,
            "bootstrap_start_forbidden",
        )
    require(
        refs["browser_hold_receipt"].get("activation_allowed") is False, "browser_start_forbidden"
    )
    require(
        refs["reviewer_manifest"].get("accepted") is True
        and refs["reviewer_manifest"].get("attempt_id") == value["attempt_id"],
        "independent_lifecycle_acceptance_required",
    )


def verify_dependencies(record: dict[str, str], preparation: dict[str, Any]) -> None:
    value = evidence(record)
    require(
        value.get("schema") == "autostop.crm.j1-work-dependencies.v1"
        and value.get("scope") == SCOPE
        and value.get("manager_revision") == MANAGER_REVISION
        and value.get("attempt_id") == preparation["attempt_id"]
        and value.get("source_root") == preparation["sources"]["work-telegram"]["path"]
        and value.get("dependency_runtime_sealed") is True
        and value.get("model_verified") is True
        and value.get("files"),
        "accepted_work_dependencies_required",
    )
    require(
        set(value.get("source_inputs", {})) == DEPENDENCY_INPUTS,
        "complete_work_dependency_inputs_required",
    )
    root = Path(preparation["sources"]["work-telegram"]["path"])
    for name, expected in value["source_inputs"].items():
        require(digest(read_file(root / name)) == expected, "work_dependency_source_mismatch")
    for alias, key in (
        (WORK_VENV_LINK, "venv"),
        (WORK_MODEL_LINK, "model"),
    ):
        target = Path(value[key])
        protected(target)
        require(
            target.is_dir() and alias.is_symlink() and alias.resolve(strict=True) == target,
            "accepted_work_runtime_changed",
        )
    for file in value["files"]:
        protected(Path(file["path"]))
        require(
            file_hash(Path(file["path"])) == file["sha256"],
            "work_dependency_content_changed",
        )
    linked_evidence(value, ("reviewer_manifest",))


def verify_closure(preparation: Path, expected: str, crm: Path) -> dict[str, bool]:
    """Require an independently accepted closed window before any live effects.

    The operator closes detached daemons/turns/direct CLI without destroying
    history and supplies hash-bound containment evidence. Here the effective
    native deny barrier and stopped units are checked live, without starting any
    app. The later activation receipt cannot retroactively satisfy this gate.
    """
    require(sys.platform == "linux" and os.geteuid() == 0, "linux_root_closure_required")
    require(socket.gethostname() == HOST, "activation_host_mismatch")
    value = verify_preparation(preparation, expected, crm)
    path = preparation.parent / "closure-acceptance.json"
    protected(path)
    closed = document(path)
    require(
        closed.get("schema") == "autostop.crm.j1-closure-acceptance.v1"
        and closed.get("scope") == SCOPE
        and closed.get("attempt_id") == value["attempt_id"]
        and closed.get("preparation_sha256") == expected,
        "closure_binding_mismatch",
    )
    previous = closed.get("previous_native_active")
    require(
        type(previous) is dict
        and set(previous) == set(NATIVE_UNITS)
        and all(type(active) is bool for active in previous.values()),
        "native_desired_states_required",
    )
    require(
        closed.get("daemon_mode") in {"accepted_warm", KERNEL_MODE},
        "cold_bootstrap_unsupported_keep_closed",
    )
    require(
        Path(value["sources"]["work-telegram"]["path"]).is_relative_to(WORK_RELEASE_ROOT),
        "work_media_source_layout_required",
    )
    require(all(previous.values()), "baseline_requires_all_six_native_roles")
    baseline = evidence(closed["native_baseline"])
    require(
        baseline.get("schema") == "autostop.crm.j1-native-baseline.v1"
        and baseline.get("scope") == SCOPE
        and baseline.get("attempt_id") == value["attempt_id"]
        and baseline.get("preparation_sha256") == expected
        and baseline.get("native_active") == previous
        and set(baseline.get("native_enabled", {})) == set(NATIVE_UNITS)
        and all(type(enabled) is bool for enabled in baseline["native_enabled"].values())
        and baseline.get("observed_before_closure") is True,
        "fresh_native_baseline_required",
    )
    require(
        type(closed.get("work_inbound_before")) is bool and closed.get("work_duty_paused") is True,
        "preclosed_work_duty_required",
    )
    pause = evidence(closed["work_pause_receipt"])
    require(
        pause.get("runtime_duty_enabled") is False
        and pause.get("attempt_id") == value["attempt_id"]
        and pause.get("preparation_sha256") == expected,
        "work_pause_proof_required",
    )
    linked_evidence(pause, ())
    require(
        previous["autostop-manager-scheduler.service"]
        and previous["autostop-work-telegram.service"],
        "required_live_roles_missing",
    )
    require(
        closed.get("detached_daemons_and_turns_closed") is True
        and closed.get("direct_cli_closed") is True
        and closed.get("history_preserved") is True
        and closed.get("files"),
        "independent_writer_containment_required",
    )
    for file in closed["files"]:
        protected(Path(file["path"]))
        require(digest(read_file(Path(file["path"]))) == file["sha256"], "closure_evidence_changed")
    verify_dependencies(closed["work_dependencies"], value)
    preliminary = evidence(closed["lifecycle"])
    require(
        preliminary.get("schema") == "autostop.manager.j1-lifecycle-eligibility.v3"
        and preliminary.get("scope") == SCOPE
        and preliminary.get("attempt_id") == value["attempt_id"]
        and preliminary.get("preparation_sha256") == expected
        and preliminary.get("accepted") is True,
        "pre_effect_lifecycle_acceptance_required",
    )
    require(
        preliminary.get("daemon_mode") == closed["daemon_mode"], "closure_lifecycle_mode_mismatch"
    )
    lifecycle_proofs(preliminary, context=value, evidence_root=preparation.parent)
    if closed["daemon_mode"] == KERNEL_MODE:
        eligible = evidence(preliminary["bootstrap_preservation_receipt"])
        require(
            eligible["baseline_ref"] == closed["native_baseline"], "kernel_baseline_ref_changed"
        )
    for unit in sorted(ALL_UNITS):
        fence = SYSTEMD_ROOT / (unit + ".d") / CLOSURE_DROPIN
        protected(fence)
        require(read_file(fence) == CLOSURE_BYTES, "native_closure_fence_required")
        result = subprocess.run(
            [
                "systemctl",
                "show",
                unit,
                "--property=ActiveState,SubState,DropInPaths,ExecCondition,NeedDaemonReload,ExecStartPre,ExecStartPost,ExecStop,ExecStopPost,ExecReload",
                "--no-pager",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        require(
            fields.get("NeedDaemonReload") == "no"
            and re.findall(r"argv\[\]=(.*?) ;", fields.get("ExecCondition", ""))
            == ["/usr/bin/false"]
            and all(
                fields.get(name) == ""
                for name in (
                    "ExecStartPre",
                    "ExecStartPost",
                    "ExecStop",
                    "ExecStopPost",
                    "ExecReload",
                )
            ),
            "loaded_native_closure_required",
        )
        desired_state = (
            ("active", "exited")
            if unit == "autostop-codex-start.service" and closed["daemon_mode"] == "accepted_warm"
            else ("inactive", "dead")
        )
        require(
            (fields.get("ActiveState"), fields.get("SubState")) == desired_state,
            "native_writer_not_closed",
        )
        dropins = fields.get("DropInPaths", "").split()
        require(dropins and dropins[-1] == str(fence), "effective_closure_fence_required")
    return previous


def verify_proof_chain(
    records: dict[str, Any], handoff: dict[str, Any], refs: dict[str, Any]
) -> None:
    checkpoint, offhost, runtime = (
        records[name] for name in ("checkpoint", "offhost_restore", "runtime_units")
    )
    policy = records["policy"]
    for name in ("checkpoint", "offhost_restore", "runtime_units", "lifecycle"):
        kind = {
            "checkpoint": "checkpoint",
            "offhost_restore": "offhost-restore",
            "runtime_units": "runtime-unit",
            "lifecycle": "lifecycle",
        }[name]
        require(
            records[name].get("schema") == f"autostop.manager.j1-{kind}-acceptance.v3",
            "semantic_acceptance_schema_required",
        )
    cache = checkpoint["source_cache"]
    require(
        cache["path"] == str(CACHE_PATH) and cache["observed_path"] == str(CACHE_PATH),
        "ordinary_cache_identity_required",
    )
    protected(CACHE_PATH)
    info, parent = CACHE_PATH.stat(), CACHE_PATH.parent.stat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and cache["identity"]
        == [info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]
        and cache["parent_identity"]
        == [parent.st_dev, parent.st_ino, parent.st_uid, stat.S_IMODE(parent.st_mode)],
        "checkpoint_cache_identity_changed",
    )
    require(
        all(
            binding is None or binding["ordinary_database"] == cache
            for binding in policy["bindings"].values()
        ),
        "policy_checkpoint_cache_mismatch",
    )
    checkpoint_refs = linked_evidence(
        checkpoint,
        ("snapshot_manifest", "quiet_inventory", "snapshot_receipt", "reviewer_manifest"),
    )
    snapshot = checkpoint_refs["snapshot_manifest"]
    require(
        snapshot.get("schema") == "autostop.j1.admin-snapshot.v1"
        and snapshot.get("target_manager_revision") == MANAGER_REVISION
        and snapshot.get("source_identity", {}).get("path") == str(CACHE_PATH)
        and snapshot.get("inspection", {}).get("sqlite_integrity") == "ok"
        and snapshot.get("inspection", {}).get("foreign_key_check") == "ok"
        and snapshot.get("fts_integrity_check") == "passed_on_disposable_staging_copy",
        "verified_ordinary_snapshot_required",
    )
    artifact = Path(checkpoint["snapshot_manifest"]["path"]).parent / "research.sqlite3"
    require(
        snapshot["artifact"]["name"] == "research.sqlite3"
        and artifact.stat().st_size == snapshot["artifact"]["bytes"]
        and file_hash(artifact) == snapshot["artifact"]["sha256"]
        and {"path": str(artifact), "sha256": snapshot["artifact"]["sha256"]}
        in checkpoint["files"],
        "snapshot_artifact_not_verified",
    )
    require(
        checkpoint_refs["snapshot_receipt"].get("manifest_sha256")
        == checkpoint["snapshot_manifest"]["sha256"]
        and checkpoint_refs["snapshot_receipt"].get("artifact_sha256")
        == snapshot["artifact"]["sha256"]
        and checkpoint_refs["quiet_inventory"].get("closure_sha256") == handoff["closure_sha256"]
        and checkpoint_refs["quiet_inventory"].get("continuous_closure_through_arm") is True,
        "quiet_snapshot_linkage_required",
    )
    require(
        checkpoint.get("checkpoint_id")
        and offhost["checkpoint_id"] == checkpoint["checkpoint_id"]
        and offhost["snapshot_manifest_sha256"] == checkpoint["snapshot_manifest"]["sha256"],
        "offhost_checkpoint_mismatch",
    )
    offhost_refs = linked_evidence(
        offhost,
        ("offhost_archive", "integrity_receipt", "disposable_restore_receipt", "reviewer_manifest"),
    )
    restore = offhost_refs["disposable_restore_receipt"]
    require(
        restore.get("snapshot_manifest_sha256") == offhost["snapshot_manifest_sha256"]
        and restore.get("sqlite_integrity") == "ok"
        and restore.get("fts_integrity") == "ok"
        and restore.get("retained_data_verified") is True
        and restore.get("production_restore_performed") is False,
        "exact_disposable_restore_required",
    )
    require(
        runtime["policy_sha256"] == refs["policy"]["sha256"]
        and runtime["source_index_sha256"] == INDEX_SHA256
        and runtime["sources"] == handoff["sources"]
        and runtime["profiles"] == policy["bindings"],
        "complete_runtime_policy_acceptance_required",
    )
    runtime_refs = linked_evidence(
        runtime,
        (
            "baseline_desired_state_receipt",
            "effective_units_receipt",
            "source_modes_receipt",
            "rollback_fence_receipt",
            "reviewer_manifest",
        ),
    )
    require(
        runtime_refs["baseline_desired_state_receipt"].get("native_active")
        == handoff["desired_native"]
        and runtime_refs["baseline_desired_state_receipt"].get("native_enabled")
        == handoff["desired_native_enabled"],
        "native_baseline_changed",
    )
    require(
        runtime_refs["rollback_fence_receipt"].get("old_writer_allowed") is False
        and runtime_refs["rollback_fence_receipt"].get("controller_death_test_passed") is True,
        "rollback_fence_acceptance_required",
    )
    for group in (checkpoint_refs, offhost_refs, runtime_refs):
        reviewer = group["reviewer_manifest"]
        require(
            reviewer.get("accepted") is True
            and reviewer.get("attempt_id") == handoff["attempt_id"],
            "independent_exact_proof_acceptance_required",
        )


def duty_command(intent_hash: str) -> str:
    require(HEX.fullmatch(intent_hash), "work_duty_intent_hash_required")
    return f"/usr/bin/python3 -I -S {DUTY_CONTROLLER} restore --intent-sha256 {intent_hash}"


def prepare_duty(preparation: Path, expected: str, crm: Path, marker: Path, lock: Path) -> str:
    value = verify_preparation(preparation, expected, crm)
    verify_closure(preparation, expected, crm)
    physical(marker)
    protected(lock)
    closed_path = preparation.parent / "closure-acceptance.json"
    closed = document(closed_path)
    intent = {
        "schema": "autostop.crm.j1-work-duty-intent.v1",
        "scope": SCOPE,
        "attempt_id": value["attempt_id"],
        "preparation_path": str(preparation),
        "preparation_sha256": expected,
        "closure_sha256": file_hash(closed_path),
        "crm_revision": value["crm"]["revision"],
        "manager_revision": MANAGER_REVISION,
        "inbound_enabled": closed["work_inbound_before"],
        "maintenance_marker": str(marker),
        "deploy_lock": str(lock),
        "controller_sha256": file_hash(crm / "scripts/j1_work_duty.py"),
    }
    intent_hash = publish(preparation.parent / "work-duty-intent.json", intent)
    # This single fixed post-start operation is inventoried by guard v2; all
    # other lifecycle hooks stay reset. No receipt-supplied command is executed.
    dropin = (
        "[Service]\nExecStartPost=\nExecStartPost=+" + duty_command(intent_hash) + "\n"
    ).encode()
    target = preparation.parent / "work-duty-restore.conf"
    with target.open("xb") as stream:
        stream.write(dropin)
        stream.flush()
        os.fsync(stream.fileno())
    if closed["daemon_mode"] == KERNEL_MODE:
        for name, unit in (
            ("kernel-readiness.conf", None),
            ("kernel-wake-readiness.conf", "autostop-codex-wake.service"),
        ):
            target = preparation.parent / name
            with target.open("xb") as stream:
                stream.write(kernel_module().gate_bytes(intent_hash, unit=unit))
                stream.flush()
                os.fsync(stream.fileno())
        for name, raw in (
            (KERNEL_RECOVERY_UNIT, kernel_module().recovery_bytes(intent_hash)),
            (KERNEL_RECOVERY_DROPIN, kernel_module().recovery_dropin_bytes()),
        ):
            with (preparation.parent / name).open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
    return intent_hash


def verify_duty_controller(
    handoff: dict[str, Any], policy: dict[str, Any], *, cold: bool = False
) -> str:
    reference = handoff["work_duty_intent"]
    require(Path(reference["path"]) == DUTY_INTENT, "fixed_work_duty_intent_required")
    intent = evidence(reference)
    require(
        intent.get("schema") == "autostop.crm.j1-work-duty-intent.v1"
        and intent.get("scope") == SCOPE
        and intent.get("attempt_id") == handoff["attempt_id"]
        and intent.get("preparation_sha256") == handoff["preparation_sha256"]
        and intent.get("closure_sha256") == handoff["closure_sha256"]
        and intent.get("crm_revision") == handoff["crm"]["revision"]
        and intent.get("manager_revision") == MANAGER_REVISION
        and intent.get("controller_sha256") == handoff["work_duty_controller_sha256"]
        and type(intent.get("inbound_enabled")) is bool,
        "work_duty_intent_binding_required",
    )
    expected_files = [
        {"path": str(DUTY_CONTROLLER), "sha256": intent["controller_sha256"]},
        reference,
        {
            "path": str(SYSTEMD_ROOT / "autostop-work-telegram.service.d" / DUTY_DROPIN),
            "sha256": digest(
                (
                    "[Service]\nExecStartPost=\nExecStartPost=+"
                    + duty_command(reference["sha256"])
                    + "\n"
                ).encode()
            ),
        },
    ]
    files = policy["bindings"]["autostop-work-telegram.service"]["unit_files"]
    for record in expected_files:
        require(
            record in files and record in handoff["files"], "work_duty_guard_inventory_required"
        )
        protected(Path(record["path"]))
        require(file_hash(Path(record["path"])) == record["sha256"], "work_duty_controller_changed")
    if cold:
        helper = {
            "path": str(KERNEL_CONSUMER),
            "sha256": file_hash(Path(__file__).with_name("j1_kernel_bootstrap.py")),
        }
        require(helper in handoff["files"], "kernel_consumer_not_bound")
        require(file_hash(KERNEL_CONSUMER) == helper["sha256"], "kernel_consumer_changed")
        recovery_files = []
        for path, raw in (
            (
                SYSTEMD_ROOT / KERNEL_RECOVERY_UNIT,
                kernel_module().recovery_bytes(reference["sha256"]),
            ),
            (
                SYSTEMD_ROOT / "autostop-codex-start.service.d" / KERNEL_RECOVERY_DROPIN,
                kernel_module().recovery_dropin_bytes(),
            ),
        ):
            require(read_file(path) == raw, "fixed_kernel_recovery_required")
            record = {"path": str(path), "sha256": digest(raw)}
            require(record in handoff["files"], "kernel_recovery_inventory_required")
            recovery_files.append(record)
        for unit in NATIVE_UNITS:
            gate = SYSTEMD_ROOT / (unit + ".d") / KERNEL_GATE_DROPIN
            raw = kernel_module().gate_bytes(reference["sha256"], unit=unit)
            require(read_file(gate) == raw, "fixed_kernel_gate_required")
            bound = [
                *expected_files[:2],
                helper,
                *recovery_files,
                {"path": str(gate), "sha256": digest(raw)},
            ]
            for record in bound:
                require(
                    record in policy["bindings"][unit]["unit_files"] and record in handoff["files"],
                    "kernel_gate_inventory_required",
                )
    return reference["sha256"]


def verify_loaded_guards(policy_hash: str, intent_hash: str, *, cold: bool = False) -> None:
    for unit in sorted(ALL_UNITS):
        result = subprocess.run(
            [
                "systemctl",
                "show",
                unit,
                "--property=NeedDaemonReload,ExecCondition,ExecStart,ExecStartPre,ExecStartPost,ExecReload,ExecStop,ExecStopPost",
                "--no-pager",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        prefix = f"/usr/bin/python3 -I -S {GUARD_ROOT / 'guard.py'}"
        conditions = [f"{prefix} check --policy-sha256 {policy_hash} --unit {unit}"]
        starts = [f"{prefix} launch --policy-sha256 {policy_hash} --unit {unit}"]
        if cold and unit == "autostop-codex-start.service":
            bootstrap = "/usr/bin/python3 -I -S -B /usr/local/libexec/autostop-j1-kernel-bootstrap/bootstrap.py"
            conditions, starts = [bootstrap + " check"], [bootstrap + " run"]
        elif cold and unit in NATIVE_UNITS:
            conditions.append(kernel_module().gate_command(intent_hash))
        require(
            fields.get("NeedDaemonReload") == "no"
            and re.findall(r"argv\[\]=(.*?) ;", fields.get("ExecCondition", "")) == conditions
            and re.findall(r"argv\[\]=(.*?) ;", fields.get("ExecStart", "")) == starts
            and all(
                fields.get(name) == ""
                for name in (
                    "ExecStartPre",
                    "ExecStop",
                    "ExecStopPost",
                    "ExecReload",
                )
            ),
            "loaded_guard_dispatch_required",
        )
        expected_post = (
            [duty_command(intent_hash)] if unit == "autostop-work-telegram.service" else []
        )
        require(
            re.findall(r"argv\[\]=(.*?) ;", fields.get("ExecStartPost", "")) == expected_post,
            "loaded_guard_dispatch_required",
        )
        if cold and unit in NATIVE_UNITS:
            ordering = subprocess.run(
                [
                    "systemctl",
                    "show",
                    unit,
                    "--property=Requires,Wants,BindsTo,After",
                    "--no-pager",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            order = dict(line.split("=", 1) for line in ordering.stdout.splitlines() if "=" in line)
            dependencies = {"autostop-codex-start.service", "autostop-j1-cache-view.service"}
            require(
                "autostop-j1-cache-view.service" in order.get("Requires", "").split()
                and "autostop-codex-start.service" in order.get("Wants", "").split()
                and "autostop-codex-start.service" in order.get("BindsTo", "").split()
                and dependencies <= set(order.get("After", "").split()),
                "loaded_kernel_ordering_required",
            )
    if cold:
        verify_loaded_recovery(intent_hash)


def verify_loaded_recovery(intent_hash: str) -> None:
    result = subprocess.run(
        ["systemctl", "show", "autostop-codex-start.service", "--property=Wants", "--no-pager"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    require(
        KERNEL_RECOVERY_UNIT in result.stdout.removeprefix("Wants=").split(),
        "bootstrap_recovery_want_required",
    )
    result = subprocess.run(
        [
            "systemctl",
            "show",
            KERNEL_RECOVERY_UNIT,
            "--property=Type,User,Group,After,BindsTo,ExecStart,ExecStartPre,ExecStartPost,ExecCondition,ExecReload,ExecStop,ExecStopPost,NeedDaemonReload,FragmentPath,DropInPaths",
            "--no-pager",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    fields = dict(row.split("=", 1) for row in result.stdout.splitlines() if "=" in row)
    expected = duty_command(intent_hash).replace(
        " restore --intent-sha256 ", " recover --intent-sha256 "
    )
    require(
        fields.get("Type") == "oneshot"
        and fields.get("User") == "root"
        and fields.get("Group") == "root"
        and fields.get("NeedDaemonReload") == "no"
        and fields.get("FragmentPath") == str(SYSTEMD_ROOT / KERNEL_RECOVERY_UNIT)
        and fields.get("DropInPaths") == ""
        and "autostop-codex-start.service" in fields.get("BindsTo", "").split()
        and {"autostop-codex-start.service", "autostop-j1-cache-view.service"}
        <= set(fields.get("After", "").split())
        and re.findall(r"argv\[\]=(.*?) ;", fields.get("ExecStart", "")) == [expected]
        and all(
            fields.get(key) == ""
            for key in (
                "ExecStartPre",
                "ExecStartPost",
                "ExecCondition",
                "ExecReload",
                "ExecStop",
                "ExecStopPost",
            )
        ),
        "loaded_kernel_recovery_required",
    )


def verify_work_startup(binding: dict[str, Any]) -> None:
    """Bind paused durable startup to the environment the native guard checks.

    Source89 resets runtime duty from this environment on *every* daemon start.
    A successful duty-set before stopping it cannot make an incoming=1 start safe.
    """
    receipt_path = binding["runtime_receipt"]
    reference = next(
        (record for record in binding["runtime_files"] if record["path"] == receipt_path),
        None,
    )
    require(reference is not None, "work_startup_receipt_hash_required")
    receipt = evidence(reference)
    environment = receipt.get("stable_input_environment")
    require(
        type(environment) is dict
        and all(type(key) is str and type(value) is str for key, value in environment.items())
        and digest(canonical(environment)) == binding["stable_environment_sha256"]
        and receipt.get("stable_input_environment_sha256") == binding["stable_environment_sha256"],
        "work_startup_environment_binding_required",
    )
    require(
        environment.get("AUTOSTOP_WORK_TELEGRAM_MONITOR_INCOMING") == "0"
        and environment.get("AUTOSTOP_WORK_TELEGRAM_DURABLE_INBOX") == "1",
        "work_paused_durable_startup_required",
    )


def verify_acceptance(handoff: dict[str, Any], handoff_hash: str, response: Path) -> None:
    protected(response)
    admission = document(response)
    require(
        set(admission) == {"schema", "handoff_sha256", "attempt_id", "scope", "evidence"},
        "acceptance_fields_invalid",
    )
    require(
        admission["schema"] == "autostop.crm.j1-activation-acceptance.v1"
        and admission["scope"] == SCOPE
        and admission["handoff_sha256"] == handoff_hash
        and admission["attempt_id"] == handoff["attempt_id"],
        "acceptance_binding_mismatch",
    )
    refs = admission["evidence"]
    require(
        set(refs)
        == {
            "policy",
            "state",
            "quiet_window",
            "checkpoint",
            "offhost_restore",
            "runtime_units",
            "lifecycle",
        },
        "acceptance_evidence_incomplete",
    )
    require(
        Path(refs["policy"]["path"]) == GUARD_POLICY
        and Path(refs["state"]["path"]) == GUARD_STATE / "armed.json",
        "fixed_guard_paths_required",
    )
    pending = GUARD_STATE / "arming-pending.json"
    protected(GUARD_ROOT / "guard.py")
    protected(GUARD_ROOT / "source-index.json")
    protected(GUARD_STATE)
    require(not pending.exists() and not pending.is_symlink(), "guard_arm_pending")
    require(
        digest(read_file(GUARD_ROOT / "guard.py")) == GUARD_SHA256
        and digest(read_file(GUARD_ROOT / "source-index.json")) == INDEX_SHA256,
        "accepted_guard_package_required",
    )
    records = {name: evidence(record) for name, record in refs.items()}
    policy, state, quiet = (records[name] for name in ("policy", "state", "quiet_window"))
    require(
        policy["schema"] == "autostop.j1.candidate-policy.v2"
        and policy["revision"] == MANAGER_REVISION
        and policy["tree"] == MANAGER_TREE
        and policy["guard_sha256"] == GUARD_SHA256
        and policy["index_sha256"] == INDEX_SHA256
        and policy["attempt_id"] == handoff["attempt_id"]
        and policy.get("host") == HOST,
        "guard_policy_mismatch",
    )
    require(
        set(policy["bindings"]) == REQUIRED_UNITS | {"autostop-telegram.service"},
        "guard_profile_set_mismatch",
    )
    bound_units = REQUIRED_UNITS | (
        {"autostop-telegram.service"}
        if handoff["desired_native"]["autostop-telegram.service"]
        else set()
    )
    for unit in bound_units:
        binding = policy["bindings"][unit]
        require(type(binding) is dict, "required_profile_unbound")
        name = (
            "work-telegram"
            if unit in {"autostop-work-telegram.service", "autostop-codex-wake.service"}
            else "manager"
        )
        if unit == "autostop-telegram.service":
            name = "personal-telegram"
        source = handoff["sources"][name]
        require(
            binding["source_root"] == source["path"]
            and binding["source_identity"] == source["identity"]
            and binding["manifest_sha256"] == source["manifest_sha256"],
            "guard_source_binding_mismatch",
        )
    verify_work_startup(policy["bindings"]["autostop-work-telegram.service"])
    require(
        state["schema"] == "autostop.j1.candidate-state.v2"
        and state["scope"] == SCOPE
        and state["attempt_id"] == handoff["attempt_id"]
        and state["policy_sha256"] == refs["policy"]["sha256"]
        and state["guard_sha256"] == GUARD_SHA256
        and state["index_sha256"] == INDEX_SHA256
        and state["revision"] == MANAGER_REVISION
        and state["candidate_may_have_written"] is True
        and state["old_writer_allowed"] is False
        and state["prerequisite_sha256"] == refs["quiet_window"]["sha256"],
        "immutable_guard_state_required",
    )
    flags = (
        "all_writers_closed",
        "detached_daemons_and_turns_closed",
        "direct_cli_closed",
        "snapshot_verified",
        "offhost_disposable_restore_verified",
        "effective_units_and_runtime_accepted",
        "rollback_fence_accepted",
    )
    require(
        quiet["schema"] == "autostop.j1.quiet-window-acceptance.v2"
        and quiet["scope"] == SCOPE
        and quiet["attempt_id"] == handoff["attempt_id"]
        and quiet["policy_sha256"] == refs["policy"]["sha256"]
        and all(quiet.get(flag) is True for flag in flags),
        "quiet_window_not_accepted",
    )
    for name in ("checkpoint", "offhost_restore", "runtime_units", "lifecycle"):
        record = records[name]
        require(
            record.get("scope") == SCOPE
            and record.get("attempt_id") == handoff["attempt_id"]
            and record.get("handoff_sha256") == handoff_hash
            and record.get("accepted") is True
            and record.get("files"),
            f"{name}_acceptance_required",
        )
        for file in record["files"]:
            protected(Path(file["path"]))
            require(file_hash(Path(file["path"])) == file["sha256"], f"{name}_evidence_changed")
    verify_proof_chain(records, handoff, refs)
    lifecycle = records["lifecycle"]
    preliminary = evidence(handoff["pre_effect_lifecycle"])
    require(
        lifecycle.get("daemon_mode") in {"accepted_warm", KERNEL_MODE},
        "explicit_warm_lifecycle_required",
    )
    require(
        lifecycle.get("daemon_mode") == preliminary.get("daemon_mode"), "lifecycle_mode_changed"
    )
    cold = lifecycle.get("daemon_mode") == KERNEL_MODE
    lifecycle_proofs(
        lifecycle, context=handoff, evidence_root=response.parent, guard_refs=refs if cold else None
    )
    require(
        lifecycle["pre_effect_acceptance"] == handoff["pre_effect_lifecycle"],
        "pre_effect_lifecycle_changed",
    )
    for unit in ALL_UNITS:
        fence = SYSTEMD_ROOT / (unit + ".d") / CLOSURE_DROPIN
        require(not fence.exists() and not fence.is_symlink(), "closure_transition_incomplete")
    intent_hash = verify_duty_controller(handoff, policy, cold=cold)
    verify_loaded_guards(refs["policy"]["sha256"], intent_hash, cold=cold)
    # Recheck all inputs after consuming acceptance. systemd launch will check
    # them again in the real service context; this is not native launch proof.
    for record in handoff["sources"].values():
        protected(Path(record["path"]))
        verify_source(record, MANAGER_REVISION)
    for file in handoff["files"]:
        protected(Path(file["path"]))
        require(digest(read_file(Path(file["path"]))) == file["sha256"], "handoff_input_changed")


def admit(
    preparation: Path, expected: str, crm: Path, image: str, backup: Path, timeout: float
) -> str:
    require(sys.platform == "linux" and os.geteuid() == 0, "linux_root_activation_required")
    value = verify_preparation(preparation, expected, crm)
    protected(preparation)
    previous = verify_closure(preparation, expected, crm)
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image), "exact_image_id_required")
    require(0 < timeout <= 480, "bounded_admission_required")
    fixed = [crm / ".env", Path("/opt/AutostopManager/.crm-mcp.env"), backup / "manifest.json"]
    fixed += [SYSTEMD_ROOT / unit for unit in NATIVE_UNITS]
    fixed += [
        SYSTEMD_ROOT / (unit + ".d") / "98-j1-release-hooks-held.conf" for unit in sorted(ALL_UNITS)
    ]
    fixed += [
        DUTY_CONTROLLER,
        DUTY_INTENT,
        SYSTEMD_ROOT / "autostop-work-telegram.service.d" / DUTY_DROPIN,
    ]
    if document(preparation.parent / "closure-acceptance.json")["daemon_mode"] == KERNEL_MODE:
        fixed += [
            KERNEL_CONSUMER,
            SYSTEMD_ROOT / KERNEL_RECOVERY_UNIT,
            SYSTEMD_ROOT / "autostop-codex-start.service.d" / KERNEL_RECOVERY_DROPIN,
        ]
        fixed += [SYSTEMD_ROOT / (unit + ".d") / KERNEL_GATE_DROPIN for unit in NATIVE_UNITS]
    handoff = {
        "schema": "autostop.crm.j1-activation-handoff.v1",
        "scope": SCOPE,
        "attempt_id": value["attempt_id"],
        "host": HOST,
        "preparation_sha256": expected,
        "crm": value["crm"],
        "manager": value["manager"],
        "sources": value["sources"],
        "closure_sha256": digest(read_file(preparation.parent / "closure-acceptance.json")),
        "pre_effect_lifecycle": document(preparation.parent / "closure-acceptance.json")[
            "lifecycle"
        ],
        "desired_native": previous,
        "desired_native_enabled": evidence(
            document(preparation.parent / "closure-acceptance.json")["native_baseline"]
        )["native_enabled"],
        "image": image,
        "work_duty_controller_sha256": file_hash(crm / "scripts/j1_work_duty.py"),
        "j1_probe_sha256": file_hash(crm / "scripts/j1_probe_readonly.py"),
        "work_duty_intent": {"path": str(DUTY_INTENT), "sha256": file_hash(DUTY_INTENT)},
        "protected_backup": str(backup),
        "files": [{"path": str(path), "sha256": digest(read_file(path))} for path in fixed],
    }
    handoff_path = preparation.parent / "handoff.json"
    handoff_hash = publish(handoff_path, handoff)
    print(json.dumps({"handoff": str(handoff_path), "sha256": handoff_hash}), flush=True)
    response = preparation.parent / "activation-acceptance.json"
    deadline = time.monotonic() + timeout
    while not response.exists():
        require(not response.is_symlink(), "acceptance_symlink_denied")
        require(time.monotonic() < deadline, "activation_acceptance_timeout_keep_closed")
        time.sleep(min(0.2, max(0.001, deadline - time.monotonic())))
    verify_acceptance(handoff, handoff_hash, response)
    publish(
        preparation.parent / "admitted.json",
        {
            "schema": "autostop.crm.j1-admission-receipt.v1",
            "scope": SCOPE,
            "attempt_id": value["attempt_id"],
            "handoff_sha256": handoff_hash,
            "preparation_sha256": expected,
            "acceptance_sha256": digest(read_file(response)),
        },
    )
    return handoff_hash


def rollback_allowed() -> bool:
    # Presence, malformed data, broken symlinks, permission errors or uncertain
    # arm publication must never be interpreted as permission for old writers.
    protected(GUARD_STATE)
    return not any(
        path.exists() or path.is_symlink()
        for path in (GUARD_STATE / "armed.json", GUARD_STATE / "arming-pending.json")
    )


def process_bytes(pid: str, name: str) -> bytes:
    with (PROC_ROOT / pid / name).open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    require(len(raw) <= 2 * 1024 * 1024, "native_process_observation_too_large")
    return raw


def native_process_identity(timeout: float = 10) -> tuple[str, str]:
    result = subprocess.run(
        [
            "systemctl",
            "show",
            "autostop-j1.service",
            "--property=MainPID,ActiveState,SubState,ControlGroup",
            "--no-pager",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    pid = fields.get("MainPID", "")
    group = "/system.slice/autostop-j1.service"
    require(
        pid.isdecimal()
        and int(pid) > 1
        and fields.get("ActiveState") == "active"
        and fields.get("SubState") == "running"
        and fields.get("ControlGroup") == group,
        "admitted_j1_process_required",
    )
    start = process_bytes(pid, "stat").rsplit(b") ", 1)[1].split()[19].decode()
    require(start.isdecimal(), "native_process_identity_required")
    groups = process_bytes(pid, "cgroup").decode().splitlines()
    require(
        groups and all(line.rsplit(":", 1)[1] == group for line in groups),
        "native_j1_cgroup_required",
    )
    return pid, start


def native_worker_argv(binding: dict[str, Any]) -> list[bytes]:
    return [
        item.encode()
        for item in (binding["interpreter"], "-P", "-m", "autostop_manager.j1_research", "worker")
    ]


def native_process_environment(pid: str) -> dict[str, str]:
    raw = process_bytes(pid, "environ")
    require(raw.endswith(b"\0"), "native_environment_invalid")
    environment = unique([tuple(item.decode().split("=", 1)) for item in raw.split(b"\0")[:-1]])
    return {
        key: value
        for key, value in environment.items()
        if key not in {"INVOCATION_ID", "JOURNAL_STREAM", "SYSTEMD_EXEC_PID"}
    }


class NativeSQLitePending(BoundaryError):
    def __init__(self, identity: tuple[str, str]):
        super().__init__("native_sqlite_import_pending")
        self.identity = identity


def native_probe_context(
    binding: dict[str, Any], *, allow_pending_sqlite: bool = False, timeout: float = 10
) -> tuple[str, str, dict[str, str], list[dict[str, str]]]:
    """Observe the admitted worker without opening SQLite or importing Manager."""
    pid, start = native_process_identity(timeout)
    require(
        os.readlink(PROC_ROOT / pid / "exe") == binding["interpreter_realpath"]
        and os.readlink(PROC_ROOT / pid / "cwd") == binding["cwd"],
        "native_j1_runtime_changed",
    )
    interpreter = Path(binding["interpreter"])
    require(
        str(interpreter.resolve(strict=True)) == binding["interpreter_realpath"]
        and [interpreter.stat().st_dev, interpreter.stat().st_ino]
        == binding["interpreter_identity"]
        and file_hash(Path(binding["interpreter_realpath"])) == binding["interpreter_sha256"],
        "native_j1_interpreter_changed",
    )
    require(
        process_bytes(pid, "cmdline").split(b"\0")[:-1] == native_worker_argv(binding),
        "native_j1_entrypoint_changed",
    )
    stable = native_process_environment(pid)
    require(stable.get("PYTHONPATH") == binding["source_root"], "native_j1_source_changed")
    before_dispatch = {**stable, "PYTHONPATH": binding["selector"]}
    require(
        digest(canonical(before_dispatch)) == binding["stable_environment_sha256"]
        and stable.get("AUTOSTOP_MANAGER_ENV_FILE") == "/dev/null"
        and stable.get("PYTHONDONTWRITEBYTECODE") == "1"
        and stable.get("PYTHONSAFEPATH") == "1"
        and stable.get("PYTHONNOUSERSITE") == "1"
        and stable.get("AUTOSTOP_J1_CACHE_DIR") == CACHE_PATH.parent.as_posix(),
        "native_j1_environment_changed",
    )
    inventory = {item["path"]: item["sha256"] for item in binding["runtime_files"]}
    for name, expected in inventory.items():
        protected(Path(name))
        require(file_hash(Path(name)) == expected, "native_runtime_file_changed")
    libraries = set()
    for line in process_bytes(pid, "maps").decode().splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) == 6:
            name = Path(parts[-1]).name
            if name.startswith("_sqlite3.") or name.startswith("libsqlite3."):
                libraries.add(parts[-1])
    refs = []
    for name in sorted(libraries):
        require(name in inventory, "native_sqlite_not_in_accepted_runtime")
        if Path(name).name.startswith("libsqlite3."):
            require(
                str(Path(name).parent) in stable.get("LD_LIBRARY_PATH", "").split(os.pathsep),
                "native_sqlite_loader_path_required",
            )
        refs.append({"path": name, "sha256": inventory[name]})
    complete = any(Path(name).name.startswith("_sqlite3.") for name in libraries) and any(
        Path(name).name.startswith("libsqlite3.") for name in libraries
    )
    if allow_pending_sqlite and not complete:
        raise NativeSQLitePending((pid, start))
    require(complete, "native_sqlite_mapping_required")
    return pid, start, stable, refs


def wait_native_probe_ready(binding: dict[str, Any], policy_hash: str) -> tuple[str, str]:
    # The monotonic deadline bounds polling and systemctl. On the production
    # Linux caller, also interrupt slow local reads/hashing within the same cap.
    if sys.platform != "linux":
        return _wait_native_probe_ready(binding, policy_hash)
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "native_readiness_timer_busy")
    previous = signal.signal(signal.SIGALRM, native_readiness_timeout)
    try:
        signal.setitimer(signal.ITIMER_REAL, NATIVE_READY_SECONDS)
        return _wait_native_probe_ready(binding, policy_hash)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def native_readiness_timeout(signum, frame):
    raise BoundaryError("native_j1_readiness_timeout")


def _wait_native_probe_ready(binding: dict[str, Any], policy_hash: str) -> tuple[str, str]:
    """Wait only for the admitted guard's exec and the worker's SQLite import."""
    require(HEX.fullmatch(policy_hash), "native_policy_hash_invalid")
    deadline = time.monotonic() + NATIVE_READY_SECONDS
    identity = None
    worker_seen = False
    guard_path = GUARD_ROOT / "guard.py"
    guard_argv = [
        item.encode()
        for item in (
            str(GUARD_PYTHON),
            "-I",
            "-S",
            str(guard_path),
            "launch",
            "--policy-sha256",
            policy_hash,
            "--unit",
            "autostop-j1.service",
        )
    ]
    worker_argv = native_worker_argv(binding)
    while True:
        remaining = deadline - time.monotonic()
        require(remaining > 0, "native_j1_readiness_timeout")
        current = native_process_identity(min(10, remaining))
        require(identity is None or current == identity, "native_j1_process_changed_during_start")
        identity = current
        pid, _ = identity
        argv = process_bytes(pid, "cmdline").split(b"\0")[:-1]
        if argv == guard_argv:
            require(not worker_seen, "native_j1_entrypoint_changed")
            protected(guard_path)
            require(file_hash(guard_path) == GUARD_SHA256, "accepted_guard_package_required")
            executable = os.readlink(PROC_ROOT / pid / "exe")
            cwd = os.readlink(PROC_ROOT / pid / "cwd")
            environment = native_process_environment(pid)
            after = process_bytes(pid, "cmdline").split(b"\0")[:-1]
            if after != guard_argv:
                # execve can fall between /proc reads; only this exact forward
                # transition is permitted, and identity is checked again above.
                require(after == worker_argv, "native_j1_entrypoint_changed")
                worker_seen = True
                continue
            require(
                executable == str(GUARD_PYTHON.resolve(strict=True)) and cwd == binding["cwd"],
                "native_j1_guard_runtime_changed",
            )
            require(
                environment.get("PYTHONPATH") == binding["selector"]
                and digest(canonical(environment)) == binding["stable_environment_sha256"],
                "native_j1_guard_environment_changed",
            )
        else:
            require(argv == worker_argv, "native_j1_entrypoint_changed")
            worker_seen = True
            remaining = deadline - time.monotonic()
            require(remaining > 0, "native_j1_readiness_timeout")
            try:
                observed = native_probe_context(
                    binding, allow_pending_sqlite=True, timeout=min(10, remaining)
                )
            except NativeSQLitePending as pending:
                require(pending.identity == identity, "native_j1_process_changed_during_start")
            else:
                require(observed[:2] == identity, "native_j1_process_changed_during_start")
                require(time.monotonic() < deadline, "native_j1_readiness_timeout")
                return identity
        remaining = deadline - time.monotonic()
        require(remaining > 0, "native_j1_readiness_timeout")
        time.sleep(min(0.1, remaining))


def native_probe(preparation: Path, expected: str, crm: Path) -> None:
    require(
        sys.platform == "linux" and os.geteuid() == 0 and socket.gethostname() == HOST,
        "linux_root_native_probe_required",
    )
    value = verify_preparation(preparation, expected, crm)
    admitted_path = preparation.parent / "admitted.json"
    protected(admitted_path)
    admitted = document(admitted_path)
    require(
        admitted.get("schema") == "autostop.crm.j1-admission-receipt.v1"
        and admitted.get("scope") == SCOPE
        and admitted.get("attempt_id") == value["attempt_id"]
        and admitted.get("preparation_sha256") == expected,
        "native_probe_admission_required",
    )
    handoff = document(preparation.parent / "handoff.json", admitted["handoff_sha256"])
    response = preparation.parent / "activation-acceptance.json"
    document(response, admitted["acceptance_sha256"])
    verify_acceptance(handoff, admitted["handoff_sha256"], response)
    refs = document(response)["evidence"]
    binding = evidence(refs["policy"])["bindings"]["autostop-j1.service"]
    ready_identity = wait_native_probe_ready(binding, refs["policy"]["sha256"])
    pid, start, environment, libraries = native_probe_context(binding)
    require((pid, start) == ready_identity, "native_j1_process_changed_during_start")
    verify_native_probe_runtime(binding, environment, refs["policy"]["sha256"])
    shim = crm / "scripts/j1_probe_readonly.py"
    require(file_hash(shim) == handoff["j1_probe_sha256"], "fixed_native_probe_changed")
    result = subprocess.run(
        [
            binding["interpreter"],
            "-P",
            str(shim),
            "--sqlite-files-json",
            canonical(libraries).decode(),
        ],
        env=environment,
        cwd=binding["cwd"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    current_pid, current_start, _, current_libraries = native_probe_context(binding)
    require(
        (pid, start, libraries) == (current_pid, current_start, current_libraries),
        "native_j1_process_changed_during_probe",
    )
    status = json.loads(result.stdout, object_pairs_hook=unique, parse_constant=invalid_number)
    require(
        status.get("ok") is True and status.get("cache_ready") is True, "native_j1_health_failed"
    )
    print(
        json.dumps(
            {
                "ok": True,
                "cache_ready": True,
                "native_runtime_bound": True,
                "sql_readonly_enforced": True,
            }
        )
    )


def verify_native_probe_runtime(
    binding: dict[str, Any], environment: dict[str, str], policy_hash: str
) -> None:
    # Reuse only the fixed v2 package's pure file/environment validators. Its
    # check/launch/authorize operations remain exclusive to real systemd units.
    path = GUARD_ROOT / "guard.py"
    require(file_hash(path) == GUARD_SHA256, "accepted_guard_package_required")
    spec = importlib.util.spec_from_file_location("accepted_j1_probe_runtime", path)
    require(spec is not None and spec.loader is not None, "accepted_guard_loader_required")
    guard = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(guard)
    finally:
        sys.dont_write_bytecode = previous
    require(file_hash(path) == GUARD_SHA256, "accepted_guard_package_required")
    guard.END = time.monotonic() + 20
    try:
        guard.verify_source(binding, document(GUARD_ROOT / "source-index.json", INDEX_SHA256))
        guard.verify_runtime(binding, SCOPE)
        guard.verify_environment({**environment, "PYTHONPATH": binding["selector"]}, binding)
        guard.verify_units(binding, "autostop-j1.service", policy_hash)
    except guard.Deny as error:
        raise BoundaryError(f"accepted_native_probe_runtime_denied: {error}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    stage = sub.add_parser("prepare")
    stage.add_argument("--crm-source", type=Path, required=True)
    stage.add_argument("--manager-source", type=Path, required=True)
    stage.add_argument("--output", type=Path, required=True)
    for operation in ("verify", "verify-closure", "admit", "prepare-duty", "native-probe"):
        command = sub.add_parser(operation)
        command.add_argument("--preparation", type=Path, required=True)
        command.add_argument("--sha256", required=True)
        command.add_argument("--crm-source", type=Path, required=True)
        if operation == "prepare-duty":
            command.add_argument("--marker", type=Path, required=True)
            command.add_argument("--lock", type=Path, required=True)
        if operation == "admit":
            command.add_argument("--image", required=True)
            command.add_argument("--backup", type=Path, required=True)
            command.add_argument("--timeout", type=float, required=True)
    sub.add_parser("rollback-allowed")
    args = parser.parse_args()
    try:
        if args.operation == "prepare":
            print(json.dumps(prepare(args.crm_source, args.manager_source, args.output)))
        elif args.operation == "verify":
            value = verify_preparation(args.preparation, args.sha256, args.crm_source)
            print(
                "\n".join(
                    [
                        value["release_id"],
                        value["sources"]["manager"]["path"],
                        value["sources"]["work-telegram"]["path"],
                        value["sources"]["personal-telegram"]["path"],
                    ]
                )
            )
        elif args.operation == "prepare-duty":
            print(
                prepare_duty(args.preparation, args.sha256, args.crm_source, args.marker, args.lock)
            )
        elif args.operation == "native-probe":
            native_probe(args.preparation, args.sha256, args.crm_source)
        elif args.operation == "admit":
            admit(
                args.preparation,
                args.sha256,
                args.crm_source,
                args.image,
                args.backup,
                args.timeout,
            )
        elif args.operation == "verify-closure":
            previous = verify_closure(args.preparation, args.sha256, args.crm_source)
            closed = document(args.preparation.parent / "closure-acceptance.json")
            enabled = evidence(closed["native_baseline"])["native_enabled"]
            print(
                "\n".join(
                    [
                        *(str(int(previous[unit])) for unit in NATIVE_UNITS),
                        str(int(closed["work_inbound_before"])),
                        *(str(int(enabled[unit])) for unit in NATIVE_UNITS),
                    ]
                )
            )
        elif not rollback_allowed():
            return 2
        return 0
    except (
        BoundaryError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"J1_RELEASE_DENY: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
