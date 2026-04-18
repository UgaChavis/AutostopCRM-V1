from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .base import ScenarioContext, ScenarioExecutionResult


def _build_vin_web_query(decoded_vin: dict[str, Any]) -> str:
    parts = [
        str(decoded_vin.get("vin", "") or "").strip(),
        str(decoded_vin.get("make", "") or "").strip(),
        str(decoded_vin.get("model", "") or "").strip(),
        str(decoded_vin.get("model_year", "") or "").strip(),
        "engine",
        "transmission",
        "horsepower",
        "specifications",
    ]
    return " ".join(part for part in parts if part).strip()


def _merge_web_enrichment(
    decoded_vin: dict[str, Any], parsed_profile: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    merged = dict(decoded_vin)
    enriched_fields: list[str] = []

    def _clean_engine_model(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        cleaned = re.sub(r"\s+\d{2,4}\s*(?:HP|Л\.?\s*С\.?|ЛС)\b.*$", "", text, flags=re.IGNORECASE)
        return cleaned.strip()

    def _set_if_missing(
        target_key: str, source_value: Any, *, field_name: str | None = None
    ) -> None:
        value = source_value
        if value in (None, ""):
            return
        normalized = str(value).strip()
        if not normalized:
            return
        if str(merged.get(target_key, "") or "").strip():
            return
        merged[target_key] = value
        label = field_name or target_key
        if label not in enriched_fields:
            enriched_fields.append(label)

    _set_if_missing("make", parsed_profile.get("make_display"), field_name="make_display")
    _set_if_missing("model", parsed_profile.get("model_display"), field_name="model_display")
    _set_if_missing(
        "model_year", parsed_profile.get("production_year"), field_name="production_year"
    )
    engine_model = _clean_engine_model(parsed_profile.get("engine_model"))
    _set_if_missing("engine_model", engine_model or parsed_profile.get("engine_model"))
    _set_if_missing("engine_power_hp", parsed_profile.get("engine_power_hp"))
    gearbox_model = parsed_profile.get("gearbox_model") or parsed_profile.get("gearbox_type")
    _set_if_missing("gearbox_model", gearbox_model)
    _set_if_missing("transmission", gearbox_model, field_name="gearbox_model")
    _set_if_missing("drive_type", parsed_profile.get("drivetrain"))

    if parsed_profile.get("warnings"):
        merged["web_enrichment_warnings"] = list(parsed_profile.get("warnings") or [])
    return merged, enriched_fields


@dataclass(frozen=True)
class VinEnrichmentScenarioExecutor:
    scenario_id: str = "vin_enrichment"

    def execute(self, context: ScenarioContext) -> ScenarioExecutionResult:
        runtime = context.runtime
        facts = context.facts
        if runtime is None:
            raise ValueError("VinEnrichmentScenarioExecutor requires runtime.")
        facts["vin_decode_attempted"] = True
        runtime._record_log_action(
            task_id=context.task_id,
            run_id=context.run_id,
            step=0,
            level="RUN",
            phase="tool",
            message="decode_vin requested.",
        )
        vin_payload = runtime._run_autofill_tool(
            task_id=context.task_id,
            run_id=context.run_id,
            step=1,
            tool_name="decode_vin",
            args={"vin": facts["vin"]},
            reason="Decode VIN first and reuse the confirmed vehicle facts in later lookup steps",
        )
        if vin_payload is None:
            facts["vin_decode_status"] = "failed"
            runtime._record_log_action(
                task_id=context.task_id,
                run_id=context.run_id,
                step=1,
                level="WARN",
                phase="tool",
                message="decode_vin failed.",
            )
            return ScenarioExecutionResult(
                scenario_id=self.scenario_id,
                status="failed",
                facts_updates={"vin_decode_status": "failed"},
                warnings=["vin decode request failed"],
                needs_followup=True,
                followup_reason="vin_decode_failed",
            )
        orchestration_payload = runtime._response_data(vin_payload) or vin_payload
        vin_status = runtime._vin_decode_status(orchestration_payload)
        facts["vin_decode_status"] = vin_status
        if isinstance(facts.get("evidence_model"), dict):
            facts["evidence_model"]["external_result_sufficient"] = vin_status == "success"
        if vin_status == "success":
            facts["vehicle_context"] = runtime._merge_vehicle_context(
                facts["vehicle_context"],
                orchestration_payload,
            )
            web_tool_calls = 0
            web_lookup_attempted = False
            if runtime._vin_web_enrichment_required(orchestration_payload):
                web_lookup_attempted = True
                facts["vin_web_enrichment_used"] = False
                web_query = _build_vin_web_query(orchestration_payload)
                web_search_args = {"query": web_query, "limit": 4}
                runtime._record_log_action(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    step=2,
                    level="INFO",
                    phase="tool",
                    message="search_web requested for VIN enrichment.",
                )
                web_tool_calls += 1
                web_search_payload = runtime._run_autofill_tool(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    step=2,
                    tool_name="search_web",
                    args=web_search_args,
                    reason="Look up VIN-derived vehicle facts in the public web when decode_vin output is sparse",
                )
                web_search_data = (
                    runtime._response_data(web_search_payload) if web_search_payload else {}
                )
                web_results = (
                    web_search_data.get("results") if isinstance(web_search_data, dict) else []
                )
                excerpt_payloads: list[dict[str, Any]] = []
                if isinstance(web_results, list):
                    for offset, item in enumerate(web_results[:4], start=3):
                        if not isinstance(item, dict):
                            continue
                        url = str(item.get("url", "") or "").strip()
                        if not url:
                            continue
                        runtime._record_log_action(
                            task_id=context.task_id,
                            run_id=context.run_id,
                            step=offset,
                            level="INFO",
                            phase="tool",
                            message="fetch_page_excerpt requested for VIN enrichment.",
                        )
                        web_tool_calls += 1
                        excerpt_payload = runtime._run_autofill_tool(
                            task_id=context.task_id,
                            run_id=context.run_id,
                            step=offset,
                            tool_name="fetch_page_excerpt",
                            args={"url": url, "max_chars": 1800},
                            reason="Read the first useful external VIN/spec page excerpt before applying the card patch",
                        )
                        if excerpt_payload is not None:
                            excerpt_payloads.append(excerpt_payload)
                combined_text_parts: list[str] = []
                if isinstance(web_results, list):
                    for item in web_results[:4]:
                        if not isinstance(item, dict):
                            continue
                        for key in ("title", "snippet", "excerpt"):
                            text = str(item.get(key, "") or "").strip()
                            if text:
                                combined_text_parts.append(text)
                for payload in excerpt_payloads:
                    excerpt_data = runtime._response_data(payload) if payload else {}
                    excerpt_text = str(
                        excerpt_data.get("excerpt", "") if isinstance(excerpt_data, dict) else ""
                    ).strip()
                    if excerpt_text:
                        combined_text_parts.append(excerpt_text)
                    title_text = str(
                        excerpt_data.get("title", "") if isinstance(excerpt_data, dict) else ""
                    ).strip()
                    if title_text:
                        combined_text_parts.append(title_text)
                parsed_profile = runtime._parse_vehicle_profile_text(
                    "\n".join(combined_text_parts),
                    explicit_vehicle=" ".join(
                        part
                        for part in (
                            str(orchestration_payload.get("make", "") or "").strip(),
                            str(orchestration_payload.get("model", "") or "").strip(),
                            str(orchestration_payload.get("model_year", "") or "").strip(),
                        )
                        if part
                    ),
                )
                enriched_payload, enriched_fields = _merge_web_enrichment(
                    orchestration_payload, parsed_profile
                )
                if enriched_fields:
                    enriched_payload["web_source_urls"] = [
                        str(item.get("url", "") or "").strip()
                        for item in (web_results if isinstance(web_results, list) else [])
                        if isinstance(item, dict) and str(item.get("url", "") or "").strip()
                    ][:4]
                    enriched_payload["web_enrichment_fields"] = enriched_fields
                    orchestration_payload = enriched_payload
                    facts["vin_web_enrichment_used"] = True
                    facts["vin_web_enrichment_fields"] = list(enriched_fields)
                    facts["vehicle_context"] = runtime._merge_vehicle_context(
                        facts["vehicle_context"],
                        orchestration_payload,
                    )
                    runtime._record_log_action(
                        task_id=context.task_id,
                        run_id=context.run_id,
                        step=max(3, 2 + len(excerpt_payloads)),
                        level="INFO",
                        phase="analysis",
                        message="VIN web enrichment produced additional confirmed facts.",
                    )
        else:
            web_tool_calls = 0
            web_lookup_attempted = False
        return ScenarioExecutionResult(
            scenario_id=self.scenario_id,
            status="success",
            tool_calls_used=1 + web_tool_calls,
            tool_results=[
                runtime._build_tool_result(
                    "decode_vin",
                    vin_payload,
                    status="success",
                    reason="Decode VIN first and reuse the confirmed vehicle facts in later lookup steps",
                    scenario_id=self.scenario_id,
                    evidence_ref="vin",
                ),
                *(
                    [
                        runtime._build_tool_result(
                            "search_web",
                            web_search_payload,
                            status="success",
                            reason="Look up VIN-derived vehicle facts in the public web when decode_vin output is sparse",
                            scenario_id=self.scenario_id,
                            evidence_ref="vin_web",
                        )
                    ]
                    if "web_search_payload" in locals() and web_search_payload is not None
                    else []
                ),
                *[
                    runtime._build_tool_result(
                        "fetch_page_excerpt",
                        payload,
                        status="success",
                        reason="Read the first useful external VIN/spec page excerpt before applying the card patch",
                        scenario_id=self.scenario_id,
                        evidence_ref="vin_web",
                    )
                    for payload in (excerpt_payloads if "excerpt_payloads" in locals() else [])
                ],
            ],
            orchestration_updates={
                "decode_vin": orchestration_payload,
                **(
                    {"vin_web_lookup": web_search_data}
                    if "web_search_data" in locals() and isinstance(web_search_data, dict)
                    else {}
                ),
            },
            facts_updates={
                "vin_decode_status": vin_status,
                "vehicle_context": dict(facts.get("vehicle_context") or {}),
            },
            warnings=(
                ["vin decode returned sparse data"]
                if vin_status == "insufficient"
                else (
                    ["VIN web enrichment returned no additional confirmed facts"]
                    if web_lookup_attempted and facts.get("vin_web_enrichment_used") is False
                    else []
                )
            ),
            needs_followup=vin_status in {"insufficient", "failed"},
            followup_reason="vin_decode_insufficient"
            if vin_status == "insufficient"
            else ("vin_decode_failed" if vin_status == "failed" else ""),
        )
