# AutoStop CRM Operations Runbook

This is the operational source of truth for workstation checks, production
parity, deployment, rollback, live verification, performance gates, and
maintenance safety.

## Production Runtime

| Item | Current value |
| --- | --- |
| Source branch | `origin/autostopcrm-v1` |
| Server checkout | `/opt/autostopcrm` |
| CRM | `https://crm.autostopcrm.ru` |
| MCP | `https://crm.autostopcrm.ru/mcp` |
| SSH host | `root@crm.autostopcrm.ru` |
| Default SSH key | `~/.ssh/autostopcrm_server_ed25519` |
| Host data | `/opt/autostopcrm/data` |
| Container data | `/root/.minimal-kanban` |

`docker-compose.yml` defines three CRM-project services:

| Service/container | Purpose | Host binding |
| --- | --- | --- |
| `autostopcrm` | UI, API, MCP, agent runtime | `127.0.0.1:8000 -> 41731`, `127.0.0.1:8001 -> 41831` |
| `autostop-searxng` | local search provider | `127.0.0.1:8890 -> 8080` |
| `autostop-crawl4ai` | local browser/extraction provider | `127.0.0.1:11235 -> 11235` |

The CRM service depends on healthy SearXNG and Crawl4AI. A normal release
replaces only `autostopcrm`; it does not recreate those dependencies or
unrelated host services.

Do not put server-wide VPN, storefront, firewall, or unrelated checkout
inventory in this repository. Inspect those systems live only when a task
explicitly includes them.

## Git Parity

On the workstation:

```powershell
git status --short --branch
git fetch origin autostopcrm-v1 --prune
git rev-parse HEAD
git rev-parse origin/autostopcrm-v1
```

Preserve every pre-existing user change. Before any production operation,
compare the server:

```powershell
if (-not $env:AUTOSTOPCRM_SSH_KEY) {
    $candidate = Join-Path $HOME ".ssh\autostopcrm_server_ed25519"
    if (Test-Path -LiteralPath $candidate) { $env:AUTOSTOPCRM_SSH_KEY = $candidate }
}
ssh -i $env:AUTOSTOPCRM_SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes root@crm.autostopcrm.ru "cd /opt/autostopcrm && git status --short --branch && git rev-parse HEAD && git rev-parse origin/autostopcrm-v1 && docker compose ps"
```

Stop if the production checkout is dirty or revisions do not match the
intended release. Do not reset a dirty checkout.

## Workstation Setup

Initial setup:

```powershell
.\scripts\setup_dev.ps1 -InstallGitHooks
```

Optional convenience bootstrap:

```powershell
.\scripts\bootstrap_tools.ps1
```

`bootstrap_tools.ps1` installs portable `gh`, `jq`, and `7z`, adds their local
bin directory to user PATH, sets `AUTOSTOPCRM_SSH_KEY` when the default key
exists, runs project setup, installs Playwright Chromium, and runs the
toolchain doctor. It installs Docker Desktop only with
`-InstallDockerDesktop`; GitHub login is explicit with `-GithubLogin`.

Read-only audit:

```powershell
.\scripts\doctor.ps1
.\scripts\toolchain_doctor.ps1
.\scripts\toolchain_doctor.ps1 -Format json
```

The toolchain doctor requires Git, `gh`, `jq`, `7z`, the project venv, and the
SSH key. Node/npm, PowerShell 7, and local Docker are optional checks. GitHub
CLI authentication may be a warning until a GitHub operation needs it; never
store a GitHub token in the repository.

## Release Checklist

