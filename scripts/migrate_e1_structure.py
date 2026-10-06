"""Prepare a guarded E1 graph replacement from a full fresh technical snapshot.

This command never calls the API or writes live CRM state. Apply its preview and
replacement through the owner/CAS/idempotency API, then independently read back.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.services import manager_structure_routing as routing  # noqa: E402
from minimal_kanban.services.manager_structure import (  # noqa: E402
    ManagerStructureService,
    _validate,
)
from minimal_kanban.services.manager_tool_catalog import validate_bundle  # noqa: E402


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _is_e_code(ident: str) -> bool:
    return re.fullmatch(r"E[1-9][0-9]*", ident) is not None


def _new_elements(snapshot: dict, bundle: dict, x: float, y: float) -> list[dict]:
    old = {node["id"]: node for node in snapshot["elements"]}
    legacy = {
        "E1": "E1",
        "E2": "E4",
        "E4": "E5",
        "E5": "E6",
        "E6": "E7",
        "E9": "E3",
        "E10": "E10",
        "E11": "E9",
        "E15": "E8",
    }
    # A parent is a logical association, not a physical enclosure. Satellite
    # cards leave room for terminals and labels while retaining the E1 hierarchy.
    positions = {
        "E1": (0, 0),
        "E4": (600, 250),
        "E5": (1100, 250),
        "E6": (1100, 530),
        "E3": (1600, 250),
        "E7": (1600, 530),
        "E9": (2100, 250),
        "E10": (2100, 530),
    }
    satellites = ("E2", "E8", "E11", "E12", "E13", "E14", "E15")
    positions.update(
        {
            code: ((index % 4) * 650, 1050 + (index // 4) * 200)
            for index, code in enumerate(satellites)
        }
    )
    result = [copy.deepcopy(node) for node in snapshot["elements"] if not _is_e_code(node["id"])]
    for module in sorted(bundle["modules"], key=lambda row: int(row["element_id"][1:])):
        code = module["element_id"]
        offset_x, offset_y = positions[code]
        node = copy.deepcopy(old.get(legacy.get(code, "E4"), old["E1"]))
        node.update(
            id=code,
            title=module["title"],
            description=module["purpose"],
            instruction=module["instruction_text"],
            group="E",
            lines=[],
            x=x + offset_x,
            y=y + offset_y,
            width=400,
            height=100,
        )
        if code == "E1":
            node.pop("parent", None)
        else:
            node["parent"] = "E1"
        result.append(node)
    return result


def _reroute_changed(diagram: dict, fixed: list[dict]) -> None:
    """Keep all unrelated routes verbatim while routing only E-bound relations."""
    fixed_by_id = {edge["id"]: edge for edge in fixed}
    template = copy.deepcopy(diagram)
    for edge in template["relations"]:
        if edge["id"] in fixed_by_id:
            edge["label_mode"] = "manual"
        else:
            edge["route_mode"] = "auto"
            edge["label_mode"] = "auto"
            edge.pop("from_anchor", None)
            edge.pop("to_anchor", None)
    retained = {edge["id"]: routing._points_from_path(edge["path"]) for edge in fixed}
    if not all(retained.values()):
        raise ValueError("unrelated_route_unreadable")
    changed_ids = sorted(edge["id"] for edge in template["relations"] if edge["id"] not in retained)
    deadline = monotonic() + 60
    for attempt in range(3):
        working = copy.deepcopy(template)
        nodes = {node["id"]: node for node in working["elements"]}
        relations = {edge["id"]: edge for edge in working["relations"]}
        assigned, preferred = routing._ports_for(nodes, list(relations.values()))
        order = [relations[ident] for ident in sorted(retained) + changed_ids]
        try:
            routing._route_pass(working, order, assigned, preferred, deadline, retained)
            for edge in working["relations"]:
                if edge["id"] in retained:
                    continue
                endpoints = routing._route_endpoints(edge["path"])
                if endpoints is None:
                    raise ValueError("changed_route_unreadable")
                for endpoint, point in zip(("from", "to"), endpoints):
                    node = nodes[edge[endpoint]]
                    anchor = routing._anchor_from_point(node, point)
                    attached = routing._anchor_point(node, anchor)
                    if any(
                        abs(actual - expected) > 0.01 for actual, expected in zip(point, attached)
                    ):
                        raise ValueError("changed_route_not_attached")
                    edge[endpoint + "_anchor"] = anchor
            working["relations"] = [
                copy.deepcopy(fixed_by_id[edge["id"]]) if edge["id"] in fixed_by_id else edge
                for edge in working["relations"]
            ]
            _validate(working)
            conflicts = routing.route_conflicts(working, parallel_gap=10, deadline=deadline)
            if conflicts:
                raise routing.RouteUnavailable(conflicts[0][1])
            ManagerStructureService._verify_manual_routes(working)
            diagram["relations"] = working["relations"]
            return
        except routing.RouteUnavailable as error:
            if attempt == 2 or error.relation_id not in changed_ids:
                raise
            changed_ids.remove(error.relation_id)
            changed_ids.insert(0, error.relation_id)


def prepare_migration(
    snapshot: dict, bundle: dict, *, x: float = 2450, y: float = 448
) -> tuple[dict, dict]:
    validate_bundle(bundle)
    if (
        snapshot.get("truncated_items")
        or type(snapshot.get("version")) is not int
        or snapshot["version"] < 0
    ):
        raise ValueError("full_versioned_snapshot_required")
    _validate(snapshot)
    codes = {node["id"] for node in snapshot["elements"] if _is_e_code(node["id"])}
    if codes != {f"E{i}" for i in range(1, 12)}:
        raise ValueError("legacy_E1_E11_map_required")
    modules = {module["element_id"] for module in bundle["modules"]}
    if modules != {f"E{i}" for i in range(1, 16)}:
        raise ValueError("complete_E1_E15_bundle_required")
    mapping = bundle.get("migration", {}).get("old_to_new")
    if (
        not isinstance(mapping, dict)
        or set(mapping) != codes
        or any(value not in modules for value in mapping.values())
    ):
        raise ValueError("complete_legacy_semantic_mapping_required")
    if mapping["E1"] != "E1":
        raise ValueError("E1_parent_mapping_required")
    graph = {
        "schema_version": snapshot["schema_version"],
        "canvas": copy.deepcopy(snapshot["canvas"]),
        "elements": _new_elements(snapshot, bundle, x, y),
        "relations": copy.deepcopy(snapshot["relations"]),
    }
    fixed = []
    remapped = []
    for edge in graph["relations"]:
        before = (edge["from"], edge["to"])
        if not any(_is_e_code(endpoint) for endpoint in before):
            fixed.append(copy.deepcopy(edge))
            continue
        edge["from"], edge["to"] = (
            mapping.get(before[0], before[0]),
            mapping.get(before[1], before[1]),
        )
        if edge["from"] == edge["to"]:
            if edge["id"] == "L21" and before == ("E7", "E6"):
                edge["from"], edge["to"] = "E6", "E5"
            else:
                raise ValueError(f"semantic_relation_collision:{edge['id']}")
        edge["description"] = (
            str(edge.get("description", ""))
            + " Связь показывает доступную передачу по задаче; обязательной последовательности вызовов нет."
        )
        remapped.append(
            {"id": edge["id"], "before": list(before), "after": [edge["from"], edge["to"]]}
        )
    _validate(graph, check_attachment=False)
    _reroute_changed(graph, fixed)
    non_e_before = [node for node in snapshot["elements"] if not _is_e_code(node["id"])]
    non_e_after = [node for node in graph["elements"] if not _is_e_code(node["id"])]
    if non_e_before != non_e_after or graph["canvas"] != snapshot["canvas"]:
        raise ValueError("unrelated_graph_changed")
    if [edge for edge in graph["relations"] if edge["id"] in {row["id"] for row in fixed}] != fixed:
        raise ValueError("unrelated_relations_changed")
    receipt = {
        "source_version": snapshot["version"],
        "source_digest": digest(snapshot),
        "diagram_digest": digest(graph),
        "catalog_source_revision": bundle["source_revision"],
        "catalog_hash": bundle["content_hash"],
        "non_e_elements_unchanged": True,
        "non_e_relations_unchanged": all(edge in graph["relations"] for edge in fixed),
        "preserve_root_tool_statuses": True,
        "source_tool_statuses_digest": digest(snapshot.get("tool_statuses", {})),
        "layout": "logical_satellites_v1",
        "remapped_relations": remapped,
    }
    return graph, receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--x", type=float, default=2450)
    parser.add_argument("--y", type=float, default=448)
    args = parser.parse_args()
    os.umask(0o077)
    snapshot = json.loads(args.snapshot.read_text())
    diagram, receipt = prepare_migration(
        snapshot, json.loads(args.bundle.read_text()), x=args.x, y=args.y
    )
    payload = {
        "operation": "replace",
        "expected_version": snapshot["version"],
        "idempotency_key": args.idempotency_key,
        "preview": True,
        "diagram": diagram,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "ok": True,
                "elements": len(diagram["elements"]),
                "relations": len(diagram["relations"]),
                "diagram_digest": receipt["diagram_digest"],
            }
        )
    )


if __name__ == "__main__":
    main()
