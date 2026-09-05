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

## Store Boundary And Quote Context

Store adapter remains internal. `store_owner_capabilities` and `store_owner_api`
are mounted owner capabilities behind the public 24-tool surface; callers use
the Gateway rather than treating the Store as a second public MCP server.

Bounded Store discovery can provide relevant context across `store_part`,
`store_quote_request`, `store_sourcing_offer`, `store_order`,
`store_marketplace_listing`, `store_batch`, `store_warehouse_operation`, and
`store_state`. It supplements CRM context; it does not replace judgment or make
a fixed sales script.

Store management operations are `assign_quote_request`,
`update_quote_request_comment`, `add_quote_request_note`,
`set_quote_request_status`, `mark_order_ready`, and
`set_batch_storage_location`. Draft/context actions may be useful without a
ceremony. Publishing a customer price or creating/advancing a real order must
pass the native impact guard with explicit authority and an exact target.

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