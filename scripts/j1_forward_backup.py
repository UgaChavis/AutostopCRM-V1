"""Fixed-scope durable backup and independent disposable restore for J1.

Standalone stdlib deliberately permits off-host verification without importing
Linux authority code. Capture requires the orchestrator's live fence observer;
observations do not themselves prove continuous closure. No production restore,
service control, shell execution, authentication test or RAM capture is provided.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SCHEMA = "autostop.j1.forward-backup-artifacts.v1"
PROFILE = "crm_only_native_preserved_full_v1"
ROOTS = (
    ("crm", "/opt/autostopcrm/data"),
    ("manager", "/opt/AutostopManager/data/autostop_manager.sqlite3"),
    ("scheduler", "/var/lib/autostop-manager-scheduler/registry.sqlite3"),
    ("j1", "/var/cache/autostop-j1/research.sqlite3"),
    ("telegram", "/var/lib/autostop-telegram"),
    ("work_telegram", "/var/lib/autostop-work-telegram"),
    ("m2", "/var/lib/autostop-manager/roles"),
    ("integration", "/var/lib/autostop-manager/integration"),
    ("codex", "/root/.codex"),
    ("telegram_config", "/etc/autostop-telegram"),
    ("work_telegram_config", "/etc/autostop-work-telegram"),
    ("crm_env", "/opt/autostopcrm/.env"),
    ("manager_env", "/opt/AutostopManager/.env"),
    ("manager_crm_env", "/opt/AutostopManager/.crm-mcp.env"),
    ("manager_config", "/etc/autostop-manager"),
    ("j1_guard_config", "/etc/autostop-j1-candidate-guard"),
    ("j1_duty_config", "/etc/autostop-crm-j1-duty"),
    ("j1_guard_state", "/var/lib/autostop-j1-candidate-guard"),
    ("j1_duty_state", "/var/lib/autostop-crm-j1-duty"),
    ("j1_guard_code", "/usr/local/libexec/autostop-j1-candidate-guard"),
    ("j1_duty_code", "/usr/local/libexec/autostop-crm-j1-duty"),
    ("forward_config", "/etc/autostop-crm-forward"),
    ("forward_state", "/var/lib/autostop-crm-forward/state"),
)
UNITS = (
    "autostop-j1",
    "autostop-manager-mcp",
    "autostop-manager-scheduler",
    "autostop-work-telegram",
    "autostop-telegram",
    "autostop-codex-wake",
    "autostop-codex-start",
    "autostop-j1-browser",
)
TIMERS = (
    "autostop-managed-pc-fleet-health",
    "autostop-managed-pc-health",
    "autostop-managed-pc-pending-cleanup",
    "autostop24-db-backup",
    "autostop-app-watchdog",
)
SELECTORS = (
    "/opt/autostop-manager-releases/current",
    "/opt/autostop-work-telegram-releases/current",
    "/opt/autostop-telegram-releases/current",
)
ROOTS += tuple(("unit_" + name, "/etc/systemd/system/" + name + ".service") for name in UNITS)
OPTIONAL_ROOTS = tuple(
    ("drop_" + name, "/etc/systemd/system/" + name + ".service.d") for name in UNITS
)
OPTIONAL_ROOTS += tuple(
    ("timer_" + name + "_" + suffix.replace(".", "_"), "/etc/systemd/system/" + name + "." + suffix)
    for name in TIMERS
    for suffix in ("timer", "service", "timer.d", "service.d")
)
ROOTS += OPTIONAL_ROOTS + tuple(
    ("selector_" + str(index), path) for index, path in enumerate(SELECTORS)
)
J1_ALIAS = Path("/run/autostop-j1-cache-view-private/rw/research.sqlite3")
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 100_000, 2 * 1024**3, 16 * 1024**3
MAX_JSON_BYTES = 64 * 1024**2
HEX = re.compile(r"[0-9a-f]{64}\Z")
SIDECARS = ("-wal", "-shm", "-journal")
FALSE_CLAIMS = dict.fromkeys(
    (
        "all_writers_quiescent_proven",
        "cross_database_atomicity_claimed",
        "production_restore_authorized",
        "activation_acceptance_granted",
        "warm_runtime_continuation_verified",
        "authentication_validated",
        "application_level_completeness_claimed",
    ),
    False,
)


class BackupRefusal(ValueError):
    """Stable, non-sensitive failure code."""


def require(condition, code):
    if not condition:
        raise BackupRefusal(code)


def canonical(value):
    return (
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode()


def document(raw):
    def pairs(entries):
        result = {}
        for key, value in entries:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise BackupRefusal("nonfinite_json")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BackupRefusal("invalid_json") from exc


def budget(deadline):
    require(time.monotonic() < deadline, "operation_deadline")


def physical(path, directory=False):
    require(path.is_absolute() and ".." not in path.parts, "absolute_physical_path_required")
    for part in [*reversed(path.parents), path]:
        info = part.lstat()
        require(
            not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
            "filesystem_link_refused",
        )
        require(
            stat.S_ISDIR(info.st_mode)
            if part != path or directory
            else stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
            "physical_directory_or_single_link_file_required",
        )
    return info


def protected_parent(path):
    physical(path, directory=True)
    if os.name == "posix":
        require(os.geteuid() == 0, "root_required")
        for parent in [path, *path.parents]:
            info = parent.stat()
            require(info.st_uid == 0 and not info.st_mode & 0o022, "root_protected_parent_required")


def native_root():
    return os.name == "posix" and os.geteuid() == 0


def version(info):
    return [
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_uid,
        info.st_gid,
        stat.S_IMODE(info.st_mode),
    ]


def digest(path, deadline):
    physical(path)
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024**2):
            budget(deadline)
            value.update(block)
    return value.hexdigest()


def sync_directory(path):
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def new_file(path):
    return os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600),
        "wb",
    )


def publish(path, value):
    raw = canonical(value)
    require(len(raw) <= MAX_JSON_BYTES, "manifest_size_limit")
    temporary = path.with_suffix(".pending")
    with new_file(temporary) as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    # Destination is private and newly created; hard-link publication is atomic
    # and fails if the final name already exists, on both Linux and NTFS.
    os.link(temporary, path)
    temporary.unlink()
    sync_directory(path.parent)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def profile_document():
    return {
        "name": PROFILE,
        "roots": ROOTS,
        "policy": "recursive_sqlite_backup_regular_durable_v1",
        "metadata_only": ["links", "sockets", "pid", "locks", "sqlite_sidecars"],
        "j1_alias": J1_ALIAS.as_posix(),
        "optional_roots": OPTIONAL_ROOTS,
        "systemd_restore_requires_operator_reconcile": True,
        "runtime_dependencies": "reacquire_exact_selector_revision_and_manifest_hashes",
        "whole_host_bootable_claimed": False,
    }


def profile_sha256():
    return hashlib.sha256(canonical(profile_document())).hexdigest()


def expected_root_kind(component, source):
    if source in SELECTORS:
        return "link"
    if component in ("manager", "scheduler", "j1"):
        return "sqlite"
    if (
        component.endswith("_env")
        or component.startswith("unit_")
        or (component, source) in OPTIONAL_ROOTS
        and not source.endswith(".d")
    ):
        return "file"
    return "directory"


def json_kind(component, relative):
    # Workspace/package fixtures and JSONC remain opaque exact bytes.
    managed = component in (
        "crm",
        "telegram",
        "work_telegram",
        "telegram_config",
        "work_telegram_config",
        "j1_guard_state",
        "j1_duty_state",
        "forward_state",
    )
    managed |= component == "codex" and (
        "/" not in relative
        or relative.startswith(("sessions/", "archived_sessions/", "app-server-daemon/"))
    )
    suffix = PurePosixPath(relative).suffix
    return suffix[1:] if managed and suffix in (".json", ".jsonl") else "file"


def check_context(value):
    require(
        type(value) is dict
        and set(value)
        == {
            "attempt_id",
            "predecessor_sha256",
            "candidate_crm_sha",
            "candidate_manager_sha",
            "scope_profile_sha256",
        },
        "context_fields_invalid",
    )
    try:
        require(str(uuid.UUID(value["attempt_id"])) == value["attempt_id"], "attempt_id_invalid")
    except (ValueError, TypeError, AttributeError) as exc:
        raise BackupRefusal("attempt_id_invalid") from exc
    require(value["scope_profile_sha256"] == profile_sha256(), "fixed_profile_hash_required")
    require(
        isinstance(value["predecessor_sha256"], str) and HEX.fullmatch(value["predecessor_sha256"]),
        "context_hash_invalid",
    )
    for key in ("candidate_crm_sha", "candidate_manager_sha"):
        require(
            isinstance(value[key], str) and re.fullmatch(r"[0-9a-f]{40}", value[key]),
            "candidate_revision_invalid",
        )


@dataclass(frozen=True)
class Item:
    component: str
    logical_name: str
    source: Path
    kind: str
    source_version: tuple
    detail: str = ""


def _scan(component, root, deadline):
    physical(root.parent, directory=True)
    entries = []

    def visit(path):
        budget(deadline)
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        reparse = getattr(info, "st_file_attributes", 0) & 0x400
        if stat.S_ISLNK(info.st_mode) or reparse:
            kind, detail = "link", os.readlink(path)
        elif stat.S_ISDIR(info.st_mode):
            kind, detail = "directory", ""
        elif stat.S_ISSOCK(info.st_mode):
            kind, detail = "runtime", "socket"
        elif stat.S_ISREG(info.st_mode):
            require(info.st_nlink == 1, "source_hardlink_refused")
            kind, detail = "file", ""
            runtime_lock = component in ("crm", "telegram", "work_telegram") and relative in (
                "manager_structure.lock",
                "mcp-oauth-state.lock",
                "settings.lock",
                "shared_files_index.lock",
                "state.json.lock",
                "state.lock",
                "users.lock",
                "inbound.sqlite3.lock",
            )
            runtime_lock |= component == "forward_state" and relative == ".store.lock"
            runtime_lock |= component == "codex" and (
                path.name == ".sqlite-maintenance.lock"
                or any(p in ("thread-writer-locks", "mcp-oauth-locks") for p in path.parts)
                or (path.name.endswith(".pid") and relative.startswith("app-server"))
            )
            if runtime_lock:
                kind, detail = "runtime", "lock_or_pid"
            else:
                with path.open("rb") as stream:
                    if stream.read(16) == b"SQLite format 3\x00":
                        kind = "sqlite"
                if kind == "file":
                    kind = json_kind(component, relative)
        else:
            raise BackupRefusal("unsupported_source_special_file")
        entries.append(Item(component, relative, path, kind, tuple(version(info)), detail))
        require(len(entries) <= MAX_FILES, "file_count_limit")
        if kind == "directory":
            # scandir errors propagate; no os.walk default error suppression.
            with os.scandir(path) as children:
                names = sorted(child.name for child in children)
            for name in names:
                visit(path / name)

    visit(root)
    if entries[0].kind == "sqlite":
        for suffix in SIDECARS:
            sidecar = Path(str(root) + suffix)
            if os.path.lexists(sidecar):
                info = physical(sidecar)
                entries.append(
                    Item(
                        component,
                        "." + suffix,
                        sidecar,
                        "runtime",
                        tuple(version(info)),
                        "sqlite_sidecar",
                    )
                )
    databases = {str(item.source) for item in entries if item.kind == "sqlite"}
    result = []
    for item in entries:
        suffix = next((s for s in SIDECARS if str(item.source).endswith(s)), None)
        if suffix and item.kind in ("file", "json", "jsonl"):
            require(str(item.source)[: -len(suffix)] in databases, "orphan_sqlite_sidecar")
            item = Item(
                item.component,
                item.logical_name,
                item.source,
                "runtime",
                item.source_version,
                "sqlite_sidecar",
            )
        result.append(item)
    return result


def build_profile(*, deadline=None):
    """Fresh, fixed approved roots; links are recorded but never followed."""
    deadline = deadline or time.monotonic() + 600
    items = []
    for component, source in ROOTS:
        if (component, source) in OPTIONAL_ROOTS and not os.path.lexists(source):
            physical(Path(source).parent, directory=True)
            items.append(Item(component, ".", Path(source), "absent", (), "optional_root_absent"))
            continue
        current = _scan(component, Path(source), deadline)
        if source in SELECTORS:
            require(current[0].kind == "link", "selector_link_required")
            target = Path(source).resolve(strict=True)
            physical(target, directory=True)
            details = {
                "target": str(target),
                "metadata_sha256": {
                    name: digest(target / name, deadline)
                    for name in ("REVISION", "MANIFEST.sha256", "MANIFEST.files0")
                },
                "dependency_copied": False,
            }
            first = current[0]
            current[0] = Item(
                first.component,
                first.logical_name,
                first.source,
                first.kind,
                first.source_version,
                canonical(details).decode().strip(),
            )
        else:
            require(
                current[0].kind == expected_root_kind(component, source),
                "required_root_type_invalid",
            )
        items.extend(current)
    require(len(items) <= MAX_FILES, "file_count_limit")
    return tuple(items)


def inventory():
    items = build_profile()
    counts = {}
    for item in items:
        count = counts.setdefault(
            item.component,
            {"files": 0, "bytes": 0, "sqlite": 0, "metadata": 0, "sqlite_sidecar_bytes": 0},
        )
        if item.kind in ("file", "json", "jsonl", "sqlite"):
            count["files"] += 1
            count["bytes"] += item.source_version[2]
            count["sqlite"] += item.kind == "sqlite"
        else:
            count["metadata"] += 1
            count["sqlite_sidecar_bytes"] += (
                item.source_version[2] if item.detail == "sqlite_sidecar" else 0
            )
    return {
        "profile": PROFILE,
        "scope_profile_sha256": profile_sha256(),
        "components": counts,
        "sqlite_wal_bytes_not_in_main_estimate": True,
        **FALSE_CLAIMS,
    }


def copy_stable_file(source, target, deadline, remaining=MAX_FILE_BYTES):
    before = physical(source)
    require(before.st_size <= min(MAX_FILE_BYTES, remaining), "file_size_limit")
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    value, count = hashlib.sha256(), 0
    try:
        opened = os.fstat(descriptor)
        require(version(opened)[:4] == version(before)[:4], "source_changed_open")
        with os.fdopen(descriptor, "rb", closefd=False) as src, new_file(target) as dst:
            while block := src.read(1024**2):
                budget(deadline)
                count += len(block)
                require(count <= min(MAX_FILE_BYTES, remaining), "file_grew_limit")
                value.update(block)
                dst.write(block)
            dst.flush()
            os.fsync(dst.fileno())
        require(
            version(os.fstat(descriptor)) == version(opened)
            and version(source.lstat()) == version(before)
            and count == before.st_size,
            "source_changed_during_copy",
        )
    finally:
        os.close(descriptor)
    require(digest(target, deadline) == value.hexdigest(), "destination_hash_mismatch")
    return {"bytes": count, "sha256": value.hexdigest()}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def inspect_sqlite(path, deadline):
    require(sqlite3.sqlite_version_info >= (3, 51, 3), "sqlite_runtime_too_old")
    physical(path)
    require(not any(os.path.lexists(str(path) + s) for s in SIDECARS), "snapshot_sidecars_refused")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as con:
        con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA temp_store=MEMORY")
        require(
            con.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "sqlite_integrity_failed"
        )
        require(not con.execute("PRAGMA foreign_key_check").fetchall(), "sqlite_fk_failed")
        schema = con.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        counts, hashes, fts = {}, {}, []
        for kind, name, _, sql in schema:
            if kind != "table":
                continue
            if sql and re.search(r"\bUSING\s+fts5\b", sql, re.I):
                fts.append(name)
            columns = con.execute("PRAGMA table_info(" + quote(name) + ")").fetchall()
            require(columns, "sqlite_table_without_columns")
            order = ",".join(quote(col[1]) + " COLLATE BINARY" for col in columns)
            value, count = hashlib.sha256(canonical([col[1] for col in columns])), 0
            for row in con.execute("SELECT * FROM " + quote(name) + " ORDER BY " + order):
                budget(deadline)
                value.update(
                    canonical(
                        [
                            ["bytes", base64.b64encode(v).decode()]
                            if isinstance(v, bytes)
                            else [type(v).__name__, v]
                            for v in row
                        ]
                    )
                )
                count += 1
            counts[name], hashes[name] = count, value.hexdigest()
        return {
            "integrity": "ok",
            "foreign_keys": "ok",
            "schema_sha256": hashlib.sha256(canonical(schema)).hexdigest(),
            "table_rows": counts,
            "logical_sha256": hashes,
            "fts5_tables": fts,
        }


def inspect_json(path, kind, deadline):
    count = 0
    with path.open("rb") as stream:
        while True:
            budget(deadline)
            raw = (
                stream.readline(MAX_JSON_BYTES + 1)
                if kind == "jsonl"
                else stream.read(MAX_JSON_BYTES + 1)
            )
            if not raw:
                break
            require(len(raw) <= MAX_JSON_BYTES, "json_size_limit")
            document(raw)
            count += 1
            if kind == "json":
                break
    require(count or kind == "jsonl", "empty_json")
    return {"json": "valid", "records": count}


def _snapshot_staged(source, target, deadline, remaining):
    require(sqlite3.sqlite_version_info >= (3, 51, 3), "sqlite_runtime_too_old")
    with new_file(target):
        pass
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=0)) as src:
        src.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
        src.execute("PRAGMA query_only=ON")
        initial = src.execute("PRAGMA data_version").fetchone()[0]
        page_size = src.execute("PRAGMA page_size").fetchone()[0]
        require(
            src.execute("PRAGMA page_count").fetchone()[0] * page_size
            <= min(MAX_FILE_BYTES, remaining),
            "sqlite_size_limit",
        )
        with closing(sqlite3.connect(target)) as dst:

            def progress(_status, _remaining, total):
                budget(deadline)
                require(total * page_size <= min(MAX_FILE_BYTES, remaining), "sqlite_size_limit")

            src.backup(dst, pages=64, progress=progress, sleep=0.01)
            require(
                src.execute("PRAGMA data_version").fetchone()[0] == initial,
                "sqlite_source_wrote_during_capture",
            )
            require(
                dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete",
                "standalone_snapshot_required",
            )
    with target.open("rb+") as stream:
        os.fsync(stream.fileno())
    require(target.stat().st_size <= min(MAX_FILE_BYTES, remaining), "sqlite_size_limit")
    return {
        "bytes": target.stat().st_size,
        "sha256": digest(target, deadline),
        "inspection": inspect_sqlite(target, deadline),
    }


def snapshot_sqlite(source, target, deadline, remaining):
    # Never open production DBs with SQLite: even mode=ro may create WAL/SHM.
    # A stable byte copy of main + existing WAL, under the caller's continuous
    # fence, is opened privately; the backup API recovers committed WAL pages.
    scratch = target.with_suffix(".staging")
    scratch.mkdir(mode=0o700)
    staged = scratch / "source.sqlite"
    originals = [(source, tuple(version(physical(source))))]
    copy_stable_file(source, staged, deadline)
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(source) + suffix)
        if os.path.lexists(sidecar):
            originals.append((sidecar, tuple(version(physical(sidecar)))))
            copy_stable_file(sidecar, Path(str(staged) + suffix), deadline)
    result = _snapshot_staged(staged, target, deadline, remaining)
    require(
        all(tuple(version(physical(path))) == old for path, old in originals),
        "sqlite_source_changed",
    )
    for child in scratch.iterdir():
        physical(child)
        child.unlink()
    scratch.rmdir()
    return result


def capture(output: Path, context: dict, observe_closure: Callable, *, timeout=1800):
    """Capture only under a live native observer supplied by the orchestrator."""
    check_context(context)
    require(native_root(), "native_root_capture_required")
    protected_parent(output.parent)
    require(
        not os.path.lexists(output) and 0 < timeout <= 3600, "new_destination_or_timeout_required"
    )
    deadline, witnesses = time.monotonic() + timeout, []

    def fence():
        budget(deadline)
        proof = observe_closure()
        require(
            type(proof) is dict
            and set(proof) == {"attempt_id", "path", "sha256"}
            and proof["attempt_id"] == context["attempt_id"]
            and isinstance(proof["path"], str)
            and Path(proof["path"]).is_absolute()
            and isinstance(proof["sha256"], str)
            and HEX.fullmatch(proof["sha256"]),
            "external_closure_reference_invalid",
        )
        witnesses.append(dict(proof))

    fence()
    items = build_profile(deadline=deadline)
    require(
        not any(output.is_relative_to(Path(source)) for _, source in ROOTS),
        "backup_inside_source_refused",
    )
    require(
        sum(i.source_version[2] for i in items if i.kind in ("file", "json", "jsonl", "sqlite"))
        <= MAX_TOTAL_BYTES,
        "total_size_limit",
    )
    output.mkdir(mode=0o700)
    sync_directory(output.parent)
    records, total, index = [], 0, 0
    for item in items:
        fence()
        require(
            not os.path.lexists(item.source)
            if item.kind == "absent"
            else tuple(version(item.source.lstat())) == item.source_version,
            "inventory_changed",
        )
        record = {
            "component": item.component,
            "logical_name": item.logical_name,
            "source": item.source.as_posix(),
            "kind": item.kind,
            "source_version": list(item.source_version),
            "detail": item.detail,
        }
        if item.kind in ("file", "json", "jsonl", "sqlite"):
            target = output / f"{index:06d}.bin"
            source = item.source
            metadata = (snapshot_sqlite if item.kind == "sqlite" else copy_stable_file)(
                source, target, deadline, MAX_TOTAL_BYTES - total
            )
            if item.kind in ("json", "jsonl"):
                metadata["inspection"] = inspect_json(target, item.kind, deadline)
            record.update(payload=target.name, **metadata)
            total += metadata["bytes"]
            index += 1
        records.append(record)
        fence()
    require(build_profile(deadline=deadline) == items, "final_inventory_changed")
    sync_directory(output)
    fence()
    manifest = {
        "schema": SCHEMA,
        "complete": True,
        "profile": profile_document(),
        "context": context,
        "artifacts": records,
        "external_closure_observations": witnesses,
        **FALSE_CLAIMS,
    }
    ref = publish(output / "manifest.json", manifest)
    sync_directory(output.parent)
    return {
        "manifest": ref,
        "manifest_sha256": ref["sha256"],
        "files": index,
        "bytes": total,
        **FALSE_CLAIMS,
    }


def verify_and_restore(
    backupdir: Path, expectedmanifestSHA: str, outputnew: Path, context: dict, *, timeout=3600
):
    """Restore opaque copies in a new private directory; never source paths."""
    check_context(context)
    physical(backupdir, directory=True)
    protected_parent(outputnew.parent)
    require(
        0 < timeout <= 3600
        and not os.path.lexists(outputnew)
        and not outputnew.is_relative_to(backupdir)
        and not backupdir.is_relative_to(outputnew),
        "new_disposable_destination_required",
    )
    deadline = time.monotonic() + timeout
    path = backupdir / "manifest.json"
    require(
        isinstance(expectedmanifestSHA, str)
        and HEX.fullmatch(expectedmanifestSHA)
        and physical(path).st_size <= MAX_JSON_BYTES
        and digest(path, deadline) == expectedmanifestSHA,
        "manifest_hash_mismatch",
    )
    manifest = document(path.read_bytes())
    require(
        type(manifest) is dict
        and manifest.get("schema") == SCHEMA
        and manifest.get("complete") is True
        and canonical(manifest.get("profile")) == canonical(profile_document())
        and manifest.get("context") == context,
        "manifest_schema_or_context_invalid",
    )
    require(all(manifest.get(key) is False for key in FALSE_CLAIMS), "unsupported_manifest_claim")
    artifacts = manifest.get("artifacts")
    require(type(artifacts) is list and 0 < len(artifacts) <= MAX_FILES, "artifact_count_invalid")
    roots, keys, files, total = dict(ROOTS), set(), [], 0
    for item in artifacts:
        require(
            type(item) is dict
            and item.get("component") in roots
            and type(item.get("logical_name")) is str,
            "artifact_shape_invalid",
        )
        relative = PurePosixPath(item["logical_name"])
        require(
            not relative.is_absolute()
            and ".." not in relative.parts
            and "\\" not in item["logical_name"],
            "logical_path_invalid",
        )
        key = (item["component"], item["logical_name"])
        expected_source = (
            roots[key[0]] + item["logical_name"][1:]
            if item["logical_name"] in tuple("." + s for s in SIDECARS)
            else str(PurePosixPath(roots[key[0]]) / relative)
        )
        require(
            key not in keys and item.get("source") == expected_source,
            "duplicate_or_unexpected_source",
        )
        keys.add(key)
        kind = item.get("kind")
        require(
            kind in ("file", "json", "jsonl", "sqlite", "directory", "link", "runtime", "absent"),
            "artifact_kind_invalid",
        )
        if item["logical_name"] == ".":
            require(
                kind == expected_root_kind(key[0], roots[key[0]])
                or kind == "absent"
                and (key[0], roots[key[0]]) in OPTIONAL_ROOTS,
                "required_root_type_invalid",
            )
        require(
            kind != "absent"
            or item["logical_name"] == "."
            and (key[0], roots[key[0]]) in OPTIONAL_ROOTS,
            "unexpected_absent_item",
        )
        if kind in ("directory", "link", "runtime", "absent"):
            require("payload" not in item, "runtime_payload_refused")
            continue
        require(item.get("payload") == f"{len(files):06d}.bin", "payload_path_invalid")
        source = backupdir / item["payload"]
        require(
            type(item.get("bytes")) is int
            and 0 <= item["bytes"] <= MAX_FILE_BYTES
            and physical(source).st_size == item["bytes"]
            and isinstance(item.get("sha256"), str)
            and HEX.fullmatch(item["sha256"])
            and digest(source, deadline) == item["sha256"],
            "artifact_bytes_mismatch",
        )
        with source.open("rb") as stream:
            is_sqlite = stream.read(16) == b"SQLite format 3\x00"
        require(
            kind == ("sqlite" if is_sqlite else json_kind(key[0], key[1])), "artifact_kind_mismatch"
        )
        total += item["bytes"]
        require(total <= MAX_TOTAL_BYTES, "total_size_limit")
        files.append(item)
    require(all((name, ".") in keys for name in roots), "profile_root_missing")
    require(
        {p.name for p in backupdir.iterdir()} == {"manifest.json", *(i["payload"] for i in files)},
        "exact_payload_inventory_required",
    )
    outputnew.mkdir(mode=0o700)
    sync_directory(outputnew.parent)
    fts_checked = 0
    for item in files:
        source, target = backupdir / item["payload"], outputnew / item["payload"]
        require(
            copy_stable_file(source, target, deadline)["sha256"] == item["sha256"],
            "restored_hash_mismatch",
        )
        if item["kind"] in ("sqlite", "json", "jsonl"):

            def inspect(p):
                return (
                    inspect_sqlite(p, deadline)
                    if item["kind"] == "sqlite"
                    else inspect_json(p, item["kind"], deadline)
                )

            before, restored = inspect(source), inspect(target)
            require(before == restored == item.get("inspection"), "restored_inspection_mismatch")
            if item["kind"] == "sqlite" and before["fts5_tables"]:
                with closing(sqlite3.connect(target)) as con:
                    con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
                    for table in before["fts5_tables"]:
                        con.execute(
                            "INSERT INTO "
                            + quote(table)
                            + "("
                            + quote(table)
                            + ",rank) VALUES('integrity-check',1)"
                        )
                        fts_checked += 1
                    con.rollback()
                require(
                    inspect_sqlite(target, deadline) == before, "fts_restore_changed_logical_state"
                )
    require(
        digest(path, deadline) == expectedmanifestSHA
        and all(digest(backupdir / i["payload"], deadline) == i["sha256"] for i in files),
        "original_backup_changed",
    )
    require(
        all(digest(outputnew / i["payload"], deadline) == i["sha256"] for i in files),
        "restored_bytes_changed",
    )
    sync_directory(outputnew)
    receipt = {
        "schema": "autostop.j1.forward-disposable-restore.v1",
        "context": context,
        "manifest_sha256": expectedmanifestSHA,
        "files": len(files),
        "bytes": total,
        "fts5_tables_checked": fts_checked,
        "sqlite_runtime": sqlite3.sqlite_version,
        "provided_artifacts_restored_exactly": True,
        "source_evidence_unchanged": True,
        "native_source_ownership_restored": False,
        "production_restore_performed": False,
        **FALSE_CLAIMS,
    }
    return {**receipt, "receipt": publish(outputnew / "verification.json", receipt)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inventory", "verify"))
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--context", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "inventory":
            result = inventory()
        else:
            require(
                args.backup_dir and args.manifest_sha256 and args.output and args.context,
                "verify_arguments_required",
            )
            require(physical(args.context).st_size <= MAX_JSON_BYTES, "context_size_limit")
            result = verify_and_restore(
                args.backup_dir,
                args.manifest_sha256,
                args.output,
                document(args.context.read_bytes()),
            )
        # Only bounded counts, hashes and false claims; never manifest/source data.
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        print('{"ok":false,"error":"backup_operation_refused"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
