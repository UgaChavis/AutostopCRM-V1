from __future__ import annotations

import ast
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.services.bundle_draft import BundleDraft, detach_card  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.errors import ServiceError  # noqa: E402


class CardBundlePolicyTests(unittest.TestCase):
    def setUp(self):
        self.service = CardService.__new__(CardService)
        self.service._logger = Mock()
        self.source = {
            name: [{"nested": ["original"]}]
            for name in (
                "columns",
                "cashboxes",
                "cash_transactions",
                "inventory_items",
                "inventory_movements",
                "stickies",
                "events",
            )
        }
        self.source["cards"] = [
            SimpleNamespace(id=value, client_id=f"client-{value}", position=i, nested=["original"])
            for i, value in enumerate(("card", "other"))
        ]
        self.source["clients"] = [
            SimpleNamespace(id=f"client-{value}", nested=["original"])
            for value in ("card", "other")
        ]
        self.source["settings"] = {"nested": ["original"]}
        self.signature = object()
        self.service._store = Mock()
        self.service._store.read_bundle_with_signature.return_value = (self.source, self.signature)

    def read_draft(self, card_id):
        return self.service._read_card_bundle_for_update(card_id)

    def test_four_domain_policy_isolates_nested_mutations_without_writing(self):
        before = deepcopy(self.source)
        draft = self.read_draft("card")
        self.assertIsInstance(draft, BundleDraft)
        self.assertIs(draft.source, self.source)
        self.assertIs(draft.signature, self.signature)
        for domain in ("columns", "cashboxes", "cash_transactions", "inventory_items"):
            self.assertIsNot(draft[domain][0], self.source[domain][0])
            draft[domain][0]["nested"].append("uncommitted")
        draft["cards"][0].nested.append("uncommitted")
        draft["clients"][0].nested.append("uncommitted")
        draft["settings"]["nested"].append("uncommitted")
        draft["events"].append({"pending": True})
        self.assertEqual(self.source, before)
        self.service._store.read_bundle_with_signature.assert_called_once_with()
        self.assertEqual(len(self.service._store.mock_calls), 1)

    def test_unaffected_objects_share_identity_but_lists_and_reordered_neighbors_do_not(self):
        draft = self.read_draft("card")
        for domain in ("inventory_movements", "stickies", "events"):
            self.assertIsNot(draft[domain], self.source[domain])
            self.assertIs(draft[domain][0], self.source[domain][0])
        self.assertIs(draft["cards"][1], self.source["cards"][1])
        self.assertIs(draft["clients"][1], self.source["clients"][1])
        detach_card(draft["cards"], draft["cards"][1]).position = 5
        self.assertEqual(self.source["cards"][1].position, 1)
        self.assertEqual(draft["cards"][1].position, 5)

    def test_card_id_error_contract_and_none_semantics_match_original_policy(self):
        for value in ("", False, 0, "unknown", " card "):
            with self.subTest(card_id=value):
                with self.assertRaises(ServiceError) as caught:
                    self.read_draft(value)
                error = caught.exception
                missing = not value
                self.assertEqual(
                    (error.code, error.message, error.status_code, error.details),
                    (
                        "validation_error" if missing else "not_found",
                        "Нужно передать card_id." if missing else "Карточка не найдена.",
                        400 if missing else 404,
                        {"field": "card_id"} if missing else {"card_id": value},
                    ),
                )
        unselected = self.read_draft(None)
        self.assertIs(unselected["cards"][0], self.source["cards"][0])
        self.assertIs(unselected["clients"][0], self.source["clients"][0])
        self.assertIsNot(unselected["cash_transactions"][0], self.source["cash_transactions"][0])

    def test_helper_forwards_original_card_id_and_exact_domains(self):
        result = object()
        self.service._read_bundle_for_update = Mock(return_value=result)
        for value in (None, False, 0, "", " card ", object()):
            with self.subTest(card_id=value):
                self.assertIs(self.read_draft(value), result)
                self.service._read_bundle_for_update.assert_called_with(
                    "columns", "cashboxes", "cash_transactions", "inventory_items", card_id=value
                )
        self.assertEqual(self.service._read_bundle_for_update.call_count, 6)

    def test_only_original_four_domain_callers_use_shared_policy(self):
        expected = {
            "card_service.py": {
                "set_card_board_summary",
                "set_card_ai_autofill",
                "cleanup_card_content",
                "run_full_card_enrichment",
                "_set_card_ai_autofill_with_agent_control",
                "_run_full_card_enrichment_with_agent_control",
                "mark_card_seen",
                "get_repair_order",
                "update_repair_order",
                "reopen_repair_order",
                "_replace_repair_order_rows",
                "set_repair_order_status",
                "print_repair_order_documents",
                "autofill_repair_order",
                "start_card_timer",
                "stop_card_timer",
                "set_card_indicator",
                "archive_card",
                "restore_card",
            },
            "card_attachments.py": {
                "add_card_attachment",
                "remove_card_attachment",
                "get_attachment_download",
                "get_card_attachment",
                "read_card_attachment",
            },
        }
        directory = Path(__file__).resolve().parents[1] / "src/minimal_kanban/services"
        for filename, names in expected.items():
            tree = ast.parse((directory / filename).read_text(encoding="utf-8"))
            calls = {}
            for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
                for method in (node for node in cls.body if isinstance(node, ast.FunctionDef)):
                    selected = [
                        node
                        for node in ast.walk(method)
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "_read_card_bundle_for_update"
                    ]
                    if selected:
                        self.assertEqual(len(selected), 1)
                        calls[method.name] = selected[0]
            self.assertEqual(set(calls), names)
            for name, call in calls.items():
                expression = (
                    "card_id" if name == "get_attachment_download" else 'payload.get("card_id", "")'
                )
                self.assertEqual(
                    ast.dump(call.args[0]), ast.dump(ast.parse(expression, mode="eval").body)
                )
                self.assertEqual(call.keywords, [])
