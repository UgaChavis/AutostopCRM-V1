# AutoStop CRM Operations Runbook

This is the source of truth for local verification, GitHub/server sync,
production deploy, live smoke, performance checks, watchdog, and maintenance
safety.

## Current Runtime

- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`
- production repo: `/opt/autostopcrm`
- branch: `autostopcrm-v1`
- compose service: `autostopcrm`
- canonical SSH identity: `autostopcrm_server_ed25519`

VPN monitoring files are not the active CRM deployment source of truth.

## Production Server Map

Active checkouts and services on `crm.autostopcrm.ru`:

- `/opt/autostopcrm` - production AutoStop CRM checkout on branch
  `autostopcrm-v1`. Docker Compose project `autostopcrm` runs container
  `autostopcrm` from image `autostopcrm-autostopcrm`. Host ports are
  `127.0.0.1:8000 -> 41731/tcp` for the API/UI and
  `127.0.0.1:8001 -> 41831/tcp` for MCP. Nginx routes
  `crm.autostopcrm.ru` to this project, including `/mcp`.
- `/opt/autostop-app` - active public app/storefront checkout. Docker Compose
  project `autostop-app` runs `autostop-app` and `autostop-db`; the app is
  published as `127.0.0.1:8010 -> 8000/tcp`. Nginx routes
  `autostop24.shop`, `autostop24.pro`, and the server `:8080` listener to
  this app. Its active Docker volumes are `autostop-app_postgres_data` and
  `autostop-app_uploads_data`.
- `/opt/AutostopManager` - separate manager checkout. It is not the active CRM
  production source and no active Docker container was tied to it in the
  2026-06-02 server inventory. Treat local modifications there as parallel
  work: inspect, document, or ask before reset/delete.
- `/opt/crm-2.0` - separate development checkout. It is not the active CRM
  production source and no active Docker container was tied to it in the
  2026-06-02 server inventory.
- `amnezia-awg2` - active Amnezia/WireGuard Docker container. It listens on
  UDP `47895`; `autostopvpn-udp443-forward.service` is an active systemd
  helper. Do not remove VPN containers, configs, or systemd units during CRM
  cleanup.

Expected active systemd services/timers:

- `docker.service`, `nginx.service`, `autostopcrm-watchdog.timer`,
  `autostopvpn-udp443-forward.service`, and `autostop-gmail-relay.service`
  are active.
- `autostopcrm-watchdog.service` is usually inactive/static between timer
  runs.
- Disabled Amnezia dashboard or traffic-collector units are not CRM runtime
  blockers, but do not delete their files without a separate VPN maintenance
  task.

Server filesystem cleanup boundaries:

- Safe candidates after read-only verification: Docker build cache, stopped
  orphan containers, inactive volumes with obsolete compose project names,
  old root-level release tarballs, old `/opt/autostop-app.previous-*` and
  `/opt/autostop-app.backup-*` directories when a newer control copy remains,
  old `/opt/autostop-app-backups` tar/tar.gz/dump copies older than 7 days
  beyond the minimum retained recent set, empty accidental files, and data
  directories from integrations that have already been removed from compose,
  code, tests, and docs.
- Always keep: `.env` files, credentials, production state, Postgres/upload
  volumes, `/root/autostopcrm-backups`, audit archives, operator activity,
  active nginx configs, active systemd files, active VPN configs, and dirty
  checkouts belonging to parallel work.
- Before cleanup, capture `df -hT /`, `du -hxd1 /opt /root /var`,
  `docker system df`, `docker ps -a`, `docker volume ls`, `docker compose ls`,
  nginx routes, systemd statuses, and git status for each checkout.
- After cleanup, rerun the same disk/Docker checks plus `nginx -t`, CRM
  health, MCP smoke, and VPN listener checks.

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

Expected current workstation status after bootstrap:

- `toolchain_doctor.ps1 -SkipServer -Strict` exits cleanly;
- `github:auth` is `PASS` for account `UgaChavis`;
- `docker` may be `SKIP` locally because server-side Docker Compose is the
  production deploy path.

Docker Desktop is optional locally. The normal production deploy path uses
server-side Docker Compose in `/opt/autostopcrm`. Install Docker Desktop only
with an explicit local-container need:

```powershell
.\scripts\bootstrap_tools.ps1 -InstallDockerDesktop
```

Server tooling stays minimal. Do not install `gh`, Node/npm, or archive tools on
the server unless a concrete maintenance task needs them.

Server checkout hygiene:

- `/opt/autostopcrm` should contain tracked CRM files plus required untracked
  runtime files only, such as `.env`, data, and backups.
- AutostopVPN source copies and duplicate VPN docs do not belong in the CRM
  checkout. If they reappear, first confirm active services reference
  `/usr/local/bin`, `/usr/local/sbin`, or `/etc/systemd/system`, then archive
  the untracked copies outside `/opt/autostopcrm` before deleting them.
- Never delete server-local credentials, production data, audit archives,
  operator activity, or compose env files during checkout cleanup.

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

Mandatory stage-1 storage/cache gate (synthetic production-sized state, no
business data):

```powershell
python scripts\perf_workflows.py --synthetic-state-profile current-production --stage1-only --skip-browser --warmup-iterations 2 --iterations 20 --max-backend-write-ms 600 --max-storage-write-ms 550 --max-revision-server-ms 20 --max-get-card-direct-ms 20
```

The profile contains approximately 620 cards, 4000 clients, 5000 events, and
1500 cash transactions and must remain at least 10 MB. Thresholds apply to p95,
not the average. CI uploads `perf-stage1.json` even when the gate fails.

Local temp server/read-only API:

```powershell
python scripts\perf_probe.py --local-temp-server --warmup-iterations 2 --iterations 5 --max-snapshot-gzip-ms 1200 --max-snapshot-gzip-bytes 120000 --max-revision-ms 800 --max-revision-server-ms 20 --max-get-card-ms 800
python scripts\perf_mcp.py --local-temp-server --iterations 3
python scripts\perf_workflows.py --local-temp-server --iterations 3
```

Before deployment, the same write gate may use the exact production state. The
script always benchmarks a temporary copy; it must never write to the supplied
`state.json`:

```bash
cd /opt/autostopcrm
.venv/bin/python scripts/perf_workflows.py \
  --state-file data/state.json \
  --stage1-only \
  --skip-browser \
  --warmup-iterations 2 \
  --iterations 20 \
  --max-backend-write-ms 600 \
  --max-storage-write-ms 550 \
  --max-revision-server-ms 20 \
  --max-get-card-direct-ms 20
