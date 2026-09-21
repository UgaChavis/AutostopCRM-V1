from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..services.automation_center_service import AutomationCenterService

AutomationCenterRouteHandler = Callable[[dict[str, Any] | None], dict[str, Any]]

AUTOMATION_CENTER_STATUS_ROUTE = "/api/automation_center/status"
AUTOMATION_CENTER_CONTROL_ROUTE = "/api/automation_center/control"


def build_automation_center_routes(
    service: AutomationCenterService,
) -> dict[str, AutomationCenterRouteHandler]:
    return {
        AUTOMATION_CENTER_STATUS_ROUTE: service.status,
        AUTOMATION_CENTER_CONTROL_ROUTE: service.control,
    }
