# AutoStop CRM Codex Workflow

Этот файл - короткий указатель для Codex. Канонический регламент остается в
`docs/OPERATIONS_RUNBOOK.md`.

## Перед Работой

1. Сверить локальную ветку с `origin/autostopcrm-v1`.
2. Проверить clean worktree.
3. Для release/deploy задач сверить production checkout `/opt/autostopcrm`.
4. Работать через отдельную `codex/...` ветку, если изменения не являются
   прямым emergency hotfix.

## Quality Gates

Используйте Release Checklist из `docs/OPERATIONS_RUNBOOK.md`:

- `scripts\doctor.ps1`
- `scripts\run_checks.ps1`
- `ruff format --check .`
- `ruff check .`
- full `unittest discover`
- `scripts\audit_localization.py`
- `scripts\check_web_assets_js.py`
- `scripts\browser_smoke.py` for browser UI/modal flow changes
- `scripts\perf_probe.py --local-temp-server` for local performance budget checks

`scripts\browser_smoke.py` is a local temp-runtime check. It must not be pointed
at production data or production credentials. It covers operator login privacy,
modal ladder, board/card roundtrip, cashbox journal, clients, employees, files,
archive and repair-order flows.

## Current UI Contracts

- Modal close behavior is stack-based: close returns to the parent context.
- Cashboxes are journal-first. Do not reintroduce visible `СВЕРКА` /
  `Финансовая сверка` entrypoints into the operator UI.
- Cash journal rows should stay compact, batch-rendered and readable on real
  data. Legacy transfer pairs without `transfer_group_id` are still expected to
  render as one logical transfer row.
- Production-data local QA uses dated sandbox folders outside the repo and must
  not leak raw client/vehicle/cashbox data into docs.

## Production Rules

- GitHub `origin/autostopcrm-v1` - источник истины.
- Production sync: fetch/reset to `origin/autostopcrm-v1`, затем `deploy.sh`.
- Финансовые live-fixes только после read-only `finance_audit_report.py`,
  owner review, dry-run и read-back verification.
- Не коммитить credentials, runtime state, raw CRM exports, cashbox ledgers или
  персональные базы клиентов.
