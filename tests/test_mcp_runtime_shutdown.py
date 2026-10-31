from __future__ import annotations

import asyncio
import logging
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.mcp.runtime import McpServerRuntime  # noqa: E402


class McpRuntimeShutdownTests(unittest.TestCase):
    def runtime(self, server):
        app = SimpleNamespace(
            settings=SimpleNamespace(host="127.0.0.1", port=1, streamable_http_path="/mcp")
        )
        runtime = McpServerRuntime(app, logging.getLogger(__name__))
        runtime._uvicorn_server = server
        runtime._shutdown_complete = False
        runtime._thread = threading.Thread(target=runtime._run_server, daemon=True)
        runtime._thread.start()
        return runtime

    def test_stop_waits_for_cancelled_default_executor_work(self):
        started = threading.Event()
        release = threading.Event()
        returned = threading.Event()
        failures = []

        class Server:
            should_exit = False

            async def serve(self):
                def work():
                    started.set()
                    if not release.wait(5):
                        raise TimeoutError("synthetic worker timeout")

                task = asyncio.create_task(asyncio.to_thread(work))
                while not started.is_set():
                    await asyncio.sleep(0.005)
                while not self.should_exit:
                    await asyncio.sleep(0.005)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        runtime = self.runtime(Server())

        def stop():
            try:
                runtime.stop()
            except BaseException as error:  # noqa: BLE001 - propagate worker failure to test.
                failures.append(error)
            finally:
                returned.set()

        thread = threading.Thread(target=stop)
        try:
            self.assertTrue(started.wait(2))
            thread.start()
            self.assertFalse(returned.wait(0.1))
        finally:
            release.set()
            if thread.ident is not None:
                thread.join(5)
        self.assertTrue(returned.is_set())
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(runtime._shutdown_complete)

    def test_serve_exception_never_reports_successful_stop(self):
        class Server:
            should_exit = False

            async def serve(self):
                raise RuntimeError("synthetic serve error")

        runtime = self.runtime(Server())
        # A failure after the server was ready is not a clean stop.
        runtime._reached_ready = True
        runtime._thread.join(5)
        with self.assertRaisesRegex(RuntimeError, "завершился с ошибкой"):
            runtime.stop()
        self.assertTrue(runtime._shutdown_complete)
        with self.assertRaisesRegex(RuntimeError, "завершился с ошибкой"):
            runtime.stop()

    def test_failed_start_can_be_cleaned_up_before_retry(self):
        class Server:
            should_exit = False

            async def serve(self):
                raise RuntimeError("synthetic startup error")

        runtime = self.runtime(Server())
        runtime._thread.join(5)
        runtime._stop_failed_start()
        runtime.stop()
        self.assertTrue(runtime._shutdown_complete)
        runtime._server.streamable_http_app = lambda: (_ for _ in ()).throw(
            RuntimeError("next attempt reached")
        )
        with self.assertRaisesRegex(RuntimeError, "next attempt reached"):
            runtime.start()

    def test_failure_before_thread_creation_requires_no_drain(self):
        app = SimpleNamespace(
            settings=SimpleNamespace(host="127.0.0.1", port=1, streamable_http_path="/mcp")
        )
        app.streamable_http_app = lambda: (_ for _ in ()).throw(
            RuntimeError("synthetic startup error")
        )
        runtime = McpServerRuntime(app, logging.getLogger(__name__))
        with self.assertRaisesRegex(RuntimeError, "startup error"):
            runtime.start()
        runtime.stop()
        self.assertIsNone(runtime._shutdown_complete)

    def test_interrupted_thread_start_preserves_live_thread_for_stop(self):
        class Server:
            should_exit = False

            async def serve(self):
                while not self.should_exit:
                    await asyncio.sleep(0.005)

        app = SimpleNamespace(
            settings=SimpleNamespace(host="127.0.0.1", port=1, streamable_http_path="/mcp")
        )
        app.streamable_http_app = lambda: object()
        runtime = McpServerRuntime(app, logging.getLogger(__name__))
        original_start = threading.Thread.start

        def started_then_interrupted(thread):
            original_start(thread)
            raise KeyboardInterrupt

        with (
            patch("minimal_kanban.mcp.runtime.uvicorn.Server", return_value=Server()),
            patch.object(threading.Thread, "start", started_then_interrupted),
            self.assertRaises(KeyboardInterrupt),
        ):
            runtime.start()
        self.assertIsNotNone(runtime._thread)
        self.assertTrue(runtime._thread.is_alive())
        with self.assertRaisesRegex(RuntimeError, "не подтверждено"):
            runtime.start()
        runtime.stop()
        self.assertTrue(runtime._shutdown_complete)

    def test_start_does_not_report_ready_while_shutdown_is_pending(self):
        app = SimpleNamespace(
            settings=SimpleNamespace(host="127.0.0.1", port=1, streamable_http_path="/mcp")
        )
        runtime = McpServerRuntime(app, logging.getLogger(__name__))
        runtime._thread = unittest.mock.Mock()
        runtime._thread.is_alive.return_value = True
        runtime._uvicorn_server = SimpleNamespace(should_exit=True)
        runtime._reached_ready = True
        with self.assertRaisesRegex(RuntimeError, "не подтверждено"):
            runtime.start()

    def test_cancelled_task_cleanup_error_does_not_report_clean_stop(self):
        class Server:
            should_exit = False

            async def serve(self):
                async def fail_on_cancel():
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        raise RuntimeError("synthetic cleanup error") from None

                asyncio.create_task(fail_on_cancel())
                await asyncio.sleep(0)

        runtime = self.runtime(Server())
        runtime._thread.join(5)
        with self.assertRaisesRegex(RuntimeError, "не подтверждено"):
            runtime.start()
        with self.assertRaisesRegex(RuntimeError, "Очистка"):
            runtime.stop()
        self.assertFalse(runtime._shutdown_complete)

    def test_async_generator_cleanup_error_does_not_report_clean_stop(self):
        class Server:
            should_exit = False
            generator = None

            async def serve(self):
                async def generator():
                    try:
                        yield 1
                    finally:
                        raise RuntimeError("synthetic generator cleanup error")

                self.generator = generator()
                await anext(self.generator)

        runtime = self.runtime(Server())
        runtime._thread.join(5)
        with self.assertRaisesRegex(RuntimeError, "Очистка"):
            runtime.stop()
        self.assertFalse(runtime._shutdown_complete)

    def test_executor_shutdown_error_is_not_mistaken_for_clean_exit(self):
        class Server:
            should_exit = False

            async def serve(self):
                return None

        loop = asyncio.new_event_loop()

        async def failed_shutdown():
            raise RuntimeError("synthetic shutdown error")

        with patch("minimal_kanban.mcp.runtime.asyncio.new_event_loop", return_value=loop):
            loop.shutdown_default_executor = failed_shutdown
            runtime = self.runtime(Server())
            runtime._thread.join(5)
        try:
            with self.assertRaisesRegex(RuntimeError, "очистка|Очистка"):
                runtime.stop()
            self.assertFalse(runtime._shutdown_complete)
            runtime._stop_failed_start()
            with self.assertRaisesRegex(RuntimeError, "Очистка"):
                runtime.start()
            with self.assertRaisesRegex(RuntimeError, "Очистка"):
                runtime.stop()
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
