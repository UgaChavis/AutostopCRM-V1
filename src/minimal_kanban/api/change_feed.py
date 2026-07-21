from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..services.change_feed_service import ChangeFeedService

ChangeFeedRouteHandler = Callable[[dict[str, Any] | None], dict[str, Any]]

CHANGE_FEED_BOOTSTRAP_ROUTE = "/api/change_feed/bootstrap"
CHANGE_FEED_READ_ROUTE = "/api/change_feed/read"
CHANGE_FEED_ACK_ROUTE = "/api/change_feed/ack"


def build_change_feed_routes(
    service: ChangeFeedService,
) -> dict[str, ChangeFeedRouteHandler]:
    """Keep the feed API isolated from legacy routes and the 24-tool MCP facade."""

    return {
        CHANGE_FEED_BOOTSTRAP_ROUTE: service.bootstrap,
        CHANGE_FEED_READ_ROUTE: service.read,
        CHANGE_FEED_ACK_ROUTE: service.ack,
    }
