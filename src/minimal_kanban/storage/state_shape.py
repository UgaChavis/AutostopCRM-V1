"""Reject destructive structural repairs while retaining legacy field normalization."""

from __future__ import annotations

STATE_RECORD_COLLECTIONS = frozenset(
    {
        "columns",
        "cards",
        "clients",
        "stickies",
        "cashboxes",
        "cash_transactions",
        "inventory_items",
        "inventory_movements",
        "events",
    }
)


def validate_state_shape(payload: object) -> None:
    """Missing legacy domains are allowed; present records must not be discarded.

    Validate before normalization or change-feed reconciliation, both of which
    otherwise interpret a malformed collection as an intentional deletion.
    Values inside valid records retain their existing migration behavior.
    """
    if not isinstance(payload, dict):
        raise ValueError("state file must contain an object")
    for name in STATE_RECORD_COLLECTIONS & payload.keys():
        records = payload[name]
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise ValueError(f"state file collection is malformed: {name}")
    if "settings" in payload and not isinstance(payload["settings"], dict):
        raise ValueError("state file settings are malformed")
