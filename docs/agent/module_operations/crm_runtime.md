# CRM runtime and integration cards

Source revision `ac10f534`, checked 2026-09-25. Use the current
[operations runbook](../../OPERATIONS_RUNBOOK.md) for every release or rollback.
The commands here are technical reads unless a row says otherwise. Do not
open CRM records or inspect private logs for a general health check.

| Module or service | Caller → entry → dependency / data owner | Safe check, expected result and failure clue | Effect / gate |
| --- | --- | --- | --- |
| Nginx public CRM | Owner client → `https://crm.autostopcrm.ru/` and `/mcp` → `autostopcrm`; Nginx owns TLS/routing, CRM owns data | `curl -sS -o /dev/null -w '%{http_code}\n' https://crm.autostopcrm.ru/` = 200; unauthenticated `/mcp` = 401. Check DNS/TLS separately if this fails. | R; HTTP status alone does not prove MCP calls. |
| `autostopcrm` Compose service | Nginx/Manager → host 8000/8001 → container 41731 API/UI and 41831 MCP → CRM services/JsonStore; CRM data owner | From the deployed checkout: `docker compose ps autostopcrm`; `docker inspect autostopcrm --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`; on the host `curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/health`. Healthy and exact candidate SHA required. | R checks; restart only through release runbook. |
| API routing/auth | Browser/MCP → [`api/server.py`](../../../src/minimal_kanban/api/server.py) and [`route_registry.py`](../../../src/minimal_kanban/api/route_registry.py) → services | `curl -sS -o /dev/null -w '%{http_code}\n' https://crm.autostopcrm.ru/api/health`; compare local upstream to public result. For route schemas use [API guide](../../../API_GUIDE.md). | R check. Authenticated calls can expose records; scope to task. |
| Domain services and JsonStore | UI/API/MCP → [`services/`](../../../src/minimal_kanban/services) → [`storage/json_store.py`](../../../src/minimal_kanban/storage/json_store.py); CRM owns cards, clients, repair orders, cashboxes and files | Synthetic `python -m unittest discover -s tests -v`; an isolated pass does not prove live state. Diagnose domain errors before storage transport. | Writes via shared services only; revision/idempotency/readback for impacts. |
| Gateway v2 | MCP → [`mcp/server.py`](../../../src/minimal_kanban/mcp/server.py) → [`agent_gateway_v2.py`](../../../src/minimal_kanban/mcp/agent_gateway_v2.py) → API and mounted dependencies | See [24-tool Gateway card](crm_gateway.md); compare `tools/list` schema, not just count. | Mixed; native guards and release smoke. |
| Manager mount | Gateway → [`manager_registration.py`](../../../src/minimal_kanban/mcp/manager_registration.py) → Manager registrar; Manager owns workflow/knowledge, CRM owns records | Production fails closed when required mounted tools are missing. `scripts/docs_audit.py --manager-root /opt/AutostopManager` checks a synthetic registered schema; `tools/list` checks active surface. | Mixed; no Manager raw data persisted in CRM docs. |
| Store adapter | Gateway → [`store_gateway.py`](../../../src/minimal_kanban/mcp/store_gateway.py) → Manager adapter → `http://autostop-app:8000/internal/agent/v1/...`; Store owns quotes, stock and orders | `scripts/validate_production_env.py --require-production --require-store` validates names/config without printing tokens. Official `--require-store` Gateway smoke checks scoped live read and flags. | Reads or guarded Store writes; no direct Store DB connection. |
| Change feed | CRM commit → [`change_feed_gateway.py`](../../../src/minimal_kanban/mcp/change_feed_gateway.py) and `/api/change_feed/{bootstrap,read,ack,register,summarize}` → Manager scheduler; CRM owns feed, Manager owns consumer cursor | `POST /api/change_feed/readiness` is technical readiness. Bootstrap/ACK/register alter consumer state; the maintenance-safe release probe handles them with its own consumer. | Readiness R; cursor operations T, exact ACK and readback. |
| G1 automation panel | [`module_map.html`](../../../src/minimal_kanban/web_app_assets/source/module_map.html) → [`automation_center.py`](../../../src/minimal_kanban/api/automation_center.py) → Manager control socket; Manager owns schedules and execution | Open `/module-map`, select G1 and use **i** beside any job or system timer. Help explains the action, live schedule, data, output and switch; opening it sends no control command. | Help/status R; switches and schedule edits T, admin session and revision guard required. |
| J1 public-web dependency | Gateway → [`web_gateway.py`](../../../src/minimal_kanban/mcp/web_gateway.py) → `autostop-searxng` / `autostop-crawl4ai`; public web is untrusted evidence | `docker compose ps autostop-searxng autostop-crawl4ai`; official `--require-web` smoke checks static and browser paths separately. Health alone does not prove research. | R externally; no fitment confirmation from web alone. |
| OAuth | Owner client → [`oauth_provider.py`](../../../src/minimal_kanban/mcp/oauth_provider.py) → CRM protected state | `python scripts/configure_mcp_oauth.py check --env-file .env` checks configuration; official `check_mcp_oauth.py` checks live authorization/refresh in a release. Never print state key/tokens. | Config check R; `ensure` and authorization mutate protected state. |
| Browser and Windows clients | UI/API → shared services; desktop entry [`main.py`](../../../main.py), MCP entry [`main_mcp.py`](../../../main_mcp.py) | `scripts/browser_smoke.py --profile core --attempts 1` uses disposable synthetic CRM; portable binary requires `run_quality_pass.ps1`. | Synthetic; local pass is not deployed UI proof. |

