from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.source_path_support import ensure_source_path, prepend_scripts_path
else:
    from source_path_support import ensure_source_path, prepend_scripts_path

ensure_source_path()
prepend_scripts_path()

from migrate_e1_structure import prepare_migration  # noqa: E402

from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.services.manager_structure import SCHEMA, ManagerStructureService  # noqa: E402
from minimal_kanban.services.manager_structure_routing import (  # noqa: E402
    RouteUnavailable,
    _anchor_point,
    _route_endpoints,
    route_conflicts,
)
from minimal_kanban.services.manager_tool_catalog import content_hash  # noqa: E402

if __package__:
    from tests.manager_tool_catalog_fixture import fixture_bundle
else:
    from manager_tool_catalog_fixture import fixture_bundle


def bundle_fixture() -> dict:
    bundle = fixture_bundle()
    template = bundle["modules"][0]
    bundle["modules"] = [
        {
            **copy.deepcopy(template),
            "element_id": f"E{index}",
            "module_key": f"test_module_{index}",
            "title": f"Новый модуль {index}",
            "purpose": f"Назначение {index}",
            "instruction_text": f"Каноническая инструкция {index}",
        }
        for index in range(1, 16)
    ]
    bundle["migration"] = {
        "old_to_new": {
            "E1": "E1",
            "E2": "E2",
            "E3": "E4",
            "E4": "E2",
            "E5": "E4",
            "E6": "E6",
            "E7": "E6",
            "E8": "E15",
            "E9": "E11",
            "E10": "E10",
            "E11": "E10",
        },
        "relation_strategy": "Semantic remap with an explicit L21 collision decision",
    }
    bundle["content_hash"] = content_hash(bundle)
    return bundle


def snapshot_fixture() -> dict:
    nodes = [
        {
            "id": ident,
            "title": "Исходная инструкция " + ident,
            "kind": "module",
            "x": x,
            "y": y,
            "width": 180,
            "height": 70,
            "description": "Сохранить описание",
            "instruction": "Сохранить текст",
            "indicator_mode": "manual",
            "indicator_state": "yellow",
            "color": "#79b9d9",
            "lines": ["Сохранить подпись"],
            "icon": "book",
            "group": "other",
        }
        for ident, x, y in (("N1", 40, 40), ("N2", 400, 40), ("EPC", 40, 350))
    ]
    nodes.extend(
        {
            "id": f"E{index}",
            "title": f"Старый модуль {index}",
            "kind": "item",
            "x": 700,
            "y": 100 + index * 90,
            "width": 200,
            "height": 60,
            "group": "E",
        }
        for index in range(1, 12)
    )
    fixed = {
        "id": "F1",
        "from": "N1",
        "to": "N2",
        "kind": "exchange",
        "direction": "both",
        "path": "M220 75 H400",
        "label": "Исходная связь",
        "label_x": 310,
        "label_y": 75,
        "description": "Точный исходный текст",
        "protocol": "Не изменять",
        "route_mode": "manual",
        "label_mode": "manual",
        "show_label": True,
        "from_anchor": {"side": "right", "offset": 0.5},
        "to_anchor": {"side": "left", "offset": 0.5},
        "color": "#79b9d9",
    }
    edges = [fixed]
    for ident, source, target in (
        ("L14", "N2", "E1"),
        ("L17", "E1", "E2"),
        ("L18", "E1", "E3"),
        ("L20", "E5", "E7"),
        ("L21", "E7", "E6"),
        ("L23", "E6", "E9"),
        ("L22", "E7", "E8"),
        ("L24", "E9", "E8"),
        ("L31", "E9", "E10"),
        ("L32", "E9", "E11"),
    ):
        edges.append(
            {
                "id": ident,
                "from": source,
                "to": target,
                "kind": "exchange",
                "direction": "forward",
                "path": "M0 0 H10",
                "label": "Сохранить связь",
                "label_x": 5,
                "label_y": 0,
                "description": "Исходное назначение",
                "protocol": "Исходный протокол",
            }
        )
    return {
        "schema_version": SCHEMA,
        "version": 120,
        "canvas": {"width": 6200, "height": 3600},
        "elements": nodes,
        "relations": edges,
        "tool_statuses": {
            "demo.inspect": {
                "state": "working",
                "updated_at": "2026-01-01T00:00:00Z",
                "updated_by": "ADMIN",
            }
        },
    }


