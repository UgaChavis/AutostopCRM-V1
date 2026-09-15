from __future__ import annotations

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