The Compose project has three CRM services: `autostopcrm`, `autostop-searxng`
and `autostop-crawl4ai`. Store and PostgreSQL are separate projects. The
production CRM baseline at `5f2fb219` on 2026-09-25 showed all three CRM
services healthy, public root 200, public MCP 401 without credentials and image
revision matching that initial production source. The candidate `ac10f534`
requires fresh release readback. These are bounded technical observations.

## Diagnose and recover

G1 reads the current schedule and actual state from Manager. Its **i** buttons
work with Enter or Space, remain available in read-only mode, and keep their
expanded state while status refreshes. The CRM digest uses new CRM change-feed
events and sends a summary to the configured owner's Telegram dialogue only
when there are changes. The database-backup timer protects the Store PostgreSQL
database; it does not back up CRM. Unknown future jobs explicitly show missing
descriptions instead of guessing their purpose. The panel shows run times and
errors; system-job output remains in the corresponding server journal.

If G1 is unavailable, compare the API status response, operator permissions,
Manager socket mount and scheduler state. A stale response disables mutations;
wait for a fresh status after recovery. A desired/actual mismatch requires
scheduler reconciliation, not repeated clicks. Use the release runbook for
service recovery. Synthetic keyboard/mobile and line-label collision checks
live in [`test_module_map_browser.py`](../../../tests/test_module_map_browser.py).

1. Compare authoritative DNS, external TCP/TLS, Nginx response, host 8000
   and 8001, then `docker compose ps` from the deployed checkout; a healthy
   container does not prove public availability.
2. If MCP initializes but a call fails, compare `tools/list` schema and the
   installed revision. Check Gateway switches, OAuth scope/permission and
   Manager mount before downstream Store/J1/provider status.
3. If a Store call fails, check `autostop-store-agent` network membership and
   Store API/flags. A missing token is configuration; HTTP 5xx is downstream;
   a quote revision conflict is domain/concurrency. Do not connect to the
   Store database.
4. Use targeted synthetic tests. If release gates fail, stop. `deploy.sh`
   seals the Manager candidate, runs its offline catalog sync and disk check
   before maintenance, then handles checkpoint, bounded pause, activation,
   verification and rollback. Manager MCP activation defaults to enabled;
   only an explicitly scoped CRM-only release sets
   `AUTOSTOP_MANAGER_MCP_ACTIVATE_ON_DEPLOY=0`. Preserve volumes; never
   manually replace production state to repair a probe.

Production environment names are documented in the
[runbook authentication section](../../OPERATIONS_RUNBOOK.md#production-authentication):
`AUTOSTOP_AGENT_GATEWAY_*`, `AUTOSTOP_MCP_OAUTH_ENABLED`,
`AUTOSTOP_MCP_OAUTH_STATE_KEY`, `MINIMAL_KANBAN_API_BEARER_TOKEN`,
`MINIMAL_KANBAN_MCP_BEARER_TOKEN`, optional `AUTOSTOP_MANAGER_PATH`, and
scoped `AUTOSTOP_STORE_*` tokens. Check presence and configured flags only.
