from __future__ import annotations

import re
import unittest

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
                "A": 3,
                "B": 4,
                "C": 7,
                "D": 5,
                "E": 9,
                "F": 5,
                "G": 1,
                "H": 2,
                "J": 1,
                "N": 2,
            }.items()
            for i in range(1, count + 1)
        }
        self.assertEqual(self.data["schema_version"], "autostopmanager.infrastructure-map.v1")
        self.assertEqual(set(self.nodes), expected - {"N1", "N2", "C1"})
        self.assertEqual(len(self.data["elements"]), 36)
        edges = {item["id"]: item for item in self.data["relations"]}
        self.assertEqual(len(self.data["relations"]), 28)
        self.assertEqual(len(self.nodes) + len(edges), 64)
        self.assertEqual(set(edges), {f"L{i}" for i in range(1, 31)} - {"L8", "L9"})
        pairs = {
            "L1": ("A1", "A2"),
            "L2": ("A2", "A3"),
            "L3": ("A3", "B1"),
            "L4": ("B1", "B2"),
            "L5": ("B1", "B3"),
            "L6": ("B2", "B4"),
            "L7": ("B4", "A2"),
            "L10": ("A2", "C2"),
            "L11": ("C2", "C3"),
            "L12": ("A2", "D1"),
            "L13": ("D1", "D2"),
            "L14": ("D1", "E1"),
            "L15": ("D1", "F1"),
            "L16": ("D2", "D3"),
            "L17": ("E1", "E2"),
            "L18": ("E1", "E3"),
            "L19": ("F1", "F2"),
            "L20": ("E5", "E7"),
            "L21": ("E7", "E6"),
            "L22": ("E7", "E8"),
            "L23": ("E6", "E9"),
            "L24": ("E9", "E8"),
            "L25": ("A2", "J1"),
            "L26": ("A2", "G1"),
            "L27": ("C3", "G1"),
            "L28": ("G1", "B1"),
            "L29": ("A2", "H1"),
            "L30": ("H1", "H2"),
        }
        for code, pair in pairs.items():
            edge = edges[code]
            self.assertEqual((edge["from"], edge["to"]), pair)
            index = int(code[1:])
            self.assertEqual(
                edge["direction"],
                "forward" if index in {1, 6, 7, 20, 21, 23, 27, 28} else "both",
            )
            self.assertEqual(edge["kind"], "event" if index in {6, 7, 27, 28} else "exchange")
        self.assertEqual(edges["L10"]["path"], "M830 132 V425")
        self.assertIn("OAuth 2.1", edges["L10"]["protocol"])
        for code in ("L20", "L21", "L22", "L23", "L24"):
            self.assertFalse(edges[code].get("show_label", True))
        self.assertEqual(edges["L22"]["path"], "M1588 600.5 H1620 V580 H1650")
        self.assertEqual(edges["L23"]["path"], "M1460 657 V667")
        self.assertEqual(edges["L24"]["path"], "M1588 682.5 H1630 V605 H1650")
        self.assertEqual(edges["L25"]["path"], "M1010 72 H1550 V97 H1590")
        self.assertEqual(edges["L25"].get("tone"), "J")
        self.assertEqual(self.nodes["G1"].get("control_surface"), "automation_center")
        self.assertEqual(self.nodes["G1"].get("indicator"), "unknown")
        for code in ("L26", "L27", "L28"):
            self.assertEqual(edges[code].get("tone"), "G")
        for code in ("L29", "L30"):
            self.assertEqual(edges[code].get("tone"), "H")
        self.assertIn("manage-owner-instagram/SKILL.md", self.nodes["A1"]["links"][2]["url"])
        self.assertEqual(self.nodes["H2"]["lines"], ["@auto.repair.parts"])
        self.assertFalse(edges["L30"].get("show_label", True))
        self.assertIn("не подключены", self.nodes["H1"]["description"])

    def test_hierarchy_geometry_and_russian_descriptions(self) -> None:
        edges = {item["id"]: item for item in self.data["relations"]}
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
        for child in ["C4", "C5", "C6", "C7", "D4", "E4", "E5", "E7", "E6", "E9", "F3", "F4", "F5"]:
            node = self.nodes[child]
            parent = self.nodes[node["parent"]]
            self.assertGreaterEqual(node["x"], parent["x"])
            self.assertGreaterEqual(node["y"], parent["y"])
            self.assertLessEqual(node["x"] + node["width"], parent["x"] + parent["width"])
            self.assertLessEqual(node["y"] + node["height"], parent["y"] + parent["height"])
        self.assertEqual(
            [node["id"] for node in self.data["elements"] if node.get("parent") == "E1"],
            ["E4", "E5", "E7", "E6", "E9"],
        )
        for code in ("E4", "E5", "E7", "E6", "E9"):
            self.assertRegex(self.nodes[code]["purpose"], r"[А-Яа-яЁё]")
        self.assertEqual(self.nodes["E7"]["title"], "Интернет-проверка детали")
        self.assertEqual(self.nodes["E8"]["title"], "Веб-шлюз")
        self.assertEqual(self.nodes["E9"]["title"], "Рыночная оценка детали")
        self.assertIn("Web Research Gateway", self.nodes["E8"]["description"])
        self.assertIn(
            "обезличенный запрос",
            next(item for item in self.data["relations"] if item["id"] == "L22")["description"],
        )
        self.assertEqual(self.nodes["E8"].get("tone"), "N")
        self.assertGreaterEqual(
            self.nodes["E8"]["x"], self.nodes["E1"]["x"] + self.nodes["E1"]["width"]
        )
        self.assertEqual((self.nodes["E8"]["width"], self.nodes["E8"]["height"]), (130, 62))
        self.assertTrue(self.nodes["E8"].get("compact"))
        self.assertEqual(self.nodes["J1"]["title"], "Интернет-исследования")
        self.assertIsNone(self.nodes["J1"].get("parent"))
        self.assertGreater(self.nodes["J1"]["x"], self.nodes["D1"]["x"] + self.nodes["D1"]["width"])
        self.assertLess(self.nodes["J1"]["y"] + self.nodes["J1"]["height"], self.nodes["D1"]["y"])
        self.assertGreaterEqual(
            self.nodes["E8"]["x"] - 9 - (self.nodes["E1"]["x"] + self.nodes["E1"]["width"]),
            32,
        )
        self.assertGreaterEqual(
            edges["L19"]["label_y"] - 14 - (self.nodes["E8"]["y"] + self.nodes["E8"]["height"]), 20
        )
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
        self.assertIn("nodes.size!==data.elements.length", MODULE_MAP_HTML)
        self.assertIn("!nodes.has('G1')", MODULE_MAP_HTML)
        self.assertIn("X-Operator-Session", MODULE_MAP_HTML)
        self.assertIn("kanban-operator-session", MODULE_MAP_HTML)
        self.assertIn("response.status===401||response.status===403", MODULE_MAP_HTML)
        self.assertNotRegex(str(self.data), r"/opt/|/root/|\b\d{1,3}(?:\.\d{1,3}){3}\b")
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
        self.assertIn("const purpose=typeof item.purpose", MODULE_MAP_HTML)
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
