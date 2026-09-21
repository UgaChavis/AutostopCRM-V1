from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..services.change_feed_service import ChangeFeedService

ChangeFeedRouteHandler = Callable[[dict[str, Any] | None], dict[str, Any]]

CHANGE_FEED_BOOTSTRAP_ROUTE = "/api/change_feed/bootstrap"
CHANGE_FEED_READINESS_ROUTE = "/api/change_feed/readiness"
CHANGE_FEED_READ_ROUTE = "/api/change_feed/read"
CHANGE_FEED_ACK_ROUTE = "/api/change_feed/ack"
CHANGE_FEED_REGISTER_ROUTE = "/api/change_feed/register"
CHANGE_FEED_SUMMARIZE_ROUTE = "/api/change_feed/summarize"
AUTOMATION_CHANGE_FEED_ROUTES = frozenset(
    {
        CHANGE_FEED_BOOTSTRAP_ROUTE,
        CHANGE_FEED_READINESS_ROUTE,
        CHANGE_FEED_READ_ROUTE,
        CHANGE_FEED_ACK_ROUTE,
        CHANGE_FEED_REGISTER_ROUTE,
        CHANGE_FEED_SUMMARIZE_ROUTE,
    }
)


def build_change_feed_routes(
    service: ChangeFeedService,
) -> dict[str, ChangeFeedRouteHandler]:
    """Keep the feed API isolated from legacy routes and the 24-tool MCP facade."""

    return {
        CHANGE_FEED_BOOTSTRAP_ROUTE: service.bootstrap,
        CHANGE_FEED_READINESS_ROUTE: service.readiness,
        CHANGE_FEED_READ_ROUTE: service.read,
        CHANGE_FEED_ACK_ROUTE: service.ack,
        CHANGE_FEED_REGISTER_ROUTE: service.register,
        CHANGE_FEED_SUMMARIZE_ROUTE: service.summarize,
    }
