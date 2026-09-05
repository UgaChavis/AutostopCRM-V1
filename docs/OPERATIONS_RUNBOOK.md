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
| Container data | `/home/autostop/.minimal-kanban` |

`docker-compose.yml` defines three CRM-project services:

| Service/container | Purpose | Host binding |
| --- | --- | --- |
| `autostopcrm` | UI, API, MCP, agent runtime | `127.0.0.1:8000 -> 41731`, `127.0.0.1:8001 -> 41831` |
| `autostop-searxng` | local search provider | `127.0.0.1:8890 -> 8080` |
| `autostop-crawl4ai` | local browser/extraction provider | `127.0.0.1:11235 -> 11235` |

The CRM service depends on healthy SearXNG and Crawl4AI. A normal release
replaces only `autostopcrm`; it does not recreate those dependencies or
unrelated host services.

Store access uses a separate precreated external network named
`autostop-store-agent`, created with `internal=true`. Its only allowed members
are `autostop-app` and `autostopcrm`; `autostop-db` and all other containers
must remain absent. Both applications retain their normal default networks.
CRM calls the mounted Manager adapter, which uses
`http://autostop-app:8000/internal/agent/v1/...`; CRM never connects directly
to the Store PostgreSQL database.

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

The production preflight is fail-closed for both repositories. CRM must be the
root checkout on branch `autostopcrm-v1`, clean including untracked files, and
its `HEAD` must equal a fresh exact fetch of `origin/autostopcrm-v1`.
`/opt/AutostopManager` must be the root of a Git checkout on branch
`AutostopManager`, clean including untracked files, with `HEAD` equal to the
freshly fetched configured remote branch. There is no skip-sync or dirty-tree
production override. Do not reset either dirty checkout.

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
.\.venv\Scripts\python.exe scripts\docs_audit.py --format text --include-skills `
  --skill-path autostopcrm-maintain
.\.venv\Scripts\python.exe scripts\docs_audit.py --format text --secret-bundle C:\path\to\private-access-bundle
```

Each `--skill-path` must name a direct, non-linked `autostopcrm-*` directory
under `CODEX_HOME/skills` (or `~/.codex/skills` when unset) with a regular
`SKILL.md`; unselected skills are ignored. To audit a custom installation,
pass the same `--skills-root C:\path\to\packages` to the installer and this
audit command, together with `--include-skills --skill-path autostopcrm-maintain`.
The secret-bundle scan reports stale instruction classes, never secret values.

The canonical CRM development skill lives in
`tools/codex/skills/autostopcrm-maintain`. Check the installed copy with
`.\.venv\Scripts\python.exe scripts\install_codex_skill.py`; exit 1 means
missing, different, or superseded CRM skills remain. Use `--apply` to install
that source and replace the four older CRM packages with this single skill.
The helper moves replaced packages into `skill-backups` beside the skills
directory, prints the backup path, and leaves other skills untouched. Restore
those saved directories to roll back. `--skills-root` selects an explicit
installation root; otherwise the helper uses `CODEX_HOME/skills` or
`~/.codex/skills`. Installed copies are artifacts, not independently edited
documentation. Developer skills are excluded from the runtime image.

The embedded agent has a separate effective prompt: a nonempty
`agent/system_prompt.md` under its app-data directory replaces the code default;
persisted context, tool descriptions, and current task hints are appended.
Inspect that precedence on the active Windows/server installation before
diagnosing restrictive behavior. Preserve custom facts, settings, and memory.

Use the fast changed-file profile while iterating:

```powershell
.\scripts\run_checks.ps1
```

For shared behavior and before publishing a substantial slice, run the one
canonical local CI profile:

```powershell
.\scripts\run_checks.ps1 -Profile ci
```

It runs full Ruff, docs, the two serial branch-coverage measurements and
`coverage_audit.py`, code health, localization, generated JavaScript,
capability and change-feed parity, mandatory browser `--profile core`, compile,
and bounded local performance gates. It uses an isolated Python cache so
coverage and compile cannot race on Windows. It never runs live probes, edits
`.env`, or accesses production.

