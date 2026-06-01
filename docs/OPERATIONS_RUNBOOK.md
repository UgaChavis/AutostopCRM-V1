# AutoStop CRM Operations Runbook

This is the source of truth for local verification, GitHub/server sync,
production deploy, live smoke, performance checks, watchdog, and maintenance
safety.

## Current Runtime

- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`
- production repo: `/opt/autostopcrm`
- branch: `autostopcrm-v1`
- compose services: `autostopcrm`, `autostopcrm-telegram-ai`
- canonical SSH identity: `autostopcrm_server_ed25519`

VPN monitoring files are not the active CRM deployment source of truth.

## Begin And Parity

```powershell
git status --short --branch
git fetch origin autostopcrm-v1 --prune
git rev-parse --short HEAD
git rev-parse --short origin/autostopcrm-v1
```

Server parity:

```powershell
if (-not $env:AUTOSTOPCRM_SSH_KEY) {
    $candidate = Join-Path $HOME ".ssh\autostopcrm_server_ed25519"
    if (Test-Path -LiteralPath $candidate) { $env:AUTOSTOPCRM_SSH_KEY = $candidate }
}
ssh -i $env:AUTOSTOPCRM_SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes root@crm.autostopcrm.ru "cd /opt/autostopcrm && git status --short --branch && git rev-parse --short HEAD && git rev-parse --short origin/autostopcrm-v1 && docker compose ps"
```

If the key is missing, use the local secret bundle. Do not try stale identity
names, password auth, or ad-hoc keys.

## Toolchain Baseline

Windows workstation bootstrap:

```powershell
.\scripts\bootstrap_tools.ps1
```

The bootstrap installs missing user-level CLI tools into
`%LOCALAPPDATA%\Programs\AutostopCRMTools\bin`, adds that directory to the user
`PATH`, creates lightweight command shims in `%LOCALAPPDATA%\Microsoft\WindowsApps`
for already-running shells, sets `AUTOSTOPCRM_SSH_KEY` when
`%USERPROFILE%\.ssh\autostopcrm_server_ed25519` exists, installs Python
dependencies, installs the git `pre-commit` hook, and verifies Playwright
Chromium.

Read-only toolchain audit:

```powershell
.\scripts\toolchain_doctor.ps1
.\scripts\toolchain_doctor.ps1 -Format json
```

Required local tools for fast maintenance are `git`, Python `.venv`, `gh`,
`jq`, `7z`, PowerShell 7, Node/npm, SSH, Playwright Chromium, and the
AutostopCRM MCP connector. `gh auth status` may warn until the operator runs
`gh auth login`; do not store GitHub tokens in the repository.

Docker Desktop is optional locally. The normal production deploy path uses
server-side Docker Compose in `/opt/autostopcrm`. Install Docker Desktop only
with an explicit local-container need:

```powershell
.\scripts\bootstrap_tools.ps1 -InstallDockerDesktop
```

Server tooling stays minimal. Do not install `gh`, Node/npm, or archive tools on
the server unless a concrete maintenance task needs them. Treat untracked VPN or
server-local files in `/opt/autostopcrm` as preservation candidates: classify
and move or split them into a separate repository only after review.

## Release Checklist

Before pushing code or docs that affect runtime, deploy, contracts, UI, or
operator instructions:

```powershell
.\scripts\doctor.ps1
.\scripts\toolchain_doctor.ps1
.\scripts\run_checks.ps1
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
python scripts\docs_audit.py --format text
python scripts\code_health_audit.py --format text
python scripts\audit_localization.py
python scripts\check_web_assets_js.py
python scripts\browser_smoke.py
```

Documentation-only minimum:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check scripts\docs_audit.py tests\test_docs_audit.py
.\.venv\Scripts\python.exe -m ruff check scripts\docs_audit.py tests\test_docs_audit.py
python -m unittest tests.test_docs_audit -v
python scripts\docs_audit.py --format text
python scripts\audit_localization.py
```

Optional secret/access bundle scan:

```powershell
$env:AUTOSTOPCRM_SECRET_BUNDLE = "C:\path\to\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер"
python scripts\docs_audit.py --format text --secret-bundle $env:AUTOSTOPCRM_SECRET_BUNDLE
```

