# Remaining maintenance work

This is a short evidence backlog, not an installation procedure. Use the
[operations runbook](OPERATIONS_RUNBOOK.md#deploy) for the current full-downtime
procedure. No live configuration or data was inspected or changed to close the
items below.

The removed reports remain available at the audited CRM base
`a027d686344d8e48a62d3171eb2712393a02f210`:

- [docs/reports/documentation-review-2026-10-10.md](https://github.com/UgaChavis/AutostopCRM-V1/blob/a027d686344d8e48a62d3171eb2712393a02f210/docs/reports/documentation-review-2026-10-10.md)
- [docs/reports/documentation-remediation-2026-10-11.md](https://github.com/UgaChavis/AutostopCRM-V1/blob/a027d686344d8e48a62d3171eb2712393a02f210/docs/reports/documentation-remediation-2026-10-11.md)

| Item | Evidence and status | Next bounded action |
| --- | --- | --- |
| Saved Manager diagram instructions | Both reports' final catalog/release sections describe source exports and offline previews; they explicitly do not establish saved live instruction acceptance. Status: not established by these reports, not a newly confirmed defect. | Compare a fresh authorized technical graph with the installed Manager/catalog using the existing offline instruction checker. Preserve IDs, geometry, relations and status records. Any later update needs current version, owner authorization, idempotency and readback; do not replay old previews. |
| Historical final CI/review receipts | The 2026-10-10 report leaves fourth-iteration exact-SHA CI/review external; the 2026-10-11 report leaves third-iteration CRM final CI/review external after a runbook-test failure. Status: unverified in the reports, not proof that current installed versions failed. | Reconcile existing acceptance receipts with the exact relevant commits if this history is needed. Reuse successful evidence; do not rerun old full suites merely to complete a historical report. |
| Retired guarded release implementation | Source removed: old forward/cold controllers, their exclusive CI/test contracts and runtime callers. Installation remains pending. Historical source remains at `11f44629de0ea8223eba08c5818c827f66c4269c`. | Use the offline maintenance procedure; ordinary backup, ownership, idempotency, auth and unknown-outcome checks remain required. No server rollout is implied by this source change. |

The reports' fixed old graph versions, Manager pins, 30 GiB requirement and
repeated full-review cycles are historical constraints, not current maintenance
requirements. Existing code-maintenance tasks remain under `tech_debt/`.
