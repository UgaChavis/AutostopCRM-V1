from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_TECHNICAL_ID_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_PRIVATE_VIEWER_SETTING_KEYS = frozenset({"_cashbox_notification_seen_by_users"})
SourceKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ProjectedEntity:
    """PII-free durable fingerprint for one CRM business entity."""

    entity_type: str
    entity_id: str
    digest: str
    routing_digest: str
    lifecycle: str


@dataclass(frozen=True, slots=True)
class ProjectedChange:
    entity_type: str
    entity_id: str
    change_type: str
    tombstone: bool

    @property
    def action(self) -> str:
        suffix = {
            "create": "created",
            "update": "updated",
            "move": "moved",
            "archive": "archived",
            "restore": "restored",
            "delete": "deleted",
        }[self.change_type]
        return f"{self.entity_type}_{suffix}"


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _source_digest(*values: object) -> str:
    payload = "\x1f".join(str(value or "") for value in values).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _selected(sources: set[SourceKey] | None, source_type: str, source_id: str) -> bool:
    return sources is None or (source_type, source_id) in sources


def _technical_id(value: object, *, fallback: str = "") -> str:
    raw = str(value or fallback or "").strip()
    if _TECHNICAL_ID_PATTERN.fullmatch(raw):
        return raw
    if not raw:
        return ""
    return f"ref-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _without(payload: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    excluded = set(keys)
    return {str(key): value for key, value in payload.items() if key not in excluded}


def _lifecycle(payload: Mapping[str, Any], *, removed_field: str = "") -> str:
    if removed_field and bool(payload.get(removed_field)):
        return "removed"
    if bool(payload.get("archived")):
        return "archived"
    return "active"


def _meaningful(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            _meaningful(item)
            for key, item in value.items()
            if key not in {"status", "payment_method"}
        )
    if isinstance(value, (list, tuple)):
        return any(_meaningful(item) for item in value)
    return value not in {None, "", False, 0}


def _add(
    projected: dict[tuple[str, str], ProjectedEntity],
    *,
    entity_type: str,
    entity_id: object,
    content: object,
    routing: object | None = None,
    lifecycle: str = "active",
) -> None:
    technical_id = _technical_id(entity_id)
    if not technical_id:
        return
    key = (entity_type, technical_id)
    projected[key] = ProjectedEntity(
        entity_type=entity_type,
        entity_id=technical_id,
        digest=_digest(content),
        routing_digest=_digest(routing if routing is not None else {}),
        lifecycle=lifecycle if lifecycle in {"active", "archived", "removed"} else "active",
    )


def _project_card(
    projected: dict[tuple[str, str], ProjectedEntity],
    card: Mapping[str, Any],
    *,
    sources: set[SourceKey] | None,
) -> None:
    card_id = _technical_id(card.get("id"))
    if not card_id or not _selected(sources, "card", card_id):
        return
    _add(
        projected,
        entity_type="card",
        entity_id=card_id,
        content=_without(
            card,
            "repair_order",
            "attachments",
            "vehicle_profile",
            "column",
            "position",
            "archived",
            "updated_at",
        ),
        routing={"column": card.get("column"), "position": card.get("position")},
        lifecycle=_lifecycle(card),
    )

    vehicle_profile = _mapping(card.get("vehicle_profile"))
    if _meaningful(vehicle_profile):
        _add(
            projected,
            entity_type="vehicle_profile",
            entity_id=card_id,
            content=vehicle_profile,
        )

    repair_order = _mapping(card.get("repair_order"))
    if _meaningful(repair_order):
        _add(
            projected,
            entity_type="repair_order",
            entity_id=card_id,
            content=_without(repair_order, "works", "materials", "payments"),
        )
        for index, work in enumerate(_items(repair_order.get("works"))):
            _add(
                projected,
                entity_type="repair_order_work",
                entity_id=f"{card_id}:work:{index}",
                content=work,
                routing={"index": index},
            )
        for index, material in enumerate(_items(repair_order.get("materials"))):
            _add(
                projected,
                entity_type="repair_order_material",
                entity_id=f"{card_id}:material:{index}",
                content=material,
                routing={"index": index},
            )
        for index, payment in enumerate(_items(repair_order.get("payments"))):
            payment_id = _technical_id(payment.get("id"), fallback=f"payment-{index}")
            _add(
                projected,
                entity_type="repair_order_payment",
                entity_id=f"{card_id}:payment:{payment_id}",
                content=payment,
                routing={"index": index},
            )

    for index, attachment in enumerate(_items(card.get("attachments"))):
        attachment_id = _technical_id(attachment.get("id"), fallback=f"attachment-{index}")
        _add(
            projected,
            entity_type="attachment",
            entity_id=f"{card_id}:attachment:{attachment_id}",
            content=_without(attachment, "removed"),
            routing={"index": index},
            lifecycle=_lifecycle(attachment, removed_field="removed"),
        )


