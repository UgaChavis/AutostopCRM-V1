"""Canonical requested fields for independent create-operation readback."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import fields
from typing import Any

from ..models import ClientProfile, VehicleProfile
from .agent_gateway_support import _subset_matches

_CLIENT_STORED_FIELDS = frozenset(field.name for field in fields(ClientProfile))


def card_requested_fields(arguments: dict[str, Any]) -> dict[str, Any]:
    # Match CardService's validated title/vehicle/description without altering
    # the request, its fingerprint, or the independent persisted-state check.
    requested: dict[str, Any] = {}
    if "title" in arguments:
        requested["title"] = str(arguments["title"] or "").strip()
    if "description" in arguments:
        value = arguments["description"]
        requested["description"] = (
            str(value if value is not None else "").replace("\r\n", "\n").replace("\r", "\n")
        )
    if "vehicle" in arguments:
        vehicle = " ".join(str(arguments["vehicle"] or "").split())
        if not vehicle:
            profile = VehicleProfile.from_dict(arguments.get("vehicle_profile"))
            vehicle = " ".join(profile.display_name().split())
        requested["vehicle"] = vehicle
    return requested


def client_requested_fields(arguments: dict[str, Any]) -> dict[str, Any]:
    raw = arguments.get("client")
    if not isinstance(raw, dict):
        return {}
    # ClientProfile is the same domain normalizer used by create_client.
    # Select only supplied fields: generated IDs and timestamps are instead
    # verified against the mutation response by the separate persisted check.
    try:
        canonical = ClientProfile.from_dict(raw).to_dict()
    except (TypeError, ValueError):
        # Failed executor validation still reaches verification; retain its
        # structured rejection instead of raising while inspecting bad input.
        return raw
    requested = {key: canonical.get(key, value) for key, value in raw.items() if value is not None}
    if "vehicles" in requested and isinstance(raw["vehicles"], list):
        explicit_ids = {
            str(vehicle.get("id") or "").strip()
            for vehicle in raw["vehicles"]
            if isinstance(vehicle, dict)
        }
        requested["vehicles"] = [
            {key: value for key, value in vehicle.items() if key != "id" or value in explicit_ids}
            for vehicle in canonical["vehicles"]
        ]
    return requested


async def verify_created_client_state(
    result: dict[str, Any],
    invoke: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Verify the wide API contract using its complete persisted response.

    The API accepts legacy aliases, nested payloads, explicit IDs and duplicate
    matches. Its returned profile is authoritative for those transformations;
    successful persistence still requires an independent read of that exact ID.
    """
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    created = data.get("client") if isinstance(data.get("client"), dict) else {}
    client_id = str(created.get("id") or "").strip()
    readback = await invoke("get_client", {"client_id": client_id}) if client_id else {}
    read_data = readback.get("data") if isinstance(readback.get("data"), dict) else {}
    actual = read_data.get("client") if isinstance(read_data.get("client"), dict) else {}
    complete_state = _CLIENT_STORED_FIELDS.issubset(created)
    persisted = {key: created[key] for key in _CLIENT_STORED_FIELDS if key in created}
    persisted_exact = complete_state and _subset_matches(persisted, actual)
    return {
        "required": True,
        "passed": bool(result.get("ok") and client_id and readback.get("ok") and persisted_exact),
        "check": "exact_created_client_state_readback",
        "evidence": {
            "client_id": client_id,
            "complete_persisted_state": complete_state,
            "persisted_state_exact": persisted_exact,
            "readback_ok": bool(readback.get("ok")),
        },
    }