Documentation-only minimum:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check scripts\docs_audit.py tests\test_docs_audit.py
.\.venv\Scripts\python.exe -m ruff check scripts\docs_audit.py tests\test_docs_audit.py
.\.venv\Scripts\python.exe -m unittest tests.test_docs_audit -v
.\.venv\Scripts\python.exe scripts\docs_audit.py --format text
.\.venv\Scripts\python.exe scripts\audit_localization.py
```

`docs_audit.py` audits this repository only by default. Cross-repository and
local-environment scans are opt-in:

```powershell
.\.venv\Scripts\python.exe scripts\docs_audit.py --format text --manager-root C:\path\to\AutostopManager
.\.venv\Scripts\python.exe scripts\docs_audit.py --format text --include-skills
.\.venv\Scripts\python.exe scripts\docs_audit.py --format text --secret-bundle C:\path\to\private-access-bundle
```

The secret-bundle scan reports stale instruction classes, never secret values.

For shared Python/service/API/MCP behavior:

```powershell
.\scripts\run_checks.ps1
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
```

Add the smallest relevant checks:

- UI assets:
  `.\.venv\Scripts\python.exe scripts\check_web_assets_js.py` and
  `.\.venv\Scripts\python.exe scripts\browser_smoke.py`;
- MCP/runtime:
  `.\.venv\Scripts\python.exe scripts\check_agent_gateway_v2.py`;
- localization:
  `.\.venv\Scripts\python.exe scripts\audit_localization.py`;
- repository health:
  `.\.venv\Scripts\python.exe scripts\code_health_audit.py --format text`.

## Performance Smoke

The mandatory stage-1 gate uses synthetic production-sized state and does not
touch business data:

```powershell
.\.venv\Scripts\python.exe scripts\perf_workflows.py --synthetic-state-profile current-production --stage1-only --skip-browser --warmup-iterations 2 --iterations 20 --max-backend-write-ms 600 --max-storage-write-ms 550 --max-revision-server-ms 20 --max-get-card-direct-ms 20
```

Local read/workflow checks:

```powershell
.\.venv\Scripts\python.exe scripts\perf_probe.py --local-temp-server --warmup-iterations 2 --iterations 5 --max-snapshot-gzip-ms 1200 --max-snapshot-gzip-bytes 120000 --max-revision-ms 800 --max-revision-server-ms 20 --max-get-card-ms 800
.\.venv\Scripts\python.exe scripts\perf_mcp.py --local-temp-server --iterations 3
.\.venv\Scripts\python.exe scripts\perf_workflows.py --local-temp-server --iterations 3
```

After deploy, use `check_live_connector.py` below for public HTTPS and auth.
Measure the production backends from inside the running container so no
credential appears in a process argument:

```bash
docker exec autostopcrm python scripts/perf_probe.py --base-url http://127.0.0.1:41731 --warmup-iterations 2 --iterations 20 --max-snapshot-gzip-ms 800 --max-snapshot-gzip-bytes 80000 --max-revision-ms 500 --max-revision-server-ms 20 --max-get-card-ms 150
docker exec autostopcrm python scripts/perf_mcp.py --mcp-url http://127.0.0.1:41831/mcp --token-env MINIMAL_KANBAN_MCP_BEARER_TOKEN --iterations 5
```

Production MCP write benchmarks remain disabled without a separate owner
approval. A pre-release state benchmark must use the script's temporary copy;
never point a write workflow at the live state file.

Emergency fast-writer rollback is
`MINIMAL_KANBAN_FAST_STATE_WRITES=0` in server-local `.env`, followed by
recreating only `autostopcrm` and rerunning connector/performance smoke. This
is a temporary kill switch, not a state migration.

## Maintenance Safety

Read first:

```powershell
.\.venv\Scripts\python.exe scripts\state_size_report.py --json
.\.venv\Scripts\python.exe scripts\state_size_report.py --benchmark-iterations 3 --json
.\.venv\Scripts\python.exe scripts\compact_audit_events.py --dry-run --json
```

Active `state.json` keeps compact audit details; full large `before`/`after`
payloads live in append-only `audit-archive`. Never edit either manually. Run
`compact_audit_events.py --apply --backup` only after reviewing a non-zero
dry-run result and approving a verified backup.

Operator activity lives under `operator-activity/current`,
`operator-activity/details`, and `operator-activity/aggregates`. Use
`scripts/operator_activity_maintenance.py --dry-run --json` first; apply only
with `--apply --backup`.

### Finance Audit-First

Finance audit is read-only first:

```powershell
.\.venv\Scripts\python.exe scripts\finance_audit_report.py --base-url https://crm.autostopcrm.ru --format text --issue-limit 50
```

`/api/finance_audit/apply_safe_fixes` is maintenance-only. Historical finance
cleanup is destructive and must start with:

```powershell
.\.venv\Scripts\python.exe scripts\clear_financial_history.py --dry-run --state-file .\path\to\state.json
```

Apply only under a separate owner-reviewed plan with a verified backup, using
the script's explicit `--apply --backup` mode. Never edit cashbox or payroll
ledgers by hand.

Repair-order number audit is also read-only:

```powershell
.\.venv\Scripts\python.exe scripts\repair_order_number_audit.py --base-url https://crm.autostopcrm.ru --format text --issue-limit 50
```

Numbers are immutable. `/api/correct_repair_order_number` is a blocking
compatibility endpoint that always returns
`repair_order_number_immutable`; no supported maintenance correction tool
exists.

## Production Authentication

Production MCP is bearer-only. Keep
`AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED=0`. The current endpoint supports clients
that can supply the bearer and is not a direct ChatGPT app until a real OAuth
2.1 flow is implemented.

All Gateway switches must be present in `.env` as `0` or `1`:

- `AUTOSTOP_AGENT_GATEWAY_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED`

Validate before restart:

```bash
cd /opt/autostopcrm
set -a
. ./.env
set +a
python3 scripts/validate_production_env.py --require-production
```

`deploy.sh` snapshots then rotates the CRM/Codex bearer after the image and
rollback source are ready. For manual repair or initial provisioning, use the
same helper and never pass a token on the command line:

```bash
cd /opt/autostopcrm
python3 scripts/configure_codex_mcp_auth.py rotate --generate
set -a
. /root/.config/autostopcrm/codex-mcp.env
set +a
python3 scripts/configure_codex_mcp_auth.py check
```

The helper updates server `.env`, the Codex MCP entry, and a mode-`0600`
runtime env without printing the token. Never dump `.env`, container
`.Config.Env`, a full process environment, or credential-bearing config into a
tool transcript. Report only allowlisted non-secret settings and boolean
credential checks.

## Deploy

Deploy only with explicit owner intent. Normal sequence:

1. Verify and commit only intended changes.
2. Push the commit to `origin/autostopcrm-v1`.
3. Confirm `/opt/autostopcrm` is clean and fast-forward it to that commit.
4. Run `deploy.sh`.
5. Compare workstation, remote, and server revisions.
6. Run live, UI-if-relevant, and performance smoke.

On the server, before `deploy.sh`:

```bash
cd /opt/autostopcrm
git status --short --branch
git fetch origin autostopcrm-v1
git merge --ff-only origin/autostopcrm-v1
```

Run the release from a workstation after the intended commit is checked out on
the server:

```powershell
ssh -i $env:AUTOSTOPCRM_SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes root@crm.autostopcrm.ru "cd /opt/autostopcrm && AUTOSTOP_DEPLOY_BRANCH=autostopcrm-v1 ./deploy.sh"
```

Prerequisites in server `.env` include the six Gateway switches and
`AUTOSTOP_SMOKE_OPERATOR_USERNAME` /
`AUTOSTOP_SMOKE_OPERATOR_PASSWORD`. Public HTTPS/API/MCP auth smoke is
mandatory; there is no skip flag.

The bounded release flow:

1. verifies branch/remote parity, a clean checkout, free space, Compose
   configuration, and production auth;
2. snapshots the mounted Manager code and prebuilds an immutable CRM image
   before maintenance;
3. snapshots and rotates auth with a private rollback copy;
4. creates the maintenance marker and stops only `autostopcrm`;
5. creates and verifies an atomic backup of CRM state/audit data and Manager
   SQLite;
6. starts the prebuilt image and runs internal authenticated smoke;
7. removes maintenance mode and runs mandatory public API plus exhaustive
   24-tool Gateway smoke;
8. tags the healthy release as stable and installs the watchdog.

Any failure or maintenance-budget overrun attempts a bounded rollback of
changed protected data, Manager release, auth configuration, and the previous
image. The marker remains if rollback cannot prove a healthy recovery.

Commonly reviewed overrides:

- `AUTOSTOP_DEPLOY_BRANCH` / `AUTOSTOP_DEPLOY_REMOTE`
- `AUTOSTOP_RELEASE_IMAGE` / `AUTOSTOP_STABLE_IMAGE`
- `AUTOSTOP_BUILD_RELEASE_IMAGE`
- `AUTOSTOP_MAINTENANCE_BUDGET_SECONDS`
- `AUTOSTOP_RELEASE_BACKUP_ROOT`
- `AUTOSTOP_DEPLOY_LOCK_PATH`
- `AUTOSTOP_SMOKE_ATTEMPTS` / `AUTOSTOP_SMOKE_DELAY_SECONDS`
- `AUTOSTOP_INSTALL_WATCHDOG`

Defaults and validation live in `deploy.sh`. Do not use
`docker compose up -d --build --remove-orphans` as a production shortcut: it
does not provide the release checkpoint or bounded rollback.

## Production Verification

From the server:

```bash
cd /opt/autostopcrm
git status --short --branch
git rev-parse HEAD
git rev-parse origin/autostopcrm-v1
docker compose ps
docker compose exec -T autostopcrm python scripts/validate_production_env.py --require-production
docker compose exec -T autostopcrm python scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --skip-mcp --expect-admin
docker compose exec -T autostopcrm python scripts/check_agent_gateway_v2.py --mcp-url https://crm.autostopcrm.ru/mcp --exhaustive
docker compose exec -T autostopcrm python scripts/docs_audit.py --format text
```

From a client with the current credential:

```powershell
.\.venv\Scripts\python.exe scripts\check_agent_gateway_v2.py --mcp-url https://crm.autostopcrm.ru/mcp --token-env AUTOSTOPCRM_MCP_TOKEN --exhaustive
```

After UI changes, run
`.\.venv\Scripts\python.exe scripts\browser_smoke.py` and manually verify
operator login, board/card changes, clients, repair orders and PDF export,
inventory, cashboxes, payroll, files, archive, nested modal behavior, and
anonymous write rejection.

## Watchdog

`deploy.sh` installs `autostopcrm-watchdog.timer` by default. It checks the
container, host API/MCP upstreams, and public CRM. Diagnostics:

```bash
systemctl status autostopcrm-watchdog.timer
systemctl status autostopcrm-watchdog.service
journalctl -u autostopcrm-watchdog.service -n 100 --no-pager
```

## Non-Negotiable Boundaries

- Keep server `.env`, `/opt/autostopcrm/data`,
  `/root/autostopcrm-backups`, active volumes, and required local runtime
  files.
- Never manually edit production state, audit archives, operator activity,
  repair-order numbers, cashboxes, or payroll ledgers.
- Never reset or delete a dirty parallel checkout.
- Never trust a copied release document over current code, tests, Git HEAD,
  Compose, or live health.
- Use GitHub-first changes and the bounded deploy path.

## Documentation Policy

Canonical active documents:

- `AGENTS.md`
- `README.md`
- `docs/OPERATIONS_RUNBOOK.md`
- `API_GUIDE.md`
- `MCP_GUIDE.md`
- `CHATGPT_CONNECTOR_SETUP.md`
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt`

`requirements.txt`, `requirements-dev.txt`, and
`requirements-runtime.txt` are dependency manifests. Do not add one-off plans,
frozen audit reports, release copies, or secret-access notes as active project
documentation. Delete obsolete material instead of maintaining parallel
sources of truth.

When an active document is added, deleted, or renamed, update
`scripts/docs_audit.py`, `README.md`, this section, and `.dockerignore`
together, then run:

```powershell
.\.venv\Scripts\python.exe scripts\docs_audit.py --format text
```
