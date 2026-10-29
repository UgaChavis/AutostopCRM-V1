# AutoStop CRM Operations Runbook

This is the operational source of truth for workstation checks, production
parity, deployment, rollback, live verification, performance gates, and
maintenance safety.

The owner's current update policy is **full application downtime**. The
[current maintenance procedure](#deploy) replaces the retired guarded J1,
warm-transition and CRM-only deployment controllers.
Unresolved historical evidence is in the [maintenance backlog](maintenance-backlog.md).

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

`docker-compose.yml` defines two CRM-project services:

| Service/container | Purpose | Host binding |
| --- | --- | --- |
| `autostopcrm` | UI, API, MCP, agent runtime | `127.0.0.1:8000 -> 41731`, `127.0.0.1:8001 -> 41831` |
| `autostop-searxng` | local search provider | `127.0.0.1:8890 -> 8080` |

The CRM service depends on healthy SearXNG. Static public-page extraction
uses pinned HTTP directly; browser extraction stays fail-closed until its
egress isolation is verified. Legacy Crawl4AI is not a runtime dependency.
Replacing CRM changes only `autostopcrm`; the maintenance window also stops
Manager and Store as described below. SearXNG and unrelated services do not
need replacement merely because CRM is updated.

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
git fetch --no-tags --refmap= origin refs/heads/autostopcrm-v1:refs/remotes/origin/crm-production-baseline
git rev-parse HEAD
git rev-parse refs/remotes/origin/crm-production-baseline
git ls-remote origin refs/heads/autostopcrm-v1
```

Preserve every pre-existing user change. Before any production operation,
compare the server:

On case-insensitive Windows filesystems, do not resolve production through an
ambiguous remote-tracking ref when a historical `AutostopCRM-V1` branch also
exists. The exact lowercase remote ref and distinct local alias above avoid
that collision; `--refmap=` disables opportunistic updates of colliding refs.

```powershell
if (-not $env:AUTOSTOPCRM_SSH_KEY) {
    $candidate = Join-Path $HOME ".ssh\autostopcrm_server_ed25519"
    if (Test-Path -LiteralPath $candidate) { $env:AUTOSTOPCRM_SSH_KEY = $candidate }
}
ssh -i $env:AUTOSTOPCRM_SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes root@crm.autostopcrm.ru "cd /opt/autostopcrm && git status --short --branch && git rev-parse HEAD && git rev-parse origin/autostopcrm-v1 && docker compose ps"
```

Use exact checked source revisions and qualified artifacts for a new update.
Keep existing local code and user work unchanged unless updating that checkout
is explicitly in scope. A clean candidate may be prepared separately; compare
the artifact with its exact CI evidence and the installed version independently.

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

SQLite comes from the Python/OS runtime, not the pip dependency pins. Before
deployment or redistribution of a desktop build, verify that its SQLite includes
the [upstream WAL-reset fix](https://sqlite.org/wal.html): version 3.51.3
or newer, or a verified vendor backport (including 3.44.6 and 3.50.7). Check the
actual linked library on Windows and in the container, not only the Python
version. Ordinary regression tests do not establish protection against this rare
concurrent-checkpoint fault. Keep runtime upgrades and before/after measurements
as a controlled environment change; do not silently replace a machine-wide DLL.

On Linux, each live change-feed store retains one SQLite connection and leases
it exclusively for consumer page reads and replays. This avoids repeated
directory synchronization from opening a new durable WAL handle for each page,
as well as last-connection checkpoints and WAL-index recovery. The connection
holds no transaction or read cursor outside its lease. Consumer pages retain
`BEGIN IMMEDIATE` and durable `synchronous=FULL` commits; producers, ACKs,
summaries and other operations retain separate connections and their existing
FULL/NORMAL durability modes. SQLite's normal automatic checkpoints remain.
The process-wide lifecycle lock serializes page leases across feed stores;
the server uses one feed database. Already open keepers permit separate fresh
WAL readers to start during a page lease. No additional persistent pool exists.

`JsonStore.close()` releases the keeper, and later use in the same process with
the same database file can reopen it. Closing a same-thread active page lease or
nesting another active lease is rejected without rolling back the outer
transaction. Failed page transactions roll back and discard the connection;
later requests reopen it. The owning embedded MCP runtime releases the keeper
after stopping its agent and API; other platforms keep the original per-request
connection lifecycle. A store inherited through `fork`, or whose database inode
was replaced, requires a fresh store instance. Fork only while feed activity is
quiescent: Linux keepers close before Python fork and reopen lazily in the
parent. This uses standard Python 3.11 APIs rather than SQLite configuration
flags added in Python 3.12.

Committed state may remain in the WAL while the keeper is alive. Follow the
[SQLite WAL backup rules](https://sqlite.org/wal.html): copying only the main
database can omit committed changes. The independent backup helper uses
`sqlite3.Connection.backup()` for an online snapshot and restores with CRM
stopped, removing stale WAL/SHM files before replacement. The current procedure
still requires one consistent backup and one isolated local restore check;
do not replace a live store's database file in place or overlay newer data.

Desktop integration settings live in the compatibility data directory's
`settings.json`. Initial creation and load-time normalization hold the same
process lock as saves. An I/O read failure propagates without replacing settings;
check file access and retry. Invalid JSON or a payload outside the supported
format/size is still backed up to a unique `settings.corrupted*.json` before
defaults are written. Keep these backups private: they can contain credentials.
Explicit API/MCP token changes through `SettingsService.update_section` update
both compatibility fields, including an explicit empty value.
Section updates without an explicit draft merge atomically with current settings;
tunnel completion uses that path. Connection diagnostics update only diagnostics.
If configuration changed while a check ran, its result becomes a warning asking
for a new check instead of reporting success for the old endpoint. Explicit full
draft saves and reset-to-defaults retain their replacement semantics.

## Manager instruction roots

Select one Manager instruction root before reading its modules or skills:

- For a work-Telegram job, use the physical root and revision pinned to that
  exact job. Do not replace them with the latest checkout or the current link.
- For other live work, resolve `/opt/autostop-manager-releases/current` to its
  physical snapshot, read its `REVISION`, and use that snapshot's instructions.
  Verify the installed manifest and active process independently when the task
  requires runtime evidence; a source Git HEAD is not an installed revision.
- For development, select the Manager checkout or worktree explicitly, record
  its Git HEAD and current diff, and use its instructions for source changes.
  Keep the CRM checkout and its HEAD/diff distinct from the Manager source.

Read the selected root's `AGENTS.md`, then its relevant
`docs/agent/modules/M1.md`, `M2.md`, `C2.md`, or other module. Resolve their
instruction references within the same root. In mixed source/live work, record
both roots and revisions. Preserve the caller's cwd and unrelated work; do not
copy runtime or switch checkouts to select an instruction version. GitHub links
are reference navigation, not authority for a live version; use an immutable
commit when sharing a particular source revision.

## Release Checklist

While editing documentation, run:

```powershell
.\.venv\Scripts\python.exe scripts\docs_audit.py --format text
.\.venv\Scripts\python.exe scripts\audit_localization.py
```

Changes to the audit implementation also require its focused tests and Python
checks. Documentation-only cleanup uses those targeted checks while iterating;
it does not require repeating full application suites. Preserve required CI
for a later merged release candidate as described below.

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
Apply a versioned skill installation only when that installation is in the
approved scope. A source-only PR leaves the installed skill unchanged until
the separately selected update.

The embedded agent has a separate effective prompt: a nonempty
`agent/system_prompt.md` under its app-data directory replaces the code default;
persisted context, tool descriptions, and current task hints are appended.
Inspect that precedence on the active Windows/server installation before
diagnosing restrictive behavior. Preserve custom facts, settings, and memory.

Use the fast changed-file profile while iterating:

```powershell
.\scripts\run_checks.ps1
```

For a local diagnosis that needs the entire suite, the canonical local CI
profile remains available:

```powershell
.\scripts\run_checks.ps1 -Profile ci
```

It runs full Ruff, docs, the two serial branch-coverage measurements and
`coverage_audit.py`, code health, localization, generated JavaScript,
capability and change-feed parity, mandatory browser `--profile core`, compile,
and bounded local performance gates. It uses an isolated Python cache so
coverage and compile cannot race on Windows. It never runs live probes, edits
`.env`, or accesses production.

Ordinary preparation uses affected tests and the changed-file profile. Do not
repeat the full local profile merely because the same candidate will be merged.
The required final attestation is one manual `quality.yml` run on the exact
final `autostopcrm-v1` commit, with `export_tested_image=true`. It includes the
Ubuntu/Python 3.12 harness, production Compose configuration and
`docker-runtime-assets` container contract.

Pull requests run fast source contracts and changed non-browser regression
modules. Browser changes receive focused local verification and the final
browser suite. Neither a successful PR `quality` check nor an older branch
SHA qualifies a final merged commit for deployment. Pushes do not launch a
second full suite. After merge, dispatch once, compare the run's `headSha`
with the final remote SHA, then use only its matching image artifact. A source
change or concrete failed gate justifies a new final run.

Hosted static, unit/coverage, browser/performance and Docker gates run in
parallel in the manual final run. Its `quality` job requires every full gate to succeed, including
when a dependency was cancelled or skipped. Pip, Chromium and Docker layers
are cached; the Docker candidate is loaded only for tests, never pushed.
All discovered browser regressions run once under runtime branch coverage,
with the existing per-class process isolation and timeout. The runtime runner
requires current-run receipts matching every planned and executed browser ID,
with no skipped cases, and exports `browser-coverage-*.json` with the coverage
evidence. The browser/performance job retains mandatory core smoke and performance
gates without repeating those covered regression modules.

The optional `release-rehearsal.yml` workflow runs the complete canonical CI
profile on separate disposable Ubuntu and Windows runners. Every job checks out the workflow's exact commit and
publishes source, dependency, native-library and failure evidence. It uses
Python 3.12.15 on Linux and 3.12.10 on Windows, the project requirements
and diagnostic constraints captured
from the successful unit job linked in `.github/release-rehearsal-constraints.txt`.
Platform-only dependencies remain visible in each complete pip-freeze receipt.
The first Windows bootstrap failed because the official
[`actions/python-versions` manifest](https://github.com/actions/python-versions/blob/main/versions-manifest.json)
has no Windows 3.12.15 payload. Windows therefore uses the latest available
3.12 Windows binary, 3.12.10; the measured runtime receipt records that choice.
The workflow has no deployment, production probes or production credentials.
It runs only on an explicit dispatch when a Windows/native change needs this
additional cross-platform evidence. It is not repeated for an ordinary
Linux application rollout and is not triggered by pushes.
The final gate requires matching completion evidence from both platform scenarios;
a skipped, absent or failed scenario cannot produce a ready result.
The manual main-branch quality run with `export_tested_image=true` exports the exact Docker image that passed
its runtime checks, its image identity, installed dependency versions and
SHA-256 checksums as a build artifact. This performs no registry push or
server update. Use that image archive in the release kit; a later rebuild
does not inherit the tested image's identity.

Its SQLite 3.51.3 is private to the runner: Linux builds the runtime image's
verified source; Windows preloads the verified official DLL through a private
Python startup directory without replacing the base interpreter's DLL. Before
the profile, `scripts/rehearsal_runtime.py` checks the actual mapped library,
version, source ID, temporary WAL/FTS5/integrity behavior, Qt PDF and Chromium.
Bootstrap failure terminates Python before the requested entrypoint runs.

The rehearsal's strict skip contract records all skipped test IDs and reasons.
Only the exact operating-system exclusions in `tests/runtime_skip_contract.py`
are accepted: Windows byte-range/job-handle cases on Linux, and the documented
Linux/POSIX release, flock, peer-credential, file-mode and browser-fixture cases
on Windows. Linux root-owned launch-journal exclusions apply only to the two
native launch test families on Windows. Linux exercises Docker packaging;
Windows records that separate packaging boundary. Missing Node, Chromium,
Playwright, supported SQLite, or an unknown skip reason fails the rehearsal.
Each platform profile has a 45-minute bound. The `rehearsal-ready` aggregate
requires both successful profiles and their exact-source completion evidence
from the current workflow attempt. Existing hosted `quality` gates remain
required. These profiles establish behavior on disposable runners; production
acceptance requires separate evidence.

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

Regression suites `test_save_isolation`, `test_bundle_draft`,
`test_storage_write_optimizations`, `test_serialization_snapshots`,
`test_repair_order_artifact_cache`, `test_change_feed_bulk_publish`, and
`test_mcp_tool_execution` cover detached-write rejection, ordering/restart,
archive and derived-file failures, and nonblocking MCP execution. A timeout
does not cancel an already running synchronous write or authorize a retry.
After an atomic state replace, deferred cleanup/readback failures are logged;
an unpublished durable change-feed stage remains available for reconciliation.
Attachment metadata repair stages the canonical file before committing its new
name, retains the legacy source until the committed target bytes are verified,
and never removes a staged target after a rejected write because another process
may already reference it. `test_column_delete_integrity` verifies that archived
card references block column deletion with `column_not_empty` instead of reaching
the storage integrity guard.
`test_salary_balance_summary` proves that the employee-list balance projection
matches the full ledger without building journal rows or a revision.
`test_printing_state_lock` owns concurrent read-modify-write safety for custom
templates, print settings and inspection-sheet drafts. Mutations sharing one
printing root hold the same bounded process lock through file replacement and
change-feed synchronization. The full projection during initialization,
explicit reconciliation and bounded pending completion-act replay use that
boundary too, so a stale
snapshot cannot overwrite a newer published entity. A lock acquisition timeout
is a retryable 503; timeouts raised by the mutation itself retain their original
meaning.

Deleting an active default print template clears the settings reference before
removing the template, defers change-feed publication until both files are
coherent, and compensates a failed second write. A hard stop between writes may
leave the template present but non-default; retrying the delete completes it.

Frontend ownership suites `test_card_workspace_context`,
`test_web_assets_async_ownership_context`, `test_web_assets_client_context`,
`test_web_assets_mobile_context`, `test_web_assets_repair_order_payment_context`,
`test_payroll_async_context`, and `test_operator_admin_async_context` cover late
responses, session changes, modal replacement, duplicate money/user writes, and
attempted parent closure while a card, file, payment, repair-order, payroll, or
operator-admin write is still pending. Full admin close and logout invalidate
pending editor intents and clear password and permission controls.
Operator-user summaries use lock-scoped, monotonic UTC `updated_at` revisions,
so the browser's stale-response guard follows commit order even after clock rollback.
`test_web_assets_cold_overlap`, `test_printing_cold_overlap`, and
`test_printing_async_context` own the cold-panel overlap, retry and prepared-read
boundaries.

A byte-identical save can retain the existing state file and its timestamp only
after SQL confirms the exact committed fingerprint with no pending outbox, and
a fresh file signature still matches the validated state and feed signatures.
Zero new feed events alone do not establish a no-op. Pending recovery, missing
files and external changes keep their normal verification/write paths.

Ordering-only shallow card copies may reuse normalized content and projection
digests only while all other fields retain the validated source references and
their serialized content matches the independent cached snapshot. Nested payloads
must not alias mutable models. Content changes, invalid positions, external reload and failed writes
must use normal validation/invalidation. The ordering and change-feed events are
still persisted; neighbouring cards do not acquire artificial user revisions.
Full-state serialization and feed reconciliation preserve native nested order
history within the existing 512-level state JSON limit; the shorter embedded
audit/settings sanitization policy is not applied to the entire state document.

Derived order text uses LF when published; existing equivalent CRLF files need
no rewrite. A bounded verification cache checks both document inputs and file
identity/size/timestamps before skipping regeneration. Missing or changed files
are verified again; this cache is not a tamper-detection mechanism. Client linking,
like other order changes, publishes these derivatives only after state commit.

Before the release-sized browser profile, run
`.\scripts\toolchain_doctor.ps1 -SkipServer`. `--profile full` fails before
creating temp runtime state when Chromium, Qt PDF, `pdfinfo`, or `pdftotext` is
missing. The mandatory `--profile core` does not require the PDF toolchain.

When the release needs the full UI/PDF scenarios beyond core, run once:

```powershell
.\.venv\Scripts\python.exe scripts\browser_smoke.py --profile full --attempts 1
```

Both profiles create a disposable local CRM with synthetic data. They provide
local regression evidence; verify the deployed interface separately under
[Production Verification](#production-verification).

Set `AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR` to a new owned directory outside
versioned files for each smoke run, then restore the caller's environment. The
full CI profile preserves that override and otherwise selects a fresh ignored
`output/browser-smoke-core-*` directory. Timer and dashboard screenshots and
completion-act artifacts use the selected directory. For standalone smoke runs
with no override, the timer screenshot remains disabled, the dashboard uses
`output/playwright`, and completion-act artifacts use the temporary runtime's
`playwright` directory.
`tests/test_browser_smoke_artifacts.py` checks the resolver and all three callers.

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
The executable verifier isolates its child process's roaming/local application
data and Windows profile. Connector notes are written to that disposable
profile's Desktop; launch overrides must not redirect them to the operator's
real profile. Full browser/PDF smoke remains a separate gate.

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

`scripts/perf_comparison.py --baseline <checkout> --output-dir output/perf-comparison`
runs these baseline/candidate series serially with alternating order and retains
every result. Add `--browser` for the representative two-session browser flows;
use `--scale 2` or `--scale 4` for larger synthetic state. It compares the median
series p95, flags missing scenarios, and applies a 10% regression limit (an
absolute 2 ms allowance for baseline operations below 10 ms).

Run `scripts/perf_browser_panels.py --source-root <checkout> --series 3 --iterations 20`
on each revision as well. It measures first printing, inventory, payroll and
cash-journal opens in fresh authenticated contexts, cold mobile startup and
input-to-current-result client search. Keep this first-open latency separate
from initial JavaScript savings; lazy loading must not hide a transferred delay.
The comparison runner's `--panels` flag alternates these cold-panel series too.
Printing setup waits for its preparatory card reads and delayed open/seen effects
to become idle before timing starts. Network records belong to the phase in which
the request started; response and finish phases remain separate so a setup request
that completes during an action cannot be mistaken for action work.

`scripts/benchmark_unit_suite.py --source-root <checkout> --output-dir output/unit-benchmark`
records full discovery/run counts, duration, exact skip reasons and failure IDs.
Run timing comparisons without concurrent builds, tests or browser measurements.
This timing helper does not replace the canonical branch-coverage CI gate.

After deploy, use `check_live_connector.py` below for public HTTPS and auth.
Measure the production backends from inside the running container. Both probes
read their bearer tokens from container environment variables, so no credential
appears in a process argument:

```bash
docker exec autostopcrm python scripts/perf_probe.py --base-url http://127.0.0.1:41731 --mcp-service-auth --warmup-iterations 2 --iterations 20 --max-snapshot-gzip-ms 800 --max-snapshot-gzip-bytes 80000 --max-revision-ms 500 --max-revision-server-ms 20 --max-get-card-ms 150
docker exec autostopcrm python scripts/perf_mcp.py --mcp-url http://127.0.0.1:41831/mcp --token-env MINIMAL_KANBAN_MCP_BEARER_TOKEN --iterations 5
```

The API probe uses `AUTOSTOP_AGENT_SERVICE_IDENTITY` and
`MINIMAL_KANBAN_MCP_BEARER_TOKEN` with the existing Gateway protocol; Gateway
and raw API must be enabled, while Gateway writes can stay disabled. This mode
accepts only a loopback API origin and the three measured read routes, bypasses
environment proxies and refuses redirects. If an API bearer is configured,
`--token-env` still supplies it separately. Without Gateway service auth, use
`--token-env` with a configured API bearer. Anonymous API reads remain denied.

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

Maintenance state writers preserve nested repair-order cycle snapshots and
JSON value types. Non-finite numbers and excessive nesting reject the write
before replacing state; they are not converted into nulls or text. Compaction
dry-run byte estimates use the same untruncated JSON representation as the save.

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

Missing-parent cash and inventory movements are retained on reads. A finance
audit reports `cash_transaction_missing_cashbox`; bundle writes reject missing
cashbox/item references before saving. Preserve the state and investigate the
missing parent under a separate recovery plan; do not delete the movement to
clear the error.

`/api/finance_audit/apply_safe_fixes` is maintenance-only. Historical finance
cleanup is destructive and must start with:

```powershell
.\.venv\Scripts\python.exe scripts\clear_financial_history.py --dry-run --state-file .\path\to\state.json
```

Apply only under a separate owner-reviewed plan with a verified backup, using
the script's explicit `--apply --backup` mode. Never edit cashbox or payroll
ledgers by hand.

Full historical cleanup also removes salary-balance reset adjustments together
with the accrual/payment history they offset. Review the dry-run field
`salary_balance_resets_removed`; retaining those adjustments alone would
create a false positive or negative employee balance.

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
- `AUTOSTOP_STORE_MANAGE_TOKEN` for the named management actions documented in
  [the MCP guide](../MCP_GUIDE.md#store-boundary-and-quote-context);
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
startup and is reported as `store_degraded`; acceptance of a Store-enabled
installation still requires valid Store settings and successful readback.

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

The offline update preserves existing working credentials. Rotate an internal
CRM server bearer only for a separately authorized credential repair; use the
existing helper and never pass a token on the command line:

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

### Manager loopback CRM MCP credential

Preserve the existing root-only credential file and principal identities in the
current offline procedure. Verify the URL/token pairing privately before start
and use readiness/readback after start; never print credentials. Manager uses the
separate root-only `/opt/AutostopManager/.crm-mcp.env`, with
`AUTOSTOP_CRM_MCP_URL=http://127.0.0.1:8001/mcp` and
`AUTOSTOP_CRM_MCP_BEARER_TOKEN` matching the configured CRM bearer. This file
belongs in the single configuration backup when the maintenance changes it.

The legacy CRM E8 Web Research Gateway contract serves Manager's E15 public
research module. Read `docs/agent/modules/E15.md` from the Manager root selected
by [Manager instruction roots](#manager-instruction-roots).
The Automation Center also uses this credential for feed readiness.

After an offline update, verify the scheduler's configured CRM and Manager
revision values against the installed artifacts before starting it. Preserve the
existing credentials. Compare the effective authenticated feed URL and credential
without logging either value, call the non-mutating `POST /api/change_feed/readiness`,
and reread ordinary scheduler status and dependencies. This readiness route does
not register a digest consumer, open a delivery or advance an ACK. Restore only
the explicitly selected application's prior operating state after acceptance.

Do not copy the bearer into Manager's general `.env`. Store operations through
Manager's F1 and F2 require completed native MCP activation and the relevant
permission. Read their `docs/agent/modules/F1.md` and `F2.md` in the selected
Manager root; E7 was the older Store module label.

## GitHub-Only Publication

### Source-only pull requests

When the approved endpoint is source publication for later review, prepare a
clean feature worktree from the current `autostopcrm-v1` branch. Preserve other
checkouts and their uncommitted changes. Run checks for the changed behavior
and documentation, push the feature branch without force, and open a PR against
`autostopcrm-v1`. Record the remote branch SHA after an independent
`git ls-remote --heads` readback, confirm the PR head through GitHub, and wait for
the required fast PR checks. If the base moved, integrate it and recheck the
affected behavior before publishing the updated candidate.

An instruction-only PR uses the targeted documentation checks above. It does
not install the global skill, update a running application, dispatch full
quality/image workflows, or authorize merge. Describe the remaining exact-SHA
release qualification in the PR; source checks do not establish runtime or
instruction-delivery acceptance.

### Release candidate after merge

When the approved endpoint includes a main-branch release candidate, start from
the reviewed candidate and fetch the exact lowercase ref:

```powershell
$candidateSha = (git rev-parse HEAD).Trim()
git fetch --no-tags --refmap= origin refs/heads/autostopcrm-v1:refs/remotes/origin/crm-production-baseline
$remoteSha = (git rev-parse refs/remotes/origin/crm-production-baseline).Trim()
git merge-base --is-ancestor $remoteSha $candidateSha
if ($LASTEXITCODE -ne 0) { throw "Candidate is not a fast-forward of autostopcrm-v1" }
git push origin HEAD:autostopcrm-v1
```

Never force-push the production branch. Confirm the final exact remote ref and
record its SHA after the merge or fast-forward. Dispatch
`gh workflow run quality.yml --ref autostopcrm-v1 -f export_tested_image=true`,
then find that manual run with
`gh run list --workflow quality.yml --branch autostopcrm-v1 --event workflow_dispatch --commit <final-sha>`.
Wait with `gh run watch --exit-status` and verify its completed successful
`headSha`, source tree and `autostopcrm-image-<final-sha>` artifact against the
selected final commit. If the branch moved before dispatch, reassess and test
the new final candidate. A run for a PR merge ref or an older commit is not
release evidence.

Neither publication path authorizes SSH, `deploy.sh`, a server fast-forward, or
live smoke. A successful GitHub push and CI run are not a production rollout.

## Deploy

### Current maintenance procedure

Deploy only with explicit owner intent for the maintenance window. The owner
accepts stopping Manager, CRM and Store together; preserving an intermediate
old/new application combination is unnecessary.

1. Record exact target Git SHAs, qualified package/image hashes and existing
   successful checks. Review the small delta and run only affected short tests
   during preparation. Keep required final/post-merge CI; do not repeat a full
   run without a changed candidate or a concrete failure. Preserve unique work.
2. Inventory the actual unit/container identities, data/config paths, active
   credentials and current versions. Calculate this window's peak disk, memory
   and time, including at least 2 GB free disk reserve. No fixed 30/34 GB budget
   from the old combined scenario applies.
3. Preserve SSH and the controlling session. Close task intake, stop application
   consumers and Manager, CRM and Store, and verify they stopped before copying
   data or replacing files. Record any application watchdog state, stop its timer
   and any already running recovery service, then verify both inactive before
   backup. Stopping only the timer does not stop an active recovery process.
   Keep unrelated timers, services and normal backups intact. Record uncertain unfinished
   operations for separate reconciliation.
4. Make **one consistent backup of necessary data and settings** for the
   changed components. Validate completeness and perform **one local isolated
   restore check**, then remove its disposable directory. Do not duplicate
   backups, images, caches or worktrees. An independent PC is outside this policy.
5. Install the exact checked artifacts and necessary configuration while the
   applications remain stopped. Keep working credentials, durable queues and
   idempotency history. Store stays at its qualified version unless an explicit
   Store update is needed; pushing Store `main` can trigger `Deploy VPS` and
   belongs only in the maintenance window. Do not invoke the historical J1,
   cold/warm transition or CRM-only release controllers.
6. Launch the applications using their reviewed existing service definitions.
   Independently check physical source/image revisions, processes, authorization,
   health and Manager → CRM → Store connectivity. Technical acceptance creates
   no customer messages, orders or payments. After acceptance, restore only a
   watchdog that was previously active, then reopen task intake.
7. After overall successful acceptance, remove this window's temporary files,
   test restore directory and release backup. Preserve ordinary scheduled
   backups, required runtimes, access files, unique work and a compact receipt.

If stop/readback fails, ownership is unclear, resources are insufficient or an
operation's result is unknown, stop the affected step and preserve its data.
Never automatically replay uncertain operations, return an old writer, or
restore an old database over newer working data. Repair requires a concrete
assessment of the current state.

The former automatic `deploy.sh` controller is retired. Its retained entrypoint
prints the procedure link and exits before reading configuration or changing services.
Do not restore old controller bindings or replay unfinished operations. Historical
source and evidence remain in Git and the separately preserved private receipts.

## Production Verification

### Automotive constructor bundle and manual status verification

The CRM image contains the self-contained automotive bundle in
`src/minimal_kanban/web_app_assets/source/automotive_tool_catalog.json`. Export it
from a clean, exact published Manager snapshot; prepare and check the CRM pin
while Manager CI runs. Run the canonical CRM hosted gates once on the final
merged CRM SHA. The exact final Manager and CRM gates may run in parallel;
activation requires both to succeed. The
catalog content hash excludes `source_revision` and `content_hash`; changing the
Manager SHA alone does not pretend that instruction content changed.

Before activation, compare the pinned artifact with the sealed Manager release:
`python scripts/check_automotive_tool_catalog.py --manager-root /path/to/sealed-manager`
regenerates the whole bundle from that sealed code with disposable state and
compares registry, native schemas and instruction text. Use `--manager-python`
for the approved dependency venv. Run this comparison against the selected
Manager `REVISION` during candidate preparation before image build or maintenance.
The checker requires its `REVISION` to match and compares every embedded Markdown
instruction with that snapshot. The Docker CI stage runs the validator without
network access. After activation, use the same checker inside the existing CRM
container with `--expected-source-revision` set to the installed published Manager
SHA, then independently read the protected catalog endpoint and graph metadata.
Source-only or old candidate checks do not establish installed parity.

In clean constructor view, open an E module with click or Enter/Space. Verify the
canonical instruction, all operation cards, owner-only status controls, Escape
and focus return, preserved viewport, and desktop/mobile scrolling. Manual status
saves do not call providers. Production status smoke must save the exact prior
record or its absence, mutate one authorized technical operation with a fresh
version and key, read it independently, and restore the original state (use
`clear_tool_status` when the original record was absent). An uncertain write is
reconciled by readback before replaying its original body and key. A 409 requires
a fresh read and a newly reviewed action; it must never overwrite a newer graph.

Portable graph exports omit `tool_statuses`; applying one retains the destination
map. Full release backup/restore preserves the complete JSON bytes, including
commissioning states and receipts. Catalog updates keep stable operation IDs;
retired IDs remain in the durable map until an explicit reviewed migration.
Manual colors, technical implementation/access and result evidence are separate.

Authenticated live probes use the existing authorized technical operator from
`AUTOSTOP_SMOKE_OPERATOR_USERNAME` and `AUTOSTOP_SMOKE_OPERATOR_PASSWORD`.
Pass credentials through the process environment, never inline or in logs.
The probes do not authorize customer messages, orders or payments.

From the server:

```bash
cd /opt/autostopcrm
git status --short --branch
git rev-parse HEAD
git rev-parse origin/autostopcrm-v1
docker compose ps
docker compose exec -T autostopcrm python scripts/validate_production_env.py --require-production --require-store
docker compose exec -T autostopcrm python scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --expect-admin
# This public smoke deliberately omits --maintenance-safe: it never runs the
# technical change-feed bootstrap, replay, or ACK probes.
docker compose exec -T autostopcrm python scripts/check_agent_gateway_v2.py --mcp-url https://crm.autostopcrm.ru/mcp --exhaustive --require-store --require-web
docker compose exec -T autostopcrm python scripts/check_mcp_oauth.py --mcp-url https://crm.autostopcrm.ru/mcp
docker compose exec -T autostopcrm python scripts/docs_audit.py --format text
```

For deploy-persistence proof, save the smoke refresh state in a private
mode-`0600` file with `--state-out`, redeploy, then pass the same path with
`--refresh-from`. Omit `--state-out` on the last run so the smoke token family
is revoked. Delete the private smoke file afterward.

The recurring `--require-store` probe uses `store_state` search and does not
advance the owner's durable `store_digest` cursor. Initial Store commissioning
may separately authorize one intentional
`agent_board_digest(scope="store", limit=1)` traversal. ACK every non-empty
page with its exact `cursor`/`ack_token` through the terminal page before
recording only compact health/count evidence; never leave a pending delivery
and never record raw orders or customer data. Verify `agent_bootstrap`
separately as a CRM-only call: it must report Store as `not_loaded`, return no
Store snapshot/cursor/ACK, issue no Store request, and leave the owner
`store_digest` checkpoint unchanged.

The public command above omits `--maintenance-safe` and never invokes the
technical change-feed bootstrap, replay or ACK probes. Those probes remain a
separate maintenance-only option requiring an active marker, revision-bound
proof and unique attempt ID. The retired controller no longer invokes them.
Use isolated synthetic tests for their regression checks; never bypass the
marker or replay an old attempt to complete routine acceptance. The public
smoke records no Store request body or Store data.

After deploying UI changes, manually verify the affected flows on the live
site: operator login, board/card changes, clients, repair orders and PDF export,
inventory, cashboxes, payroll, files, archive, nested modal behavior, and
anonymous write rejection, as relevant to the release. Use owner-approved
targets for any write verification. The local synthetic browser profile in
[Release Checklist](#release-checklist) does not establish live UI health.
For shared-display changes, also open
`ОТКРЫТЬ ДАШБОРД` from board scale settings and verify the named `/dashboard`
window at 1920x1080: the shared mechanics message board, protected message
images, exactly four Monday-based weekly bars, the green current-week `ИДЁТ`
state, no payroll or employee data, no scroll/overlap, a 401 from an anonymous
`/api/get_display_dashboard` request, and retention/recovery after a temporary
refresh error. The local full browser profile captures the same layout with
synthetic data as `tv-dashboard-1920x1080.png` in
`AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR`, or `output/playwright` when unset.

## Watchdog

The production baseline does not install `autostopcrm-watchdog.timer`.
The offline procedure does not add watchdog units. Installing or removing one
requires a separate check of its actual callers and an authorized maintenance
change. The optional `scripts/production_watchdog.py` still reads
`AUTOSTOP_DEPLOY_LOCK_PATH` (default: `.autostop-deploy.lock` under its configured
root). It skips a cycle only while another process holds the POSIX file lock;
the file's presence alone does not suppress recovery. During authorized downtime,
stop the actual application watchdog timer and any running recovery service,
verify both inactive before backup, and restore only the previously active
watchdog after acceptance. Preserve SSH and unrelated timers. The retired
deployment controller no longer manages this lock.

Read-only inspection, when relevant:

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
- Use checked published versions and the current full-downtime procedure.

## Documentation Policy

The current canonical document list is maintained in [README](../README.md).

`requirements-common.txt`, `requirements.txt`, `requirements-dev.txt`, and
`requirements-runtime.txt` are dependency manifests. Do not add one-off plans,
frozen audit reports, release copies, or secret-access notes as active project
documentation. Delete obsolete material instead of maintaining parallel
sources of truth.

When an active document is added, deleted, or renamed, update
`scripts/docs_audit.py`, `README.md`, and `.dockerignore` together, then run:

```powershell
.\.venv\Scripts\python.exe scripts\docs_audit.py --format text
```
