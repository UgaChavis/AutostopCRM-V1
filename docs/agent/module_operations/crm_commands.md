# CRM commands, gates and side effects

Inventory checked against `scripts/`, `deploy.sh`, `.github/workflows/quality.yml`
and the [runbook](../../OPERATIONS_RUNBOOK.md) at CRM revision `ac10f534`
on 2026-09-25. Run commands from the exact CRM checkout. For Python entries,
the full path is `scripts/<name>`; inspect `python scripts/<name> --help` only
for an entry with an argument parser. PowerShell uses `pwsh -File
scripts/<name>.ps1` on Linux or the `./scripts/<name>.ps1` form on Windows.
Never print environment values. **R** = read-only/static, **S** = local
synthetic/build output, **T** = technical state/config, **W** = business write,
**X** = destructive/production effect. A read-only report can still contain
private business data; keep it out of Git and shared output.

## Daily checks and release gates

| Entrypoint and verified arguments | Use and success signal | Effect / boundary |
| --- | --- | --- |
| `scripts/run_checks.ps1 -Profile changed` | Changed-file Ruff, generated JS and code health | R/S; local only |
| `scripts/run_checks.ps1 -Profile ci` | Canonical full local CI profile, including unit/coverage, docs, browser core and performance | S; disposable fixtures, not deploy |
| `scripts/docs_audit.py --format text [--manager-root /opt/AutostopManager]` | Document links, Gateway names, optional registered Manager/CRM schema parity | R; synthetic schema process |
| `scripts/code_health_audit.py --format text [--include-untracked]` | File roles and size/complexity budgets | R |
| `scripts/coverage_audit.py --format text` | Runtime/release coverage floors | R; after coverage run |
| `scripts/crm_capability_parity.py --require-complete` | API/MCP capability manifest parity | R |
| `scripts/crm_change_feed_producer_parity.py --require-complete` | Durable change-feed producer parity | R |
| `scripts/check_web_assets_js.py` | Generated browser JavaScript parity | R |
| `scripts/audit_localization.py` | Localization inventory | R |
| `scripts/validate_production_env.py --require-production --require-store` | Required flags, URLs and scoped tokens present/valid; no values printed | R; prerequisite, not readiness |
| `scripts/check_agent_gateway_v2.py --exhaustive --require-store --require-web` | Active 24-tool surface plus bounded official live smoke | R/S/T; only after release preflight, as in [Gateway card](crm_gateway.md) |
| `scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --expect-admin` | Public/UI/API/OAuth technical and business smoke; run **inside** `docker compose exec -T autostopcrm` (host mapping is 8000) | **T/W risk:** default anonymous write-protection probe attempts a synthetic sticky if auth is broken; use only official release gate. `--skip-public-write-protection` is safer for inventory. |
| `scripts/check_mcp_oauth.py --mcp-url https://crm.autostopcrm.ru/mcp` | OAuth authorization/refresh and optional `--state-out`/`--refresh-from` persistence proof | T; protected auth state; release gate only |
| `scripts/check_agent_runtime.py --help` | Agent process heartbeat and endpoint options | R; exact runtime target required |
| `scripts/check_automation_center_release.py` subcommands: `capture-feed`, `verify-feed`, `capture-telegram-effects`, `verify-telegram-effects`, `hold`, `manager`, `crm`, `telegram` | Automation Center, feed and Telegram release-state contract | R/T; private snapshots, exact release inputs and runbook only |
| `scripts/probe_manager_crm_feed_auth.py --help` | Manager scheduler identity and CRM feed auth | R; requires `--manager-env` path, not its values |
| `scripts/container_healthcheck.py` | Container readiness for Compose healthcheck | R; not public access proof |
| `scripts/container_entrypoint.py` | Container process entrypoint; starts the configured API/MCP runtime | T; invoked by Docker, not an operator smoke |
| `scripts/browser_smoke.py --profile core --attempts 1` | Required disposable browser profile | S; local, no production records |
| `scripts/browser_smoke_review.py --help` | Review generated browser artifacts | R/S; artifacts may be private |
| `scripts/attest_agent_gateway_v2.py --run-id <release-id>` | Full Gateway attestation from release procedure | S/T; not an ordinary health query |
| `scripts/post_build_verification.py --help` | Portable Windows executable verification | S; after build |

CI runs `quality.yml`: static, unit/coverage, browser/performance and Docker
runtime assets; the final `quality` job requires all. `run_checks.ps1 -Profile
ci` does not replace hosted CI, Compose, GitHub policy or a production smoke.
`run_quality_pass.ps1` adds the Windows portable build verification where
needed. The code and runbook, not this summary, define exact thresholds.

## Provisioning, production and data maintenance

