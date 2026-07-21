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

    def test_stale_full_card_response_cannot_cross_operator_sessions(self) -> None:
        fetch_full_card = _source_section(
            self.source,
            "async function fetchFullCard(cardId, expectedUpdatedAt = '')",
            "function boardCardElementById(",
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


if __name__ == "__main__":
    unittest.main()
