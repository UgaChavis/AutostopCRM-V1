from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


def _safe_confidence(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(parsed):
        return 0.0
    return round(max(0.0, min(1.0, parsed)), 2)


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    items: list[str] = []
    for raw in raw_items:
        text = _safe_text(raw)
        if text:
            items.append(text)
    return items


def _safe_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


@dataclass(frozen=True)
class FactEvidence:
    name: str
    value: Any = None
    status: str = "absent"
    source: str = "unknown"
    confidence: float = 0.0
    conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": _safe_text(self.name),
            "value": self.value,
            "status": str(self.status or "absent"),
            "source": str(self.source or "unknown"),
            "confidence": _safe_confidence(self.confidence),
            "conflicts": _safe_text_list(self.conflicts),
            "notes": _safe_text_list(self.notes),
        }


@dataclass(frozen=True)
class EvidenceResult:
    context_kind: str
    card_id: str = ""
    confirmed_facts: dict[str, Any] = field(default_factory=dict)
    fact_evidence: dict[str, FactEvidence] = field(default_factory=dict)
    missing_data: list[str] = field(default_factory=list)
    scenario_signals: dict[str, dict[str, bool]] = field(default_factory=dict)
    sensitive_fields: list[str] = field(default_factory=list)
    allowed_write_targets: list[str] = field(default_factory=list)
    summary: str = ""
    raw_context_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        fact_evidence = _safe_dict(self.fact_evidence)
        scenario_signals = _safe_dict(self.scenario_signals)
        return {
            "context_kind": _safe_text(self.context_kind),
            "card_id": _safe_text(self.card_id),
            "confirmed_facts": _safe_dict(self.confirmed_facts),
            "fact_evidence": {
                str(name): value.to_dict()
                for name, value in fact_evidence.items()
                if isinstance(value, FactEvidence)
            },
            "missing_data": _safe_text_list(self.missing_data),
            "scenario_signals": {
                str(name): {
                    "trigger_found": bool(signal.get("trigger_found")),
                    "confidence_enough": bool(signal.get("confidence_enough")),
                }
                for name, signal in scenario_signals.items()
                if isinstance(signal, dict)
            },
            "sensitive_fields": _safe_text_list(self.sensitive_fields),
            "allowed_write_targets": _safe_text_list(self.allowed_write_targets),
            "summary": _safe_text(self.summary),
            "raw_context_ref": _safe_text(self.raw_context_ref),
        }


@dataclass(frozen=True)
class PlanResult:
    scenario_id: str
    scenario_chain: list[str]
    execution_mode: str
    needs_external_tools: bool
    required_tools: list[str] = field(default_factory=list)
    optional_tools: list[str] = field(default_factory=list)
    tool_order: list[str] = field(default_factory=list)
    allowed_write_targets: list[str] = field(default_factory=list)
    forbidden_write_targets: list[str] = field(default_factory=list)
    stop_conditions: list[str] = field(default_factory=list)
    followup_policy: dict[str, Any] = field(default_factory=dict)
    confidence_mode: str = "standard"
    write_mode: str = "patch_only"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": _safe_text(self.scenario_id),
            "scenario_chain": _safe_text_list(self.scenario_chain),
            "execution_mode": _safe_text(self.execution_mode),
            "needs_external_tools": bool(self.needs_external_tools),
            "required_tools": _safe_text_list(self.required_tools),
            "optional_tools": _safe_text_list(self.optional_tools),
            "tool_order": _safe_text_list(self.tool_order),
            "allowed_write_targets": _safe_text_list(self.allowed_write_targets),
            "forbidden_write_targets": _safe_text_list(self.forbidden_write_targets),
            "stop_conditions": _safe_text_list(self.stop_conditions),
            "followup_policy": _safe_dict(self.followup_policy),
            "confidence_mode": _safe_text(self.confidence_mode),
            "write_mode": _safe_text(self.write_mode),
            "notes": _safe_text_list(self.notes),
        }


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    status: str
    source_type: str
    confidence: float
    data: dict[str, Any] = field(default_factory=dict)
    raw_ref: str = ""
    evidence_ref: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": _safe_text(self.tool_name),
            "status": _safe_text(self.status),
            "source_type": _safe_text(self.source_type),
            "confidence": _safe_confidence(self.confidence),
            "data": _safe_dict(self.data),
            "raw_ref": _safe_text(self.raw_ref),
            "evidence_ref": _safe_text(self.evidence_ref),
            "reason": _safe_text(self.reason),
        }


@dataclass(frozen=True)
class PatchResult:
    card_patch: dict[str, Any] = field(default_factory=dict)
    repair_order_patch: dict[str, Any] = field(default_factory=dict)
    repair_order_works: list[dict[str, Any]] = field(default_factory=list)
    repair_order_materials: list[dict[str, Any]] = field(default_factory=list)
    append_only_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    human_review_needed: bool = False

    def is_empty(self) -> bool:
        return not any(
            (
                self.card_patch,
                self.repair_order_patch,
                self.repair_order_works,
                self.repair_order_materials,
                self.append_only_notes,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_patch": _safe_dict(self.card_patch),
            "repair_order_patch": _safe_dict(self.repair_order_patch),
            "repair_order_works": _safe_dict_list(self.repair_order_works),
            "repair_order_materials": _safe_dict_list(self.repair_order_materials),
            "append_only_notes": _safe_text_list(self.append_only_notes),
            "warnings": _safe_text_list(self.warnings),
            "human_review_needed": bool(self.human_review_needed),
        }


@dataclass(frozen=True)
class VerifyResult:
    applied_ok: bool
    fields_changed: list[str] = field(default_factory=list)
    manual_fields_preserved: bool = True
    scenario_completed: bool = False
    needs_followup: bool = False
    outcome_state: str = "unknown"
    warnings: list[str] = field(default_factory=list)
    context_ref: str = ""
    followup_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied_ok": bool(self.applied_ok),
            "fields_changed": _safe_text_list(self.fields_changed),
            "manual_fields_preserved": bool(self.manual_fields_preserved),
            "scenario_completed": bool(self.scenario_completed),
            "needs_followup": bool(self.needs_followup),
            "outcome_state": _safe_text(self.outcome_state),
            "warnings": _safe_text_list(self.warnings),
            "context_ref": _safe_text(self.context_ref),
            "followup_reason": _safe_text(self.followup_reason),
        }


@dataclass(frozen=True)
class OrchestrationTrace:
    version: str
    trigger: dict[str, Any]
    context_snapshot_id: str
    evidence: EvidenceResult
    plan: PlanResult
    scenario_feedback: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    patch: PatchResult = field(default_factory=PatchResult)
    verify: VerifyResult = field(default_factory=lambda: VerifyResult(applied_ok=False))

    def to_dict(self) -> dict[str, Any]:
        tool_results = self.tool_results if isinstance(self.tool_results, list) else []
        return {
            "version": _safe_text(self.version),
            "trigger": _safe_dict(self.trigger),
            "context_snapshot_id": _safe_text(self.context_snapshot_id),
            "evidence": self.evidence.to_dict(),
            "plan": self.plan.to_dict(),
            "scenario_feedback": _safe_dict_list(self.scenario_feedback),
            "tool_results": [
                item.to_dict() for item in tool_results if isinstance(item, ToolResult)
            ],
            "patch": self.patch.to_dict(),
            "verify": self.verify.to_dict(),
        }
