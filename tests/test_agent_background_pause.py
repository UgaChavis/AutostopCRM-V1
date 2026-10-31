from __future__ import annotations

import logging
import threading
import time
import unittest
from unittest.mock import Mock, patch

from tests.source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.agent.control import AgentControlService  # noqa: E402


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class AgentBackgroundPauseTests(unittest.TestCase):
    def test_inactive_threads_stay_inactive_and_wrong_owner_cannot_resume(self) -> None:
        control = AgentControlService(Mock())
        owner = control.begin_background_pause()
        self.assertEqual(
            {"drained": True, "threads_unchanged": True, "backup_ready": False},
            control.background_pause_status(owner),
        )
        with self.assertRaisesRegex(RuntimeError, "start_blocked"):
            control.start_scheduler()
        with self.assertRaisesRegex(RuntimeError, "start_blocked"):
            control.start_worker(logger=Mock())
        with self.assertRaisesRegex(RuntimeError, "ownership_lost"):
            control.resume_background(object())
        self.assertTrue(control.resume_background(owner))
        self.assertIsNone(control._scheduler_thread)
        self.assertIsNone(control._worker_thread)
        with self.assertRaisesRegex(RuntimeError, "ownership_lost"):
            control.resume_background(owner)

    def test_worker_finishes_current_iteration_then_resumes_same_thread(self) -> None:
        storage = Mock()
        storage.read_prompt_text.return_value = "synthetic prompt"
        storage.read_memory_text.return_value = "synthetic memory"
        control = AgentControlService(storage)
        started, release, resumed = threading.Event(), threading.Event(), threading.Event()
        calls: list[int] = []

        def run_once() -> bool:
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                started.set()
                if not release.wait(3):
                    raise TimeoutError("test did not release worker")
            else:
                resumed.set()
            return False

        runner = Mock()
        runner.run_once.side_effect = run_once
        board = Mock()
        board.health.return_value = {"ok": True}
        owner = None
        try:
            with (
                patch("minimal_kanban.agent.control.get_agent_enabled", return_value=True),
                patch("minimal_kanban.agent.control.get_agent_openai_api_key", return_value="test"),
                patch(
                    "minimal_kanban.agent.control.get_agent_poll_interval_seconds", return_value=0.2
                ),
                patch("minimal_kanban.mcp.client.BoardApiClient", return_value=board),
                patch("minimal_kanban.agent.openai_client.OpenAIJsonAgentClient"),
                patch("minimal_kanban.agent.runner.AgentRunner", return_value=runner),
            ):
                self.assertTrue(
                    control.start_worker(
                        logger=logging.getLogger(__name__), board_api_url="http://test"
                    )
                )
                self.assertTrue(started.wait(1))
                original = control._worker_thread
                owner = control.begin_background_pause()
                self.assertFalse(control.background_pause_status(owner)["drained"])
                release.set()
                self.assertTrue(wait_for(lambda: control.background_pause_status(owner)["drained"]))
                self.assertTrue(control.background_pause_status(owner)["threads_unchanged"])
                self.assertFalse(resumed.wait(0.3))
                self.assertEqual([1], calls)
                self.assertTrue(control.resume_background(owner))
                owner = None
                self.assertTrue(resumed.wait(1))
                self.assertIs(original, control._worker_thread)
        finally:
            release.set()
            if owner is not None:
                control.resume_background(owner)
            control.close()

    def test_scheduler_pause_and_close_do_not_restart_a_thread(self) -> None:
        control = AgentControlService(Mock())
        control._scheduler_interval_seconds = 0.01
        started, release = threading.Event(), threading.Event()
        calls: list[int] = []

        def tick(*, force: bool) -> dict:
            self.assertTrue(force)
            calls.append(1)
            started.set()
            if not release.wait(3):
                raise TimeoutError("test did not release scheduler")
            return {}

        control.trigger_scheduled_tasks = tick
        owner = None
        try:
            control.start_scheduler()
            self.assertTrue(started.wait(1))
            original = control._scheduler_thread
            owner = control.begin_background_pause()
            self.assertFalse(control.background_pause_status(owner)["drained"])
            release.set()
            self.assertTrue(wait_for(lambda: control.background_pause_status(owner)["drained"]))
            self.assertTrue(control.background_pause_status(owner)["threads_unchanged"])
            time.sleep(0.05)
            self.assertEqual([1], calls)
            control.close()
            self.assertFalse(control.background_pause_status(owner)["threads_unchanged"])
            self.assertFalse(control.background_pause_status(owner)["drained"])
            self.assertFalse(original.is_alive())
            self.assertFalse(control.resume_background(owner))
            owner = None
            self.assertIsNone(control._scheduler_thread)
        finally:
            release.set()
            if owner is not None:
                control.resume_background(owner)
            control.close()

    def test_close_timeout_invalidates_pause_without_changing_retry_contract(self) -> None:
        control = AgentControlService(Mock())
        thread = Mock()
        thread.is_alive.return_value = True
        control._worker_thread = thread
        owner = control.begin_background_pause()
        with self.assertRaisesRegex(RuntimeError, "worker"):
            control.close()
        status = control.background_pause_status(owner)
        self.assertEqual(
            {"drained": False, "threads_unchanged": False, "backup_ready": False}, status
        )
        self.assertFalse(control.resume_background(owner))
        # Existing behavior: an already-live worker is returned, not duplicated.
        self.assertTrue(control.start_worker(logger=Mock()))
        self.assertIs(thread, control._worker_thread)
        thread.is_alive.return_value = False
        control.close()

    def test_interrupted_close_retains_thread_and_invalidates_pause(self) -> None:
        control = AgentControlService(Mock())
        thread = Mock()
        thread.is_alive.return_value = True
        thread.join.side_effect = KeyboardInterrupt
        control._worker_thread = thread
        owner = control.begin_background_pause()
        with self.assertRaises(KeyboardInterrupt):
            control.close()
        self.assertIs(thread, control._worker_thread)
        self.assertEqual(
            {"drained": False, "threads_unchanged": False, "backup_ready": False},
            control.background_pause_status(owner),
        )
        self.assertFalse(control.resume_background(owner))
        self.assertTrue(control.start_worker(logger=Mock()))
        self.assertIs(thread, control._worker_thread)
        thread.is_alive.return_value = False
        control.close()

    def test_resume_before_active_cycle_finishes_does_not_duplicate_it(self) -> None:
        control = AgentControlService(Mock())
        control._scheduler_interval_seconds = 0.01
        started, release, next_cycle = threading.Event(), threading.Event(), threading.Event()
        calls: list[int] = []

        def tick(*, force: bool) -> dict:
            calls.append(1)
            if len(calls) == 1:
                started.set()
                if not release.wait(3):
                    raise TimeoutError("test did not release scheduler")
            else:
                next_cycle.set()
            return {}

        control.trigger_scheduled_tasks = tick
        try:
            control.start_scheduler()
            self.assertTrue(started.wait(1))
            original = control._scheduler_thread
            owner = control.begin_background_pause()
            self.assertFalse(control.background_pause_status(owner)["drained"])
            self.assertTrue(control.resume_background(owner))
            control.start_scheduler()
            self.assertIs(original, control._scheduler_thread)
            self.assertFalse(next_cycle.wait(0.05))
            self.assertEqual([1], calls)
            release.set()
            self.assertTrue(next_cycle.wait(1))
            self.assertEqual([1, 1], calls[:2])
        finally:
            release.set()
            control.close()

    def test_worker_initialization_is_counted_before_drain(self) -> None:
        storage = Mock()
        started, release = threading.Event(), threading.Event()

        def read_prompt() -> str:
            started.set()
            if not release.wait(3):
                raise TimeoutError("test did not release initialization")
            return "synthetic prompt"

        storage.read_prompt_text.side_effect = read_prompt
        storage.read_memory_text.return_value = "synthetic memory"
        control = AgentControlService(storage)
        owner = None
        try:
            with (
                patch("minimal_kanban.agent.control.get_agent_enabled", return_value=True),
                patch("minimal_kanban.agent.control.get_agent_openai_api_key", return_value="test"),
            ):
                control.start_worker(
                    logger=logging.getLogger(__name__), board_api_url="http://test"
                )
                self.assertTrue(started.wait(1))
                owner = control.begin_background_pause()
                self.assertFalse(control.background_pause_status(owner)["drained"])
                release.set()
                self.assertTrue(wait_for(lambda: control.background_pause_status(owner)["drained"]))
                self.assertTrue(control.background_pause_status(owner)["threads_unchanged"])
                # Close a parked worker before it can reach any network client.
                control.close()
                self.assertFalse(control.resume_background(owner))
                owner = None
        finally:
            release.set()
            if owner is not None:
                control.resume_background(owner)
            control.close()


if __name__ == "__main__":
    unittest.main()
