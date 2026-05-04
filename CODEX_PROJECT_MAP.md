# AutoStopCRM Codex Project Map

## Canonical Repo

- GitHub: `UgaChavis/AutostopCRM-V1`
- Active branch: `autostopcrm-v1`
- Local workspace: `C:\Users\9860606\Desktop\AutostopCRM\autostopcrm`
- Server workspace: `/opt/autostopcrm`
- Public site and MCP endpoint: `https://crm.autostopcrm.ru` and `https://crm.autostopcrm.ru/mcp`

## First Files

- `README.md` - current product overview and capability groups.
- `PROJECT_HANDOFF.md` - current handoff/status notes.
- `MASTER-PLAN.md` - larger roadmap and operating plan.
- `MCP_GUIDE.md` - MCP tool groups, usage rules, live inventory expectations.
- `CHATGPT_CONNECTOR_SETUP.md` - ChatGPT connector setup flow.
- `API_GUIDE.md` - HTTP API behavior.

## Runtime

- Desktop/local entrypoints: `main.py`, `main_mcp.py`.
- MCP server: `src/minimal_kanban/mcp/server.py`.
- MCP runtime/auth: `src/minimal_kanban/mcp/runtime.py`, `src/minimal_kanban/mcp/auth.py`, `src/minimal_kanban/mcp/oauth_provider.py`.
- MCP backend client: `src/minimal_kanban/mcp/client.py`.
- Connection card shown by the app: `src/minimal_kanban/connection_card.py`.
- Public server deployment: `docker-compose.yml`, `Dockerfile`, `deploy.sh`.

## MCP Surface

- Base CRM MCP tools: 71.
- Optional AutostopManager tools: 13 when `/opt/AutostopManager` or sibling `AutostopManager` is mounted.
- Expected production `tools/list` with manager mounted: 84.
- `cleanup_card_content`, `autofill_vehicle_data`, and `autofill_repair_order` are not MCP runtime tools.

## Verification

- Live smoke: `python scripts/check_live_connector.py --strict --expect-https --site-url https://crm.autostopcrm.ru --mcp-url https://crm.autostopcrm.ru/mcp`
- Core MCP tests: `python -m unittest tests.test_mcp tests.test_connection_card -q`
- Full test suite: `python -m unittest discover -s tests -q`

## Deployment Notes

- Do not overwrite server-only files such as `/opt/autostopcrm/telegram-ai.env`.
- Do not commit runtime data, board snapshots, local JSON storage, SQLite databases, secrets, or credentials.
- If AutostopManager tools changed, update `/opt/AutostopManager` and restart the `autostopcrm` service so MCP re-imports the manager tools.
