from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

STORE_READ_CAPABILITY_NAMES = frozenset(
    {
        "store_runtime_status",
        "store_digest",
        "store_search",
        "store_entity_context",
    }
)
STORE_MANAGEMENT_CAPABILITY_NAME = "store_management_action"
INTERNAL_ONLY_CAPABILITY_NAMES = frozenset(
    {*STORE_READ_CAPABILITY_NAMES, STORE_MANAGEMENT_CAPABILITY_NAME}
)
STORE_SEARCH_ENTITIES = frozenset(
    {
        "store_part",
        "store_order",
        "store_quote_request",
        "store_supplier",
        "store_batch",
        "store_warehouse_operation",
        "store_marketplace_listing",
        "store_state",
        "store_sourcing_offer",
    }
)
STORE_MANAGEMENT_OPERATIONS = frozenset(
    {
        "assign_quote_request",
        "set_quote_request_status",
        "update_quote_request_comment",
        "set_batch_storage_location",
        "mark_order_ready",
        "add_quote_request_note",
        "replace_quote_offer_drafts",
    }
)
STORE_OPERATION_ENTITIES = {
    "assign_quote_request": "store_quote_request",
    "set_quote_request_status": "store_quote_request",
    "update_quote_request_comment": "store_quote_request",
    "add_quote_request_note": "store_quote_request",
    "replace_quote_offer_drafts": "store_quote_request",
    "set_batch_storage_location": "store_batch",
    "mark_order_ready": "store_order",
}
_STORE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$")
_STORE_CHANGE_FIELDS = {
    "assign_quote_request": ("assigned_user_id",),
    "set_quote_request_status": ("status",),
    "update_quote_request_comment": (
        "has_internal_comment",
        "internal_comment_sha256",
    ),
    "add_quote_request_note": ("notes_count",),
    "replace_quote_offer_drafts": ("agent_draft_count",),
    "set_batch_storage_location": ("storage_location",),
    "mark_order_ready": ("status", "ready_at"),
}


def internal_only_capability_warning(name: str) -> str:
    return (
        "named_workflow_required"
        if name == STORE_MANAGEMENT_CAPABILITY_NAME
        else "named_operation_required"
    )


