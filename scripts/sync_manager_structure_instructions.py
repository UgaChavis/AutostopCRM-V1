"""Check or prepare canonical instruction refreshes offline; never calls the CRM API."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

from manager_structure_template import DEFAULT_TEMPLATE, EDGE_FIELDS, NODE_FIELDS, REFERENCE

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.services.manager_structure import SCHEMA, _validate  # noqa: E402
from minimal_kanban.services.manager_tool_catalog import validate_bundle  # noqa: E402

BLUEPRINT_SCHEMA = "autostopmanager.infrastructure-map.v1"
A5_POINTER = (
    "# A5 — Указатель действующих инструкций\n\n"
    "Каноническая инструкция выбранного snapshot Manager: [A5](A5.md).\n"
    "Выбор источника инструкций — по "
    "[A3](A3.md); "
    "схемы аргументов — по "
    "[D1](D1.md).\n"
    "Для рабочей операции выбери installed snapshot по A3; для source-задачи — "
    "выбранный Manager worktree. Ссылки относительны к этому документу, "
    "а CRM открывает их на revision своего каталога. "
    "Полный индекс и его изменяемые счётчики здесь не дублируются.\n"
)


def canonical_instructions(graph: dict, manager_root: Path, bundle: dict | None = None) -> dict:
    root = manager_root.resolve()
    instructions = {}
    for node in graph["elements"]:
        ident = node["id"]
        relative = (
            Path("AGENTS.md") if ident == "A2" else Path("docs/agent/modules") / f"{ident}.md"
        )
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Canonical instruction missing: {ident}")
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"Canonical instruction empty: {ident}")
        instructions[ident] = A5_POINTER if ident == "A5" and len(text) > 30000 else text
        if len(instructions[ident]) > 30000:
            raise ValueError(f"Canonical instruction exceeds CRM limit: {ident}")
    if bundle is not None:
        validate_bundle(bundle)
        revision_file = root / "REVISION"
        if revision_file.is_file():
            revision = revision_file.read_text(encoding="utf-8").strip()
        else:
            revision = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        if revision != bundle["source_revision"]:
            raise ValueError("Pinned bundle differs from Manager source revision")
        for module in bundle["modules"]:
            ident = module["element_id"]
            if ident not in instructions or module["instruction_text"] != instructions[ident]:
                raise ValueError(f"Pinned bundle differs from canonical instruction: {ident}")
    return instructions


def prepare(graph: dict, instructions: dict, previous_blueprint: dict | None = None) -> dict:
    """Copy only technical graph fields; owner receipts/status records stay private."""
    _validate(graph)
    if set(instructions) != {node["id"] for node in graph["elements"]}:
        raise ValueError("Canonical instructions must cover the exact node set")
    template = {
        "schema_version": SCHEMA,
        "canvas": copy.deepcopy(graph["canvas"]),
        "elements": [],
        "relations": [
            {key: copy.deepcopy(value) for key, value in edge.items() if key in EDGE_FIELDS}
            for edge in graph["relations"]
        ],
    }
    previews = []
    changes = []
    version = graph["version"]
    for node in graph["elements"]:
        item = {key: copy.deepcopy(value) for key, value in node.items() if key in NODE_FIELDS}
        item["instruction"] = instructions[node["id"]]
        template["elements"].append(item)
        if item["instruction"] == node.get("instruction", ""):
            continue
        fields = {
            "operation": "upsert_element",
            "element": {"id": node["id"], "instruction": item["instruction"]},
        }
        previews.append(
            {
                **fields,
                "expected_version": version,
                "preview": True,
                "idempotency_key": str(uuid.uuid4()),
            }
        )
        changes.append(
            {
                **fields,
                "expected_version": version + len(changes),
                "idempotency_key": str(uuid.uuid4()),
            }
        )
    _validate(template)
    # The legacy read-only map has a distinct schema and a few safe UI affordances.
    blueprint = copy.deepcopy(template)
    blueprint["schema_version"] = BLUEPRINT_SCHEMA
    old_nodes = {node["id"]: node for node in (previous_blueprint or {}).get("elements", [])}
    for node in blueprint["elements"]:
        for field in ("links", "control_surface"):
            if field in old_nodes.get(node["id"], {}):
                node[field] = copy.deepcopy(old_nodes[node["id"]][field])
    preserved = {
        "canvas": graph["canvas"],
        "elements": [{k: v for k, v in n.items() if k != "instruction"} for n in graph["elements"]],
        "relations": graph["relations"],
        "tool_statuses": graph.get("tool_statuses", {}),
    }
    digest = hashlib.sha256(
        json.dumps(preserved, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "template": template,
        "blueprint": blueprint,
        "patch": {
            "prepared_from_version": version,
            "requires_fresh_full_read_before_apply": True,
            "unchanged_scope_digest": digest,
            "preview_requests": previews,
            "apply_requests": changes,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True, help="Fresh full technical graph JSON")
    parser.add_argument("--manager-root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, help="Validate an already exported immutable bundle")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--blueprint", type=Path, default=REFERENCE)
    parser.add_argument("--refresh-templates", action="store_true")
    parser.add_argument("--check", action="store_true", help="Read-only exact artifact comparison")
    parser.add_argument(
        "--check-saved", action="store_true", help="Read-only saved instruction drift"
    )
    parser.add_argument("--patch-output", type=Path, help="Private preparation only; never applies")
    args = parser.parse_args(argv)
    if (args.check or args.check_saved) and (args.refresh_templates or args.patch_output):
        parser.error("Read-only checks do not write files")
    if args.patch_output and args.patch_output.resolve().is_relative_to(ROOT):
        parser.error("Prepared patch must stay outside the repository")
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    children = {n["id"] for n in graph["elements"] if n.get("parent") == "E1"}
    if (len(graph["elements"]), len(graph["relations"])) != (48, 37) or children != {
        f"E{i}" for i in range(2, 16)
    }:
        parser.error("Expected retained 48-node/37-relation graph and E2–E15 children")
    bundle = json.loads(args.bundle.read_text(encoding="utf-8")) if args.bundle else None
    instructions = canonical_instructions(graph, args.manager_root, bundle)
    previous = json.loads(args.blueprint.read_text(encoding="utf-8"))
    result = prepare(graph, instructions, previous)
    if args.check:
        for field, path in (("template", args.template), ("blueprint", args.blueprint)):
            if json.loads(path.read_text(encoding="utf-8")) != result[field]:
                parser.error(f"Canonical {field} differs from prepared technical graph")
    if args.refresh_templates:
        for field, path in (("template", args.template), ("blueprint", args.blueprint)):
            path.write_text(
                json.dumps(result[field], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    if args.patch_output:
        output = args.patch_output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result["patch"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        output.chmod(0o600)
    print(
        json.dumps(
            {
                "ok": not (args.check_saved and result["patch"]["apply_requests"]),
                "offline_only": True,
                "nodes": len(graph["elements"]),
                "relations": len(graph["relations"]),
                "e1_children": len(children),
                "instruction_changes": len(result["patch"]["apply_requests"]),
                "changed_ids": [p["element"]["id"] for p in result["patch"]["apply_requests"]]
                if args.check_saved
                else [],
                "saved_matches": not bool(result["patch"]["apply_requests"])
                if args.check_saved
                else None,
                "geometry_preserved": True,
                "portable_status_records": False,
                "bundle_checked": bundle is not None,
                "source_revision": bundle["source_revision"] if bundle else None,
            },
            sort_keys=True,
        )
    )
    return int(args.check_saved and bool(result["patch"]["apply_requests"]))


if __name__ == "__main__":
    raise SystemExit(main())
