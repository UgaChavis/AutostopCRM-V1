"""Migrate private cashbox cursors or prepare the current data for an older CRM."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.models import (  # noqa: E402
    CASH_REGISTRATION_SEQUENCE_SETTING as COUNTER,
)
from minimal_kanban.models import normalize_actor_name  # noqa: E402
from minimal_kanban.storage.cashbox_receipts import (  # noqa: E402
    CASHBOX_NOTIFICATION_SEEN_SETTING_KEY as LEGACY_KEY,
)
from minimal_kanban.storage.cashbox_receipts import (  # noqa: E402
    CASHBOX_RECEIPT_FILE_RE,
    CashboxReceiptStore,
    cashbox_receipt_directory,
    cashbox_receipt_filename,
    cashbox_receipt_key,
    iter_cashbox_receipt_rows,
    normalize_legacy_cashbox_receipts,
    validate_cashbox_receipt_row,
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
    return {row["actor_key"]: row for row in iter_cashbox_receipt_rows(state_file)}


def _legacy_position(row: dict) -> dict:
    return {"transaction_id": row["transaction_id"], "created_at": row["created_at"]}


def _v2_position(actor, row):
    return {
        "schema_version": 2,
        "actor_key": actor,
        **_legacy_position(row),
        "registration_seq": row.get("registration_seq", 0),
        "by_cashbox": row.get("by_cashbox", {}),
    }


def _rollback_manifest(state, legacy, private):
    transactions = state.get("cash_transactions", [])
    seqs = {tx["id"]: tx.get("registration_seq", 0) for tx in transactions}
    business_dates = {tx["id"]: tx["created_at"] for tx in transactions}
    if len(seqs) != len(transactions):
        raise ValueError("Duplicate cashbox transaction IDs.")
    history = []
    for tx in transactions:
        position = {"transaction_id": tx["id"], "created_at": tx["created_at"]}
        key = cashbox_receipt_key({**position, "registration_seq": seqs[tx["id"]]})
        author = normalize_actor_name(tx.get("actor_name"), default="").casefold()
        history.append((cashbox_receipt_key(position), position, key, tx["cashbox_id"], author))
    history.sort(key=lambda item: item[0])
    originals, projection = {}, {}
    for actor in legacy.keys() | private.keys():
        candidates = []
        for row in (legacy.get(actor), private.get(actor)):
            if row is None:
                continue
            seq = row.get("registration_seq")
            if seq is None and business_dates.get(row["transaction_id"]) == row["created_at"]:
                seq = seqs[row["transaction_id"]]
            candidates.append({**row, "registration_seq": seq or 0})
        original = _v2_position(actor, max(candidates, key=cashbox_receipt_key))
        original["by_cashbox"] = private.get(actor, {}).get("by_cashbox", {})
        originals[actor] = original
        global_key = cashbox_receipt_key(original)
        scopes = {box: cashbox_receipt_key(pos) for box, pos in original["by_cashbox"].items()}
        # A single business-date boundary can only confirm a contiguous prefix.
        old = _legacy_position(original)
        safe = {"transaction_id": "", "created_at": ""}
        for old_key, position, key, box, author in history:
            if author != actor and key > max(global_key, scopes.get(box, global_key)):
                if cashbox_receipt_key(old) < old_key:
                    safe = max(safe, old, key=cashbox_receipt_key)
                break
            safe = position
        else:
            safe = max(safe, old, key=cashbox_receipt_key)
        projection[actor] = safe
    return {
        "version": 1,
        "phase": "preparing",
        "originals": originals,
        "projection": projection,
        "sequences": seqs,
        "highwater": max(state.get("settings", {}).get(COUNTER, 0), max(seqs.values(), default=0)),
    }


def _manifest_path(state_file):
    return state_file.with_suffix(".cashbox_rollback.json")


def _validate_manifest(value):
    if not isinstance(value, dict) or set(value) != {
        "version",
        "phase",
        "originals",
        "projection",
        "sequences",
        "highwater",
    }:
        raise ValueError("Invalid cashbox rollback manifest.")
    if (
        type(value["version"]) is not int
        or value["version"] != 1
        or value["phase"] not in ("preparing", "legacy", "restoring")
        or type(value["highwater"]) is not int
        or value["highwater"] < 0
        or not isinstance(value["sequences"], dict)
        or any(
            not key or type(seq) is not int or not 0 <= seq <= value["highwater"]
            for key, seq in value["sequences"].items()
        )
        or not isinstance(value["originals"], dict)
        or not isinstance(value["projection"], dict)
        or value["originals"].keys() != value["projection"].keys()
        or normalize_legacy_cashbox_receipts({LEGACY_KEY: value["projection"]})
        != value["projection"]
    ):
        raise ValueError("Invalid cashbox rollback data.")
    for actor, row in value["originals"].items():
        validate_cashbox_receipt_row(row, cashbox_receipt_filename(actor))
        if row["schema_version"] != 2:
            raise ValueError("Invalid original cashbox rollback position.")
    return value


def _read_manifest(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Expected a regular private rollback manifest.")
    if not path.exists():
        return None
    return _validate_manifest(
        json.loads(
            read_text_limited(
                path, max_bytes=JSON_STORE_STATE_MAX_BYTES, label="cashbox rollback manifest"
            )
        )
    )


def _write_manifest(path, manifest):
    payload = json.dumps(manifest, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(payload) > JSON_STORE_STATE_MAX_BYTES:
        raise ValueError("Cashbox rollback manifest exceeds the size limit.")
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
        CashboxReceiptStore._sync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def _restore_registration(state, manifest, *, to_legacy):
    changed = False
    highwater = manifest["highwater"]
    transactions = state.get("cash_transactions", [])
    numbers = manifest["sequences"].copy()
    if not to_legacy:
        if len({tx["id"] for tx in transactions}) != len(transactions):
            raise ValueError("Duplicate cashbox transaction IDs during restore.")
        # The previous writer discarded insertion order. Only its business
        # timestamp and stable ID survive a rewrite or interrupted retry.
        added = (tx for tx in transactions if tx["id"] not in numbers)
        for tx in sorted(
            added,
            key=lambda row: cashbox_receipt_key(
                {"transaction_id": row["id"], "created_at": row["created_at"]}
            ),
        ):
            highwater += 1
            numbers[tx["id"]] = highwater
    for tx in transactions:
        seq = numbers.get(tx["id"], 0)
        if to_legacy or not seq:
            changed |= tx.pop("registration_seq", None) is not None
        elif tx.get("registration_seq") != seq:
            tx["registration_seq"] = seq
            changed = True
    settings = state.setdefault("settings", {})
    if to_legacy or not highwater:
        changed |= settings.pop(COUNTER, None) is not None
    elif settings.get(COUNTER) != highwater:
        settings[COUNTER] = highwater
        changed = True
    return changed


def _migrate_registration(
    state_file, store, state, legacy, private, manifest, *, to_legacy, result
):
    path = _manifest_path(state_file)
    if to_legacy and manifest["phase"] == "restoring":
        raise ValueError("Finish the interrupted to-private migration before preparing a rollback.")
    if to_legacy and manifest["phase"] == "legacy":
        return result
    state_change = _restore_registration(state, manifest, to_legacy=to_legacy)
    settings = state["settings"]
    if to_legacy:
        projected = manifest["projection"]
        if legacy != projected:
            settings[LEGACY_KEY] = projected
            state_change = True
        rows = {
            actor: {"schema_version": 1, "actor_key": actor, **position}
            for actor, position in projected.items()
        }
    else:
        state_change |= settings.pop(LEGACY_KEY, None) is not None
        rows = manifest["originals"].copy()
        for actor in (legacy.keys() | private.keys()) - rows.keys():
            # An actor first seen by the old writer could have acknowledged a
            # business date spanning unseen backdated entries. The v2 zero
            # prevents this legacy position from promoting to a registered ID.
            position = max(
                (row for row in (legacy.get(actor), private.get(actor)) if row),
                key=cashbox_receipt_key,
            )
            rows[actor] = _v2_position(actor, position)
    changes = {actor: row for actor, row in rows.items() if private.get(actor) != row}
    result.update(change_state_file=state_change, positions_to_copy=len(changes))
    if store is None:
        return result
    # A retry also confirms a previously replaced manifest's directory entry
    # before overwriting either original. Writers must be offline throughout.
    if to_legacy:
        if not path.exists():
            _write_manifest(path, manifest)
    elif manifest["phase"] != "restoring":
        _write_manifest(path, {**manifest, "phase": "restoring"})
    CashboxReceiptStore._sync_directory(path.parent)
    directory = cashbox_receipt_directory(state_file)
    if changes:
        directory.mkdir(mode=0o700, exist_ok=True)
    for actor, row in changes.items():
        target = directory / cashbox_receipt_filename(actor)
        validate_cashbox_receipt_row(row, target.name)
        CashboxReceiptStore._publish(target, row, nullcontext)
    store.cashbox_receipts.sync_committed_directory()
    if _private_positions(state_file) != rows:
        raise ValueError("Private cashbox positions failed readback.")
    if state_change:
        store._write_state(state, preserve_posix_metadata=True)
    if store._read_state(preserve_corrupted=False) != state:
        raise ValueError("Migrated state failed readback.")
    CashboxReceiptStore._sync_directory(path.parent)
    if to_legacy:
        _write_manifest(path, {**manifest, "phase": "legacy"})
    else:
        path.unlink()
    return result


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
    check_file(_manifest_path(state_file), private=True)
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
            result = {
                "direction": "to-legacy" if to_legacy else "to-private",
                "apply": apply,
                "legacy_count": len(legacy),
                "private_count": len(private),
                "positions_to_copy": 0,
                "change_state_file": False,
            }
            manifest = _read_manifest(_manifest_path(state_file))
            if (
                manifest is None
                and to_legacy
                and (
                    any(row["schema_version"] == 2 for row in private.values())
                    or COUNTER in state.get("settings", {})
                    or any(tx.get("registration_seq") for tx in state.get("cash_transactions", []))
                )
            ):
                manifest = _validate_manifest(_rollback_manifest(state, legacy, private))
            if manifest is not None:
                return _migrate_registration(
                    state_file,
                    store,
                    state,
                    legacy,
                    private,
                    manifest,
                    to_legacy=to_legacy,
                    result=result,
                )
            if to_legacy:
                changes = _advances({k: _legacy_position(v) for k, v in private.items()}, legacy)
                merged = {**legacy, **changes}
                result.update(positions_to_copy=len(changes), change_state_file=bool(changes))
                if normalize_legacy_cashbox_receipts({LEGACY_KEY: merged}) != merged:
                    raise ValueError("The previous CRM cannot represent all private receipt keys.")
                if store is not None:
                    if changes:
                        state.setdefault("settings", {})[LEGACY_KEY] = merged
                        store._write_state(state, preserve_posix_metadata=True)
                    if _legacy_positions(store._read_state(preserve_corrupted=False)) != merged:
                        raise ValueError("Legacy receipt readback did not match.")
                return result
            changes = _advances(legacy, private)
            result.update(
                positions_to_copy=len(changes),
                change_state_file=LEGACY_KEY in state.get("settings", {}),
            )
            if not result["change_state_file"]:
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
