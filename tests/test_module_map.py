from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

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
            for group, count in {"A": 3, "B": 4, "C": 7, "D": 5, "E": 6, "F": 5, "N": 2}.items()
            for i in range(1, count + 1)
        }
        self.assertEqual(self.data["schema_version"], "autostopmanager.infrastructure-map.v1")
        self.assertEqual(set(self.nodes), expected - {"N1"})
        self.assertEqual(len(self.data["elements"]), 31)
        edges = {item["id"]: item for item in self.data["relations"]}
        self.assertEqual(len(self.data["relations"]), 18)
        self.assertEqual(set(edges), {f"L{i}" for i in range(1, 20)} - {"L6"})
        pairs = [
            ("A1", "A2"),
            ("A2", "A3"),
            ("A3", "B1"),
            ("B1", "B2"),
            ("B1", "B3"),
            ("B2", "B4"),
            ("B4", "A2"),
            ("A2", "C1"),
            ("C1", "C2"),
            ("A2", "C2"),
            ("C2", "C3"),
            ("A2", "D1"),
            ("D1", "D2"),
            ("D1", "E1"),
            ("D1", "F1"),
            ("D2", "D3"),
            ("E1", "E2"),
            ("E1", "E3"),
            ("F1", "F2"),
        ]
        for index, pair in enumerate(pairs, 1):
            if index == 6:
                continue
            edge = edges[f"L{index}"]
            self.assertEqual((edge["from"], edge["to"]), pair)
            self.assertEqual(edge["direction"], "forward" if index in {1, 6, 7} else "both")
            self.assertEqual(edge["kind"], "event" if index in {6, 7} else "exchange")

    def test_hierarchy_geometry_and_russian_descriptions(self) -> None:
        for node in self.nodes.values():
            self.assertRegex(node["description"], r"[А-Яа-яЁё]")
            self.assertGreater(node["width"], 0)
            self.assertGreater(node["height"], 0)
            self.assertGreaterEqual(node["x"], 0)
            self.assertGreaterEqual(node["y"], 0)
            self.assertLessEqual(node["x"] + node["width"], self.data["canvas"]["width"])
            self.assertLessEqual(node["y"] + node["height"], self.data["canvas"]["height"])
            seen = {node["id"]}
            parent = node.get("parent")
            while parent:
                self.assertIn(parent, self.nodes)
                self.assertNotIn(parent, seen)
                seen.add(parent)
                parent = self.nodes[parent].get("parent")
        for child in ["C4", "C5", "C6", "C7", "D4", "E4", "E5", "E6", "F3", "F4", "F5"]:
            node = self.nodes[child]
            parent = self.nodes[node["parent"]]
            self.assertGreaterEqual(node["x"], parent["x"])
            self.assertGreaterEqual(node["y"], parent["y"])
            self.assertLessEqual(node["x"] + node["width"], parent["x"] + parent["width"])
            self.assertLessEqual(node["y"] + node["height"], parent["y"] + parent["height"])
        for edge in self.data["relations"]:
            for field in ("label", "protocol", "description", "path"):
                self.assertTrue(edge[field].strip())
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
        self.assertIn("X-Operator-Session", MODULE_MAP_HTML)
        self.assertIn("kanban-operator-session", MODULE_MAP_HTML)
        self.assertIn("response.status===401||response.status===403", MODULE_MAP_HTML)
        self.assertNotRegex(str(self.data), r"/opt/|/root/|\b\d{1,3}(?:\.\d{1,3}){3}\b")
        self.assertEqual(len(re.findall(r"\bfetch\(", MODULE_MAP_HTML)), 1)
        self.assertNotIn("setInterval", MODULE_MAP_HTML)

    def test_board_opens_same_route_with_manager_copy(self) -> None:
        self.assertIn('href="/module-map"', BOARD_WEB_APP_CONTRACT_TEXT)
        self.assertIn("ОТКРЫТЬ ИНФРАСТРУКТУРУ МЕНЕДЖЕРА", BOARD_WEB_APP_CONTRACT_TEXT)
        self.assertIn(
            "window.open('/module-map', 'autostop-module-map')", BOARD_WEB_APP_CONTRACT_TEXT
        )
        self.assertNotIn("ОТКРЫТЬ СТРУКТУРУ IT", BOARD_WEB_APP_CONTRACT_TEXT)


if __name__ == "__main__":
    unittest.main()
