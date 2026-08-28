# AutoStop CRM MCP / Agent Gateway v2

The MCP endpoint exposes one current AutoStop CRM board through the same HTTP
API and services as the browser:

```text
MCP client -> MCP adapter -> local HTTP API -> domain services -> JsonStore
```

Source of truth:

- `src/minimal_kanban/mcp/server.py` — raw tool orchestration and remaining
  implementations;
- `src/minimal_kanban/mcp/payloads.py` — raw tool payload models and pure
  payload normalization;
- `src/minimal_kanban/mcp/connector_diagnostics.py` — permanent connector
  diagnostics registrar;
- `src/minimal_kanban/mcp/board_reads.py` — core board-read registrar;
- `src/minimal_kanban/mcp/board_column_writes.py` — board-column write
  registrar;
- `src/minimal_kanban/mcp/board_sticky_writes.py` — two-phase sticky write
  registrar preserving the raw registration order;
- `src/minimal_kanban/mcp/board_card_timer_writes.py` — card deadline, timer,
  and indicator write registrar;
- `src/minimal_kanban/mcp/card_attachment_reads.py` — card attachment metadata
  and bounded-content read registrar;
- `src/minimal_kanban/mcp/tool_registry.py` — raw CRM registry;
- `src/minimal_kanban/mcp/agent_gateway_v2.py` — production surface;
- `scripts/check_agent_gateway_v2.py` — exact release contract;
- `scripts/attest_agent_gateway_v2.py` — stop-the-line per-command
  certification runner;
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

### Guarded raw writes

Use the raw escape only when no named workflow covers the exact operation.
Natural-language discovery is deliberately read-only; for a raw write, query
the exact literal capability name, retrieve its current schema, and invoke it
only through `call_raw_capability` with that schema hash and a unique
idempotency key. The Gateway opens and closes the durable write ledger for that
call.

For example, `create_card` is not an `agent_board_workflow` operation. Search
the intended client and duplicate card/vehicle first, discover the exact
`create_card` schema, create via the guarded raw call, and require its exact
card readback. Before `link_card_to_client`, reread both targets and pass
`expected_card_updated_at` and `expected_client_updated_at` with a new
idempotency key; require exact readback of both card and client. Do not fall
back to a cached legacy App/connector tool or to the local HTTP API.

`/api/get_operator_profile` and `/api/update_personal_board_preferences` are
not MCP capabilities. They control one human operator's private extra
board-column view, are restricted to that browser session, and cannot be read
or changed by the Gateway service identity.

The shared mechanics message board is intentionally different: it is common CRM
state and is available through the named
`agent_document_workflow(operation="update_display_dashboard_message")`.
First read `api:/api/get_display_dashboard` through focused raw discovery to
obtain `message_board.revision`; upload optional photos with the existing
`upload_shared_file` document operation; prepare an action contract; then call
the message operation in `dry_run` and `apply` modes with separate
idempotency keys. Its payload is the same as `/api/update_board_settings`:
`expected_revision` plus `display_dashboard_message.body_html` and
`image_file_ids`. Dry-run proves validation without writing. Apply performs an
exact `get_display_dashboard` readback and compares the sanitized body,
ordered image IDs, and resulting revision before closing the workflow ledger.

Completion-act draft writes are named document operations, not raw fallbacks:
`agent_document_workflow(operation="save_completion_act_form")` and
`agent_document_workflow(operation="reset_completion_act_form")`. Both require
an explicit `dry_run` followed by `apply`, the exact card, current form version,
current 64-character source fingerprint, and one stable correlation ID. Dry-run
returns changed paths, next version, and a bound `dry_run_proof` without writing.
Apply uses a new idempotency key plus the prior dry-run key and proof, then closes
only after exact completion-act readback. The reset operation is destructive and requires a
verified pre-reset snapshot for compensation at the ActionContract layer. The
hidden HTTP/raw compatibility routes remain available, but their strict
route-specific schemas reject extra fields, invalid types, and more than 300
rows; `form_data` is a deprecated alias for `form`.

The raw board registry includes `start_card_timer` and `stop_card_timer`.
`create_card` leaves the timer inactive when `deadline` is omitted; an explicit
positive deadline starts it. Restarting without a deadline reuses the saved
duration. Timer-only actions are audited but do not flag the card as unseen
content for other operators.

The mounted Manager contributes 6 `INTERNAL_ONLY` Store adapter tools:
`store_runtime_status`, `store_digest`, `store_search`, `store_entity_context`,
`download_store_quote_vin_photo`, and `store_management_action`. They stay out
of raw discovery; public access remains through named Gateway tools.

`agent_bootstrap` is CRM-only and reports Store as `not_loaded` without a read.
Explicit Store context uses `agent_board_digest(scope="store")`,
`agent_entity_context`, and `agent_search` for `store_part`, `store_order`,
`store_quote_request`, `store_supplier`, `store_batch`,
`store_warehouse_operation`, `store_marketplace_listing`, `store_state`, and
`store_sourcing_offer`. `get_runtime_status` is the explicit health probe;
`agent_document_workflow(operation="download_store_quote_vin_photo")` returns
the bounded image only with `allow_large_output=true`.

