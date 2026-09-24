from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ..models import (
    Card,
    business_timezone,
    normalize_actor_name,
    normalize_int,
    normalize_text,
    parse_datetime,
    short_entity_id,
    utc_now,
)
from .journal_labels import day_label, month_label, week_label

CARD_JOURNAL_COMPACT_DEFAULT_LIMIT = 50
CARD_JOURNAL_COMPACT_TEXT_LIMIT = 1200
CARD_JOURNAL_COUNT_MAX = 1_000_000_000
CARD_JOURNAL_ACTION_LABELS = {
    "card_created": "Создана карточка",
    "card_moved": "Перемещена карточка",
    "card_archived": "Карточка отправлена в архив",
    "card_restored": "Карточка восстановлена",
    "title_changed": "Изменён заголовок",
    "vehicle_changed": "Изменён автомобиль",
    "description_changed": "Изменено описание",
    "board_summary_changed": "Обновлена краткая суть для доски",
    "signal_changed": "Изменён срок/сигнал",
    "signal_indicator_changed": "Изменён индикатор",
    "timer_started": "Запущен таймер",
    "timer_restarted": "Перезапущен таймер",
    "timer_stopped": "Остановлен таймер",
    "tag_added": "Добавлена метка",
    "tag_removed": "Удалена метка",
    "tag_color_changed": "Изменён цвет метки",
    "tags_changed": "Изменены метки",
    "attachment_added": "Добавлен файл",
    "attachment_removed": "Удалён файл",
    "vehicle_profile_updated": "Обновлена техкарта",
    "repair_order_updated": "Обновлён заказ-наряд",
    "repair_order_autofilled": "Автозаполнен заказ-наряд",
    "repair_order_vehicle_fields_synced": "Синхронизированы данные заказ-наряда",
    "ready_column_synchronized": "Синхронизирован столбец готовности",
    "cash_transaction_deleted": "Удалено движение кассы",
    "cash_transaction_created": "Добавлено движение кассы",
    "card_client_linked": "Привязан клиент",
    "card_client_unlinked": "Клиент отвязан",
    "card_client_vehicle_synced": "Синхронизирован автомобиль клиента",
    "card_client_vehicle_unlinked": "Автомобиль клиента отвязан",
    "card_cleanup_applied": "Выполнена очистка карточки",
    "card_auto_cleanup_blocked": "Автоочистка карточки заблокирована",
    "card_full_enrichment_requested": "Запрошено полное обогащение карточки",
}
CARD_JOURNAL_ACTION_ICONS = {
    "card_created": "✨",
    "card_moved": "📍",
    "card_archived": "📦",
    "card_restored": "↩️",
    "title_changed": "✏️",
    "vehicle_changed": "🚗",
    "description_changed": "📝",
    "board_summary_changed": "🧭",
    "signal_changed": "⏰",
    "signal_indicator_changed": "🚦",
    "timer_started": "▶️",
    "timer_restarted": "🔄",
    "timer_stopped": "⏹️",
    "tag_added": "🏷️",
    "tag_removed": "🏷️",
    "tag_color_changed": "🎨",
    "tags_changed": "🏷️",
    "attachment_added": "📎",
    "attachment_removed": "🗑️",
    "vehicle_profile_updated": "🧾",
    "repair_order_updated": "🛠️",
    "repair_order_autofilled": "🛠️",
    "repair_order_vehicle_fields_synced": "🛠️",
    "ready_column_synchronized": "✅",
    "cash_transaction_deleted": "💸",
    "cash_transaction_created": "💸",
    "card_client_linked": "👤",
    "card_client_unlinked": "👤",
    "card_client_vehicle_synced": "🚗",
    "card_client_vehicle_unlinked": "🚗",
    "card_cleanup_applied": "🧹",
    "card_auto_cleanup_blocked": "⚠️",
    "card_full_enrichment_requested": "🤖",
}
CARD_JOURNAL_FIELD_LABELS = {
    "vehicle": "Автомобиль",
    "title": "Заголовок",
    "description": "Описание",
    "board_summary": "Краткая суть для доски",
    "column": "Столбец",
    "deadline": "Срок/сигнал",
    "indicator": "Индикатор",
    "timer": "Таймер",
    "tags": "Метки",
    "tag": "Метка",
    "tag_color": "Цвет метки",
    "attachment": "Файл",
    "vehicle_profile": "Техкарта автомобиля",
    "repair_order": "Заказ-наряд",
    "cash_transaction": "Движение кассы",
    "client": "Клиент",
    "client_vehicle": "Автомобиль клиента",
    "ready_state": "Готовность",
}
CARD_JOURNAL_SOURCE_LABELS = {
    "ui": "интерфейс",
    "api": "API",
    "mcp": "MCP/GPT",
    "system": "система",
}
CARD_JOURNAL_FIELD_ACTION_LABELS = {
    "vehicle": "Автомобиль",
    "title": "Заголовок",
    "description": "Описание",
    "board_summary": "Краткую суть для доски",
    "column": "Столбец",
    "deadline": "Срок/сигнал",
    "indicator": "Индикатор",
    "timer": "Таймер",
    "tags": "Метки",
    "tag": "Метку",
    "tag_color": "Цвет метки",
    "attachment": "Файл",
    "vehicle_profile": "Техкарту автомобиля",
    "repair_order": "Заказ-наряд",
    "cash_transaction": "Движение кассы",
    "client": "Клиента",
    "client_vehicle": "Автомобиль клиента",
    "ready_state": "Готовность",
}
CARD_JOURNAL_VEHICLE_PROFILE_LABELS = {
    "make_display": "Марка",
    "model_display": "Модель",
    "generation_or_platform": "Поколение/платформа",
    "production_year": "Год",
    "vin": "VIN",
    "registration_plate": "Госномер",
    "mileage": "Пробег",
    "customer_name": "Клиент",
    "customer_phone": "Телефон",
    "engine_model": "Двигатель",
    "engine_code": "Код двигателя",
    "engine_displacement_l": "Объём двигателя",
    "engine_power_hp": "Мощность",
    "fuel_type": "Топливо",
    "gearbox_type": "Коробка",
    "gearbox_model": "Модель КПП",
    "drivetrain": "Привод",
    "oil_engine_capacity_l": "Масло двигателя",
    "oil_gearbox_capacity_l": "Масло КПП",
    "coolant_capacity_l": "Охлаждающая жидкость",
    "brake_front_type": "Передние тормоза",
    "brake_rear_type": "Задние тормоза",
    "steering_system_type": "Рулевое управление",
    "sts_series": "СТС серия",
    "sts_number": "СТС номер",
    "pts_series": "ПТС серия",
    "pts_number": "ПТС номер",
    "body_number": "Номер кузова",
    "chassis_number": "Номер шасси",
    "wheel_bolt_pattern": "Разболтовка",
    "oem_notes": "OEM-заметки",
}
CARD_JOURNAL_REPAIR_ORDER_LABELS = {
    "number": "Номер",
    "status": "Статус",
    "date": "Дата",
    "opened_at": "Открыт",
    "closed_at": "Закрыт",
    "client": "Клиент",
    "phone": "Телефон",
    "vehicle": "Автомобиль",
    "license_plate": "Госномер",
    "vin": "VIN",
    "mileage": "Пробег",
    "reason": "Причина обращения",
    "comment": "Комментарий",
    "note": "Заметка",
    "prepayment": "Предоплата",
    "payment_method": "Оплата",
}
CARD_JOURNAL_GENERIC_DETAIL_LABELS = {
    "before": "Было",
    "after": "Стало",
    "before_column": "Было в столбце",
    "after_column": "Стало в столбце",
    "column": "Столбец",
    "deadline_total_seconds": "Срок/сигнал",
    "before_total_seconds": "Срок был",
    "after_total_seconds": "Срок стал",
    "before_indicator": "Индикатор был",
    "after_indicator": "Индикатор стал",
    "file_name": "Файл",
    "tag": "Метка",
    "actor_name": "Оператор",
    "source": "Источник",
}
CARD_JOURNAL_COLUMN_VALUE_LABELS = {
    "inbox": "Входящие",
    "in_progress": "В работе",
    "control": "На контроле",
    "done": "Готовые автомобили",
    "priemka": "ПРИЁМКА",
    "diagnostics": "ДИАГНОСТИКА",
    "electrics": "ЭЛЕКТРИКИ",
    "mechanics": "СЛЕСАРЯ",
    "body": "КУЗОВ",
    "machining": "ТОКАРКА",
    "parts": "ЗАПЧАСТИ",
    "approval": "СОГЛАСОВАНИЕ",
    "delivery": "ВЫДАЧА",
}
CARD_JOURNAL_PAYMENT_METHOD_LABELS = {
    "cash": "наличные",
    "cashless": "безналичный расчёт",
    "card": "карта",
    "bank_card": "карта",
    "transfer": "перевод",
    "mixed": "смешанная оплата",
}


