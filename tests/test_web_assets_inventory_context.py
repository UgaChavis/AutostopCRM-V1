from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src/minimal_kanban/web_app_assets/source"


def functions(file: str, *names: str) -> str:
    source = (SOURCE / file).read_text(encoding="utf-8")
    chunks = []
    for name in names:
        match = re.search(rf"^    (?:async )?function {name}\(.*?^    }}", source, re.M | re.S)
        if match is None:
            raise AssertionError(f"Missing runtime function {file}:{name}")
        chunks.append(match.group())
    return "\n".join(chunks)


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class InventoryContextRegressionTests(unittest.TestCase):
    def run_node(self, definitions: str, body: str) -> None:
        script = (
            """
const watchdog=setTimeout(()=>{throw new Error('scenario did not complete');},2000);
(async()=>{
const assert = require('node:assert/strict');
const state = {viewerStateGeneration:0, operatorSessionToken:'A', inventoryItems:[], inventoryQuery:'',
 inventoryActiveId:'', repairOrderInventorySelectedId:'', inventoryLoaded:false,
 inventoryMovementsLoading:false, inventoryMovementsLoaded:false, editingId:'card-A', cardHydrationSeq:0};
const els = {inventoryModal:{}, repairOrderInventoryIssueButton:{}, repairOrderInventoryReturnButton:{}};
const calls=[], statuses=[], modalCalls=[];
function deferred() {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
function api(path, options) {const task=deferred();calls.push({path,options,...task});return task.promise;}
function renderInventoryItems() {} function renderInventory() {} function renderInventoryForm() {}
function renderInventoryMovements() {} function renderMobileInventoryMovements() {}
function renderRepairOrderInventoryPanel() {}
function inventoryStatus(...args) {statuses.push(args);} function setStatus(...args) {statuses.push(args);}
function maybeOpenModal(...args) {modalCalls.push(args);}
function inventoryItemById(id) {return state.inventoryItems.find(item=>item.id===id);}
function inventoryItemId(item) {return item?.id || '';}
function inventoryPayloadFromForm() {return {name:'item',actor_name:state.operatorSessionToken};}
function activeInventoryItem() {return {id:state.inventoryActiveId};}
function inventoryFormRefs() {return {replenishQuantity:{value:'1'}};}
function repairOrderParseNumber(value) {return Number(value);}
function upsertInventoryItem(item) {state.inventoryItems=[item];}
function invalidateInventoryMovements() {}
function selectedRepairOrderInventoryItem() {return {id:'item',quantity:5};}
function repairOrderInventoryQuantity() {return {raw:'1',parsed:1};}
function inventoryItemQuantityNumber() {return 5;}
function inventoryDisplayQuantity() {return '5';}
function repairOrderInventoryTargetRowIndex() {return 0;}
function repairOrderInventorySelectedMovementId() {return 'movement';}
function repairOrderResponseCard(data) {return data.card;}
function applyRepairOrderCardUpdate() {} function readRepairOrderFromForm() {return {};}
"""
            + definitions
            + "\n"
            + body
            + "\n})().then(()=>clearTimeout(watchdog),error=>{clearTimeout(watchdog);console.error(error);process.exitCode=1;});"
        )
        result = subprocess.run(["node"], input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def inventory_functions(self, *names: str) -> str:
        return (
            functions("app_main_before_printing.js", "captureCardEditingContext")
            + functions("inventory_reference.js", "inventoryAsyncContext", "readInventoryItems")
            + functions("inventory_workspace.js", "mutateInventoryMaterial", *names)
        )

    def test_inventory_read_latest_request_and_viewer_own_success_and_error(self) -> None:
        self.run_node(
            self.inventory_functions("loadInventoryItems"),
            """
const old = loadInventoryItems(true,{query:'old'});
const latest = loadInventoryItems(true,{query:'latest'});
calls[1].resolve({items:[{id:'latest'}]});await latest;
calls[0].resolve({items:[{id:'old'}]});await old;
assert.equal(state.inventoryItems[0].id,'latest');
const stale = loadInventoryItems(true);
state.viewerStateGeneration++;state.operatorSessionToken='B';
const current = loadInventoryItems(true);
calls[3].resolve({items:[{id:'B'}]});await current;
const renders=modalCalls.length;
calls[2].reject(new Error(''));await stale;
assert.equal(state.inventoryLoaded,true);assert.equal(state.inventoryItems[0].id,'B');
assert.equal(modalCalls.length,renders);assert.deepEqual(statuses,[]);
""",
        )

    def test_inventory_movement_stale_finally_does_not_clear_new_loading(self) -> None:
        self.run_node(
            self.inventory_functions("loadInventoryMovements"),
            """
const old=loadInventoryMovements();
state.viewerStateGeneration++;state.operatorSessionToken='B';state.inventoryMovementsLoading=false;
const current=loadInventoryMovements();
calls[0].reject(new Error(''));await old;
assert.equal(state.inventoryMovementsLoading,true);assert.deepEqual(statuses,[]);
calls[1].resolve({movements:[{id:'B'}]});await current;
assert.equal(state.inventoryMovementsLoading,false);assert.equal(state.inventoryMovements[0].id,'B');
""",
        )

    def test_inventory_save_stale_result_does_not_fetch_or_clear_new_pending(self) -> None:
        self.run_node(
            self.inventory_functions("saveInventoryItem", "loadInventoryItems"),
            """
const old=saveInventoryItem();
state.viewerStateGeneration++;state.operatorSessionToken='B';state.inventorySaving=false;
const current=saveInventoryItem();
calls[0].resolve({item:{id:'old'}});await old;
assert.equal(calls.length,2);assert.equal(state.inventorySaving,true);assert.deepEqual(state.inventoryItems,[]);
calls[1].reject(new Error('current failure'));await current;
assert.equal(state.inventorySaving,false);
""",
        )

    def test_inventory_write_never_continues_after_old_card_preparation(self) -> None:
        self.run_node(
            self.inventory_functions("writeOffInventoryItem", "returnInventoryMovement"),
            """
let prepared=deferred();function requireRepairOrderCardId() {return prepared.promise;}
async function loadInventoryItems() {} async function refreshRepairOrdersListAfterMutation() {}
api=async function(path) {calls.push({path});return {};};
for(const action of [writeOffInventoryItem,returnInventoryMovement]) {
 state.inventoryMaterialSaving=false;state.editingId='card-A';prepared=deferred();const old=action();
 state.viewerStateGeneration++;state.operatorSessionToken='B';state.editingId='card-B';
 prepared.resolve('card-A');await old;
}
assert.deepEqual(calls,[]);assert.deepEqual(statuses,[]);
""",
        )

    def test_inventory_replenish_old_finally_cannot_clear_new_save(self) -> None:
        self.run_node(
            self.inventory_functions("replenishInventoryItem", "loadInventoryItems"),
            """
state.inventoryActiveId='item';const old=replenishInventoryItem();
state.viewerStateGeneration++;state.inventorySaving=false;
const current=replenishInventoryItem();calls[0].reject(new Error('old failure'));await old;
assert.equal(state.inventorySaving,true);assert.deepEqual(statuses,[]);assert.equal(calls.length,2);
calls[1].resolve({item:{id:'item'}});await Promise.resolve();await Promise.resolve();
assert.equal(calls.length,3);calls[2].resolve({items:[{id:'item'}]});await current;
assert.equal(state.inventorySaving,false);assert.equal(statuses[0][0],'ОСТАТОК ПОПОЛНЕН.');
""",
        )

    def test_inventory_search_invalidates_old_read_before_debounce_and_viewer_change(self) -> None:
        self.run_node(
            self.inventory_functions(
                "loadInventoryItems",
                "handleInventorySearchInput",
                "handleMobileInventorySearchInput",
            ),
            """
const timers=[];const window={setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){}};
els.inventorySearchInput={value:'new'};els.mobileInventorySearchInput={value:'mobile'};
const old=loadInventoryItems();handleInventorySearchInput();calls[0].resolve({items:[{id:'old'}]});await old;
assert.deepEqual(state.inventoryItems,[]);
handleMobileInventorySearchInput();timers[0]();assert.equal(calls.length,1);
state.viewerStateGeneration++;timers[1]();assert.equal(calls.length,1);
""",
        )

    def test_inventory_new_card_write_keeps_captured_client_and_material_intent(self) -> None:
        self.run_node(
            self.inventory_functions("writeOffInventoryItem")
            + functions(
                "app_main_before_printing.js",
                "persistCardPayload",
                "ensureRepairOrderCard",
                "requireRepairOrderCardId",
            ),
            """
state.editingId=null;state.pendingCardClientId='client-A';state.actor='actor-A';
function currentCardPayload(){return {title:'draft'};}
function applyCardModalState(card){state.activeCard=card;state.editingId=card.id;}
async function refreshSnapshot(){} async function refreshRepairOrdersListAfterMutation(){}
async function loadInventoryItems(){}function repairOrderCardRequiredMessage(){return 'missing';}
api=async function(path,options){calls.push({path,options});return {card:{id:'created-A'}};};
await writeOffInventoryItem();
assert.deepEqual(calls.map(call=>call.path),['/api/create_card','/api/link_card_to_client','/api/write_off_inventory_item']);
assert.equal(calls[1].options.body.client_id,'client-A');assert.equal(calls[2].options.body.card_id,'created-A');
assert.equal(calls[2].options.body.actor_name,'actor-A');assert.equal(state.inventoryMaterialSaving,false);
assert.equal(statuses.at(-1)[0],'МАТЕРИАЛ СПИСАН СО СКЛАДА.');
""",
        )

    def test_inventory_duplicate_write_and_closed_draft_do_not_continue(self) -> None:
        self.run_node(
            self.inventory_functions("writeOffInventoryItem"),
            """
const pending=deferred();let prepares=0;
function requireRepairOrderCardId(){prepares++;return pending.promise;}
const old=writeOffInventoryItem();await writeOffInventoryItem();assert.equal(prepares,1);
state.cardEditingGeneration=1;pending.resolve('card-A');await old;
assert.deepEqual(calls,[]);assert.deepEqual(statuses,[]);assert.equal(state.inventoryMaterialSaving,false);
""",
        )

    def test_inventory_write_checks_context_after_nested_refresh(self) -> None:
        self.run_node(
            self.inventory_functions("writeOffInventoryItem"),
            """
const refreshed=deferred();let entered=false,loads=0;
async function requireRepairOrderCardId() {return 'card-A';}
function refreshRepairOrdersListAfterMutation() {entered=true;return refreshed.promise;}
async function loadInventoryItems() {loads++;}
const old=writeOffInventoryItem();await Promise.resolve();
calls[0].resolve({card:{id:'card-A'}});while(!entered)await Promise.resolve();
state.viewerStateGeneration++;state.operatorSessionToken='B';state.editingId='card-B';
refreshed.resolve();await old;assert.equal(loads,0);assert.deepEqual(statuses,[]);
""",
        )

    def test_inventory_old_write_error_and_finally_leave_new_write_pending(self) -> None:
        self.run_node(
            self.inventory_functions("writeOffInventoryItem", "returnInventoryMovement"),
            """
async function requireRepairOrderCardId(){return state.editingId;}
async function loadInventoryItems(){}async function refreshRepairOrdersListAfterMutation(){}
const old=writeOffInventoryItem();await Promise.resolve();
state.viewerStateGeneration++;state.inventoryMaterialSaving=false;
const current=returnInventoryMovement();await Promise.resolve();
calls[0].reject(new Error('old failure'));await old;
assert.equal(state.inventoryMaterialSaving,true);assert.deepEqual(statuses,[]);
calls[1].resolve({});await current;assert.equal(state.inventoryMaterialSaving,false);
assert.equal(statuses.at(-1)[0],'СПИСАНИЕ ВОЗВРАЩЕНО НА СКЛАД.');
""",
        )

    def test_cash_journal_old_error_success_and_download_have_no_new_viewer_effect(self) -> None:
        self.run_node(
            functions(
                "cash_journal.js",
                "cashJournalAsyncContext",
                "openCashJournalModal",
                "downloadCashJournal",
            ),
            """
const tasks=[];function loadCashJournalData(){const t=deferred();tasks.push(t);return t.promise;}
const CASH_JOURNAL_RENDER_BATCH_SIZE=100;els.cashboxJournalText={innerHTML:''};
function cashJournalDefaultFilters(){return {};}
function syncCashJournalModeButtons() {} function renderCashJournalLoading(){return 'loading';}
function renderCashJournal(data){return data.text;}function escapeHtml(s){return s;}
let downloads=0;function triggerBlobDownload(){downloads++;}
const old=openCashJournalModal(),current=openCashJournalModal();
tasks[1].resolve({text:'current'});await current;tasks[0].resolve({text:'old'});await old;
assert.equal(els.cashboxJournalText.innerHTML,'current');
const stale=openCashJournalModal();state.viewerStateGeneration++;state.operatorSessionToken='B';
const next=openCashJournalModal();tasks[3].resolve({text:'B'});await next;
tasks[2].reject(new Error(''));await stale;assert.equal(els.cashboxJournalText.innerHTML,'B');
const download=downloadCashJournal();state.viewerStateGeneration++;
tasks[4].reject(new Error(''));await download;assert.deepEqual(statuses,[]);assert.equal(downloads,0);
const staleDownload=downloadCashJournal();state.employeesCashboxesAccessRevision=1;
tasks[5].resolve({text:'old'});await staleDownload;assert.equal(downloads,0);
""",
        )

    def test_viewer_reset_clears_inventory_caches_inputs_timers_and_pending_controls(self) -> None:
        self.run_node(
            functions("app_main_before_printing.js", "resetViewerScopedState"),
            """
const cleared=[];const window={clearTimeout(id){cleared.push(id);}};
function clearCardOpenSideEffectTimer(){}
for(const key of ['fullCardCache','cardFetchInFlight','cardSeenSuppressions','unreadHoverTimers','unreadSeenDeferredTimers','unreadSeenInFlight'])state[key]=new Map();
state.inventorySearchTimer=10;state.mobileInventorySearchTimer=11;
state.inventoryItems=[{id:'old'}];state.inventoryMovements=[{id:'old'}];
state.inventoryRequests={save:{}};state.inventorySaving=true;state.inventoryMaterialSaving=true;
state.inventoryMovementsLoading=true;state.cashboxJournalData={text:'old'};
els.inventorySearchInput={value:'private search'};els.mobileInventoryNameInput={value:'private name'};
els.cashboxJournalText={textContent:'private journal'};
els.employeeSalaryActionConfirmButton={disabled:true};els.employeeSalaryAdvanceConfirmButton={disabled:true};
els.employeeShiftAccrualConfirmButton={disabled:true};
resetViewerScopedState();assert.deepEqual(cleared,[10,11]);assert.equal(state.inventoryRequests,null);
assert.deepEqual(state.inventoryItems,[]);assert.deepEqual(state.inventoryMovements,[]);
assert.equal(state.inventorySaving,false);assert.equal(state.inventoryMaterialSaving,false);
assert.equal(state.inventoryMovementsLoading,false);assert.equal(state.cashboxJournalData,null);
assert.equal(els.inventorySearchInput.value,'');assert.equal(els.mobileInventoryNameInput.value,'');
assert.equal(els.cashboxJournalText.textContent,'');assert.equal(els.employeeSalaryActionConfirmButton.disabled,false);
assert.equal(els.employeeSalaryAdvanceConfirmButton.disabled,false);assert.equal(els.employeeShiftAccrualConfirmButton.disabled,false);
""",
        )

    def test_card_save_stale_success_error_and_finally_do_not_touch_new_save(self) -> None:
        self.run_node(
            functions("app_main_before_printing.js", "captureCardEditingContext")
            + functions("app_main_after_printing.js", "saveCard"),
            """
const tasks=[];function persistCardPayload(){const task=deferred();tasks.push(task);return task.promise;}
state.activeCardIsFull=true;els.saveCardButton={disabled:false};
els.cardModal={classList:{contains(){return true;}}};
let patches=0,closes=0,clean=0;
function currentCardPayload(){return {title:'card'};}
function clearCardOpenSideEffectTimer(){}function cancelDeferredCardSeen(){return false;}
function syncCardSaveDirtyState(){}function perfMeasureAsync(_name,callback){return callback();}
function applySavedCardLocalPatch(){patches++;}function rememberCardModalCleanState(){clean++;}
function closeCardModal(){closes++;}function deferCardSeen(){throw new Error('stale seen');}
for(const reject of [false,true]){
 state.cardSaveInFlight=false;const old=saveCard();
 state.viewerStateGeneration++;state.cardSaveInFlight=false;const current=saveCard();
 const index=tasks.length-2;
 if(reject)tasks[index].reject(new Error('old failure'));else tasks[index].resolve({card:{id:'old'}});
 assert.equal(await old,false);assert.equal(state.cardSaveInFlight,true);assert.equal(els.saveCardButton.disabled,true);
 assert.equal(patches,0);assert.equal(closes,0);assert.equal(clean,0);assert.deepEqual(statuses,[]);
 tasks[index+1].resolve(null);assert.equal(await current,false);
 assert.equal(state.cardSaveInFlight,false);assert.equal(els.saveCardButton.disabled,false);
}
const valid=saveCard();tasks.at(-1).resolve({card:{id:'card-A'},meta:{changed:true}});
assert.equal(await valid,true);assert.equal(patches,1);assert.equal(closes,1);
""",
        )

    def test_card_creation_does_not_link_apply_or_refresh_into_new_viewer(self) -> None:
        self.run_node(
            functions(
                "app_main_before_printing.js",
                "captureCardEditingContext",
                "persistCardPayload",
                "ensureRepairOrderCard",
            ),
            """
state.editingId=null;state.activeCard=null;state.pendingCardClientId='client-A';
let applied=0,refreshed=0;function currentCardPayload(){return {title:'draft-A'};}
function applyCardModalState(){applied++;}async function refreshSnapshot(){refreshed++;}
function repairOrderCardRequiredMessage(){return 'missing';}
const first=deferred();api=async function(path,options){calls.push({path,options});return calls.length===1?first.promise:{card:{id:'created-A'}};};
const old=ensureRepairOrderCard();state.viewerStateGeneration++;state.operatorSessionToken='B';state.editingId='card-B';state.pendingCardClientId='client-B';
first.resolve({card:{id:'created-A'}});await old;
assert.equal(calls.length,1);assert.equal(applied,0);assert.equal(refreshed,0);assert.deepEqual(statuses,[]);
""",
        )

    def test_new_draft_after_close_cannot_receive_old_draft_result_in_same_session(self) -> None:
        self.run_node(
            functions(
                "app_main_before_printing.js",
                "captureCardEditingContext",
                "persistCardPayload",
                "ensureRepairOrderCard",
            ),
            """
state.editingId=null;state.pendingCardClientId='client-A';
function currentCardPayload(){return {title:'draft'};}
function applyCardModalState(){throw new Error('old draft applied');}
async function refreshSnapshot(){throw new Error('old refresh');}
const old=ensureRepairOrderCard();state.cardEditingGeneration=1;
calls[0].resolve({card:{id:'old-draft'}});assert.equal(await old,null);
assert.equal(calls.length,1);assert.deepEqual(statuses,[]);
""",
        )


if __name__ == "__main__":
    unittest.main()