The local profile is not a complete hosted attestation. GitHub CI remains
required for the Ubuntu/Python 3.12 harness, production Compose configuration,
and `docker-runtime-assets` container contract.

The repository-health audit classifies every tracked file as a canonical doc,
manifest, runtime code/asset, operations tool, test, or deploy configuration.
It fails on an unknown role or tracked generated artifact; `--format json`
emits the complete per-file inventory and lifecycle flags. One-off migration
scripts stay explicitly flagged for review until production evidence permits
their removal.

The health audit also enforces exact no-growth caps for every grandfathered
large module, class, and function. Branch-coverage floors live in
`scripts/coverage_baseline.json`; do not lower them to make a change pass.
GitHub Actions publishes the full coverage evidence.

Before the release-sized browser profile, run
`.\scripts\toolchain_doctor.ps1 -SkipServer`. `--profile full` fails before
creating temp runtime state when Chromium, Qt PDF, `pdfinfo`, or `pdftotext` is
missing. The mandatory `--profile core` does not require the PDF toolchain.

### Desktop Build and Release

- `scripts/build_app.ps1` creates a fresh staged PyInstaller build and
  atomically publishes `build/` and `dist/`; its DLL search uses only Windows
  and the selected Python installation, restoring the caller's PATH afterward.
- `scripts/prepare_release.ps1` calls that build, assembles the portable
  `release/Start Kanban.exe`, and publishes it from `release.staging/`.
- `scripts/run_quality_pass.ps1` synchronizes pinned dependencies and prepares
  the headless browser, runs the canonical local CI profile once,
  then `prepare_release.ps1` and `scripts/post_build_verification.py` against
  the portable executable. It is the combined local release gate; a separate
  CI run immediately before it would repeat the same checks.

Do not treat a successful `build_app.ps1` alone as a verified release.

## Performance Smoke

The mandatory stage-1 gate uses synthetic production-sized state and does not
touch business data:

```powershell
.\.venv\Scripts\python.exe scripts\perf_workflows.py --synthetic-state-profile current-production --stage1-only --skip-browser --warmup-iterations 2 --iterations 20 --max-backend-write-ms 600 --max-storage-write-ms 550 --max-revision-server-ms 20 --max-get-card-direct-ms 20 --max-list-cashboxes-ms 50 --max-feed-read-ms 50 --max-feed-replay-ms 20
```

Local read/workflow checks:

```powershell
.\.venv\Scripts\python.exe scripts\perf_probe.py --local-temp-server --warmup-iterations 2 --iterations 5 --max-snapshot-gzip-ms 1200 --max-snapshot-gzip-bytes 120000 --max-revision-ms 800 --max-revision-server-ms 20 --max-get-card-ms 800
.\.venv\Scripts\python.exe scripts\perf_mcp.py --local-temp-server --iterations 3
.\.venv\Scripts\python.exe scripts\perf_workflows.py --local-temp-server --iterations 3
```

For a release comparison, run the same harness against the baseline and candidate
checkouts with `--source-root`, three series of 20 repetitions per checkout:

```powershell
.\.venv\Scripts\python.exe scripts\perf_workflows.py --source-root <checkout> --local-temp-server --synthetic-state-profile current-production --representative-browser --stage1-only --iterations 20 --warmup-iterations 2
```

This browser profile uses the production-sized synthetic fixture, ordinary
background polling, and two sessions. Cold startup means a fresh browser context
with an already-running API. Compare medians and p95 under the same machine load;
keep the source and harness fingerprints with the JSON results. Resource entries
cover the action page; observed API requests cover both pages. The shorter default
browser fixture does not establish production-sized performance.

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

Client maintenance is also audit-first and must target an explicit copied or
approved state file. These commands are read-only unless `--apply --backup`
are both supplied:

