from __future__ import annotations

import threading

from ..experimental_lifecycle import ExperimentalLifecycleAuthority
from .call_admission import McpCallAdmission
from .tool_execution import ToolExecutor


class ExperimentalMcpDrain:
    """Bind partial MCP tracking to one server; this is not a complete writer fence.

    The owner must first close and drain calls, then close and drain the executor.
    OAuth, detached Manager workers, external effects and other writers are not
    covered. No production factory opts in or exposes a control endpoint.
    """

    def __init__(self, *, lifecycle: ExperimentalLifecycleAuthority | None = None) -> None:
        self._lifecycle = lifecycle
        self._calls = McpCallAdmission(lifecycle=lifecycle)
        self._executor = ToolExecutor(lifecycle=lifecycle)
        self._lock = threading.Lock()
        self._bound = False

    @property
    def calls(self) -> McpCallAdmission:
        return self._calls

    @property
    def executor(self) -> ToolExecutor:
        return self._executor

    def bind_once(self) -> None:
        if self._lifecycle is not None:
            self._lifecycle._require_process()
        with self._lock:
            if self._bound:
                raise RuntimeError("mcp_drain_already_bound")
            self._bound = True
