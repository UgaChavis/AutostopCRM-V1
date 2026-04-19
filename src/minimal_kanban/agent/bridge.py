from __future__ import annotations

from typing import Any

from ..vehicle_profile import VEHICLE_ALL_FIELDS

BRIDGE_PURPOSE = "card_enrichment"
BRIDGE_ALLOWED_TOP_LEVEL_PATCH_KEYS = ("description", "vehicle", "vehicle_profile")
BRIDGE_ALLOWED_VEHICLE_PROFILE_KEYS = VEHICLE_ALL_FIELDS
BRIDGE_ALLOWED_STATUS_VALUES = ("queued", "running", "needs_review", "completed", "failed")


def normalize_card_enrichment_patch(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    patch: dict[str, Any] = {}
    if "description" in payload and payload.get("description") not in (None, ""):
        patch["description"] = payload.get("description")
    if "vehicle" in payload and payload.get("vehicle") not in (None, ""):
        patch["vehicle"] = payload.get("vehicle")
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
    for key in BRIDGE_ALLOWED_VEHICLE_PROFILE_KEYS:
        if key not in payload:
            continue
        value = payload.get(key)
        if value in (None, [], {}):
            continue
        normalized[key] = value
    return normalized
