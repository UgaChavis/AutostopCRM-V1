"""Format permitted board read models without accessing storage."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import Column, short_entity_id


class BoardReadProjection:
    def __init__(self, *, json_dumps: Callable[..., str], wall_line_limit: int) -> None:
        self._json_dumps = json_dumps
        self._wall_line_limit = wall_line_limit

    def _markdown_value(self, value: Any) -> str:
        if value is None or value == "":
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            return self._json_dumps(value, sort_keys=True, separators=(",", ":"))
        return str(value).replace("\r", " ").replace("\n", " / ").strip() or "null"

    def _append_markdown_block(self, lines: list[str], key: str, value: Any) -> None:
        text = str(value or "").replace("\r", "").strip()
        if not text:
            lines.append(f"{key}: null")
            return
        lines.append(f"{key}: |")
        for raw_line in text.splitlines():
            lines.append(f"  {raw_line.rstrip()}")

    def limit_wall_text(self, text: str) -> str:
        lines = text.splitlines()
        if len(lines) <= self._wall_line_limit:
            return text
        kept = max(self._wall_line_limit - 3, 0)
        return "\n".join(
            [
                *lines[:kept],
                "",
                f"> [WALL TRUNCATED] Reached {self._wall_line_limit} lines. Use get_board_content or get_board_events for section-specific reads.",
            ]
        )

    def _append_markdown_card(self, lines: list[str], card: dict, *, index: int) -> None:
        short_id = card.get("short_id") or short_entity_id(str(card.get("id") or ""), prefix="C")
        title = card.get("heading") or card.get("title") or card.get("vehicle") or "Без названия"
        lines.append(f"#### Card {index}: {self._markdown_value(short_id)}")
        for key in (
            "id",
            "short_id",
            "column",
            "column_label",
            "archived",
            "position",
            "vehicle",
            "title",
            "heading",
            "status",
            "indicator",
            "remaining_display",
            "deadline_timestamp",
            "created_at",
            "updated_at",
            "attachment_count",
            "events_count",
        ):
            output_key = "card_id" if key == "id" else key
            lines.append(f"{output_key}: {self._markdown_value(card.get(key))}")
        lines.append(f"display_title: {self._markdown_value(title)}")
        lines.append(f"tags: {self._markdown_value(card.get('tags') or [])}")
        if isinstance(card.get("vehicle_profile"), dict):
            lines.append(
                "vehicle_profile: " + self._markdown_value(card.get("vehicle_profile") or {})
            )
        lines.append(
            "vehicle_profile_compact: "
            + self._markdown_value(card.get("vehicle_profile_compact") or {})
        )
        self._append_markdown_block(lines, "description", card.get("description"))
        lines.append("")

    def _board_content_markdown_column_lines(
        self, column: Column, active_by_column: dict[str, list[dict]]
    ) -> list[str]:
        return [
            f"### Column: {self._markdown_value(column.label)}",
            f"column_id: {self._markdown_value(column.id)}",
            f"position: {self._markdown_value(column.position)}",
            f"active_cards: {len(active_by_column.get(column.id, []))}",
            "",
        ]

    def _board_content_markdown_sticky_lines(self, sticky: dict, *, index: int) -> list[str]:
        lines = [
            f"### Sticky {index}: {self._markdown_value(sticky.get('short_id') or sticky.get('id'))}",
            f"sticky_id: {self._markdown_value(sticky.get('id'))}",
            f"short_id: {self._markdown_value(sticky.get('short_id'))}",
            f"x: {self._markdown_value(sticky.get('x'))}",
            f"y: {self._markdown_value(sticky.get('y'))}",
            f"remaining_seconds: {self._markdown_value(sticky.get('remaining_seconds'))}",
        ]
        self._append_markdown_block(lines, "text", sticky.get("text"))
        lines.append("")
        return lines

    def _append_board_content_markdown_cards(
        self, lines: list[str], columns: list[Column], active_by_column: dict[str, list[dict]]
    ) -> None:
        lines.append("## Cards By Column")
        if not any(active_by_column.values()):
            lines.append("cards: []")
            lines.append("")
        for column in columns:
            column_cards = active_by_column.get(column.id, [])
            lines.append(f"### Column: {self._markdown_value(column.label)}")
            lines.append(f"column_id: {self._markdown_value(column.id)}")
            if not column_cards:
                lines.append("cards: []")
                lines.append("")
                continue
            for index, card in enumerate(column_cards, start=1):
                self._append_markdown_card(lines, card, index=index)

    def _append_board_content_markdown_stickies(
        self, lines: list[str], stickies: list[dict]
    ) -> None:
        lines.append("## Stickies")
        if not stickies:
            lines.append("stickies: []")
            return
        for index, sticky in enumerate(stickies, start=1):
            lines.extend(self._board_content_markdown_sticky_lines(sticky, index=index))

    def board_content_markdown(
        self,
        columns: list[Column],
        cards: list[dict],
        stickies: list[dict],
        meta: dict[str, Any],
    ) -> str:
        active_cards = [card for card in cards if not card.get("archived")]
        archived_cards = [card for card in cards if card.get("archived")]
        active_by_column: dict[str, list[dict]] = {column.id: [] for column in columns}
        for card in active_cards:
            active_by_column.setdefault(str(card.get("column") or ""), []).append(card)

        lines = [
            "# AutoStop CRM Board Content",
            "",
            "## Metadata",
            f"generated_at: {self._markdown_value(meta.get('generated_at'))}",
            "text_format: markdown",
            "section_kind: board_content",
            f"include_archived: {self._markdown_value(meta.get('include_archived'))}",
            f"columns_total: {self._markdown_value(meta.get('columns'))}",
            f"active_cards_total: {self._markdown_value(meta.get('active_cards'))}",
            f"archived_cards_total: {self._markdown_value(meta.get('archived_cards'))}",
            f"cards_returned: {self._markdown_value(meta.get('cards_returned'))}",
            f"stickies_returned: {self._markdown_value(meta.get('stickies_returned'))}",
            "",
            "## Columns",
        ]
        if not columns:
            lines.append("columns: []")
        for column in columns:
            lines.extend(self._board_content_markdown_column_lines(column, active_by_column))

        self._append_board_content_markdown_cards(lines, columns, active_by_column)

        lines.append("## Archived Cards")
        if not archived_cards:
            lines.append("cards: []")
            lines.append("")
        for index, card in enumerate(archived_cards, start=1):
            self._append_markdown_card(lines, card, index=index)

        self._append_board_content_markdown_stickies(lines, stickies)
        return "\n".join(lines).rstrip()

    def event_log_text(self, events: list[dict], meta: dict[str, Any]) -> str:
        lines = [
            "# AutoStop CRM Event Log",
            "",
            "## Metadata",
            f"generated_at: {self._markdown_value(meta.get('generated_at'))}",
            "text_format: markdown",
            "section_kind: event_log",
            "event_order: newest_first",
            f"include_archived: {self._markdown_value(meta.get('include_archived'))}",
            f"events_returned: {self._markdown_value(meta.get('events_returned') or len(events))}",
            f"events_total: {self._markdown_value(meta.get('events_total') or len(events))}",
            f"event_limit: {self._markdown_value(meta.get('event_limit') or len(events))}",
            "",
            "## Events",
        ]
        if not events:
            lines.append("events: none")
            return "\n".join(lines)

        for index, event in enumerate(events, start=1):
            card_ref = str(event.get("card_short_id") or event.get("card_id") or "").strip()
            heading = str(event.get("card_heading") or "").strip()
            details = (
                str(event.get("details_text") or "").strip().replace("\r", "").replace("\n", " / ")
            )
            lines.extend(
                [
                    f"### Event {index}",
                    f"event_id: {self._markdown_value(event.get('id'))}",
                    f"time: {self._markdown_value(event.get('timestamp'))}",
                    f"actor: {self._markdown_value(event.get('actor_name'))}",
                    f"source: {self._markdown_value(event.get('source'))}",
                    f"action: {self._markdown_value(event.get('action'))}",
                    f"message: {self._markdown_value(event.get('message'))}",
                ]
            )
            if card_ref:
                lines.append(f"card: {card_ref}")
            if heading:
                lines.append(f"heading: {self._markdown_value(heading)}")
            if details:
                lines.append(f"details: {self._markdown_value(details)}")
            lines.append("")
        return "\n".join(lines).rstrip()
