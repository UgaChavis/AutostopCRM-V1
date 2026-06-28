from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ...vehicle_profile import (
    normalize_source_confidence,
    normalize_vehicle_int,
    normalize_vehicle_links,
)
from .base import ScenarioContext, ScenarioExecutionResult

_VIN_WEB_BLOCKED_DOMAINS = {
    "nhtsa.gov",
    "autozone.com",
}

_VIN_ENRICHMENT_FALLBACK_FIELDS = {
    "make": "make_display",
    "model": "model_display",
    "model_year": "production_year",
    "engine_model": "engine_model",
    "engine_power_hp": "engine_power_hp",
    "gearbox_model": "gearbox_model",
    "transmission": "gearbox_type",
    "drive_type": "drivetrain",
}


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


def _vin_web_url_is_blocked(url: str) -> bool:
    try:
        hostname = (urlparse(str(url or "").strip()).hostname or "").strip().casefold().rstrip(".")
    except ValueError:
        return True
    return any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in _VIN_WEB_BLOCKED_DOMAINS
    )


def _vin_web_result_score(item: dict[str, Any]) -> int:
    url = str(item.get("url", "") or "").strip()
    text = " ".join(
        str(item.get(key, "") or "").strip() for key in ("title", "snippet", "excerpt")
    ).casefold()
    score = 0
    for token in (
        "spec",
        "specs",
        "engine",
        "transmission",
        "gearbox",
        "horsepower",
        "power",
        "hp",
        "drivetrain",
        "awd",
        "4wd",
        "quattro",
        "technical",
    ):
        if token in text:
            score += 2
    if "vin" in text:
        score += 1
    if _vin_web_url_is_blocked(url):
        score -= 100
    return score


