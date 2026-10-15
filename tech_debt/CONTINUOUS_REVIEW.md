# Continuous code review

Owner request, 2026-10-15: review small coherent portions deeply, improve them,
publish each accepted portion, and continue until an explicit stop. Starting
revision: `adf0ed49d3e4a97158604bc733bd7e392d3d1e57` on `autostopcrm-v1`.

Existing size/complexity ownership remains in
[code_health_audit.py](../scripts/code_health_audit.py). The earlier
[UI sweep](SWEEP.md) is historical evidence, not acceptance of unreviewed code.

Each portion records before/after evidence, focused checks, full local CI and
the exact published SHA's hosted quality result. Detailed synthetic diagnostics
and publication receipts live under ignored `output/continuous-review/IR-NNN/`.
A clean portion needs no artificial source change. Production deployment and
historical-data repair require their own owner request.

## Portions

| ID | Scope and evidence | State |
| --- | --- | --- |
| IR-001 | Manager structure fingerprints: service-lock/detached-read ownership; canonical JSON, unknown fields and status-only scope. Remove nested copies from hashing; retain detached projection behavior. Baseline: 29 service/Gateway tests. Six added contract tests pass before changes. Measurements and 1,000 differential cases in the local receipt. | Local CI passed; exact-SHA hosted acceptance pending |

IR-001 measured on Python 3.13.12 with the same harness, three series of 60
calls and no concurrent tests. Status-scope hash p95 on the public template:
2.4744/3.0583/2.7096 ms before, 1.2751/1.1544/1.2607 ms after. With 2,000
synthetic statuses: 13.0896/21.4292/23.0122 ms before,
5.5962/5.3928/5.2852 ms after. Peak traced allocations for that larger scope
fall from 2,412,587 to 1,978,771 bytes. Baseline timing variability is retained;
these are isolated hash costs, not an end-to-end application speed claim.
All 35 focused service/Gateway/digest tests pass; public JSON and storage formats
are unchanged. Full local CI passed: 2,969 reported core cases (81 environment
skips, including the POSIX-only class), all 38 isolated browser groups, 36 release
cases (four environment skips), 81.98% runtime coverage, core browser smoke and
all existing performance gates. The exact-SHA hosted result belongs in the
publication receipt after the commit is created.

## Coverage queue

| Area | Next bounded review boundary | State |
| --- | --- | --- |
| Manager structure | Status validation and error side effects, then one graph mutation | IR-001 in progress |
| Read models and domain services | One snapshot projection, then one card/client/repair operation | Pending |
| HTTP API and MCP | One route/tool family, schema, failures and readback | Pending |
| Browser and Windows UI | One action's request ownership, draft and recovery | Pending |
| Printing and files | One field/download/print or file lifetime | Pending |
| Inventory, finance and payroll | One operation/calculation with unchanged financial rules | Pending |
| JsonStore and change feed | One persistence or transaction boundary | Pending |
| Agent, tests and packaging | One runtime/tool/test/build responsibility | Pending |
