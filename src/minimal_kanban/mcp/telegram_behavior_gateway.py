"""Strict hidden CRM Gateway schema for Telegram behavior graph patches."""

from __future__ import annotations

from typing import Any


def telegram_behavior_patch_schema(route: str) -> dict[str, Any]:
    """Describe one narrow versioned graph patch operation."""

    fields = {
        "id": {"type": "string", "minLength": 1, "maxLength": 80},
        "title": {"type": "string", "maxLength": 120},
        "description": {"type": "string", "maxLength": 10000},
        "x": {"type": "integer", "minimum": 0},
        "y": {"type": "integer", "minimum": 0},
        "width": {"type": "integer", "minimum": 0},
        "height": {"type": "integer", "minimum": 0},
        "tone": {
            "type": "string",
            "enum": ["A", "agent", "B", "C", "D", "E", "F", "G", "H", "J", "N"],
        },
        "icon": {
            "type": "string",
            "enum": [
                "terminal",
                "document",
                "telegram",
                "person",
                "people",
                "bell",
                "gateway",
                "gear",
                "book",
                "database",
                "car",
                "cloud",
                "globe",
                "camera",
                "plug",
                "cart",
                "box",
                "message",
                "clock",
                "photo",
                "audio",
                "video",
                "brain",
                "decision",
                "note",
            ],
        },
    }
    relation_fields = {
        "id": fields["id"],
        "from": fields["id"],
        "to": fields["id"],
        "label": fields["title"],
        "description": fields["description"],
        "tone": fields["tone"],
    }
    element_fields = {key: value for key, value in fields.items() if key != "id"}
    return {
        "$id": f"autostopcrm-agent-gateway:{route}",
        "title": route,
        "type": "object",
        "properties": {
            "expected_revision": {"type": "integer", "minimum": 0},
            "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128},
            "operations": {
                "type": "array",
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "required": ["op"],
                    "additionalProperties": False,
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": [
                                "add_element",
                                "update_element",
                                "delete_element",
                                "add_relation",
                                "update_relation",
                                "delete_relation",
                                "resize_canvas",
                            ],
                        },
                        "id": fields["id"],
                        "element": {
                            "type": "object",
                            "properties": fields,
                            "required": [
                                "id",
                                "title",
                                "description",
                                "x",
                                "y",
                                "width",
                                "height",
                            ],
                            "additionalProperties": False,
                        },
                        "relation": {
                            "type": "object",
                            "properties": relation_fields,
                            "required": ["id", "from", "to", "label", "description"],
                            "additionalProperties": False,
                        },
                        "changes": {
                            "type": "object",
                            "properties": {
                                **element_fields,
                                "from": fields["id"],
                                "to": fields["id"],
                                "label": fields["title"],
                            },
                            "additionalProperties": False,
                        },
                        "width": {"type": "integer", "minimum": 500},
                        "height": {"type": "integer", "minimum": 400},
                    },
                },
            },
        },
        "required": ["expected_revision", "idempotency_key", "operations"],
        "additionalProperties": False,
    }
