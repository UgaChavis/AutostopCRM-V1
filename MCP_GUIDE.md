# AutoStop CRM MCP / Agent Gateway v2

The MCP endpoint exposes one current AutoStop CRM board through the same HTTP
API and services as the browser:

```text
MCP client -> MCP adapter -> local HTTP API -> domain services -> JsonStore
```

Source of truth:

- `src/minimal_kanban/mcp/server.py` — raw tool implementations;
- `src/minimal_kanban/mcp/tool_registry.py` — raw CRM registry;
- `src/minimal_kanban/mcp/agent_gateway_v2.py` — production surface;
- `scripts/check_agent_gateway_v2.py` — exact release contract;
- live `tools/list`.

## Runtime And Authentication

- Local: `http://127.0.0.1:41831/mcp`
- Production: `https://crm.autostopcrm.ru/mcp`
- Entrypoint: `main_mcp.py`
- Local launcher: `.\scripts\run_mcp_server.ps1`

Relevant transport settings include `MINIMAL_KANBAN_MCP_HOST`,
`MINIMAL_KANBAN_MCP_PORT`, `MINIMAL_KANBAN_MCP_PATH`,
`MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL`,
`MINIMAL_KANBAN_MCP_BEARER_TOKEN`,
`MINIMAL_KANBAN_MCP_ALLOWED_HOSTS`, and
`MINIMAL_KANBAN_MCP_ALLOWED_ORIGINS`. The last two extend the transport host
and origin allowlists; they do not replace authentication.

Production is fail-closed and uses owner-approved OAuth 2.1 authorization code
flow with PKCE S256, dynamic client registration, short-lived access tokens,
rotating refresh tokens, exact `https://crm.autostopcrm.ru/mcp` audience, and
the complete `kanban:read kanban:write` scope set. OAuth client/token state is
encrypted at rest and persists in the CRM data volume; the encryption key is a
protected deployment setting. Anonymous reads and writes are rejected.

`AUTOSTOP_MCP_OAUTH_ENABLED=1` is mandatory in production and the former
auto-approved development mode remains explicitly disabled with
`AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED=0`. The deployment-rotated bearer is
accepted only for internal release checks and explicitly authorized Responses
API calls; Codex and Apps do not store or depend on it. A standards-compliant
401 includes protected-resource metadata so clients can authorize or refresh
instead of surfacing a generic MCP internal error. See
[the client setup](CHATGPT_CONNECTOR_SETUP.md).

## Permanent Production Surface

Production advertises exactly 24 tools:

- diagnostics (3): `ping_connector`, `get_connector_identity`,
  `get_runtime_status`;
- context and domains (8): `agent_bootstrap`, `agent_board_digest`,
  `agent_search`, `agent_entity_context`, `agent_board_workflow`,
  `agent_finance_workflow`, `agent_inventory_workflow`,
  `agent_document_workflow`;
- lazy raw access (3): `discover_raw_capabilities`,
  `get_raw_capability_schema`, `call_raw_capability`;
- managed workflow lifecycle (10): `list_agent_workflows`,
  `prepare_action_contract`, `start_workflow`, `workflow_status`,
  `workflow_transition`, `workflow_checkpoint`,
  `workflow_wait_for_external`, `complete_external_step`,
  `workflow_resume`, and `workflow_cancel`.

Low-level board, client, repair-order, inventory, finance, file, operator, and
manager functions remain in the raw registry but are not advertised directly.
Do not call a hidden legacy name from an external agent.

The raw board registry includes `start_card_timer` and `stop_card_timer`.
`create_card` leaves the timer inactive when `deadline` is omitted; an explicit
positive deadline starts it. Restarting without a deadline reuses the saved
duration. Timer-only actions are audited but do not flag the card as unseen
content for other operators.

The mounted Manager contributes five `INTERNAL_ONLY` store adapter tools:
`store_runtime_status`, `store_digest`, `store_search`,
`store_entity_context`, and `store_management_action`. Gateway captures all
five before hiding the raw registry. They are deliberately absent from raw
discovery/schema/call: store reads use the named context tools and store writes
use only `agent_inventory_workflow`.

Store coverage is additive to the existing schemas:

- `agent_bootstrap` includes one compact stateless Store snapshot from a
  single Store request. It has no Store cursor/ACK, does not read the change
  feed, and does not touch `store_digest`;
- `agent_board_digest(scope="store")` uses the durable `store_digest` stream
  and accepts the returned `cursor`/`ack_token` pair;
- `agent_search` supports `store_part`, `store_order`,
  `store_quote_request`, `store_supplier`, `store_batch`,
  `store_warehouse_operation`, `store_marketplace_listing`, and
  `store_state`;
- `agent_entity_context` reads one exact Store entity with `detail="summary"`
  or `detail="full"`; Store PII remains redacted because the service identity
  has no contact scope;
- `get_runtime_status` performs a live Store health probe. Store failure makes
  the Store section `degraded` while a healthy CRM remains usable;
- `agent_inventory_workflow` keeps its old CRM default (`mode` omitted means
  apply) but requires explicit `dry_run` or `apply` for Store actions.

