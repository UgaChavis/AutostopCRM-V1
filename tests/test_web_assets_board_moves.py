from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.web_app_assets.module_assets import read_board_source


def section(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset)]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class BoardMoveRuntimeTests(unittest.TestCase):
    def test_full_snapshot_started_before_move_does_not_restore_old_order(self) -> None:
        source = read_board_source("app_main_before_printing.js")
        code = section(
            source,
            "    async function refreshSnapshot(",
            "    async function refreshSnapshotRevision(",
        )
        script = (
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:1,boardMutationGeneration:0,snapshot:{cards:['old']}};
const els={statusLine:{dataset:{}},archiveModal:{classList:{contains:()=>false}},gptWallModal:{classList:{contains:()=>false}}};
let reply;const rendered=[];
function perfMeasureAsync(name,work){return work();}
function api(){return new Promise(resolve=>reply=resolve);}
function applyCardSeenSuppressionsToSnapshot(data){return data;}
function applyBoardScalePreference(){}
function renderBoard(){rendered.push(state.snapshot);}
function primeBoardViewport(){}
function updateSnapshotStatusLine(){}
function setStatus(){}
"""
            + code
            + """
(async()=>{
  const old=refreshSnapshot();
  state.boardMutationGeneration+=2;state.snapshot={cards:['moved']};
  reply({cards:['old'],meta:{revision:'old'}});await old;
  assert.deepEqual(state.snapshot.cards,['moved']);assert.deepEqual(rendered,[]);
  const fresh=refreshSnapshot();reply({cards:['new'],meta:{revision:'new'}});await fresh;
  assert.deepEqual(state.snapshot.cards,['new']);assert.equal(rendered.length,1);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        )
        result = subprocess.run(
            ["node"],
            input=script,
            encoding="utf-8",
            text=True,
            capture_output=True,
            timeout=15,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def run_js(self, body: str) -> None:
        source = read_board_source("app_main_before_printing.js")
        functions = section(
            source, "    async function moveCard(", "    async function moveColumn("
        )
        result = subprocess.run(
            ["node"],
            input=SETUP + functions + body,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=15,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_drop_end_has_explicit_wire_position(self) -> None:
        self.run_js("""
(async()=>{
  const pending=moveCard('A','target',''); await tick();
  assert.equal(calls[0].body.placement,'end');
  calls[0].resolve({affected_columns:[],card:{id:'A'}}); await pending;
})().catch(e=>{console.error(e);process.exitCode=1});
""")

    def test_fast_moves_preserve_dispatch_order_and_viewer_ownership(self) -> None:
        self.run_js("""
(async()=>{
  const first=moveCard('A','one','B'); await tick();
  const second=moveCard('A','two','C'); await tick();
  assert.equal(calls.length,1,'do not race board mutations');
  calls[0].resolve({affected_columns:[],card:{id:'A',column:'one'}}); await first; await tick();
  assert.equal(calls.length,2);
  calls[1].resolve({affected_columns:[],card:{id:'A',column:'two'}}); await second;
  assert.deepEqual(applied.map(c=>c.column),['one','two']);
  const old=moveCard('A','old','B'); await tick();
  const queued=moveCard('B','old','A');
  state.viewerStateGeneration++;state.operatorSessionToken='new';
  calls[2].resolve({affected_columns:[],card:{id:'A',column:'old'}}); await old; await queued;
  assert.equal(calls.length,3);assert.equal(applied.length,2);
})().catch(e=>{console.error(e);process.exitCode=1});
""")

    def test_failed_move_refreshes_authority_without_retrying_write(self) -> None:
        self.run_js("""
(async()=>{
  const failed=moveCard('A','one','B'); await tick();
  calls[0].reject(Object.assign(new Error('conflict'),{code:'state_write_conflict'}));
  assert.equal(await failed,false);assert.equal(calls.length,1);assert.equal(refreshes,1);
  assert.equal(statuses.at(-1),'conflict');assert.deepEqual(applied,[]);
})().catch(e=>{console.error(e);process.exitCode=1});
""")


SETUP = """
const assert=require('node:assert/strict');
const state={actor:'TEST',viewerStateGeneration:1,operatorSessionToken:'old',boardMutationGeneration:0};
const calls=[],applied=[],statuses=[];let refreshes=0;
function tick(){return new Promise(r=>setImmediate(r));}
function api(path,{body}){return new Promise((resolve,reject)=>calls.push({path,body,resolve,reject}));}
function perfMeasureAsync(name,work){return work();}
function clearCardOpenSideEffectTimer(){}
function finishCardDrag(){}
function applyBoardColumnOrderDelta(card){applied.push(card);return true;}
function applyBoardColumnCardsPatch(){return false;}
function replaceSnapshotCard(card){applied.push(card);}
async function refreshSnapshot(){refreshes++;}
function setStatus(message){statuses.push(message);}
function captureViewerRequestContext(){const generation=state.viewerStateGeneration,session=state.operatorSessionToken;return {isCurrent:()=>generation===state.viewerStateGeneration&&session===state.operatorSessionToken};}
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class BoardRenderFastPathTests(unittest.TestCase):
    def run_render_trace(self, render_board: str) -> dict[str, object]:
        source = read_board_source("app_main_before_printing.js")
        sorting_and_grouping = section(
            source, "    function sortBoardCards(", "    function sortedCardsForBoardColumn("
        )
        reconcile_cards = section(
            source, "    function reconcileBoardCards(", "    function reconcileBoardSection("
        )
        setup = r"""
const assert=require('node:assert/strict');
let sectionHtmlCalls=0;
const cardHtmlCalls=[];
function makeList(initial=[]) {
  const parent={children:[],insertBefore(node,before){node.remove();const index=before?this.children.indexOf(before):this.children.length;this.children.splice(index,0,node);node.parent=this;}};
  parent.children=initial;
  initial.forEach(node=>{node.parent=parent;});
  return parent;
}
function makeCard(id,title) {
  return {dataset:{cardId:id},title,textContent:title,parent:null,remove(){if(!this.parent)return;const index=this.parent.children.indexOf(this);if(index>=0)this.parent.children.splice(index,1);this.parent=null;}};
}
function makeSection(id) {
  const list=makeList();
  return {dataset:{columnId:id},isColumn:true,list,querySelector(selector){return selector==='.column__cards'?this.list:null;}};
}
const board={children:[],querySelectorAll(){return this.children.filter(node=>node.isColumn);},querySelector(selector){return selector==='.board-add-column'||selector==='#stickyLayer'?{}:null;},insertBefore(node,before){const old=this.children.indexOf(node);if(old>=0)this.children.splice(old,1);const index=before?this.children.indexOf(before):this.children.length;this.children.splice(index,0,node);},insertAdjacentHTML(){}};
const document={createElement(){
  const template={content:{children:[],firstElementChild:null},set innerHTML(html){
    const sectionMatch=html.match(/<section[^>]*data-column-id="([^"]+)"/);
    if(sectionMatch){this.content.firstElementChild=makeSection(sectionMatch[1]);return;}
    this.content.children=Array.from(html.matchAll(/<article data-card-id="([^"]+)" data-title="([^"]*)"><\/article>/g),match=>makeCard(match[1],match[2]));
  }};
  return template;
}};
const state={mobileLite:false,boardScale:1,boardRenderedCards:new WeakMap(),boardRenderedSections:new WeakMap(),snapshot:{columns:[{id:'inbox',label:'INBOX',position:0}],cards:[{id:'a',title:'A',column:'inbox',position:0},{id:'b',title:'B',column:'inbox',position:1}]}};
const els={board};
const PARTS_STORE_COLUMN_ID='parts_store';
function perfStart(){return null;}function perfEnd(){}
function extraBoardColumnIsOpen(){return false;}function partsStoreColumnIsOpen(){return false;}
function renderStickies(){}function renderMobileShell(){}
function renderBoardColumnHtml(column){sectionHtmlCalls++;return '<section data-column-id="'+column.id+'"></section>';}
function renderBoardCardHtml(card){cardHtmlCalls.push(card.id);return '<article data-card-id="'+card.id+'" data-title="'+card.title+'"></article>';}
function reconcileBoardSection(current,next,cards){
  const currentList=current?.querySelector('.column__cards');
  const nextList=next.querySelector('.column__cards');
  if(!current||!currentList||!nextList){if(nextList)reconcileBoardCards(nextList,nextList,cards);return next;}
  reconcileBoardCards(currentList,nextList,cards);
  return current;
}
"""
        scenario = r"""
renderBoard();
const sectionNode=board.children[0];
const [originalA,originalB]=sectionNode.list.children;
state.snapshot={...state.snapshot,cards:[{id:'a',title:'A',column:'inbox',position:0},{id:'b',title:'B updated',column:'inbox',position:1}]};
renderBoard();
const [updatedA,updatedB]=sectionNode.list.children;
const afterPresentation={ids:sectionNode.list.children.map(node=>node.dataset.cardId),titles:sectionNode.list.children.map(node=>node.title),unchangedCardKept:updatedA===originalA,changedCardReplaced:updatedB!==originalB,changedCardUpdated:updatedB.title==='B updated'};
state.snapshot={...state.snapshot,cards:[{id:'a',title:'A',column:'inbox',position:1},{id:'b',title:'B updated',column:'inbox',position:0}]};
renderBoard();
const afterOrder={ids:sectionNode.list.children.map(node=>node.dataset.cardId),titles:sectionNode.list.children.map(node=>node.title),aNodeKept:sectionNode.list.children[1]===updatedA,bNodeKept:sectionNode.list.children[0]===updatedB};
const placeholder={dataset:{},textContent:'empty',parent:null,remove(){if(!this.parent)return;const index=this.parent.children.indexOf(this);if(index>=0)this.parent.children.splice(index,1);this.parent=null;}};
const emptyCurrent=makeList();
reconcileBoardCards(emptyCurrent,makeList([placeholder]),[]);
const report={afterPresentation,afterOrder,sectionHtmlCalls,cardHtmlCalls,emptyPlaceholderKept:emptyCurrent.children[0]===placeholder};
assert.deepEqual(afterPresentation,{ids:['a','b'],titles:['A','B updated'],unchangedCardKept:true,changedCardReplaced:true,changedCardUpdated:true});
assert.deepEqual(afterOrder,{ids:['b','a'],titles:['B updated','A'],aNodeKept:true,bNodeKept:true});
assert.equal(report.emptyPlaceholderKept,true);
console.log(JSON.stringify(report));
"""
        result = subprocess.run(
            ["node"],
            input=setup + sorting_and_grouping + reconcile_cards + render_board + scenario,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=15,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.strip())

    def test_same_count_card_changes_and_reorder_reconcile_without_rebuilding_sections(
        self,
    ) -> None:
        source = read_board_source("app_main_before_printing.js")
        render_board = section(source, "    function renderBoard() {", "    function setTab(")
        optimized_signature = (
            "signature: JSON.stringify([column, index, snapshot.columns.length, "
            "(cardsByColumn.get(column.id) || []).length]),"
        )
        old_signature = (
            "signature: JSON.stringify([column, index, snapshot.columns.length, "
            "cardsByColumn.get(column.id) || []]),"
        )
        self.assertIn(optimized_signature, render_board)

        # Recreate the pre-optimization signature in memory as a control. It
        # produces the same card DOM, but rebuilds the section on both updates.
        old_render_board = render_board.replace(optimized_signature, old_signature, 1)
        self.assertNotEqual(old_render_board, render_board)
        optimized = self.run_render_trace(render_board)
        previous_signature = self.run_render_trace(old_render_board)

        self.assertEqual(optimized["afterPresentation"], previous_signature["afterPresentation"])
        self.assertEqual(optimized["afterOrder"], previous_signature["afterOrder"])
        self.assertEqual(
            optimized["emptyPlaceholderKept"], previous_signature["emptyPlaceholderKept"]
        )
        self.assertEqual(optimized["emptyPlaceholderKept"], True)
        self.assertEqual(optimized["sectionHtmlCalls"], 1)
        self.assertEqual(previous_signature["sectionHtmlCalls"], 3)
