from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import crm_capability_parity

REVIEWED_BASELINE_GAPS = {
    "/api/copy_shared_file",
    "/api/get_ai_chat_knowledge",
    "/api/get_board_revision",
    "/api/get_display_dashboard",
    "/api/get_module_map_infrastructure",
    "/api/get_inspection_sheet_form",
    "/api/get_operator_profile",
    "/api/get_repair_order_print_workspace",
    "/api/list_employees",
    "/api/login_operator",
    "/api/logout_operator",
    "/api/update_personal_board_preferences",
    "/api/open_card",
    "/api/set_card_ai_autofill",
}

INTENTIONAL_HUMAN_SESSION_EXEMPTIONS = {
    "/api/get_module_map_infrastructure",
    "/api/get_operator_profile",
    "/api/login_operator",
    "/api/logout_operator",
    "/api/update_personal_board_preferences",
}

CURRENT_GAPS: set[str] = set()

EXACT_RESOLVED_READBACK_CLASSES = {
    "/api/get_ai_chat_knowledge": "exact_ai_chat_knowledge",
    "/api/get_board_revision": "exact_board_revision",
    "/api/get_completion_act_form": "exact_completion_act_form",
    "/api/get_display_dashboard": "exact_display_dashboard",
    "/api/get_inspection_sheet_form": "exact_inspection_sheet_form",
    "/api/get_repair_order_print_workspace": "exact_repair_order_print_workspace",
    "/api/list_employees": "exact_employee_collection",
    "/api/open_card": "exact_operator_activity",
    "/api/set_card_ai_autofill": "exact_card_ai_autofill",
}


class CrmCapabilityParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = crm_capability_parity.load_manifest()
        cls.inventory = crm_capability_parity.build_inventory()

    def test_manifest_keeps_exact_reviewed_gap_and_exemption_decisions(self) -> None:
        self.assertEqual(REVIEWED_BASELINE_GAPS, set(self.manifest["baseline_gaps"]))
        self.assertEqual(
            INTENTIONAL_HUMAN_SESSION_EXEMPTIONS,
            set(self.manifest["intentional_exemptions"]),
        )
        self.assertEqual(
            INTENTIONAL_HUMAN_SESSION_EXEMPTIONS,
            set(crm_capability_parity.ALLOWED_INTENTIONAL_EXEMPTIONS),
        )

    def test_inventory_has_no_unexpected_uncovered_actions(self) -> None:
        self.assertTrue(self.inventory["ok"], self.inventory["issues"])
        self.assertEqual([], self.inventory["issues"])
        self.assertEqual(CURRENT_GAPS, set(self.inventory["gaps"]))
        self.assertEqual(
            INTENTIONAL_HUMAN_SESSION_EXEMPTIONS,
            set(self.inventory["intentional_exemptions"]),
        )
        self.assertEqual(14, self.inventory["summary"]["baseline_gaps"])
        self.assertEqual(9, self.inventory["summary"]["baseline_gaps_resolved"])
        self.assertTrue(self.inventory["summary"]["parity_complete"])

    def test_matrix_exposes_surfaces_reachability_readback_and_test_evidence(self) -> None:
        self.assertGreater(self.inventory["summary"]["actions"], 100)
        for row in self.inventory["matrix"]:
            with self.subTest(route=row["route"]):
                self.assertIn("ui", row["surfaces"])
                self.assertIn("backend_registered", row["surfaces"])
                self.assertIn("selected", row["reachability"])
                self.assertTrue(row["readback_class"])
                self.assertTrue(row["test_evidence"])

    def test_resolved_routes_use_exact_guarded_virtual_contracts(self) -> None:
        rows = {row["route"]: row for row in self.inventory["matrix"]}
        for route, readback_class in EXACT_RESOLVED_READBACK_CLASSES.items():
            with self.subTest(route=route):
                self.assertEqual("covered", rows[route]["status"])
                self.assertEqual(readback_class, rows[route]["readback_class"])
                self.assertEqual(
                    {
                        "kind": "guarded_virtual_api",
                        "gateway_tool": "call_raw_capability",
                        "operation": f"api:{route}",
                    },
                    rows[route]["reachability"]["selected"],
                )

    def test_only_reviewed_human_session_routes_remain_unreachable(self) -> None:
        rows = {row["route"]: row for row in self.inventory["matrix"]}
        self.assertEqual(
            INTENTIONAL_HUMAN_SESSION_EXEMPTIONS,
            {route for route, row in rows.items() if row["reachability"]["selected"] is None},
        )

    def test_binary_http_actions_have_explicit_document_coverage(self) -> None:
        rows = {row["route"]: row for row in self.inventory["matrix"]}
        for route in {"/api/attachment", "/api/fetch_shared_file", "/api/shared_file"}:
            with self.subTest(route=route):
                self.assertEqual("covered", rows[route]["status"])
                self.assertEqual("binary_document", rows[route]["readback_class"])
                self.assertIsNotNone(rows[route]["reachability"]["selected"])

    def test_new_backend_action_without_coverage_or_exemption_fails_inventory(self) -> None:
        backend_routes = crm_capability_parity.discover_backend_routes()
        backend_routes["/api/new_uncovered_action"] = {
            "registry": "service",
            "handler": "new_uncovered_action",
        }
        with patch.object(
            crm_capability_parity,
            "discover_backend_routes",
            return_value=backend_routes,
        ):
            inventory = crm_capability_parity.build_inventory()
        issue_codes = {(issue["code"], issue["action"]) for issue in inventory["issues"]}
        self.assertIn(
            ("unexpected_uncovered_action", "/api/new_uncovered_action"),
            issue_codes,
        )
        self.assertFalse(inventory["ok"])

    def test_new_ui_action_without_backend_or_gateway_coverage_fails_inventory(self) -> None:
        ui_routes, discovery_issues = crm_capability_parity.discover_ui_routes(self.manifest)
        self.assertEqual([], discovery_issues)
        ui_routes["/api/new_ui_only_action"] = ["synthetic-ui.js:1"]
        with patch.object(
            crm_capability_parity,
            "discover_ui_routes",
            return_value=(ui_routes, []),
        ):
            inventory = crm_capability_parity.build_inventory()
        issue_codes = {(issue["code"], issue["action"]) for issue in inventory["issues"]}
        self.assertIn(
            ("unexpected_uncovered_action", "/api/new_ui_only_action"),
            issue_codes,
        )
        self.assertFalse(inventory["ok"])


if __name__ == "__main__":
    unittest.main()
