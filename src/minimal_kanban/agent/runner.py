from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid
from typing import Any

from ..mcp.client import BoardApiClient, BoardApiTransportError, discover_board_api
from ..models import utc_now_iso
from ..services.vehicle_profile_service import VehicleProfileService
from ..vehicle_profile import VEHICLE_PRIMARY_FIELDS
from .config import (
    get_agent_board_api_url,
    get_agent_enabled,
    get_agent_max_steps,
    get_agent_max_tool_result_chars,
    get_agent_name,
    get_agent_openai_model,
    get_agent_poll_interval_seconds,
)
from .contracts import (
    EvidenceResult,
    FactEvidence,
    OrchestrationTrace,
    PatchResult,
    PlanResult,
    ToolResult,
    VerifyResult,
)
from .instructions import build_default_system_prompt
from .openai_client import AgentModelError, OpenAIJsonAgentClient
from .policy import ToolPolicyEngine
from .runner_output import AgentRunnerOutputMixin
from .storage import AgentStorage
from .tools import AgentToolExecutor

DEFAULT_SYSTEM_PROMPT = build_default_system_prompt()
_AUTOFILL_VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_AUTOFILL_DTC_PATTERN = re.compile(r"\b[PBCU][0-9]{4}\b", re.IGNORECASE)
_AUTOFILL_MILEAGE_PATTERN = re.compile(
    r"(?:пробег|mileage|одометр)\s*[:\-]?\s*([\d\s]{2,12})", re.IGNORECASE
)
_AUTOFILL_MAINTENANCE_PATTERN = re.compile(
    r"\b(?:то|техобслуживание|техническое обслуживание|service|oil service|замена масла)\b",
    re.IGNORECASE,
)
_AUTOFILL_WAIT_HINTS = ("ожид", "в пути", "клиент дума", "согласован", "заказали", "ждем", "ждём")
_AUTOFILL_MAINTENANCE_SCOPE_HINTS = (
    "регламент",
    "замена масла",
    "oil service",
    "service",
    "масло",
    "фильтр",
    "свеч",
    "жидкост",
)
_AUTOFILL_PART_LOOKUP_STRONG_HINTS = (
    "артикул",
    "каталож",
    "oem",
    "подобрать",
    "подбор",
    "номер детали",
    "аналог",
    "цена",
    "проценить",
    "стоимость",
    "найти",
)
_BOARD_CONTROL_COUNTER_MAX = 1_000_000_000
_BOARD_CONTROL_TRACE_LIMIT = 24
_AUTOFILL_SYMPTOM_HINTS = (
    "теч",
    "бежит",
    "стук",
    "шум",
    "гул",
    "вибрац",
    "троит",
    "не завод",
    "перегрев",
    "дым",
    "пина",
    "дерга",
    "рывк",
    "скрип",
    "свист",
    "ошибк",
    "антифриз",
    "не едет",
)
_AUTOFILL_PART_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("радиатор", ("радиатор", "radiator")),
    ("рычаг подвески", ("рычаг", "control arm")),
    ("стойка амортизатора", ("стойк", "амортиз", "shock", "strut")),
    ("ступичный подшипник", ("ступиц", "ступич", "bearing", "hub")),
    ("тормозные колодки", ("колодк", "pads")),
    ("тормозной диск", ("тормозн", "brake disc", "rotor")),
    ("термостат", ("термостат", "thermostat")),
    ("помпа", ("помп", "water pump")),
    ("ремень", ("ремень", "belt")),
    ("цепь грм", ("цеп", "timing chain")),
    ("масло", ("масло", "oil")),
    ("фильтр", ("фильтр", "filter")),
    ("свечи зажигания", ("свеч", "spark")),
    ("аккумулятор", ("аккумулятор", "battery")),
)


def _json_safe_value(value: Any, *, depth: int = 8) -> Any:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item, depth=depth - 1)
            for key, item in value.items()
            if key is not None
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_dumps(payload: Any, *, indent: int | None = None, sort_keys: bool = False) -> str:
    return json.dumps(
        _json_safe_value(payload),
        ensure_ascii=False,
        indent=indent,
        sort_keys=sort_keys,
        allow_nan=False,
    )