```powershell
.\.venv\Scripts\python.exe scripts\client_data_quality_maintenance.py --state-file .\path\to\state.json --format text
.\.venv\Scripts\python.exe scripts\client_duplicates_maintenance.py --state-file .\path\to\state.json --format text
```

The first reports placeholder/invalid vehicle VIN values; the second plans
exact duplicate-client merges and card relinks. Review the full plan and a
verified backup before any apply.

### Finance Audit-First

Finance audit is read-only first:

```powershell
.\.venv\Scripts\python.exe scripts\finance_audit_report.py --base-url https://crm.autostopcrm.ru --format text --issue-limit 50
.\.venv\Scripts\python.exe scripts\payroll_audit_report.py --base-url https://crm.autostopcrm.ru --format text --issue-limit 50
```

`/api/finance_audit/apply_safe_fixes` is maintenance-only. Historical finance
cleanup is destructive and must start with:

```powershell
.\.venv\Scripts\python.exe scripts\clear_financial_history.py --dry-run --state-file .\path\to\state.json
```

Apply only under a separate owner-reviewed plan with a verified backup, using
the script's explicit `--apply --backup` mode. Never edit cashbox or payroll
ledgers by hand.

### Repair-order posting migration

Before enabling corrections on an existing state file, stop writers and take
the normal full backup. The migration is dry-run by default and reports payroll
parity plus unchanged cash/inventory movement counts:

```powershell
.\.venv\Scripts\python.exe scripts\migrate_repair_order_cycles.py --state-file .\path\to\state.json
```

Apply only after reviewing an exact dry-run. Apply requires a separate backup
directory and creates a timestamped copy before writing:

```powershell
.\.venv\Scripts\python.exe scripts\migrate_repair_order_cycles.py --state-file .\path\to\state.json --apply --backup .\backups
```

Keep the backup and JSON reconciliation output with the release record. Do not
run this against production without a separate owner-approved migration window.

Repair-order number audit is also read-only:

```powershell
.\.venv\Scripts\python.exe scripts\repair_order_number_audit.py --base-url https://crm.autostopcrm.ru --format text --issue-limit 50
```

Numbers are immutable. `/api/correct_repair_order_number` is a blocking
compatibility endpoint that always returns
`repair_order_number_immutable`; no supported maintenance correction tool
exists.

## Production Authentication

Production MCP uses owner-approved OAuth 2.1 authorization code flow with PKCE
S256 and rotating refresh tokens. Keep `AUTOSTOP_MCP_OAUTH_ENABLED=1` and
`AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED=0`. `AUTOSTOP_MCP_OAUTH_STATE_KEY` must be
a stable protected Fernet key; encrypted client/token state is stored in the
mounted CRM data directory and survives container replacement. Never rotate
the state key as part of a routine deploy.

The normal deploy may still rotate `MINIMAL_KANBAN_MCP_BEARER_TOKEN` for
internal smoke and Responses API compatibility. Codex/ChatGPT OAuth sessions
must not depend on that bearer. Provision/check OAuth settings without printing
the key:

```bash
cd /opt/autostopcrm
python3 scripts/configure_mcp_oauth.py ensure --env-file .env
python3 scripts/configure_mcp_oauth.py check --env-file .env
```

All Gateway switches must be present in `.env` as `0` or `1`:

- `AUTOSTOP_AGENT_GATEWAY_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED`
- `AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED`

A Store-enabled release additionally provisions these server-local values:

- `AUTOSTOP_STORE_API_URL=http://autostop-app:8000`;
- `AUTOSTOP_STORE_READ_TOKEN` for pure reads;
- `AUTOSTOP_STORE_QUOTE_TOKEN` for exact full quote and sourcing reads;
- `AUTOSTOP_STORE_MANAGE_TOKEN` for the seven optimized named actions;
- `AUTOSTOP_STORE_OWNER_TOKEN` for owner-approved guarded parity with the
  existing Store employee API.

