# AutoStop CRM MCP Guide

## Purpose

The public Gateway v2 is a compact decision surface, not a script. It exposes
exactly 24 tools for 46 CRM workflow operations. Use whichever relevant
CRM, Store, or sanctioned conversation context best clarifies the request;
there is no mandatory bootstrap, read, contract, dry-run, or response order.
A VIN, article, photo, part name, or short reply can justify a bounded quote
lookup when its available context is sufficient.

## Endpoint And Authentication

- Production endpoint: `https://crm.autostopcrm.ru/mcp`.
- Public anonymous writes must remain blocked; anonymous reads are blocked too.
- ChatGPT/Codex uses owner-approved OAuth 2.1 with authorization code, PKCE,
  explicit administrator approval, scope/audience checks, and rotating refresh
  tokens. See [connector setup](CHATGPT_CONNECTOR_SETUP.md).
- `MINIMAL_KANBAN_MCP_ALLOWED_HOSTS` and
  `MINIMAL_KANBAN_MCP_ALLOWED_ORIGINS` are explicit transport allowlists, not
  permissive fallbacks.

## Public Gateway Surface

The public 24-tool surface contains these stable names:

- Context and discovery: `agent_bootstrap`, `agent_search`,
  `agent_entity_context`, `agent_board_digest`, `get_connector_identity`,
  `get_runtime_status`, `ping_connector`, `discover_raw_capabilities`, and
  `get_raw_capability_schema`.
- Workflow choice and progress: `start_workflow`, `workflow_transition`,
  `workflow_checkpoint`, `workflow_status`, `workflow_resume`,
  `workflow_wait_for_external`, `workflow_cancel`,
  `complete_external_step`, and `list_agent_workflows`.
- Business workflow entrypoints: `agent_board_workflow`,
  `agent_document_workflow`, `agent_finance_workflow`,
  `agent_inventory_workflow`, `prepare_action_contract`, and
  `call_raw_capability`.

Use only enough calls to establish the target and perform the useful next step.
A workflow can be helpful for an auditable multi-step task, but is not required
for simple context, analysis, or a normal customer reply.

## Telegram behavior diagram edits

The diagram at `/telegram-agent-behavior` is a shared CRM record, not executable
Telegram-agent configuration. Read it with `api:/api/get_telegram_agent_behavior`;
the response contains the graph, its numeric revision, and the last 20 save
summaries, without customer cards. Use `allow_large_output=true` when the full
graph exceeds the Gateway's compact response limit.

Codex edits this record through the hidden
`api:/api/patch_telegram_agent_behavior` capability exposed by the existing
`call_raw_capability` tool. Discover its current schema and hash first. A patch
contains `expected_revision`, an `idempotency_key`, and one to 50 ordered
operations: `add_element` with `element`, `update_element` with `id` and
`changes`, `delete_element` with `id`, the equivalent three `*_relation`
operations, or `resize_canvas` with `width` and `height`. The Gateway key and
patch key identify the same write. The server validates the resulting graph and
returns `409` for a stale revision; reread and reconcile before retrying.

Only a trusted Gateway call with a verified signed OAuth subject matching
`AUTOSTOP_TELEGRAM_BEHAVIOR_OWNER_LOGIN` may patch. A static service token or
caller-supplied `actor_name` cannot acquire this permission. After a successful
patch, reread the graph and exact affected IDs/revision. Browser edits by the
owner continue to use the existing full-graph save; both clients share one
revision and recent-change history. Content edits need no Git or server release.

### Manager structure through raw capabilities

The two virtual capabilities `api:/api/manager_structure` (read) and
`api:/api/manager_structure/apply` (owner write) expose the same constructor
service as the CRM browser. Discover each exact name, inspect its schema and
hash, then call `call_raw_capability`. For writes, provide `expected_version`
from the read, an `idempotency_key` for the outer raw call, and the operation
arguments described in [the API guide](API_GUIDE.md#manager-structure-constructor).
The Gateway binds the inner API key to the outer key and verifies the result
with a fresh diagram read. Reread the diagram after each write before using
the next version. This tool stores instructions; no agent workflow consumes
their text.

For geometry or endpoint changes, use `layout_element` or `layout_relation`.
The CRM calculates the saved paths; reread the accepted geometry because the
requested position may be adjusted. The Gateway compares `accepted_element`
and every calculated `routes` path with the persisted diagram.

## Finance Reads

`list_cashboxes`, `get_cashbox`, `get_cash_journal`, and `get_repair_order`
are read-only. Omit `mode`, or pass `mode="dry_run"` to make the non-mutating
intent explicit; both execute the real read. A finance read never returns or
accepts a preview proof, and `mode="apply"` is invalid. Finance writes retain
their preview-and-proof guard.

## Store Boundary And Quote Context

Store adapter remains internal. `store_owner_capabilities` and `store_owner_api`
are mounted owner capabilities behind the public 24-tool surface; callers use
the named Gateway workflows. The generic `store_owner_api` transport is an
internal dependency and is excluded from public raw discovery and execution.
Customer estimate operations use `agent_inventory_workflow` with
`operation="store_quote_conductor"`; its typed phases preserve the underlying
revision, idempotency and action-proof checks for Store writes.

Bounded Store discovery can provide relevant context across `store_part`,
`store_quote_request`, `store_sourcing_offer`, `store_order`,
`store_marketplace_listing`, `store_batch`, `store_warehouse_operation`, and
`store_state`. It supplements CRM context; it does not replace judgment or make
a fixed sales script.

Store management operations are `assign_quote_request`,
`update_quote_request_comment`, `add_quote_request_note`,
`set_quote_request_status`, `mark_order_ready`, `set_order_payment_status`, and
`set_batch_storage_location`. Draft/context actions may be useful without a
ceremony. Publishing a customer price or creating/advancing a real order must
pass the native impact guard with explicit authority and an exact target.

`set_order_payment_status` accepts only a boolean `planned_changes.paid` and
the current separate financial instruction as
`owner_intent="owner_finance: <nonempty instruction description>"` (500 characters
maximum). Preserve the same owner intent, target, revision, correlation and
planned changes for native dry-run/apply; the Store proof lasts 1800 seconds.
No extra proof identifier is required. Only `payment_status` and `paid_at`
change, with no external effects; apply requires native verified full readback.

`download_store_quote_vin_photo` returns PII-redacted references. Require
`expected_photo_sha256` when integrity matters; use `allow_large_output=true`
only when the requested evidence genuinely needs it.

## Real-Impact Boundaries

Native action checks protect money, published customer prices, orders,
deletion/archive, new external recipients, deployment, and secrets. Check them
when executing the impact, rather than forcing a tool sequence before thought,
research, drafting, or ordinary CRM updates. Preserve authorization, recipient,
revision, idempotency, and receipt checks where the underlying action needs
them.

## Verification And Change Work

Run focused unit/API/MCP checks for the changed boundary. The release-level
Gateway probe is `scripts/check_agent_gateway_v2.py --exhaustive`; use it for a
release or compatible surface change, not as a prerequisite for every query.
Deployment remains an explicit owner decision under the
[operations runbook](docs/OPERATIONS_RUNBOOK.md).
