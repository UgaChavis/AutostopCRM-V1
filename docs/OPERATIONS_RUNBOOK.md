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

Current Codex checkout on this machine:

```text
C:\Users\User\Desktop\AutostopCRM-V1
```

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

Manager knowledge:

- For CRM/MCP/connector operating context, use AutostopManager and the
  AutoStop Obsidian vault before broad repo reads.
- Prefer cloud vault `C:\Users\User\Мой диск\Obsidian CRM\AutostopCRM`; use
  `Home.md` and `80_Codex\Codex interaction.md` as entrypoints.
- Do not put live CRM snapshots, client databases, raw Gmail threads, cashbox
  ledgers, credentials, or bearer tokens into Obsidian.
- Safe Obsidian snapshots are allowed for manager orientation: board load,
  cashbox totals, repair-order counts, client-quality signals, and shared-file
  metadata. Full client rows, phone lists, VIN/license tables, raw cash
  journals, and full repair-order text remain in CRM unless the owner approves
  that exact export.

## Local Checks

Common:

```powershell
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
python scripts\audit_localization.py
```

### Release Checklist

Перед merge/deploy ветки с кодовыми изменениями:

```powershell
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
python scripts\audit_localization.py
python scripts\check_web_assets_js.py
python scripts\browser_smoke.py
```

Для production parity перед релизом:

```powershell
git status --short --branch
git rev-parse --short HEAD
git rev-parse --short origin/autostopcrm-v1
ssh -i $HOME\.ssh\codex_autostopcrm root@crm.autostopcrm.ru "cd /opt/autostopcrm && git status --short --branch && git rev-parse --short HEAD && git rev-parse --short origin/autostopcrm-v1 && docker compose ps"
```

Full regression when shared behavior changed:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
```

Browser assets:

```powershell
python scripts\check_web_assets_js.py
```

Browser UI smoke:

```powershell
python scripts\browser_smoke.py
```

`browser_smoke.py` поднимает временный local API с temp `JsonStore`, тестовым
оператором и synthetic данными. Он не использует production URL, credentials или
live CRM data. Он проверяет operator login privacy gate, board/card roundtrip,
кассы/журнал, клиентов, сотрудников, файлы, архив, заказ-наряды и modal ladder.
Запускайте его после изменений browser UI, модальных окон, карточек, касс,
клиентов, сотрудников, файлов или заказ-нарядов.

### Performance Smoke

Local temp-server probe:

```powershell
python scripts\perf_probe.py --local-temp-server --iterations 1 --max-snapshot-gzip-ms 1200 --max-snapshot-gzip-bytes 120000 --max-revision-ms 800 --max-get-card-ms 800
```

Read-only latency/payload probe:

```powershell
python scripts\perf_probe.py --base-url https://crm.autostopcrm.ru --iterations 5 --max-snapshot-gzip-ms 800 --max-snapshot-gzip-bytes 80000 --max-revision-ms 500 --max-get-card-ms 500
```

Пороговые значения нужны как guardrail, а не как SLA. Если production сеть нестабильна, приложите JSON output к задаче и повторите probe перед выводами.

### Finance Audit-First

Финансовая проверка сначала read-only:

```powershell
python scripts\finance_audit_report.py --base-url https://crm.autostopcrm.ru --format text --issue-limit 50
```

Любые `finance_audit/apply_safe_fixes` или live write-actions выполняются только после owner review отчёта, dry-run результата и отдельного подтверждения. Не редактируйте cashbox JSON/state вручную.

Operator cashbox UI is journal-first. Buttons/entrypoints for reconciliation or
`Финансовая сверка` must not be visible in the cashbox UI. Keep finance audit as
internal API/CLI/MCP diagnostics.

### Local Production-Data Sandbox

Для ручного UI QA на реалистичных данных используйте dated sandbox вне repo и
не заменяйте текущий `%APPDATA%`:

```powershell
$env:APPDATA = "C:\Users\User\Desktop\AutostopCRM-data-snapshots\prod-2026-05-19"
$env:MINIMAL_KANBAN_API_HOST = "127.0.0.1"
$env:MINIMAL_KANBAN_API_PORT = "42731"
$env:MINIMAL_KANBAN_MCP_HOST = "127.0.0.1"
$env:MINIMAL_KANBAN_MCP_PORT = "42831"
$env:MINIMAL_KANBAN_AGENT_ENABLED = "0"
$env:MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS = "1"
python main_mcp.py
```

Manual QA on sandbox data may inspect board, cards, clients, cashboxes,
employees, repair orders and files. Do not create live operations, do not run
Telegram AI/tunnel/sync, and do not commit or document raw phones, VIN/license
plates, cashbox rows, full repair-order text or client databases.

Local connector smoke:

```powershell
python scripts\check_live_connector.py --strict --skip-public-site --skip-public-write-protection --local-api-url http://127.0.0.1:41731 --mcp-url http://127.0.0.1:41831/mcp --operator-username $env:AUTOSTOP_SMOKE_OPERATOR_USERNAME --operator-password $env:AUTOSTOP_SMOKE_OPERATOR_PASSWORD --expect-admin
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
- `AUTOSTOP_INSTALL_WATCHDOG=0` - skip production watchdog timer install
- `AUTOSTOP_WATCHDOG_INTERVAL` - watchdog timer interval; default `1min`