def _project_client(
    projected: dict[tuple[str, str], ProjectedEntity],
    client: Mapping[str, Any],
    *,
    sources: set[SourceKey] | None,
) -> None:
    client_id = _technical_id(client.get("id"))
    if not client_id or not _selected(sources, "client", client_id):
        return
    _add(
        projected,
        entity_type="client",
        entity_id=client_id,
        content=_without(client, "vehicles", "deleted_vehicle_keys", "updated_at"),
    )
    for index, vehicle in enumerate(_items(client.get("vehicles"))):
        vehicle_id = _technical_id(vehicle.get("id"), fallback=f"vehicle-{index}")
        _add(
            projected,
            entity_type="client_vehicle",
            entity_id=f"{client_id}:vehicle:{vehicle_id}",
            content=vehicle,
            routing={"index": index},
        )


def _project_collection(
    projected: dict[tuple[str, str], ProjectedEntity],
    state: Mapping[str, Any],
    *,
    key: str,
    entity_type: str,
    routing_fields: tuple[str, ...] = (),
    sources: set[SourceKey] | None = None,
) -> None:
    if sources is not None and not any(
        source_type == entity_type for source_type, _source_id in sources
    ):
        return
    for index, item in enumerate(_items(state.get(key))):
        entity_id = _technical_id(item.get("id"), fallback=f"{entity_type}-{index}")
        if not _selected(sources, entity_type, entity_id):
            continue
        routing = {field: item.get(field) for field in routing_fields}
        content = _without(item, *routing_fields, "updated_at")
        _add(
            projected,
            entity_type=entity_type,
            entity_id=entity_id,
            content=content,
            routing=routing,
            lifecycle=_lifecycle(item),
        )


def _project_settings(
    projected: dict[tuple[str, str], ProjectedEntity],
    settings: Mapping[str, Any],
    *,
    sources: set[SourceKey] | None,
) -> None:
    collection_keys = {
        "employees": "employee",
        "employee_shift_accruals": "employee_shift_accrual",
        "employee_repair_order_accruals": "employee_repair_order_accrual",
    }
    for setting_key, entity_type in collection_keys.items():
        for index, item in enumerate(_items(settings.get(setting_key))):
            entity_id = _technical_id(item.get("id"), fallback=f"{entity_type}-{index}")
            if not _selected(sources, entity_type, entity_id):
                continue
            _add(
                projected,
                entity_type=entity_type,
                entity_id=entity_id,
                content=_without(item, "updated_at"),
                routing={"index": index},
            )
    board_settings = {
        key: value
        for key, value in settings.items()
        if key not in {*collection_keys, "ready_column_id", *_PRIVATE_VIEWER_SETTING_KEYS}
    }
    if _selected(sources, "board_settings", "board"):
        _add(
            projected,
            entity_type="board_settings",
            entity_id="board",
            content=board_settings,
        )