def compatible_arguments(
    raw_tools: Mapping[str, Any], name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """Pass only fields declared by a hidden Manager capability."""

    tool = raw_tools.get(name)
    schema = getattr(tool, "parameters", {}) if tool is not None else {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict) or not properties:
        return dict(arguments)
    return {key: value for key, value in arguments.items() if key in properties}


def normalized_store_data(result: Mapping[str, Any]) -> Any:
    source_data = result.get("data")
    data = dict(source_data) if isinstance(source_data, Mapping) else {}
    if isinstance(result.get("items"), list):
        data.setdefault("items", result.get("items"))
    if isinstance(result.get("changes"), (list, dict)):
        data.setdefault("changes", result.get("changes"))
    summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    if isinstance(summary.get("result"), Mapping):
        data.setdefault("result", dict(summary["result"]))
    meta = result.get("meta") if isinstance(result.get("meta"), Mapping) else {}
    effects = _safe_store_effects(meta.get("effects"))
    if effects:
        data["effects"] = effects
    if "external_effect_state" in meta:
        data["external_effect_state"] = _bounded_text(meta.get("external_effect_state"), 64)
    if "idempotency_replay" in meta:
        data["idempotency_replay"] = bool(meta.get("idempotency_replay"))
    if "correlation_id" in meta:
        data["correlation_id"] = _bounded_text(meta.get("correlation_id"), 160)
    ttl = meta.get("dry_run_proof_ttl_seconds")
    if isinstance(ttl, int) and not isinstance(ttl, bool):
        data["dry_run_proof_ttl_seconds"] = max(0, min(ttl, 86_400))
    return data or source_data


def store_gateway_envelope(
    result: Mapping[str, Any],
    *,
    summary: Mapping[str, Any],
    item_limit: int,
    envelope_factory: Callable[..., dict[str, Any]],
    compact: Callable[..., Any],
) -> dict[str, Any]:
    source_summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    source_page = result.get("page") if isinstance(result.get("page"), Mapping) else {}
    warnings = [str(item) for item in result.get("warnings") or [] if str(item).strip()]
    ok = bool(result.get("ok"))
    if not ok and not warnings:
        warnings = [
            str(_find_value(result, frozenset({"code", "error_code"})) or "store_unavailable")
        ]
    return envelope_factory(
        ok=ok,
        status=str(result.get("status") or ("completed" if ok else "degraded")),
        summary={**dict(summary), **compact(dict(source_summary), item_limit=10, key_limit=30)},
        data=compact(normalized_store_data(result), item_limit=item_limit, key_limit=100),
        verification=compact(
            result.get("verification") if isinstance(result.get("verification"), Mapping) else {},
            item_limit=10,
        ),
        warnings=warnings,
        next_actions=[str(item) for item in result.get("next_actions") or []][:10],
        page=compact(dict(source_page), item_limit=10, key_limit=20),
        meta={"source": "autostop_manager_store_adapter"},
    )


def store_reconciliation_envelope(
    outcome: Mapping[str, Any],
    *,
    label: str,
    operation: str,
    run_id: int | None,
    envelope_factory: Callable[..., dict[str, Any]],
    compact: Callable[..., Any],
) -> dict[str, Any]:
    """Build the compact public result for an explicit receipt replay."""

    return envelope_factory(
        ok=bool(outcome["ok"]),
        status=str(outcome["status"]),
        run_id=run_id,
        summary={
            "workflow_id": label,
            "operation": operation,
            "mode": "apply",
            "deduplicated": True,
            "reconciliation": "store_receipt_replay",
        },
        data=compact(outcome["data"]),
        verification=dict(outcome["verification"]),
        warnings=list(outcome["warnings"]),
        next_actions=(
            []
            if outcome["ok"]
            else [f"workflow_status(run_id={run_id}) and reconcile exact Store receipt"]
        ),
        meta={
            "mode": "apply",
            "dry_run": False,
            "ledger_owned_by_named_workflow": True,
            "explicit_retry_reconciliation": True,
        },
    )


def store_correlation_id(operation: str, payload: Mapping[str, Any]) -> str:
    """Preserve a valid explicit correlation or derive one from action identity."""

    explicit = payload.get("correlation_id")
    if explicit not in (None, ""):
        value = str(explicit)
        if _STORE_CORRELATION_ID.fullmatch(value) is None:
            raise ValueError("store_correlation_id_invalid")
        return value
    canonical = json.dumps(
        {
            "operation": operation,
            "target_id": str(payload.get("target_id") or "").strip(),
            "expected_updated_at": str(payload.get("expected_updated_at") or "").strip(),
            "planned_changes": store_planned_changes(operation, payload),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"store-action-{digest[:40]}"


def workflow_state_version(value: Mapping[str, Any] | None) -> int | None:
    """Read the Manager CAS version from a direct or enveloped workflow result."""

    if not isinstance(value, Mapping):
        return None
    candidate = value.get("state_version")
    if not isinstance(candidate, int):
        summary = value.get("summary")
        candidate = summary.get("state_version") if isinstance(summary, Mapping) else None
    return candidate if isinstance(candidate, int) and candidate > 0 else None


def store_internal_comment_sha256(value: Any) -> str:
    """Match AutoStop App's PII-safe canonical internal-comment digest."""

    normalized = str(value or "").strip()
    canonical = "none:" if not normalized else f"comment:{normalized}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def store_ledger_verification(
    verification: Mapping[str, Any],
    *,
    executor_ok: bool,
    idempotency_replay: bool | None = None,
) -> dict[str, bool]:
    """Flatten readback evidence into Manager's refs-only scalar ledger contract."""

    evidence = verification.get("evidence")
    checks = evidence.get("checks") if isinstance(evidence, Mapping) else {}
    checks = checks if isinstance(checks, Mapping) else {}
    result: dict[str, bool] = {
        "executor_ok": bool(executor_ok),
        "verification_passed": bool(verification.get("passed")),
    }
    direct_checks = {
        "readback_ok": "readback_ok",
        "target_id_exact": "target_id_exact",
        "change_envelope_exact": "field_envelope_exact",
        "correlation_id_exact": "correlation_id_exact",
        "dry_run_did_not_change_revision": "revision_unchanged",
        "revision_advanced": "revision_advanced",
        "manager_verification_passed": "manager_verified",
        "external_effect_terminal": "external_effect_terminal",
        "ready_at_present": "transition_time_present",
    }
    for source, target in direct_checks.items():
        if source in checks:
            result[target] = bool(checks[source])

    value_check_names = {
        "assignee_exact",
        "comment_presence_exact",
        "comment_sha256_exact",
        "status_exact",
        "status_ready",
        "storage_location_exact",
    }
    value_checks = [bool(value) for key, value in checks.items() if key in value_check_names]
    if value_checks:
        result["field_value_exact"] = all(value_checks)
    if idempotency_replay is not None:
        result["idempotency_replay"] = bool(idempotency_replay)
    return result


def validate_store_workflow_request(
    operation: str,
    payload: Mapping[str, Any],
    *,
    idempotency_key: str,
    mode: str | None,
) -> dict[str, Any]:
    if mode not in {"dry_run", "apply"}:
        return {"passed": False, "warning": "store_mode_required_explicit_dry_run_or_apply"}
    missing = [
        name
        for name, value in (
            ("target_id", str(payload.get("target_id") or "").strip()),
            (
                "expected_updated_at",
                str(payload.get("expected_updated_at") or "").strip(),
            ),
            ("idempotency_key", str(idempotency_key or "").strip()),
            ("owner_intent", str(payload.get("owner_intent") or "").strip()),
        )
        if not value
    ]
    if missing:
        return {
            "passed": False,
            "warning": "store_write_exact_target_revision_owner_intent_and_idempotency_required",
            "missing_fields": missing,
        }
    try:
        correlation_id = store_correlation_id(operation, payload)
    except ValueError:
        return {"passed": False, "warning": "store_correlation_id_invalid"}
    return {"passed": True, "correlation_id": correlation_id}


def store_action_arguments(
    raw_tools: Mapping[str, Any],
    operation: str,
    payload: Mapping[str, Any],
    *,
    idempotency_key: str,
    mode: str,
    correlation_id: str,
) -> dict[str, Any]:
    return compatible_arguments(
        raw_tools,
        STORE_MANAGEMENT_CAPABILITY_NAME,
        {
            "domain": STORE_OPERATION_ENTITIES[operation],
            "action": operation,
            "target_id": str(payload.get("target_id") or "").strip(),
            "planned_changes": store_planned_changes(operation, payload),
            "owner_intent": str(payload.get("owner_intent") or "").strip(),
            "expected_updated_at": str(payload.get("expected_updated_at") or "").strip(),
            "idempotency_key": idempotency_key,
            "mode": mode,
            "correlation_id": correlation_id,
        },
    )


def store_planned_changes(operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the action-specific canonical Store write payload."""

    explicit = payload.get("planned_changes")
    if isinstance(explicit, Mapping):
        changes = dict(explicit)
    else:
        control_fields = {
            "correlation_id",
            "expected_updated_at",
            "idempotency_key",
            "mode",
            "owner_intent",
            "target_id",
        }
        changes = {key: value for key, value in payload.items() if key not in control_fields}

    if operation == "assign_quote_request":
        return {"assignee_id": _normalized_text(changes.get("assignee_id"))}
    if operation == "set_quote_request_status":
        return {"status": _normalized_status(changes.get("status"))}
    if operation == "update_quote_request_comment":
        return {"internal_comment": _normalized_optional_text(changes.get("internal_comment"))}
    if operation == "add_quote_request_note":
        return {"text": _normalized_text(changes.get("text"))}
    if operation == "replace_quote_offer_drafts":
        return {"items": list(changes.get("items") or [])}
    if operation == "set_batch_storage_location":
        return {"storage_location": _normalized_text(changes.get("storage_location"))}
    if operation == "mark_order_ready":
        return {"status": "READY"}
    return changes


def store_revision(value: Any) -> str:
    for key in ("updated_at", "updatedAt", "revision", "version"):
        candidate = _find_value(value, frozenset({key}))
        if candidate not in (None, ""):
            return str(candidate).strip()
    return ""


def store_target(value: Any, target_id: str) -> dict[str, Any] | None:
    for key in ("id", "entity_id", "target_id"):
        candidate = _find_mapping(value, key, target_id)
        if candidate is not None:
            return candidate
    return None


async def preflight_store_write(
    operation: str,
    payload: Mapping[str, Any],
    read_target: Callable[[str, str], Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    entity = STORE_OPERATION_ENTITIES[operation]
    target_id = str(payload.get("target_id") or "").strip()
    expected_updated_at = str(payload.get("expected_updated_at") or "").strip()
    context = await read_target(entity, target_id)
    target = store_target(context, target_id)
    actual_updated_at = store_revision(target or context)
    checks = {
        "context_read_ok": bool(context.get("ok")),
        "target_id_exact": target is not None,
        "expected_updated_at_exact": bool(expected_updated_at)
        and actual_updated_at == expected_updated_at,
    }
    return context, {
        "passed": all(checks.values()),
        "entity": entity,
        "target_id": target_id,
        "actual_updated_at": actual_updated_at or None,
        "checks": checks,
    }


def verify_store_readback(
    operation: str,
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    mode: str,
    preflight: Mapping[str, Any],
    readback: Mapping[str, Any],
) -> dict[str, Any]:
    entity = STORE_OPERATION_ENTITIES[operation]
    target_id = str(payload.get("target_id") or "").strip()
    target = store_target(readback, target_id)
    readback_revision = store_revision(target or readback)
    changes = store_planned_changes(operation, payload)
    expected_correlation_id = store_correlation_id(operation, payload)
    actual_correlation_id = str(_find_value(result, frozenset({"correlation_id"})) or "").strip()
    checks: dict[str, bool] = {
        "readback_ok": bool(readback.get("ok")),
        "target_id_exact": target is not None,
        "change_envelope_exact": _store_change_fields(result) == _STORE_CHANGE_FIELDS[operation],
        "correlation_id_exact": actual_correlation_id == expected_correlation_id,
    }
    if mode == "dry_run":
        checks["dry_run_did_not_change_revision"] = bool(
            readback_revision
        ) and readback_revision == str(preflight.get("actual_updated_at") or "")
    else:
        checks["revision_advanced"] = bool(readback_revision) and readback_revision != str(
            preflight.get("actual_updated_at") or ""
        )
    if mode != "dry_run" and operation == "assign_quote_request":
        assignee_id = changes.get("assignee_id")
        checks["assignee_exact"] = bool(assignee_id) and _contains_any_value(
            target or readback,
            ("assigned_user_id",),
            assignee_id,
            normalizer=_normalized_text,
        )
    elif mode != "dry_run" and operation == "set_quote_request_status":
        status = _normalized_status(changes.get("status"))
        checks["status_exact"] = bool(status) and _contains_any_value(
            target or readback,
            ("status",),
            status,
            normalizer=_normalized_status,
        )
    elif mode != "dry_run" and operation == "update_quote_request_comment":
        comment = changes.get("internal_comment")
        checks["comment_presence_exact"] = _contains_any_value(
            target or readback,
            ("has_internal_comment",),
            bool(comment),
        )
        checks["comment_sha256_exact"] = _contains_any_value(
            target or readback,
            ("internal_comment_sha256",),
            store_internal_comment_sha256(comment),
            normalizer=_normalized_text,
        )
    elif mode != "dry_run" and operation == "add_quote_request_note":
        expected_text = _normalized_text(changes.get("text"))
        notes = _find_value(target or readback, frozenset({"notes"}))
        checks["note_appended_exact"] = isinstance(notes, list) and any(
            isinstance(note, Mapping)
            and note.get("origin") == "AUTOSTOP_MANAGER"
            and _normalized_text(note.get("text")) == expected_text
            for note in notes
        )
    elif mode != "dry_run" and operation == "replace_quote_offer_drafts":
        expected_items = changes.get("items")
        observed_items = _find_value(target or readback, frozenset({"items"}))
        checks["draft_candidates_exact"] = _draft_candidates_exact(expected_items, observed_items)
    elif mode != "dry_run" and operation == "set_batch_storage_location":
        location = changes.get("storage_location")
        checks["storage_location_exact"] = bool(location) and _contains_any_value(
            target or readback,
            ("storage_location", "storageLocation", "warehouse_location"),
            location,
            normalizer=_normalized_text,
        )
    elif mode != "dry_run" and operation == "mark_order_ready":
        checks["status_ready"] = _contains_any_value(
            target or readback,
            ("status",),
            "READY",
            normalizer=_normalized_status,
        )
        checks["ready_at_present"] = _find_value(
            target or readback, frozenset({"ready_at"})
        ) not in (None, "")
        external_effect_state = _normalized_status(
            _find_value(result, frozenset({"external_effect_state"}))
        )
        checks["external_effect_terminal"] = external_effect_state in {
            "SENT",
            "NOT_APPLICABLE",
        }

    result_verification = (
        result.get("verification") if isinstance(result.get("verification"), Mapping) else {}
    )
    if "passed" in result_verification:
        checks["manager_verification_passed"] = bool(result_verification.get("passed"))
    return {
        "required": True,
        "passed": all(checks.values()),
        "check": "store_entity_context_exact_readback",
        "evidence": {
            "entity": entity,
            "target_id": target_id,
            "readback_updated_at": readback_revision or None,
            "checks": checks,
            "correlation_id": actual_correlation_id or None,
            "external_effect_state": _find_value(result, frozenset({"external_effect_state"})),
        },
    }


def _draft_candidates_exact(expected_items: Any, observed_items: Any) -> bool:
    if not isinstance(expected_items, list) or not isinstance(observed_items, list):
        return False
    observed: dict[str, set[str]] = {}
    for item in observed_items:
        if not isinstance(item, Mapping):
            continue
        observed[str(item.get("item_id") or "")] = {
            str(offer.get("candidate_key") or "")
            for offer in item.get("offers", [])
            if isinstance(offer, Mapping)
            and offer.get("origin") == "AUTOSTOP_MANAGER"
            and offer.get("publication_status") == "DRAFT"
        }
    for item in expected_items:
        if not isinstance(item, Mapping):
            return False
        expected = {
            str(draft.get("candidate_key") or "")
            for draft in item.get("drafts", [])
            if isinstance(draft, Mapping)
        }
        if observed.get(str(item.get("item_id") or ""), set()) != expected:
            return False
    return True


async def verify_store_operation(
    operation: str,
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    mode: str,
    preflight: Mapping[str, Any],
    read_target: Callable[[str, str], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    entity = STORE_OPERATION_ENTITIES[operation]
    target_id = str(payload.get("target_id") or "").strip()
    readback = await read_target(entity, target_id)
    return verify_store_readback(
        operation,
        payload,
        result,
        mode=mode,
        preflight=preflight,
        readback=readback,
    )


async def reconcile_store_receipt(
    operation: str,
    payload: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any],
    run_id: int | None,
    started: Mapping[str, Any],
    invoke_action: Callable[[str, Mapping[str, Any]], Awaitable[dict[str, Any]]],
    read_target: Callable[[str, str], Awaitable[dict[str, Any]]],
    transition: Callable[..., Awaitable[dict[str, Any]]],
    state_version: Callable[[Mapping[str, Any] | None], int | None],
) -> dict[str, Any]:
    """Reconcile an explicit repeat only when Store returns its saved receipt."""

    result = await invoke_action(STORE_MANAGEMENT_CAPABILITY_NAME, arguments)
    receipt_replayed = bool(_find_value(result, frozenset({"idempotency_replay"})))
    verification = await verify_store_operation(
        operation,
        payload,
        result,
        mode="apply",
        preflight={"actual_updated_at": str(payload.get("expected_updated_at") or "").strip()},
        read_target=read_target,
    )
    executor_ok = bool(result.get("ok"))
    verification_passed = bool(verification.get("passed"))
    completed: dict[str, Any] = {}
    if run_id is not None and executor_ok and receipt_replayed and verification_passed:
        completed = await transition(
            run_id,
            "completed",
            expected_state_version=state_version(started),
            message=f"completed {operation}",
            verification=store_ledger_verification(
                verification,
                executor_ok=True,
                idempotency_replay=True,
            ),
            summary=f"store:{operation}",
        )
    ledger_closed = bool(completed.get("ok")) and str(completed.get("status")) == "completed"
    overall_ok = executor_ok and receipt_replayed and verification_passed and ledger_closed
    return {
        "ok": overall_ok,
        "status": "completed" if overall_ok else "compensating",
        "data": normalized_store_data(result),
        "verification": {
            "executor_ok": executor_ok,
            "ledger_closed": ledger_closed,
            "idempotency_reused": True,
            "idempotency_replay": receipt_replayed,
            **verification,
        },
        "warnings": [
            *[str(item) for item in result.get("warnings") or [] if str(item).strip()],
            (
                "store_receipt_replayed_and_reconciled"
                if overall_ok
                else "store_receipt_replay_reconciliation_incomplete"
            ),
        ],
    }


def _find_value(value: Any, keys: frozenset[str], *, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if isinstance(value, Mapping):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, "", [], {}):
                return candidate
        for candidate in value.values():
            found = _find_value(candidate, keys, depth=depth + 1)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for candidate in value[:25]:
            found = _find_value(candidate, keys, depth=depth + 1)
            if found not in (None, "", [], {}):
                return found
    return None


def _find_mapping(value: Any, key: str, expected: Any, *, depth: int = 0) -> dict[str, Any] | None:
    if depth > 7:
        return None
    if isinstance(value, Mapping):
        if key in value and str(value.get(key)) == str(expected):
            return dict(value)
        for item in value.values():
            found = _find_mapping(item, key, expected, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value[:200]:
            found = _find_mapping(item, key, expected, depth=depth + 1)
            if found is not None:
                return found
    return None


def _contains_value(
    value: Any,
    key: str,
    expected: Any,
    *,
    normalizer: Any = None,
    depth: int = 0,
) -> bool:
    if depth > 7:
        return False
    if isinstance(value, Mapping):
        if key in value:
            actual = value.get(key)
            if normalizer is not None:
                actual = normalizer(actual)
                expected = normalizer(expected)
            if actual is None or expected is None:
                if actual is expected:
                    return True
            elif str(actual) == str(expected):
                return True
        return any(
            _contains_value(
                item,
                key,
                expected,
                normalizer=normalizer,
                depth=depth + 1,
            )
            for item in value.values()
        )
    if isinstance(value, list):
        return any(
            _contains_value(
                item,
                key,
                expected,
                normalizer=normalizer,
                depth=depth + 1,
            )
            for item in value[:200]
        )
    return False


def _contains_any_value(
    value: Any,
    keys: tuple[str, ...],
    expected: Any,
    *,
    normalizer: Any = None,
) -> bool:
    return any(_contains_value(value, key, expected, normalizer=normalizer) for key in keys)


def _store_change_fields(value: Mapping[str, Any]) -> tuple[str, ...]:
    changes = value.get("changes")
    if not isinstance(changes, list):
        return ()
    return tuple(
        str(change.get("field") or "").strip() for change in changes if isinstance(change, Mapping)
    )


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_optional_text(value: Any) -> str | None:
    normalized = _normalized_text(value)
    return normalized or None


def _normalized_status(value: Any) -> str:
    return _normalized_text(value).upper()


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _safe_store_effects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    safe: list[dict[str, Any]] = []
    for item in value[:10]:
        if not isinstance(item, Mapping):
            continue
        effect = _bounded_text(item.get("effect"), 160)
        if not effect:
            continue
        normalized: dict[str, Any] = {"effect": effect}
        for key in (
            "applies",
            "best_effort",
            "configured",
            "customer_linked",
            "cached_chat_available",
        ):
            if key in item:
                normalized[key] = bool(item.get(key))
        if "status" in item:
            normalized["status"] = _bounded_text(item.get("status"), 64)
        if "local_items" in item and isinstance(item.get("local_items"), int):
            normalized["local_items"] = max(0, min(item["local_items"], 1_000_000))
        deliverability = _normalized_status(item.get("deliverability"))
        if deliverability in {
            "CLAIMED",
            "DISCOVERY_REQUIRED",
            "FAILED",
            "NOT_APPLICABLE",
            "READY",
            "SENT",
        }:
            normalized["deliverability"] = deliverability
        safe.append(normalized)
    return safe


__all__ = [
    "INTERNAL_ONLY_CAPABILITY_NAMES",
    "STORE_MANAGEMENT_CAPABILITY_NAME",
    "STORE_MANAGEMENT_OPERATIONS",
    "STORE_OPERATION_ENTITIES",
    "STORE_READ_CAPABILITY_NAMES",
    "STORE_SEARCH_ENTITIES",
    "compatible_arguments",
    "internal_only_capability_warning",
    "normalized_store_data",
    "preflight_store_write",
    "reconcile_store_receipt",
    "store_action_arguments",
    "store_correlation_id",
    "store_gateway_envelope",
    "store_internal_comment_sha256",
    "store_ledger_verification",
    "store_planned_changes",
    "store_reconciliation_envelope",
    "store_revision",
    "store_target",
    "validate_store_workflow_request",
    "verify_store_operation",
    "verify_store_readback",
    "workflow_state_version",
]
