from __future__ import annotations

import json
import shutil
import time
import uuid
from datetime import timedelta
from logging import Logger
from pathlib import Path
from typing import Any

from .config import get_operator_activity_dir
from .models import normalize_actor_name, normalize_text, parse_datetime, utc_now, utc_now_iso
from .services.card_service import ServiceError
from .storage.file_lock import ProcessFileLock

OPERATOR_ACTIVITY_SCHEMA_VERSION = 1
DEFAULT_ACTIVITY_PAGE_SIZE = 100
MAX_ACTIVITY_PAGE_SIZE = 500
DEFAULT_DETAIL_RETENTION_DAYS = 90
DEFAULT_AGGREGATE_RETENTION_MONTHS = 24

MODULE_LABELS = {
    "auth": "Вход",
    "card": "Карточки",
    "board": "Доска",
    "client": "Клиенты",
    "vehicle": "Автомобили",
    "repair_order": "Заказ-наряд",
    "cashbox": "Касса",
    "employee": "Сотрудники",
    "payroll": "Зарплата",
    "file": "Файлы",
    "admin": "Админ",
    "agent": "Агент",
}

ACTION_LABELS = {
    "login": "Вошел в систему",
    "logout": "Вышел из системы",
    "card_opened": "Открыл карточку",
    "repair_order_updated": "Обновил работы",
    "cash_transaction_created": "Кассовая операция",
}


