from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .change_feed_projection import (
    ProjectedEntity,
    diff_projected_entities,
    project_crm_source_signatures,
    project_crm_state,
)

CHANGE_FEED_SCHEMA_VERSION = 3
CHANGE_FEED_PAGE_DEFAULT = 25
CHANGE_FEED_PAGE_MAX = 25
CHANGE_FEED_CONSUMER_MAX_LENGTH = 64
CHANGE_FEED_TOKEN_MAX_LENGTH = 2048
CHANGE_FEED_EVENT_ID_MAX_LENGTH = 128
CHANGE_FEED_ACTION_MAX_LENGTH = 80
CHANGE_FEED_ENTITY_ID_MAX_LENGTH = 128
CHANGE_FEED_TIMESTAMP_MAX_LENGTH = 48

_CONSUMER_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")

_ENTITY_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("client_vehicle_", "client_vehicle", ("client_vehicle_id", "vehicle_id")),
    ("cash_transaction_", "cash_transaction", ("cash_transaction_id", "transaction_id")),
    ("inventory_movement_", "inventory_movement", ("inventory_movement_id", "movement_id")),
    ("inventory_item_", "inventory_item", ("inventory_item_id", "item_id")),
    ("repair_order_", "repair_order", ("repair_order_id", "order_id", "card_id")),
    ("shared_file_", "shared_file", ("shared_file_id", "file_id")),
    ("attachment_", "attachment", ("attachment_id", "file_id")),
    ("cashbox_", "cashbox", ("cashbox_id",)),
    ("employee_", "employee", ("employee_id",)),
    ("client_", "client", ("client_id",)),
    ("column_", "column", ("column_id",)),
    ("sticky_", "sticky", ("sticky_id",)),
    ("card_", "card", ("card_id",)),
)


@dataclass(slots=True)
class ChangeFeedProtocolError(ValueError):
    code: str
    message: str
    status_code: int = 409

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.message)


class ChangeFeedPendingWriteError(RuntimeError):
    """Raised when a prior durable outbox stage must be reconciled first."""


def _bounded_text(value: object, *, limit: int) -> str:
    text = _CONTROL_CHARACTERS.sub(" ", str(value or "")).strip()
    return text[:limit]


