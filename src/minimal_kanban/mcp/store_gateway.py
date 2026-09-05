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
        "download_store_quote_vin_photo",
    }
)
STORE_MANAGEMENT_CAPABILITY_NAME = "store_management_action"
STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME = "store_quote_conductor"
# The generic owner transport remains an implementation dependency for
# explicitly reviewed internal workflows, but it must never become a public
# raw escape hatch.  Admin V2 customer estimates are reachable only through
# the typed conductor below.
STORE_OWNER_API_CAPABILITY_NAME = "store_owner_api"
INTERNAL_ONLY_CAPABILITY_NAMES = frozenset(
    {
        *STORE_READ_CAPABILITY_NAMES,
        STORE_MANAGEMENT_CAPABILITY_NAME,
        STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
        STORE_OWNER_API_CAPABILITY_NAME,
    }
)
STORE_SEARCH_ENTITIES = frozenset(
    {
        "store_part",
        "store_order",
        "store_quote_request",
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
    }
)
# Only this generic operation advances a real order and may trigger an external
# customer notification. The remaining management actions are reversible
# internal coordination, so their Gateway path must not be a workflow ritual.
STORE_HIGH_IMPACT_MANAGEMENT_OPERATIONS = frozenset({"mark_order_ready"})
STORE_LOW_RISK_MANAGEMENT_OPERATIONS = (
    STORE_MANAGEMENT_OPERATIONS - STORE_HIGH_IMPACT_MANAGEMENT_OPERATIONS
)


def store_management_requires_native_guard(operation: str) -> bool:
    return operation in STORE_HIGH_IMPACT_MANAGEMENT_OPERATIONS


def inventory_gateway_operations(base_operations: frozenset[str]) -> frozenset[str]:
    """Extend the public inventory workflow enum with Store-owned operations."""

    return base_operations | STORE_MANAGEMENT_OPERATIONS | {STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME}


STORE_QUOTE_CONDUCTOR_OPERATIONS = frozenset(
    {
        "start",
        "status",
        "clarification",
        "evidence",
        "draft",
        "publish",
        "wait",
        "reply",
        "reopen",
        "order",
        "handoff",
        "decline",
    }
)
STORE_QUOTE_CONDUCTOR_STORE_WRITE_OPERATIONS = frozenset({"publish", "order"})
STORE_QUOTE_CONDUCTOR_LOW_RISK_OPERATIONS = frozenset({"draft", "reply"})
# Drafts, reopenings, and ordinary dialogue are operational context, not an
# externally published price or order. Their Manager contract still binds the
# target, but they do not impose a conversation template.
STORE_OPERATION_ENTITIES = {
    "assign_quote_request": "store_quote_request",
    "set_quote_request_status": "store_quote_request",
    "update_quote_request_comment": "store_quote_request",
    "add_quote_request_note": "store_quote_request",
    "set_batch_storage_location": "store_batch",
    "mark_order_ready": "store_order",
}
_STORE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$")
_STORE_QUOTE_CONDUCTOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_STORE_QUOTE_CONDUCTOR_HASH = re.compile(r"^[0-9a-f]{64}$")
_STORE_QUOTE_CONDUCTOR_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_STORE_QUOTE_CONDUCTOR_INBOUND_RECEIPT = re.compile(r"^[A-Za-z0-9._~-]{16,1024}$")
_STORE_CHANGE_FIELDS = {
    "assign_quote_request": ("assigned_user_id",),
    "set_quote_request_status": ("status",),
    "update_quote_request_comment": (
        "has_internal_comment",
        "internal_comment_sha256",
    ),
    "add_quote_request_note": ("notes_count",),
    "set_batch_storage_location": ("storage_location",),
    "mark_order_ready": ("status", "ready_at"),
}


