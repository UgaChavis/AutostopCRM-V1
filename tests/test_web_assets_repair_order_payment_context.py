from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/minimal_kanban/web_app_assets/source/app_main_before_printing.js"


def functions(*names: str) -> str:
    source = SOURCE.read_text(encoding="utf-8")
    chunks: list[str] = []
    dependencies = (
        "repairOrderWriteNeedsVerification",
        "repairOrderNeedsVerification",
        "repairOrderVerificationMessage",
        "markRepairOrderWriteVerificationPending",
        "clearRepairOrderVerificationPending",
    )
    for name in dict.fromkeys((*dependencies, *names)):
        declaration = re.search(
            rf"^    (?:async )?function {name}\(.*?^    }}", source, re.M | re.S
        )
        assignment = re.search(
            rf"^    {name} = (?:async )?function\(.*?^    }};", source, re.M | re.S
        )
        match = declaration or assignment
        if match is None:
            raise AssertionError(f"Missing runtime function app_main_before_printing.js:{name}")
        chunks.append(match.group())
    return "\n".join(chunks)


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class RepairOrderPaymentContextRegressionTests(unittest.TestCase):
    def run_node(self, definitions: str, body: str) -> None:
        script = (
            r"""
const watchdog=setTimeout(()=>{throw new Error('scenario did not complete');},3000);
(async()=>{
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
async function spinUntil(predicate){for(let index=0;index<30&&!predicate();index++)await Promise.resolve();assert.ok(predicate());}
const calls=[],statuses=[],appliedOrders=[],renderedPayments=[],pushedModals=[],poppedModals=[],cashboxRenders=[];
const archivedPatches=[],snapshotRefreshes=[],closedCards=[];let archiveActionSyncs=0;
const openModalKeys=new Set();
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',actor:'actor-A',
  editingId:'A',activeCard:{id:'A',updated_at:'r0',repair_order:{status:'open',payments:[]}},activeCardIsFull:true,
  cardEditingGeneration:1,cardHydrationSeq:1,repairOrderContextGeneration:1,
  repairOrderMutationRequest:null,repairOrderOpenRequest:null,repairOrderPaymentCashboxesRequest:null,
  repairOrderSaveInFlight:false,repairOrderSavePromise:null,repairOrderLoading:false,repairOrderWriteReady:true,
  repairOrderPaymentVerificationPending:'',repairOrderPayments:[],currentOrder:{status:'open',payments:[]},
  repairOrderWriteVerificationPending:'',
  repairOrderParentLayer:'card',repairOrderTags:[],cashboxes:[{id:'cashbox-A',name:'Main cash'}],
  cashboxesLoaded:true,cashboxesReferencesOnly:false,fullCardCache:new Map(),cardFetchInFlight:new Map(),
  inventoryRequests:{material:null},inventoryMaterialSaving:false,archiveCards:[],snapshot:{cards:[]},
};
const emptyControls={querySelectorAll(){return[];}};
const paymentAmount={value:'100',disabled:false,focus(){this.focused=(this.focused||0)+1;}};
const paymentCashbox={value:'cashbox-A',disabled:false,focus(){},innerHTML:''};
const paymentNote={value:'note',disabled:false};
const paymentsClassList={contains(value){return value==='is-open'&&openModalKeys.has('repair-order-payments');}};
const els={
  repairOrderModal:emptyControls,
  repairOrderPaymentsModal:{...emptyControls,classList:paymentsClassList},
  repairOrderPaymentAmount:paymentAmount,repairOrderPaymentCashbox:paymentCashbox,
  repairOrderPaymentNote:paymentNote,repairOrderSaveButton:{disabled:false},repairOrderCloseButton:{disabled:false},
  cardModal:{classList:{contains(){return true;}}},fileInput:{value:''},
};
global.window={
  setTimeout(callback){queueMicrotask(callback);return 1;},clearTimeout(){},confirm(){return true;},prompt(){return 'reason';},
};
const FULL_CARD_CACHE_LIMIT=20,CARD_JOURNAL_INITIAL_LIMIT=20;
let saveRepairOrder;
let api=async()=>{throw new Error('api stub not configured');};
let employeesLoader=()=>Promise.resolve({employees:[]});
function loadEmployeesReference(){return employeesLoader();}
function setStatus(message,isError){statuses.push({message:String(message),isError:Boolean(isError)});}
function repairOrderCardDraft(_card,order){return {...(order||{}),payments:[...((order||{}).payments||[])]};}
function applyRepairOrderToForm(order={}){
  state.currentOrder={...order};
  state.repairOrderPayments=(order.payments||[]).map(item=>({...item}));
  appliedOrders.push(order.revision||order.number||state.activeCard?.updated_at||'unknown');
  return order;
}
function applyRepairOrderCardUpdate(card,fallbackOrder={}){
  if(!card)return fallbackOrder;
  const resolved={...card,id:String(card.id||state.editingId||'').trim()};
  const order=resolved.repair_order||fallbackOrder;
  resolved.repair_order=order;
  state.activeCard=resolved;state.editingId=resolved.id;
  applyRepairOrderToForm(order);
  return order;
}
function readRepairOrderFromForm(){return {...state.currentOrder,payments:(state.repairOrderPayments||[]).map(item=>({...item}))};}
async function requireRepairOrderCardId(){return String(state.editingId||state.activeCard?.id||'').trim();}
function normalizeRepairOrderPayment(item,fallbackId){return {...item,id:String(item?.id||fallbackId||'').trim()};}
function repairOrderParseNumber(value){const parsed=Number(String(value).replace(',','.'));return Number.isFinite(parsed)?parsed:null;}
function repairOrderNumberToRaw(value){return String(value);}
function currentRepairOrderDateTime(){return '2026-09-09T10:00';}
function selectedRepairOrderPaymentCashbox(){return state.cashboxes.find(item=>item.id===paymentCashbox.value)||null;}
function repairOrderPaymentMethodFromCashboxName(){return 'cash';}
function syncRepairOrderPaymentMethodFromPayments(){}
function renderRepairOrderPayments(){renderedPayments.push((state.repairOrderPayments||[]).map(item=>({...item})));}
function scheduleRepairOrderSaveDirtyStateSync(){}
function syncRepairOrderEditingState(){}
function syncRepairOrderCloseButtonState(){}
function syncRepairOrderSaveDirtyState(){}
async function refreshRepairOrdersListAfterMutation(){}
async function refreshCashboxesAfterMoneyMutation(){}
function perfMeasureAsync(_name,callback){return callback();}
function repairOrderIsFullyPaid(){return true;}
function repairOrderCloseBlockedMessage(){return 'blocked';}
function normalizeRepairOrderStatus(value){return String(value||'open');}
function repairOrderFormatMoney(value){return String(value);}
async function loadRepairOrders(){}
function pushModal(key){pushedModals.push(key);openModalKeys.add(key);}
function popModal(key){poppedModals.push(key);openModalKeys.delete(key);}
function ensureRepairOrderPaymentsModalUi(){}
function bindRepairOrderPaymentsUiEvents(){}
function renderRepairOrderPaymentCashboxOptions(){cashboxRenders.push(state.cashboxes.map(item=>item.id));}
function closeRepairOrderWorkSalaryPopover(){}
function clearCardOpenSideEffectTimer(){}
function hideClientSuggestions(){}
function stopCardCleanupPolling(){}
function refreshRepairOrderEntry(){}
function syncCardSaveDirtyState(){}
function clearFilePreview(){}
function syncFileDropzone(){}
function syncFilePreview(){}
function renderCardCleanupIndicator(){}
function applyCardSeenSuppression(card){return card;}
function syncCardArchiveAction(){archiveActionSyncs++;}
function applyArchivedCardPatch(card){archivedPatches.push(card);return true;}
async function refreshSnapshot(force){snapshotRefreshes.push(force);}
function closeCardModal(options){closedCards.push(options);}
"""
            + definitions
            + "\n"
            + body
            + r"""
})().then(()=>clearTimeout(watchdog),error=>{clearTimeout(watchdog);console.error(error);process.exitCode=1;});
"""
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

    def payment_functions(self, *names: str) -> str:
        return functions(
            "captureRepairOrderMutationContext",
            "beginRepairOrderMutation",
            "finishRepairOrderMutation",
            "repairOrderResponseCard",
            "repairOrderCardFromResponse",
            "readRepairOrderAfterAmbiguousWrite",
            "repairOrderPaymentNeedsVerification",
            "repairOrderPaymentVerificationMessage",
            "markRepairOrderPaymentVerificationPending",
            "clearRepairOrderPaymentVerificationPending",
            *names,
        )

    def test_ambiguous_add_persists_pending_and_blocks_every_repeated_write(self) -> None:
        definitions = self.payment_functions(
            "deleteRepairOrderPayment",
            "addRepairOrderPayment",
            "persistRepairOrderRecord",
            "saveRepairOrder",
            "toggleRepairOrderStatus",
            "closeRepairOrderPaymentsModal",
            "closeRepairOrderModal",
            "resetCardModalState",
        )
        self.run_node(
            definitions,
            r"""
Date.now=()=>1000;
state.fullCardCache.set('A',{id:'A',updated_at:'r0'});
api=async(path,options)=>{
  calls.push({path,options});
  if(path==='/api/update_repair_order')throw new Error('write response lost after commit');
  if(path==='/api/get_repair_order')throw new Error('readback unavailable');
  throw new Error('unexpected request '+path);
};

await addRepairOrderPayment();
assert.deepEqual(calls.map(call=>call.path),['/api/update_repair_order','/api/get_repair_order']);
assert.equal(state.repairOrderPayments.length,1);
assert.equal(state.repairOrderPayments[0].id,'payment-1000');
assert.equal(state.repairOrderPayments[0]._saving,false);
assert.equal(state.repairOrderPayments[0]._uncertain,true);
assert.equal(state.repairOrderPaymentVerificationPending,'A');
assert.equal(repairOrderPaymentNeedsVerification('A'),true);
assert.equal(repairOrderPaymentNeedsVerification('B'),false);
assert.equal(state.fullCardCache.has('A'),false);
assert.equal(state.repairOrderMutationRequest,null,'ambiguous add left mutation locked');
assert.ok(statuses.some(item=>item.message.includes('РЕЗУЛЬТАТ ЗАПИСИ ОПЛАТЫ НЕ ОПРЕДЕЛЕН')));

const requestCount=calls.length;
await addRepairOrderPayment();
await deleteRepairOrderPayment('payment-1000');
assert.equal(await saveRepairOrder(),false);
await toggleRepairOrderStatus();
assert.equal(calls.length,requestCount,'pending payment outcome allowed a repeated write/read');
assert.ok(statuses.slice(-4).every(item=>item.message.includes('НЕ ПОДТВЕРЖДЕН')));

assert.equal(closeRepairOrderModal(),true);
assert.equal(state.repairOrderPaymentVerificationPending,'A','repair-order close cleared per-card pending');
resetCardModalState();
assert.equal(state.repairOrderPaymentVerificationPending,'A','card reset cleared per-card pending');
assert.equal(repairOrderPaymentNeedsVerification('A'),true);
""",
        )

    def test_ambiguous_delete_restores_uncertain_payment_and_blocks_writes(self) -> None:
        definitions = self.payment_functions(
            "deleteRepairOrderPayment",
            "addRepairOrderPayment",
            "persistRepairOrderRecord",
            "saveRepairOrder",
            "toggleRepairOrderStatus",
        )
        self.run_node(
            definitions,
            r"""
const original={id:'delete-me',amount:'75',cashbox_id:'cashbox-A'};
state.repairOrderPayments=[original];state.currentOrder={status:'open',payments:[original]};
state.activeCard.repair_order=state.currentOrder;
api=async(path,options)=>{
  calls.push({path,options});
  if(path==='/api/update_repair_order')throw new Error('delete response lost after commit');
  if(path==='/api/get_repair_order')throw new Error('readback unavailable');
  throw new Error('unexpected request '+path);
};

await deleteRepairOrderPayment('delete-me');
assert.deepEqual(calls.map(call=>call.path),['/api/update_repair_order','/api/get_repair_order']);
assert.equal(state.repairOrderPayments.length,1);
assert.equal(state.repairOrderPayments[0].id,'delete-me');
assert.equal(state.repairOrderPayments[0]._saving,false);
assert.equal(state.repairOrderPayments[0]._uncertain,true);
assert.equal(state.repairOrderPaymentVerificationPending,'A');
assert.equal(state.repairOrderMutationRequest,null,'ambiguous delete left mutation locked');
assert.ok(statuses.some(item=>item.message.includes('РЕЗУЛЬТАТ УДАЛЕНИЯ ОПЛАТЫ НЕ ОПРЕДЕЛЕН')));

const requestCount=calls.length;
await deleteRepairOrderPayment('delete-me');
await addRepairOrderPayment();
assert.equal(await saveRepairOrder(),false);
await toggleRepairOrderStatus();
assert.equal(calls.length,requestCount,'pending delete outcome allowed a repeated write/read');
assert.ok(statuses.slice(-4).every(item=>item.message.includes('НЕ ПОДТВЕРЖДЕН')));
""",
        )

    def test_successful_readback_confirms_ambiguous_add_and_delete(self) -> None:
        definitions = self.payment_functions(
            "deleteRepairOrderPayment", "addRepairOrderPayment", "persistRepairOrderRecord"
        )
        self.run_node(
            definitions,
            r"""
Date.now=()=>1000;
let operation='add';
api=async(path,options)=>{
  calls.push({path,options,operation});
  if(path==='/api/update_repair_order')throw new Error(operation+' response lost');
  if(path==='/api/get_repair_order'&&operation==='add')return {card:{
    id:'A',updated_at:'r1',repair_order:{revision:'confirmed-add',status:'open',payments:[{id:'payment-1000',amount:'100'}]},
  }};
  if(path==='/api/get_repair_order'&&operation==='delete')return {card:{
    id:'A',updated_at:'r2',repair_order:{revision:'confirmed-delete',status:'open',payments:[]},
  }};
  throw new Error('unexpected request '+path);
};

await addRepairOrderPayment();
assert.equal(state.repairOrderPaymentVerificationPending,'');
assert.equal(state.repairOrderPayments.some(item=>item.id==='payment-1000'),true);
assert.deepEqual(statuses.at(-1),{message:'Оплата сохранена; результат подтвержден повторным чтением.',isError:false});
assert.equal(state.repairOrderMutationRequest,null);

operation='delete';state.repairOrderWriteReady=true;
state.repairOrderPayments=[{id:'delete-me',amount:'75'}];
state.currentOrder={status:'open',payments:state.repairOrderPayments};
state.activeCard={id:'A',updated_at:'r1',repair_order:state.currentOrder};
await deleteRepairOrderPayment('delete-me');
assert.equal(state.repairOrderPaymentVerificationPending,'');
assert.deepEqual(state.repairOrderPayments,[]);
assert.deepEqual(statuses.at(-1),{message:'Оплата удалена; результат подтвержден повторным чтением.',isError:false});
assert.deepEqual(calls.map(call=>call.path),[
  '/api/update_repair_order','/api/get_repair_order','/api/update_repair_order','/api/get_repair_order',
]);
assert.equal(state.repairOrderMutationRequest,null);
""",
        )

    def test_pending_bypasses_stale_card_cache_until_exact_fresh_order_get(self) -> None:
        definitions = functions(
            "repairOrderPaymentNeedsVerification",
            "clearRepairOrderPaymentVerificationPending",
            "cacheFullCard",
            "cachedFullCardForSnapshot",
            "fetchFullCard",
            "captureCardEditingContext",
            "repairOrderResponseCard",
            "openRepairOrderModal",
        )
        self.run_node(
            definitions,
            r"""
state.repairOrderPaymentVerificationPending='A';
const stale={id:'A',updated_at:'r0',repair_order:{revision:'stale-cache',status:'open',payments:[]}};
state.fullCardCache.set('A',stale);state.snapshot.cards=[{id:'A',updated_at:'r0'}];
let orderMode='fail';
api=async(path,options)=>{
  calls.push({path,options});
  if(path.startsWith('/api/get_card?'))return {card:{...stale,description:'fresh card shell'}};
  if(path==='/api/get_repair_order'&&orderMode==='fail')throw new Error('fresh order GET failed');
  if(path==='/api/get_repair_order')return {card:{
    id:'A',updated_at:'r2',repair_order:{revision:'fresh-order',status:'open',payments:[]},
  }};
  throw new Error('unexpected request '+path);
};

assert.equal(cachedFullCardForSnapshot(state.snapshot.cards[0]),null);
const hydrated=await fetchFullCard('A','r0');
assert.equal(hydrated.description,'fresh card shell');
assert.equal(calls.filter(call=>call.path.startsWith('/api/get_card?')).length,1,'pending used stale full-card cache');
assert.equal(state.repairOrderPaymentVerificationPending,'A','generic card GET cleared payment pending');

await openRepairOrderModal();
assert.equal(state.repairOrderPaymentVerificationPending,'A','failed exact repair-order GET cleared pending');
assert.equal(state.repairOrderWriteReady,false);
orderMode='success';
await openRepairOrderModal();
assert.equal(state.repairOrderPaymentVerificationPending,'','successful fresh repair-order GET did not clear pending');
assert.equal(state.repairOrderWriteReady,true);
assert.equal(state.activeCard.updated_at,'r2');
assert.equal(state.activeCard.repair_order.revision,'fresh-order');
""",
        )

    def test_stale_open_waiting_on_employees_cannot_apply_or_clear_after_mutation(self) -> None:
        definitions = functions(
            "captureCardEditingContext",
            "captureRepairOrderMutationContext",
            "beginRepairOrderMutation",
            "finishRepairOrderMutation",
            "repairOrderResponseCard",
            "repairOrderPaymentNeedsVerification",
            "repairOrderPaymentVerificationMessage",
            "markRepairOrderPaymentVerificationPending",
            "clearRepairOrderPaymentVerificationPending",
            "openRepairOrderModal",
        )
        self.run_node(
            definitions,
            r"""
const oldEmployees=deferred();employeesLoader=()=>oldEmployees.promise;
let orderRevision='r1';
api=async(path,options)=>{
  calls.push({path,options,orderRevision});
  if(path==='/api/get_repair_order')return {card:{
    id:'A',updated_at:orderRevision,repair_order:{revision:orderRevision,status:'open',payments:[]},
  }};
  throw new Error('unexpected request '+path);
};

const staleOpen=openRepairOrderModal();
await spinUntil(()=>calls.length===1);
const appliedBeforeMutation=appliedOrders.length;
state.repairOrderLoading=false;state.repairOrderWriteReady=true;
const mutation=beginRepairOrderMutation('A');
assert.ok(mutation);assert.equal(state.repairOrderOpenRequest,null,'mutation did not invalidate old open token');
state.activeCard={id:'A',updated_at:'r2',repair_order:{revision:'r2',status:'open',payments:[]}};
state.currentOrder=state.activeCard.repair_order;state.repairOrderPayments=[];
markRepairOrderPaymentVerificationPending('A');
finishRepairOrderMutation(mutation);
oldEmployees.resolve({employees:[]});
await staleOpen;
assert.equal(appliedOrders.length,appliedBeforeMutation,'stale r1 open applied after r2 mutation');
assert.equal(state.activeCard.updated_at,'r2');
assert.equal(state.repairOrderPaymentVerificationPending,'A','stale r1 open cleared pending');

employeesLoader=()=>Promise.resolve({employees:[]});orderRevision='r3';
await openRepairOrderModal();
assert.equal(state.activeCard.updated_at,'r3');
assert.equal(state.activeCard.repair_order.revision,'r3');
assert.equal(state.repairOrderPaymentVerificationPending,'','fresh exact open failed to clear pending');
""",
        )

    def test_payment_cashbox_load_and_cached_paths_obey_exact_workspace(self) -> None:
        definitions = functions(
            "ensureRepairOrderPaymentCashboxes",
            "openRepairOrderPaymentsModal",
            "closeRepairOrderPaymentsModal",
        )
        self.run_node(
            definitions,
            r"""
state.cashboxes=[];state.cashboxesLoaded=false;
const delayed=deferred();
api=async(path,options)=>{calls.push({path,options});return delayed.promise;};
const logoutOpen=openRepairOrderPaymentsModal();
await spinUntil(()=>calls.length===1);
state.viewerStateGeneration=2;state.operatorSessionToken='session-B';state.actor='actor-B';
state.cardEditingGeneration++;state.cardHydrationSeq++;state.repairOrderContextGeneration++;
state.editingId='B';state.activeCard={id:'B'};state.cashboxes=[{id:'cashbox-B'}];
const newerRequest={};state.repairOrderPaymentCashboxesRequest=newerRequest;
delayed.resolve({cashboxes:[{id:'cashbox-A'}]});
await logoutOpen;
assert.deepEqual(state.cashboxes,[{id:'cashbox-B'}],'stale A cashboxes overwrote session B');
assert.equal(state.repairOrderPaymentCashboxesRequest,newerRequest,'stale finally cleared newer request');
assert.deepEqual(pushedModals,[],'stale A load opened payments modal');

state.repairOrderPaymentCashboxesRequest=null;state.cashboxes=[];state.cashboxesLoaded=false;
const afterClose=deferred();api=async()=>afterClose.promise;
const closingOpen=openRepairOrderPaymentsModal();
await Promise.resolve();
closeRepairOrderPaymentsModal();
state.cardEditingGeneration++;state.cardHydrationSeq++;state.editingId='C';state.activeCard={id:'C'};
afterClose.resolve({cashboxes:[{id:'cashbox-B'}]});
await closingOpen;
assert.equal(pushedModals.length,0,'closed A request reopened modal on card B');

state.cashboxes=[{id:'cached-B'}];state.cashboxesLoaded=true;
const rendersBefore=cashboxRenders.length;
assert.equal(await ensureRepairOrderPaymentCashboxes({isCurrent:()=>false}),null);
assert.equal(cashboxRenders.length,rendersBefore,'stale cached path rendered into a changed workspace');

state.repairOrderPaymentCashboxesRequest=null;
const cachedOpen=openRepairOrderPaymentsModal();
closeRepairOrderPaymentsModal();
await cachedOpen;
assert.equal(pushedModals.length,0,'cached request reopened modal after close');
""",
        )

    def test_archive_restore_response_cannot_cross_viewer_or_session(self) -> None:
        definitions = functions("beginArchiveMutation", "finishArchiveMutation", "restoreCard")
        self.run_node(
            definitions,
            r"""
const response=deferred();
api=(path,options)=>{calls.push({path,options});return response.promise;};
const restoring=restoreCard('archived-A');
assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/restore_card');
assert.equal(calls[0].options.body.card_id,'archived-A');
assert.equal(calls[0].options.body.actor_name,'actor-A');

state.viewerStateGeneration=2;state.operatorSessionToken='session-B';state.actor='actor-B';
state.activeCard={id:'B',updated_at:'current-B'};state.editingId='B';
const currentRequest={};state.archiveMutationRequest=currentRequest;
response.resolve({card:{id:'archived-A',updated_at:'stale-A'}});
assert.equal(await restoring,null);
assert.equal(state.activeCard.id,'B');assert.equal(state.activeCard.updated_at,'current-B');
assert.equal(state.archiveMutationRequest,currentRequest,'stale restore finally cleared newer request');
assert.deepEqual(archivedPatches,[]);assert.deepEqual(snapshotRefreshes,[]);
assert.deepEqual(closedCards,[]);assert.deepEqual(statuses,[]);
assert.equal(archiveActionSyncs,0);
""",
        )

    def test_archive_error_cannot_touch_same_view_reopened_card_workspace(self) -> None:
        definitions = functions(
            "repairOrderPaymentNeedsVerification",
            "beginArchiveMutation",
            "finishArchiveMutation",
            "archiveActiveCard",
        )
        self.run_node(
            definitions,
            r"""
const response=deferred();
api=(path,options)=>{calls.push({path,options});return response.promise;};
const archiving=archiveActiveCard();
assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/archive_card');
assert.equal(calls[0].options.body.card_id,'A');assert.equal(calls[0].options.body.actor_name,'actor-A');

state.cardEditingGeneration++;state.cardHydrationSeq++;
state.editingId='B';state.activeCard={id:'B',updated_at:'current-B'};
response.reject(new Error('private stale A archive failure'));
assert.equal(await archiving,undefined);
assert.equal(state.activeCard.id,'B');assert.equal(state.activeCard.updated_at,'current-B');
assert.equal(state.archiveMutationRequest,null,'owned stale request did not unlock');
assert.deepEqual(archivedPatches,[]);assert.deepEqual(snapshotRefreshes,[]);
assert.deepEqual(closedCards,[]);assert.deepEqual(statuses,[],'stale A error leaked into card B');
assert.equal(archiveActionSyncs,2,'archive action should only sync at begin and owned finish');
""",
        )
