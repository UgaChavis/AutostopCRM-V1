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

    def test_board_position_only_update_keeps_previously_rendered_nodes(self) -> None:
        reconcile = section(
            SOURCE.read_text(encoding="utf-8"),
            "    function reconcileBoardCards(",
            "    function reconcileBoardSection(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');const state={};
function list(){const parent={children:[],insertBefore(node){node.remove();this.children.push(node);node.parent=this;}};parent.children=[{dataset:{cardId:'a'},parent,remove(){this.parent.children=this.parent.children.filter(node=>node!==this);}}];return parent;}
const current=list();const document={createElement(){return {content:list(),set innerHTML(_html){this.content=list();}};}};
function renderBoardCardHtml(){return '<article data-card-id="a"></article>';}
"""
            + reconcile
            + """
reconcileBoardCards(current,current,[{id:'a',title:'stable',position:0}]);
const before=current.children[0];
reconcileBoardCards(current,list(),[{id:'a',title:'stable',position:9}]);
assert.equal(current.children[0],before,'position is ordering, not card presentation');
"""
        )

    def test_board_reconciliation_preserves_unchanged_nodes_and_reorders(self) -> None:
        reconcile = section(
            SOURCE.read_text(encoding="utf-8"),
            "    function reconcileBoardCards(",
            "    function reconcileBoardSection(",
        )
        self.run_node(
            """
const assert = require('node:assert/strict');
const state = {};
const renders=[];
let parses=0;
function list(ids) {
  const parent={children:[], insertBefore(node,before){node.remove(); const index=before ? this.children.indexOf(before) : this.children.length; this.children.splice(index,0,node); node.parent=this;}};
  parent.children=ids.map(id=>({dataset:id ? {cardId:id} : {},parent,remove(){const index=this.parent.children.indexOf(this);if(index>=0)this.parent.children.splice(index,1);}}));
  return parent;
}
function renderBoardCardHtml(card, {virtual=false}={}) {renders.push({id:card.id,virtual});return '<article data-card-id="'+card.id+'"></article>';}
const document={createElement(){return {content:list([]),set innerHTML(html){parses++;this.content=list(Array.from(html.matchAll(/data-card-id="([^"]+)"/g),match=>match[1]));}};}};
"""
            + reconcile
            + """
const current=list([]);
let models=[{id:'a',value:1,position:0},{id:'b',value:1,position:1},{id:'c',value:1,position:2}];
reconcileBoardCards(current,list([]),models);
assert.deepEqual(renders.map(item=>item.id),['a','b','c']);assert.equal(parses,1);renders.length=0;
const [a,b,c]=current.children;
reconcileBoardCards(current,list([]),models);
assert.deepEqual(renders,[]);assert.equal(parses,1);assert.deepEqual(current.children,[a,b,c]);
models=[{id:'a',value:1,position:0},{id:'b',value:2,position:1},{id:'c',value:1,position:2}];
reconcileBoardCards(current,list([]),models);
assert.deepEqual(renders.map(item=>item.id),['b']);assert.equal(parses,2);renders.length=0;
assert.equal(current.children[0],a); assert.notEqual(current.children[1],b); assert.equal(current.children[2],c);
const replacement=current.children[1];
reconcileBoardCards(current,list([]),[{id:'c',value:1,position:0},{id:'b',value:2,position:1},{id:'a',value:1,position:2}]);
assert.deepEqual(renders,[]);assert.equal(parses,2);assert.deepEqual(current.children,[c,replacement,a]);
reconcileBoardCards(current,list([]),[{id:'c',value:1,position:0},{id:'b',value:2,position:1},{id:'d',value:1,position:2}]);
assert.deepEqual(renders.map(item=>item.id),['d']);renders.length=0;
assert.deepEqual(current.children.map(node=>node.dataset.cardId),['c','b','d']);
assert.equal(current.children[0],c);assert.equal(current.children[1],replacement);
reconcileBoardCards(current,list([]),[{id:'c',value:1,column:'new',position:0}]);
assert.deepEqual(renders.map(item=>item.id),['c']);renders.length=0;
const virtual=current.children[0];
reconcileBoardCards(current,list([]),[{id:'c',value:1,column:'new',position:0}],'changed-filter');
assert.deepEqual(renders,[{id:'c',virtual:true}]);renders.length=0;
assert.notEqual(current.children[0],virtual);
const empty=list([null]);reconcileBoardCards(current,empty,[],'changed-filter');
assert.equal(current.children.length,1);assert.equal(current.children[0].dataset.cardId,undefined);
reconcileBoardCards(current,list([null]),[],'changed-filter');assert.equal(current.children.length,1);assert.deepEqual(renders,[]);
reconcileBoardCards(current,list([]),[{id:'a',value:1,position:0}],'changed-filter');
assert.equal(current.children.length,1);assert.equal(current.children[0].dataset.cardId,'a');
const beforeNested=current.children[0];renders.length=0;
reconcileBoardCards(current,list([]),[{id:'a',value:1,position:0,nested:{position:1}}],'changed-filter');
assert.notEqual(current.children[0],beforeNested);assert.equal(renders.length,1);
const nestedOne=current.children[0];renders.length=0;
reconcileBoardCards(current,list([]),[{id:'a',value:1,position:0,nested:{position:2}}],'changed-filter');
assert.notEqual(current.children[0],nestedOne);assert.equal(renders.length,1);
"""
        )

    def test_board_skeleton_preserves_controls_and_only_virtual_empty_placeholder(self) -> None:
        renderers = section(
            SOURCE.read_text(encoding="utf-8"),
            "    function renderBoardColumnHtml(",
            "    function reconcileBoardCards(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');const state={mobileLite:false,boardScale:1};
const COLUMN_TONES=[{tint:'t',head:'h',edge:'e',empty:'x'}],READY_COLUMN_LABEL='ready';
const column={id:'one',label:'regular'},snapshot={columns:[column]};let cards=[{id:'a'},{id:'b'}];
function sortedCardsForBoardColumn(){return cards;}function extraBoardColumnCards(){return cards;}
function extraBoardColumnPreferences(){return {is_detached:false};}
function normalizeBoardScale(){return 1;}function escapeHtml(value){return String(value);}
function renderBoardCardHtml(){throw new Error('skeleton must not render card HTML');}
"""
            + renderers
            + """
let html=renderBoardColumnHtml(column,0,snapshot);
assert.ok(html.includes('<div class="column__cards"></div>'));assert.match(html,/data-card-count="2"/);
assert.match(html,/disabled/);assert.match(html,/data-create-in="one"/);
html=renderExtraBoardColumnHtml(snapshot);assert.doesNotMatch(html,/<article|class="empty"/);
cards=[];html=renderBoardColumnHtml(column,0,snapshot);assert.doesNotMatch(html,/class="empty"/);
assert.match(html,/disabled/,'last column deletion remains blocked');
html=renderBoardColumnHtml({id:'ready',label:'ready'},0,{columns:[column,{id:'ready'}]});
assert.match(html,/disabled data-system-column="ready"/);
html=renderExtraBoardColumnHtml(snapshot);assert.equal((html.match(/class="empty"/g)||[]).length,1);
"""
        )

    def test_actual_card_renderer_does_not_depend_on_root_position(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        after = SOURCE.with_name("app_main_after_printing.js").read_text(encoding="utf-8")
        definitions = section(
            source, "    function cardTimerState(", "    function timerVisualState("
        )
        definitions += section(
            after, "    function buildCardHeadingHtml(", "    function refreshVehiclePanel("
        )
        self.run_node(
            """
const assert=require('node:assert/strict');Date.now=()=>0;const CARD_TAG_LIMIT=3;
function finiteNumber(value){return Number(value)||0;}function finiteNonNegativeNumber(value){return Math.max(0,Number(value)||0);}
function escapeHtml(value){return String(value);}function stripDescriptionFormatting(value){return value;}
function normalizeDraftTags(tags){return tags||[];}function extraBoardColumnPreferences(){return {filter:{tag_label:'x',tag_color:'green'}};}
"""
            + definitions
            + """
const card={id:'one',position:1,column:'inbox',title:'Title',vehicle:'Car',description_preview:'Text',timer_state:'running',deadline_timestamp:'1970-01-01T01:00:00Z',is_unread:true,tag_items:[{label:'x',color:'green'}]};
for(const virtual of [false,true])assert.equal(renderBoardCardHtml(card,{virtual}),renderBoardCardHtml({...card,position:9},{virtual}));
assert.notEqual(renderBoardCardHtml(card),renderBoardCardHtml({...card,title:'Changed'}));
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
