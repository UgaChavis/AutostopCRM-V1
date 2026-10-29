from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.printers import PrinterBackendError  # noqa: E402
from tests import test_api as api_fixtures  # noqa: E402


class PrintingAuditApiTests(unittest.TestCase):
    # Reuse the isolated HTTP server and all temporary-storage cleanups.
    setUp = api_fixtures.ApiServerTests.setUp
    request = api_fixtures.ApiServerTests.request

    def create_printable_card(self) -> str:
        status, created = self.request(
            "/api/create_card", {"title": "Synthetic print audit", "vehicle": "Synthetic car"}
        )
        self.assertEqual(status, 200, created)
        card_id = created["data"]["card"]["id"]
        status, updated = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "tax_label": "Без НДС",
                    "works": [{"name": "Synthetic work", "quantity": "1", "price": "100"}],
                },
            },
        )
        self.assertEqual(status, 200, updated)
        return card_id

    def test_print_audit_records_actual_normalized_documents(self) -> None:
        card_id = self.create_printable_card()
        cases = (
            (
                [" invoice ", "invoice", "unknown", "inspection_sheet"],
                ["invoice", "inspection_sheet"],
            ),
            (None, ["repair_order"]),
        )
        for selected, expected in cases:
            with self.subTest(selected=selected):
                before = copy.deepcopy(self.store.read_bundle())
                with patch("minimal_kanban.printing.service.print_html") as backend:
                    status, response = self.request(
                        "/api/print_repair_order_documents",
                        {
                            "card_id": card_id,
                            "selected_document_ids": selected,
                            "printer_name": "Synthetic Printer",
                            "actor_name": "SYNTHETIC AUDIT ACTOR",
                        },
                    )
                self.assertEqual(status, 200, response)
                backend.assert_called_once()
                self.assertEqual([item["id"] for item in response["data"]["documents"]], expected)
                self.assertNotIn("selected_document_ids", response["data"])
                after = self.store.read_bundle()
                self.assertEqual(len(after["events"]), len(before["events"]) + 1)
                event = after["events"][-1]
                self.assertEqual(event.action, "repair_order_printed")
                self.assertEqual(event.card_id, card_id)
                self.assertEqual(event.actor_name, "SYNTHETIC AUDIT ACTOR")
                self.assertEqual(event.details["selected_document_ids"], expected)
                self.assertEqual(
                    {key: value for key, value in after.items() if key != "events"},
                    {key: value for key, value in before.items() if key != "events"},
                )

    def test_failed_native_print_does_not_record_success(self) -> None:
        card_id = self.create_printable_card()
        before = copy.deepcopy(self.store.read_bundle())
        with patch(
            "minimal_kanban.printing.service.print_html",
            side_effect=PrinterBackendError("Synthetic unavailable printer"),
        ) as backend:
            status, response = self.request(
                "/api/print_repair_order_documents",
                {
                    "card_id": card_id,
                    "selected_document_ids": ["invoice", "inspection_sheet"],
                    "printer_name": "Synthetic Printer",
                },
            )
        self.assertEqual(status, 503, response)
        self.assertEqual(response["error"]["code"], "printer_unavailable")
        backend.assert_called_once()
        self.assertEqual(self.store.read_bundle(), before)