def project_crm_state(
    state: Mapping[str, Any] | object,
    *,
    sources: set[SourceKey] | None = None,
) -> dict[tuple[str, str], ProjectedEntity]:
    """Project all durable state entities to technical ids and irreversible digests."""

    source = state if isinstance(state, Mapping) else {}
    projected: dict[tuple[str, str], ProjectedEntity] = {}
    _project_collection(
        projected,
        source,
        key="columns",
        entity_type="column",
        routing_fields=("position",),
        sources=sources,
    )
    if sources is None or any(source_type == "card" for source_type, _source_id in sources):
        for card in _items(source.get("cards")):
            _project_card(projected, card, sources=sources)
    if sources is None or any(source_type == "client" for source_type, _source_id in sources):
        for client in _items(source.get("clients")):
            _project_client(projected, client, sources=sources)
    _project_collection(
        projected,
        source,
        key="stickies",
        entity_type="sticky",
        routing_fields=("x", "y"),
        sources=sources,
    )
    _project_collection(
        projected,
        source,
        key="cashboxes",
        entity_type="cashbox",
        routing_fields=("order",),
        sources=sources,
    )
    _project_collection(
        projected,
        source,
        key="cash_transactions",
        entity_type="cash_transaction",
        sources=sources,
    )
    _project_collection(
        projected,
        source,
        key="inventory_items",
        entity_type="inventory_item",
        sources=sources,
    )
    _project_collection(
        projected,
        source,
        key="inventory_movements",
        entity_type="inventory_movement",
        sources=sources,
    )
    _project_settings(projected, _mapping(source.get("settings")), sources=sources)
    return projected


def project_crm_source_signatures(state: Mapping[str, Any] | object) -> dict[SourceKey, str]:
    """Return cheap mutation signatures used to limit normal commit projection work."""

    source = state if isinstance(state, Mapping) else {}
    signatures: dict[SourceKey, str] = {}
    for key, entity_type in (
        ("columns", "column"),
        ("stickies", "sticky"),
        ("cashboxes", "cashbox"),
        ("cash_transactions", "cash_transaction"),
        ("inventory_items", "inventory_item"),
        ("inventory_movements", "inventory_movement"),
    ):
        for index, item in enumerate(_items(source.get(key))):
            entity_id = _technical_id(item.get("id"), fallback=f"{entity_type}-{index}")
            signatures[(entity_type, entity_id)] = _source_digest(
                item.get("updated_at"),
                item.get("created_at"),
                item.get("archived"),
                item.get("removed"),
                item.get("position"),
                item.get("order"),
                item.get("x"),
                item.get("y"),
                item.get("status"),
                item.get("cancelled_at"),
                item.get("is_cancelled"),
            )
    for card in _items(source.get("cards")):
        card_id = _technical_id(card.get("id"))
        if not card_id:
            continue
        repair_order = _mapping(card.get("repair_order"))
        signatures[("card", card_id)] = _source_digest(
            card.get("updated_at"),
            card.get("notification_updated_at"),
            card.get("column"),
            card.get("position"),
            card.get("archived"),
            card.get("is_unread"),
            _source_digest(
                *(
                    f"{key}:{value}"
                    for key, value in sorted(_mapping(card.get("seen_by_users")).items())
                )
            ),
            len(_items(card.get("attachments"))),
            len(_items(repair_order.get("works"))),
            len(_items(repair_order.get("materials"))),
            len(_items(repair_order.get("payments"))),
            repair_order.get("status"),
        )
    for client in _items(source.get("clients")):
        client_id = _technical_id(client.get("id"))
        if not client_id:
            continue
        vehicles = _items(client.get("vehicles"))
        signatures[("client", client_id)] = _source_digest(
            client.get("updated_at"),
            len(vehicles),
            ",".join(_technical_id(item.get("id")) for item in vehicles),
        )
    settings = _mapping(source.get("settings"))
    for setting_key, entity_type in (
        ("employees", "employee"),
        ("employee_shift_accruals", "employee_shift_accrual"),
        ("employee_repair_order_accruals", "employee_repair_order_accrual"),
    ):
        for index, item in enumerate(_items(settings.get(setting_key))):
            entity_id = _technical_id(item.get("id"), fallback=f"{entity_type}-{index}")
            signatures[(entity_type, entity_id)] = _source_digest(
                item.get("updated_at"),
                item.get("created_at"),
                item.get("is_active"),
                item.get("status"),
            )
    board_settings = {
        key: value
        for key, value in settings.items()
        if key
        not in {
            "employees",
            "employee_shift_accruals",
            "employee_repair_order_accruals",
            "ready_column_id",
            *_PRIVATE_VIEWER_SETTING_KEYS,
        }
    }
    signatures[("board_settings", "board")] = _digest(board_settings)
    return signatures