def _opaque_ref(prefix: str, value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _validated_consumer_id(value: object) -> str:
    consumer_id = value.strip() if isinstance(value, str) else ""
    if not _CONSUMER_PATTERN.fullmatch(consumer_id):
        raise ChangeFeedProtocolError(
            "invalid_consumer",
            "consumer_id must use 1-64 ASCII letters, digits, dots, colons, dashes or underscores.",
            400,
        )
    return consumer_id


def _event_entity(action: str, event: Mapping[str, Any]) -> tuple[str, str]:
    details = event.get("details")
    safe_details = details if isinstance(details, Mapping) else {}
    card_id = _bounded_text(event.get("card_id"), limit=CHANGE_FEED_ENTITY_ID_MAX_LENGTH)
    for prefix, entity_type, keys in _ENTITY_RULES:
        if not action.startswith(prefix):
            continue
        for key in keys:
            candidate = (
                card_id
                if key == "card_id"
                else _bounded_text(safe_details.get(key), limit=CHANGE_FEED_ENTITY_ID_MAX_LENGTH)
            )
            if candidate:
                return entity_type, candidate
        if card_id:
            return "card", card_id
        return entity_type, entity_type
    if card_id:
        return "card", card_id
    for key, entity_type in (
        ("card_id", "card"),
        ("client_vehicle_id", "client_vehicle"),
        ("client_id", "client"),
        ("column_id", "column"),
        ("sticky_id", "sticky"),
        ("cashbox_id", "cashbox"),
        ("employee_id", "employee"),
        ("inventory_item_id", "inventory_item"),
        ("file_id", "shared_file"),
    ):
        candidate = _bounded_text(safe_details.get(key), limit=CHANGE_FEED_ENTITY_ID_MAX_LENGTH)
        if candidate:
            return entity_type, candidate
    return "board", "board"


def _change_type(action: str) -> tuple[str, bool]:
    if action.endswith("_created"):
        return "create", False
    if action.endswith("_restored"):
        return "restore", False
    if action.endswith("_archived"):
        return "archive", True
    if action.endswith(("_deleted", "_removed")):
        return "delete", True
    if action.endswith(("_moved", "_reordered", "_position_updated")):
        return "move", False
    return "update", False


def compact_change_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the bounded, PII-free feed projection of one legacy audit event."""

    source_event_id = _bounded_text(event.get("id"), limit=CHANGE_FEED_EVENT_ID_MAX_LENGTH)
    if not source_event_id:
        return None
    action = _bounded_text(event.get("action"), limit=CHANGE_FEED_ACTION_MAX_LENGTH).lower()
    if not action:
        action = "unknown"
    occurred_at = _bounded_text(event.get("timestamp"), limit=CHANGE_FEED_TIMESTAMP_MAX_LENGTH)
    details = event.get("details")
    safe_details = details if isinstance(details, Mapping) else {}
    entity_type, entity_id = _event_entity(action, event)
    change_type, tombstone = _change_type(action)
    correlation_source = safe_details.get("correlation_id") or safe_details.get("run_id")
    idempotency_source = safe_details.get("idempotency_key") or source_event_id
    return {
        "source_event_id": source_event_id,
        "occurred_at": occurred_at,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "change_type": change_type,
        "tombstone": tombstone,
        "correlation_ref": _opaque_ref("corr", correlation_source or source_event_id),
        "idempotency_ref": _opaque_ref("idem", idempotency_source),
        "producer": "audit_event",
    }


class ChangeFeedStore:
    """Durable ordered CRM change feed with explicit at-least-once delivery ACKs."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self, *, durable: bool = True) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute(f"PRAGMA synchronous = {'FULL' if durable else 'NORMAL'}")
        return connection

    @contextmanager
    def _connection(self, *, durable: bool = True):
        connection = self._connect(durable=durable)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self, *, immediate: bool = False, durable: bool = True):
        connection = self._connect(durable=durable)
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
                    source_event_id TEXT NOT NULL UNIQUE,
                    occurred_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    tombstone INTEGER NOT NULL CHECK (tombstone IN (0, 1)),
                    correlation_ref TEXT NOT NULL DEFAULT '',
                    idempotency_ref TEXT NOT NULL DEFAULT '',
                    producer TEXT NOT NULL DEFAULT 'audit_event'
                );
                CREATE TABLE IF NOT EXISTS seen_sources (
                    source_event_id TEXT PRIMARY KEY,
                    sequence INTEGER UNIQUE,
                    FOREIGN KEY(sequence) REFERENCES events(sequence) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS consumers (
                    consumer_id TEXT PRIMARY KEY,
                    acked_sequence INTEGER NOT NULL DEFAULT 0 CHECK (acked_sequence >= 0)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    consumer_id TEXT PRIMARY KEY,
                    window_high_water INTEGER NOT NULL CHECK (window_high_water >= 0),
                    FOREIGN KEY(consumer_id) REFERENCES consumers(consumer_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS pending_events (
                    ordinal INTEGER PRIMARY KEY CHECK (ordinal >= 0),
                    state_fingerprint TEXT NOT NULL,
                    source_event_id TEXT NOT NULL UNIQUE,
                    occurred_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    tombstone INTEGER NOT NULL CHECK (tombstone IN (0, 1)),
                    correlation_ref TEXT NOT NULL DEFAULT '',
                    idempotency_ref TEXT NOT NULL DEFAULT '',
                    producer TEXT NOT NULL DEFAULT 'audit_event'
                );
                CREATE TABLE IF NOT EXISTS entity_state (
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    routing_digest TEXT NOT NULL,
                    lifecycle TEXT NOT NULL,
                    PRIMARY KEY(entity_type, entity_id)
                );
                CREATE TABLE IF NOT EXISTS pending_entity_changes (
                    state_fingerprint TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK (operation IN ('upsert', 'delete')),
                    digest TEXT NOT NULL DEFAULT '',
                    routing_digest TEXT NOT NULL DEFAULT '',
                    lifecycle TEXT NOT NULL DEFAULT 'active',
                    PRIMARY KEY(entity_type, entity_id)
                );
                CREATE TABLE IF NOT EXISTS external_entity_state (
                    producer TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    routing_digest TEXT NOT NULL,
                    lifecycle TEXT NOT NULL,
                    PRIMARY KEY(producer, entity_type, entity_id)
                );
                CREATE TABLE IF NOT EXISTS source_state (
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    PRIMARY KEY(source_type, source_id)
                );
                CREATE TABLE IF NOT EXISTS pending_source_changes (
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN ('upsert', 'delete')),
                    signature TEXT,
                    PRIMARY KEY(source_type, source_id)
                );
                """
            )
            self._ensure_column(connection, "events", "correlation_ref", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "events", "idempotency_ref", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(
                connection, "events", "producer", "TEXT NOT NULL DEFAULT 'audit_event'"
            )
            self._ensure_column(
                connection, "pending_events", "correlation_ref", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                connection, "pending_events", "idempotency_ref", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                connection,
                "pending_events",
                "producer",
                "TEXT NOT NULL DEFAULT 'audit_event'",
            )
            defaults = {
                "schema_version": str(CHANGE_FEED_SCHEMA_VERSION),
                "generation": str(uuid4()),
                "cursor_secret": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
                "high_water": "0",
                "initialized": "0",
                "pending_fingerprint": "",
            }
            connection.executemany(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)", defaults.items()
            )
            version = self._metadata(connection, "schema_version")
            if version in {"1", "2"}:
                self._set_metadata(connection, "schema_version", CHANGE_FEED_SCHEMA_VERSION)
                version = str(CHANGE_FEED_SCHEMA_VERSION)
            if version != str(CHANGE_FEED_SCHEMA_VERSION):
                raise RuntimeError(f"Unsupported change-feed schema version: {version}")
        for database_file in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            try:
                os.chmod(database_file, 0o600)
            except OSError:
                pass

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {
            str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _metadata(connection: sqlite3.Connection, key: str) -> str:
        row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise RuntimeError(f"Missing change-feed metadata key: {key}")
        return str(row["value"])

    @staticmethod
    def _set_metadata(connection: sqlite3.Connection, key: str, value: object) -> None:
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    @staticmethod
    def _compact_events(events: object) -> list[dict[str, Any]]:
        if not isinstance(events, list):
            return []
        compacted: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_event in events:
            if not isinstance(raw_event, Mapping):
                continue
            event = compact_change_event(raw_event)
            if event is None or event["source_event_id"] in seen:
                continue
            seen.add(event["source_event_id"])
            compacted.append(event)
        return compacted

    @staticmethod
    def _compact_unseen_events(
        connection: sqlite3.Connection, events: object
    ) -> list[dict[str, Any]]:
        if not isinstance(events, list):
            return []
        known = {
            str(row["source_event_id"])
            for row in connection.execute("SELECT source_event_id FROM seen_sources").fetchall()
        }
        compacted: list[dict[str, Any]] = []
        pending_ids: set[str] = set()
        for raw_event in events:
            if not isinstance(raw_event, Mapping):
                continue
            source_event_id = _bounded_text(
                raw_event.get("id"), limit=CHANGE_FEED_EVENT_ID_MAX_LENGTH
            )
            if not source_event_id or source_event_id in known or source_event_id in pending_ids:
                continue
            event = compact_change_event(raw_event)
            if event is None:
                continue
            pending_ids.add(source_event_id)
            compacted.append(event)
        return compacted

    @staticmethod
    def _entity_state(connection: sqlite3.Connection) -> dict[tuple[str, str], ProjectedEntity]:
        rows = connection.execute(
            "SELECT entity_type, entity_id, digest, routing_digest, lifecycle FROM entity_state"
        ).fetchall()
        return {
            (str(row["entity_type"]), str(row["entity_id"])): ProjectedEntity(
                entity_type=str(row["entity_type"]),
                entity_id=str(row["entity_id"]),
                digest=str(row["digest"]),
                routing_digest=str(row["routing_digest"]),
                lifecycle=str(row["lifecycle"]),
            )
            for row in rows
        }

    @staticmethod
    def _replace_entity_baseline(
        connection: sqlite3.Connection,
        projected: Mapping[tuple[str, str], ProjectedEntity],
    ) -> None:
        connection.execute("DELETE FROM entity_state")
        connection.executemany(
            """
            INSERT INTO entity_state(
                entity_type, entity_id, digest, routing_digest, lifecycle
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                (
                    item.entity_type,
                    item.entity_id,
                    item.digest,
                    item.routing_digest,
                    item.lifecycle,
                )
                for item in projected.values()
            ),
        )

    @staticmethod
    def _source_state(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
        rows = connection.execute(
            "SELECT source_type, source_id, signature FROM source_state"
        ).fetchall()
        return {
            (str(row["source_type"]), str(row["source_id"])): str(row["signature"]) for row in rows
        }

    @staticmethod
    def _replace_source_baseline(
        connection: sqlite3.Connection,
        signatures: Mapping[tuple[str, str], str],
    ) -> None:
        connection.execute("DELETE FROM source_state")
        connection.executemany(
            "INSERT INTO source_state(source_type, source_id, signature) VALUES (?, ?, ?)",
            ((key[0], key[1], signature) for key, signature in signatures.items()),
        )

    @staticmethod
    def _entity_state_for_sources(
        connection: sqlite3.Connection,
        sources: set[tuple[str, str]],
    ) -> dict[tuple[str, str], ProjectedEntity]:
        projected: dict[tuple[str, str], ProjectedEntity] = {}
        for source_type, source_id in sources:
            if source_type == "card":
                rows = connection.execute(
                    """
                    SELECT entity_type, entity_id, digest, routing_digest, lifecycle
                    FROM entity_state
                    WHERE (
                        entity_type IN ('card', 'vehicle_profile', 'repair_order')
                        AND entity_id = ?
                    ) OR (
                        entity_type IN (
                            'repair_order_work', 'repair_order_material',
                            'repair_order_payment', 'attachment'
                        ) AND entity_id LIKE ?
                    )
                    """,
                    (source_id, f"{source_id}:%"),
                ).fetchall()
            elif source_type == "client":
                rows = connection.execute(
                    """
                    SELECT entity_type, entity_id, digest, routing_digest, lifecycle
                    FROM entity_state
                    WHERE (entity_type = 'client' AND entity_id = ?)
                       OR (entity_type = 'client_vehicle' AND entity_id LIKE ?)
                    """,
                    (source_id, f"{source_id}:%"),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT entity_type, entity_id, digest, routing_digest, lifecycle
                    FROM entity_state WHERE entity_type = ? AND entity_id = ?
                    """,
                    (source_type, source_id),
                ).fetchall()
            for row in rows:
                entity = ProjectedEntity(
                    entity_type=str(row["entity_type"]),
                    entity_id=str(row["entity_id"]),
                    digest=str(row["digest"]),
                    routing_digest=str(row["routing_digest"]),
                    lifecycle=str(row["lifecycle"]),
                )
                projected[(entity.entity_type, entity.entity_id)] = entity
        return projected

    @staticmethod
    def _audit_source_keys(
        audit_covered_entities: Mapping[tuple[str, str], set[str]],
    ) -> set[tuple[str, str]]:
        sources: set[tuple[str, str]] = set()
        for (entity_type, entity_id), _change_types in audit_covered_entities.items():
            if entity_type in {"card", "repair_order", "vehicle_profile"}:
                sources.add(("card", entity_id))
            elif entity_type == "board":
                sources.add(("board_settings", "board"))
            elif entity_type not in {"attachment", "client_vehicle", "shared_file"}:
                sources.add((entity_type, entity_id))
        return sources

    @staticmethod
    def _stage_source_changes(
        connection: sqlite3.Connection,
        *,
        previous: Mapping[tuple[str, str], str],
        current: Mapping[tuple[str, str], str],
    ) -> set[tuple[str, str]]:
        connection.execute("DELETE FROM pending_source_changes")
        changed = {
            key for key in set(previous) | set(current) if previous.get(key) != current.get(key)
        }
        for source_type, source_id in sorted(changed):
            signature = current.get((source_type, source_id))
            if signature is None:
                connection.execute(
                    """
                    INSERT INTO pending_source_changes(source_type, source_id, operation)
                    VALUES (?, ?, 'delete')
                    """,
                    (source_type, source_id),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO pending_source_changes(
                        source_type, source_id, operation, signature
                    ) VALUES (?, ?, 'upsert', ?)
                    """,
                    (source_type, source_id, signature),
                )
        return changed

    @staticmethod
    def _apply_pending_source_changes(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT source_type, source_id, operation, signature FROM pending_source_changes"
        ).fetchall()
        for row in rows:
            key = (str(row["source_type"]), str(row["source_id"]))
            if row["operation"] == "delete":
                connection.execute(
                    "DELETE FROM source_state WHERE source_type = ? AND source_id = ?", key
                )
            else:
                connection.execute(
                    """
                    INSERT INTO source_state(source_type, source_id, signature)
                    VALUES (?, ?, ?)
                    ON CONFLICT(source_type, source_id) DO UPDATE SET
                        signature = excluded.signature
                    """,
                    (key[0], key[1], row["signature"]),
                )
        connection.execute("DELETE FROM pending_source_changes")

    @staticmethod
    def _insert_pending_event(
        connection: sqlite3.Connection,
        *,
        ordinal: int,
        state_fingerprint: str,
        event: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO pending_events(
                ordinal, state_fingerprint, source_event_id, occurred_at, action,
                entity_type, entity_id, change_type, tombstone, correlation_ref,
                idempotency_ref, producer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ordinal,
                state_fingerprint,
                event["source_event_id"],
                event["occurred_at"],
                event["action"],
                event["entity_type"],
                event["entity_id"],
                event["change_type"],
                int(bool(event["tombstone"])),
                event["correlation_ref"],
                event["idempotency_ref"],
                event["producer"],
            ),
        )

    @staticmethod
    def _structural_event(
        *,
        state_fingerprint: str,
        entity_type: str,
        entity_id: str,
        action: str,
        change_type: str,
        tombstone: bool,
        producer: str = "state_projection",
    ) -> dict[str, Any]:
        identity = "|".join((state_fingerprint, entity_type, entity_id, action, change_type))
        source_event_id = f"state-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
        return {
            "source_event_id": source_event_id,
            "occurred_at": datetime.now(UTC).isoformat(),
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "change_type": change_type,
            "tombstone": tombstone,
            "correlation_ref": _opaque_ref("corr", state_fingerprint),
            "idempotency_ref": _opaque_ref("idem", state_fingerprint),
            "producer": producer,
        }

    def _stage_entity_changes(
        self,
        connection: sqlite3.Connection,
        *,
        state_fingerprint: str,
        state: object,
        audit_covered_entities: Mapping[tuple[str, str], set[str]],
        ordinal: int,
        incremental: bool = True,
    ) -> int:
        previous_sources = self._source_state(connection)
        current_sources = project_crm_source_signatures(state)
        changed_sources = self._stage_source_changes(
            connection,
            previous=previous_sources,
            current=current_sources,
        )
        if incremental:
            changed_sources.update(self._audit_source_keys(audit_covered_entities))
            if changed_sources:
                previous = self._entity_state_for_sources(connection, changed_sources)
                current = project_crm_state(state, sources=changed_sources)
            else:
                previous = {}
                current = {}
        else:
            previous = self._entity_state(connection)
            current = project_crm_state(state)
        changes = diff_projected_entities(previous, current)
        connection.execute("DELETE FROM pending_entity_changes")
        for key in sorted(set(previous) | set(current)):
            before = previous.get(key)
            after = current.get(key)
            if before == after:
                continue
            if after is None:
                connection.execute(
                    """
                    INSERT INTO pending_entity_changes(
                        state_fingerprint, entity_type, entity_id, operation
                    ) VALUES (?, ?, ?, 'delete')
                    """,
                    (state_fingerprint, key[0], key[1]),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO pending_entity_changes(
                        state_fingerprint, entity_type, entity_id, operation,
                        digest, routing_digest, lifecycle
                    ) VALUES (?, ?, ?, 'upsert', ?, ?, ?)
                    """,
                    (
                        state_fingerprint,
                        after.entity_type,
                        after.entity_id,
                        after.digest,
                        after.routing_digest,
                        after.lifecycle,
                    ),
                )
        for change in changes:
            if change.change_type in audit_covered_entities.get(
                (change.entity_type, change.entity_id), set()
            ):
                continue
            event = self._structural_event(
                state_fingerprint=state_fingerprint,
                entity_type=change.entity_type,
                entity_id=change.entity_id,
                action=change.action,
                change_type=change.change_type,
                tombstone=change.tombstone,
            )
            staged_event = {
                **event,
                "correlation_ref": _opaque_ref("corr", state_fingerprint),
                "idempotency_ref": _opaque_ref("idem", state_fingerprint),
            }
            self._insert_pending_event(
                connection,
                ordinal=ordinal,
                state_fingerprint=state_fingerprint,
                event=staged_event,
            )
            ordinal += 1
        return ordinal

    @staticmethod
    def _apply_pending_entity_changes(
        connection: sqlite3.Connection, state_fingerprint: str
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM pending_entity_changes
            WHERE state_fingerprint = ? ORDER BY entity_type, entity_id
            """,
            (state_fingerprint,),
        ).fetchall()
        for row in rows:
            key = (str(row["entity_type"]), str(row["entity_id"]))
            if row["operation"] == "delete":
                connection.execute(
                    "DELETE FROM entity_state WHERE entity_type = ? AND entity_id = ?", key
                )
                continue
            connection.execute(
                """
                INSERT INTO entity_state(
                    entity_type, entity_id, digest, routing_digest, lifecycle
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                    digest = excluded.digest,
                    routing_digest = excluded.routing_digest,
                    lifecycle = excluded.lifecycle
                """,
                (
                    key[0],
                    key[1],
                    row["digest"],
                    row["routing_digest"],
                    row["lifecycle"],
                ),
            )
        connection.execute("DELETE FROM pending_entity_changes")

    @staticmethod
    def _external_entity_state(
        connection: sqlite3.Connection, producer: str
    ) -> dict[tuple[str, str], ProjectedEntity]:
        rows = connection.execute(
            """
            SELECT entity_type, entity_id, digest, routing_digest, lifecycle
            FROM external_entity_state WHERE producer = ?
            """,
            (producer,),
        ).fetchall()
        return {
            (str(row["entity_type"]), str(row["entity_id"])): ProjectedEntity(
                entity_type=str(row["entity_type"]),
                entity_id=str(row["entity_id"]),
                digest=str(row["digest"]),
                routing_digest=str(row["routing_digest"]),
                lifecycle=str(row["lifecycle"]),
            )
            for row in rows
        }

    @staticmethod
    def _replace_external_entity_state(
        connection: sqlite3.Connection,
        producer: str,
        projected: Mapping[tuple[str, str], ProjectedEntity],
    ) -> None:
        connection.execute("DELETE FROM external_entity_state WHERE producer = ?", (producer,))
        connection.executemany(
            """
            INSERT INTO external_entity_state(
                producer, entity_type, entity_id, digest, routing_digest, lifecycle
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    producer,
                    item.entity_type,
                    item.entity_id,
                    item.digest,
                    item.routing_digest,
                    item.lifecycle,
                )
                for item in projected.values()
            ),
        )

    @staticmethod
    def _projection_fingerprint(
        producer: str, projected: Mapping[tuple[str, str], ProjectedEntity]
    ) -> str:
        rows = [
            (
                item.entity_type,
                item.entity_id,
                item.digest,
                item.routing_digest,
                item.lifecycle,
            )
            for item in sorted(
                projected.values(), key=lambda item: (item.entity_type, item.entity_id)
            )
        ]
        raw = json.dumps(
            {"producer": producer, "entities": rows},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def initialize_external_projection(
        self,
        producer: str,
        projected: Mapping[tuple[str, str], ProjectedEntity],
    ) -> None:
        source = _bounded_text(producer, limit=48)
        if not source:
            raise ValueError("external producer is required")
        initialized_key = f"external_initialized:{source}"
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = ?", (initialized_key,)
            ).fetchone()
            if row is not None and str(row["value"]) == "1":
                return
            self._replace_external_entity_state(connection, source, projected)
            self._set_metadata(connection, initialized_key, "1")

    def reconcile_external_projection(
        self,
        producer: str,
        projected: Mapping[tuple[str, str], ProjectedEntity],
    ) -> dict[str, Any]:
        source = _bounded_text(producer, limit=48)
        if not source:
            raise ValueError("external producer is required")
        initialized_key = f"external_initialized:{source}"
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = ?", (initialized_key,)
            ).fetchone()
            if row is None or str(row["value"]) != "1":
                self._replace_external_entity_state(connection, source, projected)
                self._set_metadata(connection, initialized_key, "1")
                return {**self._status(connection), "published": 0}
            previous = self._external_entity_state(connection, source)
            fingerprint = self._projection_fingerprint(source, projected)
            published = 0
            for change in diff_projected_entities(previous, projected):
                event = self._structural_event(
                    state_fingerprint=fingerprint,
                    entity_type=change.entity_type,
                    entity_id=change.entity_id,
                    action=change.action,
                    change_type=change.change_type,
                    tombstone=change.tombstone,
                    producer=source,
                )
                if self._publish_event(connection, event) is not None:
                    published += 1
            self._replace_external_entity_state(connection, source, projected)
            return {**self._status(connection), "published": published}

    def initialize_baseline(self, events: object, *, state: object | None = None) -> None:
        """Mark pre-feed audit history as known without publishing it to consumers."""

        compacted = self._compact_events(events)
        with self._transaction(immediate=True) as connection:
            if self._metadata(connection, "initialized") == "1":
                return
            connection.executemany(
                "INSERT OR IGNORE INTO seen_sources(source_event_id, sequence) VALUES (?, NULL)",
                ((event["source_event_id"],) for event in compacted),
            )
            self._replace_entity_baseline(connection, project_crm_state(state or {}))
            self._replace_source_baseline(connection, project_crm_source_signatures(state or {}))
            self._set_metadata(connection, "initialized", "1")

    def has_pending_state_write(self) -> bool:
        with self._connection() as connection:
            return bool(self._metadata(connection, "pending_fingerprint"))

    def _stage_unseen_audit_events(
        self,
        connection: sqlite3.Connection,
        *,
        state_fingerprint: str,
        compacted: list[dict[str, Any]],
    ) -> tuple[int, dict[tuple[str, str], set[str]]]:
        ordinal = 0
        covered: dict[tuple[str, str], set[str]] = {}
        for event in compacted:
            committed_event = {
                **event,
                "correlation_ref": _opaque_ref("corr", state_fingerprint),
                "idempotency_ref": _opaque_ref("idem", state_fingerprint),
            }
            self._insert_pending_event(
                connection,
                ordinal=ordinal,
                state_fingerprint=state_fingerprint,
                event=committed_event,
            )
            entity_key = (
                str(committed_event["entity_type"]),
                str(committed_event["entity_id"]),
            )
            covered.setdefault(entity_key, set()).add(str(committed_event["change_type"]))
            ordinal += 1
        return ordinal, covered

    def prepare_state_write(
        self, state_fingerprint: str, events: object, *, state: object | None = None
    ) -> int:
        """Durably stage unseen compact events before the CRM state replace."""

        fingerprint = _bounded_text(state_fingerprint, limit=128)
        if not fingerprint:
            raise ValueError("state_fingerprint is required")
        with self._transaction(immediate=True) as connection:
            if self._metadata(connection, "initialized") != "1":
                raise RuntimeError("Change feed baseline has not been initialized.")
            pending_fingerprint = self._metadata(connection, "pending_fingerprint")
            if pending_fingerprint and pending_fingerprint != fingerprint:
                raise ChangeFeedPendingWriteError(
                    "A prior change-feed outbox stage must be reconciled before another write."
                )
            compacted = self._compact_unseen_events(connection, events)
            connection.execute("DELETE FROM pending_events")
            ordinal, covered = self._stage_unseen_audit_events(
                connection,
                state_fingerprint=fingerprint,
                compacted=compacted,
            )
            if state is not None:
                ordinal = self._stage_entity_changes(
                    connection,
                    state_fingerprint=fingerprint,
                    state=state,
                    audit_covered_entities=covered,
                    ordinal=ordinal,
                )
            else:
                connection.execute("DELETE FROM pending_entity_changes")
                connection.execute("DELETE FROM pending_source_changes")
            self._set_metadata(connection, "pending_fingerprint", fingerprint)
            return ordinal

    def abort_state_write(self, state_fingerprint: str) -> None:
        with self._transaction(immediate=True) as connection:
            if self._metadata(connection, "pending_fingerprint") != state_fingerprint:
                return
            connection.execute("DELETE FROM pending_events")
            connection.execute("DELETE FROM pending_entity_changes")
            connection.execute("DELETE FROM pending_source_changes")
            self._set_metadata(connection, "pending_fingerprint", "")

    @staticmethod
    def _publish_event(connection: sqlite3.Connection, event: Mapping[str, Any]) -> int | None:
        source_event_id = str(event["source_event_id"])
        seen = connection.execute(
            "SELECT sequence FROM seen_sources WHERE source_event_id = ?", (source_event_id,)
        ).fetchone()
        if seen is not None:
            return None
        high_water = int(ChangeFeedStore._metadata(connection, "high_water"))
        sequence = high_water + 1
        connection.execute(
            """
            INSERT INTO events(
                sequence, source_event_id, occurred_at, action, entity_type,
                entity_id, change_type, tombstone, correlation_ref, idempotency_ref,
                producer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sequence,
                source_event_id,
                event["occurred_at"],
                event["action"],
                event["entity_type"],
                event["entity_id"],
                event["change_type"],
                int(bool(event["tombstone"])),
                event["correlation_ref"],
                event["idempotency_ref"],
                event["producer"],
            ),
        )
        connection.execute(
            "INSERT INTO seen_sources(source_event_id, sequence) VALUES (?, ?)",
            (source_event_id, sequence),
        )
        ChangeFeedStore._set_metadata(connection, "high_water", sequence)
        return sequence

    def commit_state_write(self, state_fingerprint: str) -> int:
        # The matching prepare transaction is FULL-synchronous before state.json is
        # replaced.  Publishing may therefore use WAL/NORMAL: after a power loss the
        # atomic publish either survives, or the durable pending stage survives and is
        # replayed by reconcile_state against the authoritative state fingerprint.
        with self._transaction(immediate=True, durable=False) as connection:
            if self._metadata(connection, "pending_fingerprint") != state_fingerprint:
                raise RuntimeError("Change-feed state fingerprint does not match staged outbox.")
            rows = connection.execute(
                "SELECT * FROM pending_events WHERE state_fingerprint = ? ORDER BY ordinal",
                (state_fingerprint,),
            ).fetchall()
            published = 0
            for row in rows:
                if self._publish_event(connection, row) is not None:
                    published += 1
            self._apply_pending_entity_changes(connection, state_fingerprint)
            self._apply_pending_source_changes(connection)
            connection.execute("DELETE FROM pending_events")
            self._set_metadata(connection, "pending_fingerprint", "")
            return published

    def reconcile_state(
        self, state_fingerprint: str, events: object, *, state: object | None = None
    ) -> dict[str, Any]:
        """Recover an interrupted outbox publish and ingest external state-file writes."""

        fingerprint = _bounded_text(state_fingerprint, limit=128)
        if not fingerprint:
            raise ValueError("state_fingerprint is required")
        with self._transaction(immediate=True) as connection:
            if self._metadata(connection, "initialized") != "1":
                compacted = self._compact_events(events)
                connection.executemany(
                    "INSERT OR IGNORE INTO seen_sources(source_event_id, sequence) VALUES (?, NULL)",
                    ((event["source_event_id"],) for event in compacted),
                )
                self._replace_entity_baseline(connection, project_crm_state(state or {}))
                self._replace_source_baseline(
                    connection, project_crm_source_signatures(state or {})
                )
                self._set_metadata(connection, "initialized", "1")
                return self._status(connection)
            pending_fingerprint = self._metadata(connection, "pending_fingerprint")
            if pending_fingerprint:
                if hmac.compare_digest(pending_fingerprint, fingerprint):
                    rows = connection.execute(
                        "SELECT * FROM pending_events ORDER BY ordinal"
                    ).fetchall()
                    for row in rows:
                        self._publish_event(connection, row)
                    self._apply_pending_entity_changes(connection, pending_fingerprint)
                    self._apply_pending_source_changes(connection)
                connection.execute("DELETE FROM pending_events")
                connection.execute("DELETE FROM pending_entity_changes")
                connection.execute("DELETE FROM pending_source_changes")
                self._set_metadata(connection, "pending_fingerprint", "")
            compacted = self._compact_unseen_events(connection, events)
            connection.execute("DELETE FROM pending_events")
            ordinal, covered = self._stage_unseen_audit_events(
                connection,
                state_fingerprint=fingerprint,
                compacted=compacted,
            )
            if state is not None:
                self._stage_entity_changes(
                    connection,
                    state_fingerprint=fingerprint,
                    state=state,
                    audit_covered_entities=covered,
                    ordinal=ordinal,
                    incremental=False,
                )
            for row in connection.execute(
                "SELECT * FROM pending_events ORDER BY ordinal"
            ).fetchall():
                self._publish_event(connection, row)
            if state is not None:
                self._apply_pending_entity_changes(connection, fingerprint)
                self._apply_pending_source_changes(connection)
            connection.execute("DELETE FROM pending_events")
            return self._status(connection)

    @staticmethod
    def _status(connection: sqlite3.Connection) -> dict[str, Any]:
        return {
            "generation": ChangeFeedStore._metadata(connection, "generation"),
            "high_water": int(ChangeFeedStore._metadata(connection, "high_water")),
        }

    def _secret(self) -> bytes:
        with self._connection() as connection:
            encoded = self._metadata(connection, "cursor_secret")
        try:
            return base64.urlsafe_b64decode(encoded.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("Change-feed cursor secret is corrupted.") from exc

    def _encode_token(self, payload: Mapping[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        signature = (
            base64.urlsafe_b64encode(
                hmac.digest(self._secret(), body.encode("ascii"), hashlib.sha256)[:18]
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        return f"{body}.{signature}"

    def _decode_token(self, token: object, *, kind: str) -> dict[str, Any]:
        raw_token = str(token or "").strip()
        code = "invalid_cursor" if kind == "page" else "invalid_ack"
        if not raw_token or len(raw_token) > CHANGE_FEED_TOKEN_MAX_LENGTH:
            raise ChangeFeedProtocolError(code, f"The {kind} token is invalid.", 400)
        try:
            body, supplied_signature = raw_token.split(".", 1)
            expected_signature = (
                base64.urlsafe_b64encode(
                    hmac.digest(self._secret(), body.encode("ascii"), hashlib.sha256)[:18]
                )
                .rstrip(b"=")
                .decode("ascii")
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise ValueError("signature mismatch")
            padding = "=" * (-len(body) % 4)
            payload = json.loads(base64.urlsafe_b64decode(body + padding).decode("utf-8"))
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ChangeFeedProtocolError(code, f"The {kind} token is invalid.", 400) from exc
        if not isinstance(payload, dict) or payload.get("kind") != kind:
            raise ChangeFeedProtocolError(code, f"The {kind} token is invalid.", 400)
        return payload

    @staticmethod
    def _token_sequence(payload: Mapping[str, Any], key: str, *, code: str) -> int:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ChangeFeedProtocolError(code, "The change-feed token is invalid.", 400)
        return value

    @staticmethod
    def _ensure_consumer(connection: sqlite3.Connection, consumer_id: str) -> int:
        connection.execute(
            "INSERT OR IGNORE INTO consumers(consumer_id, acked_sequence) VALUES (?, 0)",
            (consumer_id,),
        )
        row = connection.execute(
            "SELECT acked_sequence FROM consumers WHERE consumer_id = ?", (consumer_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("Failed to initialize change-feed consumer.")
        return int(row["acked_sequence"])

    def bootstrap(self, consumer_id: object) -> dict[str, Any]:
        consumer = _validated_consumer_id(consumer_id)
        with self._transaction(immediate=True) as connection:
            acked_sequence = self._ensure_consumer(connection, consumer)
            pending = connection.execute(
                "SELECT window_high_water FROM deliveries WHERE consumer_id = ?", (consumer,)
            ).fetchone()
            status = self._status(connection)
            return {
                **status,
                "consumer_id": consumer,
                "acked_sequence": acked_sequence,
                "pending_high_water": (
                    int(pending["window_high_water"]) if pending is not None else None
                ),
                "has_unacked": acked_sequence < int(status["high_water"]),
            }

    @staticmethod
    def _row_event(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "sequence": int(row["sequence"]),
            "event_id": str(row["source_event_id"]),
            "occurred_at": str(row["occurred_at"]),
            "action": str(row["action"]),
            "entity_type": str(row["entity_type"]),
            "entity_id": str(row["entity_id"]),
            "change_type": str(row["change_type"]),
            "tombstone": bool(row["tombstone"]),
            "correlation_ref": str(row["correlation_ref"]),
            "idempotency_ref": str(row["idempotency_ref"]),
            "producer": str(row["producer"]),
        }

    def read_page(
        self,
        consumer_id: object,
        *,
        cursor: object | None = None,
        limit: int = CHANGE_FEED_PAGE_DEFAULT,
    ) -> dict[str, Any]:
        consumer = _validated_consumer_id(consumer_id)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ChangeFeedProtocolError("invalid_limit", "limit must be an integer.", 400)
        if limit < 1 or limit > CHANGE_FEED_PAGE_MAX:
            raise ChangeFeedProtocolError(
                "invalid_limit", f"limit must be between 1 and {CHANGE_FEED_PAGE_MAX}.", 400
            )
        decoded = self._decode_token(cursor, kind="page") if cursor is not None else None
        with self._transaction(immediate=True) as connection:
            generation = self._metadata(connection, "generation")
            acked_sequence = self._ensure_consumer(connection, consumer)
            high_water = int(self._metadata(connection, "high_water"))
            pending = connection.execute(
                "SELECT window_high_water FROM deliveries WHERE consumer_id = ?", (consumer,)
            ).fetchone()
            if decoded is None:
                after_sequence = acked_sequence
                if pending is None and high_water > acked_sequence:
                    connection.execute(
                        "INSERT INTO deliveries(consumer_id, window_high_water) VALUES (?, ?)",
                        (consumer, high_water),
                    )
                    window_high_water = high_water
                elif pending is not None:
                    window_high_water = int(pending["window_high_water"])
                else:
                    window_high_water = acked_sequence
            else:
                token_generation = str(decoded.get("generation") or "")
                if not hmac.compare_digest(token_generation, generation):
                    raise ChangeFeedProtocolError(
                        "stale_generation", "The change-feed generation has changed."
                    )
                if decoded.get("consumer") != consumer:
                    raise ChangeFeedProtocolError(
                        "cursor_consumer_mismatch", "The cursor belongs to another consumer.", 400
                    )
                after_sequence = self._token_sequence(decoded, "after", code="invalid_cursor")
                window_high_water = self._token_sequence(decoded, "window", code="invalid_cursor")
                limit = self._token_sequence(decoded, "limit", code="invalid_cursor")
                if limit < 1 or limit > CHANGE_FEED_PAGE_MAX:
                    raise ChangeFeedProtocolError(
                        "invalid_cursor", "The cursor page size is invalid.", 400
                    )
                if pending is None or int(pending["window_high_water"]) != window_high_water:
                    raise ChangeFeedProtocolError(
                        "stale_cursor",
                        "The delivery represented by this cursor is no longer active.",
                    )
                if after_sequence < acked_sequence:
                    raise ChangeFeedProtocolError(
                        "stale_cursor", "The cursor points behind the acknowledged sequence."
                    )
                if after_sequence > window_high_water:
                    raise ChangeFeedProtocolError(
                        "invalid_cursor", "The cursor points beyond its delivery window.", 400
                    )
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE sequence > ? AND sequence <= ?
                ORDER BY sequence
                LIMIT ?
                """,
                (after_sequence, window_high_water, limit),
            ).fetchall()
            if after_sequence < window_high_water:
                expected = after_sequence + 1
                if not rows or int(rows[0]["sequence"]) != expected:
                    raise ChangeFeedProtocolError(
                        "feed_gap", "The durable change feed contains a sequence gap.", 503
                    )
            events = [self._row_event(row) for row in rows]
            through_sequence = events[-1]["sequence"] if events else after_sequence
            caught_up = through_sequence >= window_high_water
            next_cursor = None
            replay_cursor = None
            ack_token = None
            if events:
                replay_cursor = self._encode_token(
                    {
                        "kind": "page",
                        "generation": generation,
                        "consumer": consumer,
                        "after": after_sequence,
                        "window": window_high_water,
                        "limit": limit,
                    }
                )
                ack_token = self._encode_token(
                    {
                        "kind": "ack",
                        "generation": generation,
                        "consumer": consumer,
                        "start": events[0]["sequence"],
                        "through": through_sequence,
                        "window": window_high_water,
                    }
                )
                if not caught_up:
                    next_cursor = self._encode_token(
                        {
                            "kind": "page",
                            "generation": generation,
                            "consumer": consumer,
                            "after": through_sequence,
                            "window": window_high_water,
                            "limit": limit,
                        }
                    )
            return {
                "generation": generation,
                "consumer_id": consumer,
                "high_water": window_high_water,
                "delivery_high_water": window_high_water,
                "acked_sequence": acked_sequence,
                "from_sequence": events[0]["sequence"] if events else None,
                "through_sequence": through_sequence if events else None,
                "events": events,
                "replay_cursor": replay_cursor,
                "next_cursor": next_cursor,
                "ack": ack_token,
                "caught_up": caught_up,
            }

    def acknowledge(self, consumer_id: object, ack_token: object) -> dict[str, Any]:
        consumer = _validated_consumer_id(consumer_id)
        decoded = self._decode_token(ack_token, kind="ack")
        with self._transaction(immediate=True) as connection:
            generation = self._metadata(connection, "generation")
            token_generation = str(decoded.get("generation") or "")
            if not hmac.compare_digest(token_generation, generation):
                raise ChangeFeedProtocolError(
                    "stale_generation", "The change-feed generation has changed."
                )
            if decoded.get("consumer") != consumer:
                raise ChangeFeedProtocolError(
                    "ack_consumer_mismatch", "The ACK belongs to another consumer.", 400
                )
            start = self._token_sequence(decoded, "start", code="invalid_ack")
            through = self._token_sequence(decoded, "through", code="invalid_ack")
            window = self._token_sequence(decoded, "window", code="invalid_ack")
            if start < 1 or through < start or through > window:
                raise ChangeFeedProtocolError("invalid_ack", "The ACK range is invalid.", 400)
            acked_sequence = self._ensure_consumer(connection, consumer)
            high_water = int(self._metadata(connection, "high_water"))
            if through <= acked_sequence:
                return {
                    "generation": generation,
                    "consumer_id": consumer,
                    "high_water": high_water,
                    "acked_sequence": acked_sequence,
                    "changed": False,
                    "delivery_complete": acked_sequence >= window,
                }
            if start != acked_sequence + 1:
                raise ChangeFeedProtocolError(
                    "ack_out_of_order", "ACKs must advance the feed without gaps."
                )
            pending = connection.execute(
                "SELECT window_high_water FROM deliveries WHERE consumer_id = ?", (consumer,)
            ).fetchone()
            if pending is None or int(pending["window_high_water"]) != window:
                raise ChangeFeedProtocolError(
                    "stale_ack", "The delivery represented by this ACK is no longer active."
                )
            count = connection.execute(
                "SELECT COUNT(*) AS total FROM events WHERE sequence BETWEEN ? AND ?",
                (start, through),
            ).fetchone()
            if count is None or int(count["total"]) != through - start + 1:
                raise ChangeFeedProtocolError(
                    "feed_gap", "The durable change feed contains a sequence gap.", 503
                )
            connection.execute(
                "UPDATE consumers SET acked_sequence = ? WHERE consumer_id = ?",
                (through, consumer),
            )
            delivery_complete = through == window
            if delivery_complete:
                connection.execute("DELETE FROM deliveries WHERE consumer_id = ?", (consumer,))
            return {
                "generation": generation,
                "consumer_id": consumer,
                "high_water": high_water,
                "acked_sequence": through,
                "changed": True,
                "delivery_complete": delivery_complete,
            }

    def rotate_generation(self) -> str:
        """Invalidate all cursors while retaining the complete ordered event log for replay."""

        generation = str(uuid4())
        with self._transaction(immediate=True) as connection:
            self._set_metadata(connection, "generation", generation)
            connection.execute("DELETE FROM deliveries")
            connection.execute("DELETE FROM consumers")
        return generation

    def raw_events_for_test(self) -> list[dict[str, Any]]:
        """Return compact rows for storage contract tests; never exposes audit details."""

        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
        return [self._row_event(row) for row in rows]

    def iter_source_ids_for_test(self) -> Iterable[str]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT source_event_id FROM seen_sources ORDER BY source_event_id"
            ).fetchall()
        return (str(row["source_event_id"]) for row in rows)
