from __future__ import annotations

from typing import Any

from ..vehicle_profile import (
    normalize_completion_state,
    normalize_source_confidence,
    normalize_vehicle_field_names,
    normalize_vehicle_field_sources,
    normalize_vehicle_links,
    normalize_vehicle_notes,
    normalize_vehicle_text,
    soft_normalize_vin,
)

BRIDGE_PURPOSE = "card_enrichment"
BRIDGE_ALLOWED_TOP_LEVEL_PATCH_KEYS = ("description", "vehicle", "vehicle_profile")
BRIDGE_ALLOWED_VEHICLE_PROFILE_KEYS = (
    "vin",
    "make_display",
    "model_display",
    "production_year",
    "engine_model",
    "gearbox_model",
    "drivetrain",
    "source_summary",
    "source_confidence",
    "source_links_or_refs",
    "autofilled_fields",
    "field_sources",
    "data_completion_state",
    "oem_notes",
)
BRIDGE_ALLOWED_STATUS_VALUES = ("queued", "running", "needs_review", "completed", "failed")


def normalize_card_enrichment_patch(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    patch: dict[str, Any] = {}
    description = normalize_vehicle_notes(payload.get("description"))
    if description:
        patch["description"] = description
    vehicle = normalize_vehicle_text(payload.get("vehicle"), limit=120)
    if vehicle:
        patch["vehicle"] = vehicle
    vehicle_profile = (
        payload.get("vehicle_profile") if isinstance(payload.get("vehicle_profile"), dict) else {}
    )
    normalized_profile = _normalize_vehicle_profile_patch(vehicle_profile)
    if normalized_profile:
        patch["vehicle_profile"] = normalized_profile
    return patch


def _normalize_vehicle_profile_patch(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    normalized: dict[str, Any] = {}
    vin = soft_normalize_vin(payload.get("vin"))
    if vin:
        normalized["vin"] = vin
    make_display = normalize_vehicle_text(payload.get("make_display"), limit=80)
    if make_display:
        normalized["make_display"] = make_display
    model_display = normalize_vehicle_text(payload.get("model_display"), limit=80)
    if model_display:
        normalized["model_display"] = model_display
    production_year = payload.get("production_year")
    if production_year not in (None, ""):
        year_text = str(production_year).strip()
        if year_text.isdigit():
            normalized["production_year"] = int(year_text)
        else:
            normalized["production_year"] = year_text[:8]
    engine_model = normalize_vehicle_text(payload.get("engine_model"), limit=120)
    if engine_model:
        normalized["engine_model"] = engine_model
    gearbox_model = normalize_vehicle_text(payload.get("gearbox_model"), limit=120)
    if gearbox_model:
        normalized["gearbox_model"] = gearbox_model
    drivetrain = normalize_vehicle_text(payload.get("drivetrain"), limit=80)
    if drivetrain:
        normalized["drivetrain"] = drivetrain
    source_summary = normalize_vehicle_text(payload.get("source_summary"), limit=120)
    if source_summary:
        normalized["source_summary"] = source_summary
    source_confidence = normalize_source_confidence(payload.get("source_confidence"))
    if source_confidence > 0:
        normalized["source_confidence"] = source_confidence
    source_links = normalize_vehicle_links(payload.get("source_links_or_refs"))
    if source_links:
        normalized["source_links_or_refs"] = source_links
    autofilled_fields = normalize_vehicle_field_names(payload.get("autofilled_fields"))
    if autofilled_fields:
        normalized["autofilled_fields"] = autofilled_fields
    field_sources = normalize_vehicle_field_sources(payload.get("field_sources"))
    if field_sources:
        normalized["field_sources"] = field_sources
    if "data_completion_state" in payload:
        data_completion_state = normalize_completion_state(payload.get("data_completion_state"))
        if data_completion_state:
            normalized["data_completion_state"] = data_completion_state
    oem_notes = normalize_vehicle_notes(payload.get("oem_notes"))
    if oem_notes:
        normalized["oem_notes"] = oem_notes
    return normalized
