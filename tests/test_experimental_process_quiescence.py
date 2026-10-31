from __future__ import annotations

import asyncio
import inspect
import logging
import sys
import time
import unittest
from unittest.mock import Mock, patch

from tests.source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.agent.control import AgentControlService  # noqa: E402
from minimal_kanban.api.server import ApiServer  # noqa: E402
from minimal_kanban.experimental_process_quiescence import (  # noqa: E402
    ExperimentalProcessQuiescence,
    LocalStatus,
    QuiescenceRecoveryError,
    QuiescenceTimeout,
)
from minimal_kanban.mcp.drain_wiring import ExperimentalMcpDrain  # noqa: E402


class ProcessQuiescenceTests(unittest.IsolatedAsyncioTestCase):
    def make_coordinator(self):
        mcp = ExperimentalMcpDrain()
        agent = AgentControlService(Mock())
        api = ApiServer(Mock(), logging.getLogger(__name__), start_port=0)
        coordinator = ExperimentalProcessQuiescence(mcp=mcp, agent=agent, api=api)
        return coordinator, mcp, agent, api

    async def test_reverse_restore_and_permanent_global_not_ready(self) -> None:
        coordinator, mcp, agent, api = self.make_coordinator()
        events: list[str] = []

        def wrapped(name, method):
            def observe(*args, **kwargs):
                events.append(name)
                return method(*args, **kwargs)

            return observe

        with (
            patch.object(mcp.calls, "begin_drain", wrapped("calls:begin", mcp.calls.begin_drain)),
            patch.object(
                agent,
                "begin_background_pause",
                wrapped("agent:begin", agent.begin_background_pause),
            ),
            patch.object(
                mcp.executor, "begin_drain", wrapped("executor:begin", mcp.executor.begin_drain)
            ),
            patch.object(api, "begin_request_drain", wrapped("api:begin", api.begin_request_drain)),
            patch.object(api, "resume_requests", wrapped("api:resume", api.resume_requests)),
            patch.object(mcp.executor, "resume", wrapped("executor:resume", mcp.executor.resume)),
            patch.object(
                agent, "resume_background", wrapped("agent:resume", agent.resume_background)
            ),
            patch.object(mcp.calls, "resume", wrapped("calls:resume", mcp.calls.resume)),
        ):
            async with coordinator.pause(timeout=1):
                status = coordinator.status()
                self.assertTrue(status.local_checks_passed)
                self.assertFalse(status.backup_ready)
                self.assertFalse(status.hard_deadline)
                self.assertTrue(status.restoration_unproven)
                self.assertIn("oauth_read_and_write", status.unproven)
                self.assertIn("host_and_deployment_writers", status.unproven)
                self.assertFalse(api._request_admission.enter())
            self.assertEqual(
                events,
                [
                    "calls:begin",
                    "agent:begin",
                    "executor:begin",
                    "api:begin",
                    "api:resume",
                    "executor:resume",
                    "agent:resume",
                    "calls:resume",
                ],
            )
        self.assertEqual((), coordinator.last_recovery_errors)
        self.assertFalse(coordinator.status().local_checks_passed)
        self.assertTrue(api._request_admission.enter())
        api._request_admission.leave()

    async def test_waits_for_real_active_call_without_blocking_event_loop(self) -> None:
        coordinator, mcp, _agent, _api = self.make_coordinator()
        mcp.calls.enter()
        entered = asyncio.Event()

        async def run():
            async with coordinator.pause(timeout=1):
                entered.set()
                self.assertFalse(coordinator.status().backup_ready)

        task = asyncio.create_task(run())
        try:
            await asyncio.sleep(0.03)
            self.assertFalse(entered.is_set())
            mcp.calls.leave()
            await asyncio.wait_for(task, 1)
            self.assertTrue(entered.is_set())
        finally:
            if not task.done():
                task.cancel()

    async def test_idle_api_request_times_out_and_recovers_every_owned_fence(self) -> None:
        coordinator, _mcp, _agent, api = self.make_coordinator()
        self.assertTrue(api._request_admission.enter())
        try:
            with self.assertRaises(QuiescenceTimeout):
                async with coordinator.pause(timeout=0.04):
                    self.fail("busy API request must prevent local quiescence")
            self.assertEqual((), coordinator.last_recovery_errors)
            self.assertTrue(api._request_admission.enter())
            api._request_admission.leave()
        finally:
            api._request_admission.leave()

    async def test_cancel_during_wait_reopens_fence_without_cancelling_the_active_call(
        self,
    ) -> None:
        coordinator, mcp, _agent, _api = self.make_coordinator()
        mcp.calls.enter()

        async def run():
            async with coordinator.pause(timeout=1):
                self.fail("active call must keep the fence waiting")

        task = asyncio.create_task(run())
        try:
            await asyncio.sleep(0.03)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual((), coordinator.last_recovery_errors)
            self.assertFalse(mcp.calls.owns_drain(coordinator._owners["calls"]))
        finally:
            mcp.calls.leave()

    async def test_interruption_after_each_fence_still_recovers_known_owner(self) -> None:
        for target_name in ("calls", "agent", "executor", "api"):
            with self.subTest(target=target_name):
                coordinator, mcp, agent, api = self.make_coordinator()
                target, method_name = {
                    "calls": (mcp.calls, "begin_drain"),
                    "agent": (agent, "begin_background_pause"),
                    "executor": (mcp.executor, "begin_drain"),
                    "api": (api, "begin_request_drain"),
                }[target_name]
                original = getattr(target, method_name)

                def interrupted(owner):
                    original(owner)
                    raise KeyboardInterrupt("synthetic post-fence interruption")

                with patch.object(target, method_name, interrupted):
                    with self.assertRaises(KeyboardInterrupt):
                        async with coordinator.pause(timeout=1):
                            self.fail("interruption should prevent yielding")
                self.assertEqual((), coordinator.last_recovery_errors)
                for name in coordinator._attempted:
                    self.assertFalse(coordinator._owns(name))

    async def test_interrupt_proven_before_begin_restores_earlier_fence(self) -> None:
        coordinator, mcp, _agent, _api = self.make_coordinator()

        class InterruptOwnerLookup(dict):
            def __getitem__(self, name):
                if name == "agent":
                    raise KeyboardInterrupt("synthetic interruption before begin")
                return super().__getitem__(name)

        coordinator._owners = InterruptOwnerLookup(coordinator._owners)
        with self.assertRaises(KeyboardInterrupt):
            async with coordinator.pause(timeout=1):
                self.fail("interruption should prevent yielding")
        self.assertEqual((), coordinator.last_recovery_errors)
        self.assertEqual(["calls"], coordinator._attempted)
        self.assertFalse(mcp.calls.owns_drain(coordinator._owners["calls"]))

    async def test_unknown_api_resume_keeps_dependent_fences_closed(self) -> None:
        coordinator, mcp, agent, api = self.make_coordinator()
        real_resume = api.resume_requests
        try:
            with patch.object(api, "resume_requests", side_effect=RuntimeError("unknown")):
                with self.assertRaises(QuiescenceRecoveryError):
                    async with coordinator.pause(timeout=1):
                        pass
            self.assertEqual(
                (
                    "api:resume_unknown",
                    "executor:held_for_dependency",
                    "agent:held_for_dependency",
                    "calls:held_for_dependency",
                ),
                coordinator.last_recovery_errors,
            )
            self.assertTrue(all(coordinator._owns(name) for name in coordinator._attempted))
        finally:
            real_resume(coordinator._owners["api"])
            mcp.executor.resume(coordinator._owners["executor"])
            agent.resume_background(coordinator._owners["agent"])
            mcp.calls.resume(coordinator._owners["calls"])

    async def test_resume_with_unknown_outcome_is_not_retried(self) -> None:
        coordinator, mcp, agent, api = self.make_coordinator()
        real_resume = api.resume_requests
        attempts = []

        def applied_then_unknown(owner):
            attempts.append(1)
            real_resume(owner)
            raise OSError("synthetic unknown result")

        try:
            with patch.object(api, "resume_requests", side_effect=applied_then_unknown):
                with self.assertRaises(QuiescenceRecoveryError):
                    async with coordinator.pause(timeout=1):
                        pass
            self.assertEqual([1], attempts)
            self.assertFalse(api.owns_request_drain(coordinator._owners["api"]))
            self.assertTrue(mcp.executor.owns_drain(coordinator._owners["executor"]))
            self.assertTrue(agent.owns_background_pause(coordinator._owners["agent"]))
            self.assertTrue(mcp.calls.owns_drain(coordinator._owners["calls"]))
        finally:
            if api.owns_request_drain(coordinator._owners["api"]):
                real_resume(coordinator._owners["api"])
            mcp.executor.resume(coordinator._owners["executor"])
            agent.resume_background(coordinator._owners["agent"])
            mcp.calls.resume(coordinator._owners["calls"])

    async def test_successful_return_without_free_readback_keeps_intake_closed(self) -> None:
        coordinator, mcp, agent, api = self.make_coordinator()
        real_resume = api.resume_requests
        try:
            with patch.object(api, "resume_requests", return_value=None):
                with self.assertRaises(QuiescenceRecoveryError):
                    async with coordinator.pause(timeout=1):
                        pass
            self.assertEqual("api:resume_not_free", coordinator.last_recovery_errors[0])
            self.assertTrue(all(coordinator._owns(name) for name in coordinator._attempted))
        finally:
            real_resume(coordinator._owners["api"])
            mcp.executor.resume(coordinator._owners["executor"])
            agent.resume_background(coordinator._owners["agent"])
            mcp.calls.resume(coordinator._owners["calls"])

    async def test_new_foreign_owner_after_resume_prevents_opening_dependent_intake(self) -> None:
        coordinator, mcp, agent, api = self.make_coordinator()
        real_resume = api.resume_requests
        foreign = []

        def replace_after_resume(owner):
            real_resume(owner)
            foreign.append(api.begin_request_drain())

        try:
            with patch.object(api, "resume_requests", side_effect=replace_after_resume):
                with self.assertRaises(QuiescenceRecoveryError):
                    async with coordinator.pause(timeout=1):
                        pass
            self.assertEqual("api:resume_not_free", coordinator.last_recovery_errors[0])
            self.assertTrue(api.owns_request_drain(foreign[0]))
            self.assertTrue(mcp.calls.owns_drain(coordinator._owners["calls"]))
        finally:
            real_resume(foreign[0])
            mcp.executor.resume(coordinator._owners["executor"])
            agent.resume_background(coordinator._owners["agent"])
            mcp.calls.resume(coordinator._owners["calls"])

    async def test_another_owner_can_race_after_sequential_free_readback(self) -> None:
        coordinator, mcp, _agent, api = self.make_coordinator()
        original_state = coordinator._state
        foreign = []

        def raced_readback(name):
            state = original_state(name)
            if name == "api" and state == "free" and not foreign:
                foreign.append(api.begin_request_drain())
            return state

        try:
            with patch.object(coordinator, "_state", side_effect=raced_readback):
                async with coordinator.pause(timeout=1):
                    pass
            # This demonstrates the limitation: an earlier free observation
            # and an empty error list do not prove a globally restored state.
            self.assertEqual((), coordinator.last_recovery_errors)
            self.assertTrue(api.owns_request_drain(foreign[0]))
            self.assertFalse(mcp.calls.owns_drain(coordinator._owners["calls"]))
            self.assertTrue(coordinator.status().restoration_unproven)
            self.assertFalse(coordinator.status().backup_ready)
        finally:
            api.resume_requests(foreign[0])

    async def test_foreign_fence_is_never_released(self) -> None:
        coordinator, mcp, _agent, _api = self.make_coordinator()
        foreign = mcp.calls.begin_drain()
        try:
            with self.assertRaisesRegex(RuntimeError, "already_active"):
                async with coordinator.pause(timeout=1):
                    self.fail("a foreign fence must not be stolen")
            self.assertTrue(mcp.calls.owns_drain(foreign))
            self.assertEqual((), coordinator.last_recovery_errors)
        finally:
            mcp.calls.resume(foreign)

    async def test_foreign_fence_preserves_foreign_state_and_restores_our_prior_fences(
        self,
    ) -> None:
        for name in ("agent", "executor", "api"):
            with self.subTest(foreign=name):
                coordinator, mcp, agent, api = self.make_coordinator()
                if name == "agent":
                    begin, resume = agent.begin_background_pause, agent.resume_background
                elif name == "executor":
                    begin, resume = mcp.executor.begin_drain, mcp.executor.resume
                else:
                    begin, resume = api.begin_request_drain, api.resume_requests
                foreign = begin()
                try:
                    with self.assertRaises(RuntimeError):
                        async with coordinator.pause(timeout=1):
                            self.fail("foreign ownership cannot be stolen")
                    self.assertEqual((), coordinator.last_recovery_errors)
                    self.assertFalse(mcp.calls.owns_drain(coordinator._owners["calls"]))
                    for attempted in coordinator._attempted:
                        self.assertFalse(coordinator._owns(attempted))
                finally:
                    resume(foreign)

    async def test_unconfirmed_begin_and_false_readback_do_not_reopen_prior_intake(self) -> None:
        coordinator, mcp, agent, _api = self.make_coordinator()
        try:
            with patch.object(agent, "begin_background_pause", side_effect=OSError("unknown")):
                with self.assertRaises(OSError):
                    async with coordinator.pause(timeout=1):
                        self.fail("unknown begin must not yield")
            self.assertEqual(
                ("agent:begin_unknown", "calls:held_for_dependency"),
                coordinator.last_recovery_errors,
            )
            self.assertTrue(mcp.calls.owns_drain(coordinator._owners["calls"]))
        finally:
            mcp.calls.resume(coordinator._owners["calls"])

    async def test_interrupted_recovery_never_reports_empty_errors(self) -> None:
        coordinator, mcp, agent, api = self.make_coordinator()
        try:
            with patch.object(coordinator, "_recover", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    async with coordinator.pause(timeout=1):
                        pass
            self.assertEqual(("recovery_not_confirmed",), coordinator.last_recovery_errors)
            self.assertTrue(coordinator._session_lock.acquire(blocking=False))
            coordinator._session_lock.release()
            self.assertTrue(all(coordinator._owns(name) for name in coordinator._attempted))
        finally:
            api.resume_requests(coordinator._owners["api"])
            mcp.executor.resume(coordinator._owners["executor"])
            agent.resume_background(coordinator._owners["agent"])
            mcp.calls.resume(coordinator._owners["calls"])

    async def test_interrupt_inside_session_lock_releases_lock_before_any_fence(self) -> None:
        coordinator, _mcp, _agent, _api = self.make_coordinator()
        function = ExperimentalProcessQuiescence.pause.__wrapped__
        lines, first = inspect.getsourcelines(function)
        target = first + next(index for index, line in enumerate(lines) if "if self._used:" in line)

        def interrupt(frame, event, arg):
            if event == "line" and frame.f_code is function.__code__ and frame.f_lineno == target:
                sys.settrace(None)
                raise KeyboardInterrupt("synthetic session setup interruption")
            return interrupt

        try:
            sys.settrace(interrupt)
            with self.assertRaises(KeyboardInterrupt):
                async with coordinator.pause(timeout=1):
                    self.fail("interrupted setup must not yield")
        finally:
            sys.settrace(None)
        self.assertTrue(coordinator._session_lock.acquire(blocking=False))
        coordinator._session_lock.release()
        self.assertEqual([], coordinator._attempted)
        self.assertEqual(("recovery_not_confirmed",), coordinator.last_recovery_errors)

    async def test_rejects_invalid_deadline_and_reuse(self) -> None:
        coordinator, _mcp, _agent, _api = self.make_coordinator()
        for invalid in (0, -1, True, float("inf"), float("nan")):
            with self.subTest(timeout=invalid):
                with self.assertRaises(ValueError):
                    async with coordinator.pause(timeout=invalid):
                        pass
        async with coordinator.pause(timeout=1):
            pass
        with self.assertRaisesRegex(RuntimeError, "single_use"):
            async with coordinator.pause(timeout=1):
                pass

    def test_ready_and_hard_deadline_are_not_configurable(self) -> None:
        with self.assertRaises(TypeError):
            LocalStatus(local_checks_passed=True, backup_ready=True)
        with self.assertRaises(TypeError):
            LocalStatus(local_checks_passed=True, hard_deadline=True)
        with self.assertRaises(TypeError):
            LocalStatus(local_checks_passed=True, restoration_unproven=False)
        status = LocalStatus(local_checks_passed=True)
        with self.assertRaises((AttributeError, TypeError)):
            status.backup_ready = True
        with self.assertRaises((AttributeError, TypeError)):
            status.hard_deadline = True
        with self.assertRaises((AttributeError, TypeError)):
            status.restoration_unproven = False

    async def test_deadline_detects_but_cannot_preempt_blocking_component(self) -> None:
        coordinator, mcp, _agent, _api = self.make_coordinator()
        original = mcp.calls.begin_drain

        def slow_begin(owner):
            time.sleep(0.05)
            return original(owner)

        started = time.monotonic()
        with patch.object(mcp.calls, "begin_drain", slow_begin):
            with self.assertRaises(QuiescenceTimeout):
                async with coordinator.pause(timeout=0.01):
                    self.fail("a late component must not yield readiness")
        self.assertGreaterEqual(time.monotonic() - started, 0.05)
        self.assertEqual((), coordinator.last_recovery_errors)
        self.assertFalse(coordinator.status().backup_ready)
