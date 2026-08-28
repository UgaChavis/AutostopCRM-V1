from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = (
    ROOT / "src" / "minimal_kanban" / "web_app_assets" / "source" / "app_main_before_printing.js"
)
NODE = shutil.which("node")


def _source_section(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _optional_source_section(source: str, start_marker: str, end_marker: str) -> str:
    try:
        return _source_section(source, start_marker, end_marker)
    except ValueError:
        return ""


@unittest.skipUnless(NODE, "Node.js is required for frontend runtime regression tests")
class WebAssetsRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")

    def _run_node(self, body: str) -> None:
        script = textwrap.dedent(body)
        with tempfile.TemporaryDirectory(prefix="autostop-web-runtime-") as temp_dir:
            script_path = Path(temp_dir) / "runtime-test.cjs"
            script_path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [NODE, str(script_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_repair_order_mutation_updates_archive_action_without_reopening_card(self) -> None:
        archive_helpers = _source_section(
            self.source,
            "function cardArchiveAvailability(card)",
            "function ensureRepairOrderRows(",
        )
        apply_update = _source_section(
            self.source,
            "function applyRepairOrderCardUpdate(updatedCard, fallbackOrder = {})",
            "function repairOrderRowInputHtml(",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            const state = {{
              activeCard: {{
                id: 'card-1',
                title: 'Полная карточка',
                description: 'Несокращённое описание',
                repair_order: {{ status: 'open', is_fully_paid: true }},
              }},
              activeCardIsFull: true,
              mobileCard: null,
            }};
            const classes = new Set();
            const els = {{
              archiveAction: {{
                disabled: true,
                dataset: {{}},
                title: '',
                classList: {{
                  toggle(name, enabled) {{
                    if (enabled) classes.add(name); else classes.delete(name);
                  }},
                }},
              }},
            }};
            function repairOrderHasAnyData() {{ return true; }}
            function repairOrderIsEmptyForArchive() {{ return false; }}
            function normalizeRepairOrderStatus(value) {{ return String(value || '').toLowerCase(); }}
            function repairOrderIsFullyPaid(order) {{ return order?.is_fully_paid === true; }}
            function repairOrderCardDraft(card, order) {{ return order || card?.repair_order || {{}}; }}
            function applyRepairOrderToForm() {{}}
            function refreshRepairOrderEntry() {{}}
            let patchedCard = null;
            function applySavedCardLocalPatch(card) {{ patchedCard = card; return true; }}

            {archive_helpers}
            {apply_update}

            applyRepairOrderCardUpdate({{
              id: 'card-1',
              repair_order: {{ status: 'closed', is_fully_paid: true }},
            }});
            assert.equal(els.archiveAction.disabled, false);
            assert.equal(els.archiveAction.dataset.archiveAvailable, 'true');
            assert.equal(state.activeCard.description, 'Несокращённое описание');
            assert.equal(state.activeCardIsFull, true);
            assert.equal(patchedCard.id, 'card-1');

            applyRepairOrderCardUpdate({{
              id: 'card-1',
              repair_order: {{ status: 'open', is_fully_paid: true }},
            }});
            assert.equal(els.archiveAction.disabled, true);
            assert.match(els.archiveAction.title, /открыт/i);

            applyRepairOrderCardUpdate({{
              id: 'card-1',
              repair_order: {{ status: 'closed', is_fully_paid: false }},
            }});
            assert.equal(els.archiveAction.disabled, true);
            assert.match(els.archiveAction.title, /не оплачен/i);
            """
        )

    def test_move_card_delta_reorders_snapshot_and_preserves_full_active_card(self) -> None:
        board_patch_helpers = _source_section(
            self.source,
            "function applyBoardColumnCardsPatch(nextCards, affectedColumnIds)",
            "function applyBoardColumnsPatch(nextColumns)",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            const state = {{
              snapshot: {{
                cards: [
                  {{ id: 'a', column: 'inbox', position: 0, description: 'A' }},
                  {{ id: 'b', column: 'inbox', position: 1, description: 'board preview' }},
                  {{ id: 'c', column: 'in_progress', position: 0, description: 'C' }},
                ],
              }},
              activeCard: {{ id: 'b', column: 'inbox', description: 'Полное описание', private_full: true }},
              activeCardIsFull: true,
              mobileCard: null,
              fullCardCache: new Map(),
            }};
            function applyCardSeenSuppressionsToCards(cards) {{ return cards; }}
            function applyCardSeenSuppression(card) {{ return card; }}
            function cacheFullCard(card) {{ state.fullCardCache.set(card.id, card); return card; }}
            function boardCardFromFullCard(card) {{
              return {{ ...card, description: 'board:' + card.description }};
            }}
            function extraBoardColumnIsOpen() {{ return false; }}
            function buildBoardCardsByColumn(snapshot) {{ return snapshot.cards; }}
            function renderBoardColumnById() {{ return true; }}
            function renderBoard() {{ throw new Error('unexpected full render'); }}

            {board_patch_helpers}

            assert.equal(
              applyBoardColumnCardsPatch(
                [{{ id: 'a', column: 'inbox' }}, {{ id: 'b', column: 'inbox', description: 'compact' }}],
                ['inbox'],
              ),
              true,
            );
            assert.equal(
              state.activeCard.description,
              'Полное описание',
              'legacy compact patch downgraded the full active card',
            );

            state.snapshot.cards = [
              {{ id: 'a', column: 'inbox', position: 0, description: 'A' }},
              {{ id: 'b', column: 'inbox', position: 1, description: 'board preview' }},
              {{ id: 'c', column: 'in_progress', position: 0, description: 'C' }},
            ];
            const patched = applyBoardColumnOrderDelta(
              {{ id: 'b', column: 'in_progress', description: 'Серверное полное описание' }},
              [
                {{ column_id: 'inbox', ordered_card_ids: ['a'] }},
                {{ column_id: 'in_progress', ordered_card_ids: ['b', 'c'] }},
              ],
              ['inbox', 'in_progress'],
            );
            assert.equal(patched, true);
            assert.deepEqual(
              state.snapshot.cards
                .filter((card) => card.column === 'inbox')
                .sort((left, right) => left.position - right.position)
                .map((card) => card.id),
              ['a'],
            );
            assert.deepEqual(
              state.snapshot.cards
                .filter((card) => card.column === 'in_progress')
                .sort((left, right) => left.position - right.position)
                .map((card) => card.id),
              ['b', 'c'],
            );
            assert.equal(state.activeCard.description, 'Серверное полное описание');
            assert.equal(state.activeCard.private_full, true);
            assert.equal(state.activeCardIsFull, true);

            const snapshotBeforeInvalid = JSON.stringify(state.snapshot.cards);
            assert.equal(
              applyBoardColumnOrderDelta(
                {{ id: 'b', column: 'in_progress' }},
                [
                  {{ column_id: 'inbox', ordered_card_ids: ['a'] }},
                  {{ column_id: 'in_progress', ordered_card_ids: ['b', 'missing'] }},
                ],
                ['inbox', 'in_progress'],
              ),
              false,
            );
            assert.equal(JSON.stringify(state.snapshot.cards), snapshotBeforeInvalid);
            """
        )

    def test_operator_session_reset_and_login_refresh_are_viewer_scoped(self) -> None:
        reset_helper = _optional_source_section(
            self.source,
            "function resetViewerScopedState()",
            "function clearOperatorSession(",
        )
        clear_session = _source_section(
            self.source,
            "function clearOperatorSession(",
            "function requireOperatorSession(",
        )
        login_operator = _source_section(
            self.source,
            "async function loginOperator()",
            "function handleIdentityCredentialInput(",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            (() => {{
            const clearedTimers = [];
            global.window = {{
              clearTimeout(timerId) {{ clearedTimers.push(timerId); }},
              setTimeout,
            }};
            const state = {{
              actor: 'FIRST',
              operatorProfile: {{ user: {{ username: 'FIRST' }} }},
              operatorUsers: [{{ username: 'FIRST' }}],
              operatorSessionToken: 'first-session',
              snapshot: {{ meta: {{ revision: 'viewer-first' }} }},
              lastSnapshotRevision: 'viewer-first',
              refreshInFlight: Promise.resolve(),
              fullCardCache: new Map([['card-1', {{ id: 'card-1' }}]]),
              cardFetchInFlight: new Map([['card-1', Promise.resolve()]]),
              cardSeenSuppressions: new Map([['card-1', {{ suppressed_at_ms: 1 }}]]),
              unreadHoverTimers: new Map([['card-1', 11]]),
              unreadSeenDeferredTimers: new Map([['card-1', 12]]),
              unreadSeenInFlight: new Set(['card-1']),
              viewerStateGeneration: 0,
              archiveCards: [{{ id: 'archived-1' }}],
              archiveLoaded: true,
              archiveLoading: Promise.resolve(),
              employees: [{{ id: 'employee-1' }}],
              employeesLoadedMonth: '2026-01',
              employeesReferencePromise: {{
                month: '2026-01',
                viewerStateGeneration: 0,
                promise: Promise.resolve(),
              }},
              employeesWorkspaceLoadGeneration: 4,
              activeEmployeeId: 'employee-1',
              employeeCreateMode: false,
              employeeFormBaseline: 'employee-form',
              payrollMonth: '2026-01',
              payrollReport: {{ meta: {{ month: '2026-01' }} }},
              payrollReportMonth: '2026-01',
              activeEmployeeSalaryId: 'employee-1',
              activeEmployeeSalaryReportId: 'report-1',
              activeEmployeeSalaryReconciliationReportId: 'reconciliation-1',
              employeeSalarySheet: {{ employee_id: 'employee-1' }},
              employeeSalaryReport: {{ employee_id: 'employee-1' }},
              activeCard: {{ id: 'card-1' }},
              activeCardIsFull: true,
              editingId: 'card-1',
              mobileCard: {{ id: 'card-1' }},
              mobileCardId: 'card-1',
              mobileCardJournalPayload: {{ entries: [] }},
              mobileCardJournalLoadedFor: 'card-1',
              cardHydrationSeq: 3,
              cardHydratingId: 'card-1',
              cardOpenSideEffectTimer: 13,
              cardOpenSideEffectCardId: 'card-1',
            }};
            const els = {{
              board: {{ replaceChildren() {{}} }},
              mobileBoardColumns: {{ textContent: '' }},
              identityInput: {{ value: 'SECOND', focus() {{}} }},
              identityPassword: {{ value: 'secret', focus() {{}}, select() {{}} }},
              identityModal: {{ classList: {{ contains() {{ return false; }} }} }},
            }};
            function clearCardOpenSideEffectTimer() {{
              if (state.cardOpenSideEffectTimer) window.clearTimeout(state.cardOpenSideEffectTimer);
              state.cardOpenSideEffectTimer = null;
              state.cardOpenSideEffectCardId = '';
            }}
            function setOperatorSessionToken(token) {{ state.operatorSessionToken = token; }}
            function applyBoardScalePreference() {{}}
            function updateOperatorButton() {{}}
            function closeOperatorEmployeeBinding() {{}}
            function popModal() {{}}
            function setStatus() {{}}
            function openOperatorLoginModal() {{}}

            {reset_helper}
            {clear_session}

            clearOperatorSession();
            assert.equal(state.snapshot, null, 'logout must discard the previous viewer snapshot');
            assert.equal(state.lastSnapshotRevision, '', 'logout must discard the viewer revision');
            assert.equal(state.refreshInFlight, null, 'logout must detach an old viewer refresh');
            assert.equal(state.fullCardCache.size, 0, 'logout must clear full-card cache');
            assert.equal(state.cardFetchInFlight.size, 0, 'logout must clear card fetches');
            assert.equal(state.cardSeenSuppressions.size, 0, 'logout must clear seen suppressions');
            assert.equal(state.unreadHoverTimers.size, 0, 'logout must clear hover timers');
            assert.equal(state.unreadSeenDeferredTimers.size, 0, 'logout must clear deferred seen timers');
            assert.equal(state.unreadSeenInFlight.size, 0, 'logout must clear seen requests');
            assert.equal(state.viewerStateGeneration, 1, 'logout must invalidate stale async work');
            assert.equal(state.employeesWorkspaceLoadGeneration, 5);
            assert.deepEqual(state.employees, []);
            assert.equal(state.employeesLoadedMonth, '');
            assert.equal(state.employeesReferencePromise, null);
            assert.equal(state.activeEmployeeId, '');
            assert.equal(state.employeeFormBaseline, null);
            assert.equal(state.payrollMonth, '');
            assert.equal(state.payrollReport, null);
            assert.equal(state.payrollReportMonth, '');
            assert.equal(state.activeEmployeeSalaryId, '');
            assert.equal(state.activeEmployeeSalaryReportId, '');
            assert.equal(state.activeEmployeeSalaryReconciliationReportId, '');
            assert.equal(state.employeeSalarySheet, null);
            assert.equal(state.employeeSalaryReport, null);
            assert.deepEqual(new Set(clearedTimers), new Set([11, 12, 13]));
            }})();

            (async () => {{
            const events = [];
            const state = {{ snapshot: {{ meta: {{ revision: 'viewer-first' }} }} }};
            const els = {{
              identityInput: {{ value: 'SECOND', focus() {{}} }},
              identityPassword: {{ value: 'secret', focus() {{}}, select() {{}} }},
              identityModal: {{ classList: {{ contains() {{ return false; }} }} }},
            }};
            function setOperatorLoginBusy(value) {{ events.push('busy:' + value); }}
            function setOperatorLoginFeedback() {{}}
            function setStatus() {{}}
            async function api(path) {{
              assert.equal(path, '/api/login_operator');
              events.push('login');
              return {{
                user: {{ username: 'SECOND', is_admin: false }},
                session: {{ token: 'second-session' }},
                stats: {{}},
              }};
            }}
            function resetViewerScopedState() {{
              events.push('reset');
              state.snapshot = null;
            }}
            function renderOperatorProfile() {{ events.push('profile'); }}
            async function refreshSnapshot(showSuccess) {{
              assert.equal(showSuccess, true);
              events.push('refresh:start');
              await new Promise((resolve) => setTimeout(resolve, 20));
              state.snapshot = {{ meta: {{ revision: 'viewer-second' }} }};
              events.push('refresh:end');
            }}
            function updateSnapshotStatusLine() {{ events.push('status'); }}

            {login_operator}

            await loginOperator();
            events.push('resolved');
            assert.ok(events.indexOf('login') < events.indexOf('reset'));
            assert.ok(events.indexOf('reset') < events.indexOf('profile'));
            assert.ok(events.indexOf('profile') < events.indexOf('refresh:start'));
            assert.ok(events.indexOf('refresh:end') < events.indexOf('resolved'));
            assert.equal(state.snapshot.meta.revision, 'viewer-second');
            }})().catch((error) => {{
              console.error(error);
              process.exitCode = 1;
            }});
            """
        )

    def test_archive_response_and_error_cannot_cross_operator_sessions(self) -> None:
        reset_helper = _source_section(
            self.source,
            "function resetViewerScopedState()",
            "function clearOperatorSession(",
        )
        archive_loader = _source_section(
            self.source,
            "async function loadArchive(",
            "function currentPayrollMonthValue(",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            global.window = {{ clearTimeout, setTimeout }};
            const state = {{
              viewerStateGeneration: 0,
              snapshot: null,
              lastSnapshotRevision: '',
              refreshInFlight: null,
              archiveCards: [],
              archiveLoaded: false,
              archiveLoading: null,
              fullCardCache: new Map(),
              cardFetchInFlight: new Map(),
              cardSeenSuppressions: new Map(),
              unreadHoverTimers: new Map(),
              unreadSeenDeferredTimers: new Map(),
              unreadSeenInFlight: new Set(),
              activeCard: null,
              activeCardIsFull: false,
              editingId: null,
              mobileCard: null,
              mobileCardId: '',
              mobileCardJournalPayload: null,
              mobileCardJournalLoadedFor: '',
              cardHydrationSeq: 0,
              cardHydratingId: '',
              boardViewportPrimed: false,
              employees: [],
              employeesLoadedMonth: '',
              employeesReferencePromise: null,
              employeesWorkspaceLoadGeneration: 0,
              activeEmployeeId: '',
              employeeCreateMode: false,
              employeeFormBaseline: null,
              payrollMonth: '',
              payrollReport: null,
              payrollReportMonth: '',
              activeEmployeeSalaryId: '',
              activeEmployeeSalaryReportId: '',
              activeEmployeeSalaryReconciliationReportId: '',
              employeeSalarySheet: null,
              employeeSalaryReport: null,
            }};
            const els = {{
              board: {{ replaceChildren() {{}} }},
              mobileBoardColumns: {{ textContent: '' }},
              archiveModal: {{}},
              archiveList: {{ innerHTML: '' }},
            }};
            const requests = [];
            const renders = [];
            const statusMessages = [];
            const ARCHIVE_PREVIEW_LIMIT = 100;
            function clearCardOpenSideEffectTimer() {{}}
            function maybeOpenModal() {{}}
            function renderArchive() {{
              renders.push(state.archiveCards.map((card) => card.viewer));
            }}
            function setStatus(message) {{ statusMessages.push(message); }}
            function api(url) {{
              let resolve;
              let reject;
              const promise = new Promise((resolveRequest, rejectRequest) => {{
                resolve = resolveRequest;
                reject = rejectRequest;
              }});
              requests.push({{ url, promise, resolve, reject }});
              return promise;
            }}

            {reset_helper}
            {archive_loader}

            (async () => {{
              const staleSuccess = loadArchive(false, {{ force: true }});
              const staleSuccessRequest = requests[0];
              resetViewerScopedState();
              const currentSuccess = loadArchive(false, {{ force: true }});
              const currentSuccessRequest = requests[1];
              assert.ok(currentSuccessRequest, 'new viewer did not start its own archive request');

              staleSuccessRequest.resolve({{
                cards: [{{ id: 'archive-first', viewer: 'FIRST' }}],
              }});
              assert.equal(await staleSuccess, null);
              assert.deepEqual(state.archiveCards, [], 'stale archive response crossed sessions');
              assert.equal(state.archiveLoaded, false);
              assert.ok(state.archiveLoading, 'stale cleanup detached the current archive request');
              assert.deepEqual(renders, []);

              currentSuccessRequest.resolve({{
                cards: [{{ id: 'archive-second', viewer: 'SECOND' }}],
              }});
              await currentSuccess;
              assert.equal(state.archiveCards[0]?.viewer, 'SECOND');
              assert.equal(state.archiveLoaded, true);
              assert.equal(state.archiveLoading, null);
              assert.deepEqual(renders, [['SECOND']]);

              const staleError = loadArchive(false, {{ force: true }});
              const staleErrorRequest = requests[2];
              resetViewerScopedState();
              const currentAfterError = loadArchive(false, {{ force: true }});
              const currentAfterErrorRequest = requests[3];
              const currentLoadingMarkup = els.archiveList.innerHTML;

              staleErrorRequest.reject(new Error('FIRST VIEWER ERROR'));
              assert.equal(await staleError, null);
              assert.deepEqual(statusMessages, [], 'stale archive error reached the new viewer');
              assert.equal(els.archiveList.innerHTML, currentLoadingMarkup);
              assert.ok(state.archiveLoading, 'stale error cleanup detached the current request');

              currentAfterErrorRequest.resolve({{
                cards: [{{ id: 'archive-fourth', viewer: 'FOURTH' }}],
              }});
              await currentAfterError;
              assert.equal(state.archiveCards[0]?.viewer, 'FOURTH');
              assert.equal(state.archiveLoading, null);
            }})().catch((error) => {{
              console.error(error);
              process.exitCode = 1;
            }});
            """
        )

    def test_get_timeout_covers_response_body_and_releases_snapshot_refresh(self) -> None:
        api_function = _source_section(
            self.source,
            "async function api(path, options = {})",
            "function setApiToken(",
        )
        refresh_snapshot = _source_section(
            self.source,
            "async function refreshSnapshot(showSuccess = false)",
            "async function refreshSnapshotRevision()",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            global.window = {{ setTimeout, clearTimeout }};
            const API_READ_RETRY_LIMIT = 0;
            const API_READ_RETRY_BASE_DELAY_MS = 0;
            const state = {{
              apiToken: '',
              operatorSessionToken: 'viewer-session',
              snapshot: null,
              lastSnapshotRevision: '',
              refreshInFlight: null,
              viewerStateGeneration: 0,
            }};
            const els = {{
              statusLine: {{ dataset: {{ connection: 'online' }} }},
              archiveModal: {{ classList: {{ contains() {{ return false; }} }} }},
              gptWallModal: {{ classList: {{ contains() {{ return false; }} }} }},
            }};
            let bodyAbortObserved = false;
            let statusMessage = '';
            function perfStart() {{ return null; }}
            function perfEnd() {{}}
            async function perfMeasureAsync(_name, callback) {{ return await callback(); }}
            function apiReadTimeoutMs() {{ return 25; }}
            function delay(ms) {{ return new Promise((resolve) => setTimeout(resolve, ms)); }}
            function clearOperatorSession() {{}}
            function openOperatorLoginModal() {{}}
            function accessDeniedMessage() {{ return 'denied'; }}
            function notifyCashboxesMutation() {{}}
            function applyCardSeenSuppressionsToSnapshot(value) {{ return value; }}
            function showConnectionPendingStatus() {{}}
            function applyBoardScalePreference() {{}}
            function renderBoard() {{}}
            function primeBoardViewport() {{}}
            async function loadArchive() {{}}
            async function loadGptWall() {{}}
            function updateSnapshotStatusLine() {{}}
            function setStatus(message) {{ statusMessage = message; }}
            global.fetch = async (_path, request) => {{
              const signal = request.signal;
              return {{
                ok: true,
                status: 200,
                headers: {{ get() {{ return ''; }} }},
                text() {{
                  return new Promise((_resolve, reject) => {{
                    const rejectAbort = () => {{
                      bodyAbortObserved = true;
                      const error = new Error('aborted');
                      error.name = 'AbortError';
                      reject(error);
                    }};
                    if (signal?.aborted) rejectAbort();
                    else signal?.addEventListener('abort', rejectAbort, {{ once: true }});
                  }});
                }},
              }};
            }};

            {api_function}
            {refresh_snapshot}

            (async () => {{
              const refresh = refreshSnapshot(false).then(() => 'settled');
              const outcome = await Promise.race([
                refresh,
                new Promise((resolve) => setTimeout(() => resolve('harness-timeout'), 150)),
              ]);
              assert.equal(outcome, 'settled', 'response-body timeout did not settle refreshSnapshot');
              assert.equal(bodyAbortObserved, true, 'response body did not receive abort');
              assert.equal(state.refreshInFlight, null, 'refreshInFlight was not released');
              assert.match(statusMessage, /НЕТ ОТВЕТА ОТ ДОСКИ/);
            }})().catch((error) => {{
              console.error(error);
              process.exitCode = 1;
            }});
            """
        )

    def test_employee_and_payroll_responses_cannot_cross_operator_sessions(self) -> None:
        reset_helper = _source_section(
            self.source,
            "function resetViewerScopedState()",
            "function clearOperatorSession(",
        )
        employee_loaders = _source_section(
            self.source,
            "function applyEmployeesReferenceData",
            "function refreshRepairOrderEmployeeSelects(",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            global.window = {{ clearTimeout, setTimeout }};
            const state = {{
              viewerStateGeneration: 0,
              snapshot: null,
              lastSnapshotRevision: '',
              refreshInFlight: null,
              archiveCards: [],
              archiveLoaded: false,
              archiveLoading: null,
              fullCardCache: new Map(),
              cardFetchInFlight: new Map(),
              cardSeenSuppressions: new Map(),
              unreadHoverTimers: new Map(),
              unreadSeenDeferredTimers: new Map(),
              unreadSeenInFlight: new Set(),
              activeCard: null,
              activeCardIsFull: false,
              editingId: null,
              mobileCard: null,
              mobileCardId: '',
              mobileCardJournalPayload: null,
              mobileCardJournalLoadedFor: '',
              cardHydrationSeq: 0,
              cardHydratingId: '',
              boardViewportPrimed: false,
              employees: [],
              employeesLoadedMonth: '',
              employeesReferencePromise: null,
              employeesWorkspaceLoadGeneration: 0,
              activeEmployeeId: '',
              employeeCreateMode: false,
              employeeFormBaseline: null,
              payrollMonth: '',
              payrollReport: null,
              payrollReportMonth: '',
              activeEmployeeSalaryId: '',
              activeEmployeeSalaryReportId: '',
              activeEmployeeSalaryReconciliationReportId: '',
              employeeSalarySheet: null,
              employeeSalaryReport: null,
            }};
            const els = {{
              board: {{ replaceChildren() {{}} }},
              mobileBoardColumns: {{ textContent: '' }},
            }};
            const requests = [];
            function clearCardOpenSideEffectTimer() {{}}
            function currentPayrollMonthValue() {{ return '2026-01'; }}
            function api(url) {{
              let resolve;
              let reject;
              const promise = new Promise((resolveRequest, rejectRequest) => {{
                resolve = resolveRequest;
                reject = rejectRequest;
              }});
              requests.push({{ url, promise, resolve, reject }});
              return promise;
            }}

            {reset_helper}
            {employee_loaders}

            (async () => {{
              const staleWorkspace = loadEmployeesWorkspaceData('2026-01');
              const staleEmployeeRequest = requests[0];
              const stalePayrollRequest = requests[1];

              resetViewerScopedState();
              const currentWorkspace = loadEmployeesWorkspaceData('2026-01');
              const employeeRequests = requests.filter((request) =>
                request.url === '/api/list_employees?month=2026-01'
              );
              const payrollRequests = requests.filter((request) =>
                request.url === '/api/get_payroll_report?month=2026-01'
              );
              assert.equal(employeeRequests.length, 2, 'new viewer reused old employee request');
              assert.equal(payrollRequests.length, 2);

              staleEmployeeRequest.resolve({{
                employees: [{{ id: 'employee-first', viewer: 'FIRST' }}],
              }});
              stalePayrollRequest.resolve({{ meta: {{ month: '2026-01', viewer: 'FIRST' }} }});
              const staleWorkspaceResult = await staleWorkspace;
              assert.equal(staleWorkspaceResult.applied, false);
              assert.deepEqual(state.employees, []);
              assert.equal(state.payrollReport, null);
              assert.equal(
                state.employeesReferencePromise?.viewerStateGeneration,
                state.viewerStateGeneration,
                'stale cleanup removed the new viewer employee request',
              );

              employeeRequests[1].resolve({{
                employees: [{{ id: 'employee-second', viewer: 'SECOND' }}],
              }});
              payrollRequests[1].resolve({{
                meta: {{ month: '2026-01', viewer: 'SECOND' }},
              }});
              const currentWorkspaceResult = await currentWorkspace;
              assert.equal(currentWorkspaceResult.applied, true);
              assert.equal(state.employees[0]?.viewer, 'SECOND');
              assert.equal(state.payrollReport?.meta?.viewer, 'SECOND');

              resetViewerScopedState();
              state.payrollMonth = '2026-02';
              const staleEmployee = loadEmployeesReference({{ month: '2026-02', apply: true }});
              const stalePayroll = loadPayrollReport({{ month: '2026-02', apply: true }});
              const directRequestOffset = requests.length - 2;

              resetViewerScopedState();
              state.payrollMonth = '2026-02';
              const currentEmployee = loadEmployeesReference({{ month: '2026-02', apply: true }});
              const currentPayroll = loadPayrollReport({{ month: '2026-02', apply: true }});
              const februaryEmployees = requests.filter((request) =>
                request.url === '/api/list_employees?month=2026-02'
              );
              const februaryPayroll = requests.filter((request) =>
                request.url === '/api/get_payroll_report?month=2026-02'
              );
              assert.equal(februaryEmployees.length, 2);
              assert.equal(februaryPayroll.length, 2);

              requests[directRequestOffset].resolve({{
                employees: [{{ id: 'employee-third', viewer: 'THIRD' }}],
              }});
              requests[directRequestOffset + 1].resolve({{
                meta: {{ month: '2026-02', viewer: 'THIRD' }},
              }});
              await Promise.all([staleEmployee, stalePayroll]);
              assert.deepEqual(state.employees, []);
              assert.equal(state.payrollReport, null);
              assert.equal(
                state.employeesReferencePromise?.viewerStateGeneration,
                state.viewerStateGeneration,
              );

              februaryEmployees[1].resolve({{
                employees: [{{ id: 'employee-fourth', viewer: 'FOURTH' }}],
              }});
              februaryPayroll[1].resolve({{
                meta: {{ month: '2026-02', viewer: 'FOURTH' }},
              }});
              await Promise.all([currentEmployee, currentPayroll]);
              assert.equal(state.employees[0]?.viewer, 'FOURTH');
              assert.equal(state.payrollReport?.meta?.viewer, 'FOURTH');
            }})().catch((error) => {{
              console.error(error);
              process.exitCode = 1;
            }});
            """
        )

    def test_employee_workspace_loads_in_parallel_and_latest_month_wins(self) -> None:
        employee_loaders = _source_section(
            self.source,
            "function applyEmployeesReferenceData",
            "function refreshRepairOrderEmployeeSelects(",
        )
        employee_workspace = _source_section(
            self.source,
            "async function loadEmployeesWorkspace",
            "async function addEmployeeFromForm(",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            const state = {{
              payrollMonth: '',
              payrollReport: null,
              payrollReportMonth: '',
              employees: [],
              employeesLoadedMonth: '',
              employeesReferencePromise: null,
              employeesWorkspaceLoadGeneration: 0,
              employeeCreateMode: false,
              activeEmployeeId: '',
            }};
            const els = {{ employeesMonthInput: {{ value: '' }} }};
            const requests = [];
            const renders = [];
            function currentPayrollMonthValue() {{ return '2026-01'; }}
            function renderEmployeesWorkspace() {{
              renders.push({{
                month: state.payrollMonth,
                employeesMonth: state.employeesLoadedMonth,
                employeeId: state.employees[0]?.id || '',
                reportMonth: state.payrollReport?.meta?.month || '',
              }});
            }}
            function refreshRepairOrderEmployeeSelects() {{}}
            function pushModal() {{}}
            global.HTMLElement = class HTMLElement {{}};
            function api(url) {{
              return new Promise((resolve) => requests.push({{ url, resolve, resolved: false }}));
            }}
            async function drainMicrotasks() {{
              for (let index = 0; index < 8; index += 1) await Promise.resolve();
            }}
            function resolveAll(url, data) {{
              requests
                .filter((request) => request.url === url && !request.resolved)
                .forEach((request) => {{
                  request.resolved = true;
                  request.resolve(data);
                }});
            }}

            {employee_loaders}
            {employee_workspace}

            (async () => {{
              els.employeesMonthInput.value = '2026-01';
              const januaryLoad = loadEmployeesWorkspace(false);
              await drainMicrotasks();
              const januaryStartedInParallel = requests.some(
                (request) => request.url === '/api/get_payroll_report?month=2026-01',
              );

              els.employeesMonthInput.value = '2026-02';
              const februaryLoad = loadEmployeesWorkspace(false);
              await drainMicrotasks();

              resolveAll('/api/list_employees?month=2026-02', {{
                employees: [{{ id: 'february-employee', name: 'February' }}],
              }});
              await drainMicrotasks();
              resolveAll('/api/get_payroll_report?month=2026-02', {{
                meta: {{ month: '2026-02' }},
                summary: [],
                detail_rows: [],
              }});
              await drainMicrotasks();

              resolveAll('/api/list_employees?month=2026-01', {{
                employees: [{{ id: 'january-employee', name: 'January' }}],
              }});
              await drainMicrotasks();
              resolveAll('/api/get_payroll_report?month=2026-02', {{
                meta: {{ month: '2026-02' }},
                summary: [],
                detail_rows: [],
              }});
              resolveAll('/api/get_payroll_report?month=2026-01', {{
                meta: {{ month: '2026-01' }},
                summary: [],
                detail_rows: [],
              }});
              await Promise.all([januaryLoad, februaryLoad]);

              assert.equal(
                januaryStartedInParallel,
                true,
                'employee and payroll requests did not start in parallel',
              );
              assert.equal(state.payrollMonth, '2026-02');
              assert.equal(state.employeesLoadedMonth, '2026-02');
              assert.equal(state.employees[0]?.id, 'february-employee');
              assert.equal(state.payrollReport?.meta?.month, '2026-02');
              assert.equal(state.payrollReportMonth, '2026-02');
              assert.deepEqual(renders, [{{
                month: '2026-02',
                employeesMonth: '2026-02',
                employeeId: 'february-employee',
                reportMonth: '2026-02',
              }}]);
            }})().catch((error) => {{
              console.error(error);
              process.exitCode = 1;
            }});
            """
        )

    def test_employee_salary_reset_permission_payload_and_double_click_guard(self) -> None:
        permission_helper = _source_section(
            self.source,
            "function operatorHasPermission(permission)",
            "function operatorStatHtml(",
        )
        reset_flow = _source_section(
            self.source,
            "function createEmployeeSalaryResetIdempotencyKey()",
            "async function handleEmployeeSalaryActionConfirm()",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            const SALARY_BALANCE_RESET_PERMISSION = 'salary_balance_reset';
            const state = {{
              operatorProfile: {{ user: {{ permissions: [] }} }},
              activeEmployeeSalaryId: 'employee-1',
              employeeSalarySheet: {{
                balance_minor: 1234567,
                balance_revision: 'salary-revision-1',
                balance_display: '12 345,67 ₽',
              }},
              employeeSalaryResetPending: false,
              employeeSalaryResetIntent: null,
              employeesLoadedMonth: '2026-08',
            }};
            const requests = [];
            const confirmations = [];
            const statuses = [];
            let salaryModalRenders = 0;
            let employeesReloads = 0;
            let payrollReloads = 0;
            let workspaceRenders = 0;

            global.window = {{
              crypto: {{ randomUUID() {{ return 'runtime-reset-key'; }} }},
              confirm(message) {{
                confirmations.push(message);
                return true;
              }},
            }};
            function selectedEmployeeSalaryRecord() {{
              return {{ id: 'employee-1', name: 'Петров Пётр' }};
            }}
            function renderEmployeeSalaryModal() {{ salaryModalRenders += 1; }}
            function setStatus(message, isError) {{ statuses.push({{ message, isError }}); }}
            function api(path, options = {{}}) {{
              return new Promise((resolve, reject) => {{
                requests.push({{ path, options, resolve, reject }});
              }});
            }}
            async function loadEmployeesReference() {{ employeesReloads += 1; }}
            async function loadPayrollReport() {{ payrollReloads += 1; }}
            function renderEmployeesWorkspace() {{ workspaceRenders += 1; }}
            async function loadEmployeeSalarySheet() {{
              throw new Error('conflict reload is not expected in this scenario');
            }}

            {permission_helper}
            {reset_flow}

            (async () => {{
              await handleEmployeeSalaryReset();
              assert.equal(requests.length, 0, 'permission denial still sent a reset');
              assert.equal(confirmations.length, 0, 'permission denial still opened confirmation');
              assert.equal(statuses.at(-1)?.isError, true);
              assert.match(statuses.at(-1)?.message || '', /НЕТ ПРАВА/);

              state.operatorProfile.user.permissions = [SALARY_BALANCE_RESET_PERMISSION];
              const firstClick = handleEmployeeSalaryReset();
              assert.equal(state.employeeSalaryResetPending, true);
              assert.equal(requests.length, 1);
              assert.equal(confirmations.length, 1);

              const secondClick = handleEmployeeSalaryReset();
              await secondClick;
              assert.equal(requests.length, 1, 'synchronous double-click sent a second POST');
              assert.equal(confirmations.length, 1, 'synchronous double-click confirmed twice');
              assert.equal(
                state.employeeSalaryResetIntent?.idempotencyKey,
                'salary-balance-reset-runtime-reset-key',
              );
              assert.equal(requests[0].path, '/api/reset_employee_salary_balance');
              assert.equal(requests[0].options.method, 'POST');
              assert.deepEqual(requests[0].options.body, {{
                employee_id: 'employee-1',
                expected_balance_minor: 1234567,
                expected_balance_revision: 'salary-revision-1',
                idempotency_key: 'salary-balance-reset-runtime-reset-key',
                source: 'ui',
              }});
              assert.match(confirmations[0], /Петров Пётр/);
              assert.match(confirmations[0], /12 345,67 ₽/);
              assert.match(confirmations[0], /некассовая корректировка/);
              assert.match(confirmations[0], /История выплат сохранится/);

              requests[0].resolve({{
                ledger: {{ balance_minor: 0, balance_revision: 'salary-revision-2' }},
                meta: {{ replayed: false }},
              }});
              await firstClick;
              assert.equal(state.employeeSalaryResetPending, false);
              assert.equal(state.employeeSalaryResetIntent, null);
              assert.equal(state.employeeSalarySheet.balance_minor, 0);
              assert.equal(state.employeesLoadedMonth, '');
              assert.equal(employeesReloads, 1);
              assert.equal(payrollReloads, 1);
              assert.equal(workspaceRenders, 1);
              assert.ok(salaryModalRenders >= 3);
              assert.equal(statuses.at(-1)?.message, 'БАЛАНС ОБНУЛЁН.');
            }})().catch((error) => {{
              console.error(error);
              process.exitCode = 1;
            }});
            """
        )

    def test_operator_password_save_preserves_permission_without_explicit_edit(self) -> None:
        save_flow = _source_section(
            self.source,
            "async function saveOperatorUser()",
            "async function deleteOperatorUser(",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            const SALARY_BALANCE_RESET_PERMISSION = 'salary_balance_reset';
            const state = {{
              operatorUsers: [{{
                username: 'UGA',
                permissions: [SALARY_BALANCE_RESET_PERMISSION],
              }}],
              operatorPermissionEditorUsername: '',
            }};
            const els = {{
              adminUserLogin: {{ value: 'UGA' }},
              adminUserPassword: {{ value: 'new-password' }},
              adminUserSalaryBalanceReset: {{ checked: false }},
            }};
            const requests = [];
            const statuses = [];

            async function api(path, options) {{
              requests.push({{ path, options }});
              return {{ meta: {{ updated: true }}, user: {{ username: options.body.username }} }};
            }}
            function setStatus(message, isError) {{ statuses.push({{ message, isError }}); }}
            async function refreshOperatorAdminSurfaces() {{}}

            {save_flow}

            (async () => {{
              await saveOperatorUser();
              assert.equal(requests[0].path, '/api/save_operator_user');
              assert.equal(requests[0].options.body.username, 'UGA');
              assert.equal(requests[0].options.body.password, 'new-password');
              assert.equal(
                Object.hasOwn(requests[0].options.body, 'permissions'),
                false,
                'ordinary password change revoked an existing permission',
              );

              state.operatorPermissionEditorUsername = 'UGA';
              els.adminUserLogin.value = 'UGA';
              els.adminUserPassword.value = '';
              els.adminUserSalaryBalanceReset.checked = false;
              await saveOperatorUser();
              assert.deepEqual(requests[1].options.body.permissions, []);

              state.operatorPermissionEditorUsername = '';
              els.adminUserLogin.value = 'NEW-OPERATOR';
              els.adminUserPassword.value = 'initial-password';
              els.adminUserSalaryBalanceReset.checked = true;
              await saveOperatorUser();
              assert.deepEqual(
                requests[2].options.body.permissions,
                [SALARY_BALANCE_RESET_PERMISSION],
              );
              assert.equal(statuses.some((item) => item.isError), false);
            }})().catch((error) => {{
              console.error(error);
              process.exitCode = 1;
            }});
            """
        )

    def test_employee_salary_reset_retry_reuses_key_and_conflict_reloads_snapshot(self) -> None:
        permission_helper = _source_section(
            self.source,
            "function operatorHasPermission(permission)",
            "function operatorStatHtml(",
        )
        reset_flow = _source_section(
            self.source,
            "function createEmployeeSalaryResetIdempotencyKey()",
            "async function handleEmployeeSalaryActionConfirm()",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            const SALARY_BALANCE_RESET_PERMISSION = 'salary_balance_reset';
            const state = {{
              operatorProfile: {{
                user: {{ permissions: [SALARY_BALANCE_RESET_PERMISSION] }},
              }},
              activeEmployeeSalaryId: 'employee-1',
              employeeSalarySheet: {{
                balance_minor: 5000,
                balance_revision: 'salary-revision-1',
                balance_display: '50,00 ₽',
              }},
              employeeSalaryResetPending: false,
              employeeSalaryResetIntent: null,
              employeesLoadedMonth: '2026-08',
            }};
            const requests = [];
            const confirmations = [];
            const reloads = [];
            const statuses = [];
            let uuidCalls = 0;

            global.window = {{
              crypto: {{
                randomUUID() {{
                  uuidCalls += 1;
                  return 'runtime-reset-key-' + uuidCalls;
                }},
              }},
              confirm(message) {{
                confirmations.push(message);
                return true;
              }},
            }};
            function selectedEmployeeSalaryRecord() {{
              return {{ id: 'employee-1', name: 'Петров Пётр' }};
            }}
            function renderEmployeeSalaryModal() {{}}
            function renderEmployeesWorkspace() {{}}
            function setStatus(message, isError) {{ statuses.push({{ message, isError }}); }}
            function api(path, options = {{}}) {{
              return new Promise((resolve, reject) => {{
                requests.push({{ path, options, resolve, reject }});
              }});
            }}
            async function loadEmployeesReference() {{}}
            async function loadPayrollReport() {{}}
            async function loadEmployeeSalarySheet(employeeId, options) {{
              reloads.push({{ employeeId, options }});
              state.employeeSalarySheet = {{
                balance_minor: 7500,
                balance_revision: 'salary-revision-3',
                balance_display: '75,00 ₽',
              }};
            }}

            {permission_helper}
            {reset_flow}

            (async () => {{
              const uncertainAttempt = handleEmployeeSalaryReset();
              assert.equal(requests.length, 1);
              requests[0].reject(new Error('СЕТЕВОЙ ОТВЕТ НЕИЗВЕСТЕН'));
              await uncertainAttempt;
              assert.equal(state.employeeSalaryResetPending, false);
              assert.equal(uuidCalls, 1);
              const retainedKey = state.employeeSalaryResetIntent?.idempotencyKey;
              assert.equal(retainedKey, 'salary-balance-reset-runtime-reset-key-1');

              const retryAttempt = handleEmployeeSalaryReset();
              assert.equal(requests.length, 2);
              assert.equal(
                requests[1].options.body.idempotency_key,
                retainedKey,
                'uncertain retry did not reuse the original idempotency key',
              );
              assert.equal(uuidCalls, 1, 'uncertain retry minted a second key');
              requests[1].resolve({{
                ledger: {{ balance_minor: 0, balance_revision: 'salary-revision-2' }},
                meta: {{ replayed: true }},
              }});
              await retryAttempt;
              assert.equal(state.employeeSalaryResetIntent, null);
              assert.equal(statuses.at(-1)?.message, 'ОБНУЛЕНИЕ УЖЕ БЫЛО ПРИМЕНЕНО.');

              state.employeeSalarySheet = {{
                balance_minor: 9000,
                balance_revision: 'salary-revision-conflict',
                balance_display: '90,00 ₽',
              }};
              const conflictAttempt = handleEmployeeSalaryReset();
              assert.equal(requests.length, 3);
              assert.equal(
                requests[2].options.body.idempotency_key,
                'salary-balance-reset-runtime-reset-key-2',
              );
              const conflict = new Error('BALANCE CHANGED');
              conflict.code = 'salary_balance_reset_conflict';
              requests[2].reject(conflict);
              await conflictAttempt;

              assert.equal(uuidCalls, 2);
              assert.equal(state.employeeSalaryResetPending, false);
              assert.equal(state.employeeSalaryResetIntent, null);
              assert.deepEqual(reloads, [{{
                employeeId: 'employee-1',
                options: {{ openModal: true }},
              }}]);
              assert.equal(state.employeeSalarySheet.balance_revision, 'salary-revision-3');
              assert.match(statuses.at(-1)?.message || '', /БАЛАНС ИЗМЕНИЛСЯ/);
              assert.equal(confirmations.length, 3);
            }})().catch((error) => {{
              console.error(error);
              process.exitCode = 1;
            }});
            """
        )

    def test_stale_full_card_response_cannot_cross_operator_sessions(self) -> None:
        fetch_full_card = _source_section(
            self.source,
            "async function fetchFullCard(cardId, expectedUpdatedAt = '')",
            "function boardCardElementsById(",
        )

        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            const state = {{
              viewerStateGeneration: 0,
              fullCardCache: new Map(),
              cardFetchInFlight: new Map(),
            }};
            const requestResolvers = [];
            function api() {{
              return new Promise((resolve) => requestResolvers.push(resolve));
            }}
            function applyCardSeenSuppression(card) {{ return card; }}
            function cacheFullCard(card) {{
              state.fullCardCache.set(card.id, card);
              return card;
            }}

            {fetch_full_card}

            (async () => {{
              const staleRequest = fetchFullCard('card-1', 'revision-1');
              state.viewerStateGeneration += 1;
              state.fullCardCache.clear();
              state.cardFetchInFlight.clear();
              const currentRequest = fetchFullCard('card-1', 'revision-1');

              requestResolvers[0]({{
                card: {{ id: 'card-1', updated_at: 'revision-1', viewer: 'FIRST' }},
              }});
              assert.equal(await staleRequest, null);
              assert.equal(state.fullCardCache.size, 0, 'stale response repopulated the cache');
              assert.equal(
                state.cardFetchInFlight.has('card-1'),
                true,
                'stale cleanup removed the current viewer request',
              );

              requestResolvers[1]({{
                card: {{ id: 'card-1', updated_at: 'revision-1', viewer: 'SECOND' }},
              }});
              const currentCard = await currentRequest;
              assert.equal(currentCard.viewer, 'SECOND');
              assert.equal(state.fullCardCache.get('card-1').viewer, 'SECOND');
              assert.equal(state.cardFetchInFlight.size, 0);
            }})().catch((error) => {{
              console.error(error);
              process.exitCode = 1;
            }});
            """
        )

    def test_personal_extra_column_matches_exact_tag_and_color_without_archived_cards(self) -> None:
        extra_column_helpers = _source_section(
            self.source,
            "function normalizeTagColor(color)",
            "function normalizeRepairOrderTags",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            const CARD_TAG_LIMIT = 3;
            const CARD_STORED_TAG_LIMIT = 4;
            const READY_CARD_TAG_LABEL = 'ГОТОВ';
            const EXTRA_BOARD_COLUMN_DEFAULT_TAG_LABEL = 'НАДО ЧТО ТО СДЕЛАТЬ';
            const EXTRA_BOARD_COLUMN_DEFAULT_TAG_COLOR = 'red';
            const TAG_COLOR_OPTIONS = [
              {{ value: 'green', label: 'ЗЕЛЁНЫЙ' }},
              {{ value: 'yellow', label: 'ЖЁЛТЫЙ' }},
              {{ value: 'red', label: 'КРАСНЫЙ' }},
            ];
            const state = {{ personalBoardPreferences: null, operatorProfile: null, snapshot: null }};
            const els = {{}};
            function finiteNumber(value, fallback = 0) {{
              const parsed = Number(value);
              return Number.isFinite(parsed) ? parsed : fallback;
            }}
            function sortBoardCards(cards) {{ return Array.from(cards || []); }}
            function escapeHtml(value) {{ return String(value || ''); }}
            function requireOperatorSession() {{ return true; }}
            function renderBoard() {{}}
            function setStatus() {{}}
            async function api() {{ return {{}}; }}

            {extra_column_helpers}

            state.personalBoardPreferences = {{
              extra_column: {{
                is_open: true,
                filter: {{ tag_label: 'НАДО ЧТО ТО СДЕЛАТЬ', tag_color: 'red' }},
              }},
            }};
            assert.equal(extraBoardColumnIsOpen(), true);
            assert.equal(
              normalizeExtraBoardColumnTagLabel(' надо,  что   то сделать '),
              'НАДО ЧТО ТО СДЕЛАТЬ',
            );
            assert.equal(cardMatchesExtraBoardColumn({{
              id: 'match',
              tag_items: [{{ label: 'надо что то сделать', color: 'red' }}],
            }}), true);
            assert.equal(cardMatchesExtraBoardColumn({{
              id: 'wrong-color',
              tag_items: [{{ label: 'НАДО ЧТО ТО СДЕЛАТЬ', color: 'yellow' }}],
            }}), false);
            assert.equal(cardMatchesExtraBoardColumn({{
              id: 'archived',
              archived: true,
              tag_items: [{{ label: 'НАДО ЧТО ТО СДЕЛАТЬ', color: 'red' }}],
            }}), false);
            assert.equal(cardMatchesExtraBoardColumn({{
              id: 'fourth-tag-match',
              tag_items: [
                {{ label: 'ПЕРВАЯ', color: 'green' }},
                {{ label: 'ВТОРАЯ', color: 'green' }},
                {{ label: 'ТРЕТЬЯ', color: 'yellow' }},
                {{ label: 'НАДО ЧТО ТО СДЕЛАТЬ', color: 'red' }},
              ],
            }}), true);
            assert.equal(normalizeDraftTags([
              {{ label: 'ГОТОВ', color: 'green' }},
              {{ label: 'ПЕРВАЯ', color: 'green' }},
              {{ label: 'ВТОРАЯ', color: 'yellow' }},
              {{ label: 'НАДО ЧТО ТО СДЕЛАТЬ', color: 'red' }},
            ]).length, 4);
            assert.deepEqual(
              extraBoardColumnCards({{
                cards: [
                  {{ id: 'match', tag_items: [{{ label: 'НАДО ЧТО ТО СДЕЛАТЬ', color: 'red' }}] }},
                  {{ id: 'wrong-color', tag_items: [{{ label: 'НАДО ЧТО ТО СДЕЛАТЬ', color: 'yellow' }}] }},
                  {{ id: 'archived', archived: true, tag_items: [{{ label: 'НАДО ЧТО ТО СДЕЛАТЬ', color: 'red' }}] }},
                  {{ id: 'fourth-tag-match', tag_items: [
                    {{ label: 'ПЕРВАЯ', color: 'green' }},
                    {{ label: 'ВТОРАЯ', color: 'green' }},
                    {{ label: 'ТРЕТЬЯ', color: 'yellow' }},
                    {{ label: 'НАДО ЧТО ТО СДЕЛАТЬ', color: 'red' }},
                  ] }},
                ],
              }}).map((card) => card.id),
              ['match', 'fourth-tag-match'],
            );
            """
        )

    def test_personal_extra_column_async_responses_cannot_cross_operator_sessions(
        self,
    ) -> None:
        extra_column_preferences = _source_section(
            self.source,
            "function normalizeExtraBoardColumnTagLabel(value)",
            "function normalizeRepairOrderTags",
        )
        profile_loader = _source_section(
            self.source,
            "async function loadOperatorProfile(openModal = false)",
            "async function openOperatorWorkspace()",
        )
        profile_renderer = _source_section(
            self.source,
            "function renderOperatorProfile(data,",
            "async function loadOperatorProfile(openModal = false)",
        )
        self._run_node(
            f"""
            const assert = require('node:assert/strict');

            (async () => {{
              {{
                const requests = [];
                const rendered = [];
                const statusMessages = [];
                const CARD_TAG_LIMIT = 3;
                const CARD_STORED_TAG_LIMIT = 4;
                const READY_CARD_TAG_LABEL = 'ГОТОВ';
                const EXTRA_BOARD_COLUMN_DEFAULT_TAG_LABEL = 'НАДО ЧТО ТО СДЕЛАТЬ';
                const EXTRA_BOARD_COLUMN_DEFAULT_TAG_COLOR = 'red';
                const TAG_COLOR_OPTIONS = [
                  {{ value: 'green', label: 'ЗЕЛЁНЫЙ' }},
                  {{ value: 'yellow', label: 'ЖЁЛТЫЙ' }},
                  {{ value: 'red', label: 'КРАСНЫЙ' }},
                ];
                const state = {{
                  viewerStateGeneration: 0,
                  operatorSessionToken: 'first-session',
                  personalBoardPreferencesRevision: 0,
                  extraBoardColumnSaving: false,
                  extraBoardColumnSettingsOpen: false,
                  personalBoardPreferences: {{
                    extra_column: {{
                      is_open: false,
                      filter: {{ tag_label: 'ПЕРВЫЙ', tag_color: 'red' }},
                    }},
                  }},
                  operatorProfile: {{ user: {{ username: 'FIRST' }} }},
                  snapshot: {{ cards: [] }},
                }};
                const els = {{}};
                function requireOperatorSession() {{ return true; }}
                function renderBoard() {{ rendered.push(state.operatorSessionToken); }}
                function setStatus(message) {{ statusMessages.push(message); }}
                function sortBoardCards(cards) {{ return Array.from(cards || []); }}
                function escapeHtml(value) {{ return String(value || ''); }}
                function finiteNumber(value, fallback = 0) {{
                  const parsed = Number(value);
                  return Number.isFinite(parsed) ? parsed : fallback;
                }}
                function api(path, options = {{}}) {{
                  return new Promise((resolve, reject) => requests.push({{ path, options, resolve, reject }}));
                }}

                {extra_column_preferences}

                const staleSave = savePersonalBoardPreferences({{
                  extra_column: {{
                    is_open: true,
                    filter: {{ tag_label: 'ПЕРВАЯ МЕТКА', tag_color: 'yellow' }},
                  }},
                }}, {{ statusMessage: 'ПЕРВАЯ КОЛОНКА СОХРАНЕНА.' }});
                assert.equal(requests.length, 1);
                assert.equal(requests[0].path, '/api/update_personal_board_preferences');

                state.viewerStateGeneration = 1;
                state.operatorSessionToken = 'second-session';
                state.extraBoardColumnSaving = false;
                state.personalBoardPreferences = {{
                  extra_column: {{
                    is_open: false,
                    filter: {{ tag_label: 'ВТОРАЯ МЕТКА', tag_color: 'red' }},
                  }},
                }};
                state.operatorProfile = {{ user: {{ username: 'SECOND' }} }};
                requests[0].resolve({{
                  board_preferences: {{
                    extra_column: {{
                      is_open: true,
                      filter: {{ tag_label: 'ПЕРВАЯ МЕТКА', tag_color: 'yellow' }},
                    }},
                  }},
                }});
                assert.equal(await staleSave, false);
                assert.equal(state.personalBoardPreferences.extra_column.filter.tag_label, 'ВТОРАЯ МЕТКА');
                assert.equal(state.personalBoardPreferences.extra_column.is_open, false);
                assert.deepEqual(rendered, [], 'stale save rendered the next operator board');
                assert.deepEqual(statusMessages, [], 'stale save reported status to the next operator');
                assert.equal(state.extraBoardColumnSaving, false);

                const currentSave = savePersonalBoardPreferences({{
                  extra_column: {{
                    is_open: true,
                    filter: {{ tag_label: 'ВТОРАЯ МЕТКА', tag_color: 'red' }},
                  }},
                }}, {{ statusMessage: 'ВТОРАЯ КОЛОНКА СОХРАНЕНА.' }});
                assert.equal(requests.length, 2);
                requests[1].resolve({{
                  board_preferences: {{
                    extra_column: {{
                      is_open: true,
                      filter: {{ tag_label: 'ВТОРАЯ МЕТКА', tag_color: 'red' }},
                    }},
                  }},
                }});
                assert.equal(await currentSave, true);
                assert.equal(state.personalBoardPreferences.extra_column.is_open, true);
                assert.equal(state.personalBoardPreferencesRevision, 1);
                assert.deepEqual(rendered, ['second-session']);
                assert.deepEqual(statusMessages, ['ВТОРАЯ КОЛОНКА СОХРАНЕНА.']);
              }}

              {{
                const requests = [];
                const renderedProfiles = [];
                const state = {{
                  viewerStateGeneration: 0,
                  operatorSessionToken: 'first-session',
                  personalBoardPreferencesRevision: 0,
                }};
                async function api(path) {{
                  assert.equal(path, '/api/get_operator_profile');
                  return new Promise((resolve, reject) => requests.push({{ resolve, reject }}));
                }}
                function renderOperatorProfile(data, options) {{
                  renderedProfiles.push([
                    data.user.username,
                    Boolean(options?.openModal),
                    Boolean(options?.preservePersonalBoardPreferences),
                  ]);
                }}

                {profile_loader}

                const staleProfile = loadOperatorProfile(true);
                assert.equal(requests.length, 1);
                state.viewerStateGeneration = 1;
                state.operatorSessionToken = 'second-session';
                requests[0].resolve({{ user: {{ username: 'FIRST' }} }});
                assert.equal(await staleProfile, null);
                assert.deepEqual(renderedProfiles, [], 'stale profile opened the next operator view');

                const currentProfile = loadOperatorProfile(false);
                assert.equal(requests.length, 2);
                requests[1].resolve({{ user: {{ username: 'SECOND' }} }});
                assert.deepEqual(await currentProfile, {{ user: {{ username: 'SECOND' }} }});
                assert.deepEqual(renderedProfiles, [['SECOND', false, false]]);

                const stalePreferencesProfile = loadOperatorProfile(false);
                assert.equal(requests.length, 3);
                state.personalBoardPreferencesRevision += 1;
                requests[2].resolve({{ user: {{ username: 'SECOND' }} }});
                assert.deepEqual(await stalePreferencesProfile, {{ user: {{ username: 'SECOND' }} }});
                assert.deepEqual(renderedProfiles, [
                  ['SECOND', false, false],
                  ['SECOND', false, true],
                ]);
              }}

              {{
                const state = {{
                  personalBoardPreferences: {{
                    extra_column: {{
                      is_open: true,
                      filter: {{ tag_label: 'НОВЫЙ ФИЛЬТР', tag_color: 'red' }},
                    }},
                  }},
                  operatorProfile: null,
                  operatorSessionToken: 'second-session',
                  snapshot: null,
                  boardScale: 1,
                }};
                const els = {{
                  operatorProfileMeta: {{ textContent: '' }},
                  operatorStatsGrid: {{ innerHTML: '' }},
                  operatorAdminButton: {{ classList: {{ toggle() {{}} }} }},
                }};
                function personalBoardPreferences() {{ return state.personalBoardPreferences; }}
                function applyPersonalBoardPreferences(value) {{ state.personalBoardPreferences = value; }}
                function setOperatorSessionToken(value) {{ state.operatorSessionToken = value; }}
                function applyBoardScalePreference() {{}}
                function updateOperatorButton() {{}}
                function syncExtraBoardColumnSettingsForm() {{}}
                function renderBoard() {{}}
                function operatorStatHtml() {{ return ''; }}
                function formatDate() {{ return ''; }}
                function renderOperatorActivity() {{}}
                function closeOperatorLoginModal() {{}}
                function refreshCashboxNotification() {{ return Promise.resolve(); }}
                function pushModal() {{}}

                {profile_renderer}

                renderOperatorProfile({{
                  session: {{ token: 'second-session' }},
                  user: {{ username: 'SECOND', is_admin: false, updated_at: '' }},
                  board_preferences: {{
                    extra_column: {{
                      is_open: false,
                      filter: {{ tag_label: 'СТАРЫЙ ФИЛЬТР', tag_color: 'yellow' }},
                    }},
                  }},
                }}, {{ preservePersonalBoardPreferences: true }});
                assert.equal(
                  state.personalBoardPreferences.extra_column.filter.tag_label,
                  'НОВЫЙ ФИЛЬТР',
                );
                assert.equal(
                  state.operatorProfile.board_preferences.extra_column.filter.tag_label,
                  'НОВЫЙ ФИЛЬТР',
                );
              }}

              {{
                const requests = [];
                const CARD_TAG_LIMIT = 3;
                const CARD_STORED_TAG_LIMIT = 4;
                const READY_CARD_TAG_LABEL = 'ГОТОВ';
                const EXTRA_BOARD_COLUMN_DEFAULT_TAG_LABEL = 'НАДО ЧТО ТО СДЕЛАТЬ';
                const EXTRA_BOARD_COLUMN_DEFAULT_TAG_COLOR = 'red';
                const TAG_COLOR_OPTIONS = [
                  {{ value: 'green', label: 'ЗЕЛЁНЫЙ' }},
                  {{ value: 'yellow', label: 'ЖЁЛТЫЙ' }},
                  {{ value: 'red', label: 'КРАСНЫЙ' }},
                ];
                const oldPreferences = {{
                  extra_column: {{
                    is_open: false,
                    filter: {{ tag_label: 'СТАРЫЙ ФИЛЬТР', tag_color: 'yellow' }},
                  }},
                }};
                const newPreferences = {{
                  extra_column: {{
                    is_open: true,
                    filter: {{ tag_label: 'НОВЫЙ ФИЛЬТР', tag_color: 'red' }},
                  }},
                }};
                const state = {{
                  viewerStateGeneration: 0,
                  operatorSessionToken: 'second-session',
                  personalBoardPreferencesRevision: 0,
                  extraBoardColumnSaving: false,
                  extraBoardColumnSettingsOpen: false,
                  personalBoardPreferences: oldPreferences,
                  operatorProfile: {{ user: {{ username: 'SECOND' }}, board_preferences: oldPreferences }},
                  snapshot: null,
                  boardScale: 1,
                }};
                const els = {{
                  operatorProfileMeta: {{ textContent: '' }},
                  operatorStatsGrid: {{ innerHTML: '' }},
                  operatorAdminButton: {{ classList: {{ toggle() {{}} }} }},
                }};
                function requireOperatorSession() {{ return true; }}
                function renderBoard() {{}}
                function setStatus() {{}}
                function finiteNumber(value, fallback = 0) {{
                  const parsed = Number(value);
                  return Number.isFinite(parsed) ? parsed : fallback;
                }}
                function sortBoardCards(cards) {{ return Array.from(cards || []); }}
                function escapeHtml(value) {{ return String(value || ''); }}
                function setOperatorSessionToken(value) {{ state.operatorSessionToken = value; }}
                function applyBoardScalePreference() {{}}
                function updateOperatorButton() {{}}
                function operatorStatHtml() {{ return ''; }}
                function formatDate() {{ return ''; }}
                function renderOperatorActivity() {{}}
                function closeOperatorLoginModal() {{}}
                function refreshCashboxNotification() {{ return Promise.resolve(); }}
                function pushModal() {{}}
                function api(path, options = {{}}) {{
                  return new Promise((resolve, reject) => requests.push({{ path, options, resolve, reject }}));
                }}

                {extra_column_preferences}
                {profile_renderer}
                {profile_loader}

                const delayedProfile = loadOperatorProfile(false);
                assert.equal(requests.length, 1);
                assert.equal(requests[0].path, '/api/get_operator_profile');
                const savedPreferences = savePersonalBoardPreferences(newPreferences);
                assert.equal(requests.length, 2);
                assert.equal(requests[1].path, '/api/update_personal_board_preferences');
                requests[1].resolve({{ board_preferences: newPreferences }});
                assert.equal(await savedPreferences, true);
                assert.equal(state.personalBoardPreferencesRevision, 1);
                assert.equal(state.personalBoardPreferences.extra_column.filter.tag_label, 'НОВЫЙ ФИЛЬТР');

                requests[0].resolve({{
                  session: {{ token: 'second-session' }},
                  user: {{ username: 'SECOND', is_admin: false, updated_at: '' }},
                  board_preferences: oldPreferences,
                }});
                await delayedProfile;
                assert.equal(state.personalBoardPreferences.extra_column.filter.tag_label, 'НОВЫЙ ФИЛЬТР');
                assert.equal(state.operatorProfile.board_preferences.extra_column.filter.tag_label, 'НОВЫЙ ФИЛЬТР');
              }}
            }})().catch((error) => {{
              console.error(error);
              process.exitCode = 1;
            }});
            """
        )


if __name__ == "__main__":
    unittest.main()