| Entrypoint and mode | Role and boundary |
| --- | --- |
| `deploy.sh` | X: official coordinated CRM/Manager release with backup, holds, activation, smoke and rollback. Its pre-maintenance phase seals the exact Manager candidate, runs that candidate's pinned public offline-parts catalog sync into a private cache, and rechecks disk space. Manager MCP activation defaults to `AUTOSTOP_MANAGER_MCP_ACTIVATE_ON_DEPLOY=1`; `=0` is only for an explicitly scoped CRM-only release. Run only with exact clean published refs and runbook gates. |
| `scripts/release_git_preflight.sh` | R/T: exact Git/ref and dirty-tree preflight; may update remote-tracking refs. |
| `scripts/agent_release_backup.py` subcommands: `create`, `verify`, `compare-current`, `restore-changed`, `restore-crm-changed`, `restore-manager-changed` | X for create/restore; durable release checkpoint and rollback component. `verify`/`compare-current` are R. Called by deploy, not an ad hoc backup substitute. |
| `scripts/agent_release_retention.py` subcommands: `prune`, `cleanup-attempt` | X: validated old release/backup or failed-attempt artifact pruning only after its specified checkpoint. |
| `scripts/coordinated_release_state.py` subcommands: `capture`, `verify`, `restore`, `stop-candidates` | T/X: scheduler/Telegram/Manager release-state checkpoint and recovery; deploy controls sequence. |
| `scripts/configure_mcp_oauth.py` subcommands: `ensure`, `check`; `--env-file .env` | T for `ensure`, R for `check`; protected OAuth config. |
| `scripts/configure_codex_mcp_auth.py` subcommands: `rotate`, `check`, `snapshot`, `restore` | T for rotate/snapshot/restore, R for check; local Codex MCP auth setup. Inspect connector setup before use. |
| `scripts/configure_manager_crm_mcp.py` subcommands: `snapshot`, `restore`, `sync`, `check` | T for snapshot/restore/sync, R for check; Manager CRM URL/token pairing. Deploy atomically coordinates it. |
| `scripts/install_codex_skill.py` | T: install versioned CRM skill into user runtime. |
| `scripts/install_production_watchdog.sh` and `scripts/production_watchdog.py` | X: optional watchdog installation/execution; current production baseline does not install its timer. Never use as first recovery step. |
| `scripts/cleanup_audit_probe_consumer.py [--apply]` | R preview / W apply: remove one technical feed probe consumer with backup/readback. |
| `scripts/client_data_quality_maintenance.py [--apply]`, `scripts/client_duplicates_maintenance.py [--apply --backup]` | Private CRM report / W apply. Historical data changes need separate scope and backup. |
| `scripts/clear_financial_history.py [--apply --backup]` | X: destructive finance maintenance; excluded from ordinary release. |
| `scripts/apply_payroll_policy_2026_07_13.py [--apply]`, `scripts/normalize_cashboxes_after_safe_fix.py`, `scripts/migrate_repair_order_cycles.py [--apply]` | W/X: one-off historical migration/correction; separate approved window and rollback evidence. |
| `scripts/compact_audit_events.py`, `scripts/operator_activity_maintenance.py [--apply --backup]` | Private audit/retention maintenance; inspect exact parser and backup gate before mutation. |

Do not infer that a no-argument invocation is dry-run. Read each parser and the
runbook before using any maintenance script against production. The normal
release permits only migrations explicitly versioned and checked by `deploy.sh`.

## Reports, benchmarks and local tooling

| Entrypoint | Purpose and effect |
| --- | --- |
| `scripts/doctor.ps1`, `scripts/toolchain_doctor.ps1 -Format json` | R: local toolchain and configuration checks; no live CRM record needed. |
| `scripts/setup_dev.ps1`, `scripts/bootstrap_tools.ps1` | T/S: install local dependencies and optional tools. |
| `scripts/run_dev.ps1`, `scripts/run_mcp_server.ps1` | T: start local disposable services; check ports and target data path. |
| `scripts/build_app.ps1`, `scripts/prepare_release.ps1`, `scripts/run_quality_pass.ps1` | S: build and verify portable Windows artifacts. |
| `scripts/finance_audit_report.py`, `scripts/payroll_audit_report.py`, `scripts/repair_order_number_audit.py`, `scripts/state_size_report.py` | R, private: report on exact state/API; do not publish rows or totals in general documentation. |
| `scripts/benchmark_unit_suite.py`, `scripts/perf_workflows.py`, `scripts/perf_browser_panels.py`, `scripts/perf_comparison.py`, `scripts/perf_probe.py`, `scripts/perf_mcp.py` | S/R: local synthetic benchmarks or explicitly scoped live read probes. Live MCP writes remain disabled without separate scope. |
| `scripts/run_isolated_write_smoke.sh` | S: disposable isolated write test; never point at production data. |

`scripts/browser_smoke_core.py`, `browser_smoke_completion_act.py`,
`browser_smoke_inventory.py`, `browser_smoke_profiles.py`,
`browser_smoke_runtime.py`, `browser_smoke_salary_balance.py`,
`browser_smoke_support.py` and `python_bootstrap.ps1` are helpers invoked by
the entrypoints above, not separate operator commands. JSON manifests and
Nginx `*.example` files are contract/config assets, not commands.

## Stop conditions

Mismatch between current checkout, exact GitHub ref and OCI revision blocks
release. A missing Manager catalog sync helper, failed download/hash/text
verification or post-import disk headroom blocks maintenance. A missing backup,
dirty checkout, failed required CI, missing OAuth/Store credential, stale schema,
unverified write, unhealthy dependency or
maintenance budget overrun blocks activation. Follow `deploy.sh` rollback and
independent readback; never use `docker compose up --build` or manual state
replacement as a shortcut.
