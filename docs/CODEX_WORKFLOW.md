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

`scripts\browser_smoke.py` is a local temp-runtime check. It must not be pointed
at production data or production credentials.

## Production Rules

- GitHub `origin/autostopcrm-v1` - источник истины.
- Production sync: fetch/reset to `origin/autostopcrm-v1`, затем `deploy.sh`.
- Финансовые live-fixes только после read-only `finance_audit_report.py`,
  owner review, dry-run и read-back verification.
- Не коммитить credentials, runtime state, raw CRM exports, cashbox ledgers или
  персональные базы клиентов.