class AgentRunner(AgentRunnerOutputMixin):
    def __init__(
        self,
        *,
        storage: AgentStorage,
        board_api: BoardApiClient,
        model_client: OpenAIJsonAgentClient,
        logger: logging.Logger,
        actor_name: str | None = None,
        max_steps: int | None = None,
        max_tool_result_chars: int | None = None,
    ) -> None:
        self._storage = storage
        self._board_api = board_api
        self._model_client = model_client
        self._logger = logger
        self._actor_name = actor_name or get_agent_name()
        self._max_steps = max_steps or get_agent_max_steps()
        self._max_tool_result_chars = max_tool_result_chars or get_agent_max_tool_result_chars()
        self._tools = AgentToolExecutor(board_api, actor_name=self._actor_name)
        self._policy = ToolPolicyEngine()
        self._vehicle_profile_service = VehicleProfileService()

    def run_once(self) -> bool:
        task = self._storage.claim_next_task()
        if task is None:
            self._storage.heartbeat(task_id=None, run_id=None)
            return False
        run_id = f"agrun_{uuid.uuid4().hex[:12]}"
        self._storage.update_status(
            running=True,
            current_task_id=task["id"],
            current_run_id=run_id,
            last_heartbeat=utc_now_iso(),
            last_run_started_at=utc_now_iso(),
            last_error="",
        )
        tool_calls = 0
        started_at = utc_now_iso()
        try:
            summary, result, display, tool_calls, orchestration = self._execute_task(
                task, run_id=run_id
            )
            completed = self._storage.complete_task(
                task_id=task["id"],
                run_id=run_id,
                summary=summary,
                result=result,
                display=display,
                tool_calls=tool_calls,
            )
            self._storage.append_run(
                {
                    "id": run_id,
                    "task_id": task["id"],
                    "status": "completed",
                    "started_at": started_at,
                    "finished_at": completed["finished_at"],
                    "source": task["source"],
                    "mode": task["mode"],
                    "task_text": task["task_text"],
                    "summary": summary,
                    "result": result,
                    "display": display,
                    "tool_calls": tool_calls,
                    "model": self._model_client.model,
                    "metadata": task.get("metadata", {}),
                    "orchestration": orchestration,
                }
            )
            self._update_board_control_runtime_after_task(
                task=task,
                orchestration=orchestration,
            )
            self._storage.update_status(
                running=False,
                current_task_id=None,
                current_run_id=None,
                last_heartbeat=utc_now_iso(),
                last_run_finished_at=completed["finished_at"],
                last_error="",
            )
            self._logger.info(
                "agent_task_completed task_id=%s run_id=%s tool_calls=%s",
                task["id"],
                run_id,
                tool_calls,
            )
            return True
        except Exception as exc:
            self._record_log_action(
                task_id=task["id"],
                run_id=run_id,
                step=tool_calls + 1,
                level="WARN",
                phase="failed",
                message=self._task_failed_message(task, exc),
            )
            failed = self._storage.fail_task(
                task_id=task["id"],
                run_id=run_id,
                error=str(exc),
                tool_calls=tool_calls,
            )
            self._storage.append_run(
                {
                    "id": run_id,
                    "task_id": task["id"],
                    "status": "failed",
                    "started_at": started_at,
                    "finished_at": failed["finished_at"],
                    "source": task["source"],
                    "mode": task["mode"],
                    "task_text": task["task_text"],
                    "summary": "",
                    "result": "",
                    "error": str(exc),
                    "tool_calls": tool_calls,
                    "model": self._model_client.model,
                    "metadata": task.get("metadata", {}),
                }
            )
            self._update_board_control_runtime_after_failure(task=task, error=str(exc))
            self._storage.update_status(
                running=False,
                current_task_id=None,
                current_run_id=None,
                last_heartbeat=utc_now_iso(),
                last_run_finished_at=failed["finished_at"],
                last_error=str(exc),
            )
            self._logger.exception(
                "agent_task_failed task_id=%s run_id=%s error=%s", task["id"], run_id, exc
            )
            return True

    def _execute_task(
        self, task: dict[str, Any], *, run_id: str
    ) -> tuple[str, str, dict[str, Any], int, dict[str, Any]]:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        self._tools.reset_task_budget()
        task_type = self._classify_task(task, metadata)
        context_kind = self._context_kind(metadata)
        return self._execute_orchestrated_task(
            task,
            run_id=run_id,
            metadata=metadata,
            task_type=task_type,
            context_kind=context_kind,
        )

    def _execute_orchestrated_task(
        self,
        task: dict[str, Any],
        *,
        run_id: str,
        metadata: dict[str, Any],
        task_type: str,
        context_kind: str,
    ) -> tuple[str, str, dict[str, Any], int, dict[str, Any]]:
        task_id = str(task.get("id", "") or "").strip()
        tool_calls = 0
        context_payload: dict[str, Any] = {}
        context_data: dict[str, Any] = {}
        context_snapshot_id = f"ctx:{task_id}:board"
        self._record_log_action(
            task_id=task_id,
            run_id=run_id,
            step=0,
            level="RUN",
            phase="start",
            message=self._task_started_message(metadata),
        )
        self._record_log_action(
            task_id=task_id,
            run_id=run_id,
            step=0,
            level="INFO",
            phase="analysis",
            message=self._task_analysis_message(metadata),
        )
        if self._should_preload_context(
            task_type=task_type, metadata=metadata, context_kind=context_kind
        ):
            card_id = (
                self._cleanup_card_id(metadata) or str(metadata.get("card_id", "") or "").strip()
            )
            context_args = {
                "card_id": card_id,
                "event_limit": 20,
                "include_repair_order_text": True,
            }
            context_tool_name, context_payload = self._load_card_autofill_context(
                card_id=card_id, context_args=context_args
            )
            context_data = self._response_data(context_payload)
            context_snapshot_id = self._build_context_snapshot_id(
                task_id=task_id, card_id=card_id, context_tool_name=context_tool_name
            )
            tool_calls += 1
            self._record_action(
                task_id=task_id,
                run_id=run_id,
                step=tool_calls,
                tool_name=context_tool_name,
                args=context_args
                if context_tool_name == "get_card_context"
                else {"card_id": card_id},
                reason="Read focused context before evidence extraction and planning",
                result_payload=context_payload,
            )
        evidence_result, facts = self._build_orchestration_evidence(
            task=task,
            metadata=metadata,
            task_type=task_type,
            context_kind=context_kind,
            context_data=context_data,
            raw_context_ref=context_snapshot_id,
        )
        plan = self._build_orchestration_plan(
            metadata=metadata,
            task_type=task_type,
            context_kind=context_kind,
            evidence=evidence_result,
            facts=facts,
        )
        # Keep the trace field for existing consumers, but scenario hints never select
        # a separate executor. Every task uses the same autonomous model loop.
        scenario_feedback: list[dict[str, Any]] = []
        summary, result, display, delta, tool_results, patch_result, verify_result = (
            self._execute_decision_loop_task(
                task,
                run_id=run_id,
                metadata=metadata,
                task_type=task_type,
                context_kind=context_kind,
                evidence=evidence_result,
                plan=plan,
                preloaded_context=context_payload,
            )
        )
        tool_calls += delta
        evidence_result = self._enrich_evidence_with_runtime_facts(evidence_result, facts=facts)
        trace = OrchestrationTrace(
            version="agent_orchestrator_v1",
            trigger={
                "task_id": task_id,
                "source": str(task.get("source", "") or "").strip(),
                "mode": str(task.get("mode", "") or "").strip(),
                "purpose": str(metadata.get("purpose", "") or "").strip(),
                "task_type": task_type,
                "requested_by": str(metadata.get("requested_by", "") or "").strip(),
            },
            context_snapshot_id=context_snapshot_id,
            evidence=evidence_result,
            plan=plan,
            scenario_feedback=scenario_feedback,
            tool_results=tool_results,
            patch=patch_result,
            verify=verify_result,
        )
        return summary, result, display, tool_calls, trace.to_dict()

    def _should_preload_context(
        self, *, task_type: str, metadata: dict[str, Any], context_kind: str
    ) -> bool:
        del task_type
        return context_kind == "card" and bool(self._cleanup_card_id(metadata))

    def _build_context_snapshot_id(
        self, *, task_id: str, card_id: str, context_tool_name: str
    ) -> str:
        normalized_card_id = str(card_id or "").strip() or "board"
        return f"ctx:{task_id}:{normalized_card_id}:{context_tool_name}"

    def _build_orchestration_evidence(
        self,
        *,
        task: dict[str, Any],
        metadata: dict[str, Any],
        task_type: str,
        context_kind: str,
        context_data: dict[str, Any],
        raw_context_ref: str,
    ) -> tuple[EvidenceResult, dict[str, Any]]:
        allowed_write_targets = self._suggest_allowed_write_targets(
            task_type=task_type, context_kind=context_kind, metadata=metadata
        )
        if context_kind == "card" and context_data:
            if task_type == "full_card_enrichment":
                facts = self._analyze_card_completion_context(
                    context_data, task_text=str(task.get("task_text", "") or "")
                )
                facts["task_type"] = task_type
                facts["context_kind"] = context_kind
                confirmed_facts = {
                    "card_title": str(facts.get("card_title", "") or "").strip(),
                    "vehicle": str(facts.get("vehicle", "") or "").strip(),
                    "has_vehicle_profile": bool(facts.get("has_vehicle_profile")),
                    "has_repair_order": bool(facts.get("has_repair_order")),
                    "missing_vehicle_fields": list(facts.get("missing_vehicle_fields") or [])[:8],
                    "missing_repair_order_fields": list(
                        facts.get("missing_repair_order_fields") or []
                    )[:8],
                }
                summary_bits = [
                    f"task_type={task_type}",
                    f"vehicle={'yes' if confirmed_facts['vehicle'] else 'no'}",
                    f"vehicle_profile={'yes' if confirmed_facts['has_vehicle_profile'] else 'no'}",
                    f"repair_order={'yes' if confirmed_facts['has_repair_order'] else 'no'}",
                ]
                evidence_result = EvidenceResult(
                    context_kind=context_kind,
                    card_id=self._cleanup_card_id(metadata),
                    confirmed_facts=confirmed_facts,
                    fact_evidence=self._build_card_completion_fact_evidence(
                        facts, confirmed_facts=confirmed_facts
                    ),
                    missing_data=list(facts.get("missing_vehicle_fields") or [])[:4]
                    + list(facts.get("missing_repair_order_fields") or [])[:4],
                    scenario_signals={},
                    sensitive_fields=[
                        "prices",
                        "part_numbers",
                        "customer_notes",
                        "manual_vehicle_fields",
                    ],
                    allowed_write_targets=allowed_write_targets,
                    summary=", ".join(summary_bits),
                    raw_context_ref=raw_context_ref,
                )
                return evidence_result, facts
            facts = self._analyze_card_autofill_context(
                context_data, task_text=str(task.get("task_text", "") or "")
            )
            facts["task_type"] = task_type
            facts["context_kind"] = context_kind
            autofill_plan = self._build_card_autofill_plan(facts)
            facts["autofill_plan"] = autofill_plan
            facts["selected_scenarios"] = autofill_plan.get("scenarios", [])
            confirmed_facts = {
                "vin": str(facts.get("vin", "") or "").strip(),
                "mileage": str(facts.get("mileage", "") or "").strip(),
                "dtc_codes": list(facts.get("dtc_codes") or [])[:3],
                "part_queries": list(facts.get("part_queries") or [])[:3],
                "waiting_state": bool(facts.get("waiting_state")),
                "vehicle_context": dict(facts.get("vehicle_context") or {}),
            }
            summary_bits = [
                f"task_type={task_type}",
                f"vin={'yes' if confirmed_facts['vin'] else 'no'}",
            ]
            evidence_result = EvidenceResult(
                context_kind=context_kind,
                card_id=self._cleanup_card_id(metadata),
                confirmed_facts=confirmed_facts,
                fact_evidence=self._build_card_fact_evidence(
                    facts, confirmed_facts=confirmed_facts
                ),
                missing_data=list(facts.get("missing_vehicle_fields") or []),
                scenario_signals=dict(facts.get("scenario_evidence") or {}),
                sensitive_fields=[
                    "prices",
                    "part_numbers",
                    "customer_notes",
                    "manual_vehicle_fields",
                ],
                allowed_write_targets=allowed_write_targets,
                summary=", ".join(summary_bits),
                raw_context_ref=raw_context_ref,
            )
            return evidence_result, facts
        generic_facts = {"task_type": task_type, "context_kind": context_kind}
        evidence_result = EvidenceResult(
            context_kind=context_kind or "board",
            confirmed_facts={
                "task_type": task_type,
                "mode": str(task.get("mode", "") or "").strip(),
            },
            fact_evidence=self._build_generic_fact_evidence(
                task_type=task_type, context_kind=context_kind, task=task
            ),
            missing_data=[],
            scenario_signals={},
            sensitive_fields=["cash_amounts", "manual_notes"],
            allowed_write_targets=allowed_write_targets,
            summary=f"task_type={task_type}, context={context_kind or 'board'}",
            raw_context_ref=raw_context_ref,
        )
        return evidence_result, generic_facts

    def _build_card_fact_evidence(
        self,
        facts: dict[str, Any],
        *,
        confirmed_facts: dict[str, Any],
    ) -> dict[str, FactEvidence]:
        vehicle_context = (
            confirmed_facts.get("vehicle_context")
            if isinstance(confirmed_facts.get("vehicle_context"), dict)
            else {}
        )
        missing_vehicle_fields = list(facts.get("missing_vehicle_fields") or [])
        evidence_model = (
            facts.get("evidence_model") if isinstance(facts.get("evidence_model"), dict) else {}
        )
        part_queries = list(confirmed_facts.get("part_queries") or [])
        return {
            "vin": FactEvidence(
                name="vin",
                value=confirmed_facts.get("vin", ""),
                status="confirmed" if confirmed_facts.get("vin") else "absent",
                source="card_context",
                confidence=1.0 if confirmed_facts.get("vin") else 0.0,
            ),
            "mileage": FactEvidence(
                name="mileage",
                value=confirmed_facts.get("mileage", ""),
                status="confirmed" if confirmed_facts.get("mileage") else "absent",
                source="vehicle_profile_or_repair_order",
                confidence=0.9 if confirmed_facts.get("mileage") else 0.0,
            ),
            "dtc_codes": FactEvidence(
                name="dtc_codes",
                value=list(confirmed_facts.get("dtc_codes") or []),
                status="confirmed" if confirmed_facts.get("dtc_codes") else "absent",
                source="card_context",
                confidence=0.95 if confirmed_facts.get("dtc_codes") else 0.0,
            ),
            "part_queries": FactEvidence(
                name="part_queries",
                value=part_queries,
                status="inferred" if part_queries else "absent",
                source="heuristic_text_extraction",
                confidence=0.7 if part_queries else 0.0,
                notes=["Derived from symptom and card text analysis."] if part_queries else [],
            ),
            "waiting_state": FactEvidence(
                name="waiting_state",
                value=bool(confirmed_facts.get("waiting_state")),
                status="weak_signal" if confirmed_facts.get("waiting_state") else "absent",
                source="heuristic_text_extraction",
                confidence=0.6 if confirmed_facts.get("waiting_state") else 0.0,
            ),
            "vehicle_context": FactEvidence(
                name="vehicle_context",
                value=dict(vehicle_context),
                status="confirmed" if vehicle_context else "absent",
                source="card_context_aggregate",
                confidence=0.85 if vehicle_context else 0.0,
                conflicts=["missing:" + field_name for field_name in missing_vehicle_fields[:4]],
            ),
            "external_result_sufficient": FactEvidence(
                name="external_result_sufficient",
                value=bool(evidence_model.get("external_result_sufficient")),
                status="confirmed"
                if evidence_model.get("external_result_sufficient")
                else "absent",
                source="external_tool_results",
                confidence=1.0 if evidence_model.get("external_result_sufficient") else 0.0,
            ),
        }

    def _build_card_completion_fact_evidence(
        self,
        facts: dict[str, Any],
        *,
        confirmed_facts: dict[str, Any],
    ) -> dict[str, FactEvidence]:
        vehicle_profile = (
            facts.get("vehicle_profile") if isinstance(facts.get("vehicle_profile"), dict) else {}
        )
        repair_order = (
            facts.get("repair_order") if isinstance(facts.get("repair_order"), dict) else {}
        )
        return {
            "card_title": FactEvidence(
                name="card_title",
                value=confirmed_facts.get("card_title", ""),
                status="confirmed" if confirmed_facts.get("card_title") else "absent",
                source="card_context",
                confidence=1.0 if confirmed_facts.get("card_title") else 0.0,
            ),
            "vehicle": FactEvidence(
                name="vehicle",
                value=confirmed_facts.get("vehicle", ""),
                status="confirmed" if confirmed_facts.get("vehicle") else "absent",
                source="card_context",
                confidence=1.0 if confirmed_facts.get("vehicle") else 0.0,
            ),
            "vehicle_profile": FactEvidence(
                name="vehicle_profile",
                value=dict(vehicle_profile),
                status="confirmed" if vehicle_profile else "absent",
                source="card_context",
                confidence=0.95 if vehicle_profile else 0.0,
                notes=["missing: " + ", ".join(confirmed_facts.get("missing_vehicle_fields") or [])]
                if confirmed_facts.get("missing_vehicle_fields")
                else [],
            ),
            "repair_order": FactEvidence(
                name="repair_order",
                value=dict(repair_order),
                status="confirmed" if repair_order else "absent",
                source="card_context",
                confidence=0.9 if repair_order else 0.0,
                notes=[
                    "missing: "
                    + ", ".join(confirmed_facts.get("missing_repair_order_fields") or [])
                ]
                if confirmed_facts.get("missing_repair_order_fields")
                else [],
            ),
        }

    def _build_generic_fact_evidence(
        self,
        *,
        task_type: str,
        context_kind: str,
        task: dict[str, Any],
    ) -> dict[str, FactEvidence]:
        return {
            "task_type": FactEvidence(
                name="task_type",
                value=task_type,
                status="confirmed",
                source="task_metadata",
                confidence=1.0,
            ),
            "mode": FactEvidence(
                name="mode",
                value=str(task.get("mode", "") or "").strip(),
                status="confirmed" if str(task.get("mode", "") or "").strip() else "absent",
                source="task_metadata",
                confidence=1.0 if str(task.get("mode", "") or "").strip() else 0.0,
            ),
            "context_kind": FactEvidence(
                name="context_kind",
                value=context_kind or "board",
                status="confirmed",
                source="task_metadata",
                confidence=1.0,
            ),
        }

    def _enrich_evidence_with_runtime_facts(
        self, evidence: EvidenceResult, *, facts: dict[str, Any]
    ) -> EvidenceResult:
        fact_evidence = dict(evidence.fact_evidence)
        related_cards = (
            facts.get("related_cards") if isinstance(facts.get("related_cards"), list) else []
        )
        if related_cards:
            fact_evidence["related_cards"] = FactEvidence(
                name="related_cards",
                value=[
                    str(item.get("id", "") or "").strip()
                    for item in related_cards[:6]
                    if isinstance(item, dict)
                ],
                status="inferred",
                source="board_search",
                confidence=min(0.85, 0.45 + 0.05 * len(related_cards)),
                notes=[
                    f"Found {len(related_cards)} related cards during runtime context expansion."
                ],
            )
        vin_status = str(facts.get("vin_decode_status", "") or "").strip().lower()
        if vin_status in {"insufficient", "failed"} and isinstance(
            facts.get("vehicle_context"), dict
        ):
            fact_evidence["vin_fallback_context"] = FactEvidence(
                name="vin_fallback_context",
                value=dict(facts.get("vehicle_context") or {}),
                status="inferred"
                if self._has_enough_vehicle_context(
                    dict(facts.get("vehicle_context") or {}),
                    missing_vehicle_fields=list(facts.get("missing_vehicle_fields") or []),
                )
                else "weak_signal",
                source="card_context_fallback",
                confidence=0.55 if vin_status == "insufficient" else 0.35,
                notes=["Used because VIN decoding did not return enough confirmed vehicle facts."],
            )
        if fact_evidence == evidence.fact_evidence:
            return evidence
        return EvidenceResult(
            context_kind=evidence.context_kind,
            card_id=evidence.card_id,
            confirmed_facts=dict(evidence.confirmed_facts),
            fact_evidence=fact_evidence,
            missing_data=list(evidence.missing_data),
            scenario_signals=dict(evidence.scenario_signals),
            sensitive_fields=list(evidence.sensitive_fields),
            allowed_write_targets=list(evidence.allowed_write_targets),
            summary=evidence.summary,
            raw_context_ref=evidence.raw_context_ref,
        )

    def _build_orchestration_plan(
        self,
        *,
        metadata: dict[str, Any],
        task_type: str,
        context_kind: str,
        evidence: EvidenceResult,
        facts: dict[str, Any],
    ) -> PlanResult:
        scenario_chain = self._scenario_chain_for_task(
            metadata=metadata,
            task_type=task_type,
            context_kind=context_kind,
            facts=facts,
        )
        purpose = str(metadata.get("purpose", "") or "").strip().lower()
        notes: list[str] = []
        if evidence.missing_data:
            notes.append("missing_data:" + ", ".join(evidence.missing_data[:4]))
        if purpose:
            notes.append(f"purpose_hint={purpose}")
        notes.append("scenario_hints=advisory")
        return self._policy.build_plan(
            scenario_chain=scenario_chain,
            execution_mode="model_loop",
            followup_enabled=bool(purpose == "card_autofill"),
            notes=notes,
        )

    def _scenario_chain_for_task(
        self,
        *,
        metadata: dict[str, Any],
        task_type: str,
        context_kind: str,
        facts: dict[str, Any],
    ) -> list[str]:
        purpose = str(metadata.get("purpose", "") or "").strip().lower()
        if purpose == "full_card_enrichment" or task_type == "full_card_enrichment":
            return ["full_card_enrichment"]
        autofill_plan = (
            facts.get("autofill_plan") if isinstance(facts.get("autofill_plan"), dict) else {}
        )
        autofill_scenarios = [
            str(item.get("name", "") or "").strip().lower()
            for item in (
                autofill_plan.get("scenarios")
                if isinstance(autofill_plan.get("scenarios"), list)
                else []
            )
            if isinstance(item, dict) and str(item.get("name", "") or "").strip()
        ]
        if purpose == "card_autofill":
            return autofill_scenarios or ["freeform_manual"]
        if purpose == "board_control":
            if autofill_scenarios:
                return autofill_scenarios
            return (
                ["vin_enrichment"] if str(facts.get("vin", "") or "").strip() else ["normalization"]
            )
        if task_type == "board_review":
            return ["board_review"]
        if task_type == "cash_review":
            return ["cash_review"]
        if task_type == "repair_order_assist":
            return ["repair_order_assistance"]
        if context_kind == "card":
            return self._scenario_chain_for_card_context(task_type, autofill_scenarios)
        return ["freeform_manual"]

    def _scenario_chain_for_card_context(
        self, task_type: str, autofill_scenarios: list[str]
    ) -> list[str]:
        if task_type == "vin_decode":
            return [item for item in autofill_scenarios if item == "vin_enrichment"] or [
                "vin_enrichment"
            ]
        if task_type == "parts_lookup":
            return [
                item
                for item in autofill_scenarios
                if item in {"vin_enrichment", "parts_lookup", "normalization"}
            ] or ["parts_lookup", "normalization"]
        if task_type == "maintenance_estimate":
            return [
                item
                for item in autofill_scenarios
                if item in {"vin_enrichment", "maintenance_lookup", "normalization"}
            ] or ["maintenance_lookup", "normalization"]
        if task_type == "dtc_lookup":
            return [
                item
                for item in autofill_scenarios
                if item in {"dtc_lookup", "fault_research", "normalization"}
            ] or ["dtc_lookup", "normalization"]
        if task_type == "card_cleanup":
            return autofill_scenarios or ["normalization"]
        return ["freeform_manual"]

    def _suggest_allowed_write_targets(
        self, *, task_type: str, context_kind: str, metadata: dict[str, Any] | None = None
    ) -> list[str]:
        purpose = str((metadata or {}).get("purpose", "") or "").strip().lower()
        if context_kind == "card":
            if purpose == "board_control":
                return ["description", "vehicle", "vehicle_profile"]
            if purpose == "full_card_enrichment" or task_type == "full_card_enrichment":
                return [
                    "title",
                    "description",
                    "tags",
                    "vehicle",
                    "vehicle_profile",
                    "repair_order",
                    "repair_order_works",
                    "repair_order_materials",
                ]
            if task_type == "repair_order_assist":
                return [
                    "description",
                    "repair_order",
                    "repair_order_works",
                    "repair_order_materials",
                ]
            return ["title", "description", "tags", "vehicle", "vehicle_profile"]
        return []

    def _build_decision_loop_system_prompt(
        self,
        *,
        task_type: str,
        context_kind: str,
        plan: PlanResult,
        evidence: EvidenceResult,
    ) -> str:
        prompt_override = self._storage.read_prompt_text().strip()
        memory_text = self._storage.read_memory_text().strip()
        system_prompt = prompt_override or DEFAULT_SYSTEM_PROMPT
        if memory_text:
            system_prompt = f"{system_prompt}\n\nPersistent memory:\n{memory_text}"
        system_prompt = (
            f"{system_prompt}\n\nAvailable tools:\n"
            f"{self._tools.describe_for_prompt(task_type=task_type, context_kind=context_kind)}"
        )
        return f"{system_prompt}\n\n{self._contract_prompt_block(plan=plan, evidence=evidence)}"

    def _handle_decision_loop_tool_call(
        self,
        *,
        task: dict[str, Any],
        run_id: str,
        step: int,
        tool_name: str,
        args: dict[str, Any],
        reason: str,
        plan: PlanResult,
        evidence: EvidenceResult,
        cleanup_task: bool,
        cleanup_card_id: str,
        patch_result: PatchResult,
        verify_result: VerifyResult,
        applied_updates: list[str],
        tool_results: list[ToolResult],
        messages: list[dict[str, str]],
        cleanup_update_applied: bool,
    ) -> tuple[dict[str, Any], dict[str, Any], PatchResult, VerifyResult, bool]:
        if tool_name in {
            "update_card",
            "update_repair_order",
            "replace_repair_order_works",
            "replace_repair_order_materials",
        }:
            args, result_payload, current_patch, verify_result = self._execute_write_tool(
                tool_name=tool_name,
                args=args,
                plan=plan,
                cleanup_card_id=cleanup_card_id,
            )
            patch_result = self._merge_patch_results(patch_result, current_patch)
        else:
            result_payload = self._tools.execute(tool_name, args)
        if (
            cleanup_task
            and tool_name == "update_card"
            and str(args.get("card_id", "") or "").strip() == cleanup_card_id
        ):
            cleanup_update_applied = True
            applied_updates.extend(self._summarize_applied_update(args, result_payload))
        tool_results.append(
            self._build_tool_result(
                tool_name,
                result_payload,
                status="success",
                reason=reason,
                scenario_id=plan.scenario_id,
                evidence_ref=evidence.raw_context_ref,
            )
        )
        self._record_action(
            task_id=task["id"],
            run_id=run_id,
            step=step,
            tool_name=tool_name,
            args=args,
            reason=reason,
            result_payload=result_payload,
        )
        messages.append(
            {
                "role": "assistant",
                "content": _json_dumps(
                    {"type": "tool", "tool": tool_name, "args": args, "reason": reason},
                ),
            }
        )
        messages.append(
            {
                "role": "user",
                "content": f"TOOL RESULT {tool_name}:\n{self._tool_result_for_model(tool_name, result_payload)}",
            }
        )
        return args, result_payload, patch_result, verify_result, cleanup_update_applied

    def _execute_decision_loop_task(
        self,
        task: dict[str, Any],
        *,
        run_id: str,
        metadata: dict[str, Any],
        task_type: str,
        context_kind: str,
        evidence: EvidenceResult,
        plan: PlanResult,
        preloaded_context: dict[str, Any] | None = None,
    ) -> tuple[str, str, dict[str, Any], int, list[ToolResult], PatchResult, VerifyResult]:
        system_prompt = self._build_decision_loop_system_prompt(
            task_type=task_type,
            context_kind=context_kind,
            plan=plan,
            evidence=evidence,
        )
        cleanup_task = task_type == "card_cleanup"
        cleanup_card_id = self._cleanup_card_id(metadata)
        cleanup_update_applied = False
        applied_updates: list[str] = []
        tool_results: list[ToolResult] = []
        patch_result = PatchResult()
        verify_result = VerifyResult(applied_ok=False)
        messages: list[dict[str, str]] = [
            {
                "role": "user",
                "content": self._build_user_task_message(
                    task,
                    metadata,
                    task_type=task_type,
                    preloaded_context=preloaded_context,
                ),
            }
        ]
        if preloaded_context:
            messages.append(
                {
                    "role": "user",
                    "content": f"READ CONTEXT SNAPSHOT:\n{self._tool_result_for_model('get_card_context', preloaded_context)}",
                }
            )
        tool_calls = 0
        for step in range(1, self._max_steps + 1):
            self._storage.heartbeat(task_id=task["id"], run_id=run_id)
            decision = self._model_client.next_step(system_prompt=system_prompt, messages=messages)
            decision_type = str(decision.get("type", "") or "").strip().lower()
            if decision_type == "final":
                apply_args = self._extract_card_update_apply(
                    decision, cleanup_card_id=cleanup_card_id
                )
                if apply_args is not None:
                    tool_calls += 1
                    apply_args, apply_result, current_patch, verify_result = (
                        self._execute_write_tool(
                            tool_name="update_card",
                            args=apply_args,
                            plan=plan,
                            cleanup_card_id=cleanup_card_id,
                        )
                    )
                    patch_result = self._merge_patch_results(patch_result, current_patch)
                    cleanup_update_applied = True
                    applied_updates.extend(self._summarize_applied_update(apply_args, apply_result))
                    tool_results.append(
                        self._build_tool_result(
                            "update_card",
                            apply_result,
                            status="success",
                            reason="Runner applied structured card update from final response",
                            scenario_id=plan.scenario_id,
                            evidence_ref=evidence.raw_context_ref,
                        )
                    )
                    self._record_action(
                        task_id=task["id"],
                        run_id=run_id,
                        step=step,
                        tool_name="update_card",
                        args=apply_args,
                        reason="Runner applied structured card update from final response",
                        result_payload=apply_result,
                    )
                summary = str(decision.get("summary", "") or "").strip() or "Task completed."
                result = str(decision.get("result", "") or "").strip() or summary
                display = self._normalize_display_payload(decision, summary=summary, result=result)
                display = self._append_applied_updates(display, applied_updates)
                self._record_log_action(
                    task_id=task["id"],
                    run_id=run_id,
                    step=step,
                    level="DONE",
                    phase="completed",
                    message=self._task_completed_message(
                        metadata, summary=summary, applied_updates=applied_updates
                    ),
                )
                verify_result = self._finalize_verify_result(
                    plan=plan, verify=verify_result, tool_results=tool_results
                )
                return (
                    summary,
                    result,
                    display,
                    tool_calls,
                    tool_results,
                    patch_result,
                    verify_result,
                )
            if decision_type != "tool":
                raise AgentModelError(
                    "Agent model returned neither a tool call nor a final answer."
                )
            tool_name = str(decision.get("tool", "") or "").strip()
            args = decision.get("args")
            if not isinstance(args, dict):
                args = {}
            reason = str(decision.get("reason", "") or "").strip()
            tool_calls += 1
            args, result_payload, patch_result, verify_result, cleanup_update_applied = (
                self._handle_decision_loop_tool_call(
                    task=task,
                    run_id=run_id,
                    step=step,
                    tool_name=tool_name,
                    args=args,
                    reason=reason,
                    plan=plan,
                    evidence=evidence,
                    cleanup_task=cleanup_task,
                    cleanup_card_id=cleanup_card_id,
                    patch_result=patch_result,
                    verify_result=verify_result,
                    applied_updates=applied_updates,
                    tool_results=tool_results,
                    messages=messages,
                    cleanup_update_applied=cleanup_update_applied,
                )
            )
        raise AgentModelError(
            f"Agent exceeded max steps ({self._max_steps}) without returning a final answer."
        )

    def _contract_prompt_block(self, *, plan: PlanResult, evidence: EvidenceResult) -> str:
        lines = [
            "Decision context (hints, not a checklist):",
            f"- intent hints: {', '.join(plan.scenario_chain) if plan.scenario_chain else 'none'}",
            f"- specialist tools that may help: {', '.join(plan.optional_tools) if plan.optional_tools else 'none'}",
            f"- current evidence: {evidence.summary or 'n/a'}",
            "- Start from facts already supplied. Choose only the narrowest useful read, research, or action.",
            "- Do not repeat known vehicle, customer, or part data. Ask a natural question only when one precise fact blocks progress.",
            "- A useful final answer is allowed without a tool call or a write.",
            "- Preserve manual facts. Before a real-impact action, use its native confirmation and reread requirement.",
        ]
        if evidence.missing_data:
            lines.append("- possibly missing: " + ", ".join(evidence.missing_data[:5]))
        return "\n".join(lines)

    def _execute_write_tool(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        plan: PlanResult,
        cleanup_card_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], PatchResult, VerifyResult]:
        normalized_tool = str(tool_name or "").strip()
        if normalized_tool == "update_card":
            return self._execute_card_update(args=args, plan=plan, cleanup_card_id=cleanup_card_id)
        if normalized_tool == "update_repair_order":
            return self._execute_repair_order_update(
                args=args, plan=plan, cleanup_card_id=cleanup_card_id
            )
        if normalized_tool in {"replace_repair_order_works", "replace_repair_order_materials"}:
            return self._execute_repair_order_rows(
                tool_name=normalized_tool, args=args, plan=plan, cleanup_card_id=cleanup_card_id
            )
        result_payload = self._tools.execute(normalized_tool, args)
        return args, result_payload, PatchResult(), VerifyResult(applied_ok=False)

    def _execute_card_update(
        self, *, args: dict[str, Any], plan: PlanResult, cleanup_card_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], PatchResult, VerifyResult]:
        card_id = str(args.get("card_id", "") or cleanup_card_id or "").strip()
        if not card_id:
            raise AgentModelError("update_card requires card_id.")
        patch = PatchResult(
            card_patch={
                key: value
                for key, value in args.items()
                if key in {"title", "description", "tags", "vehicle", "vehicle_profile"}
            }
        )
        filtered_patch = self._policy.filter_patch(plan, patch)
        if not filtered_patch.card_patch:
            raise AgentModelError("Card update needs at least one supported field.")
        write_args = {"card_id": card_id, **filtered_patch.card_patch}
        for key in ("expected_updated_at", "response_mode"):
            if key in args:
                write_args[key] = args[key]
        result_payload = self._tools.execute("update_card", write_args)
        return (
            write_args,
            result_payload,
            filtered_patch,
            VerifyResult(
                applied_ok=True,
                fields_changed=sorted(filtered_patch.card_patch),
                manual_fields_preserved=True,
                scenario_completed=True,
                outcome_state="write_applied",
                context_ref=f"write:{card_id}",
            ),
        )

    def _execute_repair_order_update(
        self, *, args: dict[str, Any], plan: PlanResult, cleanup_card_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], PatchResult, VerifyResult]:
        card_id = str(args.get("card_id", "") or cleanup_card_id or "").strip()
        if not card_id:
            raise AgentModelError("update_repair_order requires card_id.")
        patch = PatchResult(repair_order_patch=dict(args.get("repair_order") or {}))
        filtered_patch = self._policy.filter_patch(plan, patch)
        if not filtered_patch.repair_order_patch:
            raise AgentModelError(
                "Contract policy rejected repair order write outside allowed targets."
            )
        before_state = self._read_verification_state(card_id)
        write_args = {"card_id": card_id, "repair_order": filtered_patch.repair_order_patch}
        if "confirmation" in args:
            write_args["confirmation"] = args["confirmation"]
        result_payload = self._tools.execute("update_repair_order", write_args)
        verify = self._verify_repair_order_write(
            tool_name="update_repair_order",
            card_id=card_id,
            before_state=before_state,
            patch=filtered_patch,
        )
        return write_args, result_payload, filtered_patch, verify

    def _execute_repair_order_rows(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        plan: PlanResult,
        cleanup_card_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], PatchResult, VerifyResult]:
        card_id = str(args.get("card_id", "") or cleanup_card_id or "").strip()
        if not card_id:
            raise AgentModelError(f"{tool_name} requires card_id.")
        rows = [
            dict(item)
            for item in (args.get("rows") if isinstance(args.get("rows"), list) else [])
            if isinstance(item, dict)
        ]
        patch = PatchResult(
            repair_order_works=rows if tool_name == "replace_repair_order_works" else [],
            repair_order_materials=rows if tool_name == "replace_repair_order_materials" else [],
        )
        filtered_patch = self._policy.filter_patch(plan, patch)
        expected_rows = (
            filtered_patch.repair_order_works
            if tool_name == "replace_repair_order_works"
            else filtered_patch.repair_order_materials
        )
        if not expected_rows:
            raise AgentModelError(
                "Contract policy rejected repair order rows write outside allowed targets."
            )
        before_state = self._read_verification_state(card_id)
        write_args = {"card_id": card_id, "rows": expected_rows}
        if "confirmation" in args:
            write_args["confirmation"] = args["confirmation"]
        result_payload = self._tools.execute(tool_name, write_args)
        verify = self._verify_repair_order_write(
            tool_name=tool_name,
            card_id=card_id,
            before_state=before_state,
            patch=filtered_patch,
        )
        return write_args, result_payload, filtered_patch, verify

    def _read_verification_state(self, card_id: str) -> dict[str, Any]:
        state: dict[str, Any] = {}
        try:
            context_payload = self._board_api.get_card_context(
                card_id, event_limit=5, include_repair_order_text=True
            )
            state = self._response_data(context_payload)
        except Exception:
            state = {}
        try:
            card_payload = self._board_api.get_card(card_id)
            card_state = self._response_data(card_payload)
        except Exception:
            card_state = {}
        if "card" not in state:
            state = {"card": state} if isinstance(state, dict) else {}
        if isinstance(card_state, dict):
            incoming_card = (
                card_state.get("card") if isinstance(card_state.get("card"), dict) else card_state
            )
            current_card = state.get("card") if isinstance(state.get("card"), dict) else {}
            if isinstance(incoming_card, dict):
                merged_card = dict(current_card)
                merged_card.update(incoming_card)
                state["card"] = merged_card
        if "card" not in state:
            state = {"card": state} if isinstance(state, dict) else {}
        card = state.get("card") if isinstance(state.get("card"), dict) else {}
        if (
            "repair_order" not in state
            and isinstance(card, dict)
            and isinstance(card.get("repair_order"), dict)
        ):
            state["repair_order"] = dict(card.get("repair_order") or {})
        return state

    def _verify_repair_order_update(
        self,
        *,
        after_repair_order: dict[str, Any],
        patch: PatchResult,
    ) -> tuple[list[str], list[str], bool, int]:
        warnings: list[str] = []
        fields_changed: list[str] = []
        expected_targets = len(patch.repair_order_patch)
        for field_name, expected_value in patch.repair_order_patch.items():
            if self._values_equal(after_repair_order.get(field_name), expected_value):
                fields_changed.append(field_name)
            else:
                warnings.append(f"repair_order.{field_name} verification mismatch")
        scenario_completed = len(fields_changed) == expected_targets
        return fields_changed, warnings, scenario_completed, expected_targets

    def _verify_repair_order_rows(
        self,
        *,
        tool_name: str,
        after_repair_order: dict[str, Any],
        patch: PatchResult,
    ) -> tuple[list[str], list[str], bool, int]:
        warnings: list[str] = []
        fields_changed: list[str] = []
        expected_rows = (
            patch.repair_order_works
            if tool_name == "replace_repair_order_works"
            else patch.repair_order_materials
        )
        expected_targets = 1 if expected_rows else 0
        actual_rows = after_repair_order.get(
            "works" if tool_name == "replace_repair_order_works" else "materials"
        )
        if isinstance(actual_rows, list) and len(actual_rows) == len(expected_rows):
            fields_changed.append(
                "repair_order_works"
                if tool_name == "replace_repair_order_works"
                else "repair_order_materials"
            )
        else:
            warnings.append(f"{tool_name} verification mismatch")
        scenario_completed = len(fields_changed) == expected_targets
        return fields_changed, warnings, scenario_completed, expected_targets

    def _verify_repair_order_write(
        self,
        *,
        tool_name: str,
        card_id: str,
        before_state: dict[str, Any],
        patch: PatchResult,
    ) -> VerifyResult:
        after_state = self._read_verification_state(card_id)
        warnings: list[str] = []
        fields_changed: list[str] = []
        manual_fields_preserved = True
        scenario_completed = False
        expected_targets = 0
        before_card = before_state.get("card") if isinstance(before_state.get("card"), dict) else {}
        after_card = after_state.get("card") if isinstance(after_state.get("card"), dict) else {}
        after_repair_order = (
            after_state.get("repair_order")
            if isinstance(after_state.get("repair_order"), dict)
            else {}
        )
        if tool_name == "update_repair_order":
            fields_changed, warnings, scenario_completed, expected_targets = (
                self._verify_repair_order_update(after_repair_order=after_repair_order, patch=patch)
            )
        elif tool_name in {"replace_repair_order_works", "replace_repair_order_materials"}:
            fields_changed, warnings, scenario_completed, expected_targets = (
                self._verify_repair_order_rows(
                    tool_name=tool_name,
                    after_repair_order=after_repair_order,
                    patch=patch,
                )
            )
        else:
            scenario_completed = False
        non_target_card_fields = {"title", "description", "tags", "vehicle"} - set(patch.card_patch)
        for field_name in non_target_card_fields:
            if field_name and not self._values_equal(
                before_card.get(field_name), after_card.get(field_name)
            ):
                manual_fields_preserved = False
                warnings.append(f"{field_name} changed outside planned patch")
        applied_ok = scenario_completed
        return VerifyResult(
            applied_ok=applied_ok,
            fields_changed=fields_changed,
            manual_fields_preserved=manual_fields_preserved,
            scenario_completed=scenario_completed,
            needs_followup=False,
            outcome_state="write_applied" if applied_ok else "write_unverified",
            warnings=warnings,
            context_ref=f"verify:{card_id}",
        )

    def _finalize_verify_result(
        self, *, plan: PlanResult, verify: VerifyResult, tool_results: list[ToolResult]
    ) -> VerifyResult:
        del plan, tool_results
        warnings = list(verify.warnings)
        followup_reason = str(verify.followup_reason or "").strip()
        scenario_completed = bool(
            verify.scenario_completed or verify.applied_ok or not verify.needs_followup
        )
        needs_followup = bool(verify.needs_followup)
        if not verify.manual_fields_preserved:
            outcome_state = "needs_human_review"
        elif scenario_completed and verify.applied_ok:
            outcome_state = "completed_confirmed"
        elif scenario_completed:
            outcome_state = "completed_no_write"
        else:
            outcome_state = "blocked_no_progress"
        return VerifyResult(
            applied_ok=bool(verify.applied_ok),
            fields_changed=list(verify.fields_changed),
            manual_fields_preserved=bool(verify.manual_fields_preserved),
            scenario_completed=scenario_completed,
            needs_followup=needs_followup,
            outcome_state=outcome_state,
            warnings=warnings,
            context_ref=verify.context_ref,
            followup_reason=followup_reason,
        )

    def _build_tool_result(
        self,
        tool_name: str,
        payload: dict[str, Any],
        *,
        status: str,
        reason: str,
        scenario_id: str,
        evidence_ref: str,
    ) -> ToolResult:
        return ToolResult(
            tool_name=str(tool_name or "").strip(),
            status=str(status or "success").strip().lower(),
            source_type=self._policy.tool_source_type(tool_name, scenario_id=scenario_id),
            confidence=self._tool_confidence(tool_name, payload),
            data=self._tool_contract_data(tool_name, payload),
            raw_ref=f"{scenario_id}:{tool_name}",
            evidence_ref=str(evidence_ref or "").strip(),
            reason=str(reason or "").strip(),
        )

    def _tool_confidence(self, tool_name: str, payload: dict[str, Any]) -> float:
        data = self._response_data(payload)
        normalized_tool = str(tool_name or "").strip().lower()
        if normalized_tool == "decode_vin":
            status = self._vin_decode_status(data)
            return 0.92 if status == "success" else (0.45 if status == "insufficient" else 0.05)
        if normalized_tool in {"find_part_numbers", "search_part_numbers"}:
            return 0.82 if list(data.get("part_numbers") or []) else 0.25
        if normalized_tool in {"estimate_price_ru", "lookup_part_prices"}:
            return 0.78 if isinstance(data.get("price_summary"), dict) else 0.22
        if normalized_tool == "decode_dtc":
            return 0.84 if list(data.get("results") or []) else 0.25
        if normalized_tool == "search_fault_info":
            return 0.7 if list(data.get("results") or []) else 0.2
        if normalized_tool == "estimate_maintenance":
            return 0.74 if list(data.get("works") or []) else 0.25
        if normalized_tool.startswith("update_") or normalized_tool.startswith("replace_"):
            return 1.0
        return 0.65

    def _tool_contract_data(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._response_data(payload)
        if tool_name == "update_card":
            return {
                "changed": data.get("changed"),
                "changed_fields": data.get("meta", {}).get("changed_fields")
                if isinstance(data.get("meta"), dict)
                else data.get("changed"),
            }
        if tool_name in {
            "update_repair_order",
            "replace_repair_order_works",
            "replace_repair_order_materials",
        }:
            return {
                "ok": bool(payload.get("ok", True)),
                "card_id": data.get("card_id") or payload.get("card_id"),
            }
        if tool_name in {"find_part_numbers", "search_part_numbers"}:
            return {
                "part_numbers": list(data.get("part_numbers") or [])[:5],
                "vehicle_context": data.get("vehicle_context"),
            }
        if tool_name in {"estimate_price_ru", "lookup_part_prices"}:
            return {
                "price_summary": data.get("price_summary"),
                "results_total": len(data.get("results") or []),
            }
        if tool_name in {"decode_dtc", "search_fault_info"}:
            return {
                "results_total": len(data.get("results") or []),
                "query": data.get("query") or data.get("code"),
            }
        if tool_name == "decode_vin":
            return {
                "vin": data.get("vin"),
                "make": data.get("make"),
                "model": data.get("model"),
                "model_year": data.get("model_year"),
            }
        if tool_name == "estimate_maintenance":
            return {
                "service_type": data.get("service_type"),
                "works_total": len(data.get("works") or []),
                "materials_total": len(data.get("materials") or []),
            }
        return data if isinstance(data, dict) else {}

    def _values_equal(self, left: Any, right: Any) -> bool:
        if isinstance(left, dict) and isinstance(right, dict):
            return _json_dumps(left, sort_keys=True) == _json_dumps(right, sort_keys=True)
        if isinstance(left, list) and isinstance(right, list):
            return _json_dumps(left, sort_keys=True) == _json_dumps(right, sort_keys=True)
        return left == right

    def _merge_patch_results(self, left: PatchResult, right: PatchResult) -> PatchResult:
        merged_card_patch = dict(left.card_patch)
        merged_card_patch.update(right.card_patch)
        merged_repair_order_patch = dict(left.repair_order_patch)
        merged_repair_order_patch.update(right.repair_order_patch)
        return PatchResult(
            card_patch=merged_card_patch,
            repair_order_patch=merged_repair_order_patch,
            repair_order_works=[*left.repair_order_works, *right.repair_order_works],
            repair_order_materials=[*left.repair_order_materials, *right.repair_order_materials],
            append_only_notes=[*left.append_only_notes, *right.append_only_notes],
            warnings=[*left.warnings, *right.warnings],
            human_review_needed=bool(left.human_review_needed or right.human_review_needed),
        )

    def _load_card_autofill_context(
        self,
        *,
        card_id: str,
        context_args: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        try:
            return "get_card_context", self._tools.execute("get_card_context", context_args)
        except Exception:
            card_payload = self._board_api.get_card(card_id)
            card_data = self._response_data(card_payload)
            card = card_data.get("card") if isinstance(card_data.get("card"), dict) else card_data
            context: dict[str, Any] = {
                "card": dict(card) if isinstance(card, dict) else {"id": card_id},
                "events": [],
            }
            if hasattr(self._board_api, "get_repair_order"):
                try:
                    repair_order_payload = self._board_api.get_repair_order(card_id)
                    repair_order_data = self._response_data(repair_order_payload)
                    repair_order = (
                        repair_order_data.get("repair_order")
                        if isinstance(repair_order_data.get("repair_order"), dict)
                        else repair_order_data
                    )
                    if isinstance(repair_order, dict):
                        context["card"]["repair_order"] = repair_order
                except Exception:
                    pass
            if hasattr(self._board_api, "get_repair_order_text"):
                try:
                    repair_order_text_payload = self._board_api.get_repair_order_text(card_id)
                    repair_order_text_data = self._response_data(repair_order_text_payload)
                    if isinstance(repair_order_text_data, dict):
                        context["repair_order_text"] = repair_order_text_data
                except Exception:
                    pass
            return "get_card", {"ok": True, "data": context}

    def _analyze_card_completion_context(
        self, context_data: dict[str, Any], *, task_text: str = ""
    ) -> dict[str, Any]:
        card = context_data.get("card") if isinstance(context_data.get("card"), dict) else {}
        repair_order = (
            card.get("repair_order") if isinstance(card.get("repair_order"), dict) else {}
        )
        vehicle_profile = (
            card.get("vehicle_profile") if isinstance(card.get("vehicle_profile"), dict) else {}
        )
        repair_order_text = (
            context_data.get("repair_order_text")
            if isinstance(context_data.get("repair_order_text"), dict)
            else {}
        )
        visible_vehicle_fields = [
            field_name
            for field_name in VEHICLE_PRIMARY_FIELDS
            if field_name
            not in {
                "source_summary",
                "source_confidence",
                "source_links_or_refs",
                "data_completion_state",
            }
        ]
        missing_vehicle_fields = [
            field_name
            for field_name in visible_vehicle_fields
            if not str(vehicle_profile.get(field_name, "") or "").strip()
        ]
        repair_order_fields = [
            "client",
            "phone",
            "vehicle",
            "license_plate",
            "vin",
            "mileage",
            "reason",
            "client_information",
            "note",
            "payment_method",
        ]
        missing_repair_order_fields = [
            field_name
            for field_name in repair_order_fields
            if not str(repair_order.get(field_name, "") or "").strip()
        ]
        completion_text = "\n".join(
            part
            for part in (
                str(card.get("title", "") or "").strip(),
                str(card.get("vehicle", "") or "").strip(),
                str(card.get("description", "") or "").strip(),
                str(repair_order.get("reason", "") or "").strip(),
                str(repair_order.get("client_information", "") or "").strip(),
                str(repair_order.get("note", "") or "").strip(),
                str(repair_order_text.get("text", "") or "").strip(),
                str(task_text or "").strip(),
            )
            if part
        )
        return {
            "card": card,
            "vehicle": str(card.get("vehicle", "") or "").strip(),
            "card_title": str(card.get("title", "") or "").strip(),
            "vehicle_profile": vehicle_profile,
            "repair_order": repair_order,
            "repair_order_text": repair_order_text,
            "missing_vehicle_fields": missing_vehicle_fields,
            "missing_repair_order_fields": missing_repair_order_fields,
            "has_vehicle_profile": bool(vehicle_profile),
            "has_repair_order": bool(repair_order),
            "completion_text": completion_text,
        }

    def _analyze_card_autofill_context(
        self, context_data: dict[str, Any], *, task_text: str = ""
    ) -> dict[str, Any]:
        card = context_data.get("card") if isinstance(context_data.get("card"), dict) else {}
        repair_order = (
            card.get("repair_order") if isinstance(card.get("repair_order"), dict) else {}
        )
        repair_order_text = (
            context_data.get("repair_order_text")
            if isinstance(context_data.get("repair_order_text"), dict)
            else {}
        )
        vehicle_profile = (
            card.get("vehicle_profile") if isinstance(card.get("vehicle_profile"), dict) else {}
        )
        recent_events = (
            context_data.get("events") if isinstance(context_data.get("events"), list) else []
        )
        ai_log_tail = (
            card.get("ai_autofill_log") if isinstance(card.get("ai_autofill_log"), list) else []
        )
        ai_prompt = str(card.get("ai_autofill_prompt", "") or "").strip()
        grounded_description = self._strip_existing_ai_notes(str(card.get("description", "") or ""))
        known_vehicle_facts = {
            "make": str(vehicle_profile.get("make_display", "") or "").strip(),
            "model": str(vehicle_profile.get("model_display", "") or "").strip(),
            "year": str(vehicle_profile.get("production_year", "") or "").strip(),
            "engine": str(vehicle_profile.get("engine_model", "") or "").strip(),
            "gearbox": str(vehicle_profile.get("gearbox_model", "") or "").strip(),
            "drivetrain": str(vehicle_profile.get("drivetrain", "") or "").strip(),
            "vin": str(vehicle_profile.get("vin", "") or repair_order.get("vin", "") or "")
            .strip()
            .upper(),
        }
        grounded_text = "\n".join(
            part
            for part in (
                str(card.get("title", "") or "").strip(),
                str(card.get("vehicle", "") or "").strip(),
                grounded_description,
                str(repair_order.get("reason", "") or "").strip(),
                str(repair_order.get("comment", "") or "").strip(),
                str(repair_order.get("note", "") or "").strip(),
                str(repair_order_text.get("text", "") or "").strip(),
            )
            if part
        )
        ai_log_text = "\n".join(
            str(entry.get("message", "") or "").strip()
            for entry in ai_log_tail[-8:]
            if isinstance(entry, dict) and str(entry.get("message", "") or "").strip()
        )
        continuation_text = "\n".join(
            part for part in (ai_prompt, ai_log_text, str(task_text or "").strip()) if part
        )
        analysis_text = "\n".join(part for part in (grounded_text, continuation_text) if part)
        grounded_haystack = grounded_text.casefold()
        waiting_state = any(token in grounded_haystack for token in _AUTOFILL_WAIT_HINTS)
        vin_match = _AUTOFILL_VIN_PATTERN.search(grounded_text.upper())
        vin = known_vehicle_facts["vin"] or (vin_match.group(0) if vin_match else "")
        mileage = self._extract_autofill_mileage(
            card=card, repair_order=repair_order, source_text=grounded_text
        )
        dtc_codes = list(
            dict.fromkeys(match.upper() for match in _AUTOFILL_DTC_PATTERN.findall(grounded_text))
        )[:2]
        part_queries = self._extract_autofill_part_queries(grounded_text)
        maintenance_trigger_found = self._has_explicit_maintenance_trigger(grounded_text)
        maintenance_scope_hint = self._has_maintenance_scope_hint(grounded_haystack)
        maintenance_query = f"ТО на пробеге {mileage}" if mileage else "ТО"
        if "торм" in grounded_haystack:
            maintenance_query = "ТО и тормоза"
        symptom_trigger_found = self._has_explicit_symptom_trigger(grounded_haystack)
        symptom_query = (
            self._extract_autofill_symptom_query(grounded_text) if symptom_trigger_found else ""
        )
        force_vin_decode = bool(vin) and any(
            token in grounded_haystack for token in ("vin", "расшифр", "комплектац", "подтверд")
        )
        missing_vehicle_fields = self._profile_missing_fields(vehicle_profile)
        vehicle_context = self._extract_autofill_vehicle_context(
            card=card,
            repair_order=repair_order,
            vehicle_profile=vehicle_profile,
            vin=vin,
        )
        evidence_model = self._build_card_autofill_evidence_model(
            vin=vin,
            part_queries=part_queries,
            maintenance_trigger_found=maintenance_trigger_found,
            maintenance_scope_hint=maintenance_scope_hint,
            mileage=mileage,
            dtc_codes=dtc_codes,
            symptom_trigger_found=symptom_trigger_found,
            symptom_query=symptom_query,
            vehicle_context=vehicle_context,
            missing_vehicle_fields=missing_vehicle_fields,
            grounded_haystack=grounded_haystack,
        )
        scenario_evidence = self._build_card_autofill_scenario_evidence(
            evidence_model=evidence_model,
            waiting_state=waiting_state,
        )
        return {
            "card": card,
            "repair_order": repair_order,
            "vehicle_profile": vehicle_profile,
            "source_text": grounded_text,
            "grounded_text": grounded_text,
            "analysis_text": analysis_text,
            "continuation_text": continuation_text,
            "ai_prompt": ai_prompt,
            "recent_events": recent_events[-10:],
            "ai_log_tail": ai_log_tail[-8:],
            "previous_ai_notes": self._extract_existing_ai_notes(
                str(card.get("description", "") or "")
            ),
            "vin": vin,
            "mileage": mileage,
            "dtc_codes": dtc_codes,
            "part_queries": part_queries,
            "maintenance_needed": maintenance_trigger_found,
            "maintenance_query": maintenance_query,
            "symptom_query": symptom_query,
            "waiting_state": waiting_state,
            "force_vin_decode": force_vin_decode,
            "missing_vehicle_fields": missing_vehicle_fields,
            "known_vehicle_facts": known_vehicle_facts,
            "vehicle_context": vehicle_context,
            "evidence_model": evidence_model,
            "scenario_evidence": scenario_evidence,
            "related_cards": [],
        }

    def _build_card_autofill_plan(self, facts: dict[str, Any]) -> dict[str, Any]:
        """Describe available signals without selecting or ordering the model's work."""

        signals = (
            ("vin_enrichment", "VIN", bool(facts.get("vin"))),
            ("parts_lookup", "part", bool(facts.get("part_queries"))),
            ("maintenance_lookup", "maintenance", bool(facts.get("maintenance_needed"))),
            ("dtc_lookup", "DTC", bool(facts.get("dtc_codes"))),
            ("fault_research", "symptom", bool(facts.get("symptom_query"))),
        )
        return {
            "scenarios": [
                {"name": name, "label": label} for name, label, present in signals if present
            ],
            "skipped": [],
            "budget_left": 0,
        }

    def _extract_autofill_vehicle_context(
        self,
        *,
        card: dict[str, Any],
        repair_order: dict[str, Any],
        vehicle_profile: dict[str, Any],
        vin: str,
    ) -> dict[str, Any]:
        return {
            "vehicle": str(
                card.get("vehicle", "") or repair_order.get("vehicle", "") or ""
            ).strip(),
            "make": str(vehicle_profile.get("make_display", "") or "").strip(),
            "model": str(vehicle_profile.get("model_display", "") or "").strip(),
            "year": str(vehicle_profile.get("production_year", "") or "").strip(),
            "engine": str(vehicle_profile.get("engine_model", "") or "").strip(),
            "gearbox": str(vehicle_profile.get("gearbox_model", "") or "").strip(),
            "drivetrain": str(vehicle_profile.get("drivetrain", "") or "").strip(),
            "vin": str(vin or "").strip(),
            "mileage": str(
                vehicle_profile.get("mileage", "") or repair_order.get("mileage", "") or ""
            ).strip(),
            "oil_engine_capacity_l": vehicle_profile.get("oil_engine_capacity_l"),
            "oil_gearbox_capacity_l": vehicle_profile.get("oil_gearbox_capacity_l"),
            "coolant_capacity_l": vehicle_profile.get("coolant_capacity_l"),
        }

    def _extract_autofill_mileage(
        self, *, card: dict[str, Any], repair_order: dict[str, Any], source_text: str
    ) -> str:
        profile = (
            card.get("vehicle_profile") if isinstance(card.get("vehicle_profile"), dict) else {}
        )
        direct = str(profile.get("mileage", "") or repair_order.get("mileage", "") or "").strip()
        if direct:
            return direct
        match = _AUTOFILL_MILEAGE_PATTERN.search(source_text)
        return " ".join(match.group(1).split()) if match else ""

    def _extract_autofill_part_queries(self, source_text: str) -> list[str]:
        haystack = source_text.casefold()
        matches: list[str] = []
        for label, hints in _AUTOFILL_PART_HINTS:
            if any(token in haystack for token in hints):
                matches.append(label)
            if len(matches) >= 2:
                break
        return matches

    def _strip_existing_ai_notes(self, text: str) -> str:
        cleaned: list[str] = []
        inside_ai_block = False
        for raw_line in str(text or "").splitlines():
            line = str(raw_line or "")
            stripped = " ".join(line.strip().split())
            normalized = stripped.casefold()
            if not stripped:
                inside_ai_block = False
                cleaned.append("")
                continue
            if normalized in {"ии:", "ai:"}:
                inside_ai_block = True
                continue
            if normalized.startswith("ии:") or normalized.startswith("ai:"):
                continue
            if inside_ai_block and stripped.startswith("-"):
                continue
            inside_ai_block = False
            cleaned.append(line.rstrip())
        return "\n".join(cleaned).strip()

    def _has_explicit_maintenance_trigger(self, source_text: str) -> bool:
        haystack = source_text.casefold()
        return bool(_AUTOFILL_MAINTENANCE_PATTERN.search(source_text)) or "регламент" in haystack

    def _has_maintenance_scope_hint(self, haystack: str) -> bool:
        return any(token in haystack for token in _AUTOFILL_MAINTENANCE_SCOPE_HINTS)

    def _has_strong_part_lookup_hint(self, haystack: str, part_queries: list[str]) -> bool:
        if any(token in haystack for token in _AUTOFILL_PART_LOOKUP_STRONG_HINTS):
            return True
        return any(query not in {"масло", "фильтр"} for query in part_queries)

    def _has_explicit_symptom_trigger(self, haystack: str) -> bool:
        return any(token in haystack for token in _AUTOFILL_SYMPTOM_HINTS)

    def _has_enough_vehicle_context(
        self, vehicle_context: dict[str, Any], *, missing_vehicle_fields: list[str]
    ) -> bool:
        score = 0
        if str(vehicle_context.get("vehicle", "") or "").strip():
            score += 1
        if str(vehicle_context.get("vin", "") or "").strip():
            score += 1
        for field_name in ("make", "model", "year", "engine", "gearbox", "drivetrain"):
            if str(vehicle_context.get(field_name, "") or "").strip():
                score += 1
        return score >= 2 or len(missing_vehicle_fields) <= 2

    def _build_card_autofill_evidence_model(
        self,
        *,
        vin: str,
        part_queries: list[str],
        maintenance_trigger_found: bool,
        maintenance_scope_hint: bool,
        mileage: str,
        dtc_codes: list[str],
        symptom_trigger_found: bool,
        symptom_query: str,
        vehicle_context: dict[str, Any],
        missing_vehicle_fields: list[str],
        grounded_haystack: str,
    ) -> dict[str, Any]:
        part_query_found = bool(part_queries)
        explicit_part_found = part_query_found and self._has_strong_part_lookup_hint(
            grounded_haystack, part_queries
        )
        return {
            "vin_found": bool(vin),
            "part_query_found": part_query_found,
            "explicit_part_found": explicit_part_found,
            "maintenance_context_found": maintenance_trigger_found,
            "maintenance_scope_found": maintenance_scope_hint,
            "mileage_found": bool(mileage),
            "dtc_found": bool(dtc_codes),
            "fault_symptoms_found": symptom_trigger_found and bool(symptom_query),
            "enough_vehicle_context": self._has_enough_vehicle_context(
                vehicle_context,
                missing_vehicle_fields=missing_vehicle_fields,
            ),
            "external_result_sufficient": False,
        }

    def _build_card_autofill_scenario_evidence(
        self,
        *,
        evidence_model: dict[str, Any],
        waiting_state: bool,
    ) -> dict[str, dict[str, bool]]:
        vin_found = bool(evidence_model.get("vin_found"))
        part_query_found = bool(evidence_model.get("part_query_found"))
        explicit_part_found = bool(evidence_model.get("explicit_part_found"))
        maintenance_context_found = bool(evidence_model.get("maintenance_context_found"))
        maintenance_scope_found = bool(evidence_model.get("maintenance_scope_found"))
        mileage_found = bool(evidence_model.get("mileage_found"))
        dtc_found = bool(evidence_model.get("dtc_found"))
        fault_symptoms_found = bool(evidence_model.get("fault_symptoms_found"))
        return {
            "vin_enrichment": {
                "trigger_found": vin_found,
                "confidence_enough": vin_found,
            },
            "parts_lookup": {
                "trigger_found": part_query_found,
                "confidence_enough": explicit_part_found,
            },
            "maintenance_lookup": {
                "trigger_found": maintenance_context_found,
                "confidence_enough": maintenance_context_found
                and (mileage_found or maintenance_scope_found),
            },
            "dtc_lookup": {
                "trigger_found": dtc_found,
                "confidence_enough": dtc_found,
            },
            "fault_research": {
                "trigger_found": fault_symptoms_found,
                "confidence_enough": fault_symptoms_found
                and not explicit_part_found
                and not maintenance_context_found
                and not dtc_found
                and not waiting_state,
            },
        }

    def _vin_decode_status(self, payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return "failed"
        if any(
            str(payload.get(key, "") or "").strip()
            for key in ("model", "model_year", "engine_model", "transmission", "drive_type")
        ):
            return "success"
        if any(str(payload.get(key, "") or "").strip() for key in ("make", "plant_country", "vin")):
            return "insufficient"
        return "failed"

    def _extract_existing_ai_notes(self, description_text: str) -> list[str]:
        notes: list[str] = []
        inside_ai_block = False
        for raw_line in str(description_text or "").splitlines():
            line = " ".join(str(raw_line or "").strip().split())
            if not line:
                inside_ai_block = False
                continue
            normalized = line.casefold()
            if normalized in {"ии:", "ai:"}:
                inside_ai_block = True
                continue
            if normalized.startswith("ии:") or normalized.startswith("ai:"):
                notes.append(line.split(":", 1)[1].strip())
                inside_ai_block = False
                continue
            if inside_ai_block:
                notes.append(line.lstrip("- ").strip())
        return [item for item in notes if item]

    def _extract_autofill_symptom_query(self, source_text: str) -> str:
        lines: list[str] = []
        symptom_lines: list[str] = []
        blocked_prefixes = (
            "клиент",
            "customer",
            "телефон",
            "phone",
            "марка",
            "make",
            "модель",
            "model",
            "год",
            "vin",
            "гос. номер",
            "госномер",
            "license plate",
            "пробег",
            "mileage",
        )
        symptom_markers = (
            "течь",
            "антифриз",
            "стук",
            "вибрац",
            "ошибк",
            "неисправ",
            "жалоб",
            "симптом",
            "перегрев",
            "дым",
            "шум",
            "троит",
            "coolant",
            "leak",
            "overheat",
            "noise",
            "fault",
        )
        for raw_line in str(source_text or "").splitlines():
            line = " ".join(str(raw_line or "").strip().split())
            if not line:
                continue
            lower = line.casefold()
            if lower.startswith("vin") or lower.startswith("ии:") or lower.startswith("ai:"):
                continue
            if "артикул" in lower:
                continue
            if "цена" in lower and any(char.isdigit() for char in line):
                continue
            if any(lower.startswith(prefix) for prefix in blocked_prefixes):
                continue
            if self._looks_like_customer_line(lower):
                continue
            if any(marker in lower for marker in symptom_markers):
                symptom_lines.append(line)
                continue
            lines.append(line)
            if len(lines) >= 3 and len(symptom_lines) >= 2:
                break
        preferred = symptom_lines[:2] if symptom_lines else []
        fallback = [line for line in lines if line not in preferred][:1]
        return "; ".join(preferred + fallback)[:280]

    def _looks_like_customer_line(self, lower_line: str) -> bool:
        compact = " ".join(str(lower_line or "").split())
        if not compact:
            return False
        if any(token in compact for token in ("+7", "8 (", "телефон", "phone")):
            return True
        words = [item for item in compact.replace(".", " ").split() if item]
        if 2 <= len(words) <= 4 and all(word.isalpha() for word in words):
            return True
        return False

    def _profile_missing_fields(self, vehicle_profile: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for field_name in (
            "make_display",
            "model_display",
            "production_year",
            "engine_model",
            "gearbox_model",
            "drivetrain",
        ):
            if not str(vehicle_profile.get(field_name, "") or "").strip():
                missing.append(field_name)
        return missing

    def _build_user_task_message(
        self,
        task: dict[str, Any],
        metadata: dict[str, Any],
        *,
        task_type: str,
        preloaded_context: dict[str, Any] | None = None,
    ) -> str:
        lines = [
            f"Task id: {task['id']}",
            f"Mode: {task.get('mode', 'manual')}",
            f"Source: {task.get('source', 'manual')}",
            f"Task type: {task_type}",
        ]
        requested_by = str(metadata.get("requested_by", "") or "").strip()
        if requested_by:
            lines.append(f"Requested by: {requested_by}")
        scheduled_name = str(metadata.get("scheduled_task_name", "") or "").strip()
        if scheduled_name:
            lines.append(f"Scheduled task: {scheduled_name}")
        context = metadata.get("context") if isinstance(metadata.get("context"), dict) else {}
        if context:
            lines.append("Context metadata:")
            lines.append(_json_dumps(context, indent=2))
            if str(context.get("kind", "")).strip().lower() == "card":
                lines.append(
                    "This task is linked to a card; use its facts as relevant context and keep the work in scope."
                )
        scope_prompt = self._build_scope_prompt_block(metadata, card_context=preloaded_context)
        if scope_prompt:
            lines.append(scope_prompt)
        lines.append("Task:")
        lines.append(str(task.get("task_text", "") or "").strip())
        return "\n".join(lines)

    def _build_scope_prompt_block(
        self,
        metadata: dict[str, Any],
        *,
        card_context: dict[str, Any] | None = None,
    ) -> str:
        scope = metadata.get("scope") if isinstance(metadata.get("scope"), dict) else {}
        scope_type = str(scope.get("type", "") or "").strip().lower()
        if scope_type not in {"all_cards", "column", "current_card"}:
            return ""
        scope_payload: dict[str, Any] = {
            "type": scope_type,
            "column": str(scope.get("column", "") or "").strip(),
            "column_label": str(scope.get("column_label", "") or "").strip(),
            "card_id": str(scope.get("card_id", "") or "").strip(),
            "card_label": str(scope.get("card_label", "") or "").strip(),
            "cards": [],
        }
        try:
            if scope_type == "current_card" and scope_payload["card_id"]:
                if isinstance(card_context, dict):
                    context_data = self._response_data(card_context)
                else:
                    context_result = self._board_api.get_card_context(
                        scope_payload["card_id"],
                        event_limit=20,
                        include_repair_order_text=True,
                    )
                    context_data = self._response_data(context_result)
                scope_payload["card"] = (
                    context_data.get("card") if isinstance(context_data.get("card"), dict) else {}
                )
                scope_payload["events"] = (
                    context_data.get("events")
                    if isinstance(context_data.get("events"), list)
                    else []
                )[:12]
                return "Execution scope:\n" + _json_dumps(scope_payload, indent=2)
            if scope_type == "column" and scope_payload["column"]:
                result = self._board_api.search_cards(
                    query=None,
                    include_archived=False,
                    column=scope_payload["column"],
                    tag=None,
                    indicator=None,
                    status=None,
                    limit=40,
                )
                search_data = self._response_data(result)
                cards = (
                    search_data.get("cards") if isinstance(search_data.get("cards"), list) else []
                )
            else:
                snapshot = self._board_api.get_board_snapshot(archive_limit=0)
                snapshot_data = self._response_data(snapshot)
                columns = (
                    snapshot_data.get("columns")
                    if isinstance(snapshot_data.get("columns"), list)
                    else []
                )
                cards = []
                for column in columns if isinstance(columns, list) else []:
                    items = column.get("cards") if isinstance(column, dict) else []
                    if isinstance(items, list):
                        cards.extend(items)
            scope_payload["cards"] = [
                {
                    "id": item.get("id"),
                    "vehicle": item.get("vehicle"),
                    "title": item.get("title"),
                    "column": item.get("column"),
                    "tags": item.get("tags"),
                }
                for item in (cards if isinstance(cards, list) else [])[:20]
                if isinstance(item, dict)
            ]
        except Exception as exc:
            scope_payload["error"] = str(exc)
        return "Execution scope:\n" + _json_dumps(scope_payload, indent=2)

    def _cleanup_card_id(self, metadata: dict[str, Any]) -> str:
        context = metadata.get("context") if isinstance(metadata.get("context"), dict) else {}
        if str(context.get("kind", "")).strip().lower() != "card":
            return ""
        return str(context.get("card_id", "") or "").strip()

    def _context_kind(self, metadata: dict[str, Any]) -> str:
        context = metadata.get("context") if isinstance(metadata.get("context"), dict) else {}
        return str(context.get("kind", "") or "board").strip().lower() or "board"

    def _classify_task(self, task: dict[str, Any], metadata: dict[str, Any]) -> str:
        purpose = str(metadata.get("purpose", "") or "").strip().lower()
        if purpose in {"full_card_enrichment", "card_enrichment"}:
            return "full_card_enrichment"
        if purpose in {
            "card_autofill",
            "board_control",
        }:
            return "card_cleanup"
        text = self._normalized_task_text(str(task.get("task_text", "") or ""))
        if self._is_card_cleanup_task(task, metadata):
            return "card_cleanup"
        return self._classify_task_from_text(text)

    def _classify_task_from_text(self, text: str) -> str:
        if "vin" in text or "расшифру" in text or "decode vin" in text:
            return "vin_decode"
        if "dtc" in text or _AUTOFILL_DTC_PATTERN.search(text.upper()):
            return "dtc_lookup"
        if "запчаст" in text or "каталож" in text or "part number" in text or "oem" in text:
            return "parts_lookup"
        if (
            "техобслуж" in text
            or "maintenance" in text
            or "service" in text
            or "процени то" in text
            or "то на" in text
        ):
            return "maintenance_estimate"
        if "заказ-наряд" in text or "repair order" in text or "work order" in text:
            return "repair_order_assist"
        if "касс" in text or "оплат" in text or "cash" in text or "payment" in text:
            return "cash_review"
        if (
            "обзор" in text
            or "просроч" in text
            or "review board" in text
            or "review the board" in text
        ):
            return "board_review"
        return "general"

    def _is_card_cleanup_task(self, task: dict[str, Any], metadata: dict[str, Any]) -> bool:
        if not self._cleanup_card_id(metadata):
            return False
        text = self._normalized_task_text(str(task.get("task_text", "") or ""))
        cleanup_markers = (
            "наведи порядок",
            "порядок в карточке",
            "структурир",
            "заполни карточ",
            "cleanup",
            "clean up",
            "tidy up",
            "structure the card",
        )
        for marker in cleanup_markers:
            if marker in text:
                return True
        return ("карточ" in text or "card" in text) and (
            "структур" in text or "заполни" in text or "поряд" in text
        )

    def _normalized_task_text(self, value: str) -> str:
        text = " ".join(str(value or "").strip().lower().split())
        if not text:
            return ""
        repaired = self._repair_mojibake_text(text)
        return repaired if self._task_text_score(repaired) > self._task_text_score(text) else text

    def _repair_mojibake_text(self, text: str) -> str:
        candidates = [text]
        for encoding in ("latin1", "cp1251", "cp866"):
            try:
                repaired = text.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            candidates.append(" ".join(repaired.lower().split()))
        best = text
        best_score = self._task_text_score(text)
        for candidate in candidates[1:]:
            score = self._task_text_score(candidate)
            if score > best_score:
                best = candidate
                best_score = score
        return best

    def _task_text_score(self, text: str) -> int:
        normalized = str(text or "").lower()
        keywords = (
            "наведи",
            "поряд",
            "карточ",
            "структур",
            "заполни",
            "vin",
            "расшифр",
            "запчаст",
            "каталож",
            "касс",
            "оплат",
            "обзор",
            "просроч",
            "техобслуж",
            "maintenance",
            "service",
        )
        score = sum(8 for keyword in keywords if keyword in normalized)
        score += sum(1 for char in normalized if ("а" <= char <= "я") or char == "ё")
        score -= normalized.count("?") * 4
        score -= normalized.count("�") * 6
        return score

    def _extract_card_update_apply(
        self, decision: dict[str, Any], *, cleanup_card_id: str
    ) -> dict[str, Any] | None:
        payload = decision.get("apply")
        if not isinstance(payload, dict):
            return None
        if str(payload.get("type", "") or "").strip().lower() != "update_card":
            return None
        card_id = str(payload.get("card_id", "") or "").strip() or cleanup_card_id
        if not card_id:
            return None
        update_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        normalized_payload: dict[str, Any] = {"card_id": card_id}
        for field_name in (
            "vehicle",
            "title",
            "description",
            "deadline",
            "tags",
            "vehicle_profile",
            "repair_order",
        ):
            if field_name in update_payload:
                normalized_payload[field_name] = update_payload[field_name]
        return normalized_payload if len(normalized_payload) > 1 else None

    def _summarize_applied_update(
        self, args: dict[str, Any], result_payload: dict[str, Any]
    ) -> list[str]:
        response_data = self._response_data(result_payload)
        changed_payload = response_data.get("changed")
        if not isinstance(changed_payload, list):
            meta = response_data.get("meta") if isinstance(response_data.get("meta"), dict) else {}
            changed_payload = meta.get("changed_fields")
        changed_fields = (
            [str(item or "").strip() for item in changed_payload if str(item or "").strip()]
            if isinstance(changed_payload, list)
            else []
        )
        if not changed_fields:
            changed_fields = [
                field_name
                for field_name in (
                    "vehicle",
                    "title",
                    "description",
                    "deadline",
                    "tags",
                    "vehicle_profile",
                    "repair_order",
                )
                if field_name in args
            ]
        labels = {
            "vehicle": "автомобиль",
            "title": "краткая суть",
            "description": "описание",
            "deadline": "сигнал",
            "tags": "метки",
            "vehicle_profile": "паспорт автомобиля",
            "repair_order": "заказ-наряд",
        }
        return [labels.get(item, item) for item in changed_fields]

    def _append_applied_updates(
        self, display: dict[str, Any], applied_updates: list[str]
    ) -> dict[str, Any]:
        unique_updates: list[str] = []
        seen: set[str] = set()
        for item in applied_updates:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            unique_updates.append(value)
        if not unique_updates:
            return display
        payload = dict(display)
        sections = list(payload.get("sections") or [])
        sections.insert(
            0,
            {
                "title": "Применено",
                "body": "",
                "items": [f"Обновлено поле: {item}" for item in unique_updates],
            },
        )
        payload["sections"] = sections[:6]
        return payload

    def _update_board_control_runtime_after_task(
        self, *, task: dict[str, Any], orchestration: dict[str, Any] | None
    ) -> None:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        if str(metadata.get("purpose", "") or "").strip().lower() != "board_control":
            return
        card_id = self._cleanup_card_id(metadata)
        if not card_id:
            return
        status = self._storage.read_status()
        runtime = (
            status.get("board_control") if isinstance(status.get("board_control"), dict) else {}
        )
        runtime = dict(runtime)
        cache = self._safe_dict(runtime.get("card_cache"))
        cache_entry = self._safe_dict(cache.get(card_id))
        patch_payload = (
            orchestration.get("patch")
            if isinstance(orchestration, dict) and isinstance(orchestration.get("patch"), dict)
            else {}
        )
        verify_payload = (
            orchestration.get("verify")
            if isinstance(orchestration, dict) and isinstance(orchestration.get("verify"), dict)
            else {}
        )
        card_patch = (
            patch_payload.get("card_patch")
            if isinstance(patch_payload.get("card_patch"), dict)
            else {}
        )
        wrote_anything = bool(card_patch)
        verify_ok = bool(verify_payload.get("applied_ok"))
        cache_entry["last_result"] = (
            "written"
            if wrote_anything and verify_ok
            else ("completed_no_write" if not wrote_anything else "verify_failed")
        )
        cache_entry["last_verify_ok"] = verify_ok
        cache_entry["last_processed_at"] = utc_now_iso()
        cache[card_id] = cache_entry
        runtime["card_cache"] = cache
        if wrote_anything and verify_ok:
            written_count = self._safe_non_negative_int(runtime.get("written_count"))
            runtime["written_count"] = min(_BOARD_CONTROL_COUNTER_MAX, written_count + 1)
        traces = self._safe_dict_list(
            runtime.get("recent_traces"), limit=_BOARD_CONTROL_TRACE_LIMIT
        )
        traces.insert(
            0,
            {
                "card_id": card_id,
                "status": cache_entry["last_result"],
                "verify_ok": verify_ok,
                "written": wrote_anything,
                "at": utc_now_iso(),
            },
        )
        runtime["recent_traces"] = traces[:_BOARD_CONTROL_TRACE_LIMIT]
        self._storage.update_status(board_control=runtime)

    def _update_board_control_runtime_after_failure(
        self, *, task: dict[str, Any], error: str
    ) -> None:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        if str(metadata.get("purpose", "") or "").strip().lower() != "board_control":
            return
        card_id = self._cleanup_card_id(metadata)
        status = self._storage.read_status()
        runtime = (
            status.get("board_control") if isinstance(status.get("board_control"), dict) else {}
        )
        runtime = dict(runtime)
        cache = self._safe_dict(runtime.get("card_cache"))
        if card_id:
            cache_entry = self._safe_dict(cache.get(card_id))
            cache_entry["last_result"] = "failed"
            cache_entry["last_verify_ok"] = False
            cache_entry["last_processed_at"] = utc_now_iso()
            cache[card_id] = cache_entry
        runtime["card_cache"] = cache
        error_count = self._safe_non_negative_int(runtime.get("error_count"))
        runtime["error_count"] = min(_BOARD_CONTROL_COUNTER_MAX, error_count + 1)
        traces = self._safe_dict_list(
            runtime.get("recent_traces"), limit=_BOARD_CONTROL_TRACE_LIMIT
        )
        traces.insert(
            0,
            {
                "card_id": card_id,
                "status": "failed",
                "error": str(error or "").strip(),
                "at": utc_now_iso(),
            },
        )
        runtime["recent_traces"] = traces[:_BOARD_CONTROL_TRACE_LIMIT]
        self._storage.update_status(board_control=runtime)

    def _safe_dict(self, value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _safe_dict_list(self, value: Any, *, limit: int) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            items.append(dict(item))
            if len(items) >= limit:
                break
        return items

    def _safe_non_negative_int(self, value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        if not math.isfinite(numeric) or not numeric.is_integer() or numeric <= 0:
            return 0
        if numeric > 1_000_000_000:
            return 1_000_000_000
        return int(numeric)

    def _safe_text_list(self, value: Any, *, limit: int) -> list[str]:
        if isinstance(value, str):
            raw_items: list[Any] = [value]
        elif isinstance(value, list):
            raw_items = value
        else:
            return []
        items: list[str] = []
        for raw in raw_items:
            text = str(raw or "").strip()
            if not text:
                continue
            items.append(text)
            if len(items) >= limit:
                break
        return items


def build_board_api_client(*, logger: logging.Logger) -> BoardApiClient:
    board_api_url = get_agent_board_api_url() or discover_board_api(timeout_seconds=1.0)
    if not board_api_url:
        raise RuntimeError("Unable to discover a reachable local board API for the server agent.")
    try:
        client = BoardApiClient(board_api_url, logger=logger, default_source="agent")
        health = client.health()
    except BoardApiTransportError as exc:
        raise RuntimeError(f"Board API is not reachable for the server agent: {exc}") from exc
    if not health.get("ok"):
        raise RuntimeError("Board API health check failed for the server agent.")
    return client


def run_agent_loop(*, logger: logging.Logger) -> int:
    if not get_agent_enabled():
        logger.info("agent_runtime_disabled")
        return 0
    storage = AgentStorage()
    idle_sleep = get_agent_poll_interval_seconds()
    if not storage.read_prompt_text().strip():
        storage.write_prompt_text(DEFAULT_SYSTEM_PROMPT)
    if not storage.read_memory_text().strip():
        storage.write_memory_text(
            "CRM URL: https://crm.autostopcrm.ru\n"
            "MCP URL: https://crm.autostopcrm.ru/mcp\n"
            "Admin smoke credentials: use AUTOSTOP_SMOKE_OPERATOR_USERNAME/AUTOSTOP_SMOKE_OPERATOR_PASSWORD from the runtime environment.\n"
            "Use cashbox names exactly as they exist.\n"
            "If payment goes to cashbox 'Безналичный', the repair order withholds 15% taxes and fees from that gross incoming amount and applies the remaining 85% to the client debt.\n"
            "Cashboxes 'Наличный' and 'Карта Мария' count as cash-like payments and do not withhold taxes and fees.\n"
        )
    board_api = None
    while board_api is None:
        try:
            board_api = build_board_api_client(logger=logger)
        except Exception as exc:
            storage.update_status(
                running=False,
                current_task_id=None,
                current_run_id=None,
                last_heartbeat=utc_now_iso(),
                last_error=str(exc),
            )
            logger.warning("agent_waiting_for_board_api error=%s", exc)
            time.sleep(idle_sleep)
    model_client = OpenAIJsonAgentClient()
    runner = AgentRunner(
        storage=storage, board_api=board_api, model_client=model_client, logger=logger
    )
    logger.info(
        "agent_runtime_started model=%s board_api_url=%s",
        get_agent_openai_model(),
        board_api.base_url,
    )
    while True:
        try:
            processed = runner.run_once()
        except KeyboardInterrupt:
            break
        except Exception as exc:
            storage.update_status(
                running=False,
                current_task_id=None,
                current_run_id=None,
                last_heartbeat=utc_now_iso(),
                last_error=str(exc),
            )
            logger.exception("agent_runtime_loop_failed error=%s", exc)
            time.sleep(idle_sleep)
            continue
        time.sleep(idle_sleep if not processed else 0.2)
    return 0