class OperatorActivityService:
    def __init__(self, *, activity_dir: Path | None = None, logger: Logger | None = None) -> None:
        self._activity_dir = activity_dir or get_operator_activity_dir()
        self._current_dir = self._activity_dir / "current"
        self._details_dir = self._activity_dir / "details"
        self._aggregates_dir = self._activity_dir / "aggregates"
        self._logger = logger
        self._lock = ProcessFileLock(self._activity_dir / ".operator-activity.lock")

    @property
    def activity_dir(self) -> Path:
        return self._activity_dir

    def record_activity(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        row = self._compact_row(payload)
        details = payload.get("details")
        with self._lock.acquire():
            self._current_dir.mkdir(parents=True, exist_ok=True)
            self._details_dir.mkdir(parents=True, exist_ok=True)
            if isinstance(details, dict) and details:
                details_ref = self._append_details(row, details)
                row["details_ref"] = details_ref
            self._append_jsonl(self._current_file(row["timestamp"]), row)
        return {"activity": dict(row)}

    def list_activity(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        offset = _non_negative_int(payload.get("offset"), default=0)
        limit = min(
            MAX_ACTIVITY_PAGE_SIZE,
            max(1, _non_negative_int(payload.get("limit"), default=DEFAULT_ACTIVITY_PAGE_SIZE)),
        )
        rows = [row for row in self._read_current_rows() if self._row_matches(row, payload)]
        rows.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        page = rows[offset : offset + limit]
        return {
            "activities": page,
            "meta": {
                "total": len(rows),
                "offset": offset,
                "limit": limit,
                "has_more": offset + limit < len(rows),
            },
        }

    def get_activity_details(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        activity_id = normalize_text(
            payload.get("activity_id") or payload.get("id"), default="", limit=80
        )
        details_ref = normalize_text(payload.get("details_ref"), default="", limit=160)
        if not activity_id and not details_ref:
            self._fail(
                "validation_error",
                "Нужно передать activity_id или details_ref.",
                details={"field": "activity_id"},
            )
        activity = self._find_activity(activity_id=activity_id, details_ref=details_ref)
        if activity is None:
            self._fail("not_found", "Событие журнала не найдено.", status_code=404)
        resolved_ref = details_ref or str(activity.get("details_ref") or "")
        return {
            "activity": activity,
            "details": self._load_details(resolved_ref, activity_id=activity.get("id")) or {},
        }

    def get_activity_aggregates(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        rows = [row for row in self._read_current_rows() if self._row_matches(row, payload)]
        by_user: dict[str, int] = self._aggregate_counts("by_user", payload)
        by_module: dict[str, int] = self._aggregate_counts("by_module", payload)
        by_action: dict[str, int] = self._aggregate_counts("by_action", payload)
        by_source: dict[str, int] = self._aggregate_counts("by_source", payload)
        for row in rows:
            _increment(by_user, str(row.get("username") or ""))
            _increment(by_module, str(row.get("module") or ""))
            _increment(by_action, str(row.get("action") or ""))
            _increment(by_source, str(row.get("source") or ""))
        return {
            "by_user": by_user,
            "by_module": by_module,
            "by_action": by_action,
            "by_source": by_source,
            "meta": {"source": "current_and_aggregates", "total": sum(by_user.values())},
        }

    def compact_activity(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        apply = bool(payload.get("apply"))
        dry_run = bool(payload.get("dry_run") or not apply)
        backup = bool(payload.get("backup"))
        retention_days = max(
            1,
            _non_negative_int(payload.get("retention_days"), default=DEFAULT_DETAIL_RETENTION_DAYS),
        )
        if apply and not backup:
            self._fail("validation_error", "--apply требует backup.", details={"field": "backup"})

        def build_result(*, rows: list[dict[str, Any]], write: bool) -> dict:
            cutoff = utc_now() - timedelta(days=retention_days)
            old_rows: list[dict[str, Any]] = []
            recent_rows: list[dict[str, Any]] = []
            for row in rows:
                timestamp = parse_datetime(row.get("timestamp"))
                if timestamp is not None and timestamp < cutoff:
                    old_rows.append(row)
                else:
                    recent_rows.append(row)
            backup_dir = ""
            if write:
                backup_dir = self._backup_activity_dir()
                self._write_aggregates(old_rows)
                self._rewrite_current_rows(recent_rows)
                self._rewrite_details(recent_rows)
            return {
                "dry_run": not write,
                "retention_days": retention_days,
                "current_rows_total": len(rows),
                "eligible_rows": len(old_rows),
                "removed_rows": len(old_rows) if write else 0,
                "remaining_rows": len(recent_rows) if write else len(rows),
                "backup_dir": backup_dir,
            }

        if dry_run:
            return build_result(rows=self._read_current_rows(), write=False)
        with self._lock.acquire():
            return build_result(rows=self._read_current_rows(), write=True)

    def export_activity(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        rows = self.list_activity({**payload, "limit": MAX_ACTIVITY_PAGE_SIZE})["activities"]
        username = _normalized_username(payload.get("username"))
        lines = [
            "ЖУРНАЛ ДЕЙСТВИЙ ОПЕРАТОРОВ",
            f"Пользователь: {username or 'ВСЕ'}",
            f"Строк: {len(rows)}",
            "",
        ]
        for row in rows:
            amount = str(row.get("amount") or "").strip()
            amount_suffix = f" | сумма: {amount}" if amount else ""
            lines.append(
                " | ".join(
                    [
                        str(row.get("timestamp") or "-"),
                        str(row.get("username") or "-"),
                        self._module_label(str(row.get("module") or "")),
                        str(row.get("action_label") or row.get("action") or "-"),
                        str(row.get("object_label") or "-"),
                        str(row.get("summary") or "-"),
                        str(row.get("source") or "-"),
                    ]
                )
                + amount_suffix
            )
        file_name = "operator-activity"
        if username:
            file_name += f"-{username.lower()}"
        return {"file_name": f"{file_name}.txt", "text": "\n".join(lines) + "\n"}

    def _compact_row(self, payload: dict[str, Any]) -> dict[str, Any]:
        timestamp = _normalized_timestamp(payload.get("timestamp"))
        action = _normalized_slug(payload.get("action"), default="operator_action", limit=80)
        module = _normalized_slug(payload.get("module"), default="activity", limit=40)
        row = {
            "schema_version": OPERATOR_ACTIVITY_SCHEMA_VERSION,
            "id": normalize_text(payload.get("id"), default="", limit=80) or uuid.uuid4().hex,
            "timestamp": timestamp,
            "username": _normalized_username(payload.get("username")),
            "module": module,
            "action": action,
            "action_label": normalize_text(
                payload.get("action_label") or ACTION_LABELS.get(action),
                default=action,
                limit=80,
            ),
            "object_type": _normalized_slug(payload.get("object_type"), default="", limit=60),
            "object_id": normalize_text(payload.get("object_id"), default="", limit=120),
            "object_label": normalize_text(payload.get("object_label"), default="", limit=220),
            "summary": normalize_text(payload.get("summary"), default="", limit=500),
            "amount": normalize_text(payload.get("amount"), default="", limit=40),
            "source": _normalized_slug(payload.get("source"), default="api", limit=32),
            "severity": _normalized_slug(payload.get("severity"), default="normal", limit=24),
            "details_ref": "",
        }
        if not row["username"]:
            self._fail(
                "validation_error", "Нужно указать пользователя.", details={"field": "username"}
            )
        return row

    def _append_details(self, row: dict[str, Any], details: dict[str, Any]) -> str:
        record = {
            "schema_version": OPERATOR_ACTIVITY_SCHEMA_VERSION,
            "activity_id": row["id"],
            "timestamp": row["timestamp"],
            "details": details,
        }
        details_file = self._details_file(row["timestamp"])
        self._append_jsonl(details_file, record)
        return f"{details_file.name}#{row['id']}"

    def _read_current_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self._current_dir.exists():
            return rows
        for path in sorted(self._current_dir.glob("*.jsonl")):
            rows.extend(self._read_jsonl(path))
        return rows

    def _aggregate_counts(self, key: str, payload: dict[str, Any] | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        if not self._aggregates_dir.exists():
            return counts
        for path in sorted(self._aggregates_dir.glob("*.json")):
            try:
                aggregate = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                if self._logger is not None:
                    self._logger.warning(
                        "operator_activity_aggregate_read_failed path=%s error=%s", path, exc
                    )
                continue
            for count_key, count in self._aggregate_counts_from_payload(
                aggregate, key, payload or {}
            ).items():
                counts[count_key] = counts.get(count_key, 0) + count
        return counts

    def _aggregate_counts_from_payload(
        self, aggregate: dict[str, Any], key: str, payload: dict[str, Any]
    ) -> dict[str, int]:
        buckets = _clean_count_mapping(aggregate.get("buckets"))
        if buckets:
            return self._aggregate_bucket_counts(buckets, key, payload)
        username = _normalized_username(payload.get("username"))
        if username and key == "by_user":
            return {username: _clean_count_mapping(aggregate.get(key)).get(username, 0)}
        if username:
            nested_key = {
                "by_module": "by_user_module",
                "by_action": "by_user_action",
                "by_source": "by_user_source",
            }.get(key)
            nested = aggregate.get(nested_key or "")
            if isinstance(nested, dict):
                return _clean_count_mapping(nested.get(username))
        return _clean_count_mapping(aggregate.get(key))

    def _aggregate_bucket_counts(
        self, buckets: dict[str, int], key: str, payload: dict[str, Any]
    ) -> dict[str, int]:
        dimension_index = {"by_user": 0, "by_module": 1, "by_action": 2, "by_source": 3}.get(key)
        if dimension_index is None:
            return {}
        filters = (
            _normalized_username(payload.get("username")),
            _normalized_slug(payload.get("module"), default="", limit=80),
            _normalized_slug(payload.get("action"), default="", limit=80),
            _normalized_slug(payload.get("source"), default="", limit=80),
        )
        counts: dict[str, int] = {}
        for bucket, count in buckets.items():
            parts = str(bucket).split("|", maxsplit=3)
            if len(parts) != 4:
                continue
            if any(expected and parts[index] != expected for index, expected in enumerate(filters)):
                continue
            _increment_by(counts, parts[dimension_index], count)
        return counts

    def _write_aggregates(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self._aggregates_dir.mkdir(parents=True, exist_ok=True)
        by_month: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_month.setdefault(_month_from_timestamp(str(row.get("timestamp") or "")), []).append(
                row
            )
        for month, month_rows in by_month.items():
            path = self._aggregates_dir / f"{month}.json"
            aggregate = self._read_aggregate_file(path, month)
            for row in month_rows:
                self._add_row_to_aggregate(aggregate, row)
            path.write_text(
                json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    def _read_aggregate_file(self, path: Path, month: str) -> dict[str, Any]:
        if not path.exists():
            return self._normalized_aggregate_payload({}, month)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            if self._logger is not None:
                self._logger.warning(
                    "operator_activity_aggregate_read_failed path=%s error=%s", path, exc
                )
            raw = {}
        return self._normalized_aggregate_payload(raw if isinstance(raw, dict) else {}, month)

    def _normalized_aggregate_payload(self, payload: dict[str, Any], month: str) -> dict[str, Any]:
        return {
            "schema_version": OPERATOR_ACTIVITY_SCHEMA_VERSION,
            "month": normalize_text(payload.get("month"), default=month, limit=16) or month,
            "rows_total": _non_negative_int(payload.get("rows_total"), default=0),
            "by_user": _clean_count_mapping(payload.get("by_user")),
            "by_day": _clean_count_mapping(payload.get("by_day")),
            "by_module": _clean_count_mapping(payload.get("by_module")),
            "by_action": _clean_count_mapping(payload.get("by_action")),
            "by_source": _clean_count_mapping(payload.get("by_source")),
            "buckets": _clean_count_mapping(payload.get("buckets")),
        }

    def _add_row_to_aggregate(self, aggregate: dict[str, Any], row: dict[str, Any]) -> None:
        aggregate["rows_total"] = _non_negative_int(aggregate.get("rows_total"), default=0) + 1
        username = str(row.get("username") or "-")
        module = str(row.get("module") or "-")
        action = str(row.get("action") or "-")
        source = str(row.get("source") or "-")
        timestamp = str(row.get("timestamp") or "")
        day = timestamp[:10] if len(timestamp) >= 10 else "unknown"
        _increment(aggregate["by_user"], username)
        _increment(aggregate["by_day"], day)
        _increment(aggregate["by_module"], module)
        _increment(aggregate["by_action"], action)
        _increment(aggregate["by_source"], source)
        _increment(aggregate["buckets"], "|".join((username, module, action, source)))

    def _rewrite_current_rows(self, rows: list[dict[str, Any]]) -> None:
        self._current_dir.mkdir(parents=True, exist_ok=True)
        for path in self._current_dir.glob("*.jsonl"):
            path.unlink(missing_ok=True)
        for row in sorted(rows, key=lambda item: str(item.get("timestamp") or "")):
            self._append_jsonl(self._current_file(str(row.get("timestamp") or "")), row)

    def _rewrite_details(self, current_rows: list[dict[str, Any]]) -> None:
        if not self._details_dir.exists():
            return
        current_ids = {str(row.get("id") or "") for row in current_rows}
        retained_records: list[dict[str, Any]] = []
        for path in sorted(self._details_dir.glob("*.jsonl")):
            for record in self._read_jsonl(path):
                if str(record.get("activity_id") or "") in current_ids:
                    retained_records.append(record)
            path.unlink(missing_ok=True)
        for record in sorted(retained_records, key=lambda item: str(item.get("timestamp") or "")):
            self._append_jsonl(self._details_file(str(record.get("timestamp") or "")), record)

    def _backup_activity_dir(self) -> str:
        if not self._activity_dir.exists():
            return ""
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = self._activity_dir.parent / f"{self._activity_dir.name}.backup-{timestamp}"
        if backup_dir.exists():
            backup_dir = backup_dir.with_name(f"{backup_dir.name}-{uuid.uuid4().hex[:8]}")
        shutil.copytree(
            self._activity_dir,
            backup_dir,
            ignore=shutil.ignore_patterns(".operator-activity.lock"),
        )
        return str(backup_dir)

    def _row_matches(self, row: dict[str, Any], payload: dict[str, Any]) -> bool:
        username = _normalized_username(payload.get("username"))
        if username and str(row.get("username") or "") != username:
            return False
        for field in ("module", "action", "source"):
            expected = _normalized_slug(payload.get(field), default="", limit=80)
            if expected and str(row.get(field) or "") != expected:
                return False
        if not self._row_in_period(row, payload):
            return False
        query = str(payload.get("query") or payload.get("search") or "").strip().lower()
        if query and query not in self._row_search_text(row):
            return False
        return True

    def _row_in_period(self, row: dict[str, Any], payload: dict[str, Any]) -> bool:
        timestamp = parse_datetime(row.get("timestamp"))
        if timestamp is None:
            return False
        date_from = parse_datetime(
            payload.get("date_from") or payload.get("from") or payload.get("start")
        )
        date_to = parse_datetime(payload.get("date_to") or payload.get("to") or payload.get("end"))
        days = _non_negative_int(payload.get("days"), default=0)
        if days > 0 and timestamp < utc_now() - timedelta(days=days):
            return False
        if date_from and timestamp < date_from:
            return False
        if date_to and timestamp > date_to:
            return False
        return True

    def _find_activity(
        self, *, activity_id: str = "", details_ref: str = ""
    ) -> dict[str, Any] | None:
        for row in self._read_current_rows():
            if activity_id and row.get("id") == activity_id:
                return row
            if details_ref and row.get("details_ref") == details_ref:
                return row
        return None

    def _load_details(self, ref: str, *, activity_id: str | None = None) -> dict[str, Any] | None:
        file_part, sep, ref_activity_id = str(ref or "").partition("#")
        if not file_part:
            return None
        expected_activity_id = activity_id or (ref_activity_id if sep else "")
        path = self._details_dir / Path(file_part.replace("\\", "/")).name
        if not path.exists():
            return None
        for record in self._read_jsonl(path):
            if expected_activity_id and record.get("activity_id") != expected_activity_id:
                continue
            details = record.get("details")
            return details if isinstance(details, dict) else None
        return None

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        if self._logger is not None:
                            self._logger.warning("operator_activity_bad_json path=%s", path)
                        continue
                    if isinstance(payload, dict):
                        rows.append(payload)
        except OSError as exc:
            if self._logger is not None:
                self._logger.warning("operator_activity_read_failed path=%s error=%s", path, exc)
        return rows

    def _current_file(self, timestamp: str) -> Path:
        return self._current_dir / f"{_month_from_timestamp(timestamp)}.jsonl"

    def _details_file(self, timestamp: str) -> Path:
        return self._details_dir / f"{_month_from_timestamp(timestamp)}.jsonl"

    def _module_label(self, module: str) -> str:
        return MODULE_LABELS.get(module, module or "-")

    def _row_search_text(self, row: dict[str, Any]) -> str:
        fields = (
            "timestamp",
            "username",
            "module",
            "action",
            "action_label",
            "object_type",
            "object_id",
            "object_label",
            "summary",
            "amount",
            "source",
        )
        return " ".join(str(row.get(field) or "") for field in fields).lower()

    def _fail(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        raise ServiceError(code, message, status_code=status_code, details=details)


def _normalized_timestamp(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed.isoformat()
    return utc_now_iso()


def _normalized_username(value: Any) -> str:
    return normalize_actor_name(value, default="").upper()


def _normalized_slug(value: Any, *, default: str, limit: int) -> str:
    raw = normalize_text(value, default=default, limit=limit).strip().lower()
    return raw.replace(" ", "_")


def _month_from_timestamp(timestamp: str) -> str:
    raw = str(timestamp or "").strip()
    if len(raw) >= 7 and raw[4:5] == "-" and raw[7:8] in {"", "-", "T"}:
        return raw[:7]
    return "unknown"


def _non_negative_int(value: Any, *, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, number)


def _increment(target: dict[str, int], key: str) -> None:
    normalized = key or "-"
    target[normalized] = target.get(normalized, 0) + 1


def _increment_by(target: dict[str, int], key: str, count: int) -> None:
    normalized = key or "-"
    target[normalized] = target.get(normalized, 0) + max(0, count)


def _clean_count_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for raw_key, raw_count in value.items():
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        counts[str(raw_key)] = max(0, count)
    return counts
