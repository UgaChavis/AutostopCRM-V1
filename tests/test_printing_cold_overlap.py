from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class PrintingColdOverlapTests(unittest.TestCase):
    def run_node(self, body: str) -> None:
        loader = (ROOT / "src/minimal_kanban/web_app_assets/source/module_loader.js").read_text(
            encoding="utf-8"
        )
        script = (
            r"""
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
function fixture(){
  const requests=[],scripts=[],received=[],applied=[],statuses=[];
  const state={
    viewerStateGeneration:1,operatorSessionToken:'session-A',editingId:'card-A',
    activeCard:{id:'card-A'},cardEditingGeneration:3,cardHydrationSeq:4,
    modalStack:[],mobileView:'board',mobileMorePanelIntent:null,
  };
  const els={},window={};
  const context={
    state,els,window,console,Promise,
    BOARD_MODULE_MANIFEST:{printing:'/printing.js'},
    document:{createElement:()=>({remove(){}}),head:{appendChild:script=>scripts.push(script)}},
    api(path,options){const task=deferred();requests.push({path,options,...task});return task.promise;},
    readRepairOrderFromForm:()=>({number:'draft-A'}),
    setStatus:(message,isError)=>statuses.push({message,isError}),
    buildBoardModuleSharedContext:()=>({}),claimMobileMorePanelIntent:()=>null,
  };
  require('node:vm').runInNewContext(LOADER,context);
  function finishModule(){
    context.window.registerBoardModule('printing',()=>({
      async printRepairOrderDraft(prepared){
        received.push(prepared);
        if(!prepared)return 'ordinary-unsaved-path';
        const data=await prepared.promise;
        if(!prepared.isCurrent())return false;
        applied.push(data);return data;
      },
    }));
    scripts.at(-1).onload();
  }
  return {context,state,requests,scripts,received,applied,statuses,finishModule,
    invoke:()=>context.invokeBoardModule('printing','printRepairOrderDraft',[])};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
"""
            + f"\nconst LOADER={loader!r};\n"
            + "(async()=>{\n"
            + body
            + "\n})().catch(error=>{console.error(error);process.exitCode=1;});\n"
        )
        result = subprocess.run(
            ["node"],
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=ROOT,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_saved_card_read_starts_before_script_and_invocation_is_deduplicated(self) -> None:
        self.run_node(
            r"""
const f=fixture(),pending=f.invoke();
assert.equal(f.scripts.length,1);assert.equal(f.requests.length,1);
assert.equal(f.requests[0].path,'/api/get_repair_order_print_workspace');
assert.equal(JSON.stringify(f.requests[0].options),JSON.stringify({method:'POST',body:{card_id:'card-A',source:'ui',repair_order:{number:'draft-A'}}}));
assert.equal(f.invoke(),pending,'double click must share one cold invocation');
assert.equal(f.requests.length,1);assert.equal(f.received.length,0);
f.finishModule();await tick();assert.equal(f.received.length,1);assert.equal(f.applied.length,0);
f.requests[0].resolve({card_id:'card-A',documents:[]});
assert.deepEqual(await pending,{card_id:'card-A',documents:[]});
assert.equal(f.applied.length,1);assert.equal(f.statuses.length,0);
"""
        )

    def test_unsaved_card_keeps_existing_module_path_without_early_read(self) -> None:
        self.run_node(
            r"""
const f=fixture();f.state.editingId='';f.state.activeCard=null;
const pending=f.invoke();assert.equal(f.requests.length,0);assert.equal(f.scripts.length,1);
f.finishModule();assert.equal(await pending,'ordinary-unsaved-path');
assert.equal(f.received.length,1);assert.equal(f.received[0],undefined);
"""
        )

    def test_viewer_session_and_exact_card_context_reject_late_results(self) -> None:
        self.run_node(
            r"""
for(const change of ['viewer','session','card','editing','hydration']){
  const f=fixture(),pending=f.invoke();
  if(change==='viewer')f.state.viewerStateGeneration++;
  if(change==='session')f.state.operatorSessionToken='session-B';
  if(change==='card'){f.state.editingId='card-B';f.state.activeCard={id:'card-B'};}
  if(change==='editing')f.state.cardEditingGeneration++;
  if(change==='hydration')f.state.cardHydrationSeq++;
  f.finishModule();f.requests[0].resolve({card_id:'card-A',private_marker:'stale'});
  assert.equal(await pending,false,change);assert.equal(f.received.length,0,change);
  assert.equal(f.applied.length,0,change);assert.equal(f.statuses.length,0,change);
}
"""
        )

    def test_same_card_reopen_uses_a_new_owned_invocation(self) -> None:
        self.run_node(
            r"""
const f=fixture(),obsolete=f.invoke();
f.state.cardEditingGeneration++;f.state.cardHydrationSeq++;
const current=f.invoke();assert.notEqual(current,obsolete);
assert.equal(f.requests.length,2,'same-card ABA was incorrectly deduplicated');
f.finishModule();f.requests[0].resolve({card_id:'card-A',marker:'obsolete'});
f.requests[1].resolve({card_id:'card-A',marker:'current'});
assert.equal(await obsolete,false);assert.equal((await current).marker,'current');
assert.equal(f.received.length,1);assert.equal(f.applied.length,1);
assert.equal(f.applied[0].marker,'current');assert.equal(f.statuses.length,0);
"""
        )

    def test_script_or_data_failure_consumes_prepared_promise(self) -> None:
        self.run_node(
            r"""
const unhandled=[];process.on('unhandledRejection',error=>unhandled.push(error));
const scriptFailure=fixture(),first=scriptFailure.invoke();
scriptFailure.scripts[0].onerror();await first;
scriptFailure.requests[0].reject(new Error('late workspace failure'));await tick();
assert.equal(unhandled.length,0);assert.equal(scriptFailure.statuses.length,1);

const dataFailure=fixture(),second=dataFailure.invoke();dataFailure.finishModule();await tick();
dataFailure.requests[0].reject(new Error('workspace failed'));await second;await tick();
assert.equal(unhandled.length,0);assert.equal(dataFailure.applied.length,0);
assert.equal(dataFailure.statuses.length,1);assert.match(dataFailure.statuses[0].message,/workspace failed/);
"""
        )


if __name__ == "__main__":
    unittest.main()
