from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import JsonRouteDispatcher, _success_log_level  # noqa: E402
from minimal_kanban.performance import MeasuredRLock  # noqa: E402


class ApiRequestTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.route = "/api/get_board_revision"
        self.logger = logging.getLogger("test.api_request_timing")
        lock = MeasuredRLock("service_lock")

        def read_revision(_payload: dict) -> dict:
            with lock:
                return {"revision": "private-synthetic-result"}

        self.handler = Mock()
        self.handler.ROUTE_SPECS = {self.route: SimpleNamespace(path=self.route)}
        self.handler.ROUTES = {self.route: read_revision}
        self.handler._prepare_response_body.side_effect = lambda body, **_kwargs: (body, {})
        self.handler._write_body.return_value = True
        policy = Mock()
        policy.operator_context_payload.side_effect = lambda *_args, **_kwargs: {}
        self.dispatcher = JsonRouteDispatcher(
            service=Mock(), logger=self.logger, authentication_policy=policy
        )

    def dispatch(self) -> None:
        self.dispatcher.dispatch(self.handler, self.route, "synthetic-request", {})

    def assert_timings(self, message: str) -> None:
        self.assertIn("duration_ms=1250.0", message)
        self.assertIn("service_lock_ms=200.0", message)
        self.assertIn("service_lock_hold_ms=800.0", message)
        self.assertNotIn("private-synthetic-result", message)

    def test_only_slow_quiet_successes_are_promoted_to_info(self) -> None:
        self.assertEqual(_success_log_level(self.route, 999.9), logging.DEBUG)
        self.assertEqual(_success_log_level(self.route, 1000.0), logging.INFO)
        self.assertEqual(_success_log_level("/api/create_card", 1.0), logging.INFO)
        with (
            patch("minimal_kanban.api.server.perf_counter", side_effect=[0.0, 1.25]),
            patch(
                "minimal_kanban.performance.perf_counter",
                side_effect=[0.0, 0.2, 0.2, 1.0],
            ),
            self.assertLogs(self.logger, level=logging.INFO) as logs,
        ):
            self.dispatch()
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelno, logging.INFO)
        self.assertIn("status=ok", logs.output[0])
        self.assert_timings(logs.output[0])

    def test_disconnected_body_keeps_delayed_lock_holder_timings(self) -> None:
        self.handler._write_body.return_value = False
        with (
            patch("minimal_kanban.api.server.perf_counter", side_effect=[0.0, 1.25]),
            patch(
                "minimal_kanban.performance.perf_counter",
                side_effect=[0.0, 0.2, 0.2, 1.0],
            ),
            self.assertLogs(self.logger, level=logging.WARNING) as logs,
        ):
            self.dispatch()
        self.assertEqual(len(logs.records), 1)
        self.assertIn("status=client_disconnected", logs.output[0])
        self.assert_timings(logs.output[0])
        self.handler._send_error_response.assert_not_called()

    def test_disconnection_during_headers_or_body_keeps_timings_without_second_response(
        self,
    ) -> None:
        for stage in ("_send_headers", "_write_body"):
            for error_type in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                with self.subTest(stage=stage, error_type=error_type.__name__):
                    self.handler._send_headers.side_effect = None
                    self.handler._write_body.side_effect = None
                    getattr(self.handler, stage).side_effect = error_type()
                    with (
                        patch(
                            "minimal_kanban.api.server.perf_counter",
                            side_effect=[0.0, 1.0, 1.25],
                        ),
                        patch(
                            "minimal_kanban.performance.perf_counter",
                            side_effect=[0.0, 0.2, 0.2, 1.0],
                        ),
                        self.assertLogs(self.logger, level=logging.WARNING) as logs,
                    ):
                        self.dispatch()
                    self.assertEqual(len(logs.records), 1)
                    self.assertIn("status=client_disconnected", logs.output[0])
                    self.assertIn("error_type=" + error_type.__name__, logs.output[0])
                    self.assert_timings(logs.output[0])
                    self.handler._send_error_response.assert_not_called()

    def test_connection_failure_inside_route_retains_application_error_response(self) -> None:
        self.handler.ROUTES[self.route] = Mock(side_effect=ConnectionResetError())
        with (
            patch("minimal_kanban.api.server.perf_counter", side_effect=[0.0, 1.25]),
            self.assertLogs(self.logger, level=logging.ERROR) as logs,
        ):
            self.dispatch()
        self.assertIn("api_request_failed", logs.output[0])
        self.assertNotIn("status=client_disconnected", logs.output[0])
        self.handler._send_error_response.assert_called_once()
        self.assertEqual(self.handler._send_error_response.call_args.args[2], "internal_error")


if __name__ == "__main__":
    unittest.main()