def project_shared_files(files: object) -> dict[tuple[str, str], ProjectedEntity]:
    """Project the shared-file index without retaining names or file content."""

    projected: dict[tuple[str, str], ProjectedEntity] = {}
    source = {"shared_files": files if isinstance(files, list) else []}
    _project_collection(
        projected,
        source,
        key="shared_files",
        entity_type="shared_file",
        routing_fields=("x", "y"),
    )
    return projected


def project_operator_users(state: object) -> dict[tuple[str, str], ProjectedEntity]:
    """Project operator accounts while hashing usernames and excluding sessions."""

    source = state if isinstance(state, Mapping) else {}
    projected: dict[tuple[str, str], ProjectedEntity] = {}
    for index, user in enumerate(_items(source.get("users"))):
        username = str(user.get("username") or f"operator-{index}").strip()
        entity_id = f"operator-{hashlib.sha256(username.encode('utf-8')).hexdigest()[:24]}"
        _add(
            projected,
            entity_type="operator_user",
            entity_id=entity_id,
            content=_without(user, "username", "updated_at"),
            routing={"role": user.get("role")},
        )
    return projected


def project_print_module(
    *,
    settings: object,
    templates: object,
    inspection_sheet_forms: object,
) -> dict[tuple[str, str], ProjectedEntity]:
    """Project durable print configuration and drafts without retaining their content."""

    projected: dict[tuple[str, str], ProjectedEntity] = {}
    settings_payload = _mapping(settings)
    _add(
        projected,
        entity_type="print_settings",
        entity_id="print-module",
        content=settings_payload,
    )
    for index, template in enumerate(_items(templates)):
        template_id = _technical_id(template.get("id"), fallback=f"print-template-{index}")
        _add(
            projected,
            entity_type="print_template",
            entity_id=template_id,
            content=_without(template, "updated_at"),
            routing={"document_type": template.get("document_type")},
        )
    forms = inspection_sheet_forms if isinstance(inspection_sheet_forms, Mapping) else {}
    for raw_card_id, raw_form in forms.items():
        if not isinstance(raw_form, Mapping):
            continue
        _add(
            projected,
            entity_type="inspection_sheet_form",
            entity_id=raw_card_id,
            content=_without(raw_form, "updated_at"),
        )
    return projected


def diff_projected_entities(
    previous: Mapping[tuple[str, str], ProjectedEntity],
    current: Mapping[tuple[str, str], ProjectedEntity],
) -> list[ProjectedChange]:
    changes: list[ProjectedChange] = []
    for key in sorted(set(previous) | set(current)):
        before = previous.get(key)
        after = current.get(key)
        if before is None and after is not None:
            if after.lifecycle == "archived":
                change_type, tombstone = "archive", True
            elif after.lifecycle == "removed":
                change_type, tombstone = "delete", True
            else:
                change_type, tombstone = "create", False
        elif before is not None and after is None:
            change_type, tombstone = "delete", True
        elif before is not None and after is not None:
            if before == after:
                continue
            if before.lifecycle != after.lifecycle:
                if after.lifecycle == "archived":
                    change_type, tombstone = "archive", True
                elif after.lifecycle == "removed":
                    change_type, tombstone = "delete", True
                else:
                    change_type, tombstone = "restore", False
            elif before.routing_digest != after.routing_digest:
                change_type, tombstone = "move", False
            else:
                change_type, tombstone = "update", False
        else:  # pragma: no cover - exhaustive guard for static analyzers
            continue
        entity = after or before
        assert entity is not None
        changes.append(
            ProjectedChange(
                entity_type=entity.entity_type,
                entity_id=entity.entity_id,
                change_type=change_type,
                tombstone=tombstone,
            )
        )
    return changes


__all__ = [
    "ProjectedChange",
    "ProjectedEntity",
    "diff_projected_entities",
    "project_crm_state",
    "project_crm_source_signatures",
    "project_operator_users",
    "project_print_module",
    "project_shared_files",
]