The four scoped tokens must be strong and pairwise distinct. Never display
their values. A Store-enabled release also requires the App runtime flags
`STORE_AGENT_QUOTE_FULL_READ_ENABLED`,
`STORE_AGENT_QUOTE_DRAFT_WRITE_ENABLED`, and
`STORE_AGENT_SUPPLIER_LOOKUP_ENABLED`; the mandatory `--require-store` smoke
proves all three flags plus the dedicated quote credential before release
completion.
Missing or invalid Store configuration is nonfatal to normal CRM container
startup and is reported as `store_degraded`; `deploy.sh` nevertheless requires
valid Store settings before publishing a Store-enabled release.

Validate before restart:

```bash
cd /opt/autostopcrm
set -a
. ./.env
set +a
export AUTOSTOP_MAINTENANCE_MARKER="/home/autostop/.minimal-kanban/.agent-gateway-maintenance"
export MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL="${AUTOSTOP_PUBLIC_SITE_URL:-https://crm.autostopcrm.ru}"
export MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL="${AUTOSTOP_PUBLIC_MCP_URL:-https://crm.autostopcrm.ru/mcp}"
python3 scripts/validate_production_env.py --require-production --require-store
```

`deploy.sh` snapshots and rotates only the internal CRM server bearer after
the image and rollback source are ready. It never edits Codex configuration.
For manual server repair, use the same helper and never pass a token on the
command line:

```bash
cd /opt/autostopcrm
python3 scripts/configure_codex_mcp_auth.py rotate --generate
python3 scripts/configure_codex_mcp_auth.py check
```

The helper updates only server `.env` without printing the token. Configure Codex separately with the URL only and
link once with `codex mcp login autostopcrm`; automatic refresh then survives
normal deploys. Never dump `.env`, container
`.Config.Env`, a full process environment, or credential-bearing config into a
tool transcript. Report only allowlisted non-secret settings and boolean
credential checks.

## Deploy

Deploy only with explicit owner intent. Normal sequence:

1. Verify and commit only intended changes.
2. Push the commit to `origin/autostopcrm-v1`.
3. Confirm `/opt/autostopcrm` is clean and fast-forward it to that commit.
4. Run the canonical isolated Manager release gates from
   `/opt/AutostopManager/docs/agent/deployment_runbook.md`; its temporary
   `AUTOSTOP_MANAGER_DB` is mandatory for preflight. Never run a bare
   `knowledge-sync` against the persistent Manager DB before release.
5. Run `deploy.sh`.
6. Compare workstation, remote, and server revisions.
7. Run live, UI-if-relevant, and performance smoke.

On the server, before `deploy.sh`:

```bash
cd /opt/autostopcrm
git status --short --branch
git fetch origin autostopcrm-v1
git merge --ff-only origin/autostopcrm-v1
git -C /opt/AutostopManager status --short --branch
git -C /opt/AutostopManager fetch origin AutostopManager
git -C /opt/AutostopManager merge --ff-only origin/AutostopManager
# Run the `run_manager_release_gates` subshell from the canonical Manager
# deployment runbook. It exports AUTOSTOP_MANAGER_DB to a disposable tmp DB.
docker network inspect --format '{{.Internal}}' autostop-store-agent
docker network inspect --format '{{range .Containers}}{{println .Name}}{{end}}' autostop-store-agent
```

Initial infrastructure provisioning creates the network once with
`docker network create --driver bridge --internal autostop-store-agent`; the
Store deployment attaches `autostop-app`. Do not attach the database. CRM
deploy refuses a missing/non-internal network, a missing App member, the
database, or any unexpected member.

Run the release from a workstation after the intended commit is checked out on
the server. The target is fixed in `deploy.sh` as
`CRM_DEPLOY_BRANCH=autostopcrm-v1`; do not pass a branch override:

```powershell
ssh -i $env:AUTOSTOPCRM_SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes root@crm.autostopcrm.ru "cd /opt/autostopcrm && ./deploy.sh"
```

