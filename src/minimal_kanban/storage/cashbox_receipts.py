"""Private, monotonic cashbox notification receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import (
    ACTOR_NAME_LIMIT,
    business_timezone,
    normalize_actor_name,
    normalize_text,
    parse_datetime,
)
from .file_lock import ProcessFileLock
from .limited_io import read_text_limited

CASHBOX_NOTIFICATION_SEEN_SETTING_KEY = "_cashbox_notification_seen_by_users"
CASHBOX_RECEIPT_MAX_BYTES = 256 * 1024
_ROW_FIELDS = {"schema_version", "actor_key", "transaction_id", "created_at"}
_POSITION_FIELDS = {"transaction_id", "created_at", "registration_seq"}
_EMPTY_TIME = datetime.min.replace(tzinfo=UTC)
CASHBOX_RECEIPT_FILE_RE = re.compile(r"[0-9a-f]{64}\.json")
CASHBOX_RECEIPT_TRANSIENT_RE = re.compile(
    r"(?:[0-9a-f]{64}\.lock|\.[0-9a-f]{64}\.[0-9a-f]{32}\.tmp)"
)


class CashboxReceiptStoreError(ValueError):
    """The private receipt could not be safely read or updated."""


def cashbox_receipt_directory(state_file: Path) -> Path:
    return state_file.with_suffix(".cashbox_receipts")


def cashbox_receipt_filename(actor_key: str) -> str:
    return hashlib.sha256(actor_key.encode("utf-8")).hexdigest() + ".json"


def _cashbox_receipt_time(value: str) -> datetime | None:
    try:
        parsed = parse_datetime(value)
        return parsed.astimezone(business_timezone()) if parsed is not None else None
    except (OverflowError, ValueError):
        return None


def cashbox_receipt_key(receipt: Mapping[str, Any] | None) -> tuple[int, datetime, str]:
    raw = receipt or {}
    seq = raw.get("registration_seq", 0)
    if seq:
        return seq, _EMPTY_TIME, raw["transaction_id"]
    timestamp = _cashbox_receipt_time(normalize_text(raw.get("created_at"), limit=80))
    if timestamp is None:
        timestamp = datetime.min.replace(tzinfo=business_timezone())
    return 0, timestamp, normalize_text(raw.get("transaction_id"), limit=128)


def normalize_legacy_cashbox_receipts(settings: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    raw_seen = settings.get(CASHBOX_NOTIFICATION_SEEN_SETTING_KEY)
    if not isinstance(raw_seen, dict):
        return {}
    normalized = {}
    for raw_actor, raw_receipt in raw_seen.items():
        actor_key = normalize_actor_name(raw_actor, default="").casefold()
        if actor_key and isinstance(raw_receipt, dict):
            created_at = normalize_text(raw_receipt.get("created_at"), default="", limit=80)
            normalized[actor_key] = {
                "transaction_id": normalize_text(
                    raw_receipt.get("transaction_id"), default="", limit=128
                ),
                "created_at": created_at if _cashbox_receipt_time(created_at) is not None else "",
            }
    return normalized


def newer_cashbox_receipt(
    legacy: dict[str, Any] | None, private: dict[str, Any] | None
) -> dict[str, Any] | None:
    if private is None:
        return legacy
    if legacy is None or cashbox_receipt_key(private) >= cashbox_receipt_key(legacy):
        return private
    # The old global boundary may advance while a newer client has also
    # recorded positions for individual cashboxes. Keep both kinds of evidence.
    return {**private, **legacy, "registration_seq": legacy.get("registration_seq", 0)}


def _valid_position(position: dict) -> bool:
    tx_id, date = position["transaction_id"], position["created_at"]
    seq = position.get("registration_seq", 0)
    return (
        isinstance(tx_id, str)
        and len(tx_id) <= 128
        and isinstance(date, str)
        and len(date) <= 80
        and (not date or _cashbox_receipt_time(date) is not None)
        and type(seq) is int
        and seq >= 0
        and (not seq or bool(tx_id and date))
    )


def validate_cashbox_receipt_row(payload: object, filename: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CashboxReceiptStoreError("Invalid cashbox receipt record.")
    version = payload.get("schema_version")
    fields = _ROW_FIELDS | {"registration_seq", "by_cashbox"} if version == 2 else _ROW_FIELDS
    if type(version) is not int or version not in (1, 2) or set(payload) != fields:
        raise CashboxReceiptStoreError("Invalid cashbox receipt record.")
    actor_key = payload["actor_key"]
    if (
        not isinstance(actor_key, str)
        or not actor_key.strip()
        or len(actor_key) > ACTOR_NAME_LIMIT * 3
        or actor_key != actor_key.casefold()
        or not _valid_position(payload)
    ):
        raise CashboxReceiptStoreError("Invalid cashbox receipt record.")
    by_cashbox = payload.get("by_cashbox", {})
    if not isinstance(by_cashbox, dict) or any(
        not isinstance(box, str)
        or not box.strip()
        or len(box) > 128
        or not isinstance(pos, dict)
        or set(pos) != _POSITION_FIELDS
        or not _valid_position(pos)
        for box, pos in by_cashbox.items()
    ):
        raise CashboxReceiptStoreError("Invalid per-cashbox receipt.")
    try:
        valid_filename = cashbox_receipt_filename(actor_key)
        for pos in (payload, *by_cashbox.values()):
            pos["transaction_id"].encode("utf-8")
            pos["created_at"].encode("utf-8")
        for box in by_cashbox:
            box.encode("utf-8")
    except UnicodeError as exc:
        raise CashboxReceiptStoreError("Invalid cashbox receipt record.") from exc
    if valid_filename != filename or not CASHBOX_RECEIPT_FILE_RE.fullmatch(filename):
        raise CashboxReceiptStoreError("Invalid cashbox receipt filename.")
    return payload


def _unique_json_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise CashboxReceiptStoreError("Invalid cashbox receipt record.")
    return result


def load_cashbox_receipt_file(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise CashboxReceiptStoreError("Invalid cashbox receipt file.")
        payload = json.loads(
            read_text_limited(path, max_bytes=CASHBOX_RECEIPT_MAX_BYTES, label="cashbox receipt"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, ValueError, RecursionError) as exc:
        raise CashboxReceiptStoreError("Invalid cashbox receipt file.") from exc
    return validate_cashbox_receipt_row(payload, path.name)


def iter_cashbox_receipt_rows(state_file: Path) -> Iterator[dict[str, Any]]:
    directory = cashbox_receipt_directory(state_file)
    try:
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise CashboxReceiptStoreError("Invalid cashbox receipts directory.")
        if not directory.exists():
            return
        paths = sorted(directory.iterdir())
    except OSError as exc:
        raise CashboxReceiptStoreError("Unable to enumerate cashbox receipts.") from exc
    for path in paths:
        if (
            CASHBOX_RECEIPT_TRANSIENT_RE.fullmatch(path.name)
            and path.is_file()
            and not path.is_symlink()
        ):
            continue
        yield load_cashbox_receipt_file(path)


class CashboxReceiptStore:
    def __init__(self, state_file: Path) -> None:
        self._directory = cashbox_receipt_directory(state_file)
        self._registered_directory: tuple[int, int] | None = None

    def _path(self, actor_key: str) -> Path:
        if self._directory.is_symlink() or (
            self._directory.exists() and not self._directory.is_dir()
        ):
            raise CashboxReceiptStoreError("Invalid cashbox receipts directory.")
        return self._directory / cashbox_receipt_filename(actor_key)

    @staticmethod
    def _read_current(path: Path) -> dict[str, Any] | None:
        if not path.exists() and not path.is_symlink():
            return None
        return load_cashbox_receipt_file(path)

    @staticmethod
    def _actor_lock(path: Path) -> ProcessFileLock:
        lock_path = path.with_suffix(".lock")
        if lock_path.is_symlink():
            raise CashboxReceiptStoreError("Invalid cashbox receipt lock.")
        return ProcessFileLock(lock_path, metric_name="cashbox_receipt_lock")

    def read(self, actor_key: str) -> dict[str, Any] | None:
        try:
            path = self._path(actor_key)
            if not path.exists() and not path.is_symlink():
                return None
            if os.name != "nt":
                return self._read_current(path)
            # Windows readers must close the row before the writer can replace it.
            with self._actor_lock(path).acquire():
                return self._read_current(path)
        except (OSError, UnicodeError) as exc:
            raise CashboxReceiptStoreError("Unable to read cashbox receipt.") from exc

    def advance(
        self,
        actor_key: str,
        receipt: Mapping[str, Any],
        *,
        legacy_receipt: dict[str, Any] | None = None,
        cashbox_id: str = "",
        initialize_only: bool = False,
        resolve_legacy: Callable[[dict], dict] | None = None,
        publish_fence: Callable[[], AbstractContextManager] = nullcontext,
    ) -> tuple[bool, dict[str, Any]]:
        try:
            path = self._path(actor_key)
            position = {
                "transaction_id": receipt["transaction_id"],
                "created_at": receipt["created_at"],
                "registration_seq": receipt.get("registration_seq", 0),
            }
            if not _valid_position(position):
                raise CashboxReceiptStoreError("Invalid cashbox receipt position.")
            if legacy_receipt is not None and (
                initialize_only
                or cashbox_receipt_key(position) <= cashbox_receipt_key(legacy_receipt)
            ):
                private = self.read(actor_key)
                if private and private["schema_version"] == 1 and resolve_legacy:
                    private = resolve_legacy(private)
                effective = newer_cashbox_receipt(legacy_receipt, private)
                assert effective is not None
                if private is not None:
                    self._sync_directory(self._directory)
                return False, effective
            self._directory.mkdir(mode=0o700, exist_ok=True)
            with self._actor_lock(path).acquire():
                private = self._read_current(path)
                if private and private["schema_version"] == 1 and resolve_legacy:
                    private = resolve_legacy(private)
                current = newer_cashbox_receipt(legacy_receipt, private)
                scoped = (current or {}).get("by_cashbox", {})
                threshold = max(
                    cashbox_receipt_key(current), cashbox_receipt_key(scoped.get(cashbox_id))
                )
                if current is not None and (
                    initialize_only or cashbox_receipt_key(position) <= threshold
                ):
                    # A previous replace may have succeeded even if its directory
                    # sync failed, so confirm durability before a no-op succeeds.
                    if private is not None:
                        self._sync_directory(self._directory)
                    return False, current
                if cashbox_id:
                    baseline = current or {}
                    global_position = {
                        "transaction_id": baseline.get("transaction_id", ""),
                        "created_at": baseline.get("created_at", ""),
                        "registration_seq": baseline.get("registration_seq", 0),
                    }
                    scoped = {**scoped, cashbox_id: position}
                else:
                    global_position = position
                row = {
                    "schema_version": 2,
                    "actor_key": actor_key,
                    **global_position,
                    "by_cashbox": scoped,
                }
                if not scoped and not global_position["registration_seq"]:
                    row = {
                        "schema_version": 1,
                        "actor_key": actor_key,
                        "transaction_id": position["transaction_id"],
                        "created_at": position["created_at"],
                    }
                validate_cashbox_receipt_row(row, path.name)
                self._register_directory()
                self._publish(path, row, publish_fence)
                return True, row
        except (OSError, UnicodeError) as exc:
            raise CashboxReceiptStoreError("Unable to write cashbox receipt.") from exc

    def sync_committed_directory(self) -> None:
        """Confirm private rows and their parent entry before retiring legacy data."""
        try:
            if self._directory.is_symlink() or (
                self._directory.exists() and not self._directory.is_dir()
            ):
                raise CashboxReceiptStoreError("Invalid cashbox receipts directory.")
            if self._directory.exists():
                self._sync_directory(self._directory)
                self._sync_directory(self._directory.parent)
        except OSError as exc:
            raise CashboxReceiptStoreError("Unable to sync cashbox receipts directory.") from exc

    def _register_directory(self) -> None:
        if os.name == "nt":
            return
        stat = self._directory.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self._registered_directory != identity:
            # Register the parent entry before the first publisher in this process
            # can report success, even if another actor created the directory.
            self._sync_directory(self._directory.parent)
            self._registered_directory = identity

    @staticmethod
    def _publish(path: Path, row: dict[str, Any], publish_fence) -> None:
        payload = json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        if len(payload) > CASHBOX_RECEIPT_MAX_BYTES:
            raise CashboxReceiptStoreError("Cashbox receipt is too large.")
        temp = path.with_name(f".{path.stem}.{uuid4().hex}.tmp")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            with publish_fence():
                temp.replace(path)
            CashboxReceiptStore._sync_directory(path.parent)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
