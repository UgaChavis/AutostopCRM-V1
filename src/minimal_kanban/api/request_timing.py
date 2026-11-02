"""Choose the technical log level for completed API requests."""

from __future__ import annotations

import logging

QUIET_SUCCESS_ROUTES = frozenset(
    {
        "/api/health",
        "/api/get_board_revision",
        "/api/get_board_snapshot",
        "/api/get_display_dashboard",
        "/api/mark_card_seen",
        "/api/mark_cashbox_notifications_seen",
    }
)
SLOW_API_REQUEST_MS = 1000.0


def success_log_level(route: str, duration_ms: float = 0.0) -> int:
    if duration_ms >= SLOW_API_REQUEST_MS:
        return logging.INFO
    return logging.DEBUG if route in QUIET_SUCCESS_ROUTES else logging.INFO
