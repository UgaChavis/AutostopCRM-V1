from __future__ import annotations

import asyncio
import inspect
import logging
import os
import select
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.agent.control import AgentControlService  # noqa: E402
from minimal_kanban.api.request_admission import ApiRequestAdmission  # noqa: E402
from minimal_kanban.api.server import ApiServer  # noqa: E402
from minimal_kanban.experimental_lifecycle import ExperimentalLifecycleAuthority  # noqa: E402
from minimal_kanban.experimental_process_quiescence import (  # noqa: E402
    ExperimentalProcessQuiescence,
    QuiescenceRecoveryError,
)
from minimal_kanban.fence_contract import ForeignFenceHeld  # noqa: E402
from minimal_kanban.mcp.drain_wiring import ExperimentalMcpDrain  # noqa: E402
from minimal_kanban.mcp.execution_admission import ExecutionAdmissionClosed  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class ExperimentalLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make_coordinator(self):
        lifecycle = ExperimentalLifecycleAuthority()
        mcp = ExperimentalMcpDrain(lifecycle=lifecycle)
        mcp.bind_once()
        agent = AgentControlService(Mock(), experimental_lifecycle=lifecycle)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        logger = logging.getLogger(__name__)
        service = CardService(
            JsonStore(state_file=Path(temp_dir.name) / "state.json", logger=logger),
            logger,
            experimental_lifecycle=lifecycle,
        )
        api = ApiServer(service, logger, start_port=0, experimental_lifecycle=lifecycle)
        lifecycle.bind_agent_service(agent, service, api)
        coordinator = ExperimentalProcessQuiescence(
            mcp=mcp, agent=agent, api=api, lifecycle=lifecycle
        )
        return coordinator, lifecycle, mcp, agent, api

    async def test_pair_bootstrap_and_public_rebind_are_fail_closed(self) -> None:
        coordinator, _lifecycle, _mcp, agent, api = self.make_coordinator()
        service = api._service
        self.assertIs(service._agent_control, agent)
        self.assertIs(agent._board_service, service)
        async with coordinator.pause(timeout=1):
            with self.assertRaisesRegex(RuntimeError, "capability_required"):
                service.attach_agent_control(Mock())
            with self.assertRaisesRegex(RuntimeError, "capability_required"):
                agent.bind_board_service(Mock())
            self.assertIs(service._agent_control, agent)
            self.assertIs(agent._board_service, service)
            self.assertTrue(coordinator.status().local_checks_passed)
            self.assertFalse(coordinator.status().backup_ready)

    async def test_partial_restore_cannot_commit_and_reopen_admission(self) -> None:
        coordinator, lifecycle, _mcp, _agent, api = self.make_coordinator()
        for name in ("calls", "agent", "executor", "api"):
            coordinator._begin(name)
        with self.assertRaisesRegex(RuntimeError, "restore_incomplete"):
            with lifecycle.restoration(coordinator._capability) as lease:
                self.assertTrue(coordinator._resume("api"))
                lease.commit()
        with lifecycle.admission() as admitted:
            self.assertFalse(admitted)
        self.assertEqual("free", api.request_drain_state(object()))

    async def test_rollback_of_only_confirmed_first_fence(self) -> None:
        coordinator, lifecycle, mcp, _agent, _api = self.make_coordinator()
        coordinator._begin("calls")
        self.assertEqual((), coordinator._recover())
        self.assertEqual("free", mcp.calls.drain_state(object()))
        with lifecycle.admission() as admitted:
            self.assertTrue(admitted)

    async def test_initial_foreign_owner_remains_untouched_on_partial_rollback(self) -> None:
        coordinator, lifecycle, mcp, agent, _api = self.make_coordinator()
        foreign = object()
        agent.begin_background_pause(
            foreign,
            capability=lifecycle._capability_for_step(coordinator._capability, agent, "begin"),
        )
        coordinator._begin("calls")
        with self.assertRaises(ForeignFenceHeld):
            coordinator._begin("agent")
        self.assertEqual((), coordinator._recover())
        self.assertEqual("owned", agent.background_pause_state(foreign))
        self.assertEqual("free", mcp.calls.drain_state(object()))
        with lifecycle.admission() as admitted:
            self.assertTrue(admitted)

    async def test_each_initial_foreign_owner_can_release_once_after_recovery(self) -> None:
        for name in ("calls", "agent", "executor", "api"):
            with self.subTest(name=name):
                coordinator, lifecycle, mcp, agent, api = self.make_coordinator()
                fence = {
                    "calls": mcp.calls,
                    "agent": agent,
                    "executor": mcp.executor._admission,
                    "api": api._request_admission,
                }[name]
                begin = agent.begin_background_pause if name == "agent" else fence.begin_drain
                state = agent.background_pause_state if name == "agent" else fence.drain_state
                foreign = object()
                begin(
                    foreign,
                    capability=lifecycle._capability_for_step(
                        coordinator._capability, fence, "begin"
                    ),
                )
                with self.assertRaisesRegex(RuntimeError, "session_not_released"):
                    lifecycle.release_foreign(fence, foreign)
                if name != "calls":
                    coordinator._begin("calls")
                with self.assertRaises(ForeignFenceHeld):
                    coordinator._begin(name)
                self.assertEqual((), coordinator._recover())
                with self.assertRaisesRegex(RuntimeError, "session_closed"):
                    lifecycle.release_foreign(fence, object())
                with self.assertRaisesRegex(RuntimeError, "session_closed"):
                    lifecycle.release_foreign(object(), foreign)
                lifecycle.release_foreign(fence, foreign)
                self.assertEqual("free", state(foreign))
                with self.assertRaisesRegex(RuntimeError, "session_closed"):
                    lifecycle.release_foreign(fence, foreign)

    async def test_changed_agent_threads_block_commit_even_if_result_is_ignored(self) -> None:
        coordinator, lifecycle, _mcp, agent, _api = self.make_coordinator()
        stop = threading.Event()
        worker = threading.Thread(target=stop.wait)
        worker.start()
        self.addCleanup(lambda: (stop.set(), worker.join(1)))
        agent._worker_thread = worker
        coordinator._begin("agent")
        stop.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        with lifecycle.restoration(coordinator._capability) as lease:
            with self.assertRaisesRegex(RuntimeError, "original_state_changed"):
                coordinator._resume("agent")
            with self.assertRaisesRegex(RuntimeError, "restore_incomplete"):
                lease.commit()
        self.assertEqual("owned", agent.background_pause_state(coordinator._owners["agent"]))
        with lifecycle.admission() as admitted:
            self.assertFalse(admitted)

    async def test_interrupt_during_foreign_release_poison_fails_closed(self) -> None:
        function = ExperimentalLifecycleAuthority._capability_for_step
        lines, first = inspect.getsourcelines(function)
        target = first + next(i for i, line in enumerate(lines) if "return token" in line)
        for interruption in (KeyboardInterrupt, RuntimeError):
            with self.subTest(interruption=interruption.__name__):
                coordinator, lifecycle, _mcp, agent, _api = self.make_coordinator()
                foreign = object()
                agent.begin_background_pause(
                    foreign,
                    capability=lifecycle._capability_for_step(
                        coordinator._capability, agent, "begin"
                    ),
                )
                coordinator._begin("calls")
                with self.assertRaises(ForeignFenceHeld):
                    coordinator._begin("agent")
                self.assertEqual((), coordinator._recover())

                def interrupt(frame, event, arg):
                    if (
                        event == "line"
                        and frame.f_code is function.__code__
                        and frame.f_lineno == target
                    ):
                        sys.settrace(None)
                        raise interruption("synthetic foreign release interruption")
                    return interrupt

                try:
                    sys.settrace(interrupt)
                    with self.assertRaises(interruption):
                        lifecycle.release_foreign(agent, foreign)
                finally:
                    sys.settrace(None)
                self.assertEqual("owned", agent.background_pause_state(foreign))
                with lifecycle.admission() as admitted:
                    self.assertFalse(admitted)

    async def test_concurrent_foreign_release_has_one_winner(self) -> None:
        coordinator, lifecycle, _mcp, agent, _api = self.make_coordinator()
        foreign = object()
        agent.begin_background_pause(
            foreign,
            capability=lifecycle._capability_for_step(coordinator._capability, agent, "begin"),
        )
        coordinator._begin("calls")
        with self.assertRaises(ForeignFenceHeld):
            coordinator._begin("agent")
        self.assertEqual((), coordinator._recover())
        entered, release = threading.Event(), threading.Event()
        outcome = []
        original = agent.resume_background

        def slow_resume(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(1))
            return original(*args, **kwargs)

        def attempt():
            try:
                lifecycle.release_foreign(agent, foreign)
                outcome.append("released")
            except RuntimeError as exc:
                outcome.append(str(exc))

        with patch.object(agent, "resume_background", side_effect=slow_resume):
            first = threading.Thread(target=attempt)
            second = threading.Thread(target=attempt)
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            release.set()
            first.join(1)
            second.join(1)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertCountEqual(["released", "lifecycle_session_closed"], outcome)

    async def test_foreign_identity_change_during_rollback_stays_closed(self) -> None:
        coordinator, lifecycle, _mcp, agent, _api = self.make_coordinator()
        foreign = object()
        agent.begin_background_pause(
            foreign,
            capability=lifecycle._capability_for_step(coordinator._capability, agent, "begin"),
        )
        coordinator._begin("calls")
        with self.assertRaises(ForeignFenceHeld):
            coordinator._begin("agent")
        # Model an unsupported external change; final identity verification
        # must fail closed even when the replacement is also a foreign owner.
        agent._background_owner = object()
        with self.assertRaisesRegex(RuntimeError, "restore_incomplete"):
            coordinator._recover()
        with lifecycle.admission() as admitted:
            self.assertFalse(admitted)

    async def test_unknown_begin_outcome_cannot_reopen_admission(self) -> None:
        coordinator, lifecycle, mcp, _agent, _api = self.make_coordinator()
        original_begin = mcp.calls.begin_drain

        def uncertain(*args, **kwargs):
            original_begin(*args, **kwargs)
            raise RuntimeError("synthetic unknown begin result")

        with patch.object(mcp.calls, "begin_drain", side_effect=uncertain):
            with self.assertRaisesRegex(RuntimeError, "unknown begin result"):
                coordinator._begin("calls")
        with patch.object(mcp.calls, "drain_state", side_effect=RuntimeError("unknown state")):
            self.assertEqual(("calls:ownership_unknown",), coordinator._recover())
        with lifecycle.admission() as admitted:
            self.assertFalse(admitted)

    async def test_exception_after_commit_keeps_admission_closed(self) -> None:
        coordinator, lifecycle, _mcp, _agent, _api = self.make_coordinator()
        for name in ("calls", "agent", "executor", "api"):
            coordinator._begin(name)
        with self.assertRaises(KeyboardInterrupt):
            with lifecycle.restoration(coordinator._capability) as lease:
                self.assertEqual((), coordinator._recover_steps())
                lease.commit()
                raise KeyboardInterrupt("synthetic after commit")
        with lifecycle.admission() as admitted:
            self.assertFalse(admitted)

    async def test_all_supported_mutation_paths_require_capability(self) -> None:
        coordinator, _lifecycle, mcp, agent, api = self.make_coordinator()
        token = object()
        operations = (
            lambda: mcp.calls.begin_drain(token),
            lambda: mcp.calls.resume(token),
            lambda: mcp.executor.begin_drain(token),
            lambda: mcp.executor.resume(token),
            lambda: mcp.executor._admission.begin_drain(token),
            lambda: mcp.executor._admission.resume(token),
            lambda: api.begin_request_drain(token),
            lambda: api.resume_requests(token),
            lambda: api._request_admission.begin_drain(token),
            lambda: api._request_admission.resume(token),
            lambda: agent.begin_background_pause(token),
            lambda: agent.resume_background(token),
            lambda: agent.start_scheduler(),
            lambda: agent.start_worker(logger=Mock()),
            lambda: agent.close(),
            lambda: agent.bind_board_service(Mock()),
        )
        for operation in operations:
            with self.assertRaisesRegex(RuntimeError, "capability_required"):
                operation()
        async with coordinator.pause(timeout=1):
            status = coordinator.status()
            self.assertTrue(status.local_checks_passed)
            self.assertFalse(status.backup_ready)
            self.assertTrue(status.restoration_unproven)
            self.assertIn("runtime_transport_attachment", status.unproven)
        self.assertEqual((), coordinator.last_recovery_errors)

    async def test_mixed_or_replaced_component_fails_before_admission_changes(self) -> None:
        lifecycle = ExperimentalLifecycleAuthority()
        mcp = ExperimentalMcpDrain(lifecycle=lifecycle)
        mcp.bind_once()
        agent = AgentControlService(Mock(), experimental_lifecycle=lifecycle)
        api = ApiServer(Mock(), logging.getLogger(__name__), start_port=0)
        with self.assertRaisesRegex(RuntimeError, "binding_invalid"):
            ExperimentalProcessQuiescence(mcp=mcp, agent=agent, api=api, lifecycle=lifecycle)
        self.assertEqual("free", mcp.calls.drain_state(object()))

        coordinator, lifecycle, mcp, agent, api = self.make_coordinator()
        with self.assertRaisesRegex(RuntimeError, "already_bound"):
            ExperimentalProcessQuiescence(mcp=mcp, agent=agent, api=api, lifecycle=lifecycle)
        api._request_admission = ApiRequestAdmission(lifecycle=lifecycle)
        with self.assertRaisesRegex(RuntimeError, "binding_changed"):
            async with coordinator.pause(timeout=1):
                self.fail("a replaced binding must never pass")
        self.assertFalse(coordinator.status().backup_ready)

    async def test_each_step_is_bound_to_one_component_and_one_operation(self) -> None:
        coordinator, lifecycle, mcp, _agent, api = self.make_coordinator()
        with self.assertRaisesRegex(RuntimeError, "capability_required"):
            api.begin_request_drain(capability=coordinator._capability)
        step = lifecycle._capability_for_step(coordinator._capability, mcp.calls, "begin")
        with self.assertRaisesRegex(RuntimeError, "capability_required"):
            api.begin_request_drain(capability=step)
        with self.assertRaisesRegex(RuntimeError, "capability_required"):
            mcp.calls.resume(object(), capability=step)
        owner = mcp.calls.begin_drain(capability=step)
        with self.assertRaisesRegex(RuntimeError, "capability_required"):
            mcp.calls.resume(owner, capability=step)
        mcp.calls.resume(
            owner,
            capability=lifecycle._capability_for_step(coordinator._capability, mcp.calls, "resume"),
        )

    async def test_untrusted_thread_cannot_change_owner_during_or_after_restore(self) -> None:
        coordinator, lifecycle, _mcp, _agent, api = self.make_coordinator()
        original_state = coordinator._state
        started, finished = threading.Event(), threading.Event()
        failures: list[str] = []
        thread = None

        def attempt():
            started.set()
            try:
                api.begin_request_drain()
            except RuntimeError as exc:
                failures.append(str(exc))
            finally:
                finished.set()

        def reentrant(name):
            nonlocal thread
            state = original_state(name)
            if name == "api" and state == "free" and thread is None:
                thread = threading.Thread(target=attempt)
                thread.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(0.05))
            return state

        with patch.object(coordinator, "_state", side_effect=reentrant):
            async with coordinator.pause(timeout=1):
                pass
        self.assertTrue(finished.wait(1))
        thread.join(1)
        self.assertEqual(["lifecycle_capability_required"], failures)
        self.assertEqual("free", api.request_drain_state(object()))
        with self.assertRaisesRegex(RuntimeError, "capability_required"):
            api.begin_request_drain(capability=coordinator._capability)
        with self.assertRaisesRegex(RuntimeError, "session_closed"):
            lifecycle._capability_for_step(coordinator._capability, api._request_admission, "begin")

    async def test_restore_rejects_reentrant_mint_for_earlier_api_step(self) -> None:
        coordinator, lifecycle, _mcp, _agent, api = self.make_coordinator()
        original = coordinator._state
        attempted = []

        def after_api_readback(name):
            result = original(name)
            if name == "api" and result == "free":
                with self.assertRaisesRegex(RuntimeError, "restore_step_invalid"):
                    lifecycle._capability_for_step(
                        coordinator._capability, api._request_admission, "begin"
                    )
                attempted.append(True)
            return result

        with patch.object(coordinator, "_state", side_effect=after_api_readback):
            async with coordinator.pause(timeout=1):
                pass
        self.assertEqual([True], attempted)
        self.assertEqual((), coordinator.last_recovery_errors)
        self.assertEqual("free", api.request_drain_state(object()))

    async def test_session_is_closed_before_latch_reopens(self) -> None:
        coordinator, lifecycle, _mcp, _agent, api = self.make_coordinator()
        original = lifecycle._end_session
        checked = []

        def at_end(capability):
            with self.assertRaisesRegex(RuntimeError, "session_closed"):
                lifecycle._capability_for_step(capability, api._request_admission, "begin")
            checked.append(True)
            original(capability)

        with patch.object(lifecycle, "_end_session", side_effect=at_end):
            async with coordinator.pause(timeout=1):
                pass
        self.assertEqual([True], checked)

    async def test_reentrant_mutations_and_admissions_cannot_cross_restore_latch(self) -> None:
        coordinator, _lifecycle, mcp, agent, api = self.make_coordinator()
        original_state = coordinator._state
        worker_started, worker_entered, worker_release = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        stop = threading.Event()
        errors: list[BaseException] = []

        def worker():
            worker_started.set()
            try:
                if agent._enter_background(stop):
                    try:
                        worker_entered.set()
                        worker_release.wait(2)
                    finally:
                        agent._leave_background()
            except BaseException as exc:
                errors.append(exc)

        worker_thread = threading.Thread(target=worker)

        def reentrant(name):
            state = original_state(name)
            if name == "agent" and state == "free":
                self.assertFalse(worker_entered.wait(0.05))
                self.assertFalse(api._request_admission.enter())
                with self.assertRaisesRegex(RuntimeError, "mcp_calls_draining"):
                    mcp.calls.enter()
                with self.assertRaises(ExecutionAdmissionClosed):
                    mcp.executor._admission.checkpoint()
                with self.assertRaisesRegex(RuntimeError, "transition_mutation_denied"):
                    api.begin_request_drain(capability=coordinator._capability)
            return state

        try:
            with patch.object(coordinator, "_state", side_effect=reentrant):
                async with coordinator.pause(timeout=1):
                    worker_thread.start()
                    self.assertTrue(worker_started.wait(1))
                    self.assertFalse(worker_entered.wait(0.05))
            self.assertTrue(worker_entered.wait(1))
            self.assertEqual((), coordinator.last_recovery_errors)
        finally:
            stop.set()
            worker_release.set()
            worker_thread.join(2)
        self.assertFalse(worker_thread.is_alive())
        self.assertEqual([], errors)

    async def test_interrupted_restoration_poison_keeps_all_new_admissions_closed(self) -> None:
        coordinator, lifecycle, mcp, agent, api = self.make_coordinator()
        original_resume = coordinator._resume

        def interrupted(name):
            if name == "agent":
                raise KeyboardInterrupt("synthetic interruption")
            return original_resume(name)

        with patch.object(coordinator, "_resume", side_effect=interrupted):
            with self.assertRaises(QuiescenceRecoveryError):
                async with coordinator.pause(timeout=1):
                    pass
        self.assertIn("agent:resume_unknown", coordinator.last_recovery_errors)
        self.assertFalse(api._request_admission.enter())
        with self.assertRaisesRegex(RuntimeError, "mcp_calls_draining"):
            mcp.calls.enter()
        with self.assertRaises(ExecutionAdmissionClosed):
            mcp.executor._admission.checkpoint()
        with self.assertRaisesRegex(RuntimeError, "lifecycle_poisoned"):
            api.begin_request_drain(capability=coordinator._capability)
        self.assertTrue(coordinator.status().restoration_unproven)
        self.assertFalse(coordinator.status().backup_ready)

    async def test_interrupt_after_latch_closes_poison_fails_closed(self) -> None:
        coordinator, lifecycle, mcp, _agent, api = self.make_coordinator()
        function = ExperimentalLifecycleAuthority.restoration.__wrapped__
        lines, first = inspect.getsourcelines(function)
        target = first + next(index for index, line in enumerate(lines) if "yield lease" in line)

        def interrupt(frame, event, arg):
            if event == "line" and frame.f_code is function.__code__ and frame.f_lineno == target:
                sys.settrace(None)
                raise KeyboardInterrupt("synthetic restoration interruption")
            return interrupt

        try:
            with self.assertRaises(KeyboardInterrupt):
                async with coordinator.pause(timeout=1):
                    sys.settrace(interrupt)
        finally:
            sys.settrace(None)
        self.assertEqual(("recovery_not_confirmed",), coordinator.last_recovery_errors)
        self.assertTrue(lifecycle._poisoned)
        self.assertFalse(api._request_admission.enter())
        with self.assertRaisesRegex(RuntimeError, "mcp_calls_draining"):
            mcp.calls.enter()

    async def test_signal_handler_cannot_mint_step_inside_active_mutation(self) -> None:
        coordinator, lifecycle, mcp, _agent, _api = self.make_coordinator()
        observed = []

        def handler(_number, _frame):
            try:
                lifecycle._capability_for_step(coordinator._capability, mcp.calls, "begin")
            except RuntimeError as exc:
                observed.append(str(exc))

        old_handler = signal.signal(signal.SIGINT, handler)
        try:
            step = lifecycle._capability_for_step(coordinator._capability, mcp.calls, "begin")
            with lifecycle.mutation(step, mcp.calls, "begin"):
                with mcp.calls._lock:
                    signal.raise_signal(signal.SIGINT)
        finally:
            signal.signal(signal.SIGINT, old_handler)
        self.assertEqual(["lifecycle_mutation_reentrant"], observed)

    async def test_interrupt_after_close_lease_marks_authority_poisoned(self) -> None:
        coordinator, lifecycle, _mcp, agent, api = self.make_coordinator()
        function = ExperimentalLifecycleAuthority._agent_close_start.__wrapped__
        lines, first = inspect.getsourcelines(function)
        target = first + next(index for index, line in enumerate(lines) if "yield lease" in line)

        def interrupt(frame, event, arg):
            if event == "line" and frame.f_code is function.__code__ and frame.f_lineno == target:
                sys.settrace(None)
                raise KeyboardInterrupt("synthetic close-lease interruption")
            return interrupt

        try:
            step = lifecycle._capability_for_step(coordinator._capability, agent, "close")
            sys.settrace(interrupt)
            with self.assertRaises(KeyboardInterrupt):
                agent.close(capability=step)
        finally:
            sys.settrace(None)
        self.assertTrue(lifecycle._poisoned)
        self.assertFalse(api._request_admission.enter())

    async def test_interrupt_after_mutation_begins_marks_authority_poisoned(self) -> None:
        coordinator, lifecycle, mcp, _agent, api = self.make_coordinator()
        function = ExperimentalLifecycleAuthority.mutation.__wrapped__
        lines, first = inspect.getsourcelines(function)
        target = first + next(
            index for index, line in enumerate(lines) if "self._step_capability = None" in line
        )

        def interrupt(frame, event, arg):
            if event == "line" and frame.f_code is function.__code__ and frame.f_lineno == target:
                sys.settrace(None)
                raise KeyboardInterrupt("synthetic mutation interruption")
            return interrupt

        try:
            step = lifecycle._capability_for_step(coordinator._capability, mcp.calls, "begin")
            sys.settrace(interrupt)
            with self.assertRaises(KeyboardInterrupt):
                mcp.calls.begin_drain(capability=step)
        finally:
            sys.settrace(None)
        self.assertTrue(lifecycle._poisoned)
        self.assertFalse(api._request_admission.enter())

    async def test_interrupt_during_latch_release_stays_fail_closed(self) -> None:
        coordinator, lifecycle, mcp, _agent, api = self.make_coordinator()
        function = ExperimentalLifecycleAuthority.restoration.__wrapped__
        lines, first = inspect.getsourcelines(function)
        target = first + next(
            index for index, line in enumerate(lines) if "if lease.committed:" in line
        )

        def interrupt(frame, event, arg):
            if event == "line" and frame.f_code is function.__code__ and frame.f_lineno == target:
                sys.settrace(None)
                raise KeyboardInterrupt("synthetic latch-release interruption")
            return interrupt

        try:
            with self.assertRaises(KeyboardInterrupt):
                async with coordinator.pause(timeout=1):
                    sys.settrace(interrupt)
        finally:
            sys.settrace(None)
        self.assertEqual(("recovery_not_confirmed",), coordinator.last_recovery_errors)
        self.assertTrue(lifecycle._transition or lifecycle._poisoned)
        self.assertFalse(api._request_admission.enter())
        with self.assertRaisesRegex(RuntimeError, "mcp_calls_draining"):
            mcp.calls.enter()

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    async def test_fork_child_rejects_before_acquiring_inherited_authority_lock(self) -> None:
        coordinator, lifecycle, mcp, _agent, _api = self.make_coordinator()
        step = lifecycle._capability_for_step(coordinator._capability, mcp.calls, "begin")
        read_fd, write_fd = os.pipe()
        with lifecycle.mutation(step, mcp.calls, "begin"):
            pid = os.fork()
            if pid == 0:
                try:
                    os.close(read_fd)
                    try:
                        mcp.calls.enter()
                    except RuntimeError as exc:
                        os.write(write_fd, str(exc).encode())
                finally:
                    os._exit(0)
            os.close(write_fd)
            try:
                self.assertTrue(select.select([read_fd], [], [], 2)[0])
                self.assertEqual(b"lifecycle_wrong_process", os.read(read_fd, 100))
            finally:
                os.close(read_fd)
                _, status = os.waitpid(pid, 0)
                self.assertEqual(0, os.waitstatus_to_exitcode(status))

    async def test_interrupted_agent_close_keeps_thread_reference_and_allows_retry(self) -> None:
        coordinator, lifecycle, _mcp, agent, _api = self.make_coordinator()
        thread = Mock()
        thread.is_alive.return_value = True
        thread.join.side_effect = KeyboardInterrupt
        agent._worker_thread = thread
        with self.assertRaises(KeyboardInterrupt):
            agent.close(
                capability=lifecycle._capability_for_step(coordinator._capability, agent, "close")
            )
        self.assertIs(thread, agent._worker_thread)
        self.assertFalse(agent._background_closing)
        self.assertIsNone(lifecycle._agent_close_lease)
        thread.is_alive.return_value = False
        agent.close(
            capability=lifecycle._capability_for_step(coordinator._capability, agent, "close")
        )
        self.assertIsNone(agent._worker_thread)

    async def test_join_occurs_outside_global_lifecycle_lock(self) -> None:
        coordinator, lifecycle, _mcp, agent, _api = self.make_coordinator()
        started, release = threading.Event(), threading.Event()
        agent._scheduler_interval_seconds = 0.01

        def tick(*, force: bool):
            started.set()
            if not release.wait(2):
                raise TimeoutError("synthetic scheduler timeout")
            return {}

        agent.trigger_scheduled_tasks = tick
        agent.start_scheduler(
            capability=lifecycle._capability_for_step(
                coordinator._capability, agent, "start_scheduler"
            )
        )
        self.assertTrue(started.wait(1))
        closed, errors = threading.Event(), []

        def close_agent():
            try:
                agent.close(
                    capability=lifecycle._capability_for_step(
                        coordinator._capability, agent, "close"
                    )
                )
            except BaseException as exc:
                errors.append(exc)
            finally:
                closed.set()

        thread = threading.Thread(target=close_agent)
        thread.start()
        try:
            await asyncio.sleep(0.03)
            self.assertFalse(closed.is_set())
            # This read takes the global lock. It must complete while join waits.
            lifecycle._verify_binding(coordinator._capability, coordinator._participants())
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