def _normalize_web_enrichment_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _clean_web_enrichment_engine_model(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    cleaned = re.sub(r"\s+\d{2,4}\s*(?:HP|Л\.?\s*С\.?|ЛС)\b.*$", "", text, flags=re.IGNORECASE)
    return cleaned.strip()


def _web_enrichment_fallback_value(fallback_profile: dict[str, Any], *, target_key: str) -> Any:
    source_key = _VIN_ENRICHMENT_FALLBACK_FIELDS.get(target_key)
    if source_key is None:
        return ""
    return fallback_profile.get(source_key, "")


def _apply_web_enrichment_field(
    merged: dict[str, Any],
    enriched_fields: list[str],
    *,
    fallback_profile: dict[str, Any],
    target_key: str,
    source_value: Any,
    field_name: str | None = None,
) -> None:
    value = source_value
    if value in (None, ""):
        return
    normalized = str(value).strip()
    if not normalized:
        return
    if str(merged.get(target_key, "") or "").strip():
        return
    fallback_value = _web_enrichment_fallback_value(fallback_profile, target_key=target_key)
    if fallback_value and _normalize_web_enrichment_text(
        fallback_value
    ) != _normalize_web_enrichment_text(value):
        return
    merged[target_key] = value
    label = field_name or target_key
    if label not in enriched_fields:
        enriched_fields.append(label)


def _merge_web_enrichment(
    decoded_vin: dict[str, Any],
    parsed_profile: dict[str, Any],
    *,
    fallback_profile: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    merged = dict(decoded_vin) if isinstance(decoded_vin, dict) else {}
    enriched_fields: list[str] = []
    fallback_profile = fallback_profile if isinstance(fallback_profile, dict) else {}

    if fallback_profile:
        for target_key in _VIN_ENRICHMENT_FALLBACK_FIELDS:
            merged[target_key] = fallback_profile.get(
                _VIN_ENRICHMENT_FALLBACK_FIELDS[target_key], merged.get(target_key, "")
            )

    _apply_web_enrichment_field(
        merged,
        enriched_fields,
        fallback_profile=fallback_profile,
        target_key="make",
        source_value=parsed_profile.get("make_display"),
        field_name="make_display",
    )
    _apply_web_enrichment_field(
        merged,
        enriched_fields,
        fallback_profile=fallback_profile,
        target_key="model",
        source_value=parsed_profile.get("model_display"),
        field_name="model_display",
    )
    engine_model = _clean_web_enrichment_engine_model(parsed_profile.get("engine_model"))
    _apply_web_enrichment_field(
        merged,
        enriched_fields,
        fallback_profile=fallback_profile,
        target_key="model_year",
        source_value=parsed_profile.get("production_year"),
        field_name="production_year",
    )
    _apply_web_enrichment_field(
        merged,
        enriched_fields,
        fallback_profile=fallback_profile,
        target_key="engine_model",
        source_value=engine_model or parsed_profile.get("engine_model"),
    )
    _apply_web_enrichment_field(
        merged,
        enriched_fields,
        fallback_profile=fallback_profile,
        target_key="engine_power_hp",
        source_value=parsed_profile.get("engine_power_hp"),
    )
    gearbox_model = parsed_profile.get("gearbox_model")
    gearbox_type = parsed_profile.get("gearbox_type")
    _apply_web_enrichment_field(
        merged,
        enriched_fields,
        fallback_profile=fallback_profile,
        target_key="gearbox_model",
        source_value=gearbox_model,
    )
    if gearbox_type:
        _apply_web_enrichment_field(
            merged,
            enriched_fields,
            fallback_profile=fallback_profile,
            target_key="transmission",
            source_value=gearbox_type,
            field_name="gearbox_type",
        )
    elif gearbox_model:
        _apply_web_enrichment_field(
            merged,
            enriched_fields,
            fallback_profile=fallback_profile,
            target_key="transmission",
            source_value=gearbox_model,
            field_name="gearbox_model",
        )
    _apply_web_enrichment_field(
        merged,
        enriched_fields,
        fallback_profile=fallback_profile,
        target_key="drive_type",
        source_value=parsed_profile.get("drivetrain"),
    )

    if parsed_profile.get("warnings"):
        merged["web_enrichment_warnings"] = list(parsed_profile.get("warnings") or [])
    return merged, enriched_fields


def _build_vin_vehicle_profile_patch(
    *, facts: dict[str, Any], orchestration_payload: dict[str, Any], vin_status: str
) -> dict[str, Any]:
    vehicle_profile_patch: dict[str, Any] = {}
    current_vin = str(facts.get("vin", "") or "").strip().upper()
    if vin_status == "success" and current_vin:
        vehicle_profile_patch["vin"] = current_vin
    _populate_vin_vehicle_profile_fields(vehicle_profile_patch, orchestration_payload)
    source_summary = str(orchestration_payload.get("source_summary", "") or "").strip()
    if source_summary:
        vehicle_profile_patch["source_summary"] = source_summary
    source_confidence = orchestration_payload.get("source_confidence")
    if source_confidence not in (None, ""):
        vehicle_profile_patch["source_confidence"] = normalize_source_confidence(source_confidence)
    source_links = orchestration_payload.get("source_links_or_refs")
    normalized_links = normalize_vehicle_links(source_links)
    if normalized_links:
        vehicle_profile_patch["source_links_or_refs"] = normalized_links
    if not vehicle_profile_patch:
        if source_summary:
            vehicle_profile_patch["source_summary"] = source_summary
        if source_confidence not in (None, ""):
            vehicle_profile_patch["source_confidence"] = normalize_source_confidence(
                source_confidence
            )
        if normalized_links:
            vehicle_profile_patch["source_links_or_refs"] = normalized_links
    if vehicle_profile_patch:
        vehicle_profile_patch["autofilled_fields"] = [
            key
            for key in (
                "vin",
                "make_display",
                "model_display",
                "production_year",
                "engine_model",
                "gearbox_model",
                "drivetrain",
            )
            if key in vehicle_profile_patch
        ]
        vehicle_profile_patch["field_sources"] = {
            key: "vin_web_research" for key in vehicle_profile_patch["autofilled_fields"]
        }
        vehicle_profile_patch["data_completion_state"] = (
            "mostly_autofilled" if vin_status == "success" else "partially_autofilled"
        )
    return vehicle_profile_patch


def _collect_vin_web_lookup_artifacts(
    context: ScenarioContext,
    runtime: object,
    orchestration_payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any], list[dict[str, Any]], int]:
    web_search_payload: dict[str, Any] | None = None
    web_search_data: dict[str, Any] = {}
    excerpt_payloads: list[dict[str, Any]] = []
    web_tool_calls = 0

    web_query = _build_vin_web_query(orchestration_payload)
    web_search_args = {"query": web_query, "limit": 6}
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
    web_search_data = runtime._response_data(web_search_payload) if web_search_payload else {}
    web_results = web_search_data.get("results") if isinstance(web_search_data, dict) else []
    if isinstance(web_results, list):
        ranked_results = [item for item in web_results if isinstance(item, dict)]
        ranked_results.sort(key=_vin_web_result_score, reverse=True)
        for offset, item in enumerate(ranked_results[:4], start=3):
            url = str(item.get("url", "") or "").strip()
            if not url or _vin_web_url_is_blocked(url):
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
                excerpt_data = runtime._response_data(excerpt_payload)
                excerpt_text = str(
                    excerpt_data.get("excerpt", "") if isinstance(excerpt_data, dict) else ""
                ).strip()
                if excerpt_text:
                    break
    return web_search_payload, web_search_data, excerpt_payloads, web_tool_calls


def _vin_web_combined_text_parts(
    runtime: object,
    web_search_data: dict[str, Any],
    excerpt_payloads: list[dict[str, Any]],
) -> list[str]:
    web_results = web_search_data.get("results") if isinstance(web_search_data, dict) else []
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
    return combined_text_parts


def _vin_web_source_urls(web_search_data: dict[str, Any]) -> list[str]:
    web_results = web_search_data.get("results") if isinstance(web_search_data, dict) else []
    return [
        str(item.get("url", "") or "").strip()
        for item in (web_results if isinstance(web_results, list) else [])
        if isinstance(item, dict)
        and str(item.get("url", "") or "").strip()
        and not _vin_web_url_is_blocked(str(item.get("url", "") or "").strip())
    ][:4]


def _apply_vin_web_enrichment_updates(
    *,
    context: ScenarioContext,
    runtime: object,
    facts: dict[str, Any],
    orchestration_payload: dict[str, Any],
    fallback_profile: dict[str, Any] | None,
    web_search_data: dict[str, Any],
    excerpt_payloads: list[dict[str, Any]],
    vin_status: str,
) -> tuple[dict[str, Any], str]:
    combined_text_parts = _vin_web_combined_text_parts(runtime, web_search_data, excerpt_payloads)
    parsed_profile = runtime._parse_vehicle_profile_text(
        "\n".join(combined_text_parts),
        explicit_vehicle=" ".join(
            part
            for part in (
                str(
                    (facts.get("related_vehicle_profile") or {}).get("make_display", "")
                    or orchestration_payload.get("make", "")
                    or ""
                ).strip(),
                str(
                    (facts.get("related_vehicle_profile") or {}).get("model_display", "")
                    or orchestration_payload.get("model", "")
                    or ""
                ).strip(),
                str(
                    (facts.get("related_vehicle_profile") or {}).get("production_year", "")
                    or orchestration_payload.get("model_year", "")
                    or ""
                ).strip(),
            )
            if part
        ),
    )
    enriched_payload, enriched_fields = _merge_web_enrichment(
        orchestration_payload,
        parsed_profile,
        fallback_profile=fallback_profile,
    )
    if enriched_fields or fallback_profile:
        enriched_payload["web_source_urls"] = _vin_web_source_urls(web_search_data)
        orchestration_payload = enriched_payload
        facts["vehicle_context"] = runtime._merge_vehicle_context(
            facts["vehicle_context"],
            orchestration_payload,
        )
        if enriched_fields:
            enriched_payload["web_enrichment_fields"] = enriched_fields
            facts["vin_web_enrichment_used"] = True
            facts["vin_web_enrichment_fields"] = list(enriched_fields)
            vin_status = "success"
            facts["vin_decode_status"] = vin_status
            runtime._record_log_action(
                task_id=context.task_id,
                run_id=context.run_id,
                step=max(3, 2 + len(excerpt_payloads)),
                level="INFO",
                phase="analysis",
                message="VIN web enrichment produced additional confirmed facts.",
            )
    return orchestration_payload, vin_status


def _build_vin_vehicle_label(orchestration_payload: dict[str, Any]) -> str:
    vehicle_label = str(orchestration_payload.get("vehicle_label", "") or "").strip()
    if vehicle_label:
        return vehicle_label
    parts = [
        str(orchestration_payload.get("make", "") or "").strip(),
        str(orchestration_payload.get("model", "") or "").strip(),
        str(orchestration_payload.get("model_year", "") or "").strip(),
    ]
    return " ".join(part for part in parts if part).strip()


def _build_vin_description_line(orchestration_payload: dict[str, Any], *, vin_status: str) -> str:
    description_line = str(orchestration_payload.get("description_line", "") or "").strip()
    if description_line:
        return (
            description_line
            if description_line.endswith((".", "!", "?"))
            else f"{description_line}."
        )
    summary_bits = [
        str(orchestration_payload.get(key, "") or "").strip()
        for key in (
            "make",
            "model",
            "model_year",
            "engine_model",
            "transmission",
            "drive_type",
        )
        if str(orchestration_payload.get(key, "") or "").strip()
    ]
    if not summary_bits:
        return ""
    prefix = (
        "По VIN подтверждено: "
        if vin_status == "success"
        else "По VIN выполнено best-effort исследование: "
    )
    summary = prefix + ", ".join(summary_bits[:4])
    return summary if summary.endswith((".", "!", "?")) else f"{summary}."


def _populate_vin_vehicle_profile_fields(
    vehicle_profile_patch: dict[str, Any], orchestration_payload: dict[str, Any]
) -> None:
    for field_name, payload_key in (
        ("make_display", "make"),
        ("model_display", "model"),
        ("production_year", "model_year"),
        ("engine_model", "engine_model"),
        ("gearbox_model", "transmission"),
        ("drivetrain", "drive_type"),
    ):
        value = str(orchestration_payload.get(payload_key, "") or "").strip()
        if not value:
            continue
        if field_name == "production_year":
            parsed_year = normalize_vehicle_int(value)
            if parsed_year is None:
                continue
            vehicle_profile_patch[field_name] = parsed_year
        else:
            vehicle_profile_patch[field_name] = value


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
        scenario_patch = self._build_card_patch(
            facts=facts, orchestration_payload=orchestration_payload, vin_status=vin_status
        )
        if isinstance(facts.get("evidence_model"), dict):
            facts["evidence_model"]["external_result_sufficient"] = vin_status == "success"
        if vin_status in {"success", "insufficient"}:
            facts["vehicle_context"] = runtime._merge_vehicle_context(
                facts["vehicle_context"],
                orchestration_payload,
            )
            fallback_profile = (
                facts.get("related_vehicle_profile")
                if isinstance(facts.get("related_vehicle_profile"), dict)
                else None
            )
            web_tool_calls = 0
            web_lookup_attempted = False
            if runtime._vin_web_enrichment_required(orchestration_payload):
                web_lookup_attempted = True
                facts["vin_web_enrichment_used"] = False
                (
                    web_search_payload,
                    web_search_data,
                    excerpt_payloads,
                    web_tool_calls,
                ) = _collect_vin_web_lookup_artifacts(context, runtime, orchestration_payload)
                orchestration_payload, vin_status = _apply_vin_web_enrichment_updates(
                    context=context,
                    runtime=runtime,
                    facts=facts,
                    orchestration_payload=orchestration_payload,
                    fallback_profile=fallback_profile,
                    web_search_data=web_search_data,
                    excerpt_payloads=excerpt_payloads,
                    vin_status=vin_status,
                )
        else:
            web_tool_calls = 0
            web_lookup_attempted = False
            web_search_payload = None
            web_search_data = {}
            excerpt_payloads = []
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
            patch=scenario_patch,
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

    def _build_card_patch(
        self, *, facts: dict[str, Any], orchestration_payload: dict[str, Any], vin_status: str
    ) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        vehicle_profile_patch = _build_vin_vehicle_profile_patch(
            facts=facts, orchestration_payload=orchestration_payload, vin_status=vin_status
        )
        if vehicle_profile_patch:
            patch["vehicle_profile"] = vehicle_profile_patch

        vehicle_label = _build_vin_vehicle_label(orchestration_payload)
        if vehicle_label:
            patch["vehicle"] = vehicle_label

        description_line = _build_vin_description_line(orchestration_payload, vin_status=vin_status)
        if description_line:
            patch["description"] = description_line
        if not patch:
            source_summary = str(orchestration_payload.get("source_summary", "") or "").strip()
            if source_summary:
                patch["description"] = f"VIN research: {source_summary[:220].rstrip()}"
            else:
                patch["description"] = (
                    "VIN research completed; source output was sparse."
                    if vin_status == "success"
                    else "VIN research completed in best-effort mode."
                )
        return patch