The scan reports stale instruction classes and paths only; it must not print
token or key values.

## Performance Smoke

Local temp server:

```powershell
python scripts\perf_probe.py --local-temp-server --iterations 1 --max-snapshot-gzip-ms 1200 --max-snapshot-gzip-bytes 120000 --max-revision-ms 800 --max-get-card-ms 800
python scripts\perf_mcp.py --local-temp-server --iterations 3
python scripts\perf_workflows.py --local-temp-server --iterations 3
```

Production read-only:

```powershell
python scripts\perf_probe.py --base-url https://crm.autostopcrm.ru --iterations 5 --max-snapshot-gzip-ms 800 --max-snapshot-gzip-bytes 80000 --max-revision-ms 500 --max-get-card-ms 500
python scripts\perf_mcp.py --mcp-url https://crm.autostopcrm.ru/mcp --iterations 5
```

Production MCP write scenarios stay disabled unless a separate owner approval
explicitly allows live writes.

## Maintenance Safety

State diagnostics are read-only first:

```powershell
python scripts\state_size_report.py --json
python scripts\state_size_report.py --benchmark-iterations 3 --json
python scripts\compact_audit_events.py --dry-run --json
```

Heavy audit events keep compact details in active `state.json`; full
`before/after` values live in append-only `audit-archive`. Do not edit
`state.json` or `audit-archive` manually. Run
`compact_audit_events.py --apply --backup` only after dry-run review and backup
approval.

Operator activity lives under:

```text
operator-activity/current
operator-activity/details
operator-activity/aggregates
```

Use `scripts/operator_activity_maintenance.py --dry-run --json` before cleanup;
apply only with `--apply --backup`.

### Finance Audit-First

Finance audit is read-only first:

```powershell
python scripts\finance_audit_report.py --base-url https://crm.autostopcrm.ru --format text --issue-limit 50
```

`/api/finance_audit/apply_safe_fixes` and repair-order number corrections are
maintenance-only. Use owner-reviewed reports, backup decisions, dry-run checks,
and explicit approval. Do not edit cashbox data manually. Operator cashbox UI is
journal-first and must not expose finance-audit/reconciliation entrypoints.

Repair-order number audit:

```powershell
python scripts\repair_order_number_audit.py --base-url https://crm.autostopcrm.ru --format text --issue-limit 50
```

## Deploy

Normal path:

1. Commit intended local changes.
2. Push to `origin/autostopcrm-v1`.
3. Deploy from `/opt/autostopcrm`.
4. Verify local/GitHub/server `HEAD`.
5. Run live smoke and performance checks.

Server command:

```powershell
ssh -i $env:AUTOSTOPCRM_SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes root@crm.autostopcrm.ru "cd /opt/autostopcrm && AUTOSTOP_DEPLOY_BRANCH=autostopcrm-v1 AUTOSTOP_VERIFY_PUBLIC_HTTPS=1 ./deploy.sh"
```

Useful deploy variables:

- `AUTOSTOP_DEPLOY_BRANCH` - branch to fetch/reset; default `autostopcrm-v1`.
- `AUTOSTOP_DEPLOY_REMOTE` - git remote; default `origin`.
- `AUTOSTOP_SKIP_GIT_SYNC=1` - rebuild current checkout without fetch/reset.
- `AUTOSTOP_COMPOSE_SERVICE` - compose service; default `autostopcrm`.
- `AUTOSTOP_VERIFY_PUBLIC_HTTPS=1` - run public HTTPS smoke.
- `AUTOSTOP_PUBLIC_SITE_URL`, `AUTOSTOP_PUBLIC_MCP_URL` - public smoke URLs.
- `AUTOSTOP_SMOKE_OPERATOR_USERNAME`, `AUTOSTOP_SMOKE_OPERATOR_PASSWORD` -
  smoke credentials.
- `AUTOSTOP_SMOKE_ATTEMPTS`, `AUTOSTOP_SMOKE_DELAY_SECONDS` - deploy smoke
  retry budget; defaults are 20 attempts and 3 seconds.
