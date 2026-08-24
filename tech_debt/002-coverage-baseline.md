# 002. Добавить измеряемый coverage baseline и critical-path gates

Приоритет: P0
Этап: 1
Оценка: 2–3 дня
Риск реализации: низкий/средний
Статус: completed locally 2026-08-23; hosted Linux run starts after publish

## Реализация и доказательства (2026-08-23)

- Добавлены pinned `coverage==7.15.3`, `.coveragerc` с branch/relative/parallel
  data и машинный `coverage_audit.py`.
- `coverage_baseline.json` содержит 13 фактически измеренных global/critical
  floors. Главные пары baseline/floor: runtime 78.82/78.50%, API server
  86.89/86.50%, release backup/restore 78.40/78.00%.
- Runtime и release scripts измеряются отдельно; отсутствие critical file,
  non-branch report, floor ниже baseline более чем на 0.5 pp и фактическое
  падение coverage fail closed.
- CI covered unit run заменяет прежний plain unit run, а не запускает suite
  второй раз; text/JSON/XML/HTML публикуются artifact-ом.
- Audit текущих финальных measurement reports: 13/13 PASS; audit unit tests:
  8/8 `OK`. Baseline снят на Windows CPython 3.13; workflow использует
  relative paths и будет подтверждён hosted Ubuntu runner при публикации.
- Финальный интегрированный covered suite: 1 946 тестов за 454.949 s,
  34 platform skip, результат `OK`; runtime coverage 79.18%, release
  backup/restore 78.40%, coverage audit 13/13 `PASS`.

## Результат

Команда видит, какие runtime branches реально исполняются тестами. Coverage
используется как ratchet и инструмент TDD, а не как KPI ради процента.

## Доказательства

- На исходном commit полный suite содержал 1 908 успешных тестов, но
  `coverage.py` в venv и `requirements-dev.txt` отсутствовал.
- Крупнейшие risk modules — finance, payroll, repair-order, Gateway, API auth и
  storage — имеют много тестов, но нет машинного подтверждения branch gaps.
- 34 local skip в основном обусловлены Linux/Node/symlink prerequisites;
  baseline должен различать Windows local и Linux CI.

## Scope

1. Добавить pinned `coverage.py` только в dev dependencies.
2. Добавить `.coveragerc` или `pyproject`-секцию:
   branch coverage, relative paths, parallel-safe data, исключение build/data.
3. Снять и сохранить baseline по пакетам, не только глобальный процент.
4. Ввести два gates и зафиксировать точные critical modules:
   - global ratchet: не ниже измеренного baseline;
   - critical-path ratchet для repair-order, finance/payroll, API auth,
     `operator_auth`, `api/server`, `deployment_security`,
     `mcp/oauth_provider`, Gateway ledger/raw readback, JSON store/change-feed,
     finance/payroll/repair-order, attachments/printing и release
     backup/restore.
5. Сформировать human-readable missing-branches artifact в CI.
6. Не считать scripts production attestation тем же coverage scope, что
   runtime; для них сохранить отдельную измеряемую группу.
7. Не запускать полный suite второй раз в том же 30-minute CI job: coverage
   заменяет текущий unit invocation либо собирается параллельными job data
   files с `coverage combine`.

## Последовательность

1. Measurement-only commit без fail-under.
2. Проверить Linux CI result и расхождение с Windows.
3. Зафиксировать baseline floor округлением вниз максимум на 0.5 pp.
4. Добавить critical package floors.
5. Для каждого последующего refactor требовать, чтобы touched critical module
   не потерял branch coverage.

## Обязательные characterization-наборы

- closed repair order → preview → reopen → edit → reclose;
- cash/cashless payment and reversal invariants;
- payroll snapshot/posting/reconciliation;
- inventory material movement guard;
- API bearer/operator/admin/maintenance matrix;
- Gateway dry-run/apply/idempotency/compensating/readback;
- JsonStore conflict, corruption preservation and change-feed commit;
- attachment path/symlink/size/type validation;
- print draft save/reset/migration and VAT balancing.

## Подводные камни

- Не ставить 100% и не добавлять бессмысленные tests getter-ов.
- Не понижать floor при первом красном CI; сначала выяснить platform skips.
- Generated web strings не должны искусственно портить Python coverage.
- `# pragma: no cover` разрешать только для доказуемо platform-only веток с
  тестом на целевой платформе.
- Coverage subprocess/concurrency для PDF/agent runtime может требовать
  отдельной настройки; не ломать основной gate ради этого в первом commit.

## Acceptance criteria

- `python -m coverage run --branch -m unittest discover -s tests` проходит.
- CI публикует text/HTML/XML artifact без внешнего сервиса.
- Global и critical floors задокументированы фактическими числами.
- Уменьшение coverage ниже baseline валит тест audit.
- Добавлен короткий focused command для одного task.
- Полный suite, parity и health gates остаются зелёными.

## Проверки

`python -m coverage erase`
`python -m coverage run --branch -m unittest discover -s tests`
`python -m coverage report --show-missing`
`python scripts/crm_capability_parity.py --require-complete`
`python scripts/crm_change_feed_producer_parity.py --require-complete`

## Stop condition

Если единый coverage run превышает CI budget, сначала разделить data files по
job-группам и объединить `coverage combine`. Не исключать критические модули
ради времени.