Prerequisites in server `.env` include the six Gateway switches, the three
Store adapter settings, distinct non-empty
`AUTOSTOP_CRAWL4AI_API_TOKEN` and `AUTOSTOP_CRAWL4AI_SECRET_KEY`, and
`AUTOSTOP_SMOKE_OPERATOR_USERNAME` /
`AUTOSTOP_SMOKE_OPERATOR_PASSWORD`. `deploy.sh` and Compose validation fail
closed before maintenance when either Crawl4AI credential is absent or they
are the same. Public HTTPS/API/MCP auth smoke is mandatory; there is no skip
flag.

The bounded release flow:

1. verifies exact branch/fetched-remote parity and full cleanliness for both
   the CRM and Manager root checkouts before any snapshot, image build, or auth
   rotation, then checks free space, Compose configuration, production auth,
   scoped Store identity, and the isolated Store network;
2. creates the candidate Manager release strictly from the verified Manager
   commit via `git archive HEAD`, then reruns only `knowledge-sync` and
   `knowledge-audit` from that sealed candidate snapshot in a disposable
   `mktemp` DB; this narrow gate does not replace the canonical full Manager
   release gates. It then prebuilds an immutable CRM image before maintenance;
   an early EXIT guard owns only this attempt's exact Manager paths and Docker
   refs, so a pre-maintenance failure removes or restores them without touching
   the live/previous release;
3. provisions stable encrypted OAuth and rotates the internal compatibility
   bearer with a private rollback copy, without editing Codex configuration;
4. creates the maintenance marker and stops only `autostopcrm`;
5. creates and verifies an atomic backup of CRM state/audit data and Manager
   SQLite;
6. atomically activates the sealed candidate Manager snapshot, confirms the
   active identity under the release deadline, then runs `knowledge-sync` and
   `knowledge-audit` from it against the
   persistent Manager DB; this happens only after the verified backup and
   before CRM start, so any failure uses the existing rollback for both the DB
   and `current` symlink;
7. starts the prebuilt image, proves only CRM and App share the Store network,
   and runs internal authenticated CRM plus Store-read smoke; Store Gateway
   readiness and every candidate-side Docker probe remain inside the bounded
   release budget, so a short cold-start initialization is tolerated but an
   unavailable Store still fails the release and triggers rollback;
8. while maintenance protection remains active, runs bounded Store runtime,
   search, and read-only owner-contract inventory checks plus the feed probes
   with a revision-bound proof and unique release attempt id; the generic
   `store_owner_api` transport remains internal and is never discovered or
   called by the public smoke; mandatory public API and OAuth checks and the
   exhaustive maintenance-safe 24-tool Gateway smoke must verify the
   change-feed checkpoints and exact public surface;
9. installs the watchdog only through a separately authorized opt-in;
   otherwise leaves it disabled or absent, then tags the healthy release as
   stable and removes the maintenance marker as the final fallible release
   action;
10. after success is marked and the rollback trap is removed, best-effort
   retention prunes only validated old backup directories, Manager release
   snapshots, and exact CRM release/rollback image tags. Current and rollback
   references are always protected; retention failure cannot roll back or stop
   the healthy release.

Any failure or maintenance-budget overrun attempts a bounded rollback of
changed protected data, Manager release, auth configuration, and the previous
image. Rollback restores protected state only after the candidate container is
proven stopped; if stop fails, state/feed/Manager data remain untouched and the
maintenance marker stays active. The marker also remains if rollback cannot
prove a healthy recovery.

Commonly reviewed settings:

- `AUTOSTOP_MANAGER_DEPLOY_REMOTE` (the Manager branch remains fixed as
  `AutostopManager`)
