from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_agent_runtime.py"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent import config as agent_config  # noqa: E402
from minimal_kanban.agent.control import AgentControlService  # noqa: E402
from minimal_kanban.agent.storage import AgentStorage  # noqa: E402
from minimal_kanban.models import parse_datetime, utc_now  # noqa: E402


def _load_script_module():
    spec = importlib.util.spec_from_file_location("check_agent_runtime_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._body
        return self._body[:size]


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

    def test_request_json_rejects_nonstandard_json_constants(self) -> None:
        with patch.object(
            self.module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(b'{"ok": true, "data": NaN}'),
        ):
            with self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"):
                self.module._request_json("http://127.0.0.1:41731/api/agent_status")

    def test_request_json_rejects_deeply_nested_response(self) -> None:
        deep_json = ("[" * 5000 + "0" + "]" * 5000).encode("utf-8")

        with patch.object(
            self.module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(deep_json),
        ):
            with self.assertRaisesRegex(ValueError, "API response JSON is too deeply nested"):
                self.module._request_json("http://127.0.0.1:41731/api/agent_status")

    def test_request_json_rejects_oversized_response(self) -> None:
        with (
            patch.object(self.module, "CHECK_AGENT_RUNTIME_RESPONSE_MAX_BYTES", 4),
            patch.object(
                self.module,
                "_urlopen_no_redirect",
                return_value=FakeResponse(b"12345"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "API response is too large"):
                self.module._request_json("http://127.0.0.1:41731/api/agent_status")

    def test_request_json_rejects_redirect_response(self) -> None:
        redirect = self.module.urllib.error.HTTPError(
            url="http://127.0.0.1:41731/api/login_operator",
            code=302,
            msg="Found",
            hdrs={"Location": "https://example.test/login"},
            fp=None,
        )

        with patch.object(self.module, "_urlopen_no_redirect", side_effect=redirect):
            with self.assertRaisesRegex(ValueError, "API response redirected"):
                self.module._request_json(
                    "http://127.0.0.1:41731/api/login_operator",
                    method="POST",
                    payload={"username": "admin", "password": "secret"},
                )

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

    def test_heartbeat_age_cli_limit_is_bounded(self) -> None:
        self.assertEqual(self.module._bounded_heartbeat_age_seconds(float("inf")), 30.0)
        self.assertEqual(self.module._bounded_heartbeat_age_seconds("bad"), 30.0)
        self.assertEqual(self.module._bounded_heartbeat_age_seconds(0), 1.0)
        self.assertEqual(self.module._bounded_heartbeat_age_seconds(1e308), 86_400.0)

    def test_main_requires_explicit_operator_credentials(self) -> None:
        output = StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "AUTOSTOP_SMOKE_OPERATOR_USERNAME": "",
                    "AUTOSTOP_SMOKE_OPERATOR_PASSWORD": "",
                },
                clear=False,
            ),
            patch.object(self.module, "_login") as login,
            redirect_stdout(output),
        ):
            exit_code = self.module.main([])

        self.assertEqual(exit_code, 1)
        self.assertIn("missing_operator_credentials", output.getvalue())
        login.assert_not_called()

    def test_main_uses_smoke_operator_environment_credentials(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "AUTOSTOP_SMOKE_OPERATOR_USERNAME": "smoke-admin",
                    "AUTOSTOP_SMOKE_OPERATOR_PASSWORD": "smoke-secret",
                },
                clear=False,
            ),
            patch.object(self.module, "_login", return_value="session-token") as login,
            patch.object(
                self.module,
                "_evaluate_agent_runtime_mode",
                return_value=("api_only", {"api_url": "http://127.0.0.1:41731", "reason": "x"}),
            ),
            redirect_stdout(StringIO()),
        ):
            exit_code = self.module.main([])

        self.assertEqual(exit_code, 0)
        login.assert_called_once_with(
            "http://127.0.0.1:41731",
            "smoke-admin",
            "smoke-secret",
        )


