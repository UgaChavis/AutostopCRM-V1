from __future__ import annotations

from typing import Any

from .control import AgentControlService
from .storage import AgentStorage


def start_embedded_agent_runtime(
    *,
    service: Any | None,
    logger: Any,
    board_api_url: str | None,
    api_bearer_token: str | None = None,
) -> AgentControlService:
    agent_storage = AgentStorage()
    agent_control = AgentControlService(agent_storage)
    if service is not None and hasattr(service, "attach_agent_control"):
        service.attach_agent_control(agent_control)
    agent_control.bind_board_service(service)
    agent_started = agent_control.start_worker(
        logger=logger, board_api_url=board_api_url, api_bearer_token=api_bearer_token
    )
    if agent_started:
        logger.info("embedded_agent_worker_started board_api_url=%s", board_api_url or "")
    else:
        logger.info("embedded_agent_worker_not_started")
    return agent_control
