from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.integration_runtime import McpRuntimeController  # noqa: E402
from minimal_kanban.settings_models import IntegrationSettings  # noqa: E402


class McpRuntimeControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        logger = logging.getLogger(f"test.integration.runtime.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        self.controller = McpRuntimeController(
            board_api_url="http://127.0.0.1:41731",
            logger=logger,
        )
        self.settings = IntegrationSettings.defaults()

    def test_failed_start_retains_runtime_when_cleanup_is_uncertain(self) -> None:
        runtime = Mock()
        runtime.start.side_effect = RuntimeError("start failed")
        runtime.stop.side_effect = RuntimeError("stop failed")

        with (
            patch("minimal_kanban.integration_runtime.BoardApiClient"),
            patch("minimal_kanban.integration_runtime.create_mcp_server"),
            patch(
                "minimal_kanban.integration_runtime.McpServerRuntime",
                return_value=runtime,
            ),
        ):
            state = self.controller.start(self.settings)

        self.assertFalse(state.running)
        runtime.stop.assert_called_once_with()
        self.assertIs(runtime, self.controller._runtime)

    def test_failed_stop_keeps_retry_handle_until_success(self) -> None:
        runtime = Mock()
        runtime.stop.side_effect = RuntimeError("stop failed")
        self.controller._runtime = runtime

        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            self.controller.stop()

        self.assertIs(runtime, self.controller._runtime)
        runtime.stop.side_effect = None
        state = self.controller.stop()
        self.assertFalse(state.running)
        self.assertIsNone(self.controller._runtime)


if __name__ == "__main__":
    unittest.main()
