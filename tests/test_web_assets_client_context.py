from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/minimal_kanban/web_app_assets/source/app_main_before_printing.js"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.web_assets import BOARD_WEB_APP_CONTRACT_TEXT  # noqa: E402


def section(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset)]


class OperatorProfileContractTests(unittest.TestCase):
    def test_absent_admin_activity_grid_has_no_state_or_event_bindings(self) -> None:
        for old_name in (
            "operatorActivityFilters",
            "operatorActivityDays",
            "operatorActivityUserFilter",
            "operatorActivityModuleFilter",
            "operatorActivityActionFilter",
            "operatorActivitySearchInput",
            "operatorActivityExportButton",
            "operatorActivityTable",
            "operatorActivityMeta",
            "operatorActivityScrollHint",
            "operatorActivityDetailsPanel",
            "operatorActivityRows",
            "operatorActivitySelectedId",
            "operatorActivityDetailsLoading",
            "operatorActivityDebounceTimer",
            "renderOperatorActivityUserOptions",
            "reloadOperatorActivity",
            "exportOperatorActivity",
            "updateOperatorActivityScrollHint",
            "handleOperatorActivityTableClick",
            "handleOperatorActivityTableKeydown",
        ):
            self.assertNotIn(old_name, BOARD_WEB_APP_CONTRACT_TEXT)
        self.assertIn('id="operatorActivityList"', BOARD_WEB_APP_CONTRACT_TEXT)
        self.assertIn(
            "renderOperatorActivity(profile?.recent_actions || []);", BOARD_WEB_APP_CONTRACT_TEXT
        )
        self.assertIn("'/api/get_operator_profile'", BOARD_WEB_APP_CONTRACT_TEXT)


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class ClientContextRegressionTests(unittest.TestCase):
    def run_node(self, body: str) -> None:
        result = subprocess.run(
            ["node"], input=body, text=True, capture_output=True, cwd=ROOT, timeout=15
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_operator_profile_still_renders_recent_actions_and_empty_state(self) -> None:
        renderer = section(
            SOURCE.read_text(encoding="utf-8"),
            "    function renderOperatorActivity(",
            "    function renderOperatorProfile(",
        )
        self.run_node(
            """
const assert = require('node:assert/strict');
const els = {operatorActivityList:{innerHTML:''}};
function escapeHtml(value) {return String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;');}
function formatDate(value) {return 'date:' + value;}
"""
            + renderer
            + """
renderOperatorActivity([{timestamp:'2026-09-08',message:'Opened <card>'}]);
assert.match(els.operatorActivityList.innerHTML, /date:2026-09-08/);
assert.match(els.operatorActivityList.innerHTML, /Opened &lt;card&gt;/);
assert.doesNotMatch(els.operatorActivityList.innerHTML, /Opened <card>/);
renderOperatorActivity([]);
assert.match(els.operatorActivityList.innerHTML, /ДЕЙСТВИЙ ПОКА НЕТ/);
assert.doesNotMatch(els.operatorActivityList.innerHTML, /Opened/);
"""
        )

    def test_board_reconciliation_preserves_unchanged_nodes_and_reorders(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        reconcile = section(
            source, "    function reconcileBoardCards(", "    function reconcileBoardSection("
        )
        self.run_node(
            """
const assert = require('node:assert/strict');
const state = {};
function list(ids) {
  const parent={children:[], insertBefore(node,before){node.remove(); const index=before ? this.children.indexOf(before) : this.children.length; this.children.splice(index,0,node); node.parent=this;}};
  parent.children=ids.map(id=>({dataset:{cardId:id},parent,remove(){const index=this.parent.children.indexOf(this);if(index>=0)this.parent.children.splice(index,1);}}));
  return parent;
}
"""
            + reconcile
            + """
const current=list(['a','b','c']);
let models=[{id:'a',value:1},{id:'b',value:1},{id:'c',value:1}];
reconcileBoardCards(current,current,models);
const [a,b,c]=current.children;
reconcileBoardCards(current,list(['a','b','c']),[{id:'a',value:1},{id:'b',value:2},{id:'c',value:1}]);
assert.equal(current.children[0],a); assert.notEqual(current.children[1],b); assert.equal(current.children[2],c);
const replacement=current.children[1];
reconcileBoardCards(current,list(['c','b','d']),[{id:'c',value:1},{id:'b',value:2},{id:'d',value:1}]);
assert.deepEqual(current.children.map(node=>node.dataset.cardId),['c','b','d']);
assert.equal(current.children[0],c);assert.equal(current.children[1],replacement);
const virtual=current.children[0];
reconcileBoardCards(current,list(['c']),[{id:'c',value:1}],'changed-filter');
assert.notEqual(current.children[0],virtual);
"""
        )

    def test_client_selection_responses_and_errors_belong_to_current_selection(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        desktop = section(
            source, "    async function selectClient(", "    function activeClientVehicles("
        )
        mobile = section(
            source,
            "    async function loadMobileClientProfile(",
            "    function openMobileClientsPanel(",
        )
        self.run_node(
            """
const assert = require('node:assert/strict');
const state = {viewerStateGeneration: 0, clientsProfileRequestSeq: 0};
const pending = new Map(), errors = [];
function api(path) { return new Promise((resolve,reject) => pending.set(path.match(/client_id=([^&]*)/)[1], {resolve,reject})); }
function renderClientProfile(data) { state.clientsActiveProfile = data; }
function renderClientsList() {}
function renderMobileClientsPanel() {}
function setStatus(message) { errors.push(message); }
"""
            + desktop
            + mobile
            + """
(async () => {
  const first = selectClient('A'), second = selectClient('B');
  pending.get('B').resolve({client:{id:'B'}}); await second;
  pending.get('A').resolve({client:{id:'A'}}); await first;
  assert.equal(state.clientsActiveId, 'B');
  assert.equal(state.clientsActiveProfile.client.id, 'B');
  const third = loadMobileClientProfile('C'), fourth = loadMobileClientProfile('D');
  pending.get('C').reject(new Error('obsolete')); await third;
  assert.equal(state.clientsActiveId, 'D');
  assert.equal(state.mobileClientProfileLoading, true);
  assert.deepEqual(errors, []);
  pending.get('D').resolve({client:{id:'D'}}); await fourth;
  assert.equal(state.mobileClientProfileLoading, false);
  const old = selectClient('old'); state.viewerStateGeneration++;
  pending.get('old').resolve({client:{id:'old'}}); await old;
  assert.equal(state.clientsActiveProfile.client.id, 'D');
  const closed = selectClient('closed'); state.clientsActiveId = '';
  pending.get('closed').resolve({client:{id:'closed'}}); await closed;
  assert.equal(state.clientsActiveId, '');
})().catch(error => {console.error(error); process.exitCode=1;});
"""
        )

    def test_modal_data_does_not_reopen_or_report_errors_after_invalidation(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        loader = section(
            source, "    async function loadModalData(", "    function clientDisplayName("
        )
        self.run_node(
            """
const assert = require('node:assert/strict');
let finish, valid = true, applied = 0, opened = 0, reported = 0;
function api() { return new Promise((resolve,reject) => {finish={resolve,reject};}); }
function perfMeasureAsync(_name, fn) {return fn();}
function maybeOpenModal() {opened++;}
function setStatus() {reported++;}
"""
            + loader
            + """
(async () => {
  let request=loadModalData('/clients', {openModal:true, isCurrent:()=>valid, onSuccess:()=>applied++});
  valid=false; finish.resolve({clients:[]}); await request;
  assert.equal(applied,0); assert.equal(opened,0);
  valid=true;
  request=loadModalData('/clients', {openModal:true, isCurrent:()=>valid, onError:()=>applied++});
  valid=false; finish.reject(new Error('obsolete')); await request;
  assert.equal(applied,0); assert.equal(opened,0); assert.equal(reported,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_full_card_cache_requires_revision_and_is_bounded(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        cache = section(
            source, "    function cacheFullCard(", "    function boardCardElementsById("
        )
        self.run_node(
            """
const assert = require('node:assert/strict');
const FULL_CARD_CACHE_LIMIT = 3;
const state = {fullCardCache:new Map(),cardFetchInFlight:new Map(),viewerStateGeneration:0};
let fetches=0;
async function api() {fetches++; return {card:{id:'archive',updated_at:'new'}};}
function applyCardSeenSuppression(card) {return card;}
"""
            + cache
            + """
(async () => {
  cacheFullCard({id:'archive',updated_at:'old'});
  assert.equal(cachedFullCardForSnapshot({id:'archive'}),null);
  assert.equal((await fetchFullCard('archive')).updated_at,'new');
  assert.equal(fetches,1);
  assert.equal((await fetchFullCard('archive','new')).updated_at,'new');
  assert.equal(fetches,1);
  cacheFullCard({id:'b',updated_at:'1'}); cacheFullCard({id:'c',updated_at:'1'});
  cachedFullCardForSnapshot({id:'archive',updated_at:'new'});
  cacheFullCard({id:'d',updated_at:'1'});
  assert.equal(state.fullCardCache.size,3);
  assert.equal(state.fullCardCache.has('archive'),true);
  assert.equal(state.fullCardCache.has('b'),false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )
