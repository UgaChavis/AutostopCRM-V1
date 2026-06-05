from __future__ import annotations

import importlib.util
import logging
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_agent_runtime.py"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.control import AgentControlService  # noqa: E402
from minimal_kanban.agent.storage import AgentStorage  # noqa: E402
from minimal_kanban.models import parse_datetime, utc_now  # noqa: E402


def _load_script_module():
    spec = importlib.util.spec_from_file_location("check_agent_runtime_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CheckAgentRuntimeScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script_module()

    def test_returns_api_only_when_agent_status_route_is_retired(self) -> None:
        http_404 = self.module.urllib.error.HTTPError(
            url="http://127.0.0.1:41731/api/agent_status",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )
        with patch.object(self.module, "_request_json", side_effect=http_404):
            status, details = self.module._evaluate_agent_runtime_mode(
                base_url="http://127.0.0.1:41731",
                token="token",
                max_heartbeat_age_seconds=30.0,
            )

        self.assertEqual(status, "api_only")
        self.assertEqual(details["reason"], "agent_status_route_retired")

    def test_returns_api_only_when_embedded_agent_is_disabled(self) -> None:
        payload = {
            "data": {
                "status": {"last_heartbeat": ""},
                "agent": {"enabled": False, "model": ""},
            }
        }
        with patch.object(self.module, "_request_json", return_value=payload):
            status, details = self.module._evaluate_agent_runtime_mode(
                base_url="http://127.0.0.1:41731",
                token="token",
                max_heartbeat_age_seconds=30.0,
            )

        self.assertEqual(status, "api_only")
        self.assertEqual(details["reason"], "embedded_agent_disabled")

    def test_returns_ok_for_live_embedded_agent(self) -> None:
        payload = {
            "data": {
                "status": {"last_heartbeat": "2026-04-16T08:00:00+00:00"},
                "agent": {"enabled": True, "model": "gpt-test"},
            }
        }
        with (
            patch.object(self.module, "_request_json", return_value=payload),
            patch.object(
                self.module,
                "_heartbeat_age_seconds",
                return_value=5.0,
            ),
        ):
            status, details = self.module._evaluate_agent_runtime_mode(
                base_url="http://127.0.0.1:41731",
                token="token",
                max_heartbeat_age_seconds=30.0,
            )

        self.assertEqual(status, "ok")
        self.assertEqual(details["heartbeat_age_seconds"], "5.00")
        self.assertEqual(details["model"], "gpt-test")


class AgentControlServiceTests(unittest.TestCase):
    def test_interval_schedule_reschedules_from_new_enqueue_time_after_overdue_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")
            control = AgentControlService(storage)
            created = control.save_agent_scheduled_task(
                {
                    "name": "Interval check",
                    "prompt": "Проверь доску",
                    "scope_type": "all_cards",
                    "schedule_type": "interval",
                    "interval_value": 5,
                    "interval_unit": "minute",
                    "active": True,
                }
            )["task"]
            schedule_id = created["id"]
            overdue_at = (utc_now() - timedelta(hours=1)).isoformat()
            storage.update_schedule(
                schedule_id,
                active=True,
                last_enqueued_at=overdue_at,
                next_run_at=overdue_at,
            )

            result = control.trigger_scheduled_tasks(force=True)

            self.assertEqual(result["launched"], [schedule_id])
            updated = storage.get_schedule(schedule_id)
            self.assertIsNotNone(updated)
            assert updated is not None
            last_enqueued_at = parse_datetime(str(updated["last_enqueued_at"]))
            next_run_at = parse_datetime(str(updated["next_run_at"]))
            self.assertIsNotNone(last_enqueued_at)
            self.assertIsNotNone(next_run_at)
            assert last_enqueued_at is not None
            assert next_run_at is not None
            self.assertEqual(next_run_at - last_enqueued_at, timedelta(minutes=5))
            self.assertGreater(next_run_at, utc_now())

    def test_save_once_schedule_returns_paused_task_after_immediate_enqueue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")
            control = AgentControlService(storage)

            created = control.save_agent_scheduled_task(
                {
                    "name": "Once check",
                    "prompt": "Проверь один раз",
                    "scope_type": "all_cards",
                    "schedule_type": "once",
                    "active": True,
                }
            )["task"]

            self.assertFalse(created["active"])
            self.assertEqual(created["status"], "paused")
            self.assertEqual(created["next_run_at"], "")
            self.assertTrue(created["busy"])

    def test_start_worker_uses_configured_board_api_url_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")
            control = AgentControlService(storage)

            class DummyThread:
                def __init__(self, target, args, name, daemon) -> None:
                    self.target = target
                    self.args = args
                    self.name = name
                    self.daemon = daemon
                    self.started = False

                def is_alive(self) -> bool:
                    return self.started

                def start(self) -> None:
                    self.started = True

                def join(self, timeout=None) -> None:  # noqa: ANN001
                    _ = timeout

            created_threads: list[DummyThread] = []

            def make_thread(*, target, args, name, daemon):
                thread = DummyThread(target, args, name, daemon)
                created_threads.append(thread)
                return thread

            with (
                patch(
                    "minimal_kanban.agent.control.get_agent_enabled",
                    return_value=True,
                ),
                patch(
                    "minimal_kanban.agent.control.get_agent_openai_api_key",
                    return_value="sk-test",
                ),
                patch(
                    "minimal_kanban.agent.control.get_agent_board_api_url",
                    return_value="http://127.0.0.1:41731",
                ),
                patch(
                    "minimal_kanban.agent.control.threading.Thread",
                    side_effect=make_thread,
                ),
            ):
                started = control.start_worker(
                    logger=logging.getLogger("test.agent.worker"),
                    board_api_url="http://127.0.0.1:41731",
                )

            self.assertTrue(started)
            self.assertEqual(len(created_threads), 1)
            self.assertEqual(created_threads[0].args[1], "http://127.0.0.1:41731")
            control.close()


if __name__ == "__main__":
    unittest.main()