def internal_only_capability_warning(name: str) -> str:
    return (
        "named_workflow_required"
        if name
        in {
            STORE_MANAGEMENT_CAPABILITY_NAME,
            STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
            STORE_OWNER_API_CAPABILITY_NAME,
        }
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


def store_implicit_idempotency_key(operation: str, payload: Mapping[str, Any]) -> str:
    """Give low-risk adapter calls a stable internal key when callers omit one."""

    canonical = json.dumps(
        {"operation": operation, "payload": dict(payload)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"store-implicit-{digest[:40]}"


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
    if operation not in STORE_MANAGEMENT_OPERATIONS:
        return {"passed": False, "warning": "store_operation_not_allowed"}
    native_guard = store_management_requires_native_guard(operation)
    if native_guard and mode not in {"dry_run", "apply"}:
        return {"passed": False, "warning": "store_mode_required_explicit_dry_run_or_apply"}
    effective_mode = str(mode or "apply")
    if effective_mode not in {"dry_run", "apply"}:
        return {"passed": False, "warning": "store_mode_invalid"}
    required = [("target_id", str(payload.get("target_id") or "").strip())]
    if native_guard:
        required.extend(
            (
                ("expected_updated_at", str(payload.get("expected_updated_at") or "").strip()),
                ("idempotency_key", str(idempotency_key or "").strip()),
                ("owner_intent", str(payload.get("owner_intent") or "").strip()),
            )
        )
    missing = [name for name, value in required if not value]
    if missing:
        return {
            "passed": False,
            "warning": (
                "store_write_exact_target_revision_owner_intent_and_idempotency_required"
                if native_guard
                else "store_target_id_required"
            ),
            "missing_fields": missing,
        }
    try:
        correlation_id = store_correlation_id(operation, payload)
    except ValueError:
        return {"passed": False, "warning": "store_correlation_id_invalid"}
    return {
        "passed": True,
        "correlation_id": correlation_id,
        "mode": effective_mode,
        "requires_native_guard": native_guard,
    }


def validate_store_quote_conductor_request(
    payload: Mapping[str, Any],
    *,
    idempotency_key: str,
    mode: str | None,
) -> dict[str, Any]:
    """Validate the narrow public bridge to Manager's quote conductor.

    The conductor owns its own durable Store workflow.  This gateway must not
    turn a phase call into the generic ``store_management_action`` path or put
    the transient estimate / Telegram content into a second ledger.
    """

    operation = _normalized_text(payload.get("operation")).casefold()
    if operation not in STORE_QUOTE_CONDUCTOR_OPERATIONS:
        return {
            "passed": False,
            "warning": "store_quote_conductor_operation_not_allowed",
        }

    is_store_write = operation in STORE_QUOTE_CONDUCTOR_STORE_WRITE_OPERATIONS
    is_low_risk = operation in STORE_QUOTE_CONDUCTOR_LOW_RISK_OPERATIONS
    nested_mode = _normalized_text(payload.get("mode"))
    nested_key = _normalized_text(payload.get("idempotency_key"))
    if is_store_write:
        if mode not in {"dry_run", "apply"}:
            return {
                "passed": False,
                "warning": "store_quote_conductor_write_mode_required_explicit_dry_run_or_apply",
            }
        effective_mode = str(mode)
        effective_key = _normalized_text(idempotency_key)
    elif is_low_risk:
        effective_mode = _normalized_text(mode) or nested_mode or "apply"
        if effective_mode not in {"dry_run", "apply"}:
            return {
                "passed": False,
                "warning": "store_quote_conductor_mode_invalid",
            }
        effective_key = (
            _normalized_text(idempotency_key)
            or nested_key
            or store_implicit_idempotency_key(operation, payload)
        )
    else:
        if mode not in {None, "apply"}:
            return {
                "passed": False,
                "warning": "store_quote_conductor_refs_only_apply_required",
            }
        effective_mode = "apply"
        effective_key = _normalized_text(idempotency_key)
    if not is_low_risk:
        if nested_mode and nested_mode != effective_mode:
            return {"passed": False, "warning": "store_quote_conductor_mode_conflict"}
        if nested_key and nested_key != effective_key:
            return {"passed": False, "warning": "store_quote_conductor_idempotency_key_conflict"}

    quote_request_id = _normalized_text(payload.get("quote_request_id"))
    run_id = _positive_int(payload.get("run_id"))
    expected_state_version = _positive_int(payload.get("expected_state_version"))
    missing: list[str] = []
    if _STORE_QUOTE_CONDUCTOR_ID.fullmatch(quote_request_id) is None:
        missing.append("quote_request_id")
    if operation != "start" and not is_low_risk:
        if run_id is None:
            missing.append("run_id")
        if operation != "status" and expected_state_version is None:
            missing.append("expected_state_version")
    if is_store_write or operation == "wait":
        if not _normalized_text(payload.get("expected_revision")):
            missing.append("expected_revision")
    if operation == "start" or is_store_write or operation == "wait":
        correlation_id = _normalized_text(payload.get("correlation_id"))
        if _STORE_CORRELATION_ID.fullmatch(correlation_id) is None:
            missing.append("correlation_id")
    if not effective_key:
        missing.append("idempotency_key")
    if missing:
        return {
            "passed": False,
            "warning": "store_quote_conductor_required_fields_missing_or_invalid",
            "missing_fields": list(dict.fromkeys(missing)),
        }

    entries = payload.get("entries")
    if operation == "draft":
        if (
            not isinstance(entries, list)
            or not 1 <= len(entries) <= 50
            or not all(isinstance(item, Mapping) for item in entries)
        ):
            return {"passed": False, "warning": "store_quote_conductor_entries_invalid"}
    elif entries is not None:
        return {"passed": False, "warning": "store_quote_conductor_entries_not_allowed"}

    coverage = payload.get("coverage")
    if operation == "draft" and (
        not isinstance(coverage, list)
        or not 1 <= len(coverage) <= 50
        or not all(isinstance(item, Mapping) for item in coverage)
    ):
        return {"passed": False, "warning": "store_quote_conductor_coverage_invalid"}
    if operation != "draft" and coverage is not None:
        return {"passed": False, "warning": "store_quote_conductor_coverage_not_allowed"}
    customer_response = payload.get("customer_response")
    if operation == "publish" and (
        not isinstance(customer_response, str) or not 1 <= len(customer_response.strip()) <= 2_000
    ):
        return {"passed": False, "warning": "store_quote_conductor_customer_response_invalid"}
    if operation != "publish" and customer_response not in (None, ""):
        return {"passed": False, "warning": "store_quote_conductor_customer_response_not_allowed"}
    evidence = payload.get("evidence")
    if operation == "evidence" and not isinstance(evidence, Mapping):
        return {"passed": False, "warning": "store_quote_conductor_evidence_required"}
    if evidence is not None and not isinstance(evidence, Mapping):
        return {"passed": False, "warning": "store_quote_conductor_evidence_invalid"}

    step_id = _normalized_text(payload.get("step_id"))
    if operation == "reply" and _STORE_QUOTE_CONDUCTOR_ID.fullmatch(step_id) is None:
        return {"passed": False, "warning": "store_quote_conductor_step_id_required"}
    reply_classification = _normalized_text(payload.get("reply_classification")).casefold()
    if (
        operation == "reply"
        and reply_classification
        and (_STORE_QUOTE_CONDUCTOR_SAFE_CODE.fullmatch(reply_classification) is None)
    ):
        return {"passed": False, "warning": "store_quote_conductor_reply_classification_invalid"}
    if operation != "reply" and reply_classification:
        return {
            "passed": False,
            "warning": "store_quote_conductor_reply_classification_not_allowed",
        }
    if operation == "reply":
        # The incoming Telegram bridge, not a Gateway caller, proves the
        # current quote/version/context.  These hashes used to be caller
        # assertions and are deliberately rejected on replies now.
        asserted_fields = (
            "consent_context_hash",
            "published_snapshot_hash",
            "telegram_context_hash",
        )
        if any(_normalized_text(payload.get(field)) for field in asserted_fields):
            return {
                "passed": False,
                "warning": "store_quote_conductor_reply_binding_must_be_transport_verified",
            }
        receipt = payload.get("telegram_inbound_receipt")
        if (
            not isinstance(receipt, str)
            or _STORE_QUOTE_CONDUCTOR_INBOUND_RECEIPT.fullmatch(receipt.strip()) is None
        ):
            return {
                "passed": False,
                "warning": "store_quote_conductor_telegram_inbound_receipt_required",
            }
    elif _normalized_text(payload.get("telegram_inbound_receipt")):
        return {
            "passed": False,
            "warning": "store_quote_conductor_telegram_inbound_receipt_not_allowed",
        }
    if operation == "reply":
        # Hash-shaped receipts are opaque transient capabilities.  Do not
        # normalize, retain, or return them from this Gateway.
        if len(str(payload.get("telegram_inbound_receipt") or "")) > 1024:
            return {
                "passed": False,
                "warning": "store_quote_conductor_telegram_inbound_receipt_required",
            }
    telegram_context_hash = _normalized_text(payload.get("telegram_context_hash")).casefold()
    telegram_message = payload.get("telegram_message")
    telegram_message_kind = _normalized_text(payload.get("telegram_message_kind")).casefold()
    if operation == "wait":
        if (
            _STORE_QUOTE_CONDUCTOR_HASH.fullmatch(
                _normalized_text(payload.get("published_snapshot_hash")).casefold()
            )
            is None
        ):
            return {
                "passed": False,
                "warning": "store_quote_conductor_wait_published_snapshot_hash_required",
            }
        if _STORE_QUOTE_CONDUCTOR_HASH.fullmatch(telegram_context_hash) is None:
            return {
                "passed": False,
                "warning": "store_quote_conductor_telegram_context_hash_required",
            }
        if _STORE_QUOTE_CONDUCTOR_SAFE_CODE.fullmatch(telegram_message_kind) is None:
            return {
                "passed": False,
                "warning": "store_quote_conductor_telegram_message_kind_invalid",
            }
        if not _valid_store_quote_telegram_text(telegram_message):
            return {
                "passed": False,
                "warning": "store_quote_conductor_telegram_message_invalid",
            }
    elif telegram_context_hash:
        if operation != "reply":
            return {
                "passed": False,
                "warning": "store_quote_conductor_telegram_context_hash_not_allowed",
            }
    if operation != "wait" and telegram_message not in (None, ""):
        return {
            "passed": False,
            "warning": "store_quote_conductor_telegram_message_not_allowed",
        }
    if operation != "wait" and telegram_message_kind:
        return {
            "passed": False,
            "warning": "store_quote_conductor_telegram_message_kind_not_allowed",
        }
    if operation == "order":
        for field in ("consent_context_hash", "published_snapshot_hash"):
            value = _normalized_text(payload.get(field)).casefold()
            if _STORE_QUOTE_CONDUCTOR_HASH.fullmatch(value) is None:
                return {
                    "passed": False,
                    "warning": "store_quote_conductor_order_context_hash_required",
                    "missing_fields": [field],
                }

    return {
        "passed": True,
        "operation": operation,
        "mode": effective_mode,
        "is_store_write": is_store_write,
        "is_low_risk": is_low_risk,
        "run_id": run_id,
        "expected_state_version": expected_state_version,
        "idempotency_key": effective_key,
    }


def store_quote_conductor_arguments(
    raw_tools: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    idempotency_key: str,
    mode: str,
) -> dict[str, Any]:
    """Project a Gateway payload onto the exact Manager conductor signature."""

    arguments: dict[str, Any] = {
        "operation": _normalized_text(payload.get("operation")).casefold(),
        "quote_request_id": _normalized_text(payload.get("quote_request_id")),
        "run_id": _positive_int(payload.get("run_id")),
        "expected_state_version": _positive_int(payload.get("expected_state_version")),
        "expected_revision": _normalized_text(payload.get("expected_revision")),
        "idempotency_key": str(idempotency_key).strip(),
        "correlation_id": _normalized_text(payload.get("correlation_id")),
        "entries": list(payload["entries"]) if isinstance(payload.get("entries"), list) else None,
        "evidence": dict(payload["evidence"])
        if isinstance(payload.get("evidence"), Mapping)
        else None,
        "step_id": _normalized_text(payload.get("step_id")),
        "reply_classification": _normalized_text(payload.get("reply_classification")).casefold(),
        "consent_context_hash": _normalized_text(payload.get("consent_context_hash")).casefold(),
        "published_snapshot_hash": _normalized_text(
            payload.get("published_snapshot_hash")
        ).casefold(),
        "telegram_context_hash": _normalized_text(payload.get("telegram_context_hash")).casefold(),
        # This opaque one-time receipt is intentionally transient: it is
        # passed only to the typed Manager transport readback and is excluded
        # from all Gateway projections and ledger-like output.
        "telegram_inbound_receipt": payload.get("telegram_inbound_receipt")
        if isinstance(payload.get("telegram_inbound_receipt"), str)
        else "",
        # Telegram wording is deliberately passed through only to the named
        # Manager capability.  This bridge never puts it in a Gateway ledger
        # or projects it back into the public result.
        "telegram_message": payload.get("telegram_message")
        if isinstance(payload.get("telegram_message"), str)
        else "",
        "telegram_message_kind": _normalized_text(payload.get("telegram_message_kind")).casefold(),
        "mode": mode,
    }
    # The implementation additionally accepts these optional Store-side fields.
    # They remain transient and are filtered out if an older Manager does not
    # advertise them in its hidden capability schema.
    if isinstance(payload.get("coverage"), list):
        arguments["coverage"] = list(payload["coverage"])
    if isinstance(payload.get("customer_response"), str):
        arguments["customer_response"] = payload["customer_response"]
    return compatible_arguments(raw_tools, STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME, arguments)


def store_quote_conductor_safe_projection(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return only refs-only conductor evidence for the public Gateway reply."""

    safe: dict[str, Any] = {}
    safe_hash_fields = {
        "consent_context_hash",
        "entries_hash",
        "evidence_hash",
        "published_snapshot_hash",
        "target_ref_sha256",
        "coverage_hash",
        "delivery_binding_sha256",
        "delivery_ref_sha256",
        "message_sha256",
        "reply_text_sha256",
        "incoming_ref_sha256",
        "inbound_binding_sha256",
        "quote_snapshot_hash",
        "telegram_context_hash",
    }
    safe_int_fields = {"state_version", "entries_count", "coverage_count", "evidence_count"}
    safe_code_fields = {
        "phase",
        "operation",
        "reply_classification",
        "error_code",
        "external_effect_state",
        "telegram_message_kind",
    }

    def collect(value: Any, *, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(value, Mapping):
            for raw_key, item in value.items():
                key = str(raw_key).strip().casefold()
                if key in safe_hash_fields:
                    normalized = _normalized_text(item).casefold()
                    if _STORE_QUOTE_CONDUCTOR_HASH.fullmatch(normalized):
                        safe[key] = normalized
                elif (
                    key in safe_int_fields and isinstance(item, int) and not isinstance(item, bool)
                ):
                    safe[key] = max(0, min(item, 1_000_000))
                elif key in safe_code_fields:
                    normalized = _normalized_text(item).casefold()
                    if _STORE_QUOTE_CONDUCTOR_SAFE_CODE.fullmatch(normalized):
                        safe[key] = normalized
                elif key in {"deduplicated", "idempotency_replay", "revision_verified"}:
                    safe[key] = bool(item)
                elif isinstance(item, Mapping):
                    collect(item, depth=depth + 1)

    collect(result.get("summary"))
    collect(result.get("data"))
    collect(result.get("meta"))
    collect(result.get("error"))
    return safe


def store_quote_conductor_safe_verification(result: Mapping[str, Any]) -> dict[str, bool]:
    """Keep verification scalar and code-only; raw Store or Telegram data never escapes."""

    source = result.get("verification")
    if not isinstance(source, Mapping):
        return {}
    return {
        str(key): bool(value)
        for key, value in source.items()
        if _STORE_QUOTE_CONDUCTOR_SAFE_CODE.fullmatch(str(key).strip()) and isinstance(value, bool)
    }


def store_quote_conductor_safe_warnings(result: Mapping[str, Any]) -> list[str]:
    warnings = result.get("warnings")
    if not isinstance(warnings, list):
        return []
    return [
        str(item).strip()
        for item in warnings[:20]
        if _STORE_QUOTE_CONDUCTOR_SAFE_CODE.fullmatch(str(item).strip())
    ]


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
            "expected_updated_at": str(payload.get("expected_updated_at") or "").strip(),
            "planned_changes": store_planned_changes(operation, payload),
            "owner_intent": str(payload.get("owner_intent") or "").strip()
            or "autonomous_low_risk_store_update",
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


def _valid_store_quote_telegram_text(value: Any) -> bool:
    """Accept natural bounded text while keeping transport-safe content."""

    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 4_096
        and not any(marker in value for marker in ("\r", "\n", "\x00"))
    )


def _normalized_optional_text(value: Any) -> str | None:
    normalized = _normalized_text(value)
    return normalized or None


def _normalized_status(value: Any) -> str:
    return _normalized_text(value).upper()


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


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
    "STORE_OWNER_API_CAPABILITY_NAME",
    "STORE_OPERATION_ENTITIES",
    "STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME",
    "STORE_QUOTE_CONDUCTOR_OPERATIONS",
    "STORE_QUOTE_CONDUCTOR_STORE_WRITE_OPERATIONS",
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
    "store_quote_conductor_arguments",
    "store_quote_conductor_safe_projection",
    "store_quote_conductor_safe_verification",
    "store_quote_conductor_safe_warnings",
    "store_reconciliation_envelope",
    "store_revision",
    "store_target",
    "validate_store_workflow_request",
    "validate_store_quote_conductor_request",
    "verify_store_operation",
    "verify_store_readback",
    "workflow_state_version",
]