Every non-empty Store digest page, including the final source page, has
`page.ack_required=true`. Pass its exact opaque Manager cursor and ACK token to
the same public tool. Intermediate ACK returns the next page; final ACK returns
an empty terminal page with `next_cursor=null` and only then commits durable
high-water. Repeating an unacknowledged page or final ACK is idempotent. Raw App
checkpoint/replay cursors are never public.

The bootstrap snapshot contains Store API readiness, product/active-order/open
quote counts, aggregate inventory, marketplace state, Store contract version,
and a safe export-error report (24 hours, 7 days, all time, latest five). Error
rows contain only time, fixed error code, part/account refs, and attempt count;
provider messages, payloads, credentials, and contacts are never returned.

## Gateway Guarantees

Gateway responses use `agent_envelope_v2` and compact verification evidence.

- Raw use is a three-step sequence: discover, fetch the current schema, then
  call with the route-bound `schema_hash`.
- Writes require the applicable policy switch, a unique idempotency key, and a
  durable workflow ledger. A missing ledger fails closed.
- Named workflows own and close that ledger automatically. Do not wrap one
  `agent_board_workflow` call in a second manual `start_workflow`; use a parent
  workflow only for a genuinely multi-operation or cross-system request.
- Named workflow records and responses preserve the requested `mode` and
  `dry_run` value so `workflow_status` cannot describe a preview as an apply.
- Applied writes are reread. If the change occurred but verification is
  uncertain, the workflow enters `compensating`; it is not reported as a clean
  retryable failure.
- Workflow lifecycle changes use state-version compare-and-swap.
- Finance writes require current revision evidence where the operation defines
  it.
- Agent mutations use the audited `codex-owner-agent` service identity;
  caller-supplied human actor names do not override it.
- Operator-admin raw routes additionally require the local service identity
  and matching bearer token; public proxy traffic cannot claim that identity.
- Exact UI/backend reads for AI chat knowledge, board revision, display
  dashboard, inspection form, repair-order print workspace, and employees are
  guarded virtual `api:/api/...` capabilities classified as reads. They do not
  open a write ledger. `set_card_ai_autofill` and audited `open_card` remain
  guarded writes with idempotency plus exact card/activity readback.
- The durable CRM feed is available only through raw discovery as
  `api:/api/change_feed/bootstrap`, `api:/api/change_feed/read`, and
  `api:/api/change_feed/ack`; it adds no public tool. `read` is a bounded read
  (maximum 25 complete events), while `bootstrap` and `ack` use the guarded
  write path with an idempotency key, ledger record, and exact durable
  checkpoint readback. Feed cursors and ACKs remain opaque.
- During maintenance, write routes return `503 maintenance_mode` while
  diagnostics and reads remain available.
- The five Store adapter tools cannot be invoked through the raw escape hatch;
  attempts return `named_operation_required` or `named_workflow_required`.
- Store outage, missing credentials, or malformed Store configuration degrades
  only Store context. CRM startup, board reads, and CRM workflows remain
  independent.

Production kill switches are:

- `AUTOSTOP_AGENT_GATEWAY_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED`

All six must be explicitly `0` or `1`. Disabling the master switch leaves only
diagnostics; it never reveals the legacy surface. A change takes effect after
recreating the CRM container.

## Call Order

1. `agent_bootstrap`
2. `agent_board_digest` for board-wide scope
3. `agent_search` and `agent_entity_context` for exact targets
4. `prepare_action_contract` for every intended mutation
5. the narrow board, finance, inventory, or document workflow in `dry_run`,
   then `apply`
6. exact-target reread plus `workflow_status`/verification evidence
7. raw discovery only when no named workflow covers the request

For a Store digest, finish the cursor/ACK loop before treating “what is new” as
consumed. Only `agent_board_digest(scope="store")` participates in that
ACK/replay/CAS protocol; `agent_bootstrap` is stateless.

For Store writes, `agent_inventory_workflow` permits exactly:
`assign_quote_request`, `set_quote_request_status`,
`update_quote_request_comment`, `set_batch_storage_location`, and
`mark_order_ready`. Supply exact `target_id`, current `expected_updated_at`,
`owner_intent`, `planned_changes`, a unique idempotency key, and explicit
`mode`. Dry-run and apply use different idempotency keys but the same stable
`correlation_id`. If omitted, Gateway derives it from operation, exact target,
effective revision, and canonical changes; mode, key, and owner wording do not
affect it. An explicit value is preserved only when it passes the Store format
validation.

Gateway creates a compact ledger run with `scope.domain=store` and
`scope.source=store`, then performs the exact revision preflight and closes a
failed preflight without invoking Store. Successful actions are reread through
the App-shaped DTO: assignments verify `assigned_user_id`; comments verify
`has_internal_comment` plus the canonical `internal_comment_sha256`, never raw
comment text; comment and READY actions retain their two-field change
envelopes. READY closes only when its notifier state is `SENT` or
`NOT_APPLICABLE`; `CLAIMED` and `FAILED` keep the core-applied run in
`compensating`.

