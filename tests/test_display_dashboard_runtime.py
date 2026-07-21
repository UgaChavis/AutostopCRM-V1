from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = (
    ROOT / "src" / "minimal_kanban" / "web_app_assets" / "source" / "display_dashboard.html"
)
NODE = shutil.which("node")


def _dashboard_script(source: str) -> str:
    start = source.rindex("<script>") + len("<script>")
    end = source.index("</script>", start)
    return source[start:end]


@unittest.skipUnless(NODE, "Node.js is required for dashboard runtime regression tests")
class DisplayDashboardRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dashboard_script = _dashboard_script(SOURCE_PATH.read_text(encoding="utf-8"))

    def _run_node(self, scenario: str) -> None:
        harness = r"""
          const assert = require('node:assert/strict');

          class FakeElement {
            constructor() {
              this.dataset = {};
              this.hidden = false;
              this.textContent = '';
              this.innerHTMLWrites = 0;
              this._innerHTML = '';
              this.style = { setProperty() {} };
            }
            set innerHTML(value) {
              this.innerHTMLWrites += 1;
              this._innerHTML = value;
            }
            get innerHTML() { return this._innerHTML; }
          }

          const ids = [
            'dashboard', 'statusBadge', 'updatedAt', 'salaryPeriod', 'salaryList',
            'weeksChart', 'averageNote', 'accessState',
          ];
          const elements = Object.fromEntries(ids.map((id) => [id, new FakeElement()]));
          const documentListeners = {};
          const windowListeners = {};
          let nextTimerId = 1;
          let storageFailure = false;
          const timers = new Map();
          const fetchCalls = [];

          global.document = {
            hidden: false,
            getElementById(id) { return elements[id]; },
            addEventListener(name, callback) { documentListeners[name] = callback; },
          };
          global.window = {
            setTimeout(callback, delay) {
              const id = nextTimerId++;
              timers.set(id, { callback, delay });
              return id;
            },
            clearTimeout(id) { timers.delete(id); },
            setInterval() { throw new Error('dashboard polling must not use setInterval'); },
            clearInterval() {},
            addEventListener(name, callback) { windowListeners[name] = callback; },
          };
          global.localStorage = {
            getItem() {
              if (storageFailure) throw new Error('storage unavailable');
              return '';
            },
          };
          global.requestAnimationFrame = (callback) => callback();
          global.fetch = (path, options) => new Promise((resolve, reject) => {
            const call = { path, options, resolve, reject, aborted: false };
            fetchCalls.push(call);
            options.signal?.addEventListener('abort', () => {
              call.aborted = true;
              const error = new Error('aborted');
              error.name = 'AbortError';
              reject(error);
            }, { once: true });
          });

          function makeData(generatedAt) {
            return {
              schema_version: 'display_dashboard.v2',
              generated_at: generatedAt,
              timezone: 'Asia/Krasnoyarsk',
              salary_period: { label: '20.07–26.07', date_from: '2026-07-20', date_to: '2026-07-26' },
              employees: [{ name: 'Мастер', position: 'Механик', salary: '1000' }],
              weeks: [0, 1, 2, 3].map((index) => ({
                label: 'W' + index,
                amount: String((index + 1) * 1000),
                orders_count: index + 1,
                is_current: index === 3,
              })),
              completed_week_average: '2000',
            };
          }

          function respond(callIndex, generatedAt) {
            fetchCalls[callIndex].resolve({
              ok: true,
              status: 200,
              async json() { return { ok: true, data: makeData(generatedAt) }; },
            });
          }

          function respondWithHangingBody(callIndex) {
            const call = fetchCalls[callIndex];
            call.resolve({
              ok: true,
              status: 200,
              json() {
                return new Promise((_resolve, reject) => {
                  const rejectAbort = () => {
                    call.bodyAbortObserved = true;
                    const error = new Error('body aborted');
                    error.name = 'AbortError';
                    reject(error);
                  };
                  if (call.options.signal.aborted) rejectAbort();
                  else call.options.signal.addEventListener('abort', rejectAbort, { once: true });
                });
              },
            });
          }

          async function drainMicrotasks() {
            for (let index = 0; index < 8; index += 1) await Promise.resolve();
            await new Promise((resolve) => setImmediate(resolve));
          }

          function timerCount(delay) {
            return [...timers.values()].filter((timer) => timer.delay === delay).length;
          }

          function fireTimer(delay) {
            const match = [...timers.entries()].find(([, timer]) => timer.delay === delay);
            assert.ok(match, 'missing timer with delay ' + delay);
            const [id, timer] = match;
            timers.delete(id);
            return timer.callback();
          }
        """
        script = (
            textwrap.dedent(harness)
            + "\n"
            + self.dashboard_script
            + "\n(async () => {\n"
            + textwrap.dedent(scenario)
            + "\n})().catch((error) => { console.error(error); process.exitCode = 1; });\n"
        )
        with tempfile.TemporaryDirectory(prefix="autostop-dashboard-runtime-") as temp_dir:
            script_path = Path(temp_dir) / "dashboard-runtime-test.cjs"
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

    def test_polling_waits_for_completion_and_pauses_while_hidden(self) -> None:
        self._run_node(
            r"""
              await drainMicrotasks();
              assert.equal(fetchCalls.length, 1, 'initial refresh did not start');
              assert.equal(timerCount(45000), 0, 'next poll started before refresh completed');

              respond(0, '2026-07-21T10:00:00+07:00');
              await drainMicrotasks();
              assert.equal(timerCount(45000), 1, 'next poll was not scheduled');

              const scheduledRefresh = fireTimer(45000);
              await drainMicrotasks();
              assert.equal(fetchCalls.length, 2);
              const concurrentRefresh = window.__AUTOSTOP_DISPLAY_DASHBOARD__.refresh();
              await drainMicrotasks();
              assert.equal(fetchCalls.length, 2, 'concurrent refresh overlapped the active request');

              document.hidden = true;
              documentListeners.visibilitychange();
              await drainMicrotasks();
              assert.equal(fetchCalls[1].aborted, true, 'hidden dashboard kept its active request');
              assert.equal(timerCount(45000), 0, 'hidden dashboard kept its polling timer');
              assert.equal(fetchCalls.length, 2, 'hidden dashboard refreshed');

              document.hidden = false;
              documentListeners.visibilitychange();
              await drainMicrotasks();
              assert.equal(fetchCalls.length, 3, 'visible dashboard did not refresh immediately');
              respond(2, '2026-07-21T10:02:00+07:00');
              await Promise.all([scheduledRefresh, concurrentRefresh]);
              await drainMicrotasks();
              assert.equal(timerCount(45000), 1, 'polling did not resume after visibility recovery');
            """
        )

    def test_request_timeout_aborts_fetch_and_polling_recovers(self) -> None:
        self._run_node(
            r"""
              await drainMicrotasks();
              respond(0, '2026-07-21T10:00:00+07:00');
              await drainMicrotasks();

              const scheduledRefresh = fireTimer(45000);
              await drainMicrotasks();
              assert.equal(fetchCalls.length, 2);
              respondWithHangingBody(1);
              await drainMicrotasks();
              fireTimer(15000);
              await scheduledRefresh;
              await drainMicrotasks();

              assert.equal(fetchCalls[1].aborted, true, 'timed-out fetch was not aborted');
              assert.equal(fetchCalls[1].bodyAbortObserved, true, 'response body ignored abort');
              assert.equal(elements.statusBadge.textContent, 'НЕТ ОБНОВЛЕНИЯ');
              assert.equal(window.__AUTOSTOP_DISPLAY_DASHBOARD__.state.inFlight, false);
              assert.equal(timerCount(45000), 1, 'polling did not recover after timeout');
            """
        )

    def test_unchanged_payload_updates_freshness_without_rerendering_charts(self) -> None:
        self._run_node(
            r"""
              await drainMicrotasks();
              respond(0, '2026-07-21T10:00:00+07:00');
              await drainMicrotasks();
              const firstUpdatedAt = elements.updatedAt.textContent;
              assert.equal(elements.salaryList.innerHTMLWrites, 1);
              assert.equal(elements.weeksChart.innerHTMLWrites, 1);

              const refresh = window.__AUTOSTOP_DISPLAY_DASHBOARD__.refresh();
              await drainMicrotasks();
              respond(1, '2026-07-21T10:01:00+07:00');
              await refresh;

              assert.equal(elements.salaryList.innerHTMLWrites, 1, 'employees rerendered unchanged data');
              assert.equal(elements.weeksChart.innerHTMLWrites, 1, 'weeks rerendered unchanged data');
              assert.notEqual(elements.updatedAt.textContent, firstUpdatedAt, 'freshness timestamp stayed stale');
              assert.equal(elements.statusBadge.textContent, 'АКТУАЛЬНО');
            """
        )

    def test_synchronous_refresh_setup_failure_releases_state_and_polling_recovers(self) -> None:
        self._run_node(
            r"""
              await drainMicrotasks();
              respond(0, '2026-07-21T10:00:00+07:00');
              await drainMicrotasks();

              storageFailure = true;
              const failedRefresh = fireTimer(45000);
              await failedRefresh;
              await drainMicrotasks();

              const hook = window.__AUTOSTOP_DISPLAY_DASHBOARD__;
              assert.equal(hook.state.inFlight, false, 'sync failure left refresh in flight');
              assert.equal(hook.state.refreshPromise, null, 'sync failure retained refresh promise');
              assert.equal(elements.statusBadge.textContent, 'НЕТ ОБНОВЛЕНИЯ');
              assert.equal(timerCount(45000), 1, 'sync failure stopped recursive polling');

              storageFailure = false;
              const recoveredRefresh = fireTimer(45000);
              await drainMicrotasks();
              assert.equal(fetchCalls.length, 2, 'polling did not retry after sync failure');
              respond(1, '2026-07-21T10:03:00+07:00');
              await recoveredRefresh;
              await drainMicrotasks();
              assert.equal(elements.statusBadge.textContent, 'АКТУАЛЬНО');
              assert.equal(timerCount(45000), 1, 'recovered polling did not continue');
            """
        )


if __name__ == "__main__":
    unittest.main()
