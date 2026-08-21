from __future__ import annotations

import json
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

MODULE_MAP_DATA_RE = re.compile(
    r'<script id="moduleMapData" type="application/json">\s*(.*?)\s*</script>',
    re.DOTALL,
)


class ModuleMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        match = MODULE_MAP_DATA_RE.search(MODULE_MAP_HTML)
        if match is None:
            raise AssertionError("module map JSON payload is missing")
        cls.application = json.loads(match.group(1))
        cls.infrastructure = MODULE_MAP_INFRASTRUCTURE

    def assert_map_integrity(self, data: dict) -> None:
        modules = data["modules"]
        relations = data["relations"]
        module_ids = [module["id"] for module in modules]
        zone_roles = {zone["id"]: zone["role"] for zone in data["zones"]}

        self.assertEqual(len(module_ids), len(set(module_ids)))
        self.assertTrue(all(re.fullmatch(r"[A-Z][A-Z0-9_]*", item) for item in module_ids))
        self.assertTrue(all(module["zone"] in zone_roles for module in modules))
        self.assertTrue(all(module["role"] == zone_roles[module["zone"]] for module in modules))
        self.assertTrue(all(module["paths"] for module in modules))

        relation_pairs: set[tuple[str, str]] = set()
        for relation in relations:
            pair = (relation["from"], relation["to"])
            self.assertIn(relation["from"], module_ids)
            self.assertIn(relation["to"], module_ids)
            self.assertNotEqual(relation["from"], relation["to"])
            self.assertTrue(relation["label"].strip())
            self.assertIsInstance(relation["overview"], bool)
            self.assertNotIn(pair, relation_pairs)
            relation_pairs.add(pair)

        self.assertTrue(set(data.get("aliases", {}).values()).issubset(module_ids))
        self.assertEqual(
            {relation["from"] for relation in relations}
            | {relation["to"] for relation in relations},
            set(module_ids),
        )

    def test_application_map_has_current_stable_baseline(self) -> None:
        self.assertEqual(self.application["schema_version"], "autostopcrm.module-map.v10.4")
        self.assertEqual(self.application["verified_at"], "2026-08-21")
        self.assertEqual(self.application["baseline"], "origin/autostopcrm-v1")
        self.assertEqual(len(self.application["modules"]), 23)
        self.assertEqual(len(self.application["relations"]), 46)
        self.assertEqual(
            {module["id"] for module in self.application["modules"]},
            {
                "AGENT",
                "API",
                "API_CLIENTS",
                "AUTH",
                "AUTH_STATE",
                "BOARD",
                "CARD_SERVICE",
                "CRM",
                "DASHBOARD",
                "DESKTOP",
                "EVENTS",
                "FILES",
                "FINANCE",
                "INVENTORY",
                "MCP",
                "MCP_CLIENTS",
                "OPS",
                "REPAIR",
                "RESEARCH",
                "SHARED",
                "STATE",
                "STORE",
                "UI",
            },
        )
        self.assert_map_integrity(self.application)

    def test_infrastructure_map_is_complete_and_consistent(self) -> None:
        self.assertEqual(self.infrastructure["verified_at"], "2026-08-21")
        self.assertEqual(len(self.infrastructure["zones"]), 8)
        self.assertEqual(len(self.infrastructure["modules"]), 43)
        self.assertEqual(len(self.infrastructure["relations"]), 50)
        self.assertEqual(len(self.infrastructure["primary_relations"]), 15)
        relation_keys = {
            f"{relation['from']}>{relation['to']}" for relation in self.infrastructure["relations"]
        }
        self.assertTrue(set(self.infrastructure["primary_relations"]).issubset(relation_keys))
        self.assert_map_integrity(self.infrastructure)

    def test_public_shell_does_not_embed_private_infrastructure(self) -> None:
        self.assertNotIn("infrastructure", self.application)
        self.assertNotIn("/opt/autostopcrm", MODULE_MAP_HTML)
        self.assertNotIn("PROD_VPS", MODULE_MAP_HTML)
        self.assertIn("/api/get_module_map_infrastructure", MODULE_MAP_HTML)
        self.assertIn("X-Operator-Session", MODULE_MAP_HTML)
        self.assertIn("kanban-operator-session", MODULE_MAP_HTML)

    def test_user_facing_map_copy_is_russian(self) -> None:
        has_cyrillic = re.compile(r"[А-Яа-яЁё]")
        for data in (self.application, self.infrastructure):
            self.assertTrue(all(has_cyrillic.search(zone["label"]) for zone in data["zones"]))
            translated_names = sum(
                bool(has_cyrillic.search(module["name"])) for module in data["modules"]
            )
            self.assertGreaterEqual(translated_names, len(data["modules"]) * 3 // 4)
            self.assertTrue(
                all(has_cyrillic.search(module["description"]) for module in data["modules"])
            )
            self.assertTrue(
                all(has_cyrillic.search(relation["label"]) for relation in data["relations"])
            )

    def test_overview_path_matches_project_architecture(self) -> None:
        overview = {
            (relation["from"], relation["to"], relation["label"])
            for relation in self.application["relations"]
            if relation["overview"]
        }
        expected = {
            ("UI", "API", "отправляет запросы"),
            ("MCP", "API", "обращается к API"),
            ("API", "AUTH", "проверяет сеанс"),
            ("API", "CARD_SERVICE", "передаёт задачу"),
            ("CARD_SERVICE", "BOARD", "управляет доской"),
            ("BOARD", "STATE", "читает и сохраняет"),
            ("MCP", "STORE", "получает каталог и склад"),
        }
        self.assertTrue(expected.issubset(overview))
        self.assertEqual(len(overview), 18)

    def test_page_supports_views_focus_zoom_pan_and_blank_clear(self) -> None:
        expected_fragments = (
            "Карта модулей V10.4",
            'data-map-view="infrastructure"',
            "async function switchView(view)",
            "function selectModule(moduleId, options)",
            "window.location.hash",
            "addEventListener('wheel'",
            "addEventListener('pointerdown'",
            "setPointerCapture(event.pointerId)",
            "Math.exp(-event.deltaY",
            "function selectRelation(index, x, y)",
            "selectModule('');",
            "data-relation-key",
        )
        for fragment in expected_fragments:
            self.assertIn(fragment, MODULE_MAP_HTML)
        self.assertNotIn(".module.is-hidden", MODULE_MAP_HTML)
        self.assertNotIn("<script src=", MODULE_MAP_HTML)
        self.assertNotIn('<link rel="stylesheet"', MODULE_MAP_HTML)

    def test_board_has_one_click_module_map_link(self) -> None:
        self.assertIn(
            '<a class="btn" id="moduleMapLink" href="/module-map" '
            'target="_blank" rel="noopener">КАРТА</a>',
            BOARD_WEB_APP_CONTRACT_TEXT,
        )


if __name__ == "__main__":
    unittest.main()
