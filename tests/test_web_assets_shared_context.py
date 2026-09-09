from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.web_app_assets.module_assets import read_board_source  # noqa: E402


class _ExpandedBoardSource:
    @staticmethod
    def read_text(*, encoding: str) -> str:
        del encoding
        return read_board_source("app_main_before_printing.js")


SOURCE = _ExpandedBoardSource()


def section(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset)]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class SharedFilesContextRegressionTests(unittest.TestCase):
    def run_node(self, body: str) -> None:
        result = subprocess.run(
            ["node"],
            input=body,
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=ROOT,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shared_file_load_cannot_cross_viewer_or_session(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        definitions = section(
            source,
            "    async function loadSharedFiles(",
            "    async function openSharedFilesModal(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const pending=[],opened=[],renders=[],statuses=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',sharedFilesRequestSeq:0,
  sharedFiles:[{id:'A'}],sharedFilesStorage:{owner:'A'},sharedFilesActiveId:'A',
};
const els={sharedFilesModal:{},sharedFilesMeta:{textContent:'CURRENT META'}};
function api(path){assert.equal(path,'/api/list_shared_files');const task=deferred();pending.push(task);return task.promise;}
function sharedFileById(fileId){return state.sharedFiles.find(file=>file.id===fileId)||null;}
function renderSharedFiles(){renders.push(state.sharedFiles.map(file=>file.id));}
function maybeOpenModal(modal,openModal){if(openModal)opened.push(modal);}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + definitions
            + """
(async()=>{
  const staleSuccess=loadSharedFiles({openModal:true});
  state.viewerStateGeneration=2;
  state.sharedFiles=[{id:'B'}]; state.sharedFilesStorage={owner:'B'}; state.sharedFilesActiveId='B';
  pending[0].resolve({files:[{id:'private-A'}],storage:{owner:'private-A'}});
  assert.equal(await staleSuccess,null);
  assert.deepEqual(state.sharedFiles,[{id:'B'}]);
  assert.deepEqual(state.sharedFilesStorage,{owner:'B'});
  assert.equal(state.sharedFilesActiveId,'B');

  const staleError=loadSharedFiles({openModal:true});
  state.operatorSessionToken='session-B';
  pending[1].reject(new Error('private session error'));
  assert.equal(await staleError,null);
  assert.equal(els.sharedFilesMeta.textContent,'CURRENT META');
  assert.deepEqual(renders,[]);
  assert.deepEqual(opened,[]);
  assert.deepEqual(statuses,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_same_view_stale_load_cannot_replace_new_selection_or_request(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        definitions = section(
            source,
            "    async function loadSharedFiles(",
            "    async function openSharedFilesModal(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const pending=[],opened=[],renders=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',sharedFilesRequestSeq:0,
  sharedFiles:[{id:'A'},{id:'B'}],sharedFilesStorage:{revision:'initial'},sharedFilesActiveId:'A',
};
const els={sharedFilesModal:{},sharedFilesMeta:{textContent:''}};
function api(path){assert.equal(path,'/api/list_shared_files');const task=deferred();pending.push(task);return task.promise;}
function sharedFileById(fileId){return state.sharedFiles.find(file=>file.id===fileId)||null;}
function renderSharedFiles(){renders.push(state.sharedFiles.map(file=>file.id));}
function maybeOpenModal(modal,openModal){if(openModal)opened.push(modal);}
function setStatus(){throw new Error('stale success must not report status');}
"""
            + definitions
            + """
(async()=>{
  const stale=loadSharedFiles({openModal:true});
  state.sharedFilesActiveId='B';
  const current=loadSharedFiles({openModal:false});
  const currentRequestSeq=state.sharedFilesRequestSeq;

  pending[0].resolve({files:[{id:'A'}],storage:{revision:'stale-A'}});
  assert.equal(await stale,null);
  assert.equal(state.sharedFilesActiveId,'B','same-view stale load replaced the newer selection');
  assert.equal(state.sharedFilesRequestSeq,currentRequestSeq,'stale completion replaced current request identity');
  assert.deepEqual(state.sharedFiles,[{id:'A'},{id:'B'}]);
  assert.deepEqual(renders,[]);assert.deepEqual(opened,[]);

  pending[1].resolve({files:[{id:'B'}],storage:{revision:'fresh-B'}});
  assert.ok(await current);
  assert.equal(state.sharedFilesActiveId,'B');
  assert.deepEqual(state.sharedFiles,[{id:'B'}]);
  assert.deepEqual(state.sharedFilesStorage,{revision:'fresh-B'});
  assert.deepEqual(renders,[['B']]);assert.deepEqual(opened,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_shared_file_upload_captures_actor_and_stops_after_reset(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        context = section(
            source,
            "    function sharedFileById(",
            "    function sharedFileKindLabel(",
        )
        upload = section(
            source,
            "    async function uploadSharedFiles(",
            "    function openActiveSharedFile(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const firstBuffer=deferred(),calls=[],statuses=[];
let secondBufferReads=0;
const files=[
  {name:'one.txt',type:'text/plain',size:3,arrayBuffer(){return firstBuffer.promise;}},
  {name:'two.txt',type:'text/plain',size:3,arrayBuffer(){secondBufferReads++;return Promise.resolve(new Uint8Array([2]).buffer);}},
];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',actor:'actor-A',
  sharedFilesRequestSeq:0,sharedFilesMutationRequest:null,sharedFiles:[],
  sharedFilesActiveId:'',sharedFilesClipboardId:'',
};
const els={sharedFilesInput:{value:'old-selection'}};
const SHARED_FILE_UPLOAD_MAX_SIZE_BYTES=25*1024*1024;
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function updateSharedFilesActions(){}
function normalizeSharedFilesDropPoint(){return null;}
function sharedFilesFileNameForUpload(file){return file.name;}
function attachmentExtension(){return 'txt';}
function normalizeAttachmentMimeType(value){return value;}
function attachmentMimeTypeFromExtension(){return '';}
function sharedFilesUploadPoint(index){return {x:index*10,y:index*20};}
function arrayBufferToBase64(){return 'AQ==';}
function loadSharedFiles(){throw new Error('stale upload must not reload the list');}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + context
            + upload
            + """
(async()=>{
  const writing=uploadSharedFiles(files);
  await Promise.resolve();
  state.actor='actor-B';
  firstBuffer.resolve(new Uint8Array([1]).buffer);
  for(let index=0;index<5&&calls.length===0;index++)await Promise.resolve();
  assert.equal(calls.length,1);
  assert.equal(calls[0].path,'/api/upload_shared_file');
  assert.equal(calls[0].options.body.actor_name,'actor-A');
  assert.equal(calls[0].options.body.file_name,'one.txt');

  state.viewerStateGeneration=2;
  state.operatorSessionToken='session-B';
  state.actor='actor-B';
  state.sharedFilesMutationRequest=null;
  state.sharedFilesActiveId='B';
  els.sharedFilesInput.value='new-selection';
  calls[0].resolve({file:{id:'uploaded-A'}});
  assert.equal(await writing,null);
  assert.equal(calls.length,1,'stale upload started a second write');
  assert.equal(secondBufferReads,0,'stale upload read the second file');
  assert.equal(state.sharedFilesActiveId,'B');
  assert.equal(els.sharedFilesInput.value,'new-selection');
  assert.deepEqual(statuses,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_failed_upload_and_reconciliation_report_unknown_outcome(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        context = section(
            source,
            "    function sharedFileById(",
            "    function sharedFileKindLabel(",
        )
        loader = section(
            source,
            "    async function loadSharedFiles(",
            "    async function openSharedFilesModal(",
        )
        upload = section(
            source,
            "    async function uploadSharedFiles(",
            "    function openActiveSharedFile(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
const statuses=[],calls=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',actor:'actor-A',
  sharedFilesRequestSeq:0,sharedFilesMutationRequest:null,sharedFiles:[],
  sharedFilesStorage:null,sharedFilesActiveId:'',sharedFilesClipboardId:'',
};
const els={sharedFilesInput:{value:'selected'},sharedFilesModal:{},sharedFilesMeta:{textContent:''}};
const SHARED_FILE_UPLOAD_MAX_SIZE_BYTES=25*1024*1024;
const file={name:'uncertain.txt',type:'text/plain',size:3,arrayBuffer:async()=>new Uint8Array([1]).buffer};
async function api(path){
  calls.push(path);
  if(path==='/api/upload_shared_file')throw new Error('upload response lost');
  if(path==='/api/list_shared_files')throw new Error('list unavailable');
  throw new Error('unexpected path '+path);
}
function updateSharedFilesActions(){}
function normalizeSharedFilesDropPoint(){return null;}
function sharedFilesFileNameForUpload(value){return value.name;}
function attachmentExtension(){return 'txt';}
function normalizeAttachmentMimeType(value){return value;}
function attachmentMimeTypeFromExtension(){return '';}
function sharedFilesUploadPoint(){return {x:0,y:0};}
function arrayBufferToBase64(){return 'AQ==';}
function renderSharedFiles(){}
function maybeOpenModal(modal,openModal){assert.equal(openModal,false,'failed reconciliation opened a modal');}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + context
            + loader
            + upload
            + """
(async()=>{
  assert.equal(await uploadSharedFiles([file]),null);
  assert.deepEqual(calls,['/api/upload_shared_file','/api/list_shared_files']);
  assert.equal(state.sharedFilesMutationRequest,null);
  assert.equal(els.sharedFilesInput.value,'');
  const finalStatus=statuses.at(-1);
  assert.equal(finalStatus.isError,true);
  assert.match(finalStatus.message,/upload response lost/);
  assert.match(finalStatus.message,/ПРОВЕРЬТЕ СПИСОК ПЕРЕД ПОВТОРНОЙ ЗАГРУЗКОЙ/);
  assert.doesNotMatch(finalStatus.message,/СПИСОК ОБНОВЛЕН/);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_delayed_delete_reloads_list_without_replacing_new_selection(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        context = section(
            source,
            "    function sharedFileById(",
            "    function sharedFileKindLabel(",
        )
        loader = section(
            source,
            "    async function loadSharedFiles(",
            "    async function openSharedFilesModal(",
        )
        selection = section(
            source,
            "    function selectSharedFile(",
            "    function normalizeSharedFilesDropPoint(",
        )
        delete_file = section(
            source,
            "    async function deleteActiveSharedFile(",
            "    function beginSharedFileDrag(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[],statuses=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',actor:'actor-A',
  sharedFilesRequestSeq:0,sharedFilesMutationRequest:null,
  sharedFiles:[{id:'A',original_name:'A.txt'},{id:'B',original_name:'B.txt'}],
  sharedFilesStorage:null,sharedFilesActiveId:'A',sharedFilesClipboardId:'',
};
const els={sharedFilesModal:{},sharedFilesMeta:{textContent:''}};
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function updateSharedFilesSelection(){}
function updateSharedFilesActions(){}
function renderSharedFiles(){}
function maybeOpenModal(){}
function setStatus(message,isError){statuses.push({message,isError});}
global.window={confirm(){return true;}};
"""
            + context
            + loader
            + selection
            + delete_file
            + """
(async()=>{
  const deleting=deleteActiveSharedFile();
  assert.equal(calls.length,1);
  assert.equal(calls[0].path,'/api/delete_shared_file');
  assert.equal(calls[0].options.body.file_id,'A');
  assert.equal(calls[0].options.body.actor_name,'actor-A');

  selectSharedFile('B');
  calls[0].resolve({deleted:true});
  for(let index=0;index<5&&calls.length<2;index++)await Promise.resolve();
  assert.equal(calls.length,2,'successful delete did not reconcile the file list');
  assert.equal(calls[1].path,'/api/list_shared_files');
  calls[1].resolve({files:[{id:'B',original_name:'B.txt'}],storage:{used_bytes:1}});
  await deleting;

  assert.deepEqual(state.sharedFiles,[{id:'B',original_name:'B.txt'}]);
  assert.equal(state.sharedFilesActiveId,'B');
  assert.equal(sharedFileById('A'),null);
  assert.equal(sharedFileById('B').original_name,'B.txt');
  assert.equal(state.sharedFilesMutationRequest,null);
  assert.deepEqual(statuses,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_stale_mobile_more_finally_cannot_clear_new_viewer_loading(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        loader = section(
            source,
            "    async function loadMobileMoreModules(",
            "    function renderMobileMore(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const pending=[],statuses=[];
let moduleRenders=0,archiveRenders=0,fileRenders=0;
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',mobileMoreLoading:false,
  mobileMoreLoaded:false,mobileMoreRequest:null,mobileMoreError:'',
};
function task(label){const item=deferred();pending.push({label,...item});return item.promise;}
function loadClients(){return task('clients');}
function loadArchive(){return task('archive');}
function loadSharedFiles(){return task('files');}
function operatorCanViewEmployees(){return false;}
function loadEmployeesReference(){throw new Error('employees should not load');}
function renderMobileMoreModules(){moduleRenders++;}
function renderMobileArchivePanel(){archiveRenders++;}
function renderMobileSharedFilesPanel(){fileRenders++;}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + loader
            + """
(async()=>{
  const stale=loadMobileMoreModules();
  assert.equal(pending.length,3);

  state.viewerStateGeneration=2;
  state.operatorSessionToken='session-B';
  state.mobileMoreRequest=null;
  state.mobileMoreLoading=false;
  state.mobileMoreLoaded=false;
  const current=loadMobileMoreModules();
  assert.equal(pending.length,6);
  const currentRequest=state.mobileMoreRequest;
  const renderCountBeforeStaleSettles=moduleRenders;

  pending.slice(0,3).forEach(item=>item.resolve(item.label));
  assert.equal(await stale,null);
  assert.equal(state.mobileMoreLoaded,false,'stale response marked replacement viewer loaded');
  assert.equal(state.mobileMoreLoading,true,'stale finally cleared replacement loading');
  assert.equal(state.mobileMoreRequest,currentRequest,'stale finally cleared replacement request');
  assert.equal(moduleRenders,renderCountBeforeStaleSettles,'stale finally rendered replacement state');
  assert.equal(archiveRenders,0);
  assert.equal(fileRenders,0);

  pending.slice(3).forEach(item=>item.resolve(item.label));
  const results=await current;
  assert.equal(results.length,3);
  assert.equal(state.mobileMoreLoaded,true);
  assert.equal(state.mobileMoreLoading,false);
  assert.equal(state.mobileMoreRequest,null);
  assert.equal(statuses.length,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )


if __name__ == "__main__":
    unittest.main()
