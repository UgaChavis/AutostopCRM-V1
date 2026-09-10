# 013. Payroll calculations

Payroll calculations, salary ledger, reconciliation and reports should share
formulas and make state I/O distinguishable from calculation. A previous file
extraction did not itself reduce total complexity; judge changes by actual
responsibility, duplication and measured cost.

Text presentation is isolated in `payroll_report_text.py`; monetary column
projection is shared. Remaining work is calculation/ledger separation, not
moving the same report text again.

`list_employees` uses a balance-only projection of the shared ledger calculation.
It preserves the same Decimal totals and legacy sources while skipping journal-row
construction, sorting and revision hashing. The full employee ledger retains its
rows and revision contract. `test_salary_balance_summary` owns equality across
current and legacy accruals, payouts, advances and balance resets.

Preserve minor units/Decimal, ROUND_HALF_UP, deterministic cent balancing and
legacy normalization. Presentation rounding must not alter ledger values.
Keep revision checks and posting/reversal behavior.

Validate hourly/piecework/material/shift/manual accruals, reopen/reclose,
missing snapshots, negative/zero/boundary amounts, timezone/month boundaries,
order independence and line-total/journal balance. Real financial data repair
is outside code cleanup; investigate differences before replacing expectations.
