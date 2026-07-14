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

Production is bearer-only and fail-closed. Embedded OAuth/DCR must remain off
with `AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED=0`; anonymous reads and writes are
rejected. The container validates the token, HTTPS public URLs, service
identity, maintenance marker, and all Gateway switches before startup.

ChatGPT Apps & Connectors require OAuth 2.1 for an authenticated custom MCP and
cannot present an arbitrary owner API key. The current bearer-only production
endpoint therefore is not directly linkable as a ChatGPT app. Codex/manual MCP
clients that can set an Authorization header and Responses API clients remain
supported. See [the client compatibility note](CHATGPT_CONNECTOR_SETUP.md).

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

## Gateway Guarantees

Gateway responses use `agent_envelope_v2` and compact verification evidence.

- Raw use is a three-step sequence: discover, fetch the current schema, then
  call with the route-bound `schema_hash`.
- Writes require the applicable policy switch, a unique idempotency key, and a
  durable workflow ledger. A missing ledger fails closed.
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
- During maintenance, write routes return `503 maintenance_mode` while
  diagnostics and reads remain available.

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
4. `list_agent_workflows` and `prepare_action_contract` for managed work
5. the narrow board, finance, inventory, or document workflow
6. `workflow_status` and target readback
7. raw discovery only when no named workflow covers the request

Use `get_runtime_status` for runtime/auth diagnostics, not as the normal
bootstrap. When the optional AutostopManager package is mounted, its memory,
routing, and ledger capabilities remain behind the same Gateway and raw
discovery; the visible count stays 24.

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

Responses API remote MCP tools use the production URL as `server_url` and send
the current bearer value in the tool's `authorization` field on every response
creation request. The Responses API does not store that value. Never paste a
token into source, docs, logs, or ordinary chat.

Public anonymous writes must remain blocked. Public anonymous reads are also
blocked in production.

## Checks

Local:

```powershell
.\scripts\run_mcp_server.ps1
.\.venv\Scripts\python.exe -m unittest tests.test_mcp tests.test_mcp_main tests.test_agent_gateway_v2 -v
.\.venv\Scripts\python.exe scripts\check_agent_gateway_v2.py --mcp-url http://127.0.0.1:41831/mcp --exhaustive
```

Release verification:

```powershell
.\.venv\Scripts\python.exe scripts\check_agent_gateway_v2.py --mcp-url https://crm.autostopcrm.ru/mcp --token-env AUTOSTOPCRM_MCP_TOKEN --exhaustive
```

The script verifies anonymous rejection, the exact tool set, payload budgets,
all 24 calls with read-only/dry-run/synthetic inputs, and does not print board
data or the token. Production deploy and rollback are in
[the operations runbook](docs/OPERATIONS_RUNBOOK.md).
