# CRM Gateway v2 and MCP

Gateway source verified 2026-09-25 at `ac10f534`; the live technical MCP
inventory below was observed at deployed revision `5f2fb219`. Production transport is streamable
HTTP `https://crm.autostopcrm.ru/mcp` behind Nginx; local container transport
is `http://127.0.0.1:41831/mcp`. OAuth 2.1 with PKCE serves the owner client;
the internal bearer supports the official smoke. Anonymous access must return
401. The server is built by
[`create_mcp_server`](../../../src/minimal_kanban/mcp/server.py), Gateway
registration by
[`register_agent_gateway_v2`](../../../src/minimal_kanban/mcp/agent_gateway_v2.py),
authorization by [`auth.py`](../../../src/minimal_kanban/mcp/auth.py) and
[`oauth_provider.py`](../../../src/minimal_kanban/mcp/oauth_provider.py).
`tools/list` is the active input-schema source. The tables below record every
property name observed from the running 24-tool Gateway; ask `tools/list` for
types, enum values, bounds and descriptions after any revision change.

**Schema procedure.** From an authenticated MCP client, run `initialize`, then
`tools/list`; compare every `name`, `inputSchema` and annotations with the
intended installed revision. `scripts/check_agent_gateway_v2.py --exhaustive`
does this plus safe release probes. In the container it reads
`MINIMAL_KANBAN_MCP_BEARER_TOKEN` by name; never put its value on a command
line or in output. The 2026-09-25 active name plus input-schema SHA-256 is
`dd3c03f9fdde60828dc716b72656bdc32b565755e2d2278c4445fc263c4fe73f`.
The [Manager CRM MCP catalog](https://github.com/UgaChavis/AutostopManager/blob/AutostopManager/docs/agent/crm_mcp_catalog.json)
is a versioned schema fingerprint, while this card explains operation and
recovery. `scripts/docs_audit.py --manager-root /opt/AutostopManager` compares
the registered surfaces and that manifest in a synthetic process.

In the table, **R** is read-only, **T** is technical or workflow state, **M**
can mutate CRM/Store or cause an external effect, and **D** only prepares a
contract. Required fields are before `;`; optional fields follow. `E` means
the official exhaustive smoke owns the safe invocation using read-only,
dry-run or synthetic terminal inputs. For an individual live call, use only
an R tool with a bounded exact target and review its current schema first.
All M and T calls need a disposable synthetic environment or the authorized
release procedure; do not improvise a production test record.

| Tool | Required; optional input properties from active `inputSchema` | Class | Safe probe |
| --- | --- | --- | --- |
| `agent_bootstrap` | none; `query`, `intent`, `sample_limit`, `include_store_context` | R | E; CRM-only call without Store signal |
| `agent_board_digest` | none; `include_archived`, `cursor`, `limit`, `fields`, `scope`, `since`, `ack_token` | R/T for ACK | E; never ACK a page without exact token |
| `agent_search` | `entity`; `query`, `include_archived`, `limit`, `filters`, `cursor` | R | E |
| `agent_entity_context` | `entity`, `entity_id`; `detail` | R | E; exact current ID |
| `agent_board_workflow` | `operation`, `payload`, `idempotency_key`; `mode` | R/M by operation | E |
| `agent_finance_workflow` | `operation`, `payload`, `idempotency_key`; `mode`, `dry_run_proof`, `dry_run_idempotency_key` | R/M; finance guard | E |
| `agent_inventory_workflow` | `operation`, `payload`; `idempotency_key`, `mode` | R/M; CRM or Store owner | E |
| `agent_document_workflow` | `operation`, `payload`, `idempotency_key`; `allow_large_output`, `mode` | R/M; files and documents | E |
| `discover_raw_capabilities` | none; `query`, `limit` | R | E |
| `get_raw_capability_schema` | `name`; none | R | E; use discovered exact name |
| `call_raw_capability` | `name`, `arguments`, `schema_hash`; `idempotency_key`, `allow_large_output`, `release_smoke_revision`, `release_smoke_proof` | R/M by named capability | E; no generic Store owner escape |
| `get_connector_identity` | none | R | E |
| `get_runtime_status` | none | R | E |
| `ping_connector` | none | R | E |
| `list_agent_workflows` | none; `query`, `intent`, `limit` | R | E |
| `prepare_action_contract` | `domain`, `action`; `target_id`, `planned_changes`, `owner_intent`, `expected_revision`, `idempotency_key`, `correlation_id`, `run_id`, `actor`, `dry_run` | D | E; returns a plan only |
| `start_workflow` | `workflow_id`, `intent`, `idempotency_key`; `query`, `request_id`, `correlation_id`, `actor`, `scope`, `selected_ids`, `dry_run`, `source`, `metadata` | T | E |
| `workflow_status` | `run_id`; `include_events`, `include_external_steps` | R | E |
| `workflow_transition` | `run_id`, `status`; `message`, `verification`, `summary`, `expected_state_version` | T | E |
| `workflow_checkpoint` | `run_id`, `checkpoint`; `selected_ids`, `message`, `expected_state_version` | T | E |
| `workflow_wait_for_external` | `run_id`, `step_id`, `connector`, `action`; `request_refs`, `expected_state_version` | T | E |
| `complete_external_step` | `run_id`, `step_id`; `result_refs`, `expected_state_version` | T | E |
| `workflow_resume` | `run_id`; `expected_state_version` | T | E |
| `workflow_cancel` | `run_id`; `reason`, `expected_state_version` | T | E |

The operation enums and field limits are in
[`gateway_contract.py`](../../../src/minimal_kanban/mcp/gateway_contract.py),
the Store operation names and impact split in
[`store_gateway.py`](../../../src/minimal_kanban/mcp/store_gateway.py),
and raw capability authorization, schema hashes and readback in
[`raw_gateway.py`](../../../src/minimal_kanban/mcp/raw_gateway.py).
`resolve_vin_oem_parts` is a read-only **hidden Manager dependency**, included
by [`MANAGER_GATEWAY_DEPENDENCY_NAMES`](../../../src/minimal_kanban/mcp/agent_gateway_support.py);
it is reached through raw discovery and is not a 25th public tool. The generic
`store_owner_api` is internal only; quote writes use the typed
`agent_inventory_workflow` / `store_quote_conductor` route. CRM must use the
documented Store API boundary, never Store PostgreSQL.

## Safe release smoke and failures

From the exact candidate checkout after the runbook's preflight and activation:

```bash
cd /opt/autostopcrm
docker compose exec -T autostopcrm python scripts/check_agent_gateway_v2.py \
  --mcp-url https://crm.autostopcrm.ru/mcp --exhaustive --require-store --require-web
```

The maintenance-safe variant with revision and attempt ID belongs **inside**
`deploy.sh` while the maintenance marker is active. Run Gateway unit tests in
a disposable local environment:

```bash
cd /opt/autostopcrm
.venv/bin/python -m unittest tests.test_agent_gateway_v2 \
  tests.test_agent_gateway_v2_smoke_script \
  tests.test_agent_gateway_v2_attestation_unittest -q
```

`initialize` failure suggests transport/auth; a missing tool suggests Gateway
switches, Manager registration or version parity; schema mismatch suggests
client/server revision drift; a present tool with failed Store/J1/PartsAPI
call suggests downstream health or provider failure. A CRM domain rejection
is not a transport failure. For write uncertainty, reread the exact target and
ledger before retrying the same idempotency key. The official release script
owns rollback; see the [runbook](../../OPERATIONS_RUNBOOK.md#production-verification).
