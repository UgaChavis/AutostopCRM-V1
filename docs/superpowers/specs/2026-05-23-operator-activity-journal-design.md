# Operator Activity Journal Design

## Status

Approved for implementation planning.

This design modernizes the operator profile and admin panel around a central,
human-readable activity journal. The approved UI direction is a dense table on
all devices, including mobile, with horizontal scrolling rather than card
conversion.

## Goals

- Show all meaningful actions by every CRM user in a readable operator journal.
- Keep the admin view dense and scannable: filters first, then a table.
- Avoid unbounded growth in `state.json` and `users.json`.
- Preserve enough detail for short-term operational review and dispute
  investigation.
- Preserve long-term statistics without storing every old detail forever.

## Non-Goals

- This is not a full RBAC redesign.
- This does not merge CRM operator accounts with employee/payroll records.
- This does not expose raw tokens, password hashes, or secrets in reports.
- This does not replace the existing audit log; it adds an operator-facing
  activity view over normalized events.

## Approved UI

The main admin activity view is a dense table with a filter toolbar.

Base columns:

- `Время`
- `Пользователь`
- `Модуль`
- `Действие`
- `Объект`
- `Суть изменения`
- `Сумма`
- `Источник`

The first column stays pinned. On mobile, the table keeps its structure and uses
horizontal scroll. Technical identifiers such as `card_id`, raw `object_id`,
request ids, and raw details are hidden by default and shown only in an expanded
row/detail view.

Toolbar filters:

- period;
- user;
- module;
- action type;
- free text search across client, vehicle, repair order number, file name,
  object labels, and known ids;
- export.

## Storage Model

Do not store the expanded journal in `users.json` or directly in main
`state.json`.

Create a separate operator activity storage area:

```text
%APPDATA%\Minimal Kanban\operator-activity\
  current\
    YYYY-MM.jsonl
  details\
    YYYY-MM.jsonl
  aggregates\
    YYYY-MM.json
```

Docker/server deployments use the same data root as the existing CRM runtime,
under `/root/.minimal-kanban/operator-activity/`.

### Compact Activity Row

Each row in `current/YYYY-MM.jsonl` is compact and table-oriented:

```json
{
  "schema_version": 1,
  "id": "activity_uuid",
  "timestamp": "2026-05-23T10:58:00+07:00",
  "username": "ADMIN",
  "module": "repair_order",
  "action": "repair_order_updated",
  "action_label": "Обновил работы",
  "object_type": "repair_order",
  "object_id": "card_or_order_id",
  "object_label": "ЗН 000124 • Toyota Camry",
  "summary": "Добавлена работа: замена масла; исполнитель: Иван",
  "amount": "4800",
  "source": "ui",
  "severity": "normal",
  "details_ref": "2026-05.jsonl#activity_uuid"
}
```

The table reads compact rows first. It loads `details_ref` only when the user
opens row details or exports a report that explicitly requests full details.

### Details Archive

`details/YYYY-MM.jsonl` stores heavier context only when useful:

- raw object ids;
- before/after previews;
- changed fields;
- request source metadata;
- links to existing CRM audit event ids;
- finance/cashbox metadata when applicable.

Large before/after values should use the same principle as existing
`audit-archive`: compact previews in the active row, heavy payload in an
append-only details archive.

### Aggregates

`aggregates/YYYY-MM.json` stores durable counters:

- by user;
- by day;
- by module;
- by action;
- by source;
- optional money totals for finance/payroll categories.

Aggregates are the long-term analytics source after detailed rows age out.

## Retention Policy

Use the approved R3 policy:

- detailed operator activity rows are guaranteed for the most recent 90 days;
- detailed rows older than 90 days are eligible for maintenance compaction or
  deletion after aggregates have been written;
- aggregates are retained for 24 months by default;
- maintenance scripts must support `--dry-run` first and `--apply --backup`
  for destructive cleanup;
- the UI must make the selected period and detail availability clear.

This gives broad short-term visibility without allowing the activity journal to
grow indefinitely.

## Captured Actions

