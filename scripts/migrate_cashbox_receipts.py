"""Migrate private cashbox cursors or prepare the current data for an older CRM."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.models import normalize_actor_name  # noqa: E402
from minimal_kanban.storage.cashbox_receipts import (  # noqa: E402
    CASHBOX_NOTIFICATION_SEEN_SETTING_KEY as LEGACY_KEY,
)
from minimal_kanban.storage.cashbox_receipts import (  # noqa: E402
    cashbox_receipt_key,
    iter_cashbox_receipt_rows,
    normalize_legacy_cashbox_receipts,
)
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


def _legacy_positions(state: dict) -> dict:
    settings = state.get("settings", {})
    if not isinstance(settings, dict) or not isinstance(settings.get(LEGACY_KEY, {}), dict):
        raise ValueError("Unexpected legacy cashbox receipt format; state was preserved.")
    positions = normalize_legacy_cashbox_receipts(settings)
    for name, raw in settings.get(LEGACY_KEY, {}).items():
        key = normalize_actor_name(name, default="").casefold()
        if not key or not isinstance(raw, dict) or positions.get(key) != raw:
            raise ValueError("A legacy receipt cannot be migrated losslessly; state was preserved.")
    return positions


def _private_positions(state_file: Path) -> dict:
    return {
        row["actor_key"]: {"transaction_id": row["transaction_id"], "created_at": row["created_at"]}
        for row in iter_cashbox_receipt_rows(state_file)
    }


def _advances(source: dict, destination: dict) -> dict:
    return {
        key: position
        for key, position in source.items()
        if key not in destination
        or cashbox_receipt_key(position) > cashbox_receipt_key(destination[key])
    }


def migrate(state_file: Path, *, to_legacy: bool = False, apply: bool = False) -> dict:
    if not state_file.is_file() or state_file.is_symlink():
        raise ValueError("Expected an existing regular state file.")
    store = JsonStore(state_file)
    try:
        with store._process_lock.acquire():
            state = store._read_state(preserve_corrupted=False)
            legacy = _legacy_positions(state)
            private = _private_positions(state_file)
            changes = _advances(private, legacy) if to_legacy else _advances(legacy, private)
            result = {
                "direction": "to-legacy" if to_legacy else "to-private",
                "apply": apply,
                "legacy_count": len(legacy),
                "private_count": len(private),
                "positions_to_copy": len(changes),
                "change_state_file": bool(changes)
                if to_legacy
                else LEGACY_KEY in state.get("settings", {}),
            }
            if not result["change_state_file"]:
                return result
            if to_legacy:
                merged = {**legacy, **changes}
                if normalize_legacy_cashbox_receipts({LEGACY_KEY: merged}) != merged:
                    raise ValueError("The previous CRM cannot represent all private receipt keys.")
                if apply:
                    state.setdefault("settings", {})[LEGACY_KEY] = merged
                    store._write_state(state)
                    if _legacy_positions(store._read_state(preserve_corrupted=False)) != merged:
                        raise ValueError("Legacy receipt readback did not match.")
                return result
        if not apply:
            return result

        # Runtime writers take the actor lock first, then the state lock for publication.
        receipts = store.cashbox_receipts
        for key, position in changes.items():
            receipts.advance(key, position, publish_fence=store._process_lock.acquire)
        if legacy:
            # A previous attempt may have replaced its last row and failed its directory fsync.
            receipts.sync_committed_directory()
        with store._process_lock.acquire():
            current = store._read_state(preserve_corrupted=False)
            if _legacy_positions(current) != legacy or _advances(
                legacy, _private_positions(state_file)
            ):
                raise ValueError(
                    "Receipt positions changed during migration; retry with fresh data."
                )
            if LEGACY_KEY in current.get("settings", {}):
                del current["settings"][LEGACY_KEY]
                store._write_state(current)
            if LEGACY_KEY in store._read_state(preserve_corrupted=False).get("settings", {}):
                raise ValueError("Legacy receipt cleanup failed readback.")
        return result
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--direction", choices=("to-private", "to-legacy"), default="to-private")
    parser.add_argument("--apply", action="store_true", help="Apply to a quiesced CRM profile.")
    args = parser.parse_args()
    try:
        result = migrate(args.state_file, to_legacy=args.direction == "to-legacy", apply=args.apply)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"Cashbox receipt migration failed ({type(error).__name__}): {error}\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
