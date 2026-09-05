# 013. Payroll calculations

Payroll calculations, salary ledger, reconciliation and reports should share
formulas and make state I/O distinguishable from calculation. A previous file
extraction did not itself reduce total complexity; judge changes by actual
responsibility, duplication and measured cost.

Preserve minor units/Decimal, ROUND_HALF_UP, deterministic cent balancing and
legacy normalization. Presentation rounding must not alter ledger values.
Keep revision checks and posting/reversal behavior.

Validate hourly/piecework/material/shift/manual accruals, reopen/reclose,
missing snapshots, negative/zero/boundary amounts, timezone/month boundaries,
order independence and line-total/journal balance. Real financial data repair
is outside code cleanup; investigate differences before replacing expectations.
