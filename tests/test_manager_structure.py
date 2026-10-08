from __future__ import annotations

import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.services.manager_structure import ManagerStructureService  # noqa: E402
from minimal_kanban.services.manager_structure_routing import (  # noqa: E402
    RoutingTimeout,
    _anchor_from_point,
    _blocked_by_node,
    _point_segment_distance,
    _points_from_path,
    _reanchor_path,
    _segments,
    route_conflicts,
    route_intersections,
)
from minimal_kanban.services.telegram_behavior_graph import initial_graph  # noqa: E402


class ManagerStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        owner = patch.dict("os.environ", {"AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN": "ADMIN"})
        owner.start()
        self.addCleanup(owner.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "manager_structure.json"
        self.service = ManagerStructureService(self.path)

    def apply(self, version: int, key: str, operation: str, **body):
        return self.service.apply(
            {
                "expected_version": version,
                "idempotency_key": key,
                "operation": operation,
                "_operator_session": {
                    "is_admin": True,
                    "username": "admin",
                    "token": "local-test-session",
                },
                **body,
            }
        )

    def test_nested_instructions_relations_reload_and_guards(self) -> None:
        outer = {
            "id": "M1",
            "title": "Менеджер",
            "kind": "module",
            "x": 10,
            "y": 10,
            "width": 300,
            "height": 220,
            "instruction": "Полная инструкция владельца",
            "lines": ["Краткая подпись"],
            "icon": "person",
            "color": "#79b9d9",
        }
        child = {
            "id": "M2",
            "title": "Запись клиента",
            "kind": "item",
            "parent": "M1",
            "x": 30,
            "y": 100,
            "width": 180,
            "height": 50,
            "instruction": "Детальная инструкция вложенного блока",
            "lines": [],
            "icon": "document",
            "color": "#68dba8",
            "compact": True,
        }
        edge = {
            "id": "R1",
            "from": "M1",
            "to": "M2",
            "kind": "exchange",
            "direction": "both",
            "path": "M160 80 V100",
            "label": "Передача",
            "label_x": 180,
            "label_y": 80,
            "show_label": True,
            "color": "#dfb461",
        }
        self.assertEqual(self.apply(0, "outer-0001", "upsert_element", element=outer)["version"], 1)
        self.assertEqual(self.apply(1, "child-0001", "upsert_element", element=child)["version"], 2)
        self.assertEqual(
            self.apply(2, "edge-00001", "upsert_relation", relation=edge)["version"], 3
        )
        restarted = ManagerStructureService(self.path)
        data = restarted.read()
        self.assertEqual(data["elements"][0]["instruction"], outer["instruction"])
        self.assertEqual(data["elements"][1]["instruction"], child["instruction"])
        self.assertEqual(data["relations"][0]["direction"], "both")
        with self.assertRaisesRegex(ServiceError, "Схема изменилась"):
            self.apply(2, "stale-0001", "upsert_element", element={"id": "M2", "title": "Устарело"})
        self.assertTrue(
            restarted.apply(
                {
                    "expected_version": 0,
                    "idempotency_key": "edge-00001",
                    "operation": "upsert_relation",
                    "relation": edge,
                    "_operator_session": {
                        "is_admin": True,
                        "username": "admin",
                        "token": "local-test-session",
                    },
                }
            )["deduplicated"]
        )
        with self.assertRaisesRegex(ServiceError, "Ключ уже использован"):
            self.apply(3, "edge-00001", "remove_relation", id="R1")
        with self.assertRaisesRegex(ServiceError, "Сначала удалите"):
            self.apply(3, "remove-001", "remove_element", id="M1")
        self.assertEqual(
            self.apply(
                3,
                "child-text-001",
                "upsert_element",
                element={"id": "M2", "instruction": "Новый полный текст"},
            )["version"],
            4,
        )
        self.assertEqual(
            ManagerStructureService(self.path).read()["elements"][1]["instruction"],
            "Новый полный текст",
        )

    def test_invalid_parent_and_unsafe_path_rejected_without_writing(self) -> None:
        node = {
            "id": "M1",
            "title": "Модуль",
            "kind": "module",
            "x": 0,
            "y": 0,
            "width": 200,
            "height": 100,
        }
        self.apply(0, "outer-0001", "upsert_element", element=node)
        with self.assertRaises(ServiceError):
            self.apply(
                1,
                "bad-child-001",
                "upsert_element",
                element={
                    "id": "M2",
                    "title": "Нет родителя",
                    "kind": "item",
                    "parent": "M9",
                    "x": 300,
                    "y": 0,
                    "width": 50,
                    "height": 40,
                },
            )
        with self.assertRaises(ServiceError):
            self.apply(
                1,
                "bad-edge-0001",
                "upsert_relation",
                relation={
                    "id": "R1",
                    "from": "M1",
                    "to": "M1",
                    "kind": "exchange",
                    "direction": "forward",
                    "path": 'M1 1" onload=alert(1)',
                    "label_x": 10,
                    "label_y": 10,
                },
            )
        self.assertEqual(self.service.read()["version"], 1)

    def test_saved_legacy_graph_is_copied_without_deleting_original(self) -> None:
        original = {"revision": 1, "graph": initial_graph()}
        original["graph"]["elements"][0]["icon"] = "clock"
        settings = {"telegram_agent_behavior": original}
        migrated = ManagerStructureService(self.path, legacy_settings_loader=lambda: settings)
        data = migrated.read()
        self.assertEqual(data["version"], 1)
        self.assertEqual(
            data["elements"][0]["instruction"], original["graph"]["elements"][0]["description"]
        )
        self.assertEqual(data["elements"][0]["icon"], "clock")
        self.assertEqual(len(data["relations"]), len(original["graph"]["relations"]))
        migrated.apply(
            {
                "operation": "upsert_element",
                "element": {"id": data["elements"][0]["id"], "instruction": "Новый текст"},
                "expected_version": 1,
                "idempotency_key": "migrate-001",
                "_operator_session": {
                    "is_admin": True,
                    "username": "admin",
                    "token": "local-test-session",
                },
            }
        )
        self.assertEqual(settings["telegram_agent_behavior"], original)
        self.assertTrue(self.path.exists())
        self.assertEqual(migrated.read()["elements"][0]["instruction"], "Новый текст")

    def test_unconfigured_or_other_admin_cannot_edit(self) -> None:
        with self.assertRaisesRegex(ServiceError, "только владелец"):
            self.service.apply(
                {
                    "operation": "set_canvas",
                    "canvas": {"width": 900, "height": 900},
                    "expected_version": 0,
                    "idempotency_key": "wrong-owner-001",
                    "_operator_session": {
                        "is_admin": True,
                        "username": "another",
                        "token": "admin-session",
                    },
                }
            )

    def test_layout_preview_and_atomic_reroute(self) -> None:
        nodes = [
            {
                "id": "M1",
                "title": "Отправитель",
                "kind": "module",
                "x": 40,
                "y": 80,
                "width": 160,
                "height": 80,
            },
            {
                "id": "M2",
                "title": "Получатель",
                "kind": "module",
                "x": 500,
                "y": 300,
                "width": 160,
                "height": 80,
            },
        ]
        self.apply(0, "layout-node-001", "upsert_element", element=nodes[0])
        self.apply(1, "layout-node-002", "upsert_element", element=nodes[1])
        self.apply(
            2,
            "layout-edge-001",
            "layout_relation",
            relation={
                "id": "R1",
                "from": "M1",
                "to": "M2",
                "kind": "exchange",
                "direction": "forward",
            },
        )
        before = self.service.read()
        self.assertEqual(route_intersections(before), [])
        preview = self.service.apply(
            {
                "operation": "layout_element",
                "element": {"id": "M2", "x": 600},
                "expected_version": before["version"],
                "idempotency_key": "layout-preview-001",
                "preview": True,
                "_operator_session": {
                    "is_admin": True,
                    "username": "admin",
                    "token": "local-test-session",
                },
            }
        )
        self.assertTrue(preview["preview"])
        self.assertEqual(self.service.read(), before)
        result = self.apply(
            before["version"], "layout-move-001", "layout_element", element={"id": "M2", "x": 600}
        )
        after = self.service.read()
        self.assertEqual(result["version"], before["version"] + 1)
        self.assertEqual(result["accepted_element"]["x"], 600)
        self.assertEqual(result["routes"], {"R1": after["relations"][0]["path"]})
        self.assertEqual(after["elements"][1]["x"], 600)
        self.assertNotEqual(after["relations"][0]["path"], before["relations"][0]["path"])
        self.assertEqual(route_intersections(after), [])

    def test_manual_curve_anchors_label_and_workspace_survive_reload_and_move(self) -> None:
        nodes = [
            {
                "id": "M1",
                "title": "Источник",
                "kind": "module",
                "x": 100,
                "y": 100,
                "width": 120,
                "height": 100,
            },
            {
                "id": "M2",
                "title": "Получатель",
                "kind": "module",
                "x": 500,
                "y": 100,
                "width": 120,
                "height": 100,
            },
        ]
        self.apply(0, "manual-node-001", "upsert_element", element=nodes[0])
        self.apply(1, "manual-node-002", "upsert_element", element=nodes[1])
        relation = {
            "id": "R1",
            "from": "M1",
            "to": "M2",
            "kind": "exchange",
            "direction": "reverse",
            "route_mode": "manual",
            "path": "M220 150 C280 80 420 80 500 150",
            "from_anchor": {"side": "right", "offset": 0.5},
            "to_anchor": {"side": "left", "offset": 0.5},
            "label_mode": "manual",
            "label_x": 360,
            "label_y": 45,
        }
        self.apply(2, "manual-edge-001", "upsert_relation", relation=relation)
        reloaded = ManagerStructureService(self.path)
        saved = reloaded.read()
        edge = saved["relations"][0]
        self.assertEqual(edge["path"], relation["path"])
        self.assertEqual(edge["from_anchor"], relation["from_anchor"])
        self.assertEqual(edge["to_anchor"], relation["to_anchor"])
        self.assertEqual((edge["label_x"], edge["label_y"]), (360, 45))
        self.assertEqual(edge["direction"], "reverse")

        self.apply(
            saved["version"],
            "manual-move-001",
            "layout_element",
            element={"id": "M2", "x": 600},
        )
        moved = reloaded.read()
        edge = moved["relations"][0]
        self.assertEqual(edge["path"], "M220 150 C280 80 520 80 600 150")
        self.assertEqual((edge["label_x"], edge["label_y"]), (360, 45))

        self.apply(
            moved["version"],
            "manual-canvas-001",
            "set_canvas",
            canvas={"width": 4200, "height": 2400},
        )
        expanded = reloaded.read()
        self.assertEqual(expanded["canvas"], {"width": 4200, "height": 2400})
        self.assertEqual(
            [(node["x"], node["y"]) for node in expanded["elements"]], [(100, 100), (600, 100)]
        )
        self.assertEqual(expanded["relations"][0]["path"], edge["path"])

    def test_manual_orthogonal_path_readback_and_endpoint_moves(self) -> None:
        nodes = [
            {
                "id": "M1",
                "title": "Источник",
                "kind": "module",
                "x": 100,
                "y": 100,
                "width": 120,
                "height": 100,
            },
            {
                "id": "M2",
                "title": "Получатель",
                "kind": "module",
                "x": 500,
                "y": 150,
                "width": 120,
                "height": 100,
            },
        ]
        self.apply(0, "orthogonal-node-001", "upsert_element", element=nodes[0])
        self.apply(1, "orthogonal-node-002", "upsert_element", element=nodes[1])
        path = "M220 150 H300 V200 H500"
        self.apply(
            2,
            "orthogonal-edge-001",
            "upsert_relation",
            relation={
                "id": "R1",
                "from": "M1",
                "to": "M2",
                "kind": "exchange",
                "direction": "forward",
                "route_mode": "manual",
                "path": path,
                "from_anchor": {"side": "right", "offset": 0.5},
                "to_anchor": {"side": "left", "offset": 0.5},
                "label_mode": "manual",
                "label_x": 360,
                "label_y": 130,
            },
        )
        self.assertEqual(ManagerStructureService(self.path).read()["relations"][0]["path"], path)
        self.apply(3, "orthogonal-move-001", "layout_element", element={"id": "M1", "y": 120})
        self.apply(4, "orthogonal-move-002", "layout_element", element={"id": "M2", "y": 200})
        edge = ManagerStructureService(self.path).read()["relations"][0]
        self.assertEqual(edge["path"], "M220 170 H300 V200 V250 H500")
        points = _points_from_path(edge["path"])
        self.assertTrue(all(a[0] == b[0] or a[1] == b[1] for a, b in _segments(points)))
        self.assertEqual((edge["label_x"], edge["label_y"]), (360, 130))

    def test_curved_routes_are_sampled_for_crossing_geometry(self) -> None:
        diagram = {
            "relations": [
                {"id": "R1", "path": "M0 0 C10 20 20 20 30 0"},
                {"id": "R2", "path": "M0 20 C10 0 20 0 30 20"},
            ]
        }
        high_bend = _points_from_path("M0 0 C2000 2000 -2000 2000 30 0")
        self.assertGreater(len(high_bend), 24)
        self.assertTrue(route_intersections(diagram))
        self.assertEqual(route_conflicts(diagram), [])

    def test_route_obstacle_checks_ignore_boundary_touch_but_reject_interior_crossing(self) -> None:
        node = {
            "id": "B2",
            "x": 30,
            "y": 632,
            "width": 205,
            "height": 60,
        }
        self.assertFalse(_blocked_by_node((165.333, 808), (165.333, 704), node))
        self.assertTrue(_blocked_by_node((165.333, 808), (165.333, 694), node))
        self.assertTrue(_blocked_by_node((20, 650), (250, 650), node))
        self.assertTrue(_blocked_by_node((20, 620), (250, 704), node))

    def test_manual_orthogonal_reanchor_quadratic_and_blocked_route(self) -> None:
        orthogonal = "M220 150 H300 V200 H500"
        self.assertEqual(_reanchor_path(orthogonal, (220, 150), (500, 200)), orthogonal)
        moved = _reanchor_path(orthogonal, (220, 170), (500, 250))
        points = _points_from_path(moved)
        self.assertEqual((points[0], points[-1]), ((220, 170), (500, 250)))
        self.assertTrue(all(a[0] == b[0] or a[1] == b[1] for a, b in _segments(points)))

        orthogonal_lines = "M220 150 L300 150 L300 200 L500 200"
        moved_lines = _reanchor_path(orthogonal_lines, (220, 170), (500, 250))
        line_points = _points_from_path(moved_lines)
        self.assertEqual((line_points[0], line_points[-1]), ((220, 170), (500, 250)))
        self.assertTrue(all(a[0] == b[0] or a[1] == b[1] for a, b in _segments(line_points)))
        self.assertEqual(
            _reanchor_path("M0 0 Q50 50 100 0", (10, 20), (110, 30)),
            "M10 20 Q60 75 110 30",
        )

        path = "M220 150 H500"
        nodes = [
            {
                "id": ident,
                "title": ident,
                "kind": "module",
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            }
            for ident, x, y, width, height in (
                ("M1", 100, 100, 120, 100),
                ("M2", 500, 100, 120, 100),
                ("M3", 320, 138, 40, 24),
            )
        ]
        relation = {
            "id": "R1",
            "from": "M1",
            "to": "M2",
            "kind": "exchange",
            "direction": "forward",
            "route_mode": "manual",
            "path": path,
            "from_anchor": {"side": "right", "offset": 0.5},
            "to_anchor": {"side": "left", "offset": 0.5},
            "label_x": 360,
            "label_y": 130,
        }
        service = ManagerStructureService(self.path)
        for version, node in enumerate(nodes):
            self.apply(version, f"blocked-node-{version:03}", "upsert_element", element=node)
        with self.assertRaisesRegex(ServiceError, "постороннюю карточку"):
            self.apply(3, "blocked-route-001", "upsert_relation", relation=relation)
        self.assertEqual(service.read()["version"], 3)
        self.assertEqual(service.read()["relations"], [])

    def test_portable_reference_preserves_retained_geometry_and_known_conflict(self) -> None:
        reference = json.loads(
            (
                Path(__file__).resolve().parents[1] / "templates" / "manager_structure.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(len(reference["elements"]), 48)
        self.assertEqual(len(reference["relations"]), 37)
        # The instruction refresh preserves this existing owner's layout defect.
        self.assertEqual(route_conflicts(reference), [("L30", "L36")])
        self.assertGreater(len(route_intersections(reference)), 0)
        self.assertEqual(len({tuple(r["path"].split()[:2]) for r in reference["relations"]}), 37)
        self.assertEqual(
            [n["id"] for n in reference["elements"] if n.get("parent") == "E1"],
            [f"E{i}" for i in range(2, 16)],
        )
        self.assertNotIn("tool_statuses", reference)
        self.apply(0, "portable-retained-001", "replace", diagram=reference)
        self.assertEqual(self.service.read()["relations"], reference["relations"])

    def test_reroute_preview_exact_geometry_and_semantic_preservation(self) -> None:
        reference = json.loads(
            (
                Path(__file__).resolve().parents[1] / "templates" / "manager_structure.json"
            ).read_text(encoding="utf-8")
        )
        self.apply(0, "reroute-reference-001", "replace", diagram=reference)
        before = self.service.read()
        stored = self.path.read_bytes()
        started = time.monotonic()
        preview = self.apply(1, "reroute-preview-001", "reroute", preview=True)
        elapsed = time.monotonic() - started
        self.assertEqual(self.path.read_bytes(), stored)
        self.assertEqual(preview["version"], 1)
        self.assertEqual(preview["diagram"]["elements"], before["elements"])
        geometry = {"path", "label_x", "label_y", "auto_hidden_label"}
        self.assertEqual(
            [{k: v for k, v in edge.items() if k not in geometry} for edge in before["relations"]],
            [
                {k: v for k, v in edge.items() if k not in geometry}
                for edge in preview["diagram"]["relations"]
            ],
        )
        written = self.apply(1, "reroute-save-001", "reroute")
        saved = self.service.read()
        self.assertEqual(written["version"], 2)
        self.assertEqual(written["routes"], preview["routes"])
        self.assertEqual(written["labels"], preview["labels"])
        self.assertEqual(
            written["routes"], {edge["id"]: edge["path"] for edge in saved["relations"]}
        )
        self.assertTrue(self.apply(1, "reroute-save-001", "reroute")["deduplicated"])
        nodes = {node["id"]: node for node in saved["elements"]}
        for edge in saved["relations"]:
            points = _points_from_path(edge["path"])
            for endpoint, first, second in (
                ("from", points[0], points[1]),
                ("to", points[-1], points[-2]),
            ):
                side = _anchor_from_point(nodes[edge[endpoint]], first)["side"]
                self.assertEqual(first[1] == second[1], side in {"left", "right"}, edge["id"])
                self.assertGreaterEqual(
                    abs(first[0] - second[0]) + abs(first[1] - second[1]),
                    16 if len(points) > 2 else 0,
                    edge["id"],
                )
            if edge.get("show_label", True):
                self.assertFalse(edge["auto_hidden_label"])
                self.assertLessEqual(
                    min(
                        _point_segment_distance((edge["label_x"], edge["label_y"]), a, b)
                        for a, b in _segments(points)
                    ),
                    0.001,
                    edge["id"],
                )
        started = time.monotonic()
        self.apply(
            2,
            "reroute-narrow-001",
            "layout_relation",
            relation={"id": "L25", "label": "Agent context"},
            preview=True,
        )
        print(f"manager routing: full={elapsed:.3f}s, narrow={time.monotonic() - started:.3f}s")

    def test_routing_timeout_is_atomic_and_identifies_relation(self) -> None:
        stored = copy.deepcopy(self.service.read())
        with patch(
            "minimal_kanban.services.manager_structure.route_diagram",
            side_effect=RoutingTimeout("L12"),
        ):
            with self.assertRaisesRegex(ServiceError, "L12") as raised:
                self.apply(0, "timeout-reroute-001", "reroute")
        self.assertEqual(raised.exception.details["relation_id"], "L12")
        self.assertEqual(self.service.read(), stored)

    def test_impossible_layout_keeps_previous_version(self) -> None:
        diagram = {
            "schema_version": "autostopcrm.manager-structure.v1",
            "canvas": {"width": 1000, "height": 600},
            "elements": [
                {
                    "id": ident,
                    "title": ident,
                    "kind": "module",
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                }
                for ident, x, y, width, height in (
                    ("A", 50, 250, 100, 80),
                    ("B", 850, 250, 100, 80),
                    ("C", 450, 100, 100, 400),
                )
            ],
            "relations": [],
        }
        self.apply(0, "impossible-fixture-001", "replace", diagram=diagram)
        self.apply(
            1,
            "impossible-edge-001",
            "layout_relation",
            relation={
                "id": "R",
                "from": "A",
                "to": "B",
                "kind": "exchange",
                "direction": "forward",
            },
        )
        before = self.service.read()
        with self.assertRaisesRegex(ServiceError, "Перемещение не сохранено"):
            self.apply(
                2,
                "impossible-resize-001",
                "layout_element",
                element={"id": "C", "y": 0, "height": 600},
            )
        self.assertEqual(self.service.read(), before)
        blocked = json.loads(json.dumps(diagram))
        blocked["elements"][2]["y"] = 0
        blocked["elements"][2]["height"] = 600
        self.apply(2, "blocked-fixture-001", "replace", diagram=blocked)
        blocked_before = self.service.read()
        with self.assertRaisesRegex(ServiceError, "Изменение связи не сохранено"):
            self.apply(
                3,
                "blocked-edge-001",
                "layout_relation",
                relation={
                    "id": "R",
                    "from": "A",
                    "to": "B",
                    "kind": "exchange",
                    "direction": "forward",
                },
            )
        self.assertEqual(self.service.read(), blocked_before)

    def test_overlapping_move_snaps_to_nearest_free_candidate(self) -> None:
        diagram = {
            "schema_version": "autostopcrm.manager-structure.v1",
            "canvas": {"width": 700, "height": 400},
            "elements": [
                {
                    "id": ident,
                    "title": ident,
                    "kind": "module",
                    "x": x,
                    "y": 100,
                    "width": 100,
                    "height": 100,
                }
                for ident, x in (("A", 100), ("B", 280))
            ],
            "relations": [],
        }
        self.apply(0, "snap-fixture-001", "replace", diagram=diagram)
        preview = self.service.apply(
            {
                "operation": "layout_element",
                "element": {"id": "A", "x": 200},
                "expected_version": 1,
                "idempotency_key": "snap-preview-001",
                "preview": True,
                "_operator_session": {
                    "is_admin": True,
                    "username": "admin",
                    "token": "local-test-session",
                },
            }
        )
        self.assertTrue(preview["adjusted"])
        self.assertEqual(preview["diagram"]["elements"][0]["x"], 176)
        self.assertEqual(self.service.read()["version"], 1)
        saved = self.apply(1, "snap-save-001", "layout_element", element={"id": "A", "x": 200})
        self.assertTrue(saved["adjusted"])
        self.assertEqual(saved["accepted_element"]["x"], 176)
        self.assertEqual(ManagerStructureService(self.path).read()["elements"][0]["x"], 176)

    def test_reference_move_rebuilds_all_paths_without_conflicts(self) -> None:
        reference = json.loads(
            (
                Path(__file__).resolve().parents[1] / "templates" / "manager_structure.json"
            ).read_text(encoding="utf-8")
        )
        self.apply(0, "reference-fixture-001", "replace", diagram=reference)
        current = next(node for node in reference["elements"] if node["id"] == "G1")
        saved = self.apply(
            1,
            "reference-move-001",
            "layout_element",
            element={"id": "G1", "x": current["x"] + 30},
        )
        after = ManagerStructureService(self.path).read()
        self.assertEqual(saved["accepted_element"]["x"], current["x"] + 30)
        self.assertEqual(len(saved["routes"]), 37)
        self.assertEqual(route_conflicts(after), [])


if __name__ == "__main__":
    unittest.main()