```

After deployment, run the public read-only gate. The revision limit uses the
backend `Server-Timing: app` p95; public network/TLS latency is measured
separately:

```powershell
python scripts\perf_probe.py --base-url https://crm.autostopcrm.ru --warmup-iterations 2 --iterations 20 --max-snapshot-gzip-ms 800 --max-snapshot-gzip-bytes 80000 --max-revision-ms 500 --max-revision-server-ms 20 --max-get-card-ms 150
python scripts\perf_mcp.py --mcp-url https://crm.autostopcrm.ru/mcp --iterations 5
```

API `Server-Timing` must contain finite, non-negative `app`, `total`, `lock`,
`service_lock`, `store_lock`, `file_lock`, `normalize`, `serialize`, `write`,
and `storage` values. Write logs include the same phase breakdown without
payload data.

Production MCP write scenarios stay disabled unless a separate owner approval
explicitly allows live writes.

Emergency fast-write rollback: set `MINIMAL_KANBAN_FAST_STATE_WRITES=0` in the
server-local `.env`, recreate only the `autostopcrm` service, and rerun connector
and performance smoke. This switches CardService to the legacy normalizing
writer; it does not change state format. Remove the override only after the
failed path is diagnosed and tested.

```bash
docker compose up -d --no-deps --force-recreate autostopcrm
```

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

Historical financial cleanup is a destructive state-sanitization helper, not a
normal repair flow. Run it in read-only mode first:

```powershell
python scripts\clear_financial_history.py --dry-run --state-file .\path\to\state.json
```

Apply only after reviewing the dry-run summary and only with a backup:

```powershell
python scripts\clear_financial_history.py --apply --backup --state-file .\path\to\state.json
```

Do not run it against production `state.json` without a separate owner-reviewed
maintenance plan and verified backup.

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

## Watchdog

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

## Production Cautions

- GitHub branch `autostopcrm-v1` is the tracked production source of truth.
- Server mirror is disposable for tracked files but may contain required
  untracked runtime/local files.
- Untracked AutostopVPN source/docs under `/opt/autostopcrm` are not required
  CRM runtime files after they have been archived and active service paths have
  been checked.
- Do not delete server-local `.env`, data, or secret files.
- Do not edit production state, audit archives, operator activity, or cashbox
  data manually.
- Do not trust stale docs over code, tests, or live `HEAD`.
- Always use GitHub-first change, then deploy.

## Documentation Policy

Canonical active docs:

- `AGENTS.md`
- `README.md`
- `docs/OPERATIONS_RUNBOOK.md`
- `API_GUIDE.md`
- `MCP_GUIDE.md`
- `CHATGPT_CONNECTOR_SETUP.md`
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt`

`requirements.txt` and `requirements-dev.txt` are manifests. Release copies and
secret-bundle copies must either match canonical docs or be clearly historical.
Do not add one-off plans or frozen reports to active docs.

Documentation cleanup loop:

1. Inventory tracked `*.md` and `*.txt` files before editing.
2. Delete or archive duplicate, historical, or one-off docs instead of keeping
   parallel sources of truth.
3. Update every kept document, then run `python scripts\docs_audit.py --format text`.
4. Keep `AGENTS.md` as the agent-only startup file, `README.md` as the short
   project map, this runbook as the operational source of truth, and
   API/MCP/ChatGPT docs as narrow contract references.
