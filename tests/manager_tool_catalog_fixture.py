"""Synthetic bundle independent of the production commissioning state."""

from __future__ import annotations

from minimal_kanban.services.manager_tool_catalog import BUNDLE_SCHEMA, content_hash


def fixture_bundle() -> dict:
    tool = {
        "tool_id": "demo.inspect",
        "title": "Проверить обозначение",
        "purpose": "Синтетическая операция",
        "provider_id": "local_demo",
        "primary_data_source": "synthetic",
        "implementation_state": "implemented",
        "execution_kind": "pure",
        "invocation": {"tool_name": "demo_inspect"},
        "input_fields": ["identifier"],
        "limitations": ["Не подтверждает применимость"],
        "instruction_ref": "docs/agent/tools/demo.md",
        "instruction_text": "Передайте обозначение; результат может быть unknown.",
    }
    bundle = {
        "schema_version": BUNDLE_SCHEMA,
        "source_revision": "0" * 40,
        "modules": [
            {
                "module_key": "vehicle_identity",
                "element_id": "E2",
                "title": "Автомобиль и модификация",
                "purpose": "Синтетическая инструкция",
                "instruction_ref": "docs/agent/modules/E2.md",
                "instruction_text": "Каноническая инструкция тестового пакета",
                "tool_ids": [tool["tool_id"]],
            }
        ],
        "tools": [tool],
    }
    bundle["content_hash"] = content_hash(bundle)
    return bundle