class E1MigrationTests(unittest.TestCase):
    def test_atomic_remap_preserves_unrelated_records_and_attaches_routes(self) -> None:
        snapshot, bundle = snapshot_fixture(), bundle_fixture()
        original = copy.deepcopy(snapshot)
        graph, receipt = prepare_migration(snapshot, bundle)
        self.assertEqual(snapshot, original)
        self.assertEqual(graph["canvas"], snapshot["canvas"])
        self.assertEqual(graph["elements"][:3], snapshot["elements"][:3])
        self.assertEqual(graph["relations"][0], snapshot["relations"][0])
        nodes = {node["id"]: node for node in graph["elements"]}
        self.assertEqual(set(nodes), {"N1", "N2", "EPC"} | {f"E{i}" for i in range(1, 16)})
        self.assertNotIn("parent", nodes["E1"])
        self.assertTrue(all(nodes[f"E{i}"]["parent"] == "E1" for i in range(2, 16)))
        self.assertTrue(
            all(
                nodes[f"E{i}"]["instruction"] == f"Каноническая инструкция {i}"
                for i in range(1, 16)
            )
        )
        edges = {edge["id"]: edge for edge in graph["relations"]}
        self.assertEqual((edges["L21"]["from"], edges["L21"]["to"]), ("E6", "E5"))
        self.assertEqual((edges["L18"]["from"], edges["L18"]["to"]), ("E1", "E4"))
        self.assertEqual((edges["L32"]["from"], edges["L32"]["to"]), ("E11", "E10"))
        self.assertEqual(set(edges), {edge["id"] for edge in snapshot["relations"]})
        for edge in graph["relations"][1:]:
            before = next(row for row in snapshot["relations"] if row["id"] == edge["id"])
            self.assertEqual(edge["protocol"], before["protocol"])
            self.assertEqual(edge["label"], before["label"])
            for endpoint, point in zip(("from", "to"), _route_endpoints(edge["path"])):
                attached = _anchor_point(nodes[edge[endpoint]], edge[endpoint + "_anchor"])
                self.assertAlmostEqual(point[0], attached[0], places=2)
                self.assertAlmostEqual(point[1], attached[1], places=2)
        self.assertFalse(route_conflicts(graph, parallel_gap=10))
        self.assertTrue(receipt["non_e_elements_unchanged"])
        self.assertTrue(receipt["non_e_relations_unchanged"])
        self.assertNotIn("tool_statuses", graph)

    def test_preview_preserves_bytes_statuses_and_owner_cas_guards(self) -> None:
        snapshot, bundle = snapshot_fixture(), bundle_fixture()
        graph, _ = prepare_migration(snapshot, bundle)
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict("os.environ", {"AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN": "ADMIN"}),
        ):
            path = Path(temp) / "local-state.json"
            path.write_text(
                json.dumps({**snapshot, "receipts": {}}, ensure_ascii=False), encoding="utf-8"
            )
            before = path.read_bytes()
            service = ManagerStructureService(path, tool_catalog_loader=lambda: bundle)
            payload = {
                "operation": "replace",
                "expected_version": 120,
                "idempotency_key": "synthetic-preview",
                "preview": True,
                "diagram": graph,
                "_operator_session": {
                    "is_admin": True,
                    "username": "ADMIN",
                    "token": "test-session",
                },
            }
            preview = service.apply(payload)
            self.assertTrue(preview["preview"])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(preview["diagram"]["tool_statuses"], snapshot["tool_statuses"])
            with self.assertRaises(ServiceError) as error:
                service.apply({**payload, "expected_version": 119})
            self.assertEqual(error.exception.status_code, 409)
            with self.assertRaises(ServiceError) as error:
                service.apply(
                    {
                        **payload,
                        "_operator_session": {
                            "is_admin": True,
                            "username": "OTHER",
                            "token": "test",
                        },
                    }
                )
            self.assertEqual(error.exception.status_code, 403)
            self.assertEqual(path.read_bytes(), before)
            apply_payload = {
                **payload,
                "preview": False,
                "idempotency_key": "synthetic-apply-new-key",
            }
            self.assertEqual(service.apply(apply_payload)["version"], 121)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["tool_statuses"], snapshot["tool_statuses"])
            self.assertEqual(saved["elements"][:3], snapshot["elements"][:3])
            self.assertEqual(saved["relations"][0], snapshot["relations"][0])
            saved_bytes = path.read_bytes()
            self.assertTrue(service.apply(apply_payload)["deduplicated"])
            self.assertEqual(path.read_bytes(), saved_bytes)

    def test_only_the_explicit_legacy_L21_self_loop_is_resolved(self) -> None:
        snapshot = snapshot_fixture()
        next(edge for edge in snapshot["relations"] if edge["id"] == "L21")["id"] = "UNEXPECTED"
        with self.assertRaisesRegex(ValueError, "semantic_relation_collision:UNEXPECTED"):
            prepare_migration(snapshot, bundle_fixture())

    def test_partial_or_unmapped_snapshots_and_foreign_E_codes_are_rejected(self) -> None:
        for mutation in (
            "truncated",
            "boolean_version",
            "missing_mapping",
            "foreign_mapping",
            "E16",
        ):
            with self.subTest(mutation=mutation):
                snapshot, bundle = snapshot_fixture(), bundle_fixture()
                if mutation == "truncated":
                    snapshot["truncated_items"] = True
                elif mutation == "boolean_version":
                    snapshot["version"] = True
                elif mutation == "missing_mapping":
                    bundle["migration"]["old_to_new"].pop("E9")
                elif mutation == "foreign_mapping":
                    bundle["migration"]["old_to_new"]["N2"] = "E1"
                else:
                    snapshot["elements"].append({**snapshot["elements"][-1], "id": "E16"})
                bundle["content_hash"] = content_hash(bundle)
                with self.assertRaises(ValueError):
                    prepare_migration(snapshot, bundle)

    def test_failed_routing_has_bounded_retries_and_does_not_mutate_inputs(self) -> None:
        snapshot, bundle = snapshot_fixture(), bundle_fixture()
        before = copy.deepcopy(snapshot)
        with patch(
            "migrate_e1_structure.routing._route_pass", side_effect=RouteUnavailable("L23")
        ) as route:
            with self.assertRaises(RouteUnavailable):
                prepare_migration(snapshot, bundle)
        self.assertEqual(route.call_count, 3)
        self.assertEqual(snapshot, before)


if __name__ == "__main__":
    unittest.main()