Normal production deploy should stay on `autostopcrm-v1`.

## Production Watchdog

`deploy.sh` installs and enables `autostopcrm-watchdog.timer` on systemd hosts by default.
The watchdog runs `scripts/production_watchdog.py` from the production checkout and checks:

- local host API upstream: `http://127.0.0.1:8000/api/health`;
- local host MCP upstream: `http://127.0.0.1:8001/mcp`;
- public CRM page: `https://crm.autostopcrm.ru`.

If the container is not ready or the local host upstream fails, it runs:

```bash
cd /opt/autostopcrm
docker compose restart autostopcrm
```

If local upstreams are healthy but the public site fails, it validates nginx config and reloads nginx.

Useful commands:

```bash
systemctl status autostopcrm-watchdog.timer
systemctl status autostopcrm-watchdog.service
journalctl -u autostopcrm-watchdog.service -n 100 --no-pager
systemctl start autostopcrm-watchdog.service
```

## Production Verification

From server:

```bash
cd /opt/autostopcrm
docker compose ps
docker compose exec -T autostopcrm python scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --mcp-url https://crm.autostopcrm.ru/mcp --operator-username "${AUTOSTOP_SMOKE_OPERATOR_USERNAME:?set smoke username}" --operator-password "${AUTOSTOP_SMOKE_OPERATOR_PASSWORD:?set smoke password}" --expect-admin
```

From local machine:

```powershell
python scripts\check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url https://crm.autostopcrm.ru --mcp-url https://crm.autostopcrm.ru/mcp --operator-username $env:AUTOSTOP_SMOKE_OPERATOR_USERNAME --operator-password $env:AUTOSTOP_SMOKE_OPERATOR_PASSWORD --expect-admin
```

Manual UI smoke after UI changes:

- board loads;
- topbar modules open;
- card open/save works;
- card journal is readable;
- Files grid upload/paste/drag/download/delete works;
- clients, repair orders, cashboxes and employees modals open;
- employees -> salary/accrual row -> repair order -> close returns to the same employee;
- clients -> linked repair order -> close returns to the same client profile;
- repair orders list -> repair order -> close returns to the list/filter context;
- cashboxes -> journal/transfer -> close returns to cashboxes;
- cashboxes -> journal shows compact operation rows, collapsed balances,
  no reconciliation entrypoint, no visible `нет пары` diagnostic chips, and
  transfer pairs as one `касса -> касса` row;
- card -> repair order -> payments -> close/Escape steps back one modal at a time;
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
