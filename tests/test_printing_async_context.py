from __future__ import annotations

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.printing.web_async_context import PRINTING_ASYNC_CONTEXT_SCRIPT
from minimal_kanban.printing.web_module import PRINTING_WEB_MODULE_SCRIPT


def function_source(name: str) -> str:
    match = re.search(
        rf"^    (?:async )?function {re.escape(name)}\(.*?^    }}\n",
        PRINTING_WEB_MODULE_SCRIPT,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"Missing emitted printing function: {name}")
    return match.group()


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class PrintingAsyncContextTests(unittest.TestCase):
    def run_javascript(self, script: str) -> None:
        result = subprocess.run(
            ["node"], input=script, text=True, capture_output=True, cwd=ROOT, timeout=15
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_operation_request_tokens_and_editor_contexts_are_independent(self) -> None:
        self.run_javascript(
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:0,editingId:'A'};
const repairOrderPrintState={mode:'card',selectedDocumentIds:['invoice'],activeDocumentId:'invoice'};
let writes=0;async function api(){writes++;return {ok:true};}
"""
            + PRINTING_ASYNC_CONTEXT_SCRIPT
            + """
(async()=>{
  const first=capturePrintOperation('preview'),second=capturePrintOperation('preview');
  assert.equal(first.current(),false);assert.equal(second.current(),true);
  await assert.rejects(first.wait(Promise.resolve({})),{code:'stale_print_operation'});
  const edit=capturePrintOperation('',{template:true});invalidatePrintTemplateContext();
  assert.equal(edit.current(),false);assert.equal(second.current(),true);
  const selection=capturePrintOperation('',{selection:true});
  repairOrderPrintState.selectedDocumentIds=['repair_order'];assert.equal(selection.current(),false);
  assert.deepEqual(await second.request('/synthetic'),{ok:true});assert.equal(writes,1);
  invalidatePrintWorkspaceContext();assert.equal(second.current(),false);
  assert.throws(()=>second.request('/never-write'),{code:'stale_print_operation'});
  const viewer=capturePrintOperation();state.viewerStateGeneration++;
  assert.equal(viewer.current(),false);
  const card=capturePrintOperation();state.editingId='B';assert.equal(card.current(),false);
  assert.equal(writes,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_template_preview_last_request_and_upload_editor_transition(self) -> None:
        self.run_javascript(
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:0,editingId:'A'};
const repairOrderPrintState={mode:'card',templateEditor:{documentType:'invoice',templateId:'old'}};
const printEls={templatePreviewMeta:{},templatePreviewFrame:{},templateName:{}};
const requests=[],loaded=[];let draft='old';
function api(){return new Promise((resolve,reject)=>requests.push({resolve,reject}));}
function readPrintTemplateEditorContent(){return draft;}
function repairOrderPrintRequestPayload(data){return data;}
function buildPrintTemplateEditorFallbackHtml(title,message){return message;}
function printFiniteNumber(value){return Number(value);}
function setStatus(){}
function loadPrintTemplateEditorContent(text){loaded.push(text);}
class HTMLInputElement{}
const PRINT_TEMPLATE_UPLOAD_MAX_SIZE_BYTES=262144;
"""
            + PRINTING_ASYNC_CONTEXT_SCRIPT
            + function_source("previewCurrentPrintTemplate")
            + function_source("handlePrintTemplateUploadChange")
            + """
(async()=>{
  const first=previewCurrentPrintTemplate();draft='new';const second=previewCurrentPrintTemplate();
  requests[1].resolve({documents:[{pages:[{html:'NEW'}]}]});await second;
  requests[0].reject(new Error('obsolete failure'));await first;
  assert.equal(printEls.templatePreviewFrame.srcdoc,'NEW');
  const old=previewCurrentPrintTemplate();invalidatePrintTemplateContext();
  printEls.templatePreviewFrame.srcdoc='OTHER TEMPLATE';
  requests[2].resolve({documents:[{pages:[{html:'OLD'}]}]});await old;
  assert.equal(printEls.templatePreviewFrame.srcdoc,'OTHER TEMPLATE');
  let resolveFile;const input=new HTMLInputElement();input.value='old';
  input.files=[{size:10,name:'old.html',text:()=>new Promise(resolve=>resolveFile=resolve)}];
  const upload=handlePrintTemplateUploadChange({target:input});
  invalidatePrintTemplateContext();input.value='new-file';printEls.templateName.value='NEW';
  resolveFile('OBSOLETE');await upload;
  assert.deepEqual(loaded,[]);assert.equal(input.value,'new-file');assert.equal(printEls.templateName.value,'NEW');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_completion_session_rejects_same_card_counter_reuse_after_reset(self) -> None:
        self.run_javascript(
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:0,editingId:'A'};
const repairOrderPrintState={mode:'card',completionAct:{generation:1,cardId:'A'}};
const printEls={completionActModal:{classList:{contains:()=>true}}};
function completionActActiveCardId(){return state.editingId;}
function invalidateCompletionActEditorSession(){throw new Error('unexpected card change');}
"""
            + PRINTING_ASYNC_CONTEXT_SCRIPT
            + function_source("completionActEditorSessionSnapshot")
            + function_source("completionActEditorSessionIsCurrent")
            + """
const session=completionActEditorSessionSnapshot();assert.equal(completionActEditorSessionIsCurrent(session),true);
state.viewerStateGeneration++;repairOrderPrintState.completionAct={generation:1,cardId:'A'};
assert.equal(completionActEditorSessionIsCurrent(session),false);
const current=completionActEditorSessionSnapshot();invalidatePrintWorkspaceContext();
assert.equal(completionActEditorSessionIsCurrent(current),false);
"""
        )

    def test_pending_iframe_is_removed_without_print_on_viewer_reset(self) -> None:
        self.run_javascript(
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:0,editingId:'A'},repairOrderPrintState={mode:'card'};
const timers=new Map(),frames=[];let seq=0,prints=0,loadOnClose=true;
const window={setTimeout(fn){timers.set(++seq,fn);return seq;},clearTimeout(id){timers.delete(id);}};
const document={body:{appendChild(){}},createElement(){
  const frame={style:{},setAttribute(){},remove(){this.removed=true;}};
  frame.contentWindow={focus(){},print(){prints++;frame.contentWindow.onafterprint();}};
  frame.contentDocument={open(){},write(){},close(){if(loadOnClose)frame.onload();}};
  frames.push(frame);return frame;
}};
function fire(){const [id,fn]=timers.entries().next().value;timers.delete(id);fn();}
"""
            + PRINTING_ASYNC_CONTEXT_SCRIPT
            + function_source("runBrowserPrintHtml")
            + """
(async()=>{
  for(const waitingForLoad of [false,true]){
    loadOnClose=!waitingForLoad;
    const pending=runBrowserPrintHtml('<p>Synthetic</p>');
    const rejected=assert.rejects(pending,{code:'stale_print_operation'});
    state.viewerStateGeneration++;resetPrintAsyncContext();await rejected;
    assert.equal(frames.at(-1).removed,true);assert.equal(timers.size,0);assert.equal(prints,0);
  }
  loadOnClose=true;const normal=runBrowserPrintHtml('<p>Current</p>');fire();await normal;
  assert.equal(prints,1);while(timers.size)fire();assert.equal(frames.at(-1).removed,true);
  assert.equal(printFrameCancellations.size,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_obsolete_print_preview_never_falls_back_or_clears_new_job(self) -> None:
        self.run_javascript(
            """
const assert = require('node:assert/strict');
const state = {viewerStateGeneration:0,editingId:'A'};
const repairOrderPrintState = {workspace:{},mode:'card',templateEditor:{},isPrintRunning:false,previewByDocument:{repair_order:{}}};
const printEls = {printButton:{disabled:false},printerSelect:{value:'Synthetic printer'}};
let pending, settle, writes=0;
const statuses=[];
function completionActActiveCardId(){return state.editingId;}
function cancelPendingRepairOrderPrintPreview(){}
function refreshRepairOrderPrintPreview(){return pending;}
function repairOrderPrintSelectedIds(){return ['repair_order'];}
function runRepairOrderBrowserPrint(){return Promise.resolve();}
function repairOrderPrintRequestPayload(){return {card_id:state.editingId};}
async function api(){writes++;return {};}
function setStatus(message){statuses.push(message);}
function syncRepairOrderPrintPrinterState(){printEls.printButton.disabled=repairOrderPrintState.isPrintRunning;}
"""
            + PRINTING_ASYNC_CONTEXT_SCRIPT
            + function_source("runRepairOrderPrintJob")
            + """
(async()=>{
  for(const reject of [true,false]){
    pending=new Promise((resolve,fail)=>{settle=()=>reject?fail(new Error('old preview failed')):resolve({});});
    repairOrderPrintState.isPrintRunning=false;
    const old=runRepairOrderPrintJob();
    state.viewerStateGeneration++;state.editingId='B';
    repairOrderPrintState.workspace={};repairOrderPrintState.isPrintRunning=true;
    printEls.printButton.disabled=true;
    settle();await old;
    assert.equal(writes,0,'old operation must not send a new print request');
    assert.equal(repairOrderPrintState.isPrintRunning,true,'new job retains its flag');
    assert.equal(printEls.printButton.disabled,true);
    assert.deepEqual(statuses,[]);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_print_job_releases_only_its_flag_and_preserves_current_fallback(self) -> None:
        self.run_javascript(
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:0,editingId:'A'};
const repairOrderPrintState={workspace:{},mode:'card',selectedDocumentIds:['invoice'],activeDocumentId:'invoice',isPrintRunning:false,previewByDocument:{invoice:{}}};
const printEls={printerSelect:{value:'Synthetic'},printButton:{disabled:false}};
let pending,settle,writes=0,browserFailure=false;const statuses=[];
function cancelPendingRepairOrderPrintPreview(){}
function refreshRepairOrderPrintPreview(){return pending;}
function repairOrderPrintSelectedIds(){return ['invoice'];}
function runRepairOrderBrowserPrint(){return browserFailure?Promise.reject(new Error('browser blocked')):Promise.resolve();}
function repairOrderPrintRequestPayload(){return {card_id:state.editingId};}
async function api(){writes++;return {printer_name:'Synthetic'};}
function setStatus(message){statuses.push(message);}
function syncRepairOrderPrintPrinterState(){printEls.printButton.disabled=repairOrderPrintState.isPrintRunning;}
"""
            + PRINTING_ASYNC_CONTEXT_SCRIPT
            + function_source("runRepairOrderPrintJob")
            + """
(async()=>{
  for(const change of [()=>state.editingId+='B',()=>invalidatePrintWorkspaceContext(),()=>repairOrderPrintState.activeDocumentId+='X']){
    pending=new Promise(resolve=>settle=resolve);const old=runRepairOrderPrintJob();
    change();settle({});await old;
    assert.equal(writes,0);assert.equal(repairOrderPrintState.isPrintRunning,false);assert.deepEqual(statuses,[]);
  }
  pending=Promise.resolve({});browserFailure=true;
  await runRepairOrderPrintJob();assert.equal(writes,1);assert.equal(repairOrderPrintState.isPrintRunning,false);
  assert.equal(printEls.printButton.disabled,false);assert.equal(statuses.length,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_workspace_card_preparation_and_inspection_failure_keep_current_editor(self) -> None:
        self.run_javascript(
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:0,editingId:''};
const repairOrderPrintState={workspace:null,mode:'card'};
const printEls={modal:{classList:{add(){}}},inspectionSheetAutofillButton:{disabled:false},inspectionSheetFooterMeta:{textContent:''}};
const requests=[],applied=[],statuses=[];let prepare;
function requireRepairOrderCardId(){return prepare;}
function completionActActiveCardId(){return state.editingId;}
function syncRepairOrderPrintMode(){}
function readRepairOrderFromForm(){return {};}
function api(path,options){return new Promise((resolve,reject)=>requests.push({path,options,resolve,reject}));}
function applyRepairOrderPrintWorkspace(data){applied.push(data);repairOrderPrintState.workspace=data;}
function refreshRepairOrderPrintPreview(){return Promise.resolve({});}
function repairOrderPrintRequestPayload(data){return data;}
function applyInspectionSheetFormToInputs(data){applied.push(data);}
function blankInspectionSheetForm(){return {};}
function setStatus(message){statuses.push(message);}
"""
            + PRINTING_ASYNC_CONTEXT_SCRIPT
            + function_source("loadRepairOrderPrintWorkspace")
            + function_source("autofillInspectionSheetFormDraft")
            + """
(async()=>{
  let ready;prepare=new Promise(resolve=>ready=resolve);
  const loading=loadRepairOrderPrintWorkspace();
  state.editingId='created';ready('created');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,1);assert.equal(requests[0].options.body.card_id,'created');
  requests[0].resolve({card_id:'created'});await loading;
  assert.deepEqual(applied,[{card_id:'created'}]);
  const old=autofillInspectionSheetFormDraft();
  invalidatePrintInspectionContext();
  printEls.inspectionSheetFooterMeta.textContent='NEW EDITOR';printEls.inspectionSheetAutofillButton.disabled=true;
  requests[1].reject(new Error('old failed'));await old;
  assert.equal(printEls.inspectionSheetFooterMeta.textContent,'NEW EDITOR');
  assert.equal(printEls.inspectionSheetAutofillButton.disabled,true);assert.deepEqual(statuses,[]);
  prepare=new Promise(resolve=>ready=resolve);const obsolete=loadRepairOrderPrintWorkspace();
  const rejected=assert.rejects(obsolete,{code:'stale_print_operation'});
  state.viewerStateGeneration++;state.editingId='other';ready('created');await rejected;
  assert.equal(requests.length,2,'stale preparation must not start another request');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_prepared_workspace_read_is_applied_only_for_its_current_card(self) -> None:
        self.run_javascript(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const state={viewerStateGeneration:0,editingId:'A',activeCard:{id:'A'}};
const repairOrderPrintState={workspace:null,mode:'card',selectedDocumentIds:[],activeDocumentId:''};
const opened=[],applied=[],statuses=[],requests=[];let previews=0;
const printEls={modal:{classList:{add:name=>opened.push(name)}}};
function requireRepairOrderCardId(){return state.editingId;}
function completionActActiveCardId(){return state.editingId;}
function syncRepairOrderPrintMode(){}
function readRepairOrderFromForm(){throw new Error('prepared path reread the form');}
function api(path,options){requests.push({path,options});return Promise.resolve({unexpected:true});}
function applyRepairOrderPrintWorkspace(data){applied.push(data);repairOrderPrintState.workspace=data;}
function refreshRepairOrderPrintPreview(){previews++;return Promise.resolve({});}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + PRINTING_ASYNC_CONTEXT_SCRIPT
            + function_source("loadRepairOrderPrintWorkspace")
            + function_source("openRepairOrderPrintWorkspace")
            + """
(async()=>{
  let task=deferred(),current=true;
  const prepared={cardId:'A',isCurrent:()=>current,promise:task.promise};
  const opening=openRepairOrderPrintWorkspace(prepared);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,0);assert.equal(previews,0,'preview must wait for workspace data');
  task.resolve({card_id:'A',documents:[{id:'repair_order'}]});await opening;
  assert.equal(applied.length,1);assert.deepEqual(opened,['is-open']);assert.equal(previews,1);

  task=deferred();current=true;
  const stale={cardId:'A',isCurrent:()=>current,promise:task.promise};
  const obsolete=openRepairOrderPrintWorkspace(stale);await new Promise(resolve=>setImmediate(resolve));
  current=false;task.resolve({card_id:'A',private_marker:'stale'});await obsolete;
  assert.equal(applied.length,1);assert.deepEqual(opened,['is-open']);assert.equal(previews,1);
  assert.equal(statuses.length,0);

  task=deferred();current=true;
  const failed={cardId:'A',isCurrent:()=>current,promise:task.promise};
  const rejected=openRepairOrderPrintWorkspace(failed);await new Promise(resolve=>setImmediate(resolve));
  task.reject(new Error('workspace failed'));await rejected;
  assert.equal(requests.length,0,'prepared error retried the workspace request');
  assert.equal(applied.length,1);assert.equal(previews,1);assert.equal(statuses.length,1);
  assert.match(statuses[0].message,/workspace failed/);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_all_emitted_async_printing_requests_use_operation_guards(self) -> None:
        names = re.findall(r"^    async function (\w+)\(", PRINTING_WEB_MODULE_SCRIPT, re.MULTILINE)
        self.assertEqual(len(names), 23)
        for name in names:
            with self.subTest(name=name):
                source = function_source(name)
                self.assertIn("capturePrintOperation(", source)
                self.assertNotIn("await api(", source)
                self.assertNotRegex(source, r"await (?!operation\.)(?:[\w.]+)\(")
