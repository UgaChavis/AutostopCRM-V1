from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/minimal_kanban/web_app_assets/source/app_main_before_printing.js"


def section(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset)]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class MobileWorkspaceContextRegressionTests(unittest.TestCase):
    def run_node(self, body: str) -> None:
        result = subprocess.run(
            ["node"], input=body, text=True, capture_output=True, cwd=ROOT, timeout=15
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_closing_repair_order_list_invalidates_pending_modal_open(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        close_named_modal = section(
            source,
            "    function closeNamedModal(",
            "    async function loadModalData(",
        )
        self.assertIn(
            "'repair-orders': () => {\n          invalidateRepairOrdersRequests();",
            close_named_modal,
        )

    def test_mobile_card_open_responses_belong_to_exact_workspace(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        definitions = section(
            source,
            "    function closeMobileCardDetail()",
            "    async function saveMobileCardDetail()",
        )
        self.run_node(
            """
const assert = require('node:assert/strict');
function deferred() {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const pending = new Map(), statuses = [], opened = [];
const state = {
  viewerStateGeneration: 1, operatorSessionToken: 'session-A', actor: 'A',
  mobileCardContextGeneration: 0, mobileCardOpenRequest: null,
  mobileCardId: '', mobileCard: null, mobileCardTab: 'overview', mobileCardCreating: false,
  mobileCardLoading: false, mobileCardSaving: false, mobileCardFilesBusy: false,
  activeCard: null, activeCardIsFull: false, editingId: null, pendingCardClientId: '',
};
const els = {mobileCardTitleInput:{focus(){}}};
global.requestAnimationFrame = (callback) => callback();
function snapshotCardById(id) {return {id, title:'summary-'+id, updated_at:'r-'+id};}
function fetchFullCard(id) {
  if (!pending.has(id)) pending.set(id,deferred());
  return pending.get(id).promise;
}
function resetMobileCardJournal() {}
function syncMobileCardEditorBusyState() {}
function renderMobileShell() {}
function renderMobileCardDetail() {}
function cacheFullCard() {}
function recordCardOpenSideEffects(id) {opened.push(id);}
function loadMobileCardJournal() {}
function setStatus(message, isError) {statuses.push({message,isError});}
function emptyMobileCardDraft() {return {id:''};}
"""
            + definitions
            + """
(async()=>{
  const first=openMobileCardDetail('A'), second=openMobileCardDetail('B');
  pending.get('B').resolve({id:'B',title:'full-B',updated_at:'full-r-B'}); await second;
  pending.get('A').resolve({id:'A',title:'full-A',updated_at:'full-r-A'}); await first;
  assert.equal(state.mobileCardId,'B');
  assert.equal(state.mobileCard.title,'full-B');
  assert.deepEqual(opened,['B']);

  const closed=openMobileCardDetail('C'); closeMobileCardDetail();
  pending.get('C').resolve({id:'C',title:'full-C'}); await closed;
  assert.equal(state.mobileCardId,'');
  assert.equal(state.mobileCard,null);

  const loggedOut=openMobileCardDetail('D');
  state.viewerStateGeneration++; state.operatorSessionToken='session-B';
  state.mobileCardId=''; state.mobileCard=null;
  pending.get('D').resolve(null); await loggedOut;
  assert.equal(state.mobileCardId,'');
  assert.equal(state.mobileCard,null);

  state.operatorSessionToken='session-B';
  const staleError=openMobileCardDetail('E'), current=openMobileCardDetail('F');
  pending.get('E').reject(new Error('obsolete mobile card')); await staleError;
  assert.equal(state.mobileCardId,'F');
  assert.equal(state.mobileCardLoading,true);
  assert.equal(statuses.some(item=>item.message==='obsolete mobile card'),false);
  pending.get('F').resolve({id:'F',title:'full-F'}); await current;
  assert.equal(state.mobileCardId,'F');

  const oldSame=openMobileCardDetail('G'); closeMobileCardDetail();
  const newSame=openMobileCardDetail('G');
  pending.get('G').resolve({id:'G',title:'current-same-id'});
  await Promise.all([oldSame,newSame]);
  assert.equal(state.mobileCardId,'G');
  assert.equal(state.mobileCard.title,'current-same-id');
  assert.equal(opened.filter(id=>id==='G').length,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_mobile_card_save_does_not_mutate_replacement_workspace(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        definitions = section(
            source,
            "    function closeMobileCardDetail()",
            "    function handleMobileBoardClick(",
        )
        self.run_node(
            """
const assert = require('node:assert/strict');
function deferred() {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[], statuses=[];
const state = {
  viewerStateGeneration: 1, operatorSessionToken: 'session-A', actor: 'A',
  mobileCardContextGeneration: 0, mobileCardOpenRequest: null, mobileCardSaveRequest: null,
  mobileCardId: 'A', mobileCard: {id:'A',title:'draft-A'}, mobileCardTab:'overview',
  mobileCardCreating:false, mobileCardLoading:false, mobileCardSaving:false, mobileCardFilesBusy:false,
  activeCard:{id:'A'}, activeCardIsFull:true, editingId:'A', pendingCardClientId:'', snapshot:{columns:[{id:'todo'}]},
};
const els = {mobileCardSaveButton:{disabled:false,textContent:''},mobileCardTitleInput:{focus(){}}};
global.requestAnimationFrame=(callback)=>callback();
function deferredApi(path,options) {const task=deferred();calls.push({path,options,...task});return task.promise;}
const api=deferredApi;
function readMobileCardDraft(){return {actor_name:state.actor,source:'ui',title:'saved-A',column:'todo',tags:[],vehicle_profile:{}};}
function currentMobileCard(){return state.mobileCard;}
function resetMobileCardJournal(){}
function syncMobileCardEditorBusyState(){}
function renderMobileShell(){}
function renderMobileCardDetail(){}
function cacheFullCard(){}
function applySavedCardLocalPatch(){}
function refreshRepairOrderEntry(){}
function setStatus(message,isError){statuses.push({message,isError});}
function emptyMobileCardDraft(){return {id:''};}
function snapshotCardById(){return null;}
function fetchFullCard(){throw new Error('not used');}
function recordCardOpenSideEffects(){}
function loadMobileCardJournal(){}
"""
            + definitions
            + """
(async()=>{
  const saving=saveMobileCardDetail();
  assert.equal(calls.length,1);
  assert.equal(closeMobileCardDetail(),false);
  assert.equal(state.mobileCardId,'A');
  assert.equal(state.mobileCardSaving,true);
  calls[0].resolve({card:{id:'A',title:'server-A'}}); await saving;
  assert.equal(state.mobileCardId,'A');
  assert.equal(state.mobileCard.title,'server-A');
  assert.equal(state.mobileCardSaving,false);
  assert.equal(statuses.some(item=>item.message==='КАРТОЧКА СОХРАНЕНА.'),true);
  assert.equal(closeMobileCardDetail(),true);
  assert.equal(state.mobileCardId,'');

  state.mobileCardContextGeneration++;
  state.mobileCardId='C'; state.mobileCard={id:'C',title:'current-C'};
  state.mobileCardLoading=false; state.mobileCardSaving=false;
  const rejected=saveMobileCardDetail(); invalidateMobileCardContext(); state.mobileCardSaving=false;
  calls[1].reject(new Error('obsolete save')); await rejected;
  assert.equal(statuses.some(item=>item.message==='obsolete save'),false);
  assert.equal(state.mobileCardSaving,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_mobile_card_journal_response_and_finally_are_request_owned(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        journal = section(
            source,
            "    function resetMobileCardJournal()",
            "    function readMobileCardDraft()",
        )
        journal_helpers = section(
            source,
            "    function cardJournalLoadKey(",
            "    function handleCardJournalLoadMore(",
        )
        context = section(
            source,
            "    function invalidateMobileCardContext()",
            "    function openMobileNewCard()",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const CARD_JOURNAL_INITIAL_LIMIT=50,CARD_JOURNAL_MAX_LIMIT=1000,CARD_JOURNAL_LIMIT_STEP=50;
const state={
  viewerStateGeneration:1,operatorSessionToken:'A',actor:'A',mobileCardContextGeneration:1,
  mobileCardId:'same',mobileCardCreating:false,mobileCardJournalRequest:null,
  mobileCardJournalPayload:null,mobileCardJournalLoadedFor:'',mobileCardJournalLimit:50,mobileCardJournalLoading:false,
};
const els={mobileCardJournal:{innerHTML:''},mobileCardJournalMeta:{textContent:''},mobileCardJournalRefreshButton:{disabled:false}};
const calls=[],statuses=[];
function api(){const task=deferred();calls.push(task);return task.promise;}
function finiteNumber(value,fallback=0){const number=Number(value);return Number.isFinite(number)?number:fallback;}
function cardJournalEntriesFromPayload(payload){return payload?.entries||[];}
function buildCardJournalDays(){return [];}
function escapeHtml(value){return String(value);}
function renderCardJournalDetails(){return {inlineHtml:'',blockHtml:''};}
function cardJournalActorName(){return '';}
function cardJournalActionLabel(){return '';}
function formatJournalTime(){return '';}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + journal_helpers
            + context
            + journal
            + """
(async()=>{
  const old=loadMobileCardJournal({force:true});
  invalidateMobileCardContext();
  state.mobileCardId='same'; state.mobileCardCreating=false; state.mobileCardJournalLoading=false;
  const current=loadMobileCardJournal({force:true});
  calls[1].resolve({entries:[{id:'current'}],meta:{events_total:1}}); await current;
  calls[0].resolve({entries:[{id:'old'}],meta:{events_total:1}}); await old;
  assert.equal(state.mobileCardJournalPayload.entries[0].id,'current');
  assert.equal(state.mobileCardJournalLoading,false);

  const staleError=loadMobileCardJournal({force:true});
  invalidateMobileCardContext();
  state.mobileCardId='same'; state.mobileCardCreating=false; state.mobileCardJournalLoading=false;
  const replacement=loadMobileCardJournal({force:true});
  calls[2].reject(new Error('obsolete journal')); await staleError;
  assert.equal(state.mobileCardJournalLoading,true,'stale finally cleared replacement loading');
  assert.equal(statuses.length,0,'stale journal error was reported');
  calls[3].resolve({entries:[{id:'replacement'}],meta:{events_total:1}}); await replacement;
  assert.equal(state.mobileCardJournalPayload.entries[0].id,'replacement');
  assert.equal(state.mobileCardJournalLoading,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_mobile_card_file_write_stops_after_workspace_invalidation(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        files = section(
            source,
            "    function applyMobileCardFromFullCard(",
            "    function resetMobileCardJournal()",
        )
        context = section(
            source,
            "    function invalidateMobileCardContext()",
            "    function openMobileNewCard()",
        )
        close = section(
            source,
            "    function closeMobileCardDetail()",
            "    function invalidateMobileCardContext()",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const upload={name:'one.txt',type:'text/plain',arrayBuffer:async()=>new Uint8Array([1]).buffer};
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',actor:'A',mobileCardContextGeneration:1,
  mobileCardId:'A',mobileCard:{id:'A'},mobileCardCreating:false,mobileCardLoading:false,
  mobileCardFilesBusy:false,mobileCardFilesRequest:null,fullCardCache:new Map(),
  activeCard:null,activeCardIsFull:false,editingId:null,pendingCardClientId:'',
};
const els={mobileCardFileInput:{files:[upload],value:'old-selection'},mobileCardFileAddButton:{},mobileCardFileMeta:{}};
const calls=[],statuses=[];
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function currentMobileCard(){return state.mobileCard;}
function renderMobileCardFiles(){}
function normalizeUploadableAttachmentFile(file){return file;}
function arrayBufferToBase64(){return 'AQ==';}
function normalizeAttachmentMimeType(value){return value;}
function attachmentMimeTypeFromExtension(){return '';}
function attachmentExtension(){return 'txt';}
function cacheFullCard(){}
function applySavedCardLocalPatch(){}
function refreshRepairOrderEntry(){}
function refreshSnapshot(){throw new Error('stale upload must not refresh snapshot');}
function setStatus(message,isError){statuses.push({message,isError});}
function resetMobileCardJournal(){}
function renderMobileShell(){}
"""
            + close
            + context
            + files
            + """
(async()=>{
  const writing=uploadMobileCardFiles();
  for(let i=0;i<5 && calls.length===0;i++) await Promise.resolve();
  assert.equal(calls.length,1);
  assert.equal(calls[0].path,'/api/add_card_attachment');
  assert.equal(calls[0].options.body.card_id,'A');
  assert.equal(calls[0].options.body.actor_name,'A');
  assert.equal(closeMobileCardDetail(),false);
  assert.equal(state.mobileCardId,'A');
  assert.equal(state.mobileCardFilesBusy,true);
  invalidateMobileCardContext();
  state.mobileCardId='B'; state.mobileCard={id:'B'}; state.mobileCardFilesBusy=false;
  els.mobileCardFileInput.value='new-selection'; state.actor='B'; state.operatorSessionToken='session-B';
  calls[0].resolve({}); await writing;
  assert.equal(calls.length,1,'stale upload continued with another request');
  assert.equal(state.mobileCardId,'B');
  assert.equal(state.mobileCardFilesBusy,false);
  assert.equal(els.mobileCardFileInput.value,'new-selection','stale finally cleared the replacement input');
  assert.equal(statuses.length,1);
  assert.equal(statuses[0].isError,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_mobile_repair_order_open_and_save_are_workspace_owned(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        definitions = section(
            source,
            "    function closeMobileRepairOrderDetail()",
            "    function handleMobileRepairOrdersListClick(",
        )
        self.run_node(
            """
const assert = require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[], statuses=[], cached=[], localPatches=[], repaired=[];
let draftStatus='closed', currentStatus='open', listLoads=0;
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',actor:'A',
  mobileRepairOrderContextGeneration:0,mobileRepairOrderOpenRequest:null,mobileRepairOrderSaveRequest:null,
  mobileRepairOrderCardId:'',mobileRepairOrderCard:null,mobileRepairOrderTab:'client',mobileRepairOrderLoading:false,mobileRepairOrderSaving:false,
  activeCard:null,editingId:null,pendingCardClientId:'',
  repairOrderPaymentVerificationPending:'',repairOrderWriteVerificationPending:'',
};
const els={mobileRepairOrderSaveButton:{disabled:false,textContent:''},mobileRepairOrderStatusSelect:{value:'closed'}};
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function mobileRepairOrderSummaryCard(id){return {id,repair_order:{status:'open'}};}
function lockMobileRepairOrderEditor(){}
function renderMobileShell(){}
function renderMobileRepairOrderDetail(){}
function ensureMobileRepairOrderPaymentCashboxes(){return Promise.resolve();}
function cacheFullCard(card){cached.push(card);}
function setStatus(message,isError){statuses.push({message,isError});}
function readMobileRepairOrderDraft(){return {status:draftStatus,works:[],materials:[],payments:[]};}
function currentMobileRepairOrderDraft(){return {status:currentStatus};}
function normalizeRepairOrderStatus(value){return value||'open';}
function applySavedCardLocalPatch(card){localPatches.push(card);}
function refreshRepairOrderEntry(card){repaired.push(card);}
function loadRepairOrders(){listLoads++;return Promise.resolve();}
function repairOrderFormatMoney(value){return String(value);}
function repairOrderNeedsVerification(){return false;}
function repairOrderWriteNeedsVerification(){return false;}
function repairOrderVerificationMessage(){return 'verification pending';}
function clearRepairOrderVerificationPending(){}
function markRepairOrderWriteVerificationPending(){}
global.window={confirm(){return true;},prompt(){return 'reason';}};
"""
            + definitions
            + """
(async()=>{
  const first=openMobileRepairOrderDetail('A');
  const callsBeforeBlockedSave=calls.length;
  saveMobileRepairOrder();
  assert.equal(calls.length,callsBeforeBlockedSave,'save ran before the full repair order loaded');
  assert.equal(state.mobileRepairOrderLoading,true);
  const second=openMobileRepairOrderDetail('B');
  calls[1].resolve({card:{id:'B',repair_order:{status:'open'}}}); await second;
  calls[0].resolve({card:{id:'A',repair_order:{status:'open'}}}); await first;
  assert.equal(state.mobileRepairOrderCardId,'B');

  const staleError=openMobileRepairOrderDetail('C'), current=openMobileRepairOrderDetail('D');
  calls[2].reject(new Error('obsolete order')); await staleError;
  assert.equal(state.mobileRepairOrderCardId,'D');
  assert.equal(statuses.some(item=>item.message==='obsolete order'),false,'stale open error was reported');
  calls[3].resolve({card:{id:'D',repair_order:{status:'open'}}}); await current;

  const oldSame=openMobileRepairOrderDetail('same');
  const oldSameCall=calls.at(-1);
  closeMobileRepairOrderDetail();
  const newSame=openMobileRepairOrderDetail('same');
  const newSameCall=calls.at(-1);
  newSameCall.resolve({card:{id:'same',updated_at:'same-new',repair_order:{status:'open'}}}); await newSame;
  oldSameCall.resolve({card:{id:'same',updated_at:'same-old',repair_order:{status:'open'}}}); await oldSame;
  assert.equal(state.mobileRepairOrderCard.updated_at,'same-new');

  state.mobileRepairOrderCardId='E'; state.mobileRepairOrderCard={id:'E',updated_at:'r1',repair_order:{status:'open'}};
  state.mobileRepairOrderContextGeneration++;
  const saving=saveMobileRepairOrder();
  const updateCall=calls.at(-1);
  assert.equal(updateCall.path,'/api/update_repair_order');
  closeMobileRepairOrderDetail();
  const statusCountAfterBlockedClose=statuses.length;
  state.viewerStateGeneration++; state.operatorSessionToken='session-B'; state.actor='B';
  state.mobileRepairOrderCardId='F'; state.mobileRepairOrderCard={id:'F',repair_order:{status:'open'}};
  updateCall.resolve({card:{id:'E',updated_at:'r2',repair_order:{status:'open'}}}); await saving;
  assert.equal(calls.filter(call=>call.path==='/api/set_repair_order_status').length,0,'stale save issued the second write');
  assert.equal(state.mobileRepairOrderCardId,'F');
  assert.equal(statuses.length,statusCountAfterBlockedClose,'stale save reported status: '+JSON.stringify(statuses));

  state.mobileRepairOrderContextGeneration++;
  state.mobileRepairOrderCardId='G';
  state.mobileRepairOrderCard={id:'G',updated_at:'g-r1',repair_order:{status:'open'}};
  state.mobileRepairOrderLoading=false; state.mobileRepairOrderSaving=false;
  const successful=saveMobileRepairOrder();
  const currentUpdate=calls.at(-1);
  assert.equal(currentUpdate.path,'/api/update_repair_order');
  assert.equal(currentUpdate.options.body.actor_name,'B');
  currentUpdate.resolve({card:{id:'G',updated_at:'g-r2',repair_order:{status:'open'}}});
  for(let i=0;i<5 && calls.at(-1)===currentUpdate;i++) await Promise.resolve();
  const closeCall=calls.at(-1);
  assert.equal(closeCall.path,'/api/set_repair_order_status');
  assert.equal(closeCall.options.body.card_id,'G');
  assert.equal(closeCall.options.body.expected_updated_at,'g-r2');
  assert.equal(closeCall.options.body.actor_name,'B');
  closeCall.resolve({card:{id:'G',updated_at:'g-r3',repair_order:{status:'closed'}}});
  await successful;
  assert.equal(calls.filter(call=>call.path==='/api/set_repair_order_status').length,1);
  assert.equal(state.mobileRepairOrderCard.updated_at,'g-r3');
  assert.equal(state.mobileRepairOrderSaving,false);

  draftStatus='ready'; currentStatus='open'; els.mobileRepairOrderStatusSelect.value='ready';
  state.mobileRepairOrderContextGeneration++;
  state.mobileRepairOrderCardId='H';
  state.mobileRepairOrderCard={id:'H',updated_at:'h-r1',repair_order:{status:'open'}};
  const readySave=saveMobileRepairOrder();
  const readyUpdate=calls.at(-1);
  readyUpdate.resolve({card:{id:'H',updated_at:'h-r2',repair_order:{status:'open'}}});
  for(let i=0;i<5 && calls.at(-1)===readyUpdate;i++) await Promise.resolve();
  const readyStatus=calls.at(-1);
  assert.equal(readyStatus.path,'/api/set_repair_order_status');
  assert.equal(readyStatus.options.body.status,'ready');
  readyStatus.resolve({card:{id:'H',updated_at:'h-r3',repair_order:{status:'ready'}}});
  await readySave;
  assert.equal(state.mobileRepairOrderCard.repair_order.status,'ready');

  state.mobileRepairOrderContextGeneration++;
  state.mobileRepairOrderCardId='I';
  state.mobileRepairOrderCard={id:'I',updated_at:'i-r1',repair_order:{status:'open'}};
  const ambiguousStatusCount=statuses.length;
  const ambiguous=saveMobileRepairOrder();
  const ambiguousUpdate=calls.at(-1);
  ambiguousUpdate.resolve({card:{id:'I',updated_at:'i-r2',repair_order:{status:'open'}}});
  for(let i=0;i<5 && calls.at(-1)===ambiguousUpdate;i++) await Promise.resolve();
  const ambiguousWrite=calls.at(-1);
  assert.equal(ambiguousWrite.path,'/api/set_repair_order_status');
  ambiguousWrite.reject(new Error('lost status response'));
  for(let i=0;i<5 && calls.at(-1)===ambiguousWrite;i++) await Promise.resolve();
  const statusReadback=calls.at(-1);
  assert.equal(statusReadback.path,'/api/get_repair_order');
  statusReadback.resolve({card:{id:'I',updated_at:'i-r3',repair_order:{status:'ready'}}});
  await ambiguous;
  assert.equal(state.mobileRepairOrderCard.updated_at,'i-r3');
  assert.equal(state.mobileRepairOrderCard.repair_order.status,'ready');
  assert.equal(statuses.at(-1).isError,false);
  assert.equal(statuses.length,ambiguousStatusCount+1);
  assert.equal(calls.filter(call=>call.path==='/api/update_repair_order'&&call.options.body.card_id==='I').length,1);
  assert.equal(calls.filter(call=>call.path==='/api/set_repair_order_status'&&call.options.body.card_id==='I').length,1);

  draftStatus='closed'; currentStatus='closed'; els.mobileRepairOrderStatusSelect.value='closed';
  state.mobileRepairOrderContextGeneration++;
  state.mobileRepairOrderCardId='J';
  state.mobileRepairOrderCard={id:'J',updated_at:'j-r1',repair_order:{status:'closed'}};
  const reopen=saveMobileRepairOrder();
  const preview=calls.at(-1);
  assert.equal(preview.path,'/api/preview_repair_order_reopen');
  preview.resolve({payroll_reversals:[]});
  for(let i=0;i<5 && calls.at(-1)===preview;i++) await Promise.resolve();
  const reopenWrite=calls.at(-1);
  assert.equal(reopenWrite.path,'/api/reopen_repair_order');
  reopenWrite.reject(new Error('lost reopen response'));
  for(let i=0;i<5 && calls.at(-1)===reopenWrite;i++) await Promise.resolve();
  const reopenReadback=calls.at(-1);
  assert.equal(reopenReadback.path,'/api/get_repair_order');
  reopenReadback.resolve({card:{id:'J',updated_at:'j-r2',repair_order:{status:'open'}}});
  await reopen;
  assert.equal(state.mobileRepairOrderCard.updated_at,'j-r2');
  assert.equal(state.mobileRepairOrderCard.repair_order.status,'open');
  assert.equal(statuses.at(-1).isError,false);
  assert.equal(localPatches.at(-1).id,'J');
  assert.equal(repaired.at(-1).id,'J');
  assert.equal(cached.at(-1).id,'J');
  assert.ok(listLoads>=4);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_repair_order_list_ignores_superseded_responses(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        loader = section(
            source,
            "    loadRepairOrders = async function(openModal = false)",
            "    function applyRepairOrdersSearch()",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const state={viewerStateGeneration:1,operatorSessionToken:'A',repairOrdersRequestSeq:0,repairOrdersFilter:'open',repairOrdersItems:[]};
const els={repairOrdersModal:{},repairOrdersMeta:{},repairOrdersList:{}};
const pending=[];
function repairOrdersRequestPath(){return '/orders?status='+state.repairOrdersFilter;}
function renderRepairOrders(data){state.repairOrdersItems=data.repair_orders;}
function setModalListError(){throw new Error('unexpected current error');}
function loadModalData(path,options){
  const task=deferred();pending.push({path,options,...task});
  return task.promise.then(data=>{
    if (options.isCurrent && !options.isCurrent()) return null;
    options.onSuccess(data); return data;
  },error=>{
    if (options.isCurrent && !options.isCurrent()) return null;
    options.onError(error); return null;
  });
}
"""
            + loader
            + """
(async()=>{
  const old=loadRepairOrders(true);
  state.repairOrdersFilter='closed';
  const current=loadRepairOrders(false);
  pending[1].resolve({repair_orders:[{card_id:'new'}]}); await current;
  pending[0].resolve({repair_orders:[{card_id:'old'}]}); await old;
  assert.equal(state.repairOrdersItems[0].card_id,'new');

  const loggedOut=loadRepairOrders(false);
  state.viewerStateGeneration++; state.operatorSessionToken='B'; state.repairOrdersItems=[];
  pending[2].resolve({repair_orders:[{card_id:'private-old-viewer'}]}); await loggedOut;
  assert.deepEqual(state.repairOrdersItems,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )


if __name__ == "__main__":
    unittest.main()
