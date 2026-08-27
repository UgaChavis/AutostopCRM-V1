from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import BoardApiClient
from .payloads import DeadlinePayload, JsonEnvelope

BOARD_CARD_TIMER_WRITE_TOOL_NAMES = frozenset(
    {
        "set_card_deadline",
        "set_card_indicator",
        "start_card_timer",
        "stop_card_timer",
    }
)


@dataclass(frozen=True, slots=True)
class BoardCardTimerWriteContext:
    board_api: BoardApiClient
    scoped_description: Callable[[str], str]
    write_tool_annotations: Callable[..., ToolAnnotations]
    relay_board_call: Callable[..., JsonEnvelope]


def register_board_card_timer_writes(
    server: FastMCP,
    context: BoardCardTimerWriteContext,
) -> frozenset[str]:
    @server.tool(
        name="set_card_deadline",
        description=context.scoped_description(
            "Change only the deadline of a card on the current AutoStop CRM board. "
            "The deadline accepts either days/hours/minutes/seconds or total_seconds."
        ),
        annotations=context.write_tool_annotations("Set Card Deadline"),
        structured_output=True,
    )
    def set_card_deadline(
        card_id: str,
        deadline: DeadlinePayload,
        actor_name: str | None = None,
        response_mode: Literal["full", "compact"] = "full",
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "set_card_deadline",
            lambda: context.board_api.set_card_deadline(
                card_id=card_id,
                deadline=deadline.model_dump(),
                actor_name=actor_name,
                response_mode=response_mode,
            ),
        )

    @server.tool(
        name="start_card_timer",
        description=context.scoped_description(
            "Start or restart a card timer. Supply a deadline to change the duration; omit it to reuse the card's saved duration."
        ),
        annotations=context.write_tool_annotations("Start Card Timer"),
        structured_output=True,
    )
    def start_card_timer(
        card_id: str,
        deadline: DeadlinePayload | None = None,
        expected_updated_at: str | None = None,
        actor_name: str | None = None,
        response_mode: Literal["full", "compact"] = "full",
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "start_card_timer",
            lambda: context.board_api.start_card_timer(
                card_id=card_id,
                deadline=deadline.model_dump() if deadline is not None else None,
                expected_updated_at=expected_updated_at,
                actor_name=actor_name,
                response_mode=response_mode,
            ),
        )

    @server.tool(
        name="stop_card_timer",
        description=context.scoped_description(
            "Stop a card timer without deleting the saved duration. A later start begins the full saved duration again."
        ),
        annotations=context.write_tool_annotations("Stop Card Timer"),
        structured_output=True,
    )
    def stop_card_timer(
        card_id: str,
        expected_updated_at: str | None = None,
        actor_name: str | None = None,
        response_mode: Literal["full", "compact"] = "full",
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "stop_card_timer",
            lambda: context.board_api.stop_card_timer(
                card_id=card_id,
                expected_updated_at=expected_updated_at,
                actor_name=actor_name,
                response_mode=response_mode,
            ),
        )

    @server.tool(
        name="set_card_indicator",
        description=context.scoped_description(
            "Service tool for changing the signal lamp state of a card. Because the indicator is derived from time, this operation recalculates the deadline to reach the requested color."
        ),
        annotations=context.write_tool_annotations("Set Card Indicator"),
        structured_output=True,
    )
    def set_card_indicator(
        card_id: str,
        indicator: Literal["green", "yellow", "red"],
        actor_name: str | None = None,
        response_mode: Literal["full", "compact"] = "full",
    ) -> JsonEnvelope:
        return context.relay_board_call(
            "set_card_indicator",
            lambda: context.board_api.set_card_indicator(
                card_id=card_id,
                indicator=indicator,
                actor_name=actor_name,
                response_mode=response_mode,
            ),
        )

    return BOARD_CARD_TIMER_WRITE_TOOL_NAMES