Store digest pages use opaque cursor/ACK replay and commit high-water only
after the final ACK. Store actions use explicit `dry_run`/`apply` through
`agent_inventory_workflow`; omitted mode remains CRM-only legacy compatibility.

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
- Agent mutations use the authenticated OAuth owner as the audit actor when
  that owner is still an active CRM administrator. The technical
  `codex-owner-agent` service identity remains fixed for local authorization;
  caller-supplied actor names never override either identity. Legacy internal
  bearer calls remain audited as `codex-owner-agent`.
- Operator-admin raw routes additionally require the local service identity
  and matching bearer token; public proxy traffic cannot claim that identity.
- Exact backend compatibility reads for AI knowledge, plus UI/backend reads
  for board revision, display dashboard, inspection form, repair-order print
  workspace, and employees are
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
- The 6 Store adapter tools cannot be invoked through the raw escape hatch;
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
ACK/replay/CAS protocol; `agent_bootstrap` makes no Store request.

For Store writes, `agent_inventory_workflow` permits exactly 7 operations:
`assign_quote_request`, `set_quote_request_status`,
`update_quote_request_comment`, `add_quote_request_note`,
`replace_quote_offer_drafts`, `set_batch_storage_location`, and
`mark_order_ready`. It requires the exact target/revision, owner intent,
planned changes, distinct mode keys, and one stable correlation. Gateway keeps
only compact refs/hashes, verifies exact readback, leaves unresolved notifier
states compensating, and reconciles an uncertain POST only by replaying the
same apply key and Store receipt; it never retries automatically.

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
- New finance writes use `agent_finance_workflow` with explicit `dry_run`, then
  `apply` with its bound proof and a different key. Reads omit `mode`; omitted
  write mode preserves legacy compatibility.
- Use `agent_document_workflow` and the CRM renderer for standard AutoStop
  documents, including documents without a card.
- Use named completion-act save/reset operations with dry-run proof, separate
  idempotency keys, stable correlation, and exact form readback.
- Use `agent_document_workflow(operation="update_display_dashboard_message")`
  for shared mechanics-board text and photo references; never embed image
  bytes or external HTML in the message.
- Repair-order numbers are immutable; the API compatibility correction route
  is blocked.
- Closed repair orders must be corrected only through
  `preview_repair_order_reopen` -> `reopen_repair_order` -> edits ->
  `set_repair_order_status(status="closed")`. Pass the latest
  `expected_updated_at` and a unique idempotency key for each write. Use
  `get_repair_order_cycles` for readback; do not patch status, payments, cash,
  or inventory through the order payload.
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
.\.venv\Scripts\python.exe -m unittest tests.test_mcp_registration_contracts tests.test_mcp_payload_contracts -v
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

Run the production Gateway check inside the CRM container so the compatibility
bearer remains server-local, then verify OAuth separately with
`scripts/check_mcp_oauth.py`.

Stop-the-line production attestation is separate from release smoke. It freezes
the live 24 public tools, 43 CRM workflow operations and Manager-used CRM raw
capabilities, then executes one case per invocation:

```bash
.venv/bin/python scripts/attest_agent_gateway_v2.py --run-id AST-GWAT-YYYYMMDDTHHMMSSZ --mcp-url https://crm.autostopcrm.ru/mcp --inventory
.venv/bin/python scripts/attest_agent_gateway_v2.py --run-id AST-GWAT-YYYYMMDDTHHMMSSZ --mcp-url https://crm.autostopcrm.ru/mcp --next --apply-synthetic
.venv/bin/python scripts/attest_agent_gateway_v2.py --run-id AST-GWAT-YYYYMMDDTHHMMSSZ --mcp-url https://crm.autostopcrm.ru/mcp --retry --apply-synthetic
.venv/bin/python scripts/attest_agent_gateway_v2.py --run-id AST-GWAT-YYYYMMDDTHHMMSSZ --mcp-url https://crm.autostopcrm.ru/mcp --cleanup
.venv/bin/python scripts/attest_agent_gateway_v2.py --run-id AST-GWAT-YYYYMMDDTHHMMSSZ --summary
```

The token is read only from the configured environment. Mutating cases require
the run prefix and explicit `--apply-synthetic`; any defect blocks later cases.
Reports are mode `0600` and live outside Git under
`/var/lib/autostop-manager/integration/gateway-attestation/<run-id>/`. They
store hashes, sizes, timings, statuses and compact refs, never request/response
bodies, tokens, full cards or financial journals.

For a Store-enabled release, add `--require-store`. For the guarded web route,
add `--require-web`; it discovers and calls `search_web_multi`,
`fetch_page_excerpt`, `fetch_page_browser`, and `research_drive2_cases` through
the existing raw escape hatch. The Drive2 route searches a bounded set of
public logbooks, returns compact case evidence with access status, never uses a
Drive2 account, and does not persist raw pages. These checks do not increase
the public surface beyond 24 tools.

The Store probe performs a live
adapter health probe and one bounded `store_state` search without advancing the
owner's `store_digest` cursor, while retaining the exact 24-tool assertion.

The script verifies anonymous rejection, the exact tool set, payload budgets,
all 24 calls with read-only/dry-run/synthetic inputs, and does not print board
data or the token. Production deploy and rollback are in
[the operations runbook](docs/OPERATIONS_RUNBOOK.md).