Gateway does not automatically retry a Store POST after an uncertain result.
If the caller repeats the exact same apply request and idempotency key for a
`compensating` run, Gateway explicitly asks Store for the existing receipt,
requires `idempotency_replay=true`, rereads the exact target, and may then close
the ledger. READY dry-runs report bounded notification effects plus
`external_effect_state`, `idempotency_replay`, and `correlation_id`. Gateway
never forwards Store `owner_intent`, idempotency keys, credentials, raw comment
text, or raw metadata in public data.

For the active-card timer floor, use `domain="board"`,
`action="bulk_set_deadline_if_below"`, `target_id="active_cards"`, and planned
changes `include_archived=false`, `min_total_seconds=172800`,
`target_total_seconds=173700`. This collection-scoped contract does not require
a synthetic `expected_revision`. Run the named board workflow once in
`dry_run`, then once in `apply` with a new idempotency key; separate run ids are
expected and both records state their mode.

Use `get_runtime_status` for runtime/auth diagnostics, not as the normal
bootstrap. When the optional AutostopManager package is mounted, its memory,
routing, and ledger capabilities remain behind the same Gateway and raw
discovery; the visible count stays 24. Store traffic uses
`AUTOSTOP_STORE_API_URL` plus separate read/quote/manage/owner service tokens
over the internal `autostop-store-agent` network. The owner credential is
reserved for guarded OpenAPI-bound employee-route parity and does not expand
the public 24-tool surface. Never print these settings' values.

## Write Rules

- Read current context before every write and use confirmed IDs.
- Dry-run broad board work before apply.
- Use one unique idempotency key per intended mutation.
- Read back the target and inspect verification/workflow status.
- Do not move, archive, delete, or modify money, clients, files, orders, or
  inventory without explicit owner intent.
- Search/suggest before creating or linking clients.
- Use `agent_finance_workflow` for repair-order payments rather than a generic
  cash transaction.
- Use `agent_document_workflow` and the CRM renderer for standard AutoStop
  documents, including documents without a card.
- Repair-order numbers are immutable; the API compatibility correction route
  is blocked.
- Finance safe fixes remain maintenance-only.

`Приберись` means: inspect live context, preserve operator-entered facts and
all financial/order/file history, patch only confirmed fields, refresh the
short board summary, and reread. It does not imply move, archive, or delete.

## Client Notes

Codex registers the URL without bearer/static headers and links once with
`codex mcp login autostopcrm`; its protected credential store retains the
refresh session and renews short-lived access automatically. ChatGPT Apps use
the same public OAuth discovery and owner approval flow. Dynamic registration
accepts only ChatGPT connector callbacks and protected loopback Codex callbacks
(`/callback` or current `/callback/<12-character-id>` on a high local port).

Responses API remote MCP tools may continue to use the production URL as
`server_url` and send the current internal bearer in the tool's `authorization`
field on every response creation request. The Responses API does not store that
value. Never paste a token into source, docs, logs, or ordinary chat.

Public anonymous writes must remain blocked. Public anonymous reads are also
blocked in production.

## Checks

Local:

```powershell
.\scripts\run_mcp_server.ps1
.\.venv\Scripts\python.exe scripts\crm_capability_parity.py --require-complete
.\.venv\Scripts\python.exe scripts\crm_change_feed_producer_parity.py --require-complete
.\.venv\Scripts\python.exe -m unittest tests.test_mcp tests.test_mcp_main tests.test_agent_gateway_v2 -v
.\.venv\Scripts\python.exe scripts\check_agent_gateway_v2.py --mcp-url http://127.0.0.1:41831/mcp --exhaustive
```

The producer parity gate does not count guarded `api:/api/*` reachability as
end-to-end verification by itself. Every `executor_contract_only` write route
must either execute its real registry handler against isolated temporary state
and replay the resulting durable feed event, or match a fixed reviewed
model/runtime/render boundary in the route-contract test.

On Linux/VPS, `scripts/run_isolated_write_smoke.sh` exercises real create,
inventory, archive, idempotency, and fail-closed paths against temporary state.
The temporary state is removed on exit and is never the production state file.

Release verification:

```powershell
.\.venv\Scripts\python.exe scripts\check_agent_gateway_v2.py --mcp-url https://crm.autostopcrm.ru/mcp --token-env AUTOSTOPCRM_MCP_TOKEN --exhaustive
.\.venv\Scripts\python.exe scripts\check_mcp_oauth.py --mcp-url https://crm.autostopcrm.ru/mcp
```

For a Store-enabled release, add `--require-store`. For the guarded web route,
add `--require-web`; it discovers and calls `search_web_multi`,
`fetch_page_excerpt`, and `fetch_page_browser` through the existing raw escape
hatch. These checks do not increase the public surface beyond 24 tools.

The Store probe performs a live
adapter health probe and one bounded `store_state` search without advancing the
owner's `store_digest` cursor, while retaining the exact 24-tool assertion.

The script verifies anonymous rejection, the exact tool set, payload budgets,
all 24 calls with read-only/dry-run/synthetic inputs, and does not print board
data or the token. Production deploy and rollback are in
[the operations runbook](docs/OPERATIONS_RUNBOOK.md).
