# AutoStop CRM Operations Runbook

This is the source of truth for local verification, GitHub/server sync,
production deploy, live smoke, performance checks, and maintenance safety.

## Current Endpoints

- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`
- production repo: `/opt/autostopcrm`
- branch: `autostopcrm-v1`
- compose services: `autostopcrm`, `autostopcrm-telegram-ai`

The runbook covers the CRM repository and in-repo Telegram AI worker. VPN
monitoring files are not the active CRM deployment source of truth.

## Начало Работы

Local checkout (use the actual clone root on the workstation):

```powershell
Set-Location <current AutoStop CRM clone root>
```

Local parity:

```powershell
git status --short --branch
git rev-parse --short HEAD
git fetch origin autostopcrm-v1 --prune
git rev-parse --short origin/autostopcrm-v1
```

Production parity from this workstation:

```powershell
if (-not $env:AUTOSTOPCRM_SSH_KEY) {
    $candidate = Join-Path $HOME ".ssh\autostopcrm_server_ed25519"
    if (Test-Path -LiteralPath $candidate) {
        $env:AUTOSTOPCRM_SSH_KEY = $candidate
    }
}
Test-Path -LiteralPath $env:AUTOSTOPCRM_SSH_KEY
ssh -i $env:AUTOSTOPCRM_SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes root@crm.autostopcrm.ru "cd /opt/autostopcrm && git status --short --branch && git rev-parse --short HEAD && git rev-parse --short origin/autostopcrm-v1 && docker compose ps"
```

Canonical SSH identity file name is `autostopcrm_server_ed25519`. Prefer
`$env:AUTOSTOPCRM_SSH_KEY`; the usual workstation fallback is
`$HOME\.ssh\autostopcrm_server_ed25519`. If the key is missing, inspect the
local secret bundle first. Do not try stale identity names, password auth, or
new ad-hoc keys.

## Release Checklist

Before pushing code or docs that affect runtime, deployment, contracts, UI, or
operator instructions:

```powershell
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
python scripts\docs_audit.py --format text
python scripts\audit_localization.py
python scripts\check_web_assets_js.py
python scripts\browser_smoke.py
```

`scripts\browser_smoke.py` runs a temp-runtime payroll chain smoke: a closed
repair order with per-row salary override is checked through payroll report,
employee ledger, monthly salary report, and the printable reconciliation act.

For documentation-only changes, the minimum gate is:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check scripts\docs_audit.py tests\test_docs_audit.py
.\.venv\Scripts\python.exe -m ruff check scripts\docs_audit.py tests\test_docs_audit.py
python -m unittest tests.test_docs_audit -v
python scripts\docs_audit.py --format text
python scripts\audit_localization.py
```

Secret/access bundle stale-instruction scan:

```powershell
$env:AUTOSTOPCRM_SECRET_BUNDLE = "C:\path\to\КЛЮЧЕВАЯ ДОКУМЕНТАЦИЯ CRM VPN Сервер"
python scripts\docs_audit.py --format text --secret-bundle $env:AUTOSTOPCRM_SECRET_BUNDLE
```

This scan reports stale instruction classes and file paths only. It must not
print token or key values.

## Performance Smoke

Local temp server:

```powershell
python scripts\perf_probe.py --local-temp-server --iterations 1 --max-snapshot-gzip-ms 1200 --max-snapshot-gzip-bytes 120000 --max-revision-ms 800 --max-get-card-ms 800
python scripts\perf_mcp.py --local-temp-server --iterations 3
python scripts\perf_workflows.py --local-temp-server --iterations 3
```

`perf_workflows.py --local-temp-server` includes browser timings for opening a
repair order salary override popover, the employee salary ledger, and the
salary reconciliation print document.

## Workspace Cleanup Hygiene

Before release or after browser-heavy QA, inspect local growth and ignored
artifacts:

```powershell
git status --ignored --short
Get-ChildItem -Force | Where-Object { $_.PSIsContainer } | ForEach-Object {
    $bytes = (Get-ChildItem -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    [pscustomobject]@{ Name = $_.Name; MB = [math]::Round(($bytes / 1MB), 2) }
} | Sort-Object MB -Descending
```

Safe cleanup candidates are ignored temp folders such as `output\playwright`,
`tmp\local-crm`, `.pytest_cache`, `.ruff_cache`, and `__pycache__` directories.
Keep `dist\`, `release\`, historical audit outputs, real data, and secret files
unless a separate release/archive task explicitly says otherwise.

Production read-only:

```powershell
python scripts\perf_probe.py --base-url https://crm.autostopcrm.ru --iterations 5 --max-snapshot-gzip-ms 800 --max-snapshot-gzip-bytes 80000 --max-revision-ms 500 --max-get-card-ms 500
python scripts\perf_mcp.py --mcp-url https://crm.autostopcrm.ru/mcp --iterations 5
```

Production MCP write scenarios must stay disabled unless a separate owner
approval explicitly allows live writes. Compare MCP tool names; optional manager
tools such as `estimate_repair_work_cost` can exist only when `AutostopManager`
is mounted.

## Размер State И Audit Store

State diagnostics are read-only first:

```powershell
python scripts\state_size_report.py --json
python scripts\state_size_report.py --benchmark-iterations 3 --json
python scripts\compact_audit_events.py --dry-run --json
```

Heavy audit events keep compact details in active `state.json`; full
`before/after` values live in append-only `audit-archive` under the same data
directory. Do not edit `state.json` or `audit-archive` manually.

Live compaction policy:

```powershell
python scripts\compact_audit_events.py --dry-run --json
python scripts\compact_audit_events.py --apply --backup
```

Run `--apply --backup` only after reviewing the dry-run report and confirming
backup policy. The maintenance script takes the state file lock, creates backup
when requested, appends full details to `audit-archive`, and rewrites active
state with compact details.

## Operator Activity Journal

Operator activity is stored outside `state.json` under the same runtime data
directory:

```text
operator-activity/current
operator-activity/details
operator-activity/aggregates
```

The admin journal keeps recent compact rows and detail records for operational
review. Older rows are eligible for R3 compaction after aggregates are written.
Maintenance is read-only first:

```powershell
python scripts\operator_activity_maintenance.py --dry-run --json
python scripts\operator_activity_maintenance.py --apply --backup
```

Run `--apply --backup` only after reviewing the dry-run report. Do not expose
activity cleanup as a casual UI button and do not edit activity JSONL files
manually.

## Finance Audit-First

Finance audit is read-only first:

```powershell
python scripts\finance_audit_report.py --base-url https://crm.autostopcrm.ru --format text --issue-limit 50
```

`/api/finance_audit/apply_safe_fixes` is maintenance-only. Use it only after an
owner-reviewed report, dry-run result, backup decision, and explicit approval.
Do not edit cashbox data manually.

Operator cashbox UI is journal-first. It must not show a finance-audit or
reconciliation entrypoint. Keep audit as API/CLI/MCP diagnostics.

## Repair Order Number Audit

Repair-order number is immutable after first assignment. Historical corrections
are maintenance-only:

```powershell
python scripts\repair_order_number_audit.py --format text --issue-limit 50
```

Do not correct order numbers through normal UI/API/MCP flows. Use backup,
dry-run, owner approval, and post-fix finance audit.

## Deploy

Normal path:

1. Commit intended local changes.
2. Push to `origin/autostopcrm-v1`.
3. Deploy on production through `deploy.sh`.
4. Verify local/GitHub/server `HEAD`.
5. Run live smoke and performance checks.

Server command:

```powershell
if (-not $env:AUTOSTOPCRM_SSH_KEY) {
    $candidate = Join-Path $HOME ".ssh\autostopcrm_server_ed25519"
    if (Test-Path -LiteralPath $candidate) {
        $env:AUTOSTOPCRM_SSH_KEY = $candidate
    }
}
ssh -i $env:AUTOSTOPCRM_SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes root@crm.autostopcrm.ru "cd /opt/autostopcrm && AUTOSTOP_DEPLOY_BRANCH=autostopcrm-v1 AUTOSTOP_VERIFY_PUBLIC_HTTPS=1 ./deploy.sh"
```

Useful deploy variables:

- `AUTOSTOP_DEPLOY_BRANCH` - branch to fetch/reset; default `autostopcrm-v1`.
- `AUTOSTOP_DEPLOY_REMOTE` - git remote; default `origin`.
- `AUTOSTOP_SKIP_GIT_SYNC=1` - rebuild current checkout without fetch/reset.
- `AUTOSTOP_COMPOSE_SERVICE` - compose service; default `autostopcrm`.
- `AUTOSTOP_VERIFY_PUBLIC_HTTPS=1` - run public HTTPS smoke after local smoke.
- `AUTOSTOP_PUBLIC_SITE_URL`, `AUTOSTOP_PUBLIC_MCP_URL` - public smoke URLs.
- `AUTOSTOP_SMOKE_OPERATOR_USERNAME`, `AUTOSTOP_SMOKE_OPERATOR_PASSWORD` -
  smoke credentials.
- `AUTOSTOP_DESKTOP_INSTRUCTION_PATH` - server copy target for
  `AUTOSTOPCRM_FULL_INSTRUCTION.txt`.
- `AUTOSTOP_INSTALL_WATCHDOG=0` - skip watchdog install.

`deploy.sh` fetches the target branch, resets tracked files to `FETCH_HEAD`,
builds containers, waits for health, runs local connector smoke, optionally
runs public HTTPS smoke, copies the short server instruction, and installs the
watchdog timer on systemd hosts.

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
- card open/save/move works;
- card journal is readable;
- clients, repair orders, cashboxes, employees, files, and archive modals open;
- repair-order executor salary gear opens, calculates `5000 + 45% = 11750`,
  preserves `0%`, and reset clears the row override before saving;
- employee salary ledger and `ОТЧЕТ` printable reconciliation act open without
  CRM chrome and show the applied salary scheme;
- nested modals close one level at a time;
- cashbox journal shows compact operation rows and `from -> to` transfer pairs;
- public anonymous writes remain blocked.

## Production Watchdog

`deploy.sh` installs and enables `autostopcrm-watchdog.timer` by default. The
watchdog runs `scripts/production_watchdog.py` and checks:

- local host API upstream: `http://127.0.0.1:8000/api/health`;
- local host MCP upstream: `http://127.0.0.1:8001/mcp`;
- public CRM page: `https://crm.autostopcrm.ru`.

Useful commands:

```bash
systemctl status autostopcrm-watchdog.timer
systemctl status autostopcrm-watchdog.service
journalctl -u autostopcrm-watchdog.service -n 100 --no-pager
systemctl status autostopcrm-watchdog.service --no-pager
```

## Telegram AI Worker

Service: `autostopcrm-telegram-ai`.

- long polling, no public port;
- internal CRM API: `http://autostopcrm:41731`;
- secrets live in server-local `telegram-ai.env`;
- runtime audit/state files live under `/root/.minimal-kanban/telegram_ai/`.

Never remove or commit `telegram-ai.env`.

## Production Cautions

- GitHub branch `autostopcrm-v1` is the tracked production source of truth.
- Server mirror is disposable for tracked files but can contain required
  untracked runtime/local files.
- Do not delete server-local `.env`, `telegram-ai.env`, data, or secret files.
- Do not edit production state or cashbox data manually.
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

Release copies and secret-bundle copies must either match these docs or be
clearly marked historical. Do not add one-off plans or frozen reports to active
docs.
