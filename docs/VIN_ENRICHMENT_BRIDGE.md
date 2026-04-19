# VIN Enrichment Bridge

This document fixes the CRM-side contract for the card enrichment worker.

## Purpose

The green button in the card modal launches a single card enrichment flow:

`button -> enqueue task -> get_card_context -> VIN research -> compact patch -> update_card`

The worker reads the card, finds VIN, performs web research, normalizes confirmed facts, and writes back a compact patch.

## CRM Read Path

Source of truth:

- `POST /api/get_card_context`

The worker must call this first and treat the result as authoritative.

Returned context includes:

- `card`
- `events`
- `attachments`
- `removed_attachments`
- `repair_order_text`
- `board_context`
- `meta`

## CRM Write Path

Single write path:

- `POST /api/update_card`

Allowed payload fields for this flow:

- `description`
- `vehicle`
- `vehicle_profile`

There is no separate public `update_vehicle_profile` endpoint.
The `vehicle_profile` patch is applied through `update_card`.

## Task Payload

Minimal task payload accepted by the bridge:

```json
{
  "task_id": "agtask_123",
  "card_id": "card_123",
  "purpose": "card_enrichment",
  "trigger": "button",
  "requested_by": "crm_ui",
  "task_text": "optional",
  "card_context": "optional_hint_only"
}
```

Rules:

- `card_id` is required.
- `purpose` must be `card_enrichment`.
- `card_context` is a hint only.
- the worker must still call `get_card_context(card_id)`.

## Response Payload

Minimal worker response:

```json
{
  "task_id": "agtask_123",
  "card_id": "card_123",
  "status": "completed",
  "summary": "short result",
  "patch": {
    "description": "...",
    "vehicle": "...",
    "vehicle_profile": {}
  },
  "warnings": [],
  "sources": [],
  "needs_review": false
}
```

Status values:

- `queued`
- `running`
- `needs_review`
- `completed`
- `failed`

## Vehicle Profile Rules

Only confirmed facts belong in `vehicle_profile`.

The bridge accepts the full `VehicleProfile` shape defined in
`src/minimal_kanban/vehicle_profile.py` and passes known fields through to CRM
without adding another VIN-specific allowlist or text trimming layer.
Unknown keys are dropped at the bridge boundary.

The CRM storage layer still performs its own model-level normalization for the
incoming profile, including helper fields such as `raw_input_text`,
`warnings`, `manual_fields`, and `autofilled_fields`.

Do not touch in this flow:

- `repair_order`
- parts
- DTC
- maintenance

## Current CRM Entry Points

The CRM already exposes the integration surface needed by the worker:

- `POST /api/run_full_card_enrichment`
- `POST /api/agent_enqueue_task`
- `GET /api/agent_status`
- `GET /api/agent_tasks`
- `GET /api/agent_actions`
- `GET /api/agent_scheduled_tasks`

The UI button uses the full-card enrichment entrypoint, while the agent runtime uses the shared task storage and CRM write path.

## Implementation Note

If data quality is weak, the worker should return `needs_review` instead of writing speculative values.
