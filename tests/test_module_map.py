from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.web_assets import (  # noqa: E402
    BOARD_WEB_APP_CONTRACT_TEXT,
    MODULE_MAP_HTML,
    MODULE_MAP_INFRASTRUCTURE,
)


class ModuleMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = MODULE_MAP_INFRASTRUCTURE
        self.nodes = {item["id"]: item for item in self.data["elements"]}

    def test_exact_reference_ids_and_connection_endpoints(self) -> None:
        expected = {
            f"{group}{i}"
            for group, count in {
                "A": 5,
                "B": 4,
                "C": 7,
                "D": 5,
                "E": 15,
                "F": 5,
                "G": 1,
                "H": 2,
                "I": 2,
                "J": 1,
                "M": 2,
            }.items()
            for i in range(1, count + 1)
        } - {"C1"}
        self.assertEqual(self.data["schema_version"], "autostopmanager.infrastructure-map.v1")
        self.assertEqual(set(self.nodes), expected)
        self.assertEqual(len(self.nodes), 48)
        edges = {item["id"]: item for item in self.data["relations"]}
        self.assertEqual(
            set(edges), ({f"L{i}" for i in range(1, 39)} - {"L8", "L9", "L13"}) | {"R1", "R2"}
        )
        self.assertEqual(len(edges), 37)
        for edge in edges.values():
            self.assertIn(edge["from"], self.nodes)
            self.assertIn(edge["to"], self.nodes)
            self.assertIn(edge["direction"], {"forward", "reverse", "both", "none"})
            self.assertIn(edge["kind"], {"event", "exchange"})
        self.assertEqual((edges["L10"]["from"], edges["L10"]["to"]), ("A2", "C2"))
        self.assertIn("OAuth 2.1", edges["L10"]["protocol"])
        self.assertEqual(self.nodes["G1"].get("control_surface"), "automation_center")
        self.assertIn("manage-owner-instagram/SKILL.md", self.nodes["A1"]["links"][2]["url"])
        template = json.loads(
            (
                Path(__file__).resolve().parents[1] / "templates" / "manager_structure.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(self.data["canvas"], template["canvas"])
        self.assertEqual(self.data["relations"], template["relations"])
        for actual, portable in zip(self.data["elements"], template["elements"]):
            self.assertEqual(
                {k: v for k, v in actual.items() if k not in {"links", "control_surface"}}, portable
            )

    def test_hierarchy_geometry_and_russian_descriptions(self) -> None:
        for node in self.nodes.values():
            self.assertRegex(node["description"], r"[А-Яа-яЁё]")
            self.assertGreater(node["width"], 0)
            self.assertGreater(node["height"], 0)
            self.assertGreaterEqual(node["x"], 0)
            self.assertGreaterEqual(node["y"], 0)
            self.assertLessEqual(node["x"] + node["width"], self.data["canvas"]["width"])
            self.assertLessEqual(node["y"] + node["height"], self.data["canvas"]["height"])
            self.assertTrue(node["instruction"].strip())
            self.assertLessEqual(len(node["instruction"]), 30000)
            seen = {node["id"]}
            parent = node.get("parent")
            while parent:
                self.assertIn(parent, self.nodes)
                self.assertNotIn(parent, seen)
                seen.add(parent)
                parent = self.nodes[parent].get("parent")
        self.assertEqual(
            [n["id"] for n in self.data["elements"] if n.get("parent") == "E1"],
            [f"E{i}" for i in range(2, 16)],
        )
        self.assertEqual(self.nodes["E8"]["title"], "Масла, жидкости и объёмы")
        self.assertEqual(self.nodes["E15"]["title"], "Публичный поиск и исследования")
        self.assertNotIn("tool_statuses", self.data)
        self.assertNotIn("receipts", self.data)
        for edge in self.data["relations"]:
            self.assertTrue(edge["path"].strip())
            self.assertRegex(edge["path"], r"^M[0-9]")

    def test_public_shell_does_not_embed_topology_or_old_map(self) -> None:
        for old in (
            "PROD_VPS",
            "/opt/autostopcrm",
            "autostopcrm.module-map.v10.4",
            "moduleMapData",
            "data-map-view",
            "Статус неизвестен",
        ):
            self.assertNotIn(old, MODULE_MAP_HTML)
        for item in self.data["elements"]:
            self.assertNotIn(item["description"], MODULE_MAP_HTML)
        self.assertIn("/api/get_module_map_infrastructure", MODULE_MAP_HTML)
        self.assertIn("nodes.size!==data.elements.length", MODULE_MAP_HTML)
        self.assertIn("!nodes.has('G1')", MODULE_MAP_HTML)
        self.assertIn("X-Operator-Session", MODULE_MAP_HTML)
        self.assertIn("kanban-operator-session", MODULE_MAP_HTML)
        self.assertIn("response.status===401||response.status===403", MODULE_MAP_HTML)
        self.assertNotRegex(
            str({k: v for k, v in self.data.items() if k != "elements"}),
            r"/opt/|/root/|\b\d{1,3}(?:\.\d{1,3}){3}\b",
        )
        self.assertEqual(len(re.findall(r"\bfetch\(", MODULE_MAP_HTML)), 2)
        self.assertNotIn("setInterval", MODULE_MAP_HTML)
        self.assertIn("const AUTOMATION_POLL_MS=5000", MODULE_MAP_HTML)
        self.assertIn(
            "if(($('automationLayer').hidden&&!force)||automationRequest)return automationRequest",
            MODULE_MAP_HTML,
        )
        self.assertIn("hashSelection();refreshAutomationStatus(true)", MODULE_MAP_HTML)
        self.assertIn(
            "if(!$('automationLayer').hidden)automationPollTimer=setTimeout",
            MODULE_MAP_HTML,
        )
        self.assertIn("/api/automation_center/status", MODULE_MAP_HTML)
        self.assertIn("/api/automation_center/control", MODULE_MAP_HTML)
        self.assertIn("toggle.role='switch'", MODULE_MAP_HTML)
        self.assertIn("timer_id:timer.id", MODULE_MAP_HTML)
        self.assertIn("minutes<5||minutes>1440", MODULE_MAP_HTML)
        self.assertIn(
            "automationStatus=normalizeAutomationStatus(await automationRequestJson",
            MODULE_MAP_HTML,
        )

    def test_e1_child_purpose_is_rendered_below_the_diagram(self) -> None:
        self.assertIn('id="detailPurpose"', MODULE_MAP_HTML)
        self.assertLess(
            MODULE_MAP_HTML.index('id="detailDiagram"'),
            MODULE_MAP_HTML.index('id="detailPurpose"'),
        )
        self.assertIn("const purpose=typeof item.instruction", MODULE_MAP_HTML)
        self.assertIn("$('detailPurpose').hidden=!purpose", MODULE_MAP_HTML)

    def test_board_opens_same_route_with_manager_copy(self) -> None:
        self.assertIn('href="/module-map"', BOARD_WEB_APP_CONTRACT_TEXT)
        self.assertIn("ОТКРЫТЬ ИНФРАСТРУКТУРУ МЕНЕДЖЕРА", BOARD_WEB_APP_CONTRACT_TEXT)
        self.assertIn(
            "window.open('/module-map', 'autostop-module-map')", BOARD_WEB_APP_CONTRACT_TEXT
        )
        self.assertNotIn("ОТКРЫТЬ СТРУКТУРУ IT", BOARD_WEB_APP_CONTRACT_TEXT)


if __name__ == "__main__":
    unittest.main()
