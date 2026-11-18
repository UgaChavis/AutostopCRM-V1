"""Migrate private cashbox cursors or prepare the current data for an older CRM."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.models import normalize_actor_name  # noqa: E402
from minimal_kanban.storage.cashbox_receipts import (  # noqa: E402
    CASHBOX_NOTIFICATION_SEEN_SETTING_KEY as LEGACY_KEY,
)
from minimal_kanban.storage.cashbox_receipts import (  # noqa: E402
    CASHBOX_RECEIPT_FILE_RE,
    cashbox_receipt_directory,
    cashbox_receipt_key,
    iter_cashbox_receipt_rows,
    normalize_legacy_cashbox_receipts,
)
from minimal_kanban.storage.json_store import JSON_STORE_STATE_MAX_BYTES, JsonStore  # noqa: E402
from minimal_kanban.storage.limited_io import read_text_limited  # noqa: E402


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


def _require_crm_user(state_file: Path) -> None:
    if os.name != "posix":
        return
    owner = (os.geteuid(), os.getegid())
    state_stat = state_file.lstat()
    if (state_stat.st_uid, state_stat.st_gid) != owner:
        raise PermissionError("Run this migration as the CRM state file's runtime UID and GID.")
    parent = state_file.parent.stat()
    if owner[0] == 0 and parent.st_uid != 0:
        raise PermissionError("Refusing to migrate a non-root CRM profile as root.")
    if parent.st_mode & stat.S_ISGID and parent.st_gid != owner[1]:
        raise PermissionError("New profile files would inherit a different CRM group.")

    def check_file(path: Path, *, private: bool = False) -> None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        mode = stat.S_IMODE(info.st_mode)
        if (
            not stat.S_ISREG(info.st_mode)
            or (info.st_uid, info.st_gid) != owner
            or mode & 0o600 != 0o600
            or (private and mode != 0o600)
        ):
            raise PermissionError(
                "A CRM state, receipt or lock file has unexpected ownership or permissions."
            )

    check_file(state_file)
    check_file(state_file.with_suffix(".lock"))
    for filename in ("change_feed.sqlite3", "change_feed.sqlite3-wal", "change_feed.sqlite3-shm"):
        check_file(state_file.with_name(filename))
    directory = cashbox_receipt_directory(state_file)
    try:
        info = directory.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_uid, info.st_gid) != owner
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise PermissionError(
            "The cashbox receipt directory must belong to the CRM user with mode 0700."
        )
    for path in directory.iterdir():
        check_file(path, private=bool(CASHBOX_RECEIPT_FILE_RE.fullmatch(path.name)))


@contextmanager
def _migration_store(state_file: Path, apply: bool):
    if not apply:
        yield None
        return
    _require_crm_user(state_file)
    store = JsonStore(state_file)
    try:
        yield store
        if os.name == "posix":
            # Retry this barrier even if a previous replace already made the state match.
            descriptor = os.open(state_file.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        store.close()


def migrate(state_file: Path, *, to_legacy: bool = False, apply: bool = False) -> dict:
    if not state_file.is_file() or state_file.is_symlink():
        raise ValueError("Expected an existing regular state file.")
    with _migration_store(state_file, apply) as store:
        with store._process_lock.acquire() if store else nullcontext():
            # The offline preview must also work on a read-only state-only recovery copy.
            state = JsonStore._decode_state(
                read_text_limited(
                    state_file, max_bytes=JSON_STORE_STATE_MAX_BYTES, label="state file"
                )
            )
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
                if store is not None:
                    state.setdefault("settings", {})[LEGACY_KEY] = merged
                    store._write_state(state, preserve_posix_metadata=True)
                    if _legacy_positions(store._read_state(preserve_corrupted=False)) != merged:
                        raise ValueError("Legacy receipt readback did not match.")
                return result
        if store is None:
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
                store._write_state(current, preserve_posix_metadata=True)
            if LEGACY_KEY in store._read_state(preserve_corrupted=False).get("settings", {}):
                raise ValueError("Legacy receipt cleanup failed readback.")
        return result


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
