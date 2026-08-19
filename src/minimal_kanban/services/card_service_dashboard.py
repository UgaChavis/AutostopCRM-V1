from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from html import escape as html_escape
from html import unescape as html_unescape
from html.parser import HTMLParser
from typing import Any

from .. import models as model_helpers
from ..models import Card, business_timezone, parse_business_datetime
from ..repair_order import REPAIR_ORDER_STATUS_CLOSED

DASHBOARD_VISIBLE_FIELD = "dashboard_visible"
DISPLAY_DASHBOARD_TIMEZONE = "Asia/Krasnoyarsk"
DISPLAY_DASHBOARD_WEEKS = 4
DISPLAY_DASHBOARD_MESSAGE_KEY = "display_dashboard_message"
DISPLAY_DASHBOARD_MESSAGE_SCHEMA = "display_dashboard_message.v1"
DISPLAY_DASHBOARD_MESSAGE_MAX_HTML = 40_000
DISPLAY_DASHBOARD_MESSAGE_MAX_TEXT = 12_000
DISPLAY_DASHBOARD_MESSAGE_MAX_IMAGES = 8

_DISPLAY_DASHBOARD_ALLOWED_TAGS = frozenset(
    {
        "blockquote",
        "b",
        "br",
        "div",
        "em",
        "font",
        "h2",
        "h3",
        "i",
        "li",
        "ol",
        "p",
        "span",
        "strong",
        "u",
        "ul",
    }
)
_DISPLAY_DASHBOARD_VOID_TAGS = frozenset({"br"})
_DISPLAY_DASHBOARD_SUPPRESSED_TAGS = frozenset(
    {"audio", "iframe", "math", "object", "script", "style", "svg", "video"}
)
_DISPLAY_DASHBOARD_FILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")

_ADMINISTRATIVE_POSITION_PATTERN = re.compile(
    r"(?:^|[^\w])(?:администратор\w*|административ\w*|administrator\w*|admin)(?:$|[^\w])",
    re.IGNORECASE,
)


def is_administrative_position(value: object) -> bool:
    return bool(_ADMINISTRATIVE_POSITION_PATTERN.search(str(value or "").strip().casefold()))


class _DisplayDashboardHtmlSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppressed: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = str(tag or "").casefold()
        if self._suppressed:
            if normalized_tag in _DISPLAY_DASHBOARD_SUPPRESSED_TAGS:
                self._suppressed.append(normalized_tag)
            return
        if normalized_tag in _DISPLAY_DASHBOARD_SUPPRESSED_TAGS:
            self._suppressed.append(normalized_tag)
            return
        if normalized_tag not in _DISPLAY_DASHBOARD_ALLOWED_TAGS:
            return
        if normalized_tag == "font":
            raw_size = next(
                (str(value or "").strip() for name, value in attrs if name.casefold() == "size"),
                "",
            )
            size = raw_size if raw_size in {"1", "2", "3", "4", "5", "6", "7"} else "3"
            self.parts.append(f'<font size="{size}">')
            return
        self.parts.append(f"<{normalized_tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = str(tag or "").casefold()
        if normalized_tag == "br" and not self._suppressed:
            self.parts.append("<br>")
            return
        self.handle_starttag(normalized_tag, attrs)
        self.handle_endtag(normalized_tag)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = str(tag or "").casefold()
        if self._suppressed:
            if normalized_tag == self._suppressed[-1]:
                self._suppressed.pop()
            return
        if (
            normalized_tag in _DISPLAY_DASHBOARD_ALLOWED_TAGS
            and normalized_tag not in _DISPLAY_DASHBOARD_VOID_TAGS
        ):
            self.parts.append(f"</{normalized_tag}>")

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            self.parts.append(html_escape(data, quote=False))


def _sanitize_display_dashboard_html(value: object) -> str:
    raw = str(value or "")
    if len(raw) > DISPLAY_DASHBOARD_MESSAGE_MAX_HTML * 2:
        raise ValueError("display_dashboard_message_html_too_large")
    sanitizer = _DisplayDashboardHtmlSanitizer()
    sanitizer.feed(raw)
    sanitizer.close()
    sanitized = "".join(sanitizer.parts).strip()
    if len(sanitized) > DISPLAY_DASHBOARD_MESSAGE_MAX_HTML:
        raise ValueError("display_dashboard_message_html_too_large")
    plain_text = html_unescape(re.sub(r"<[^>]*>", " ", sanitized))
    plain_text = re.sub(r"\s+", " ", plain_text).strip()
    if len(plain_text) > DISPLAY_DASHBOARD_MESSAGE_MAX_TEXT:
        raise ValueError("display_dashboard_message_text_too_large")
    return sanitized


def _display_dashboard_message_revision(body_html: str, image_file_ids: list[str]) -> str:
    canonical = json.dumps(
        {"body_html": body_html, "image_file_ids": image_file_ids},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


class CardServiceDashboardMixin:
    def configure_display_dashboard_shared_file_resolver(
        self,
        resolver: Callable[[str], dict[str, Any] | None] | None,
    ) -> None:
        self._display_dashboard_shared_file_resolver = resolver

    def get_display_dashboard(self, payload: dict | None = None) -> dict[str, Any]:
        del payload
        with self._lock:
            bundle = self._store.read_bundle()
            now = model_helpers.utc_now()
            local_now = now.astimezone(business_timezone())
            weeks = self._display_dashboard_week_buckets(bundle["cards"], now=now)
            completed_amounts = [Decimal(item["amount"]) for item in weeks[:3]]
            completed_average = (
                sum(completed_amounts, Decimal("0")) / Decimal(len(completed_amounts))
                if completed_amounts
                else Decimal("0")
            )
            return {
                "schema_version": "display_dashboard.v3",
                "generated_at": local_now.isoformat(),
                "timezone": DISPLAY_DASHBOARD_TIMEZONE,
                "message_board": self._normalized_display_dashboard_message(
                    bundle["settings"].get(DISPLAY_DASHBOARD_MESSAGE_KEY)
                ),
                "weeks": weeks,
                "completed_week_average": self._format_display_dashboard_rubles(completed_average),
            }

    def _normalized_display_dashboard_message(self, value: object) -> dict[str, Any]:
        source = value if isinstance(value, dict) else {}
        try:
            body_html = _sanitize_display_dashboard_html(source.get("body_html"))
        except ValueError:
            body_html = ""
        raw_image_ids = source.get("image_file_ids")
        image_file_ids: list[str] = []
        if isinstance(raw_image_ids, list):
            for item in raw_image_ids:
                file_id = str(item or "").strip()
                if (
                    file_id
                    and _DISPLAY_DASHBOARD_FILE_ID_PATTERN.fullmatch(file_id)
                    and file_id not in image_file_ids
                ):
                    image_file_ids.append(file_id)
                if len(image_file_ids) >= DISPLAY_DASHBOARD_MESSAGE_MAX_IMAGES:
                    break
        return {
            "schema_version": DISPLAY_DASHBOARD_MESSAGE_SCHEMA,
            "body_html": body_html,
            "image_file_ids": image_file_ids,
            "updated_at": str(source.get("updated_at") or "").strip()[:48],
            "updated_by": str(source.get("updated_by") or "").strip()[:120],
            "revision": _display_dashboard_message_revision(body_html, image_file_ids),
        }

    def _validated_display_dashboard_message(
        self,
        value: object,
        *,
        previous: dict[str, Any],
        expected_revision: object,
        actor_name: str,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            self._fail(
                "validation_error",
                "Нужно передать информационную доску объектом.",
                details={"field": DISPLAY_DASHBOARD_MESSAGE_KEY},
            )
        expected = str(expected_revision or "").strip()
        if not expected:
            self._fail(
                "validation_error",
                "Перед обновлением информационной доски нужно передать её текущую ревизию.",
                details={"field": "expected_revision"},
            )
        if expected != previous["revision"]:
            self._fail(
                "revision_conflict",
                "Информационная доска уже была изменена. Обновите данные и повторите правку.",
                status_code=409,
                details={
                    "expected_revision": expected,
                    "current_revision": previous["revision"],
                },
            )
        try:
            body_html = _sanitize_display_dashboard_html(value.get("body_html"))
        except ValueError as exc:
            self._fail(
                "validation_error",
                "Текст информационной доски слишком большой или содержит недопустимую разметку.",
                details={
                    "field": f"{DISPLAY_DASHBOARD_MESSAGE_KEY}.body_html",
                    "reason": str(exc),
                },
            )
        raw_image_ids = value.get("image_file_ids")
        if raw_image_ids is None:
            raw_image_ids = []
        if not isinstance(raw_image_ids, list):
            self._fail(
                "validation_error",
                "Список изображений информационной доски должен быть массивом.",
                details={"field": f"{DISPLAY_DASHBOARD_MESSAGE_KEY}.image_file_ids"},
            )
        if len(raw_image_ids) > DISPLAY_DASHBOARD_MESSAGE_MAX_IMAGES:
            self._fail(
                "validation_error",
                f"К информационной доске можно прикрепить не больше {DISPLAY_DASHBOARD_MESSAGE_MAX_IMAGES} изображений.",
                details={
                    "field": f"{DISPLAY_DASHBOARD_MESSAGE_KEY}.image_file_ids",
                    "max_items": DISPLAY_DASHBOARD_MESSAGE_MAX_IMAGES,
                },
            )
        image_file_ids: list[str] = []
        for item in raw_image_ids:
            file_id = str(item or "").strip()
            if not _DISPLAY_DASHBOARD_FILE_ID_PATTERN.fullmatch(file_id):
                self._fail(
                    "validation_error",
                    "Некорректная ссылка на изображение информационной доски.",
                    details={"field": f"{DISPLAY_DASHBOARD_MESSAGE_KEY}.image_file_ids"},
                )
            if file_id not in image_file_ids:
                image_file_ids.append(file_id)
        resolver = getattr(self, "_display_dashboard_shared_file_resolver", None)
        if image_file_ids and not callable(resolver):
            self._fail(
                "validation_error",
                "Хранилище общих файлов недоступно для проверки изображений.",
                details={"field": f"{DISPLAY_DASHBOARD_MESSAGE_KEY}.image_file_ids"},
            )
        for file_id in image_file_ids:
            file_info = resolver(file_id) if callable(resolver) else None
            mime_type = (
                str(file_info.get("mime_type") or "").strip().casefold()
                if isinstance(file_info, dict)
                else ""
            )
            if not mime_type.startswith("image/"):
                self._fail(
                    "validation_error",
                    "Информационная доска принимает только существующие изображения из общих файлов.",
                    details={
                        "field": f"{DISPLAY_DASHBOARD_MESSAGE_KEY}.image_file_ids",
                        "file_id": file_id,
                    },
                )
        return {
            "schema_version": DISPLAY_DASHBOARD_MESSAGE_SCHEMA,
            "body_html": body_html,
            "image_file_ids": image_file_ids,
            "updated_at": model_helpers.utc_now_iso(),
            "updated_by": str(actor_name or "").strip()[:120],
            "revision": _display_dashboard_message_revision(body_html, image_file_ids),
        }

    def _display_dashboard_week_buckets(
        self, cards: list[Card], *, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        timezone = business_timezone()
        local_now = (now or model_helpers.utc_now()).astimezone(timezone)
        current_week_start = datetime(
            local_now.year,
            local_now.month,
            local_now.day,
            tzinfo=timezone,
        ) - timedelta(days=local_now.weekday())
        starts = [
            current_week_start - timedelta(weeks=offset)
            for offset in range(DISPLAY_DASHBOARD_WEEKS - 1, -1, -1)
        ]
        totals = [Decimal("0") for _ in starts]
        counts = [0 for _ in starts]

        for card in cards:
            order = card.repair_order
            if order.status != REPAIR_ORDER_STATUS_CLOSED:
                continue
            active_cycle = order.cycles[-1] if order.cycles else {}
            recognized_at = active_cycle.get("recognized_at") or order.closed_at
            closed_at = parse_business_datetime(recognized_at)
            if closed_at is None:
                continue
            closed_at = closed_at.astimezone(timezone)
            if closed_at > local_now:
                continue
            for index, start_at in enumerate(starts):
                end_at = start_at + timedelta(days=7)
                if start_at <= closed_at < end_at:
                    cycle_total = active_cycle.get("grand_total") or order.grand_total_amount()
                    totals[index] += Decimal(str(cycle_total))
                    counts[index] += 1
                    break

        weeks: list[dict[str, Any]] = []
        for index, start_at in enumerate(starts):
            is_current = index == len(starts) - 1
            date_to = local_now.date() if is_current else (start_at + timedelta(days=6)).date()
            weeks.append(
                {
                    "date_from": start_at.date().isoformat(),
                    "date_to": date_to.isoformat(),
                    "label": f"{start_at:%d.%m}–{date_to:%d.%m}",
                    "amount": self._format_display_dashboard_rubles(totals[index]),
                    "orders_count": counts[index],
                    "is_current": is_current,
                }
            )
        return weeks

    @staticmethod
    def _format_display_dashboard_rubles(amount: object) -> str:
        return str(Decimal(str(amount)).to_integral_value(rounding=ROUND_CEILING))
