# Operator Activity Journal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved dense operator activity journal with broad activity capture and bounded R3 storage.

**Architecture:** Add a focused `OperatorActivityService` backed by append-only JSONL files under `operator-activity`, then expose paged/filterable API routes and replace the admin panel's center with a dense table. Existing `OperatorAuthService` remains responsible for login/session/user management, but delegates activity recording and reads to the new service when available.

**Tech Stack:** Python 3.12, stdlib JSON/Path/file locks, existing `ServiceError` API envelope, generated browser UI in `src/minimal_kanban/web_app_assets/assembler.py`, unittest.

---

## File Structure

- Create `src/minimal_kanban/operator_activity.py`: activity row normalization, append-only storage, list/detail/aggregate/export APIs, retention helpers.
- Modify `src/minimal_kanban/config.py`: add `OPERATOR_ACTIVITY_DIR_NAME` and `get_operator_activity_dir()`.
- Modify `src/minimal_kanban/app.py` and `src/minimal_kanban/mcp/main.py`: instantiate `OperatorActivityService` and pass it to `OperatorAuthService`/`ApiServer`.
- Modify `src/minimal_kanban/operator_auth.py`: record login/logout/open-card/admin user/report events; calculate profile stats from activity rows with legacy fallback.
- Modify `src/minimal_kanban/api/server.py`: add operator activity routes and authorization rules.
- Modify `src/minimal_kanban/web_app_assets/assembler.py`: add dense journal filters/table/export to admin modal.
- Modify `API_GUIDE.md` and `README.md`: document new routes/storage.
- Create or extend `tests/test_operator_activity.py`, `tests/test_api.py`, and `tests/test_web_assets.py`.

## Task 1: Activity Storage Core

**Files:**
- Create: `src/minimal_kanban/operator_activity.py`
- Modify: `src/minimal_kanban/config.py`
- Test: `tests/test_operator_activity.py`

- [ ] **Step 1: Write failing storage tests**

Add tests that create a temporary activity directory, record two events, list them newest-first, filter by username/module/search text, and load a detail record by `details_ref`.

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_operator_activity -v`
Expected: FAIL because `minimal_kanban.operator_activity` does not exist.

- [ ] **Step 2: Implement minimal storage**

Implement:

- constants `DEFAULT_DETAIL_RETENTION_DAYS = 90`, `DEFAULT_AGGREGATE_RETENTION_MONTHS = 24`;
- `OperatorActivityService(activity_dir: Path | None = None, logger: Logger | None = None)`;
- `record_activity(payload) -> dict`;
- `list_activity(payload) -> dict`;
- `get_activity_details(payload) -> dict`;
- `export_activity(payload) -> dict`;
- `get_activity_aggregates(payload) -> dict`.

Use monthly JSONL files, `ProcessFileLock`, compact row fields from the design, and a details JSONL row only when details are present.

- [ ] **Step 3: Verify storage tests pass**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_operator_activity -v`
Expected: PASS.

## Task 2: API And Auth Integration

**Files:**
- Modify: `src/minimal_kanban/operator_auth.py`
- Modify: `src/minimal_kanban/api/server.py`
- Modify: `src/minimal_kanban/app.py`
- Modify: `src/minimal_kanban/mcp/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing API tests**

Add tests for:

- admin can call `/api/list_operator_activity`;
- operator can call `/api/list_operator_activity` but sees only own rows;
- non-admin cannot read another user's activity;
- `/api/open_card` records an activity row;
- `/api/get_operator_profile` reads `cards_opened` from activity rows while retaining fallback behavior.

Run focused tests with `.\.venv\Scripts\python.exe -m unittest tests.test_api -v`.
Expected: FAIL on missing routes/recording.

- [ ] **Step 2: Wire service constructors**

Instantiate `OperatorActivityService` next to `OperatorAuthService`. Pass the service into `OperatorAuthService` and `ApiServer`.

- [ ] **Step 3: Add API routes and auth rules**

Add:

- `GET|POST /api/list_operator_activity`
- `GET|POST /api/get_operator_activity_details`
- `GET|POST /api/get_operator_activity_aggregates`
- `GET|POST /api/export_operator_activity`

Routes require an operator session. Admins can read all users; non-admins are restricted to themselves.

- [ ] **Step 4: Record auth/open-card/admin activity**

Record:

- login;
- logout;
- open card;
- save/delete operator user;
- operator report export.

If recording fails, log and keep the original CRM action successful.

- [ ] **Step 5: Verify API tests pass**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_api -v`
Expected: PASS.

## Task 3: Dense Admin Journal UI

**Files:**
- Modify: `src/minimal_kanban/web_app_assets/assembler.py`
- Test: `tests/test_web_assets.py`

- [ ] **Step 1: Write failing UI asset tests**

Assert the generated HTML contains:

- `id="operatorActivityFilters"`;
- `id="operatorActivityTable"`;
- `id="operatorActivityExportButton"`;
- `'/api/list_operator_activity'`;
- `'/api/export_operator_activity'`;
- sticky first column CSS for the activity table;
- no card-based mobile replacement.

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_web_assets -v`
Expected: FAIL.

- [ ] **Step 2: Implement admin journal markup**

Replace the admin modal center with:

- filter toolbar;
- dense table columns `Время`, `Пользователь`, `Модуль`, `Действие`, `Объект`, `Суть изменения`, `Сумма`, `Источник`;
- horizontal scroll container;
- user list as a secondary section.

- [ ] **Step 3: Implement JS loading/filter/export**

Add state for filters and activity rows. Load the first page when the admin modal opens. Export text using `/api/export_operator_activity`.

- [ ] **Step 4: Verify UI tests pass**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_web_assets -v`
Expected: PASS.

## Task 4: R3 Retention And Aggregates

**Files:**
- Modify: `src/minimal_kanban/operator_activity.py`
- Create: `scripts/operator_activity_maintenance.py`
- Test: `tests/test_operator_activity.py`

- [ ] **Step 1: Write failing retention tests**

Create rows older than 90 days and assert dry-run reports eligible rows without deletion, apply removes old detail rows after aggregate updates, and aggregate files retain counters.

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_operator_activity -v`
Expected: FAIL on missing retention behavior.

- [ ] **Step 2: Implement retention helpers and script**

Implement dry-run/apply maintenance with `--backup` required for apply. Keep recent detail rows and write aggregate counters for old rows.

- [ ] **Step 3: Verify retention tests pass**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_operator_activity -v`
Expected: PASS.

## Task 5: Documentation And Release Checks

**Files:**
- Modify: `API_GUIDE.md`
- Modify: `README.md`
- Possibly modify: `docs/OPERATIONS_RUNBOOK.md`

- [ ] **Step 1: Document routes and storage**

Update API docs with operator activity routes and README data paths.

- [ ] **Step 2: Run focused checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check src\minimal_kanban\operator_activity.py scripts\operator_activity_maintenance.py tests\test_operator_activity.py
.\.venv\Scripts\python.exe -m ruff check src\minimal_kanban\operator_activity.py scripts\operator_activity_maintenance.py tests\test_operator_activity.py
.\.venv\Scripts\python.exe -m unittest tests.test_operator_activity tests.test_api tests.test_web_assets -v
python scripts\docs_audit.py --format text
python scripts\audit_localization.py
```

Expected: all PASS.

- [ ] **Step 3: Run broad checks if focused checks pass**

Run:

```powershell
.\scripts\run_checks.ps1
```

Expected: PASS.