- `AUTOSTOP_RELEASE_IMAGE` / `AUTOSTOP_STABLE_IMAGE`
- `AUTOSTOP_BUILD_RELEASE_IMAGE`
- `AUTOSTOP_MAINTENANCE_BUDGET_SECONDS`
- `AUTOSTOP_RELEASE_BACKUP_ROOT`
- `AUTOSTOP_RELEASE_BACKUP_RETENTION_COUNT`
- `AUTOSTOP_MANAGER_RELEASE_RETENTION_COUNT`
- `AUTOSTOP_RELEASE_IMAGE_RETENTION_COUNT` /
  `AUTOSTOP_ROLLBACK_IMAGE_RETENTION_COUNT`
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
docker compose exec -T autostopcrm python scripts/validate_production_env.py --require-production --require-store
docker compose exec -T autostopcrm python scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --expect-admin
docker compose exec -T autostopcrm python scripts/check_agent_gateway_v2.py --mcp-url https://crm.autostopcrm.ru/mcp --exhaustive --require-store --require-web
docker compose exec -T autostopcrm python scripts/check_mcp_oauth.py --mcp-url https://crm.autostopcrm.ru/mcp
docker compose exec -T autostopcrm python scripts/docs_audit.py --format text
```

For deploy-persistence proof, save the smoke refresh state in a private
mode-`0600` file with `--state-out`, redeploy, then pass the same path with
`--refresh-from`. Omit `--state-out` on the last run so the smoke token family
is revoked. Delete the private smoke file afterward.

The recurring `--require-store` probe uses `store_state` search and does not
advance the owner's durable `store_digest` cursor. After the first coordinated
Store release, separately perform one intentional
`agent_board_digest(scope="store", limit=1)` traversal. ACK every non-empty
page with its exact `cursor`/`ack_token` through the terminal page before
recording only compact health/count evidence; never leave a pending delivery
and never record raw orders or customer data. Verify `agent_bootstrap`
separately as a CRM-only call: it must report Store as `not_loaded`, return no
Store snapshot/cursor/ACK, issue no Store request, and leave the owner
`store_digest` checkpoint unchanged.

The post-release command above intentionally does not repeat the
maintenance-only CRM change-feed probes after the marker is removed. Those
probes occur only inside `deploy.sh` with
`--exhaustive --maintenance-safe --require-store`, a revision-bound proof, and
a unique attempt id. The public smoke never invokes the generic Store owner
transport and records no Store request body or Store data.

After UI changes, run
`.\.venv\Scripts\python.exe scripts\browser_smoke.py` and manually verify
operator login, board/card changes, clients, repair orders and PDF export,
inventory, cashboxes, payroll, files, archive, nested modal behavior, and
anonymous write rejection. For shared-display changes, also open
`ОТКРЫТЬ ДАШБОРД` from board scale settings and verify the named `/dashboard`
window at 1920x1080: the shared mechanics message board, protected message
images, exactly four Monday-based weekly bars, the green current-week `ИДЁТ`
state, no payroll or employee data, no scroll/overlap, a 401 from an anonymous
`/api/get_display_dashboard` request, and retention/recovery after a temporary
refresh error. The browser smoke saves
`output/playwright/tv-dashboard-1920x1080.png` for this check.

## Watchdog

The production baseline does not install `autostopcrm-watchdog.timer`.
`deploy.sh` keeps watchdog installation disabled by default; do not set
`AUTOSTOP_INSTALL_WATCHDOG=1` without separate owner authorization. Existing
watchdog units are legacy state and should be removed during maintenance.
When auditing that state, use:

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

The current canonical document list is maintained in [README](../README.md).

`requirements.txt`, `requirements-dev.txt`, and
`requirements-runtime.txt` are dependency manifests. Do not add one-off plans,
frozen audit reports, release copies, or secret-access notes as active project
documentation. Delete obsolete material instead of maintaining parallel
sources of truth.

When an active document is added, deleted, or renamed, update
`scripts/docs_audit.py`, `README.md`, and `.dockerignore` together, then run:

```powershell
.\.venv\Scripts\python.exe scripts\docs_audit.py --format text
```