Use a broad approach. Capture both important views and all mutations.

Initial required categories:

- auth: login, logout, expired/invalid session cleanup where useful;
- cards: open, create, update, move, archive, restore, mark ready, mark seen;
- card content: board summary changes, deadline changes, indicator/status
  changes, tags;
- clients and vehicles: create, update, link, unlink, delete;
- repair orders: create/update, status changes, works/materials replacement,
  payment-related updates, PDF export/print;
- cashboxes: transaction, transfer, salary payout/advance, cancel last
  transaction, cashbox create/delete/reorder;
- employees/payroll: employee create/update/toggle/delete, payroll report
  access where applicable;
- files: attachment add/remove/read/download, shared file upload/download,
  rename/copy/paste/delete/move;
- admin: user create/update/delete, report export;
- automation/agent/MCP/Telegram AI: actions that mutate CRM data or produce
  operator-visible effects.

Read-only views should be captured only when they are operationally meaningful:
opening a card, opening a repair order, downloading/reading a file, opening an
admin report. Routine polling and board refreshes must not be logged as user
activity.

## Ingestion And Normalization

Add an `OperatorActivityService` with a small public API:

- `record_activity(payload)` for explicit activity writes;
- `list_activity(payload)` for paged/filterable table reads;
- `get_activity_details(payload)` for expanded row details;
- `get_activity_aggregates(payload)` for dashboard/stat summaries;
- `compact_activity(payload)` or a maintenance script for retention.

`ApiServer` should continue resolving operator sessions and injecting
`actor_name`. Mutating UI routes should record activity through the service
after the domain action succeeds.

Existing `AuditEvent` records remain the source for many board/domain changes.
The new service may normalize from those events where reliable, but it should
also record explicit operator-only actions such as `open_card`, login/logout,
downloads, and admin report access.

## API Surface

Initial routes:

- `GET|POST /api/list_operator_activity`
- `GET|POST /api/get_operator_activity_details`
- `GET|POST /api/get_operator_activity_aggregates`
- `GET|POST /api/export_operator_activity`

Access:

- an operator may see their own activity;
- an admin may see all users and all activity;
- admin-only maintenance/cleanup stays separate and must not be exposed as a
  casual UI button.

## UI Changes

The existing profile/admin panel should evolve as follows:

- profile keeps a compact personal summary and a link to the journal filtered
  by current user;
- admin panel gets the approved dense journal table as its main surface;
- the old minimal user list remains available as a side panel or secondary tab;
- current four KPI cards become summary chips above or beside the table, not the
  main experience;
- row expansion shows technical ids and archived details.

## Error Handling

- If activity write fails after a successful CRM mutation, the CRM action must
  not be rolled back. Log the activity failure and return the original result.
- Activity storage writes must be file-locked and append-safe.
- Corrupt activity archive files should be skipped with warnings, not crash the
  CRM UI.
- Missing details archives should show a clear message in row details:
  `Детали события недоступны, компактная строка сохранена.`

## Performance

- Activity list endpoints must be paged.
- Default table page size should be 100 rows.
- Filter queries should avoid loading unbounded historical files.
- The UI should query only the selected period.
- Aggregates should power summary counts instead of scanning all details.

## Testing

Add focused coverage for:

- activity row serialization and normalization;
- recording login/logout/open-card and representative mutations;
- admin vs operator visibility rules;
- filters by user, module, action, period, text, and source;
- pagination;
- retention dry-run and apply behavior;
- aggregate generation;
- UI asset checks for the dense journal table, filters, export button, mobile
  horizontal scroll, and hidden technical details;
- docs audit if API docs/runbook are updated.

## Migration

Existing `users.json` action history remains readable during migration, but new
activity writes should go to `operator-activity`.

The current `cards_opened` profile count can be calculated from the new
activity journal. During rollout, keep backward compatibility by falling back to
old `action_history` when no activity rows exist.

Existing audit events in `state.json` do not need bulk backfill for the first
release. Backfill can be a later maintenance script if historical user activity
is needed.
