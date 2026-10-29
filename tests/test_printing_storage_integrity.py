"""Unreadable print stores must neither become defaults nor publish deletes."""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.errors import PrintModuleError  # noqa: E402
from minimal_kanban.printing.service import PrintModuleService  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.storage.change_feed_store import ChangeFeedStore  # noqa: E402
from tests import test_api  # noqa: E402
from tests.test_printing_service import build_card  # noqa: E402


class PrintingStorageIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.feed = ChangeFeedStore(self.root / "change_feed.sqlite3")
        self.service = PrintModuleService(self.root, change_feed_store=self.feed)
        self.card = build_card()

    def seed(self) -> None:
        self.service.save_settings({"service_profile": {"bank_name": "Synthetic bank"}})
        self.template = self.service.save_template(
            document_type="invoice", name="Synthetic", content="<p>Saved template</p>"
        )["template"]
        self.service.set_default_template(document_type="invoice", template_id=self.template["id"])
        self.service.save_inspection_sheet_form(self.card, form_data={"findings": "Saved finding"})

    def stores(self, service: PrintModuleService):
        return (
            (
                service._settings_path,
                service._read_settings,
                lambda: service.save_settings({"copies": 4}),
            ),
            (
                service._templates_path,
                service._read_custom_templates,
                lambda: service.save_template(document_type="invoice", name="New", content="New"),
            ),
            (
                service._inspection_sheet_forms_path,
                service._read_inspection_sheet_form_map,
                lambda: service.save_inspection_sheet_form(
                    self.card, form_data={"findings": "New"}
                ),
            ),
        )

    def assert_unavailable(self, action, path: Path) -> None:
        with self.assertRaises(PrintModuleError) as caught:
            action()
        self.assertEqual(caught.exception.code, "print_store_unavailable")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.details, {"file": path.name})

    def test_missing_files_are_normal_defaults_and_can_be_saved(self) -> None:
        for path, read, save in self.stores(self.service):
            with self.subTest(file=path.name):
                self.assertFalse(path.exists())
                read()
                self.assertFalse(path.exists())
                save()
                self.assertTrue(path.exists())
                read()

    def test_corrupt_bytes_block_reads_writes_and_cold_start_projection(self) -> None:
        self.seed()
        for path, _, _ in self.stores(self.service):
            original = path.read_bytes()
            for damaged in (original[:-1], b"", b"\xff", b"null", b'"scalar"', b'{"x":NaN}'):
                with self.subTest(file=path.name, damaged=damaged[:10]):
                    path.write_bytes(damaged)
                    before = self.feed.raw_events_for_test()
                    restarted = PrintModuleService(self.root, change_feed_store=self.feed)
                    _, read, save = next(item for item in self.stores(restarted) if item[0] == path)
                    self.assert_unavailable(read, path)
                    self.assert_unavailable(save, path)
                    restarted.reconcile_change_feed()
                    self.assertEqual(path.read_bytes(), damaged)
                    self.assertEqual(self.feed.raw_events_for_test(), before)
                    path.write_bytes(original)
                    restarted.reconcile_change_feed()
                    self.assertFalse(restarted._print_feed_initialization_pending)
                    self.assertEqual(self.feed.raw_events_for_test(), before)
                    read()

    def test_wrong_nested_shapes_fail_closed_without_discarding_records(self) -> None:
        self.seed()
        cases = (
            (
                self.service._settings_path,
                self.service._read_settings,
                [
                    [],
                    {"service_profile": []},
                    {"service_profile": None},
                    {"default_template_ids": []},
                    {"_service_profile_explicit_empty_fields": "bank_name"},
                    {"_service_profile_explicit_empty_fields": [1]},
                    {"service_profile": {"bank_name": {"invalid": "shape"}}},
                ],
            ),
            (
                self.service._templates_path,
                self.service._read_custom_templates,
                [
                    {},
                    [None],
                    [self.template, {"id": "broken"}],
                    [{**self.template, "content": []}],
                ],
            ),
            (
                self.service._inspection_sheet_forms_path,
                self.service._read_inspection_sheet_form_map,
                [
                    [],
                    {self.card.id: None},
                    {self.card.id: {"findings": []}},
                    {self.card.id: {"planned_work_rows": {"name": "lost"}}},
                ],
            ),
        )
        for path, read, payloads in cases:
            original = path.read_bytes()
            for payload in payloads:
                with self.subTest(file=path.name, payload=payload):
                    damaged = json.dumps(payload).encode("utf-8")
                    path.write_bytes(damaged)
                    self.assert_unavailable(read, path)
                    self.assertEqual(path.read_bytes(), damaged)
            path.write_bytes(original)

    def test_io_failure_does_not_turn_into_defaults_or_overwrite(self) -> None:
        self.seed()
        before = self.feed.raw_events_for_test()
        for path, read, save in self.stores(self.service):
            original = path.read_bytes()
            with (
                self.subTest(file=path.name),
                patch(
                    "minimal_kanban.printing.service._read_print_json_text",
                    side_effect=PermissionError("private path must not reach the API"),
                ),
            ):
                self.assert_unavailable(read, path)
                self.assert_unavailable(save, path)
                PrintModuleService(self.root, change_feed_store=self.feed)
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.feed.raw_events_for_test(), before)

    def test_unrelated_save_is_committed_and_feed_resumes_after_recovery(self) -> None:
        self.seed()
        path = self.service._templates_path
        original = path.read_bytes()
        path.write_bytes(original[:-1])
        before = len(self.feed.raw_events_for_test())
        restarted = PrintModuleService(self.root, change_feed_store=self.feed)
        self.assertEqual(restarted.save_settings({"copies": 4})["settings"]["copies"], 4)
        self.assertEqual(len(self.feed.raw_events_for_test()), before)
        path.write_bytes(original)
        # This save also exercises full deferred initialization under an already
        # acquired process lock; reacquiring that lock would time out on Windows.
        restarted.save_settings({"copies": 5})
        events = self.feed.raw_events_for_test()[before:]
        self.assertEqual(
            [(item["entity_type"], item["tombstone"]) for item in events],
            [("print_settings", False)],
        )
        self.assertFalse(restarted._print_feed_initialization_pending)

    def test_corrupt_settings_prevents_template_write_before_any_commit(self) -> None:
        self.seed()
        settings_path = self.service._settings_path
        settings_path.write_bytes(settings_path.read_bytes()[:-1])
        original = self.service._templates_path.read_bytes()
        self.assert_unavailable(
            lambda: self.service.save_template(document_type="invoice", name="New", content="New"),
            settings_path,
        )
        self.assert_unavailable(
            lambda: self.service.duplicate_template(template_id=self.template["id"]),
            settings_path,
        )
        self.assertEqual(self.service._templates_path.read_bytes(), original)

    def test_concurrent_instances_cannot_overwrite_corrupt_store(self) -> None:
        self.seed()
        for path, _, _ in self.stores(self.service):
            with self.subTest(file=path.name):
                original = path.read_bytes()
                damaged = original[:-1]
                path.write_bytes(damaged)
                services = [
                    PrintModuleService(self.root, change_feed_store=self.feed) for _ in range(2)
                ]
                before = self.feed.raw_events_for_test()
                errors = []
                start = threading.Barrier(2)

                def attempt(service):
                    try:
                        start.wait(timeout=5)
                        next(item[2] for item in self.stores(service) if item[0] == path)()
                    except Exception as exc:
                        errors.append(exc)

                threads = [
                    threading.Thread(target=attempt, args=(service,)) for service in services
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(
                    [getattr(error, "code", None) for error in errors],
                    ["print_store_unavailable", "print_store_unavailable"],
                )
                self.assertEqual(path.read_bytes(), damaged)
                self.assertEqual(self.feed.raw_events_for_test(), before)
                path.write_bytes(original)

    def test_legacy_partial_settings_and_textual_inspection_rows_remain_valid(self) -> None:
        self.service._settings_path.write_text(
            json.dumps({"copies": "2", "service_profile": {"bank_name": "", "phone": None}}),
            encoding="utf-8",
        )
        self.assertEqual(self.service._read_settings().copies, 2)
        self.service._inspection_sheet_forms_path.write_text(
            json.dumps({self.card.id: {"planned_work_rows": ["Legacy work"], "findings": "Saved"}}),
            encoding="utf-8",
        )
        form = self.service.get_inspection_sheet_form(self.card)["form"]
        self.assertEqual(form["planned_work_rows"], [{"name": "Legacy work", "quantity": ""}])
        self.assertEqual(form["findings"], "Saved")


class PrintingStorageApiTests(unittest.TestCase):
    setUp = test_api.ApiServerTests.setUp
    request = test_api.ApiServerTests.request

    def test_corrupt_stores_return_503_while_cold_crm_and_general_api_stay_available(self) -> None:
        status, created = self.request(
            "/api/create_card", {"title": "Storage test", "deadline": {"hours": 1}}
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        printing = self.service._print_module
        cases = (
            (printing._settings_path, "/api/save_print_module_settings", {"copies": 3}),
            (
                printing._templates_path,
                "/api/save_print_template",
                {"document_type": "invoice", "name": "Synthetic", "content": "<p>Synthetic</p>"},
            ),
            (
                printing._inspection_sheet_forms_path,
                "/api/save_inspection_sheet_form",
                {"card_id": card_id, "form_data": {"findings": "Synthetic"}},
            ),
        )
        for path, route, payload in cases:
            with self.subTest(file=path.name):
                self.assertEqual(self.request(route, payload)[0], 200)
                original = path.read_bytes()
                damaged = original[:-1]
                path.write_bytes(damaged)
                logger = logging.getLogger("test.print.cold")
                logger.addHandler(logging.NullHandler())
                cold = CardService(self.store, logger)
                self.assertEqual(len(cold.get_cards({})["cards"]), 1)
                read_route = (
                    "/api/get_inspection_sheet_form"
                    if path == printing._inspection_sheet_forms_path
                    else "/api/get_repair_order_print_workspace"
                )
                for target, data in (
                    (route, payload),
                    (read_route, {"card_id": card_id}),
                ):
                    status, response = self.request(target, data)
                    self.assertEqual(status, 503, response.get("error"))
                    self.assertEqual(response["error"]["code"], "print_store_unavailable")
                    self.assertEqual(response["error"]["details"], {"file": path.name})
                self.assertEqual(self.request("/api/health", method="GET")[0], 200)
                self.assertEqual(self.request("/api/get_cards", {})[0], 200)
                self.assertEqual(path.read_bytes(), damaged)
                path.write_bytes(original)
                self.assertEqual(self.request(route, payload)[0], 200)