class CardLogProjection:
    def __init__(self, *, json_dumps: Callable[..., str]) -> None:
        self._json_dumps = json_dumps

    def _compact_card_log_text(
        self, value: Any, *, limit: int = CARD_JOURNAL_COMPACT_TEXT_LIMIT
    ) -> tuple[str, bool]:
        text = self._card_log_full_value_text(value).replace("\r", "").strip()
        if len(text) <= limit:
            return text, False
        kept = max(0, limit - 3)
        return text[:kept].rstrip() + "...", True

    def _compact_card_log_block(self, block: dict[str, Any]) -> dict[str, Any]:
        compact = {
            key: value
            for key, value in block.items()
            if key
            in {
                "schema_version",
                "kind",
                "field",
                "label",
                "title",
                "is_full_value",
                "is_empty",
                "change_kind",
            }
        }
        text, truncated = self._compact_card_log_text(block.get("text"))
        compact["text"] = text
        if truncated:
            compact["is_truncated"] = True
        return compact

    def _compact_card_log_change(self, change: dict[str, Any]) -> dict[str, Any]:
        before_text, before_truncated = self._compact_card_log_text(
            change.get("before_summary") or change.get("before"), limit=240
        )
        after_text, after_truncated = self._compact_card_log_text(
            change.get("after_summary") or change.get("after"), limit=240
        )
        compact = {
            key: value
            for key, value in change.items()
            if key
            in {
                "schema_version",
                "field",
                "label",
                "kind",
                "human_kind",
            }
        }
        compact.update(
            {
                "before": before_text,
                "after": after_text,
                "before_human": before_text,
                "after_human": after_text,
                "before_summary": before_text,
                "after_summary": after_text,
            }
        )
        if before_truncated or after_truncated:
            compact["is_truncated"] = True
        return compact

    def _compact_card_log_groups(self, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in group.items() if key != "entries"} for group in groups
        ]

    def _compact_card_log_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact_entries: list[dict[str, Any]] = []
        for entry in entries:
            journal_blocks = [
                self._compact_card_log_block(block)
                for block in entry.get("journal_blocks", [])
                if isinstance(block, dict)
            ]
            detail_lines = self._card_log_blocks_to_detail_lines(journal_blocks)
            compact_entry = {
                key: value
                for key, value in entry.items()
                if key
                in {
                    "schema_version",
                    "id",
                    "timestamp",
                    "business_timestamp",
                    "date",
                    "time",
                    "time_short",
                    "day_key",
                    "week_key",
                    "month_key",
                    "actor_name",
                    "display_actor_name",
                    "source",
                    "source_label",
                    "action",
                    "action_label",
                    "icon",
                    "message",
                    "human_message",
                    "card_id",
                    "card_short_id",
                    "card_heading",
                    "change_count",
                    "has_deletion",
                    "display_line",
                    "summary",
                }
            }
            compact_entry.update(
                {
                    "journal_blocks": journal_blocks,
                    "changes": [
                        self._compact_card_log_change(change)
                        for change in entry.get("changes", [])
                        if isinstance(change, dict)
                    ],
                }
            )
            if detail_lines and not journal_blocks:
                compact_entry["detail_lines"] = detail_lines
            compact_entries.append(compact_entry)
        return compact_entries

    def _card_log_full_value_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "да" if value else "нет"
        if isinstance(value, (dict, list)):
            return self._json_dumps(value, sort_keys=True, indent=2)
        return str(value)

    def _card_log_count_value(self, value: Any) -> int:
        return normalize_int(value, default=0, maximum=CARD_JOURNAL_COUNT_MAX)

    def _card_log_plural_ru(self, count: int, one: str, few: str, many: str) -> str:
        count_value = self._card_log_count_value(count)
        value = abs(count_value)
        if value % 100 in {11, 12, 13, 14}:
            word = many
        elif value % 10 == 1:
            word = one
        elif value % 10 in {2, 3, 4}:
            word = few
        else:
            word = many
        return f"{count_value} {word}"

    def _card_log_count_summary(self, item: dict[str, object]) -> str:
        return " | ".join(
            [
                self._card_log_plural_ru(
                    self._card_log_count_value(item.get("count")),
                    "событие",
                    "события",
                    "событий",
                ),
                self._card_log_plural_ru(
                    self._card_log_count_value(item.get("changes")),
                    "изменение",
                    "изменения",
                    "изменений",
                ),
                self._card_log_plural_ru(
                    self._card_log_count_value(item.get("deletions")),
                    "очищение",
                    "очищения",
                    "очищений",
                ),
                self._card_log_plural_ru(
                    self._card_log_count_value(item.get("actors")),
                    "участник",
                    "участника",
                    "участников",
                ),
            ]
        )

    def _card_log_human_datetime(self, value: Any) -> str:
        parsed = parse_datetime(value)
        if parsed is None:
            return normalize_text(value, default="", limit=80)
        return parsed.astimezone(business_timezone()).strftime("%d.%m.%Y %H:%M")

    def _card_log_trim_human_text(self, value: Any, *, limit: int = 600) -> str:
        text = normalize_text(value, default="", limit=limit)
        if not text:
            return ""
        return " ".join(text.replace("\r", " ").split())

    def _card_log_deadline_human_value(self, value: Any) -> str:
        if self._card_log_is_empty_value(value):
            return ""
        try:
            numeric_seconds = float(str(value))
        except (OverflowError, TypeError, ValueError):
            return self._card_log_trim_human_text(value, limit=120)
        if not math.isfinite(numeric_seconds):
            return self._card_log_trim_human_text(value, limit=120)
        if numeric_seconds <= 0:
            return "без срока"
        numeric_seconds = min(numeric_seconds, 365 * 86400)
        seconds = int(numeric_seconds)
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = remainder // 60
        parts: list[str] = []
        if days:
            parts.append(self._card_log_plural_ru(days, "день", "дня", "дней"))
        if hours:
            parts.append(self._card_log_plural_ru(hours, "час", "часа", "часов"))
        if minutes and not days:
            parts.append(self._card_log_plural_ru(minutes, "минута", "минуты", "минут"))
        return " ".join(parts[:2]) or "меньше минуты"

    def _card_log_collection_names(self, value: Any, *, name_keys: tuple[str, ...]) -> list[str]:
        if not isinstance(value, list):
            return []
        names: list[str] = []
        for item in value:
            if isinstance(item, dict):
                for key in name_keys:
                    text = self._card_log_trim_human_text(item.get(key), limit=80)
                    if text:
                        names.append(text)
                        break
            else:
                text = self._card_log_trim_human_text(item, limit=80)
                if text:
                    names.append(text)
        return names

    def _card_log_positions_human_line(
        self,
        title: str,
        value: Any,
        *,
        name_keys: tuple[str, ...] = ("name", "title", "label"),
    ) -> str:
        if not isinstance(value, list) or not value:
            return ""
        count = len(value)
        names = self._card_log_collection_names(value, name_keys=name_keys)
        line = f"{title}: {self._card_log_plural_ru(count, 'позиция', 'позиции', 'позиций')}"
        if names:
            preview = "; ".join(names[:3])
            if len(names) > 3:
                preview += "; ..."
            line += f" ({preview})"
        return line

    def _card_log_tags_human_value(self, value: Any) -> str:
        if self._card_log_is_empty_value(value):
            return ""
        if isinstance(value, list):
            names = self._card_log_collection_names(value, name_keys=("label", "name", "title"))
            return (
                ", ".join(names)
                if names
                else self._card_log_plural_ru(len(value), "метка", "метки", "меток")
            )
        return self._card_log_trim_human_text(value, limit=240)

    def _card_log_scalar_human_value(self, value: Any) -> str:
        if self._card_log_is_empty_value(value):
            return ""
        if isinstance(value, bool):
            return "да" if value else "нет"
        if isinstance(value, (int, float)):
            return str(value)
        return self._card_log_trim_human_text(value, limit=600)

    def _card_log_is_punctuation_only_text(self, value: str) -> bool:
        return bool(value) and not any(char.isalnum() for char in value)

    def _card_log_vehicle_profile_human_value(self, value: Any) -> str:
        if not isinstance(value, dict) or not value:
            return self._card_log_scalar_human_value(value)
        lines: list[str] = []
        for key, label in CARD_JOURNAL_VEHICLE_PROFILE_LABELS.items():
            raw = value.get(key)
            if self._card_log_is_empty_value(raw):
                continue
            text = self._card_log_scalar_human_value(raw)
            if not text:
                continue
            if key == "customer_name" and self._card_log_is_punctuation_only_text(text):
                continue
            if key == "registration_plate":
                text = text.upper()
            lines.append(f"{label}: {text}")
        return "\n".join(lines)

    def _card_log_repair_order_status_text(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        labels = {
            "open": "открыт",
            "closed": "закрыт",
            "ready": "готов",
            "draft": "черновик",
            "cancelled": "отменён",
            "canceled": "отменён",
        }
        return labels.get(raw, self._card_log_trim_human_text(value, limit=80))

    def _card_log_repair_order_human_value(self, value: Any) -> str:
        if not isinstance(value, dict) or not value:
            return self._card_log_scalar_human_value(value)
        lines: list[str] = []
        for key, label in CARD_JOURNAL_REPAIR_ORDER_LABELS.items():
            raw = value.get(key)
            if self._card_log_is_empty_value(raw):
                continue
            if key == "status":
                text = self._card_log_repair_order_status_text(raw)
            elif key == "payment_method":
                text = CARD_JOURNAL_PAYMENT_METHOD_LABELS.get(
                    str(raw or "").strip().lower(), self._card_log_scalar_human_value(raw)
                )
            else:
                text = self._card_log_scalar_human_value(raw)
            if text:
                lines.append(f"{label}: {text}")
        for title, key in (
            ("Работы", "works"),
            ("Материалы", "materials"),
            ("Оплаты", "payments"),
            ("Метки", "tags"),
        ):
            line = self._card_log_positions_human_line(title, value.get(key))
            if line:
                lines.append(line)
        return "\n".join(lines)

    def _card_log_generic_human_value(self, value: Any) -> str:
        if self._card_log_is_empty_value(value):
            return ""
        if isinstance(value, list):
            names = self._card_log_collection_names(value, name_keys=("label", "name", "title"))
            if names:
                return ", ".join(names[:8])
            return self._card_log_plural_ru(len(value), "запись", "записи", "записей")
        if isinstance(value, dict):
            lines: list[str] = []
            for key in sorted(value.keys()):
                raw = value.get(key)
                if self._card_log_is_empty_value(raw):
                    continue
                label = CARD_JOURNAL_GENERIC_DETAIL_LABELS.get(
                    key, CARD_JOURNAL_FIELD_LABELS.get(key, "")
                )
                if not label:
                    continue
                text = self._card_log_human_change_value(key, raw)
                if text:
                    lines.append(f"{label}: {text.replace(chr(10), ' / ')}")
            if lines:
                return "\n".join(lines)
            non_empty = [item for item in value.values() if not self._card_log_is_empty_value(item)]
            return self._card_log_plural_ru(
                len(non_empty), "поле с данными", "поля с данными", "полей с данными"
            )
        return self._card_log_scalar_human_value(value)

    def _card_log_human_change_value(self, field: str, value: Any) -> str:
        if field in {
            "deadline",
            "deadline_total_seconds",
            "before_total_seconds",
            "after_total_seconds",
        }:
            return self._card_log_deadline_human_value(value)
        if field == "column":
            raw = str(value or "").strip()
            return CARD_JOURNAL_COLUMN_VALUE_LABELS.get(
                raw, self._card_log_scalar_human_value(value)
            )
        if field == "indicator":
            raw = str(value or "").strip().lower()
            return {
                "green": "зелёный",
                "yellow": "жёлтый",
                "red": "красный",
            }.get(raw, self._card_log_scalar_human_value(value))
        if field == "timer":
            raw = str(value or "").strip().lower()
            return {
                "inactive": "выключен",
                "running": "запущен",
            }.get(raw, self._card_log_scalar_human_value(value))
        if field in {"tags", "tag"}:
            return self._card_log_tags_human_value(value)
        if field == "vehicle_profile":
            return self._card_log_vehicle_profile_human_value(value)
        if field == "repair_order":
            return self._card_log_repair_order_human_value(value)
        if isinstance(value, (dict, list)):
            return self._card_log_generic_human_value(value)
        return self._card_log_scalar_human_value(value)

    def _card_log_compact_change_value(self, value: Any) -> str:
        text = self._card_log_full_value_text(value).replace("\r", " ").replace("\n", " / ")
        text = " ".join(text.split())
        if not text:
            return "—"
        return text[:237] + "..." if len(text) > 240 else text

    def _card_log_is_empty_value(self, value: Any) -> bool:
        if value is None or value == "":
            return True
        if isinstance(value, (list, tuple, set, dict)) and not value:
            return True
        return False

    def _card_log_change_kind(self, before: Any, after: Any) -> str:
        before_empty = self._card_log_is_empty_value(before)
        after_empty = self._card_log_is_empty_value(after)
        if before_empty and not after_empty:
            return "added"
        if not before_empty and after_empty:
            return "removed"
        return "changed"

    def _card_log_change(
        self,
        field: str,
        *,
        before: Any = "",
        after: Any = "",
        label: str | None = None,
    ) -> dict[str, Any]:
        kind = self._card_log_change_kind(before, after)
        before_human = self._card_log_human_change_value(field, before)
        after_human = self._card_log_human_change_value(field, after)
        return {
            "schema_version": "card_journal.change.v2",
            "field": field,
            "label": label or CARD_JOURNAL_FIELD_LABELS.get(field, field.replace("_", " ")),
            "kind": kind,
            "human_kind": self._card_log_change_kind(before_human, after_human),
            "before": self._card_log_full_value_text(before),
            "after": self._card_log_full_value_text(after),
            "before_human": before_human,
            "after_human": after_human,
            "before_summary": self._card_log_compact_change_value(before),
            "after_summary": self._card_log_compact_change_value(after),
        }

    def _card_log_action_label(self, action: str) -> str:
        return CARD_JOURNAL_ACTION_LABELS.get(action, action.replace("_", " ").strip() or "Событие")

    def _card_log_action_icon(self, action: str) -> str:
        return CARD_JOURNAL_ACTION_ICONS.get(action, "•")

    def _card_log_source_label(self, source: Any) -> str:
        normalized = str(source or "").strip().lower()
        return CARD_JOURNAL_SOURCE_LABELS.get(normalized, normalized or "система")

    def _card_log_display_actor_name(self, actor_name: str) -> str:
        if actor_name.strip().upper() == "API":
            return "СЕРВЕР"
        return actor_name

    def _card_log_human_message(
        self, message: str, *, actor_name: str, display_actor_name: str
    ) -> str:
        if actor_name != display_actor_name and message.startswith(f"{actor_name} "):
            return f"{display_actor_name}{message[len(actor_name) :]}"
        return message

    def _card_log_changes_for_lifecycle_actions(
        self, action: str, details: dict[str, Any]
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []

        def add(field: str, before: Any = "", after: Any = "", label: str | None = None) -> None:
            if self._card_log_is_empty_value(before) and self._card_log_is_empty_value(after):
                return
            changes.append(self._card_log_change(field, before=before, after=after, label=label))

        if action == "card_created":
            add("vehicle", after=details.get("vehicle"))
            add("title", after=details.get("title"))
            add("description", after=details.get("description"))
            add("column", after=details.get("column"))
            add("tags", after=details.get("tags"))
            add("deadline", after=details.get("deadline_total_seconds"))
        elif action == "card_moved":
            add("column", before=details.get("before_column"), after=details.get("after_column"))
        elif action in {"card_archived", "card_restored"}:
            add("column", after=details.get("column"))
        elif action == "vehicle_changed":
            add("vehicle", before=details.get("before"), after=details.get("after"))
        elif action == "title_changed":
            add("title", before=details.get("before"), after=details.get("after"))
        return changes

    def _card_log_changes_for_description_and_signal_actions(
        self, action: str, details: dict[str, Any]
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []

        def add(field: str, before: Any = "", after: Any = "", label: str | None = None) -> None:
            if self._card_log_is_empty_value(before) and self._card_log_is_empty_value(after):
                return
            changes.append(self._card_log_change(field, before=before, after=after, label=label))

        if action == "description_changed":
            if "before" in details or "after" in details:
                add("description", before=details.get("before"), after=details.get("after"))
            else:
                add(
                    "description",
                    before=details.get("before_preview"),
                    after=details.get("after_preview") or details.get("description_preview"),
                )
        elif action == "board_summary_changed":
            add("board_summary", before=details.get("before"), after=details.get("after"))
        elif action == "signal_changed":
            add(
                "deadline",
                before=details.get("before_total_seconds"),
                after=details.get("after_total_seconds"),
            )
        elif action == "signal_indicator_changed":
            add(
                "indicator",
                before=details.get("before_indicator"),
                after=details.get("after_indicator"),
            )
            if "deadline_total_seconds" in details:
                add("deadline", after=details.get("deadline_total_seconds"))
        elif action in {"timer_started", "timer_restarted", "timer_stopped"}:
            add(
                "timer",
                before=details.get("before_timer_state"),
                after=details.get("after_timer_state"),
            )
            if action != "timer_stopped" and "deadline_total_seconds" in details:
                add("deadline", after=details.get("deadline_total_seconds"))
        return changes

    def _card_log_changes_for_tag_actions(
        self, action: str, details: dict[str, Any]
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []

        def add(field: str, before: Any = "", after: Any = "", label: str | None = None) -> None:
            if self._card_log_is_empty_value(before) and self._card_log_is_empty_value(after):
                return
            changes.append(self._card_log_change(field, before=before, after=after, label=label))

        if action == "attachment_added":
            add("attachment", after=details.get("file_name"))
        elif action == "attachment_removed":
            add("attachment", before=details.get("file_name"))
        elif action == "tag_added":
            add("tag", after=details.get("tag"))
        elif action == "tag_removed":
            add("tag", before=details.get("tag"))
        elif action == "tag_color_changed":
            add(
                "tag_color",
                before=details.get("before_color"),
                after=details.get("after_color"),
                label=f"Цвет метки {details.get('tag') or ''}".strip(),
            )
        elif action == "tags_changed":
            add("tags", before=details.get("before"), after=details.get("after"))
        return changes

    def _card_log_changes_for_payload_actions(
        self, action: str, details: dict[str, Any]
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []

        def add(field: str, before: Any = "", after: Any = "", label: str | None = None) -> None:
            if self._card_log_is_empty_value(before) and self._card_log_is_empty_value(after):
                return
            changes.append(self._card_log_change(field, before=before, after=after, label=label))

        if action == "vehicle_profile_updated":
            if "before" in details or "after" in details:
                add("vehicle_profile", before=details.get("before"), after=details.get("after"))
            elif details.get("changed_fields"):
                add("vehicle_profile", after={"changed_fields": details.get("changed_fields")})
        elif action == "repair_order_updated":
            if "before" in details or "after" in details:
                add("repair_order", before=details.get("before"), after=details.get("after"))
            else:
                summary_keys = (
                    "number",
                    "status",
                    "works",
                    "materials",
                    "payments",
                    "paid_total",
                    "payment_status",
                )
                summary = {key: details.get(key) for key in summary_keys if key in details}
                add("repair_order", after=summary)
        elif action == "cash_transaction_deleted":
            add("cash_transaction", before=details)
        return changes

    def _card_log_changes(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        details = event.get("details")
        if not isinstance(details, dict) or not details:
            return []

        action = str(event.get("action") or "").strip()
        changes: list[dict[str, Any]] = []
        for handler in (
            self._card_log_changes_for_lifecycle_actions,
            self._card_log_changes_for_description_and_signal_actions,
            self._card_log_changes_for_tag_actions,
            self._card_log_changes_for_payload_actions,
        ):
            changes.extend(handler(action, details))
        return changes

    def _card_log_value_lines(self, value: Any) -> list[str]:
        text = str(value or "")
        if not text:
            return ["—"]
        return [line.rstrip() or "—" for line in text.splitlines()]

    def _card_log_change_phrase(self, change: dict[str, Any]) -> str:
        field = str(change.get("field") or "")
        label = str(change.get("label") or CARD_JOURNAL_FIELD_LABELS.get(field) or field or "Поле")
        kind = str(change.get("human_kind") or change.get("kind") or "changed")
        field_titles = {
            "vehicle": {
                "added": "Автомобиль заполнен",
                "removed": "⚠️ Автомобиль очищен",
                "changed": "Автомобиль обновлён",
            },
            "title": {
                "added": "Заголовок заполнен",
                "removed": "⚠️ Заголовок очищен",
                "changed": "Заголовок обновлён",
            },
            "description": {
                "added": "Описание заполнено",
                "removed": "⚠️ Описание очищено",
                "changed": "Описание обновлено",
            },
            "board_summary": {
                "added": "Краткая суть заполнена",
                "removed": "⚠️ Краткая суть очищена",
                "changed": "Краткая суть обновлена",
            },
            "column": {
                "added": "Столбец выбран",
                "removed": "⚠️ Столбец очищен",
                "changed": "Столбец обновлён",
            },
            "deadline": {
                "added": "Срок/сигнал задан",
                "removed": "⚠️ Срок/сигнал очищен",
                "changed": "Срок/сигнал обновлён",
            },
            "indicator": {
                "added": "Индикатор задан",
                "removed": "⚠️ Индикатор очищен",
                "changed": "Индикатор обновлён",
            },
            "tags": {
                "added": "Метки добавлены",
                "removed": "⚠️ Метки очищены",
                "changed": "Метки обновлены",
            },
            "tag": {
                "added": "Метка добавлена",
                "removed": "⚠️ Метка удалена",
                "changed": "Метка обновлена",
            },
            "tag_color": {
                "added": "Цвет метки задан",
                "removed": "⚠️ Цвет метки очищен",
                "changed": "Цвет метки обновлён",
            },
            "attachment": {
                "added": "Файл добавлен",
                "removed": "⚠️ Файл удалён",
                "changed": "Файл обновлён",
            },
            "vehicle_profile": {
                "added": "Техкарта автомобиля заполнена",
                "removed": "⚠️ Техкарта автомобиля очищена",
                "changed": "Техкарта автомобиля обновлена",
            },
            "repair_order": {
                "added": "Заказ-наряд заполнен",
                "removed": "⚠️ Заказ-наряд очищен",
                "changed": "Заказ-наряд обновлён",
            },
            "cash_transaction": {
                "added": "Движение кассы добавлено",
                "removed": "⚠️ Движение кассы удалено",
                "changed": "Движение кассы обновлено",
            },
        }
        title = field_titles.get(field, {}).get(kind)
        if title:
            return title
        if kind == "removed":
            return f"⚠️ {label} очищено"
        if kind == "added":
            verb = "добавлено" if field in {"tag", "tags", "attachment"} else "заполнено"
            return f"{label} {verb}"
        return f"{label} обновлено"

    def _card_log_deadline_journal_text(self, event: dict[str, Any], change: dict[str, Any]) -> str:
        details = event.get("details")
        details = details if isinstance(details, dict) else {}
        duration = str(change.get("after_human") or "").strip()
        deadline_timestamp = (
            details.get("after_deadline_timestamp")
            or details.get("deadline_timestamp")
            or details.get("deadline_at")
        )
        lines: list[str] = []
        if duration:
            lines.append(f"Срок: {duration}")
        if deadline_timestamp:
            lines.append(f"Дедлайн: {self._card_log_human_datetime(deadline_timestamp)}")
        return "\n".join(lines)

    def _card_log_journal_change_text(self, event: dict[str, Any], change: dict[str, Any]) -> str:
        field = str(change.get("field") or "")
        kind = str(change.get("kind") or "")
        if kind == "removed":
            if field in {"tag", "attachment"}:
                return str(change.get("before_human") or change.get("before_summary") or "").strip()
            return ""
        if field == "deadline":
            return self._card_log_deadline_journal_text(event, change)
        if field in {"description", "board_summary"}:
            return str(change.get("after") or "")
        return str(change.get("after_human") or change.get("after") or "").strip()

    def _card_log_journal_change_block(
        self, event: dict[str, Any], change: dict[str, Any]
    ) -> dict[str, Any]:
        field = str(change.get("field") or "")
        text = self._card_log_journal_change_text(event, change)
        return {
            "schema_version": "card_journal.block.v1",
            "kind": "field",
            "field": field,
            "label": str(change.get("label") or CARD_JOURNAL_FIELD_LABELS.get(field) or field),
            "title": self._card_log_change_phrase(change),
            "text": text,
            "is_full_value": field in {"description", "board_summary"},
            "is_empty": not bool(text.strip()),
            "change_kind": str(change.get("kind") or "changed"),
        }

    def _card_log_note_block(self, title: str, text: Any = "") -> dict[str, Any]:
        normalized_title = normalize_text(title, default="", limit=220)
        normalized_text = self._card_log_full_value_text(text).strip()
        return {
            "schema_version": "card_journal.block.v1",
            "kind": "note",
            "field": "",
            "label": normalized_title,
            "title": normalized_title,
            "text": normalized_text,
            "is_full_value": False,
            "is_empty": not bool(normalized_text),
            "change_kind": "note",
        }

    def _card_log_event_specific_journal_blocks(
        self, event: dict[str, Any]
    ) -> list[dict[str, Any]]:
        details = event.get("details")
        if not isinstance(details, dict):
            return []
        action = str(event.get("action") or "").strip()
        blocks: list[dict[str, Any]] = []
        if action == "card_client_linked":
            client_name = self._card_log_trim_human_text(details.get("client_name"), limit=160)
            if client_name:
                blocks.append(self._card_log_note_block("Клиент", client_name))
            if details.get("vehicle_created") is True:
                blocks.append(self._card_log_note_block("Автомобиль клиента", "создан из карточки"))
            elif details.get("client_vehicle_id"):
                blocks.append(
                    self._card_log_note_block(
                        "Автомобиль клиента", "привязан существующий автомобиль"
                    )
                )
            return blocks
        if action == "card_client_unlinked":
            return [self._card_log_note_block("Клиент", "отвязан от карточки")]
        if action == "card_client_vehicle_synced":
            return [self._card_log_note_block("Автомобиль клиента", "обновлён по данным карточки")]
        if action == "card_client_vehicle_unlinked":
            return [self._card_log_note_block("Автомобиль клиента", "отвязан от карточки")]
        return blocks

    def _card_log_entry_journal_blocks(
        self,
        event: dict[str, Any],
        changes: list[dict[str, Any]],
        *,
        action_label: str,
        message: str,
    ) -> list[dict[str, Any]]:
        if changes:
            return [self._card_log_journal_change_block(event, change) for change in changes]

        event_specific_blocks = self._card_log_event_specific_journal_blocks(event)
        if event_specific_blocks:
            return event_specific_blocks

        details = event.get("details")
        blocks: list[dict[str, Any]] = []
        if not isinstance(details, dict) or not details:
            if message and message != action_label:
                blocks.append(self._card_log_note_block(action_label, message))
            return blocks
        for key in sorted(details.keys()):
            value = details.get(key)
            if self._card_log_is_empty_value(value):
                continue
            label = CARD_JOURNAL_GENERIC_DETAIL_LABELS.get(
                key, CARD_JOURNAL_FIELD_LABELS.get(key, key.replace("_", " "))
            )
            compact = self._card_log_human_change_value(key, value)
            if compact:
                blocks.append(self._card_log_note_block(label, compact))
        return blocks

    def _card_log_blocks_to_detail_lines(self, blocks: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for block in blocks:
            title = normalize_text(block.get("title"), default="", limit=220)
            text = str(block.get("text") or "")
            if not title and not text:
                continue
            if text:
                lines.append(f"{title}:")
                lines.extend(f"  {line}" for line in self._card_log_value_lines(text))
            elif title:
                lines.append(title)
        return lines

    def _card_log_entries(
        self,
        events: list[dict[str, Any]],
        *,
        card: Card,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        card_short_id = short_entity_id(card.id, prefix="C")
        card_heading = card.heading()
        for event in events:
            timestamp = parse_datetime(event.get("timestamp")) or utc_now()
            business_timestamp = timestamp.astimezone(business_timezone())
            day_key = business_timestamp.date().isoformat()
            iso_year, iso_week, _ = business_timestamp.isocalendar()
            week_key = f"{iso_year}-W{iso_week:02d}"
            month_key = business_timestamp.strftime("%Y-%m")
            time_short = business_timestamp.strftime("%H:%M")
            details = event.get("details")
            action = normalize_text(event.get("action"), default="unknown", limit=80)
            source = event.get("source") or "system"
            changes = self._card_log_changes(event)
            details_copy = dict(details) if isinstance(details, dict) else {}
            has_deletion = any(item.get("kind") == "removed" for item in changes)
            actor_name = normalize_actor_name(event.get("actor_name"))
            display_actor_name = self._card_log_display_actor_name(actor_name)
            action_label = self._card_log_action_label(action)
            source_label = self._card_log_source_label(source)
            icon = self._card_log_action_icon(action)
            message = normalize_text(event.get("message"), default="Событие", limit=300)
            human_message = self._card_log_human_message(
                message, actor_name=actor_name, display_actor_name=display_actor_name
            )
            journal_blocks = self._card_log_entry_journal_blocks(
                event,
                changes,
                action_label=action_label,
                message=human_message,
            )
            detail_lines = self._card_log_blocks_to_detail_lines(journal_blocks)
            journal_text = "\n".join(detail_lines)
            source_hint = ""
            if source_label == "API" and display_actor_name != "СЕРВЕР":
                source_hint = " через сервер"
            elif source_label == "MCP/GPT" and display_actor_name.upper() != source_label.upper():
                source_hint = " через MCP/GPT"
            elif source_label == "система" and display_actor_name.lower() != "система":
                source_hint = " через систему"
            display_line = (
                f"{time_short} | {icon} {action_label} | {display_actor_name}{source_hint}"
            )
            entry = {
                "schema_version": "card_journal.entry.v2",
                "id": event.get("id") or "",
                "timestamp": timestamp.isoformat(),
                "business_timestamp": business_timestamp.isoformat(),
                "date": day_key,
                "time": business_timestamp.strftime("%H:%M:%S"),
                "time_short": time_short,
                "day_key": day_key,
                "week_key": week_key,
                "month_key": month_key,
                "actor_name": actor_name,
                "display_actor_name": display_actor_name,
                "source": source,
                "source_label": source_label,
                "action": action,
                "action_label": action_label,
                "icon": icon,
                "message": message,
                "human_message": human_message,
                "card_id": card.id,
                "card_short_id": card_short_id,
                "card_heading": card_heading,
                "details": details_copy,
                "details_text": journal_text,
                "detail_lines": detail_lines,
                "journal_blocks": journal_blocks,
                "published_blocks": journal_blocks,
                "journal_text": journal_text,
                "published_text": journal_text,
                "changes": changes,
                "change_count": len(changes),
                "has_deletion": has_deletion,
                "display_line": display_line,
            }
            entry["summary"] = " · ".join(
                value
                for value in (
                    entry["time_short"],
                    entry["actor_name"],
                    str(entry["action_label"] or entry["message"] or "").strip(),
                )
                if value
            )
            entries.append(entry)
        return entries

    def _card_log_group_entries(
        self, entries: list[dict[str, Any]], *, key: str, kind: str
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in entries:
            grouped.setdefault(str(item.get(key) or "unknown"), []).append(item)
        result: list[dict[str, Any]] = []
        for group_key in sorted(grouped.keys(), reverse=True):
            group_entries = grouped[group_key]
            totals = self._card_log_totals(group_entries)
            payload: dict[str, Any] = {
                "key": group_key,
                "entries": group_entries,
                **totals,
            }
            if kind == "day":
                payload["day_key"] = group_key
                payload["label"] = self._card_log_day_label(group_key)
                payload["first_timestamp"] = group_entries[0]["timestamp"]
                payload["last_timestamp"] = group_entries[-1]["timestamp"]
            elif kind == "week":
                payload["week_key"] = group_key
                payload["label"] = self._card_log_week_label(group_key)
            else:
                payload["month_key"] = group_key
                payload["label"] = self._card_log_month_label(group_key)
            result.append(payload)
        return result

    _card_log_day_label = staticmethod(day_label)

    _card_log_week_label = staticmethod(week_label)

    _card_log_month_label = staticmethod(month_label)

    def _card_log_totals(self, entries: list[dict[str, Any]]) -> dict[str, object]:
        return {
            "count": len(entries),
            "actors": len(
                {str(item.get("actor_name") or "") for item in entries if item.get("actor_name")}
            ),
            "sources": len(
                {str(item.get("source") or "") for item in entries if item.get("source")}
            ),
            "actions": len(
                {str(item.get("action") or "") for item in entries if item.get("action")}
            ),
            "changes": sum(
                self._card_log_count_value(item.get("change_count")) for item in entries
            ),
            "deletions": sum(1 for item in entries if item.get("has_deletion")),
        }

    def _card_log_markdown(
        self,
        *,
        card: Card,
        entries: list[dict[str, Any]],
        days: list[dict[str, Any]],
        weeks: list[dict[str, Any]],
        months: list[dict[str, Any]],
        totals: dict[str, object],
        meta: dict[str, object],
    ) -> str:
        lines = [
            "# 🧾 Журнал карточки",
            "",
            "## 📊 Итоги карточки",
            f"- Карточка: {card.heading()}",
            f"- Показано: {self._card_log_plural_ru(self._card_log_count_value(totals.get('count')), 'событие', 'события', 'событий')} из {meta['events_total']}",
            f"- Участвовали: {self._card_log_plural_ru(self._card_log_count_value(totals.get('actors')), 'человек', 'человека', 'человек')}",
            f"- Разных действий: {self._card_log_plural_ru(self._card_log_count_value(totals.get('actions')), 'тип', 'типа', 'типов')}",
            f"- Изменений в полях: {self._card_log_plural_ru(self._card_log_count_value(totals.get('changes')), 'изменение', 'изменения', 'изменений')}",
            f"- Очищений/удалений: {self._card_log_plural_ru(self._card_log_count_value(totals.get('deletions')), 'случай', 'случая', 'случаев')}",
        ]
        if meta.get("has_more"):
            lines.append("- Показана только часть журнала по лимиту выгрузки.")
        if meta.get("oldest_timestamp") and meta.get("newest_timestamp"):
            lines.append(
                "- Период журнала: "
                + f"{self._card_log_human_datetime(meta.get('oldest_timestamp'))} → "
                + f"{self._card_log_human_datetime(meta.get('newest_timestamp'))}"
            )
        lines.append("")
        if not entries:
            lines.extend(["## 🧾 События", "Журнал пуст."])
            return "\n".join(lines).strip()

        lines.extend(["## 🗓️ По месяцам"])
        for item in months:
            lines.append(f"- **{item['label']}**: {self._card_log_count_summary(item)}")
        lines.extend(["", "## 📅 По неделям"])
        for item in weeks:
            lines.append(f"- **{item['label']}**: {self._card_log_count_summary(item)}")
        lines.extend(["", "## 🧾 События по дням"])
        for day in days:
            lines.extend(
                [
                    "",
                    f"### 📆 {day['label']}",
                    f"Итого за день: {self._card_log_count_summary(day)}",
                    "",
                ]
            )
            for item in day["entries"]:
                lines.append(f"- {item['display_line']}")
                self._append_card_log_journal_blocks(lines, item.get("journal_blocks"))
        return "\n".join(lines).strip()

    def _append_card_log_journal_blocks(self, lines: list[str], blocks: Any) -> None:
        if not isinstance(blocks, list):
            return
        for block in blocks:
            if not isinstance(block, dict):
                continue
            title = normalize_text(block.get("title"), default="", limit=220)
            text = str(block.get("text") or "")
            if not title and not text:
                continue
            if title:
                value_lines = self._card_log_value_lines(text) if text else []
                if text and len(value_lines) == 1:
                    lines.append(f"  - {title}: {value_lines[0]}")
                    continue
                lines.append(f"  - {title}")
            if text:
                for value_line in self._card_log_value_lines(text):
                    lines.append(f"      {value_line}")
