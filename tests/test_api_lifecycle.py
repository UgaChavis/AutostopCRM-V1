from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.api.server import ApiServer  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class ApiLifecycleTests(unittest.TestCase):
    def test_parallel_servers_have_independent_handlers_ports_and_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            logger = logging.getLogger("test.api.lifecycle")
            servers = []
            try:
                for index in range(2):
                    store = JsonStore(
                        state_file=Path(temporary) / str(index) / "state.json", logger=logger
                    )
                    service = CardService(
                        store,
                        logger,
                        attachments_dir=Path(temporary) / str(index) / "attachments",
                        repair_orders_dir=Path(temporary) / str(index) / "repair-orders",
                    )
                    server = ApiServer(
                        service, logger, host="127.0.0.1", start_port=0, fallback_limit=1
                    )
                    server.start()
                    servers.append(server)
                first, second = servers
                self.assertNotEqual(first.port, second.port)
                self.assertIsNot(
                    first._server.RequestHandlerClass, second._server.RequestHandlerClass
                )
                first_thread = first._thread
                started = time.monotonic()
                first.stop()
                self.assertLess(time.monotonic() - started, 0.4)
                self.assertFalse(first_thread.is_alive())
                with urllib.request.urlopen(second.base_url + "/api/health", timeout=2) as response:
                    self.assertTrue(json.load(response)["ok"])
                self.assertTrue(second._thread.is_alive())
                first.stop()
            finally:
                for server in servers:
                    server.stop()
