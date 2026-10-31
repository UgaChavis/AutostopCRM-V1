import asyncio
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from mcp.server.fastmcp import FastMCP

from minimal_kanban.agent.control import AgentControlService
from minimal_kanban.api.request_admission import ApiRequestAdmission
from minimal_kanban.mcp.backup_admission import McpAdmission, TrackedExecutor
from minimal_kanban.mcp.backup_drain import BackupDrain
from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.mcp.runtime import McpServerRuntime
from minimal_kanban.mcp.server import create_mcp_server


class FakeMcp:
    def __init__(self):
        self.backup_admission = SimpleNamespace(
            requests=ApiRequestAdmission(), tools=ApiRequestAdmission(), close_idle_sse=lambda: None
        )
        self.workers_idle = True

    def backup_workers_idle(self):
        return self.workers_idle


class BackupPredrainTests(unittest.TestCase):
    def setUp(self):
        self.mcp = FakeMcp()
        self.agent = SimpleNamespace(backup_admission=ApiRequestAdmission())
        self.api = SimpleNamespace(_request_admission=ApiRequestAdmission())
        self.drain = BackupDrain(self.mcp, self.agent, self.api)

    def test_timeout_does_not_cancel_existing_writer_and_reopens(self):
        gate = self.agent.backup_admission
        self.assertTrue(gate.enter())
        try:
            self.assertFalse(self.drain.prepare(0.1, lambda: False))
            self.assertTrue(gate.enter())
            gate.leave()
        finally:
            gate.leave()
        self.assertTrue(self.mcp.backup_admission.requests.enter())
        self.mcp.backup_admission.requests.leave()
        self.assertTrue(self.api._request_admission.enter())
        self.api._request_admission.leave()

    def test_success_waits_for_all_and_commit_cannot_be_aborted(self):
        mcp_gate = self.mcp.backup_admission.requests
        api_gate = self.api._request_admission
        self.assertTrue(mcp_gate.enter())
        self.assertTrue(api_gate.enter())
        self.mcp.workers_idle = False
        answer = []
        worker = threading.Thread(
            target=lambda: answer.append(self.drain.prepare(3, lambda: False))
        )
        worker.start()
        time.sleep(0.1)
        self.assertFalse(mcp_gate.enter())
        self.assertTrue(worker.is_alive())
        mcp_gate.leave()
        self.mcp.workers_idle = True
        time.sleep(0.15)
        self.assertTrue(worker.is_alive())
        api_gate.leave()
        worker.join(2)
        self.assertEqual(answer, [True])
        self.drain.commit()
        self.assertFalse(self.drain.abort())
        for gate in (mcp_gate, api_gate, self.agent.backup_admission):
            self.assertFalse(gate.enter())

    def test_abandoned_executor_work_is_counted_until_it_really_finishes(self):
        active, release = threading.Event(), threading.Event()
        executor = TrackedExecutor()

        def work():
            active.set()
            release.wait(2)

        future = executor.submit(work)
        self.assertTrue(active.wait(1))
        future.cancel()
        self.assertEqual(executor.pending, 1)
        release.set()
        try:
            future.result(2)
        except CancelledError:
            pass
        executor.shutdown(wait=True)
        self.assertEqual(executor.pending, 0)

    def test_real_agent_loop_pauses_and_resumes_without_restarting_task(self):
        updated = threading.Event()

        class Storage:
            count = 0

            def read_prompt_text(self):
                return "synthetic prompt"

            def read_memory_text(self):
                return "synthetic memory"

            def update_status(self, **_):
                self.count += 1
                updated.set()

        storage = Storage()
        agent = AgentControlService(storage)
        with (
            patch("minimal_kanban.agent.control.get_agent_enabled", return_value=False),
            patch("minimal_kanban.agent.control.get_agent_poll_interval_seconds", return_value=0.2),
        ):
            worker = threading.Thread(
                target=agent._worker_loop, args=(logging.getLogger("synthetic"), "")
            )
            agent._worker_thread = worker
            worker.start()
            try:
                self.assertTrue(updated.wait(2))
                owner = object()
                agent.backup_admission.begin_drain(owner)
                self.assertTrue(agent.backup_admission.wait_drained(owner, 1))
                count = storage.count
                time.sleep(0.35)
                self.assertEqual(storage.count, count)
                agent.backup_admission.resume(owner)
                end = time.monotonic() + 2
                while storage.count == count and time.monotonic() < end:
                    time.sleep(0.02)
                self.assertGreater(storage.count, count)
                agent.backup_admission.begin_drain(owner)
                self.assertTrue(agent.backup_admission.wait_drained(owner, 1))
                agent.close()
                self.assertFalse(worker.is_alive())
                agent.backup_admission.resume(owner)
            finally:
                agent._worker_stop.set()
                worker.join(2)

    def test_unexpected_sdk_resource_fails_closed(self):
        server = FastMCP("synthetic")
        runtime = McpServerRuntime(server, logging.getLogger("synthetic"))
        runtime._backup_executor = TrackedExecutor()
        runtime._backup_app = server.streamable_http_app()
        runtime._backup_tool_fingerprint = McpAdmission(runtime._backup_app).wrap_tools(server)
        try:
            self.assertTrue(runtime.backup_workers_idle())

            @server.resource("synthetic://resource")
            def resource():
                return "synthetic"

            self.assertFalse(runtime.backup_workers_idle())
        finally:
            runtime._backup_executor.shutdown(wait=True)

    def test_actual_oauth_route_topology_is_attested(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(
                os.environ,
                {
                    "AUTOSTOP_MCP_OAUTH_ENABLED": "1",
                    "AUTOSTOP_MCP_OAUTH_STATE_KEY": Fernet.generate_key().decode(),
                },
            ),
        ):
            logger = logging.getLogger("synthetic-oauth")
            board = BoardApiClient("http://127.0.0.1:1", bearer_token="synthetic", logger=logger)
            server = create_mcp_server(
                board,
                logger,
                host="127.0.0.1",
                port=41831,
                bearer_token=secrets.token_urlsafe(48),
                public_endpoint_url="https://example.test/mcp",
                oauth_state_file=Path(temp) / "oauth.json",
            )
            runtime = McpServerRuntime(server, logger)
            runtime._backup_executor = TrackedExecutor()
            runtime._backup_app = server.streamable_http_app()
            runtime._backup_tool_fingerprint = McpAdmission(runtime._backup_app).wrap_tools(server)
            try:
                self.assertTrue(runtime.backup_workers_idle())
            finally:
                runtime._backup_executor.shutdown(wait=True)
                if getattr(server, "store_workers", None) is not None:
                    server.store_workers.close()

    def test_mcp_gate_rejects_new_requests_but_allows_existing_to_finish(self):
        async def scenario():
            active, release = asyncio.Event(), asyncio.Event()

            async def app(_scope, _receive, send):
                active.set()
                await release.wait()
                await send({"type": "http.response.body", "body": b"ok"})

            wrapper = McpAdmission(app)
            messages = []

            async def send(msg):
                messages.append(msg)

            task = asyncio.create_task(wrapper({"type": "http"}, None, send))
            await active.wait()
            owner = object()
            wrapper.requests.begin_drain(owner)
            rejected = []

            async def reject(msg):
                rejected.append(msg)

            await wrapper({"type": "http"}, None, reject)
            self.assertEqual(rejected[0]["status"], 503)
            self.assertFalse(wrapper.requests.wait_drained(owner, 0))
            release.set()
            await task
            self.assertTrue(wrapper.requests.wait_drained(owner, 0))
            wrapper.requests.resume(owner)

        asyncio.run(scenario())

    def test_idle_sse_does_not_block_but_detached_tool_does(self):
        async def scenario():
            active, release = asyncio.Event(), asyncio.Event()

            async def app(_scope, _receive, _send):
                active.set()
                await release.wait()

            wrapper = McpAdmission(app, "/mcp", True)

            async def send(_msg):
                pass

            scope = {
                "type": "http",
                "method": "GET",
                "path": "/mcp",
                "headers": [(b"accept", b"text/event-stream")],
            }
            stream = asyncio.create_task(wrapper(scope, None, send))
            await active.wait()
            owner = object()
            wrapper.requests.begin_drain(owner)
            self.assertTrue(wrapper.requests.wait_drained(owner, 0))
            wrapper.requests.resume(owner)
            release.set()
            await stream
            started, finished = asyncio.Event(), asyncio.Event()

            async def tool():
                started.set()
                await finished.wait()

            server = SimpleNamespace(
                _tool_manager=SimpleNamespace(
                    _tools={"tool": SimpleNamespace(fn=tool, is_async=True)}
                )
            )
            wrapper.wrap_tools(server)
            task = asyncio.create_task(server._tool_manager._tools["tool"].fn())
            await started.wait()
            wrapper.tools.begin_drain(owner)
            self.assertFalse(wrapper.tools.wait_drained(owner, 0))
            finished.set()
            await task
            self.assertTrue(wrapper.tools.wait_drained(owner, 0))
            wrapper.tools.resume(owner)

        asyncio.run(scenario())

    def test_committing_closes_idle_sse_without_touching_requests(self):
        async def scenario():
            started = asyncio.Event()

            async def app(_scope, receive, _send):
                started.set()
                self.assertEqual(await receive(), {"type": "http.disconnect"})

            async def never_receive():
                await asyncio.Event().wait()

            async def send(_msg):
                pass

            wrapper = McpAdmission(app, "/mcp", True)
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/mcp",
                "headers": [(b"accept", b"text/event-stream")],
            }
            stream = asyncio.create_task(wrapper(scope, never_receive, send))
            await started.wait()
            owner = object()
            wrapper.requests.begin_drain(owner)
            self.assertTrue(wrapper.requests.wait_drained(owner, 0))
            wrapper.close_idle_sse()
            await asyncio.wait_for(stream, 1)
            wrapper.requests.resume(owner)

        asyncio.run(scenario())

    def test_signal_lease_and_commit_survive_lost_controller(self):
        program = """
import json, signal, sys
from types import SimpleNamespace
from minimal_kanban.api.request_admission import ApiRequestAdmission
from minimal_kanban.mcp.backup_drain import BackupDrain, controlled_wait
mcp=SimpleNamespace(backup_admission=SimpleNamespace(requests=ApiRequestAdmission(), tools=ApiRequestAdmission(), close_idle_sse=lambda: None), backup_workers_idle=lambda: True)
agent=SimpleNamespace(backup_admission=ApiRequestAdmission())
api=SimpleNamespace(_request_admission=ApiRequestAdmission())
drain=BackupDrain(mcp, agent, api)
stop=[False]
signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__(0, True))
controlled_wait(stop,drain,sys.argv[1],prepare_timeout=1,lease_timeout=0.4)
with open(sys.argv[2], 'w') as out: json.dump({'open':mcp.backup_admission.requests.enter()},out)
"""
        with tempfile.TemporaryDirectory() as temp:
            status, final = Path(temp) / "status", Path(temp) / "final"

            def wait_state(expected):
                end = time.monotonic() + 4
                while time.monotonic() < end:
                    if status.exists():
                        data = json.loads(status.read_text())
                        if data["state"] == expected:
                            return data
                    time.sleep(0.02)
                self.fail("expected control state not reached: " + expected)

            def launch():
                return subprocess.Popen([sys.executable, "-c", program, str(status), str(final)])

            proc = launch()
            try:
                idle = wait_state("idle")
                os.kill(proc.pid, signal.SIGUSR1)
                ready = wait_state("ready")
                self.assertEqual(idle["generation"], ready["generation"])
                self.assertTrue(ready["attempt"])
                wait_state("aborted")
                os.kill(proc.pid, signal.SIGTERM)
                self.assertEqual(proc.wait(4), 0)
                self.assertTrue(json.loads(final.read_text())["open"])
                final.unlink()
                proc = launch()
                again = wait_state("idle")
                self.assertNotEqual(idle["generation"], again["generation"])
                os.kill(proc.pid, signal.SIGUSR1)
                ready = wait_state("ready")
                os.kill(proc.pid, signal.SIGUSR2)
                committed = wait_state("committed")
                self.assertEqual(
                    (ready["generation"], ready["attempt"]),
                    (committed["generation"], committed["attempt"]),
                )
                time.sleep(0.6)
                os.kill(proc.pid, signal.SIGHUP)
                time.sleep(0.2)
                self.assertEqual(json.loads(status.read_text())["state"], "committed")
                os.kill(proc.pid, signal.SIGTERM)
                self.assertEqual(proc.wait(4), 0)
                self.assertFalse(json.loads(final.read_text())["open"])
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(4)

    def test_visible_commit_survives_receipt_write_error(self):
        program = """
import json, os, signal, sys
from types import SimpleNamespace
from minimal_kanban.api.request_admission import ApiRequestAdmission
from minimal_kanban.mcp.backup_drain import BackupDrain, controlled_wait
mcp=SimpleNamespace(backup_admission=SimpleNamespace(requests=ApiRequestAdmission(), tools=ApiRequestAdmission(), close_idle_sse=lambda: None), backup_workers_idle=lambda: True)
drain=BackupDrain(mcp, SimpleNamespace(backup_admission=ApiRequestAdmission()), SimpleNamespace(_request_admission=ApiRequestAdmission()))
original=os.replace
def replace_then_fail(source, target):
    state=json.load(open(source))['state']
    original(source,target)
    if state=='committed': raise OSError('synthetic failure after rename')
os.replace=replace_then_fail
stop=[False]
signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__(0, True))
try: controlled_wait(stop,drain,sys.argv[1],prepare_timeout=1,lease_timeout=1)
except OSError: pass
with open(sys.argv[2], 'w') as out: json.dump({'open':mcp.backup_admission.requests.enter()},out)
"""
        with tempfile.TemporaryDirectory() as temp:
            status, final = Path(temp) / "status", Path(temp) / "final"
            proc = subprocess.Popen([sys.executable, "-c", program, str(status), str(final)])

            def wait_state(expected):
                end = time.monotonic() + 4
                while time.monotonic() < end:
                    if status.exists() and json.loads(status.read_text())["state"] == expected:
                        return
                    time.sleep(0.02)
                self.fail("expected state not reached: " + expected)

            try:
                wait_state("idle")
                os.kill(proc.pid, signal.SIGUSR1)
                wait_state("ready")
                os.kill(proc.pid, signal.SIGUSR2)
                wait_state("committed")
                os.kill(proc.pid, signal.SIGHUP)
                time.sleep(0.2)
                self.assertIsNone(proc.poll())
                os.kill(proc.pid, signal.SIGTERM)
                self.assertEqual(proc.wait(4), 0)
                self.assertFalse(json.loads(final.read_text())["open"])
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(4)


if __name__ == "__main__":
    unittest.main()
