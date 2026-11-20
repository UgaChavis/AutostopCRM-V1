from __future__ import annotations

import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.pdf import PdfRenderError  # noqa: E402
from minimal_kanban.printing.printers import PrinterBackendError  # noqa: E402
from minimal_kanban.storage.json_store import (  # noqa: E402
    StateFileCorruptedError,
    StateWriteConflictError,
)
from tests import test_printing_audit_api as audit_fixtures  # noqa: E402


class PrintingBackendConcurrencyTests(unittest.TestCase):
    setUp = audit_fixtures.PrintingAuditApiTests.setUp
    request = audit_fixtures.PrintingAuditApiTests.request
    create_printable_card = audit_fixtures.PrintingAuditApiTests.create_printable_card

    def delayed_backend(self, native, operation):
        entered, release = threading.Event(), threading.Event()
        captured = {}

        def backend(html, **options):
            captured.update(html=html, options=options)
            entered.set()
            if not release.wait(5):
                raise AssertionError("Synthetic backend was not released")
            return None if native else b"%PDF-SYNTHETIC"

        target = "print_html" if native else "render_html_to_pdf_bytes"
        with patch(f"minimal_kanban.printing.service.{target}", side_effect=backend) as mocked:
            with ThreadPoolExecutor(max_workers=3) as executor:
                future = executor.submit(operation)
                self.assertTrue(entered.wait(3), "Print backend not entered")
                try:
                    yield executor, captured, future, mocked
                finally:
                    release.set()

    def test_workspace_printer_discovery_does_not_block_card_updates(self):
        card_id = self.create_printable_card()
        self.store._committed_card_source = None  # Exercise the ordinary bundle fallback.
        entered, release = threading.Event(), threading.Event()

        def discover(**options):
            entered.set()
            self.assertTrue(release.wait(5), "Synthetic printer discovery was not released")
            return []

        with (
            patch("minimal_kanban.printing.service.list_printers", side_effect=discover),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            workspace = executor.submit(
                self.request, "/api/get_repair_order_print_workspace", {"card_id": card_id}
            )
            try:
                self.assertTrue(entered.wait(3), "Printer discovery not entered")
                status, changed = executor.submit(
                    self.request, "/api/update_card", {"card_id": card_id, "title": "UPDATED TITLE"}
                ).result(2)
                self.assertEqual(status, 200, changed)
            finally:
                release.set()
            status, old_workspace = workspace.result(2)

        self.assertEqual(status, 200, old_workspace)
        self.assertIn("Synthetic print audit", old_workspace["data"]["heading"])
        with patch("minimal_kanban.printing.service.list_printers", return_value=[]):
            status, new_workspace = self.request(
                "/api/get_repair_order_print_workspace", {"card_id": card_id}
            )
        self.assertEqual(status, 200, new_workspace)
        self.assertIn("UPDATED TITLE", new_workspace["data"]["heading"])

    def test_inspection_autofill_preserves_a_draft_saved_while_the_model_is_running(self):
        for initially_saved in (False, True):
            with self.subTest(initially_saved=initially_saved):
                card_id = self.create_printable_card()
                query = {"card_id": card_id}
                if initially_saved:
                    status, result = self.request(
                        "/api/save_inspection_sheet_form",
                        {**query, "form_data": {"findings": "First draft"}},
                    )
                    self.assertEqual(status, 200, result)
                entered, release = threading.Event(), threading.Event()

                def delayed_model(**kwargs):
                    entered.set()
                    self.assertTrue(release.wait(10), "Synthetic model was not released")
                    return {"findings": "Late model result"}

                with (
                    patch("minimal_kanban.services.card_service.OpenAIJsonAgentClient") as factory,
                    ThreadPoolExecutor(max_workers=1) as worker,
                ):
                    factory.return_value.complete_json.side_effect = delayed_model
                    factory.return_value.model = "synthetic-model"
                    pending = worker.submit(
                        self.request, "/api/autofill_inspection_sheet_form", query
                    )
                    try:
                        self.assertTrue(entered.wait(5), "Model call not entered")
                        status, manual = self.request(
                            "/api/save_inspection_sheet_form",
                            {**query, "form_data": {"findings": "New operator draft"}},
                        )
                        self.assertEqual(status, 200, manual)
                    finally:
                        release.set()
                    status, conflict = pending.result(5)

                self.assertEqual(status, 409, conflict)
                self.assertEqual(conflict["error"]["code"], "inspection_sheet_update_conflict")
                status, loaded = self.request("/api/get_inspection_sheet_form", query)
                self.assertEqual(status, 200, loaded)
                self.assertEqual(loaded["data"]["form"], manual["data"]["form"])

    def test_inspection_autofill_rejects_changed_order_source_and_allows_a_timer_edit(self):
        for source_changed in (True, False):
            with self.subTest(source_changed=source_changed):
                query = {"card_id": self.create_printable_card()}
                status, original = self.request(
                    "/api/update_repair_order",
                    {**query, "repair_order": {"reason": "Original reason"}},
                )
                self.assertEqual(status, 200, original)
                entered, release = threading.Event(), threading.Event()

                def delayed_model(**kwargs):
                    entered.set()
                    self.assertTrue(release.wait(10), "Synthetic model was not released")
                    return {"findings": "Built from the supplied reason"}

                with (
                    patch("minimal_kanban.services.card_service.OpenAIJsonAgentClient") as factory,
                    ThreadPoolExecutor(max_workers=1) as worker,
                ):
                    factory.return_value.complete_json.side_effect = delayed_model
                    factory.return_value.model = "synthetic-model"
                    pending = worker.submit(
                        self.request,
                        "/api/autofill_inspection_sheet_form",
                        {**query, "repair_order": {"reason": "Editor reason"}},
                    )
                    try:
                        self.assertTrue(entered.wait(5), "Model call not entered")
                        if source_changed:
                            status, changed = self.request(
                                "/api/update_repair_order",
                                {**query, "repair_order": {"reason": "New persisted reason"}},
                            )
                        else:
                            status, changed = self.request(
                                "/api/set_card_deadline", {**query, "deadline": {"minutes": 5}}
                            )
                        self.assertEqual(status, 200, changed)
                    finally:
                        release.set()
                    status, result = pending.result(5)

                if source_changed:
                    self.assertEqual(status, 409, result)
                    self.assertEqual(result["error"]["code"], "inspection_sheet_source_conflict")
                    status, loaded = self.request("/api/get_inspection_sheet_form", query)
                    self.assertEqual(status, 200, loaded)
                    self.assertFalse(loaded["data"]["meta"]["has_saved_draft"])
                else:
                    self.assertEqual(status, 200, result)
                    self.assertEqual(
                        result["data"]["form"]["findings"], "Built from the supplied reason"
                    )

    def test_pdf_snapshot_survives_concurrent_card_and_settings_edits(self):
        card_id = self.create_printable_card()
        payload = {"card_id": card_id, "selected_document_ids": ["invoice"]}
        for executor, captured, future, backend in self.delayed_backend(
            False, lambda: self.request("/api/export_repair_order_print_pdf", payload)
        ):
            status, read = executor.submit(self.request, "/api/get_cards", {}).result(2)
            self.assertEqual(status, 200, read)
            status, changed = executor.submit(
                self.request, "/api/update_card", {"card_id": card_id, "title": "CONCURRENT TITLE"}
            ).result(2)
            self.assertEqual(status, 200, changed)
            status, settings = executor.submit(
                self.request,
                "/api/save_print_module_settings",
                {"service_profile": {"company_name": "CONCURRENT SERVICE"}},
            ).result(2)
            self.assertEqual(status, 200, settings)
            after_changes = copy.deepcopy(self.store.read_bundle())
            self.assertNotIn("CONCURRENT TITLE", captured["html"])
            self.assertNotIn("CONCURRENT SERVICE", captured["html"])
        status, response = future.result(2)
        self.assertEqual(status, 200, response)
        self.assertEqual(response["data"]["mime_type"], "application/pdf")
        backend.assert_called_once()
        self.assertEqual(self.store.read_bundle(), after_changes)

    def test_manual_pdf_does_not_block_readers_or_write_an_audit(self):
        before = copy.deepcopy(self.store.read_bundle())
        payload = {
            "document_without_card": True,
            "manual_document": {
                "tax_label": "Без НДС",
                "works": [{"name": "Synthetic", "quantity": "1", "price": "1"}],
            },
            "selected_document_ids": ["invoice"],
        }
        for executor, _, future, _ in self.delayed_backend(
            False, lambda: self.request("/api/export_repair_order_print_pdf", payload)
        ):
            self.assertEqual(executor.submit(self.request, "/api/get_cards", {}).result(2)[0], 200)
        status, response = future.result(2)
        self.assertEqual(status, 200, response)
        self.assertTrue(response["data"]["meta"]["document_without_card"])
        self.assertEqual(self.store.read_bundle(), before)

    def test_native_audit_uses_latest_bundle_after_edit_or_removal(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                card_id = self.create_printable_card()
                payload = {
                    "card_id": card_id,
                    "selected_document_ids": ["invoice"],
                    "printer_name": "Synthetic Printer",
                    "actor_name": "PRINT ACTOR",
                }
                for executor, captured, future, backend in self.delayed_backend(
                    True, lambda: self.request("/api/print_repair_order_documents", payload)
                ):
                    self.assertEqual(
                        executor.submit(self.request, "/api/get_cards", {}).result(2)[0], 200
                    )
                    if remove:
                        # Isolated storage fixture models a concurrent maintenance deletion.
                        bundle = copy.deepcopy(self.store.read_bundle())
                        bundle["cards"] = [card for card in bundle["cards"] if card.id != card_id]
                        self.store.write_bundle(**bundle)
                    else:
                        status, changed = executor.submit(
                            self.request,
                            "/api/update_card",
                            {"card_id": card_id, "title": "CONCURRENT NATIVE TITLE"},
                        ).result(2)
                        self.assertEqual(status, 200, changed)
                    after_changes = copy.deepcopy(self.store.read_bundle())
                    self.assertNotIn("CONCURRENT NATIVE TITLE", captured["html"])
                status, response = future.result(2)
                self.assertEqual(status, 200, response)
                backend.assert_called_once()
                after = self.store.read_bundle()
                self.assertEqual(
                    {key: value for key, value in after.items() if key != "events"},
                    {key: value for key, value in after_changes.items() if key != "events"},
                )
                event = after["events"][-1]
                self.assertEqual(event.action, "repair_order_printed")
                self.assertEqual(event.card_id, card_id)
                self.assertEqual(event.actor_name, "PRINT ACTOR")
                self.assertEqual(event.details["selected_document_ids"], ["invoice"])

    def test_successful_spool_is_not_repeated_when_audit_cannot_commit(self):
        card_id = self.create_printable_card()
        for error, code, status in (
            (StateWriteConflictError("synthetic CAS"), "state_write_conflict", 409),
            (OSError("synthetic disk failure"), "internal_error", 500),
            (StateFileCorruptedError("synthetic damaged state"), "state_file_corrupted", 503),
            (RuntimeError("synthetic unexpected audit failure"), "internal_error", 500),
        ):
            before = copy.deepcopy(self.store.read_bundle())
            with (
                self.subTest(code=code),
                patch("minimal_kanban.printing.service.print_html") as backend,
                patch.object(self.store, "write_cached_bundle", side_effect=error),
                patch.object(self.store, "write_bundle", side_effect=error),
            ):
                actual, response = self.request(
                    "/api/print_repair_order_documents",
                    {
                        "card_id": card_id,
                        "selected_document_ids": ["invoice"],
                        "printer_name": "Synthetic Printer",
                    },
                )
            self.assertEqual(actual, status, response)
            self.assertEqual(response["error"]["code"], code)
            self.assertTrue(response["error"]["details"]["printing_completed"])
            self.assertIn("Не повторяйте печать", response["error"]["message"])
            backend.assert_called_once()
            self.assertEqual(self.store.read_bundle(), before)

    def test_backend_failure_never_claims_completed_print_or_writes_audit(self):
        card_id = self.create_printable_card()
        before = copy.deepcopy(self.store.read_bundle())
        for route, backend, error in (
            ("print_repair_order_documents", "print_html", PrinterBackendError("Unknown spool")),
            (
                "export_repair_order_print_pdf",
                "render_html_to_pdf_bytes",
                PdfRenderError("Failed PDF"),
            ),
        ):
            with patch(f"minimal_kanban.printing.service.{backend}", side_effect=error):
                status, response = self.request(
                    "/api/" + route,
                    {
                        "card_id": card_id,
                        "selected_document_ids": ["invoice"],
                        "printer_name": "Synthetic",
                    },
                )
            self.assertGreaterEqual(status, 500)
            self.assertFalse(response["error"].get("details", {}).get("printing_completed"))
            self.assertEqual(self.store.read_bundle(), before)

    def test_prepared_job_contains_no_live_mutable_context(self):
        card_id = self.create_printable_card()
        card = next(card for card in self.store.read_bundle()["cards"] if card.id == card_id)
        job = self.service._print_module.prepare_documents_pdf(
            card, selected_document_ids=["invoice"]
        )
        with self.assertRaises(FrozenInstanceError):
            job.html = "replaced"
        self.assertTrue(all(isinstance(value, (str, int)) for value in vars(job).values()))

    def test_backend_jobs_remain_serial_without_holding_the_crm_lock(self):
        card_id = self.create_printable_card()
        entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
        captured = []

        def backend(html, **options):
            captured.append(html)
            if len(captured) == 1:
                entered.set()
                self.assertTrue(release.wait(5))
            else:
                self.assertTrue(release.is_set(), "Concurrent Qt backend")
            return b"%PDF-SYNTHETIC"

        payload = {"card_id": card_id, "selected_document_ids": ["invoice"]}

        def second():
            second_started.set()
            return self.service.export_repair_order_print_pdf(payload)

        with patch("minimal_kanban.printing.service.render_html_to_pdf_bytes", side_effect=backend):
            with ThreadPoolExecutor(max_workers=3) as executor:
                first = executor.submit(self.service.export_repair_order_print_pdf, payload)
                self.assertTrue(entered.wait(3))
                queued = executor.submit(second)
                self.assertTrue(second_started.wait(3))
                try:
                    self.assertEqual(
                        executor.submit(self.request, "/api/get_cards", {}).result(2)[0], 200
                    )
                    status, changed = executor.submit(
                        self.request,
                        "/api/update_repair_order",
                        {
                            "card_id": card_id,
                            "repair_order": {
                                "tax_label": "Без НДС",
                                "works": [
                                    {"name": "SECOND SNAPSHOT", "quantity": "1", "price": "200"}
                                ],
                            },
                        },
                    ).result(2)
                    self.assertEqual(status, 200, changed)
                    self.assertEqual(len(captured), 1)
                    self.assertNotIn("SECOND SNAPSHOT", captured[0])
                finally:
                    release.set()
                first.result(3)
                queued.result(3)
        self.assertEqual(len(captured), 2)
        self.assertIn("SECOND SNAPSHOT", captured[1])