- `AUTOSTOP_DESKTOP_INSTRUCTION_PATH` - server copy target for
  `AUTOSTOPCRM_FULL_INSTRUCTION.txt`.
- `AUTOSTOP_DEPLOY_LOCK_PATH` - deploy/watchdog lock path; default
  `.autostop-deploy.lock`.
- `AUTOSTOP_INSTALL_WATCHDOG=0` - skip watchdog install.

`deploy.sh` loads server-local `.env` before resolving smoke credentials,
then fetches and resets tracked files, builds containers, waits for
health, runs local connector smoke, optionally runs public HTTPS smoke, copies
the short server instruction, and installs the watchdog timer on systemd hosts.

## Production Verification

From server:

```bash
cd /opt/autostopcrm
git status --short --branch
git rev-parse --short HEAD
git rev-parse --short origin/autostopcrm-v1
docker compose ps
docker compose exec -T autostopcrm python scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --mcp-url https://crm.autostopcrm.ru/mcp --operator-username "${AUTOSTOP_SMOKE_OPERATOR_USERNAME:?set smoke username}" --operator-password "${AUTOSTOP_SMOKE_OPERATOR_PASSWORD:?set smoke password}" --expect-admin
docker compose exec -T autostopcrm python scripts/docs_audit.py --format text
```

From local machine:

```powershell
python scripts\check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url https://crm.autostopcrm.ru --mcp-url https://crm.autostopcrm.ru/mcp --operator-username $env:AUTOSTOP_SMOKE_OPERATOR_USERNAME --operator-password $env:AUTOSTOP_SMOKE_OPERATOR_PASSWORD --expect-admin
```

Manual UI smoke after UI changes:

- board loads after operator login;
- topbar modules open;
- card open/save/move and card journal work;
- clients, repair orders, cashboxes, employees, files, and archive modals open;
- repair-order executor salary gear calculates `5000 + 45% = 11750`, preserves
  `0%`, and reset clears row override;
- employee `+ СМЕНЫ` accrual appears as `ВЫПЛАТА ЗА СМЕНЫ`;
- employee salary ledger and `ОТЧЕТ` printable reconciliation act open without
  CRM chrome;
- nested modals close one level at a time;
- cashbox journal shows compact operation rows and `from -> to` transfer pairs;
- public anonymous writes remain blocked.

The automated `browser_smoke.py` includes
`employee_shift_accrual_manual_salary`, operator-to-employee material executor
defaults, modal ladder checks, and payroll report/reconciliation coverage.

## Watchdog And Telegram AI

`deploy.sh` installs `autostopcrm-watchdog.timer` by default. It checks:

- local host API upstream: `http://127.0.0.1:8000/api/health`;
- local host MCP upstream: `http://127.0.0.1:8001/mcp`;
- public CRM page: `https://crm.autostopcrm.ru`.

Useful commands:

```bash
systemctl status autostopcrm-watchdog.timer
systemctl status autostopcrm-watchdog.service
journalctl -u autostopcrm-watchdog.service -n 100 --no-pager
```

Telegram AI service: `autostopcrm-telegram-ai`. It uses long polling, opens no
public port, talks to `http://autostopcrm:41731`, and keeps secrets in
server-local `telegram-ai.env`. Never remove or commit `telegram-ai.env`.

## Production Cautions

- GitHub branch `autostopcrm-v1` is the tracked production source of truth.
- Server mirror is disposable for tracked files but may contain required
  untracked runtime/local files.
- Do not delete server-local `.env`, `telegram-ai.env`, data, or secret files.
- Do not edit production state, audit archives, operator activity, or cashbox
  data manually.
- Do not trust stale docs over code, tests, or live `HEAD`.
- Always use GitHub-first change, then deploy.

## Documentation Policy

Canonical active docs:

- `README.md`
- `docs/OPERATIONS_RUNBOOK.md`
- `API_GUIDE.md`
- `MCP_GUIDE.md`
- `CHATGPT_CONNECTOR_SETUP.md`
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt`

`requirements.txt` and `requirements-dev.txt` are manifests. Release copies and
secret-bundle copies must either match canonical docs or be clearly historical.
Do not add one-off plans or frozen reports to active docs.
