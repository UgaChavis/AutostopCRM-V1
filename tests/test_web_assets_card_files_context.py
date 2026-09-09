from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/minimal_kanban/web_app_assets/source"


def functions(file_name: str, *names: str) -> str:
    source = (SOURCE / file_name).read_text(encoding="utf-8")
    chunks: list[str] = []
    for name in names:
        match = re.search(rf"^    (?:async )?function {name}\(.*?^    }}", source, re.M | re.S)
        if match is None:
            raise AssertionError(f"Missing runtime function {file_name}:{name}")
        chunks.append(match.group())
    return "\n".join(chunks)


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class CardFilesContextRegressionTests(unittest.TestCase):
    def run_node(self, definitions: str, body: str) -> None:
        script = (
            """
const watchdog=setTimeout(()=>{throw new Error('scenario did not complete');},2000);
(async()=>{
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
async function spinUntil(predicate){for(let index=0;index<20&&!predicate();index++)await Promise.resolve();assert.ok(predicate());}
const calls=[],statuses=[],cached=[],rendered=[];
let snapshotRefreshes=0;
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',actor:'actor-A',
  editingId:'A',activeCard:{id:'A',attachments:[]},activeCardIsFull:true,
  cardEditingGeneration:1,cardHydrationSeq:1,cardFilesMutationRequest:null,
  cardSaveRequest:null,cardSaveInFlight:false,cardSavePromise:null,cardCloseAfterSave:false,
  cardDescriptionLoading:false,cardJournalLoadedFor:'loaded',unreadSeenDeferredTimers:new Map(),
};
const els={
  fileInput:{value:'selection-A',disabled:false},
  fileDropzone:{classList:{remove(){},toggle(){}},setAttribute(){},dataset:{},textContent:''},
  fileList:{querySelectorAll(){return[];}},saveCardButton:{disabled:false},uploadButton:{disabled:false},
  cardModal:{classList:{contains(){return false;}}},
};
let api=async()=>{throw new Error('api stub not configured');};
function syncCardFilesMutationState(){}
function captureCardEditingContext(){
  const viewer=state.viewerStateGeneration,session=state.operatorSessionToken;
  const hydration=state.cardHydrationSeq,editing=state.cardEditingGeneration||0;
  return()=>state.viewerStateGeneration===viewer&&state.operatorSessionToken===session
    &&state.cardHydrationSeq===hydration&&(state.cardEditingGeneration||0)===editing;
}
function requireSavedCardForFiles(){return Boolean(state.editingId);}
function normalizeUploadableAttachmentFile(file){return file;}
function arrayBufferToBase64(){return 'AQ==';}
function normalizeAttachmentMimeType(value){return value;}
function attachmentMimeTypeFromExtension(){return '';}
function attachmentExtension(){return 'txt';}
function cacheFullCard(card){cached.push(card);}
function renderFiles(card){rendered.push(card);}
async function refreshSnapshot(){snapshotRefreshes++;}
function setStatus(message,isError){statuses.push({message,isError});return false;}
const CARD_TITLE_REQUIRED_MESSAGE='TITLE REQUIRED';
"""
            + definitions
            + "\n"
            + body
            + "\n})().then(()=>clearTimeout(watchdog),error=>{clearTimeout(watchdog);console.error(error);process.exitCode=1;});"
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

    def card_file_functions(self, *names: str) -> str:
        return functions(
            "app_main_before_printing.js",
            "beginCardFilesMutation",
            "finishCardFilesMutation",
            *names,
        )

    def test_multi_upload_captures_actor_and_card_then_stops_after_switch(self) -> None:
        definitions = self.card_file_functions("refreshActiveCardFiles", "uploadProvidedFiles")
        self.run_node(
            definitions,
            """
const firstBuffer=deferred(),firstWrite=deferred();let secondFileReads=0;
const files=[
  {name:'one.txt',type:'text/plain',arrayBuffer(){return firstBuffer.promise;}},
  {name:'two.txt',type:'text/plain',arrayBuffer(){secondFileReads++;return Promise.resolve(new Uint8Array([2]).buffer);}},
];
api=(path,options)=>{calls.push({path,options});if(path==='/api/add_card_attachment')return firstWrite.promise;throw new Error('stale upload must not read back');};

const uploading=uploadProvidedFiles(files);
assert.ok(state.cardFilesMutationRequest);
state.actor='actor-B';
firstBuffer.resolve(new Uint8Array([1]).buffer);
await spinUntil(()=>calls.length===1);
assert.equal(calls[0].path,'/api/add_card_attachment');
assert.equal(calls[0].options.body.card_id,'A');
assert.equal(calls[0].options.body.actor_name,'actor-A');
assert.equal(calls[0].options.body.file_name,'one.txt');

state.viewerStateGeneration=2;state.operatorSessionToken='session-B';state.actor='actor-B';
state.cardEditingGeneration++;state.cardHydrationSeq++;state.cardFilesMutationRequest=null;
state.editingId='B';state.activeCard={id:'B',attachments:[]};els.fileInput.value='selection-B';
firstWrite.resolve({card:{id:'A'}});
assert.equal(await uploading,null);
assert.equal(calls.length,1,'stale multi-upload continued to a second write or readback');
assert.equal(secondFileReads,0,'stale multi-upload read file 2');
assert.equal(state.editingId,'B');assert.equal(state.activeCard.id,'B');
assert.equal(els.fileInput.value,'selection-B');
assert.deepEqual(statuses,[]);assert.equal(snapshotRefreshes,0);
""",
        )

    def test_same_id_aba_invalidates_delayed_upload_and_remove(self) -> None:
        definitions = self.card_file_functions(
            "refreshActiveCardFiles", "removeActiveCardAttachment", "uploadProvidedFiles"
        )
        self.run_node(
            definitions,
            """
let pendingWrite=deferred();
api=(path,options)=>{
  calls.push({path,options});
  if(path.startsWith('/api/get_card?'))throw new Error('stale mutation performed a readback');
  return pendingWrite.promise;
};
const file={name:'old.txt',type:'text/plain',arrayBuffer:async()=>new Uint8Array([1]).buffer};

const oldUpload=uploadProvidedFiles([file]);
await spinUntil(()=>calls.length===1);
assert.equal(calls[0].path,'/api/add_card_attachment');
state.cardEditingGeneration++;state.cardHydrationSeq++;state.cardFilesMutationRequest=null;
state.editingId='A';state.activeCard={id:'A',revision:'reopened-upload',attachments:[]};
els.fileInput.value='reopened-selection';
pendingWrite.resolve({card:{id:'A',revision:'old-upload'}});
assert.equal(await oldUpload,null);
assert.equal(state.activeCard.revision,'reopened-upload');
assert.equal(els.fileInput.value,'reopened-selection');
assert.equal(calls.length,1);

pendingWrite=deferred();
state.activeCard={id:'A',revision:'before-remove',attachments:[{id:'attachment-A'}]};
const oldRemove=removeActiveCardAttachment('attachment-A');
await spinUntil(()=>calls.length===2);
assert.equal(calls[1].path,'/api/remove_card_attachment');
state.cardEditingGeneration++;state.cardHydrationSeq++;state.cardFilesMutationRequest=null;
state.editingId='A';state.activeCard={id:'A',revision:'reopened-remove',attachments:[{id:'current'}]};
pendingWrite.resolve({card:{id:'A',revision:'old-remove'}});
assert.equal(await oldRemove,null);
assert.equal(state.activeCard.revision,'reopened-remove');
assert.deepEqual(state.activeCard.attachments,[{id:'current'}]);
assert.equal(calls.length,2,'same-id stale mutation performed a readback');
assert.deepEqual(statuses,[]);assert.equal(snapshotRefreshes,0);
""",
        )

    def test_ambiguous_remove_reads_back_once_and_never_repeats_write(self) -> None:
        definitions = self.card_file_functions(
            "findCardAttachment", "refreshActiveCardFiles", "removeActiveCardAttachment"
        )
        self.run_node(
            definitions,
            """
let mode='confirmed';
api=async(path,options)=>{
  calls.push({path,options});
  if(path==='/api/remove_card_attachment')throw new Error('remove response lost');
  if(path.startsWith('/api/get_card?')){
    if(mode==='confirmed')return {card:{id:'A',attachments:[]}};
    throw new Error('readback unavailable');
  }
  throw new Error('unexpected path '+path);
};

state.activeCard={id:'A',attachments:[{id:'gone'}]};
const confirmed=await removeActiveCardAttachment('gone');
assert.deepEqual(confirmed,{id:'A',attachments:[]});
assert.equal(calls.filter(call=>call.path==='/api/remove_card_attachment').length,1);
assert.equal(calls.filter(call=>call.path.startsWith('/api/get_card?')).length,1);
assert.equal(statuses.at(-1).isError,false);
assert.match(statuses.at(-1).message,/ПОДТВЕРЖДЕН ПОВТОРНЫМ ЧТЕНИЕМ/);
assert.equal(state.cardFilesMutationRequest,null);

mode='unavailable';statuses.length=0;
state.activeCard={id:'A',attachments:[{id:'unknown'}]};
const unknown=await removeActiveCardAttachment('unknown');
assert.equal(unknown,null);
assert.equal(calls.filter(call=>call.path==='/api/remove_card_attachment').length,2,'ambiguous remove repeated the write');
assert.equal(calls.filter(call=>call.path.startsWith('/api/get_card?')).length,2,'ambiguous remove used more than one readback');
assert.equal(statuses.length,1);assert.equal(statuses[0].isError,true);
assert.match(statuses[0].message,/РЕЗУЛЬТАТ УДАЛЕНИЯ НЕ ОПРЕДЕЛЕН/);
assert.match(statuses[0].message,/НЕ ПОВТОРЯЙТЕ ОПЕРАЦИЮ/);
assert.doesNotMatch(statuses[0].message,/ФАЙЛ УДАЛЕН/);
assert.equal(state.cardFilesMutationRequest,null);
""",
        )

    def test_save_and_file_mutations_are_mutually_exclusive(self) -> None:
        definitions = self.card_file_functions(
            "refreshActiveCardFiles", "removeActiveCardAttachment", "uploadProvidedFiles"
        ) + functions("app_main_after_printing.js", "saveCard")
        self.run_node(
            definitions,
            """
const uploadBuffer=deferred();
const uploadFile={name:'upload.txt',type:'text/plain',arrayBuffer(){return uploadBuffer.promise;}};
const blockedFile={name:'blocked.txt',type:'text/plain',arrayBuffer(){throw new Error('blocked upload read its file');}};
let payloadReads=0;const saveResult=deferred();
function currentCardPayload(){payloadReads++;return {title:'Card A'};}
function clearCardOpenSideEffectTimer(){}
function cancelDeferredCardSeen(){return false;}
function syncCardSaveDirtyState(){}
function perfMeasureAsync(_name,callback){return callback();}
function persistCardPayload(){return saveResult.promise;}
function applySavedCardLocalPatch(){}
function rememberCardModalCleanState(){}
function closeCardModal(){}
function deferCardSeen(){}
api=async(path,options)=>{
  calls.push({path,options});
  if(path==='/api/add_card_attachment')return {file:{id:'uploaded'}};
  if(path.startsWith('/api/get_card?'))return {card:{id:'A',attachments:[{id:'uploaded'}]}};
  throw new Error('unexpected mutation '+path);
};

const uploading=uploadProvidedFiles([uploadFile]);
assert.ok(state.cardFilesMutationRequest);
assert.equal(await saveCard(),false);
assert.equal(payloadReads,0,'save read payload while a file mutation was running');
assert.equal(state.cardSaveInFlight,false);

uploadBuffer.resolve(new Uint8Array([1]).buffer);
assert.equal(await uploading,1);
assert.equal(state.cardFilesMutationRequest,null);
statuses.length=0;calls.length=0;

const saving=saveCard();
await Promise.resolve();
assert.equal(state.cardSaveInFlight,true);
assert.equal(payloadReads,1);
assert.equal(await uploadProvidedFiles([blockedFile]),null);
assert.equal(await removeActiveCardAttachment('uploaded'),null);
assert.equal(state.cardFilesMutationRequest,null);
assert.deepEqual(calls,[],'file mutation wrote while card save was running');
assert.equal(statuses.length,2);assert.ok(statuses.every(item=>item.isError));

saveResult.resolve({card:{id:'A'},meta:{changed:true}});
assert.equal(await saving,true);
assert.equal(state.cardSaveInFlight,false);
""",
        )


if __name__ == "__main__":
    unittest.main()
