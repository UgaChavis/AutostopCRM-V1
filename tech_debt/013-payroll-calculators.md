# 013. Вынести payroll calculators и reconciliation

Приоритет: P1
Этап: 1
Оценка: 8–12 дней
Риск реализации: высокий
Статус: ready после 001 и 012; coverage 002 параллельно

## Результат

Payroll-расчёты, reconciliation и report aggregation отделены от state I/O.
Mutation services сначала строят детерминированный plan, затем применяют его.

## Доказательства

`CardServicePayrollMixin`:

- 4 608 строк / 86 methods;
- ledger builder complexity 45, 376 строк;
- reconciliation complexity 37, 362 строки;
- report builder complexity 33, 450 строк.

## Scope

1. Pure calculators принимают immutable snapshots и возвращают typed result:
   lines, totals, issues, proposed postings/reversals.
2. Отдельный applier проверяет expected revisions и пишет bundle один раз.
3. Общие money/rounding/sign helpers не дублируются.
4. Payroll reports используют calculator result, а не повторяют формулу.
5. Finance audit/safe fixes остаются за отдельной задачей 019.

## TDD-план

Golden fixtures без персональных production данных:

- hourly/piecework/material/shift/manual accruals;
- reopen/reclose reversal;
- salary policy migration;
- negative/zero/boundary cents;
- timezone/month boundary;
- legacy missing snapshots;
- order independence/permutation invariants;
- sum(lines) == totals and journal balance invariants.

Добавить property-style loops стандартным unittest; внешний Hypothesis только
если несколько конкретных багов докажут его ценность.

## Подводные камни

- Decimal/minor units не смешивать с float.
- ROUND_HALF_UP и deterministic cent balancing сохранить.
- Report presentation rounding не менять ledger.
- Legacy records с отсутствующими fields должны нормализоваться до расчёта.
- Plan должен исключать actor PII и secret data.

## Acceptance criteria

- Core calculators не читают/пишут store и не используют current time без
  аргумента clock.
- Existing totals/fixtures byte-equivalent.
- Mutation applier делает один controlled commit.
- Complexity/length ключевых builders заметно снижены; exemptions ratcheted.
- Payroll audit scripts и full suite проходят.

## Проверки

`python -m unittest tests.test_payroll_audit_report tests.test_payroll_policy_2026_07_13 tests.test_payroll_snapshot_preservation tests.test_payroll_unaccrued_work_rows tests.test_repair_order_payroll_accruals tests.test_repair_order_reopen -v`
`python scripts/payroll_audit_report.py --help`
`python scripts/perf_workflows.py --synthetic-state-profile current-production --stage1-only --skip-browser --warmup-iterations 2 --iterations 20 --max-backend-write-ms 600 --max-storage-write-ms 550 --max-revision-server-ms 20 --max-get-card-direct-ms 20 --max-list-cashboxes-ms 50 --max-feed-read-ms 50 --max-feed-replay-ms 20`

## Stop condition

Если golden fixture расходится с текущим behavior, сначала определить:
регрессия это или известная ошибка. Нельзя менять expected totals в cleanup
commit без owner-reviewed finance defect.
