from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.raw_gateway import virtual_api_risk
from minimal_kanban.web_assets import (
    BOARD_WEB_APP_CSS,
    BOARD_WEB_APP_HTML,
    BOARD_WEB_APP_JS,
    DISPLAY_DASHBOARD_HTML,
)
from scripts import crm_capability_parity


class CrmParityInventoryQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = crm_capability_parity.load_manifest()
        cls.inventory = crm_capability_parity.build_inventory()

    def test_source_inventory_covers_assembled_ui_and_exact_dynamic_routes(self) -> None:
        source_routes, issues = crm_capability_parity.discover_ui_routes(self.manifest)
        assembled_routes = {
            route
            for text in (
                BOARD_WEB_APP_HTML,
                BOARD_WEB_APP_CSS,
                BOARD_WEB_APP_JS,
                DISPLAY_DASHBOARD_HTML,
            )
            for value in crm_capability_parity.API_ROUTE_PATTERN.findall(text)
            if (route := crm_capability_parity._normalized_route(value))
        }

        self.assertEqual([], issues)
        self.assertEqual(set(), assembled_routes - set(source_routes))
        self.assertEqual(
            {"/api/pause_agent_scheduled_task", "/api/resume_agent_scheduled_task"},
            set(source_routes) - assembled_routes,
        )

    def test_server_only_health_text_and_print_alias_have_exact_gateway_coverage(self) -> None:
        rows = {row["route"]: row for row in self.inventory["matrix"]}

        self.assertEqual("covered", rows["/api/health"]["status"])
        self.assertEqual("runtime_health", rows["/api/health"]["readback_class"])
        self.assertEqual(
            {
                "kind": "permanent_gateway_tool",
                "gateway_tool": "ping_connector",
                "operation": "ping_connector",
            },
            rows["/api/health"]["reachability"]["selected"],
        )
        self.assertEqual("covered", rows["/api/repair_order_text"]["status"])
        self.assertEqual("text_document", rows["/api/repair_order_text"]["readback_class"])
        self.assertEqual(
            {
                "kind": "guarded_raw_mcp",
                "gateway_tool": "call_raw_capability",
                "mcp_tool": "get_repair_order_text",
            },
            rows["/api/repair_order_text"]["reachability"]["selected"],
        )
        print_row = rows["/employee_salary_reconciliation_print"]
        self.assertEqual("covered", print_row["status"])
        self.assertEqual(
            "printable_salary_reconciliation_payload",
            print_row["readback_class"],
        )
        self.assertEqual(
            {
                "kind": "guarded_virtual_api_alias",
                "gateway_tool": "call_raw_capability",
                "operation": "api:/api/get_employee_salary_reconciliation",
            },
            print_row["reachability"]["selected"],
        )

    def test_new_server_only_route_without_manifest_coverage_fails_inventory(self) -> None:
        http_routes, discovery_issues = crm_capability_parity.discover_http_routes(self.manifest)
        self.assertEqual([], discovery_issues)
        synthetic = dict(http_routes)
        synthetic["/api/new_server_only_action"] = ["synthetic-server.py:1"]

        with patch.object(
            crm_capability_parity,
            "discover_http_routes",
            return_value=(synthetic, []),
        ):
            inventory = crm_capability_parity.build_inventory()

        issue_codes = {(issue["code"], issue["action"]) for issue in inventory["issues"]}
        self.assertIn(
            ("unexpected_uncovered_action", "/api/new_server_only_action"),
            issue_codes,
        )
        self.assertFalse(inventory["ok"])

    def test_new_non_api_business_route_without_classification_fails_inventory(self) -> None:
        http_routes, discovery_issues = crm_capability_parity.discover_http_routes(self.manifest)
        self.assertEqual([], discovery_issues)
        synthetic = dict(http_routes)
        synthetic["/new_print_action"] = ["synthetic-server.py:1"]

        with patch.object(
            crm_capability_parity,
            "discover_http_routes",
            return_value=(synthetic, []),
        ):
            inventory = crm_capability_parity.build_inventory()

        issue_codes = {(issue["code"], issue["action"]) for issue in inventory["issues"]}
        self.assertIn(
            ("unexpected_uncovered_action", "/new_print_action"),
            issue_codes,
        )
        self.assertFalse(inventory["ok"])

    def test_route_evidence_does_not_count_the_parity_test_itself(self) -> None:
        evidence = crm_capability_parity.discover_test_evidence()
        inventory_test_names = crm_capability_parity.PARITY_INVENTORY_TEST_FILES

        self.assertTrue(evidence["/api/open_card"])
        self.assertFalse(
            any(
                any(item.startswith(f"tests/{name}:") for name in inventory_test_names)
                for locations in evidence.values()
                for item in locations
            )
        )
        for row in self.inventory["matrix"]:
            with self.subTest(route=row["route"]):
                self.assertFalse(
                    any(
                        name in item
                        for name in inventory_test_names
                        for item in row["test_evidence"]
                    )
                )
                self.assertEqual(
                    [crm_capability_parity.PARITY_TEST_EVIDENCE],
                    row["inventory_test_evidence"],
                )

    def test_virtual_route_read_write_class_matches_gateway_risk(self) -> None:
        for row in self.inventory["matrix"]:
            selected = row["reachability"].get("selected") or {}
            if selected.get("kind") != "guarded_virtual_api":
                continue
            gateway_risk = virtual_api_risk(row["route"], selected["operation"])
            expected = "read" if gateway_risk == "read" else "write"
            with self.subTest(route=row["route"]):
                self.assertEqual(expected, row["risk"])


if __name__ == "__main__":
    unittest.main()
