from __future__ import annotations

# The fixture import sets up the source path.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.printing.errors import PrintModuleError
from minimal_kanban.repair_order import RepairOrderRow
from minimal_kanban.storage.json_store import JsonStore


class RepairOrderPrecisionTests(CardServiceCase):
    def test_legacy_adjustment_is_preserved_and_existing_print_guard_still_blocks(self):
        values = {
            "name": "Legacy",
            "quantity": "100.00000000637",
            "price": "134312.87284144427",
            "total": "13431287.30",
        }
        card_id = self.service.create_card({"title": "Legacy"})["card"]["id"]
        saved = self.service.update_repair_order(
            {"card_id": card_id, "repair_order": {"works": [values]}}
        )
        self.assertEqual(saved["repair_order"]["works"][0]["total"], "13431287.3")
        before = self.state_file.read_bytes()
        fresh = JsonStore(state_file=self.state_file, logger=self.logger)
        card = next(card for card in fresh.read_cards() if card.id == card_id)
        self.assertEqual(card.repair_order.works[0].total, "13431287.3")
        preview = self.service.preview_repair_order_print_documents(
            {"card_id": card_id, "selected_document_ids": ["repair_order"]}
        )
        self.assertFalse(preview["money_validation"]["valid"])
        self.assertIn(
            "line_amount_mismatch", {item["code"] for item in preview["money_validation"]["errors"]}
        )
        with self.assertRaises(PrintModuleError) as error:
            self.service._print_module.prepare_documents_pdf(
                card, selected_document_ids=["repair_order"]
            )
        self.assertEqual(error.exception.code, "document_money_validation_failed")
        self.assertEqual(before, self.state_file.read_bytes())

    def test_automatic_total_rounds_exact_product_once_and_preserves_explicit_adjustment(self):
        values = {"name": "Synthetic", "quantity": "100.00000000637", "price": "134312.87284144427"}
        self.assertEqual(RepairOrderRow.from_dict(values).total, "13431287.28")
        self.assertEqual(
            RepairOrderRow.from_dict({**values, "total": "13431287.29"}).total, "13431287.29"
        )
        self.assertEqual(
            RepairOrderRow.from_dict({**values, "total": "13431287.30"}).total, "13431287.3"
        )
        for field in ("works", "materials"):
            with self.subTest(field=field):
                card_id = self.service.create_card({"title": f"Precision {field}"})["card"]["id"]
                result = self.service.update_repair_order(
                    {"card_id": card_id, "repair_order": {field: [values]}}
                )
                self.assertEqual(result["repair_order"][field][0]["total"], "13431287.28")
                fresh = JsonStore(state_file=self.state_file, logger=self.logger)
                card = next(card for card in fresh.read_cards() if card.id == card_id)
                self.assertEqual(getattr(card.repair_order, field)[0].total, "13431287.28")
                preview = self.service.preview_repair_order_print_documents(
                    {"card_id": card_id, "selected_document_ids": ["repair_order"]}
                )
                html = "".join(page["html"] for page in preview["documents"][0]["pages"])
                self.assertIn("13 431 287,28", html)
                self.assertNotIn("13 431 287,29", html)
                module = self.service._print_module
                for job in (
                    module.prepare_documents_pdf(card, selected_document_ids=["repair_order"]),
                    module.prepare_print_documents(
                        card, selected_document_ids=["repair_order"], printer_name="Synthetic"
                    ),
                ):
                    self.assertIn("13 431 287,28", job.html)
                    self.assertNotIn("13 431 287,29", job.html)
