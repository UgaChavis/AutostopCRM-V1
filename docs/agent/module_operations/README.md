# CRM module operations

Catalog reviewed against CRM source `ac10f5340c21554be8f2bd8f3572b58bbe355ee4`
on 2026-09-25. The technical runtime inventory below was taken before that
commit was deployed, at OCI revision `5f2fb21911914bfd7b4f2b979d192ed1a5e35d52`.
Authenticated local MCP `initialize` and `tools/list` returned 24 tools. These
observations establish visibility of the then-running schema, not the success
of every downstream business operation or the later release. Repeat the checks
after a release. Never copy live records, tokens, or request bodies into this catalog.

| Area | Card | Owner and boundary |
| --- | --- | --- |
| Public MCP, Gateway v2, raw discovery, Manager/Store mounts | [CRM Gateway](crm_gateway.md) | CRM owns board, clients, vehicles, repairs, finance, inventory and files. Manager owns orchestration; Store owns quotes, stock and orders. |
| API, services, containers and integrations | [CRM runtime](crm_runtime.md) | CRM API and services own CRM records; external dependencies are reached through documented adapters. |
| Local checks, probes, build, maintenance and release commands | [CRM commands](crm_commands.md) | The command's target and mode determine its effects. |

The full HTTP contract is [API_GUIDE.md](../../../API_GUIDE.md), the public MCP
contract is [MCP_GUIDE.md](../../../MCP_GUIDE.md), and the authoritative release
and rollback procedure is the [operations runbook](../../OPERATIONS_RUNBOOK.md).
Manager native MCP, VIN/PartsAPI, J1, Telegram and scheduler operations belong
to the Manager catalog; this CRM catalog covers only their CRM-side mount and
handoff. The Store API and its deployment belong to the Store repository.

## Choose and diagnose

1. Confirm source, exact remote ref, working tree, running image revision and
   endpoint separately. `git`, CI, Docker health and HTTP 200 each prove only
   their own layer.
2. For MCP, check DNS/TLS/HTTP transport, anonymous 401, authenticated
   `initialize`, `tools/list` names and `inputSchema`, then one bounded
   read-only call. A tool appearing in the list does not prove its dependency.
3. For a failed call, distinguish transport, schema, OAuth/permission/Gateway
   switch, missing Manager mount, Store/J1/provider health, CRM domain error and
   stale installed client. Reconcile an uncertain write before retrying.
4. Use local synthetic tests first. Production writes require an exact current
   target, authorization, native revision/idempotency guard and independent
   readback. No synthetic customer, quote, order or payment is created live.

Stop a release at a failed gate. The release script owns its backup checkpoint,
maintenance hold and rollback; do not substitute an ad hoc container restart or
manual data restore. See the [runbook release sequence](../../OPERATIONS_RUNBOOK.md#release-checklist).

## Verification record

| Check, 2026-09-25 | Evidence | Status and limit |
| --- | --- | --- |
| Exact GitHub production ref, checkout and image at baseline | `autostopcrm-v1` and OCI `org.opencontainers.image.revision` were `5f2fb219…`; the GitHub ref later advanced to `ac10f534…` | HISTORICAL PASS; recheck parity before any release |
| Public HTTPS root and MCP without credentials | root 200; MCP 401 | PASS for routing and anonymous denial only |
| Local MCP `initialize`/`tools/list` | 24 names; name plus `inputSchema` SHA-256 `dd3c03f9fdde60828dc716b72656bdc32b565755e2d2278c4445fc263c4fe73f` | PASS for active technical surface only |
| Local MCP technical calls | `ping_connector`, `get_connector_identity`, `get_runtime_status`, `list_agent_workflows` returned without MCP errors | PASS for those four read-only calls only |
| Gateway-focused synthetic tests | 129 unit tests passed | PASS for tested contracts; no live write proof |
| Documentation and code-health audits | both passed on baseline | PASS for their declared checks |
| Authenticated live business calls and full release smoke | outside this read-only inventory | NOT TESTED |
