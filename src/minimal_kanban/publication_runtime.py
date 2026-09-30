from __future__ import annotations

from threading import Condition

from .models import utc_now_iso
from .settings_service import ConnectionCheckResult


class PublicationOperationGate:
    """Reserve the desktop's process pair, and drain it before resource cleanup."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._busy = False
        self._closing = False

    @property
    def busy(self) -> bool:
        with self._condition:
            return self._busy or self._closing

    def begin(self) -> bool:
        with self._condition:
            if self._busy or self._closing:
                return False
            self._busy = True
            return True

    def finish(self) -> None:
        with self._condition:
            self._busy = False
            self._condition.notify_all()

    def close_and_wait(self) -> None:
        with self._condition:
            self._closing = True
            self._condition.wait_for(lambda: not self._busy)


def publication_operation_gate(controller) -> PublicationOperationGate:
    # Look in the instance dictionary so fake/mock controllers use the same gate
    # without accidentally manufacturing it through dynamic attribute lookup.
    gate = vars(controller).get("_publication_operation_gate")
    if gate is None:
        gate = PublicationOperationGate()
        controller._publication_operation_gate = gate
    return gate


class PublicationRuntime:
    """Process IO and settings reconciliation, independent of widget lifetime."""

    def __init__(self, service, mcp, tunnel=None) -> None:
        self.service = service
        self.mcp = mcp
        self.tunnel = tunnel

    def start_automatic(self, settings):
        state = self._start_mcp(settings)
        if state.running and self._needs_tunnel(settings):
            settings = self._start_tunnel(settings)
            # The automatic path establishes local MCP before publishing it.
            state = self.mcp.restart(settings)
        return settings, state

    def run(self, action, settings):
        if action == "automatic":
            return self.start_automatic(settings)
        if action in {"restart", "stop"}:
            if self.tunnel is not None:
                self.tunnel.stop()
            state = self.mcp.stop()
        if action == "stop":
            settings = self.service.save(
                self.service.update_section(
                    "mcp", {"tunnel_url": ""}, settings=settings, persist=False
                )
            )
            status = "warning"
        else:
            # Restart retains stop-before-validation/save semantics. The form
            # snapshot was collected on the GUI thread before starting the IO.
            settings = self.service.save(settings)
            if not settings.mcp.mcp_enabled:
                return settings, None
            if self._needs_tunnel(settings):
                settings = self._start_tunnel(settings)
            state = self._start_mcp(settings)
            status = "success" if state.running else "failed"
        result = ConnectionCheckResult(
            "mcp",
            status,
            state.message,
            utc_now_iso(),
            errors=(state.error,) if state.error else (),
        )
        settings = self.service.apply_test_result(settings, "mcp", result, persist=True)
        return settings, state

    def _needs_tunnel(self, settings) -> bool:
        return (
            self.tunnel is not None
            and not settings.mcp.full_mcp_url_override
            and not settings.mcp.public_https_base_url
        )

    def _start_tunnel(self, settings):
        state = self.tunnel.start(settings)
        return self.service.update_section(
            "mcp", {"tunnel_url": state.public_url if state.running else ""}, persist=True
        )

    def _start_mcp(self, settings):
        return self.mcp.restart(settings) if self.mcp.state.running else self.mcp.start(settings)