class AgentControlServiceTests(unittest.TestCase):
    def test_close_retains_each_live_thread_until_a_retry_confirms_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")

            for attribute, role in (
                ("_scheduler_thread", "scheduler"),
                ("_worker_thread", "worker"),
            ):
                with self.subTest(role=role):
                    control = AgentControlService(storage)
                    thread = Mock()
                    thread.is_alive.side_effect = [True, True, True, False]
                    setattr(control, attribute, thread)

                    with self.assertRaisesRegex(RuntimeError, role):
                        control.close()

                    self.assertTrue(control._scheduler_stop.is_set())
                    self.assertTrue(control._worker_stop.is_set())
                    self.assertIs(thread, getattr(control, attribute))

                    control.close()

                    self.assertIsNone(getattr(control, attribute))
                    self.assertEqual(thread.join.call_count, 2)

    def test_storage_constructor_falls_back_for_invalid_retention_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(
                base_dir=Path(temp_dir) / "agent",
                max_finished_tasks=True,
                max_runs=float("inf"),
                max_actions=1.5,
                compact_threshold_bytes=False,
            )

            self.assertEqual(storage._max_finished_tasks, 300)
            self.assertEqual(storage._max_runs, 1000)
            self.assertEqual(storage._max_actions, 4000)
            self.assertEqual(storage._compact_threshold_bytes, 262_144)

            bounded = AgentStorage(
                base_dir=Path(temp_dir) / "bounded-agent",
                max_finished_tasks=1e308,
                max_runs=1e308,
                max_actions=1e308,
                compact_threshold_bytes=1e308,
            )

            self.assertEqual(bounded._max_finished_tasks, 10_000)
            self.assertEqual(bounded._max_runs, 20_000)
            self.assertEqual(bounded._max_actions, 100_000)
            self.assertEqual(bounded._compact_threshold_bytes, 2 * 1024 * 1024)

    def test_storage_finish_task_normalizes_invalid_tool_call_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")
            task = storage.enqueue_task(task_text="Проверить карточку")
            claimed = storage.claim_next_task()
            self.assertIsNotNone(claimed)

            completed = storage.complete_task(
                task_id=task["id"],
                run_id="run-1",
                summary="ok",
                result="done",
                display={},
                tool_calls=float("inf"),  # type: ignore[arg-type]
            )

            self.assertEqual(completed["tool_calls"], 0)
            self.assertEqual(storage.list_tasks()[0]["tool_calls"], 0)

            second = storage.enqueue_task(task_text="Проверить вторую карточку")
            claimed = storage.claim_next_task()
            self.assertIsNotNone(claimed)
            bounded = storage.complete_task(
                task_id=second["id"],
                run_id="run-2",
                summary="ok",
                result="done",
                display={},
                tool_calls=1e308,  # type: ignore[arg-type]
            )

            self.assertEqual(bounded["tool_calls"], 1_000_000)

    def test_storage_writes_json_without_non_finite_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)

            storage.update_status(
                board_control={
                    "written_count": float("inf"),
                    "recent_traces": [{"score": float("nan")}],
                }
            )
            storage.append_action({"payload": {"score": float("inf"), "items": [float("nan")]}})

            status_text = (base_dir / "status.json").read_text(encoding="utf-8")
            actions_text = (base_dir / "actions.jsonl").read_text(encoding="utf-8")

            self.assertNotIn("Infinity", status_text + actions_text)
            self.assertNotIn("NaN", status_text + actions_text)
            status_payload = json.loads(status_text)
            action_payload = json.loads(actions_text.strip())
            self.assertIsNone(status_payload["board_control"]["written_count"])
            self.assertIsNone(status_payload["board_control"]["recent_traces"][0]["score"])
            self.assertIsNone(action_payload["payload"]["score"])
            self.assertEqual(action_payload["payload"]["items"], [None])

    def test_storage_compacts_oversized_jsonl_records_before_append(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)

            with (
                patch("minimal_kanban.agent.storage.AGENT_JSONL_LINE_MAX_BYTES", 1024),
                patch("minimal_kanban.agent.storage.AGENT_JSONL_PREVIEW_MAX_CHARS", 80),
            ):
                storage.append_action(
                    {
                        "id": "action-huge",
                        "task_id": "task-1",
                        "run_id": "run-1",
                        "kind": "tool",
                        "tool": "update_card",
                        "args": {"description": "x" * 5000},
                        "result_preview": "ok",
                    }
                )
                storage.append_run(
                    {
                        "id": "run-huge",
                        "task_id": "task-1",
                        "status": "completed",
                        "orchestration": {"trace": "x" * 5000},
                    }
                )

            actions_text = (base_dir / "actions.jsonl").read_text(encoding="utf-8")
            runs_text = (base_dir / "runs.jsonl").read_text(encoding="utf-8")
            actions = storage.list_actions(limit=10)
            runs = storage.list_runs(limit=10)

            self.assertLess(len(actions_text.encode("utf-8")), 1024)
            self.assertLess(len(runs_text.encode("utf-8")), 1024)
            self.assertEqual(actions[0]["id"], "action-huge")
            self.assertEqual(runs[0]["id"], "run-huge")
            self.assertTrue(actions[0]["truncated"])
            self.assertTrue(runs[0]["truncated"])
            self.assertIn("args_preview", actions[0])
            self.assertIn("orchestration_preview", runs[0])
            self.assertGreater(actions[0]["original_bytes"], 1024)
            self.assertGreater(runs[0]["original_bytes"], 1024)

    def test_storage_json_write_does_not_overwrite_existing_fixed_tmp_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            fixed_tmp = base_dir / "status.json.tmp"
            fixed_tmp.write_text("sentinel", encoding="utf-8")

            storage.update_status(last_error="updated")

            self.assertEqual(fixed_tmp.read_text(encoding="utf-8"), "sentinel")

    def test_storage_json_write_rejects_payload_larger_than_read_limit_without_clobbering(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            status_file = base_dir / "status.json"
            original = status_file.read_text(encoding="utf-8")

            with patch("minimal_kanban.agent.storage.AGENT_JSON_FILE_MAX_BYTES", 128):
                with self.assertRaisesRegex(ValueError, "agent JSON state file is too large"):
                    storage._write_json(status_file, {"padding": "x" * 512})

            self.assertEqual(status_file.read_text(encoding="utf-8"), original)
            self.assertEqual(list(base_dir.glob("*.tmp")), [])

    def test_storage_text_write_keeps_existing_file_when_temp_write_fails(self) -> None:
        original_write_text = Path.write_text

        def partial_temp_write(path: Path, data: str, *args, **kwargs) -> int:
            original_write_text(path, "partial", *args, **kwargs)
            raise OSError("disk full")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            storage.write_prompt_text("old")

            with (
                patch.object(Path, "write_text", partial_temp_write),
                self.assertRaises(OSError),
            ):
                storage.write_prompt_text("new")

            self.assertEqual(storage.read_prompt_text(), "old")
            self.assertEqual(list(base_dir.glob("*.tmp")), [])

    def test_storage_prompt_write_rejects_payload_larger_than_read_limit_without_clobbering(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            storage.write_prompt_text("old prompt")

            with patch("minimal_kanban.agent.storage.AGENT_TEXT_FILE_MAX_BYTES", 8):
                with self.assertRaisesRegex(ValueError, "agent text file is too large"):
                    storage.write_prompt_text("x" * 64)

            self.assertEqual(storage.read_prompt_text(), "old prompt")
            self.assertEqual(list(base_dir.glob("*.tmp")), [])

    def test_storage_backs_up_oversized_prompt_text_before_resetting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            prompt_file = base_dir / "system_prompt.md"
            prompt_file.write_text("old prompt text", encoding="utf-8")

            with patch("minimal_kanban.agent.storage.AGENT_TEXT_FILE_MAX_BYTES", 8):
                self.assertEqual(storage.read_prompt_text(), "")

            backup = base_dir / "system_prompt.corrupted.md"
            self.assertEqual(backup.read_text(encoding="utf-8"), "old prompt text")
            self.assertEqual(prompt_file.read_text(encoding="utf-8"), "")

    def test_storage_backs_up_corrupted_tasks_json_before_resetting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            tasks_file = base_dir / "tasks.json"
            tasks_file.write_text("{broken", encoding="utf-8")

            self.assertEqual(storage.list_tasks(), [])

            backup = base_dir / "tasks.corrupted.json"
            self.assertEqual(backup.read_text(encoding="utf-8"), "{broken")
            self.assertEqual(tasks_file.read_text(encoding="utf-8").strip(), "[]")

    def test_storage_backs_up_oversized_tasks_json_before_resetting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            tasks_file = base_dir / "tasks.json"
            tasks_file.write_text('[{"id":"bad","padding":"xxxxxxxx"}]', encoding="utf-8")

            with patch("minimal_kanban.agent.storage.AGENT_JSON_FILE_MAX_BYTES", 8):
                self.assertEqual(storage.list_tasks(), [])

            backup = base_dir / "tasks.corrupted.json"
            self.assertIn("padding", backup.read_text(encoding="utf-8"))
            self.assertEqual(tasks_file.read_text(encoding="utf-8").strip(), "[]")

    def test_storage_backs_up_deeply_nested_tasks_json_before_resetting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            tasks_file = base_dir / "tasks.json"
            deep_json = "[" * 5000 + "]" * 5000
            tasks_file.write_text(deep_json, encoding="utf-8")

            self.assertEqual(storage.list_tasks(), [])

            backup = base_dir / "tasks.corrupted.json"
            self.assertEqual(backup.read_text(encoding="utf-8"), deep_json)
            self.assertEqual(tasks_file.read_text(encoding="utf-8").strip(), "[]")

    def test_storage_backs_up_wrong_root_json_type_before_resetting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            status_file = base_dir / "status.json"
            status_file.write_text("[]", encoding="utf-8")

            status = storage.read_status()

            backup = base_dir / "status.corrupted.json"
            self.assertFalse(status["running"])
            self.assertEqual(backup.read_text(encoding="utf-8"), "[]")
            self.assertIn('"running": false', status_file.read_text(encoding="utf-8"))

    def test_storage_backs_up_nonstandard_json_constants_before_resetting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            tasks_file = base_dir / "tasks.json"
            tasks_file.write_text('[{"id":"bad","created_at":NaN}]', encoding="utf-8")

            self.assertEqual(storage.list_tasks(limit=10), [])

            backup = base_dir / "tasks.corrupted.json"
            self.assertIn("NaN", backup.read_text(encoding="utf-8"))
            self.assertEqual(tasks_file.read_text(encoding="utf-8").strip(), "[]")

    def test_storage_skips_jsonl_rows_with_nonstandard_constants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            actions_file = base_dir / "actions.jsonl"
            actions_file.write_text(
                '{"id":"bad","score":NaN}\n'
                + json.dumps({"id": "good", "score": 1}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(storage.list_actions(limit=10), [{"id": "good", "score": 1}])

    def test_storage_ignores_non_dict_task_and_schedule_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            (base_dir / "tasks.json").write_text(
                '[1, {"id": "task-1", "created_at": "2026-01-01T00:00:00Z"}]',
                encoding="utf-8",
            )
            (base_dir / "schedules.json").write_text(
                '[false, {"id": "schedule-1", "created_at": "2026-01-01T00:00:00Z"}]',
                encoding="utf-8",
            )

            self.assertEqual([item["id"] for item in storage.list_tasks(limit=10)], ["task-1"])
            self.assertEqual(
                [item["id"] for item in storage.list_schedules()],
                ["schedule-1"],
            )
            self.assertEqual(storage.get_schedule("schedule-1")["id"], "schedule-1")

    def test_storage_backs_up_non_utf8_jsonl_before_returning_empty_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            runs_file = base_dir / "runs.jsonl"
            runs_file.write_bytes(b"\xff")

            self.assertEqual(storage.list_runs(limit=10), [])

            backup = base_dir / "runs.corrupted.jsonl"
            self.assertEqual(backup.read_bytes(), b"\xff")
            self.assertEqual(runs_file.read_text(encoding="utf-8"), "")

    def test_storage_reads_only_bounded_tail_of_oversized_jsonl_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "agent"
            storage = AgentStorage(base_dir=base_dir)
            runs_file = base_dir / "runs.jsonl"
            runs_file.write_text(
                json.dumps({"id": "old", "padding": "x" * 300}, ensure_ascii=False)
                + "\n"
                + json.dumps({"id": "recent-1"}, ensure_ascii=False)
                + "\n"
                + json.dumps({"id": "recent-2"}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )

            with patch("minimal_kanban.agent.storage.AGENT_JSONL_TAIL_MAX_BYTES", 128):
                runs = storage.list_runs(limit=10)

            self.assertEqual([item["id"] for item in runs], ["recent-2", "recent-1"])

    def test_control_constructor_falls_back_for_invalid_scheduler_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")

            control = AgentControlService(storage, scheduler_interval_seconds=float("inf"))

            self.assertEqual(control._scheduler_interval_seconds, 20.0)

    def test_control_limits_fall_back_for_non_finite_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")
            control = AgentControlService(storage)

            tasks = control.agent_tasks({"limit": float("inf")})
            created = control.save_agent_scheduled_task(
                {
                    "name": "Bad interval",
                    "prompt": "Проверь доску",
                    "scope_type": "all_cards",
                    "schedule_type": "interval",
                    "interval_value": float("inf"),
                    "active": False,
                }
            )["task"]
            clamped = control.save_agent_scheduled_task(
                {
                    "name": "Huge interval",
                    "prompt": "Проверь доску",
                    "scope_type": "all_cards",
                    "schedule_type": "interval",
                    "interval_value": 1e308,
                    "active": False,
                }
            )["task"]

            self.assertEqual(tasks["meta"]["limit"], 50)
            self.assertEqual(created["interval_value"], 1)
            self.assertEqual(clamped["interval_value"], 525_600)
            self.assertEqual(control._bounded_int({}, default=7, minimum=1, maximum=10), 7)
            self.assertEqual(
                control._bounded_int(1e308, default=7, minimum=0),
                1_000_000_000,
            )
            self.assertEqual(control._normalize_seconds([], default=2.0, minimum=0.2), 2.0)
            self.assertEqual(control._normalize_seconds(1e308, default=2.0, minimum=0.2), 3600.0)

    def test_board_control_settings_fall_back_for_invalid_numeric_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")
            control = AgentControlService(storage)

            class BadBoardService:
                def get_ai_board_control_settings(self):
                    return {
                        "enabled": True,
                        "interval_minutes": {},
                        "cooldown_minutes": [],
                    }

            control.bind_board_service(BadBoardService())

            settings = control._board_control_settings()

            self.assertTrue(settings["enabled"])
            self.assertEqual(settings["interval_minutes"], 20)
            self.assertEqual(settings["cooldown_minutes"], 60)

    def test_board_control_status_falls_back_for_invalid_counters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")
            control = AgentControlService(storage)

            status = control._board_control_status_payload(
                {
                    "board_control": {
                        "considered_count": float("inf"),
                        "triggered_count": "bad",
                        "enqueued_count": -5,
                    }
                }
            )

            self.assertEqual(status["considered_count"], 0)
            self.assertEqual(status["triggered_count"], 0)
            self.assertEqual(status["enqueued_count"], 0)

    def test_board_control_runtime_ignores_malformed_trace_and_cache_containers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")
            control = AgentControlService(storage)

            runtime = control._board_control_runtime(
                {"board_control": {"recent_traces": "bad", "card_cache": ["bad"]}}
            )
            persisted = control._persist_board_control_runtime(
                {"recent_traces": "bad", "card_cache": ["bad"]}
            )
            control._append_board_control_trace(runtime, {"card_id": "card-1"})

            self.assertEqual(persisted["recent_traces"], [])
            self.assertEqual(persisted["card_cache"], {})
            self.assertEqual(runtime["recent_traces"], [{"card_id": "card-1"}])

    def test_board_control_counter_increment_normalizes_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")
            control = AgentControlService(storage)
            runtime = {"triggered_count": float("inf"), "enqueued_count": True}

            control._increment_board_control_counter(runtime, "triggered_count")
            control._increment_board_control_counter(runtime, "enqueued_count")
            runtime["written_count"] = 1e308
            control._increment_board_control_counter(runtime, "written_count")

            self.assertEqual(runtime["triggered_count"], 1)
            self.assertEqual(runtime["enqueued_count"], 1)
            self.assertEqual(runtime["written_count"], 1_000_000_000)

    def test_board_control_task_treats_string_trigger_reason_as_one_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")
            control = AgentControlService(storage)

            task = control.enqueue_board_control_task(
                {"card_id": "card-1", "trigger_reasons": "new_card"}
            )

            assert task is not None
            self.assertEqual(
                task["metadata"]["board_control"]["trigger_reasons"],
                ["new_card"],
            )
            self.assertIn("Trigger rules: new_card.", task["task_text"])

    def test_agent_float_config_rejects_non_finite_environment_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MINIMAL_KANBAN_AGENT_REQUEST_TIMEOUT_SECONDS": "inf",
                "MINIMAL_KANBAN_AGENT_POLL_INTERVAL_SECONDS": "nan",
                "MINIMAL_KANBAN_AGENT_MAX_STEPS": "1e308",
                "MINIMAL_KANBAN_AGENT_MAX_TOOL_RESULT_CHARS": "1e308",
            },
            clear=False,
        ):
            self.assertEqual(
                agent_config.get_agent_request_timeout_seconds(),
                agent_config.DEFAULT_REQUEST_TIMEOUT_SECONDS,
            )
            self.assertEqual(agent_config.get_agent_poll_interval_seconds(), 2.0)
            self.assertEqual(agent_config.get_agent_max_steps(), 200)
            self.assertEqual(agent_config.get_agent_max_tool_result_chars(), 200_000)

        with patch.dict(
            os.environ,
            {
                "MINIMAL_KANBAN_AGENT_REQUEST_TIMEOUT_SECONDS": "1e308",
                "MINIMAL_KANBAN_AGENT_POLL_INTERVAL_SECONDS": "1e308",
            },
            clear=False,
        ):
            self.assertEqual(agent_config.get_agent_request_timeout_seconds(), 120.0)
            self.assertEqual(agent_config.get_agent_poll_interval_seconds(), 60.0)

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
                    self.started = False

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
