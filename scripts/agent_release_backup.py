from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.storage.file_lock import ProcessFileLock  # noqa: E402

BACKUP_SCHEMA = "autostop-agent-release-backup.v2"
LEGACY_BACKUP_SCHEMA = "autostop-agent-release-backup.v1"
SUPPORTED_BACKUP_SCHEMAS = frozenset({BACKUP_SCHEMA, LEGACY_BACKUP_SCHEMA})
MANIFEST_NAME = "manifest.json"
STATE_BACKUP_NAME = "state.json"
CHANGE_FEED_BACKUP_NAME = "change_feed.sqlite3"
AUDIT_BACKUP_NAME = "audit-archive.tar.gz"
MANAGER_BACKUP_NAME = "autostop_manager.sqlite3"
COPY_CHUNK_BYTES = 1024 * 1024
MAX_RESTORE_ID = (1 << 32) - 2
ARTIFACT_FILE_NAMES = {
    "state": STATE_BACKUP_NAME,
    "change_feed_sqlite": CHANGE_FEED_BACKUP_NAME,
    "audit_archive": AUDIT_BACKUP_NAME,
    "manager_sqlite": MANAGER_BACKUP_NAME,
}
LEGACY_ARTIFACT_KEYS = frozenset({"state", "audit_archive", "manager_sqlite"})
CURRENT_ARTIFACT_KEYS = frozenset(ARTIFACT_FILE_NAMES)
REQUIRED_ARTIFACT_KEYS = frozenset({"state", "manager_sqlite"})
METADATA_ARTIFACT_KEYS = frozenset({"state", "change_feed_sqlite", "manager_sqlite"})
BASE_ARTIFACT_FIELDS = frozenset({"name", "size_bytes", "sha256"})
MANIFEST_FIELDS = frozenset(
    {"schema", "backup_id", "created_at", "complete", "sources", "artifacts"}
)


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path, *, restore_metadata: dict[str, int] | None = None) -> dict[str, object]:
    artifact: dict[str, object] = {
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if restore_metadata is not None:
        artifact["restore_metadata"] = dict(restore_metadata)
    return artifact


def _file_restore_metadata(path: Path) -> dict[str, int]:
    source_stat = path.stat()
    return {
        "mode": stat.S_IMODE(source_stat.st_mode),
        "uid": int(source_stat.st_uid),
        "gid": int(source_stat.st_gid),
    }


def _validated_restore_metadata(value: object, *, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise BackupError(f"Invalid restore metadata: {label}")
    validated: dict[str, int] = {}
    for key, maximum in (("mode", 0o7777), ("uid", MAX_RESTORE_ID), ("gid", MAX_RESTORE_ID)):
        raw = value.get(key)
        if type(raw) is not int or raw < 0 or raw > maximum:
            raise BackupError(f"Invalid restore metadata {key}: {label}")
        validated[key] = raw
    if set(value) != set(validated):
        raise BackupError(f"Invalid restore metadata fields: {label}")
    return validated


def _restore_metadata_matches(path: Path, value: object) -> bool:
    if value is None:
        return True
    expected = _validated_restore_metadata(value, label=path.name)
    try:
        return _file_restore_metadata(path) == expected
    except OSError:
        return False


def _fsync_file(path: Path) -> None:
    # Python 3.13 on Windows rejects fsync() for a read-only descriptor even
    # though the file itself exists and is readable. A writable descriptor is
    # portable and does not modify the already-written backup payload.
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _fsync_file(temp_path)
    temp_path.replace(path)


def _copy_state(state_file: Path, destination: Path) -> None:
    if not state_file.is_file():
        raise BackupError(f"CRM state file does not exist: {state_file}")
    lock = ProcessFileLock(state_file.with_suffix(".lock"), timeout_seconds=30.0)
    with lock.acquire():
        shutil.copyfile(state_file, destination)
        with destination.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise BackupError("CRM state backup is not a JSON object")
    _fsync_file(destination)


def _copy_audit_archive(audit_dir: Path, destination: Path) -> bool:
    if not audit_dir.is_dir():
        return False
    lock = ProcessFileLock(audit_dir / ".audit-archive.lock", timeout_seconds=30.0)
    with lock.acquire():
        with tarfile.open(destination, "w:gz") as archive:
            for source in sorted(audit_dir.rglob("*")):
                if source.name == ".audit-archive.lock" or not source.is_file():
                    continue
                if source.is_symlink():
                    raise BackupError(f"Audit archive contains an unsupported symlink: {source}")
                archive.add(source, arcname=source.relative_to(audit_dir), recursive=False)
    _fsync_file(destination)
    return True


def _sqlite_integrity_check(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if not result or str(result[0]).lower() != "ok":
        raise BackupError(f"SQLite integrity check failed for {path.name}")


def _copy_sqlite(source: Path, destination: Path) -> bool:
    if not source.is_file():
        return False
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.execute("PRAGMA wal_checkpoint(FULL)")
    finally:
        destination_connection.close()
        source_connection.close()
    _sqlite_integrity_check(destination)
    _fsync_file(destination)
    return True


def create_backup(
    *,
    output_root: Path,
    crm_data_dir: Path,
    manager_db: Path,
    backup_id: str | None = None,
    include_audit_archive: bool = True,
) -> dict[str, Any]:
    created_at = datetime.now(UTC)
    resolved_id = backup_id or created_at.strftime("%Y%m%dT%H%M%SZ")
    if not resolved_id.replace("-", "").replace("_", "").isalnum():
        raise BackupError("backup id may contain only letters, digits, dashes, and underscores")

    output_root.mkdir(parents=True, exist_ok=True)
    final_dir = output_root / resolved_id
    if final_dir.exists():
        raise BackupError(f"Backup already exists: {final_dir}")

    temp_dir = Path(tempfile.mkdtemp(prefix=f".{resolved_id}.partial-", dir=output_root))
    try:
        state_source = crm_data_dir / "state.json"
        if not state_source.is_file():
            raise BackupError(f"CRM state file does not exist: {state_source}")
        state_restore_metadata = _file_restore_metadata(state_source)
        state_destination = temp_dir / STATE_BACKUP_NAME
        _copy_state(state_source, state_destination)
        artifacts: dict[str, Any] = {
            "state": _artifact(
                state_destination,
                restore_metadata=state_restore_metadata,
            )
        }

        change_feed_source = crm_data_dir / "change_feed.sqlite3"
        change_feed_restore_metadata = (
            _file_restore_metadata(change_feed_source) if change_feed_source.is_file() else None
        )
        change_feed_destination = temp_dir / CHANGE_FEED_BACKUP_NAME
        if _copy_sqlite(change_feed_source, change_feed_destination):
            artifacts["change_feed_sqlite"] = _artifact(
                change_feed_destination,
                restore_metadata=change_feed_restore_metadata,
            )
        else:
            # An older release may predate the durable feed. Recording the
            # absence is important: rollback must then remove a database first
            # created by the failed candidate instead of retaining mixed state.
            artifacts["change_feed_sqlite"] = None

        audit_destination = temp_dir / AUDIT_BACKUP_NAME
        if include_audit_archive and _copy_audit_archive(
            crm_data_dir / "audit-archive", audit_destination
        ):
            artifacts["audit_archive"] = _artifact(audit_destination)
        else:
            artifacts["audit_archive"] = None

        if not manager_db.is_file():
            raise BackupError(f"Manager SQLite does not exist: {manager_db}")
        manager_restore_metadata = _file_restore_metadata(manager_db)
        manager_destination = temp_dir / MANAGER_BACKUP_NAME
        if _copy_sqlite(manager_db, manager_destination):
            artifacts["manager_sqlite"] = _artifact(
                manager_destination,
                restore_metadata=manager_restore_metadata,
            )
        else:
            raise BackupError(f"Manager SQLite does not exist: {manager_db}")

        manifest = {
            "schema": BACKUP_SCHEMA,
            "backup_id": resolved_id,
            "created_at": created_at.isoformat(),
            "complete": True,
            "sources": {
                "crm_data_dir": str(crm_data_dir.resolve()),
                "manager_db": str(manager_db.resolve()),
            },
            "artifacts": artifacts,
        }
        _write_json_atomic(temp_dir / MANIFEST_NAME, manifest)
        temp_dir.replace(final_dir)
        return {"ok": True, **manifest, "backup_dir": str(final_dir)}
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _load_manifest(backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise BackupError(f"Invalid backup manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise BackupError("Unsupported backup manifest schema")
    schema = manifest.get("schema")
    if not isinstance(schema, str) or schema not in SUPPORTED_BACKUP_SCHEMAS:
        raise BackupError("Unsupported backup manifest schema")
    if set(manifest) != MANIFEST_FIELDS:
        raise BackupError("Invalid backup manifest fields")
    if not isinstance(manifest.get("backup_id"), str) or not manifest["backup_id"]:
        raise BackupError("Invalid backup manifest id")
    if not isinstance(manifest.get("created_at"), str) or not manifest["created_at"]:
        raise BackupError("Invalid backup manifest timestamp")
    if manifest.get("complete") is not True:
        raise BackupError("Backup is not marked complete")

    sources = manifest.get("sources")
    if (
        not isinstance(sources, dict)
        or set(sources) != {"crm_data_dir", "manager_db"}
        or any(not isinstance(value, str) or not value for value in sources.values())
    ):
        raise BackupError("Invalid backup manifest sources")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise BackupError("Invalid backup manifest artifacts")
    expected_artifact_keys = (
        LEGACY_ARTIFACT_KEYS if schema == LEGACY_BACKUP_SCHEMA else CURRENT_ARTIFACT_KEYS
    )
    if set(artifacts) != expected_artifact_keys:
        raise BackupError("Invalid backup manifest artifact keys")
    for artifact_name in expected_artifact_keys:
        metadata = artifacts[artifact_name]
        if metadata is None:
            if artifact_name in REQUIRED_ARTIFACT_KEYS:
                raise BackupError(f"Required backup artifact is missing: {artifact_name}")
            continue
        if not isinstance(metadata, dict):
            raise BackupError(f"Invalid artifact metadata: {artifact_name}")
        expected_fields = set(BASE_ARTIFACT_FIELDS)
        if schema == BACKUP_SCHEMA and artifact_name in METADATA_ARTIFACT_KEYS:
            expected_fields.add("restore_metadata")
        if set(metadata) != expected_fields:
            raise BackupError(f"Invalid artifact metadata fields: {artifact_name}")
        expected_filename = ARTIFACT_FILE_NAMES[artifact_name]
        if metadata.get("name") != expected_filename:
            raise BackupError(f"Invalid backup artifact filename: {artifact_name}")
        size_bytes = metadata.get("size_bytes")
        if type(size_bytes) is not int or size_bytes < 0:
            raise BackupError(f"Invalid backup artifact size: {artifact_name}")
        sha256 = metadata.get("sha256")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise BackupError(f"Invalid backup artifact hash: {artifact_name}")
        if "restore_metadata" in metadata:
            _validated_restore_metadata(metadata["restore_metadata"], label=artifact_name)

    if schema == LEGACY_BACKUP_SCHEMA:
        # Release backups created before the durable change feed did not have
        # this artifact at all. Normalize that historical absence to the v2
        # representation so rollback removes a feed first created by a failed
        # candidate instead of retaining state from two different revisions.
        manifest = dict(manifest)
        normalized_artifacts = dict(artifacts)
        normalized_artifacts.setdefault("change_feed_sqlite", None)
        manifest["artifacts"] = normalized_artifacts
    return manifest


def verify_backup(backup_dir: Path) -> dict[str, Any]:
    manifest = _load_manifest(backup_dir)
    verified: list[str] = []
    for artifact_name, metadata in manifest.get("artifacts", {}).items():
        if metadata is None:
            continue
        path = backup_dir / ARTIFACT_FILE_NAMES[artifact_name]
        if path.is_symlink() or not path.is_file():
            raise BackupError(f"Backup artifact is missing: {artifact_name}")
        if path.stat().st_size != metadata["size_bytes"]:
            raise BackupError(f"Backup artifact size mismatch: {artifact_name}")
        if _sha256(path) != metadata["sha256"]:
            raise BackupError(f"Backup artifact hash mismatch: {artifact_name}")
        if "restore_metadata" in metadata:
            _validated_restore_metadata(
                metadata["restore_metadata"],
                label=artifact_name,
            )
        verified.append(artifact_name)
    _sqlite_integrity_check(backup_dir / MANAGER_BACKUP_NAME)
    change_feed_metadata = manifest.get("artifacts", {}).get("change_feed_sqlite")
    if change_feed_metadata is not None:
        _sqlite_integrity_check(backup_dir / CHANGE_FEED_BACKUP_NAME)
    with (backup_dir / STATE_BACKUP_NAME).open("r", encoding="utf-8") as handle:
        if not isinstance(json.load(handle), dict):
            raise BackupError("CRM state backup is not a JSON object")
    return {
        "ok": True,
        "backup_id": manifest.get("backup_id"),
        "backup_dir": str(backup_dir),
        "verified_artifacts": verified,
    }


def _restore_file_atomic(
    source: Path,
    destination: Path,
    *,
    restore_metadata: object = None,
    lock_path: Path | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    context = (
        ProcessFileLock(lock_path, timeout_seconds=30.0).acquire()
        if lock_path is not None
        else nullcontext()
    )
    with context:
        if restore_metadata is None:
            if not destination.is_file():
                raise BackupError(
                    f"Restore metadata is missing and destination does not exist: {destination}"
                )
            effective_metadata = _file_restore_metadata(destination)
        else:
            effective_metadata = _validated_restore_metadata(
                restore_metadata,
                label=destination.name,
            )
        descriptor, raw_temp_path = tempfile.mkstemp(
            prefix=f".{destination.name}.restore-",
            dir=destination.parent,
        )
        os.close(descriptor)
        temp_path = Path(raw_temp_path)
        try:
            shutil.copyfile(source, temp_path)
            chown = getattr(os, "chown", None)
            if chown is not None:
                chown(temp_path, effective_metadata["uid"], effective_metadata["gid"])
            elif os.name == "posix":  # pragma: no cover - POSIX always exposes os.chown
                raise BackupError("POSIX restore cannot apply file ownership")
            os.chmod(temp_path, effective_metadata["mode"])
            _fsync_file(temp_path)
            os.replace(temp_path, destination)
        finally:
            temp_path.unlink(missing_ok=True)


def _verified_restore_sources(backup_dir: Path) -> tuple[dict[str, Any], Path, Path]:
    manifest = _load_manifest(backup_dir)
    verify_backup(backup_dir)
    sources = manifest.get("sources", {})
    crm_data_dir = Path(str(sources.get("crm_data_dir", "")))
    manager_db = Path(str(sources.get("manager_db", "")))
    if not crm_data_dir.is_absolute() or not manager_db.is_absolute():
        raise BackupError("Backup source paths must be absolute")
    return manifest, crm_data_dir, manager_db


def _restore_result(manifest: dict[str, Any], restored: list[str]) -> dict[str, Any]:
    return {
        "ok": True,
        "backup_id": manifest.get("backup_id"),
        "restored": restored,
        "unchanged": not restored,
    }


def restore_crm_state_and_feed(backup_dir: Path) -> dict[str, Any]:
    """Restore CRM-owned protected state while the CRM container is stopped."""

    manifest, crm_data_dir, _ = _verified_restore_sources(backup_dir)

    restored: list[str] = []
    state_source = backup_dir / STATE_BACKUP_NAME
    state_destination = crm_data_dir / "state.json"
    state_metadata = manifest["artifacts"]["state"].get("restore_metadata")
    if (
        not state_destination.is_file()
        or _sha256(state_destination) != _sha256(state_source)
        or not _restore_metadata_matches(state_destination, state_metadata)
    ):
        _restore_file_atomic(
            state_source,
            state_destination,
            restore_metadata=state_metadata,
            lock_path=state_destination.with_suffix(".lock"),
        )
        restored.append("state")

    change_feed_metadata = manifest.get("artifacts", {}).get("change_feed_sqlite")
    change_feed_destination = crm_data_dir / "change_feed.sqlite3"
    # Candidate commits may exist only in WAL. Remove sidecars before opening
    # or replacing the main database so SQLite cannot replay candidate pages
    # into the restored backup.
    change_feed_sidecars_removed = False
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{change_feed_destination}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
            change_feed_sidecars_removed = True
    if change_feed_metadata is None:
        if change_feed_destination.exists():
            change_feed_destination.unlink()
            restored.append("change_feed_sqlite")
    else:
        change_feed_source = backup_dir / CHANGE_FEED_BACKUP_NAME
        if (
            not change_feed_destination.is_file()
            or _sha256(change_feed_destination) != _sha256(change_feed_source)
            or not _restore_metadata_matches(
                change_feed_destination,
                change_feed_metadata.get("restore_metadata"),
            )
        ):
            _restore_file_atomic(
                change_feed_source,
                change_feed_destination,
                restore_metadata=change_feed_metadata.get("restore_metadata"),
            )
            restored.append("change_feed_sqlite")
        _sqlite_integrity_check(change_feed_destination)
    # WAL journal mode may recreate empty sidecars during integrity_check.
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{change_feed_destination}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
            change_feed_sidecars_removed = True
    if change_feed_sidecars_removed and "change_feed_sqlite" not in restored:
        restored.append("change_feed_sqlite")

    return _restore_result(manifest, restored)


def restore_manager_database(backup_dir: Path) -> dict[str, Any]:
    """Restore Manager SQLite after the caller proves there are no open handles.

    The main database is replaced even when its hash matches the backup because
    committed candidate writes may exist only in a WAL sidecar. Removing both
    sidecars before and after the atomic replacement prevents stale WAL replay.
    """

    manifest, _, manager_db = _verified_restore_sources(backup_dir)
    manager_source = backup_dir / MANAGER_BACKUP_NAME
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{manager_db}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    manager_metadata = manifest["artifacts"]["manager_sqlite"].get("restore_metadata")
    _restore_file_atomic(
        manager_source,
        manager_db,
        restore_metadata=manager_metadata,
    )
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{manager_db}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    _sqlite_integrity_check(manager_db)
    # Opening a database whose persistent journal mode is WAL may recreate
    # empty sidecars during the integrity check. The caller has already proved
    # there are no open handles, so remove those fresh sidecars as well.
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{manager_db}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    return _restore_result(manifest, ["manager_sqlite"])


def restore_changed_state_and_manager(backup_dir: Path) -> dict[str, Any]:
    """Compatibility wrapper for offline callers that already proved DB ownership."""

    crm_result = restore_crm_state_and_feed(backup_dir)
    manager_result = restore_manager_database(backup_dir)
    restored = list(crm_result["restored"]) + list(manager_result["restored"])
    return _restore_result(_load_manifest(backup_dir), restored)


def compare_current_protected_state(backup_dir: Path) -> dict[str, Any]:
    """Compare current protected data with a guard backup, including live WAL state."""

    manifest, crm_data_dir, manager_db = _verified_restore_sources(backup_dir)
    changed: list[str] = []
    state_source = backup_dir / STATE_BACKUP_NAME
    state_current = crm_data_dir / "state.json"
    state_lock = ProcessFileLock(state_current.with_suffix(".lock"), timeout_seconds=30.0)
    with state_lock.acquire():
        if not state_current.is_file() or _sha256(state_current) != _sha256(state_source):
            changed.append("state")

    with tempfile.TemporaryDirectory(prefix="autostop-release-compare-") as temp_dir:
        temp_root = Path(temp_dir)
        change_feed_metadata = manifest.get("artifacts", {}).get("change_feed_sqlite")
        current_change_feed = crm_data_dir / "change_feed.sqlite3"
        if change_feed_metadata is None:
            if current_change_feed.exists():
                changed.append("change_feed_sqlite")
        else:
            change_feed_snapshot = temp_root / CHANGE_FEED_BACKUP_NAME
            if not _copy_sqlite(current_change_feed, change_feed_snapshot) or _sha256(
                change_feed_snapshot
            ) != _sha256(backup_dir / CHANGE_FEED_BACKUP_NAME):
                changed.append("change_feed_sqlite")

        manager_snapshot = temp_root / MANAGER_BACKUP_NAME
        if not _copy_sqlite(manager_db, manager_snapshot) or _sha256(manager_snapshot) != _sha256(
            backup_dir / MANAGER_BACKUP_NAME
        ):
            changed.append("manager_sqlite")

    return {
        "ok": not changed,
        "backup_id": manifest.get("backup_id"),
        "changed_artifacts": changed,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Atomic Agent Gateway release backup utility.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--output-root", type=Path, required=True)
    create.add_argument("--crm-data-dir", type=Path, required=True)
    create.add_argument("--manager-db", type=Path, required=True)
    create.add_argument("--backup-id", default=None)
    create.add_argument("--skip-audit-archive", action="store_true")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--backup-dir", type=Path, required=True)

    compare = subparsers.add_parser("compare-current")
    compare.add_argument("--backup-dir", type=Path, required=True)

    for command in ("restore-changed", "restore-crm-changed", "restore-manager-changed"):
        restore = subparsers.add_parser(command)
        restore.add_argument("--backup-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_backup(
                output_root=args.output_root,
                crm_data_dir=args.crm_data_dir,
                manager_db=args.manager_db,
                backup_id=args.backup_id,
                include_audit_archive=not args.skip_audit_archive,
            )
        elif args.command == "verify":
            result = verify_backup(args.backup_dir)
        elif args.command == "compare-current":
            result = compare_current_protected_state(args.backup_dir)
        elif args.command == "restore-crm-changed":
            result = restore_crm_state_and_feed(args.backup_dir)
        elif args.command == "restore-manager-changed":
            result = restore_manager_database(args.backup_dir)
        else:
            result = restore_changed_state_and_manager(args.backup_dir)
    except (BackupError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
