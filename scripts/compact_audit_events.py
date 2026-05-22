# ruff: noqa: E402,I001
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_state_file
from minimal_kanban.storage.audit_archive import (
    AUDIT_ARCHIVE_DIR_NAME,
    AuditArchiveStore,
    compact_audit_event_details,
    details_need_archive,
)
from minimal_kanban.storage.file_lock import ProcessFileLock


@dataclass(frozen=True)
class CompactResult:
    state_file: str
    archive_dir: str
    dry_run: bool
    events_total: int
    events_compacted: int
    heavy_details_before_bytes: int
    compact_details_after_bytes: int
    estimated_state_bytes_before: int
    estimated_state_bytes_after: int
    archive_bytes_written: int
    backup_file: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_file": self.state_file,
            "archive_dir": self.archive_dir,
            "dry_run": self.dry_run,
            "events_total": self.events_total,
            "events_compacted": self.events_compacted,
            "heavy_details_before_bytes": self.heavy_details_before_bytes,
            "compact_details_after_bytes": self.compact_details_after_bytes,
            "estimated_state_bytes_before": self.estimated_state_bytes_before,
            "estimated_state_bytes_after": self.estimated_state_bytes_after,
            "archive_bytes_written": self.archive_bytes_written,
            "backup_file": self.backup_file,
        }


def json_bytes(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    )


def archive_ref_for_event(event: dict[str, Any]) -> str:
    event_id = str(event.get("id") or "").strip()
    timestamp = str(event.get("timestamp") or "").strip()
    month = timestamp[:7] if len(timestamp) >= 7 and timestamp[4:5] == "-" else "unknown"
    return f"{month}.jsonl#{event_id}"


def compact_state_payload(
    state: dict[str, Any],
    *,
    archive_store: AuditArchiveStore,
    apply: bool,
) -> tuple[dict[str, Any], dict[str, int]]:
    events = state.get("events")
    if not isinstance(events, list):
        return dict(state), {
            "events_total": 0,
            "events_compacted": 0,
            "heavy_details_before_bytes": 0,
            "compact_details_after_bytes": 0,
            "archive_bytes_written": 0,
        }

    next_events: list[Any] = []
    compacted = 0
    before_bytes = 0
    after_bytes = 0
    archive_bytes = 0
    for event in events:
        if not isinstance(event, dict):
            next_events.append(event)
            continue
        action = str(event.get("action") or "")
        details = event.get("details")
        if not details_need_archive(action, details if isinstance(details, dict) else None):
            next_events.append(event)
            continue
        assert isinstance(details, dict)
        event_id = str(event.get("id") or "").strip()
        card_id = str(event.get("card_id") or "").strip() or None
        timestamp = str(event.get("timestamp") or "")
        if apply:
            archived = archive_store.archive_details(
                event_id=event_id,
                action=action,
                card_id=card_id,
                timestamp=timestamp,
                details=details,
            )
            archive_ref = archived.ref
            archive_bytes += archived.bytes_written
        else:
            archive_ref = archive_ref_for_event(event)
            archive_bytes += (
                json_bytes(
                    {
                        "schema_version": 1,
                        "event_id": event_id,
                        "action": action,
                        "card_id": card_id,
                        "timestamp": timestamp,
                        "details": details,
                    }
                )
                + 1
            )
        compact_details = compact_audit_event_details(
            action=action,
            details=details,
            archive_ref=archive_ref,
        )
        next_event = dict(event)
        next_event["details"] = compact_details
        next_events.append(next_event)
        compacted += 1
        before_bytes += json_bytes(details)
        after_bytes += json_bytes(compact_details)

    next_state = dict(state)
    next_state["events"] = next_events
    return next_state, {
        "events_total": len(events),
        "events_compacted": compacted,
        "heavy_details_before_bytes": before_bytes,
        "compact_details_after_bytes": after_bytes,
        "archive_bytes_written": archive_bytes,
    }


def backup_state_file(state_file: Path) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_file = state_file.with_name(f"{state_file.name}.backup-{timestamp}.json")
    shutil.copy2(state_file, backup_file)
    return backup_file


def write_state_file(state_file: Path, state: dict[str, Any]) -> None:
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    temp_file = state_file.with_suffix(".compact.tmp")
    temp_file.write_text(payload, encoding="utf-8")
    temp_file.replace(state_file)


def compact_state_file(
    state_file: Path,
    *,
    archive_dir: Path | None = None,
    apply: bool = False,
    backup: bool = False,
) -> CompactResult:
    state_file = state_file.expanduser().resolve()
    archive_dir = (
        (archive_dir or (state_file.parent / AUDIT_ARCHIVE_DIR_NAME)).expanduser().resolve()
    )
    archive_store = AuditArchiveStore(archive_dir)

    def run() -> CompactResult:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        before_size = state_file.stat().st_size
        backup_file = ""
        if apply and backup:
            backup_file = str(backup_state_file(state_file))
        next_state, stats = compact_state_payload(state, archive_store=archive_store, apply=apply)
        after_size = json_bytes(next_state)
        if apply and stats["events_compacted"]:
            write_state_file(state_file, next_state)
            after_size = state_file.stat().st_size
        return CompactResult(
            state_file=str(state_file),
            archive_dir=str(archive_dir),
            dry_run=not apply,
            events_total=stats["events_total"],
            events_compacted=stats["events_compacted"],
            heavy_details_before_bytes=stats["heavy_details_before_bytes"],
            compact_details_after_bytes=stats["compact_details_after_bytes"],
            estimated_state_bytes_before=before_size,
            estimated_state_bytes_after=after_size,
            archive_bytes_written=stats["archive_bytes_written"],
            backup_file=backup_file,
        )

    if not apply:
        return run()
    lock = ProcessFileLock(state_file.with_suffix(".lock"), timeout_seconds=60.0)
    with lock.acquire():
        return run()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Move heavy AutoStop CRM audit event details into append-only archive."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Only report planned compaction.")
    mode.add_argument("--apply", action="store_true", help="Compact state.json under file lock.")
    parser.add_argument(
        "--backup", action="store_true", help="Required with --apply; copy state first."
    )
    parser.add_argument("--state-file", type=Path, default=get_state_file())
    parser.add_argument("--archive-dir", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    return args


def print_text(result: CompactResult) -> None:
    data = result.to_dict()
    for key, value in data.items():
        print(f"{key}: {value}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = compact_state_file(
        args.state_file,
        archive_dir=args.archive_dir,
        apply=args.apply,
        backup=args.backup,
    )
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
