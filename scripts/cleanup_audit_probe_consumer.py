from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

AUDIT_PROBE_CONSUMER = "audit-probe"


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _verify_backup_for_database(backup_dir: Path, database: Path) -> None:
    from scripts.agent_release_backup import _load_manifest, verify_backup

    if not backup_dir.is_absolute() or backup_dir.is_symlink() or not backup_dir.is_dir():
        raise RuntimeError("verified_backup_required")
    verified = verify_backup(backup_dir)
    if "change_feed_sqlite" not in verified.get("verified_artifacts", []):
        raise RuntimeError("verified_change_feed_backup_required")
    manifest = _load_manifest(backup_dir)
    source_dir = Path(str(manifest["sources"]["crm_data_dir"])).resolve(strict=True)
    if database.resolve(strict=True).parent != source_dir:
        raise RuntimeError("backup_source_mismatch")


def cleanup_audit_probe(
    database: Path,
    *,
    apply: bool,
    backup_dir: Path | None = None,
    backup_verifier: Callable[[Path, Path], None] = _verify_backup_for_database,
) -> dict[str, Any]:
    resolved = database.resolve(strict=True)
    if database.is_symlink() or not database.is_file() or resolved != database.absolute():
        raise RuntimeError("change_feed_database_path_invalid")
    if apply:
        if backup_dir is None:
            raise RuntimeError("verified_backup_required")
        backup_verifier(backup_dir, resolved)
    connection = sqlite3.connect(resolved, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("BEGIN IMMEDIATE")
        metadata = connection.execute(
            "SELECT value FROM metadata WHERE key = 'high_water'"
        ).fetchone()
        before_high_water = int(metadata["value"]) if metadata else -1
        before_events = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        consumer = connection.execute(
            "SELECT acked_sequence FROM consumers WHERE consumer_id = ?",
            (AUDIT_PROBE_CONSUMER,),
        ).fetchone()
        deliveries = int(
            connection.execute(
                "SELECT COUNT(*) FROM deliveries WHERE consumer_id = ?",
                (AUDIT_PROBE_CONSUMER,),
            ).fetchone()[0]
        )
        has_digest_snapshots = _table_exists(connection, "digest_snapshots")
        snapshots = (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM digest_snapshots WHERE consumer_id = ?",
                    (AUDIT_PROBE_CONSUMER,),
                ).fetchone()[0]
            )
            if has_digest_snapshots
            else 0
        )
        if snapshots:
            raise RuntimeError("audit_probe_digest_snapshots_present")
        other_consumers = [
            tuple(row)
            for row in connection.execute(
                "SELECT consumer_id, acked_sequence FROM consumers "
                "WHERE consumer_id <> ? ORDER BY consumer_id",
                (AUDIT_PROBE_CONSUMER,),
            ).fetchall()
        ]
        other_deliveries = [
            tuple(row)
            for row in connection.execute(
                "SELECT consumer_id, window_high_water FROM deliveries "
                "WHERE consumer_id <> ? ORDER BY consumer_id",
                (AUDIT_PROBE_CONSUMER,),
            ).fetchall()
        ]
        other_snapshots = (
            [
                tuple(row)
                for row in connection.execute(
                    "SELECT digest_id, consumer_id, content_hash FROM digest_snapshots "
                    "WHERE consumer_id <> ? ORDER BY digest_id",
                    (AUDIT_PROBE_CONSUMER,),
                ).fetchall()
            ]
            if has_digest_snapshots
            else []
        )
        if consumer is None:
            result = {
                "format": "crm_change_feed_audit_probe_cleanup_v1",
                "consumer_id": AUDIT_PROBE_CONSUMER,
                "present": False,
                "applied": False,
                "acked_sequence": None,
                "deliveries": 0,
                "digest_snapshots": 0,
                "event_count": before_events,
                "high_water": before_high_water,
                "verified": True,
            }
            connection.rollback()
            return result
        acked_sequence = int(consumer["acked_sequence"])
        if acked_sequence != 0:
            raise RuntimeError("audit_probe_checkpoint_not_zero")
        if apply:
            connection.execute(
                "DELETE FROM deliveries WHERE consumer_id = ?", (AUDIT_PROBE_CONSUMER,)
            )
            changed = connection.execute(
                "DELETE FROM consumers WHERE consumer_id = ? AND acked_sequence = 0",
                (AUDIT_PROBE_CONSUMER,),
            ).rowcount
            if changed != 1:
                raise RuntimeError("audit_probe_delete_conflict")
            after_events = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            after_high_water_row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'high_water'"
            ).fetchone()
            after_high_water = int(after_high_water_row["value"]) if after_high_water_row else -1
            if (after_events, after_high_water) != (before_events, before_high_water):
                raise RuntimeError("audit_probe_cleanup_changed_business_feed")
            connection.commit()
        else:
            connection.rollback()
        result = {
            "format": "crm_change_feed_audit_probe_cleanup_v1",
            "consumer_id": AUDIT_PROBE_CONSUMER,
            "present": True,
            "applied": apply,
            "acked_sequence": acked_sequence,
            "deliveries": deliveries,
            "digest_snapshots": snapshots,
            "event_count": before_events,
            "high_water": before_high_water,
            "verified": not apply,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    if apply:
        with sqlite3.connect(resolved, timeout=10) as readback:
            remaining_consumer = readback.execute(
                "SELECT COUNT(*) FROM consumers WHERE consumer_id = ?",
                (AUDIT_PROBE_CONSUMER,),
            ).fetchone()[0]
            remaining_delivery = readback.execute(
                "SELECT COUNT(*) FROM deliveries WHERE consumer_id = ?",
                (AUDIT_PROBE_CONSUMER,),
            ).fetchone()[0]
            after_events = int(readback.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            after_high_water_row = readback.execute(
                "SELECT value FROM metadata WHERE key = 'high_water'"
            ).fetchone()
            after_high_water = int(after_high_water_row[0]) if after_high_water_row else -1
            readback_consumers = readback.execute(
                "SELECT consumer_id, acked_sequence FROM consumers ORDER BY consumer_id"
            ).fetchall()
            readback_deliveries = readback.execute(
                "SELECT consumer_id, window_high_water FROM deliveries ORDER BY consumer_id"
            ).fetchall()
            readback_snapshots = (
                readback.execute(
                    "SELECT digest_id, consumer_id, content_hash "
                    "FROM digest_snapshots ORDER BY digest_id"
                ).fetchall()
                if _table_exists(readback, "digest_snapshots")
                else []
            )
        if (
            remaining_consumer
            or remaining_delivery
            or (after_events, after_high_water) != (before_events, before_high_water)
            or [tuple(row) for row in readback_consumers] != other_consumers
            or [tuple(row) for row in readback_deliveries] != other_deliveries
            or [tuple(row) for row in readback_snapshots] != other_snapshots
        ):
            raise RuntimeError("audit_probe_cleanup_readback_failed")
        result["verified"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely remove only the obsolete audit-probe feed checkpoint."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = cleanup_audit_probe(
            args.database,
            apply=args.apply,
            backup_dir=args.backup_dir,
        )
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "data": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
