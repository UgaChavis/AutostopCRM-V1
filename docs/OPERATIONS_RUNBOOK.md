# AutoStop CRM Operations Runbook

Короткий регламент для local work, GitHub sync, deploy и live checks.

## Endpoints

- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`
- production repo: `/opt/autostopcrm`
- branch: `autostopcrm-v1`

Этот runbook покрывает CRM repo и in-repo Telegram AI worker. VPN-monitoring проект ведётся отдельно.

## Перед Работой

Local:

```powershell
git status --short --branch
git rev-parse --short HEAD
git fetch origin autostopcrm-v1 --prune
git rev-parse --short origin/autostopcrm-v1
```

Production:

```powershell
ssh root@crm.autostopcrm.ru "cd /opt/autostopcrm && git status --short --branch && git rev-parse --short HEAD && git rev-parse --short origin/autostopcrm-v1"
```

Если SSH identity/host отличается на машине, используйте local access notes вне репозитория. Credentials не коммитить.

## Local Checks

Common:

```powershell
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
python scripts\audit_localization.py
```

Full regression when shared behavior changed:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
```

Browser assets:

```powershell
python scripts\check_web_assets_js.py
```

Local connector smoke:

```powershell
python scripts\check_live_connector.py --strict --skip-public-site --skip-public-write-protection --local-api-url http://127.0.0.1:41731 --mcp-url http://127.0.0.1:41831/mcp --operator-username admin --operator-password admin --expect-admin
```

## Deploy

Нормальный путь:

1. commit intended local change;
2. push to `origin/autostopcrm-v1`;
3. на production fetch/reset to `origin/autostopcrm-v1`;
4. run `./deploy.sh`;
5. run container and connector smoke.

Server commands:

```bash
cd /opt/autostopcrm
git fetch origin autostopcrm-v1 --prune
git reset --hard origin/autostopcrm-v1
./deploy.sh
docker compose ps
```

Useful `deploy.sh` env vars:

- `AUTOSTOP_DEPLOY_BRANCH` - branch to fetch/reset; default `autostopcrm-v1`
- `AUTOSTOP_SKIP_GIT_SYNC=1` - skip fetch/reset when already synced
- `AUTOSTOP_COMPOSE_SERVICE` - compose service name; default `autostopcrm`
- `AUTOSTOP_VERIFY_PUBLIC_HTTPS=1` - enable public HTTPS smoke
- `AUTOSTOP_PUBLIC_SITE_URL`, `AUTOSTOP_PUBLIC_MCP_URL` - public smoke URLs
- `AUTOSTOP_SMOKE_OPERATOR_USERNAME`, `AUTOSTOP_SMOKE_OPERATOR_PASSWORD` - smoke credentials
- `AUTOSTOP_DESKTOP_INSTRUCTION_PATH` - where to copy `AUTOSTOPCRM_FULL_INSTRUCTION.txt`

Normal production deploy should stay on `autostopcrm-v1`.

## Production Verification

From server:

```bash
cd /opt/autostopcrm
docker compose ps
docker compose exec -T autostopcrm python scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --mcp-url https://crm.autostopcrm.ru/mcp --operator-username admin --operator-password admin --expect-admin
```

From local machine:

```powershell
python scripts\check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url https://crm.autostopcrm.ru --mcp-url https://crm.autostopcrm.ru/mcp --operator-username admin --operator-password admin --expect-admin
```

Manual UI smoke after UI changes:

- board loads;
- topbar modules open;
- card open/save works;
- card journal is readable;
- Files grid upload/paste/drag/download/delete works;
- clients, repair orders, cashboxes and employees modals open;
- public anonymous writes remain blocked.

## Telegram AI Worker

Docker service: `autostopcrm-telegram-ai`.

Properties:

- long polling, no public port;
- CRM API inside compose: `http://autostopcrm:41731`;
- secrets live in server-local `telegram-ai.env`;
- `telegram-ai.env` is repo-ignored and must not be removed during sync;
- audit/state/conversation files live under `/root/.minimal-kanban/telegram_ai/`.

Required env when enabled:

```env
AUTOSTOP_TELEGRAM_AI_ENABLED=1
AUTOSTOP_TELEGRAM_BOT_TOKEN=...
AUTOSTOP_TELEGRAM_OWNER_IDS=123456789
OPENAI_API_KEY=...
AUTOSTOP_CRM_API_BASE_URL=http://autostopcrm:41731
```

Useful optional env:

```env
AUTOSTOP_AI_MODEL=gpt-5.4-mini
AUTOSTOP_AI_STRONG_MODEL=gpt-5.4
AUTOSTOP_AI_WEB_SEARCH_ENABLED=1
AUTOSTOP_AI_REASONING_EFFORT=medium
AUTOSTOP_AI_STRONG_REASONING_EFFORT=high
```

Worker checks:

```bash
docker compose ps
docker compose logs --tail=100 autostopcrm-telegram-ai
```

## Manager Cleanup

When the owner says `Приберись`, treat it as an agent procedure:

1. read live card/board context;
2. preserve operator data;
3. patch only confirmed `vehicle`, `title`, `description`, safe `tags`, and source-backed `vehicle_profile`;
4. do not move/archive cards without separate explicit command;
5. refresh `board_summary` and verify `board_summary_stale=false`.

`description` is full recoverable text. `board_summary` is only the short board preview.

## Production Cautions

- do not trust stale docs for current server HEAD;
- do not rotate credentials casually;
- do not remove server-local env files;
- do not edit production state files by hand;
- GitHub-first change, then deploy.

## Documentation Policy

Canonical active docs:

- `00_START_HERE_AUTOSTOP_CRM.md`
- `PROJECT_HANDOFF.md`
- `README.md`
- this runbook
- `MCP_GUIDE.md`
- `API_GUIDE.md`

Workflow docs остаются только пока их реально используют active code, deploy, release или operator flows. Не добавляйте frozen reports, commit lists и one-off plans в active docs.
