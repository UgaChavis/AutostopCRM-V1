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
class AsyncOwnershipContextRegressionTests(unittest.TestCase):
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

    def test_ai_enrichment_launch_cannot_cross_card_logout_or_same_id_aba(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        request_context = section(
            source,
            "    function beginCardScopedRequest(",
            "    function captureRepairOrderMutationContext(",
        )
        enrichment = section(
            source,
            "    async function runFullCardEnrichment()",
            "    async function syncAgentTaskEffects(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
global.HTMLElement=class HTMLElement {};
const calls=[],statuses=[],applied=[],scheduled=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',actor:'actor-A',
  cardEditingGeneration:1,cardHydrationSeq:1,editingId:'A',
  activeCard:{id:'A',updated_at:'A-r1',ai_autofill_prompt:'private-A'},
  cardEnrichmentRequest:null,agentTaskContext:null,agentTaskId:'',
  cardCleanupState:'idle',cardCleanupError:'',
};
const els={cardAgentButton:null,cardModal:{classList:{contains(){return true;}}}};
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function requireOperatorSession(){return Boolean(state.operatorSessionToken&&state.actor);}
function buildAiFullCardEnrichmentContextPacket(){return {card_id:state.editingId};}
function renderCardCleanupIndicator(){}
function applyCardModalState(card){applied.push(card);state.activeCard=card;}
function setStatus(message,isError){statuses.push({message,isError});}
function scheduleCardCleanupPolling(delay,context){scheduled.push({delay,context,taskId:state.agentTaskId});}
"""
            + request_context
            + enrichment
            + """
(async()=>{
  const staleCard=runFullCardEnrichment();
  assert.equal(calls.length,1);
  assert.equal(calls[0].options.body.card_id,'A');
  assert.equal(calls[0].options.body.actor_name,'actor-A');
  assert.equal(calls[0].options.body.prompt,'private-A');

  state.cardEnrichmentRequest=null;state.agentTaskContext=null;state.agentTaskId='';
  state.cardEditingGeneration++;state.cardHydrationSeq++;
  state.editingId='B';state.activeCard={id:'B',updated_at:'B-r1'};
  calls[0].resolve({card:{id:'A',title:'stale-A'},meta:{launched:true,task_id:'task-A'}});
  await staleCard;
  assert.equal(state.activeCard.id,'B');
  assert.deepEqual(applied,[]);assert.deepEqual(scheduled,[]);assert.deepEqual(statuses,[]);

  const staleLogout=runFullCardEnrichment();
  assert.equal(calls.length,2);
  state.viewerStateGeneration++;state.operatorSessionToken='';state.actor='';
  state.cardEnrichmentRequest=null;state.agentTaskContext=null;state.agentTaskId='';
  state.editingId=null;state.activeCard=null;
  calls[1].reject(new Error('private-A failure'));
  await staleLogout;
  assert.deepEqual(statuses,[],'logout leaked a stale AI error');

  state.operatorSessionToken='session-C';state.actor='actor-C';
  state.editingId='same';state.activeCard={id:'same',updated_at:'old-r1',ai_autofill_prompt:'old'};
  state.cardEditingGeneration++;state.cardHydrationSeq++;
  const oldSame=runFullCardEnrichment();
  state.cardEnrichmentRequest=null;state.agentTaskContext=null;state.agentTaskId='';
  state.cardEditingGeneration++;state.cardHydrationSeq++;
  state.editingId='same';state.activeCard={id:'same',updated_at:'new-r1',ai_autofill_prompt:'new'};
  const newSame=runFullCardEnrichment();
  calls[3].resolve({card:{id:'same',updated_at:'new-r2'},meta:{launched:true,task_id:'task-new'}});
  await newSame;
  calls[2].resolve({card:{id:'same',updated_at:'old-r2'},meta:{launched:true,task_id:'task-old'}});
  await oldSame;
  assert.equal(state.activeCard.updated_at,'new-r2');
  assert.equal(state.agentTaskId,'task-new');
  assert.equal(scheduled.length,1);assert.equal(scheduled[0].taskId,'task-new');
  assert.equal(statuses.length,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_ai_enrichment_poll_cannot_cross_card_session_or_same_id_aba(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        request_context = section(
            source,
            "    function beginCardScopedRequest(",
            "    function captureRepairOrderMutationContext(",
        )
        polling = section(
            source,
            "    function stopCardCleanupPolling()",
            "    function applyCardModalState(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[],synced=[],renders=[],scheduled=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',actor:'actor-A',
  cardEditingGeneration:1,cardHydrationSeq:1,editingId:'same',activeCard:{id:'same',updated_at:'old'},
  cardEnrichmentRequest:null,agentTaskContext:null,agentTaskId:'',agentSyncedTaskId:'',
  cardCleanupPollTimer:null,cardCleanupState:'running',cardCleanupError:'',
};
const window={clearTimeout(){},setTimeout(callback,delay){scheduled.push({callback,delay});return scheduled.length;}};
function api(path){const task=deferred();calls.push({path,...task});return task.promise;}
function renderCardCleanupIndicator(){renders.push({state:state.cardCleanupState,error:state.cardCleanupError});}
async function syncAgentTaskEffects(task,context){if(context.isCurrent())synced.push(task.id);}
"""
            + request_context
            + polling
            + """
(async()=>{
  const oldContext=beginCardScopedRequest('cardEnrichmentRequest');
  state.agentTaskContext=oldContext;state.agentTaskId='task-old';
  const oldPoll=refreshCardCleanupState(oldContext);
  assert.equal(calls.length,1);

  state.cardEnrichmentRequest=null;state.agentTaskContext=null;state.agentTaskId='';
  state.cardEditingGeneration++;state.cardHydrationSeq++;
  state.editingId='same';state.activeCard={id:'same',updated_at:'new'};
  const currentContext=beginCardScopedRequest('cardEnrichmentRequest');
  state.agentTaskContext=currentContext;state.agentTaskId='task-new';
  const currentPoll=refreshCardCleanupState(currentContext);
  calls[1].resolve({tasks:[{id:'task-new',status:'completed'}]});await currentPoll;
  calls[0].resolve({tasks:[{id:'task-old',status:'completed'}]});await oldPoll;
  assert.deepEqual(synced,['task-new']);
  assert.equal(state.cardCleanupState,'idle');
  assert.equal(state.agentTaskId,'');assert.equal(state.agentTaskContext,null);
  assert.equal(state.cardEnrichmentRequest,null);

  state.operatorSessionToken='session-B';state.actor='actor-B';
  state.cardEditingGeneration++;state.cardHydrationSeq++;
  const logoutContext=beginCardScopedRequest('cardEnrichmentRequest');
  state.agentTaskContext=logoutContext;state.agentTaskId='task-logout';state.cardCleanupState='running';
  const logoutPoll=refreshCardCleanupState(logoutContext);
  state.viewerStateGeneration++;state.operatorSessionToken='';
  state.cardEnrichmentRequest=null;state.agentTaskContext=null;state.agentTaskId='';
  calls[2].reject(new Error('private poll error'));await logoutPoll;
  assert.deepEqual(synced,['task-new']);
  assert.equal(state.cardCleanupState,'running','stale poll changed logged-out replacement state');
  assert.equal(scheduled.length,0,'stale poll scheduled another request');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_card_timer_response_is_owned_by_card_and_operator_session(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        request_context = section(
            source,
            "    function beginCardScopedRequest(",
            "    function captureRepairOrderMutationContext(",
        )
        timer_actions = section(
            source,
            "    function applyCardTimerOperationResult(",
            "    function clampSignalPart(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[],statuses=[],patches=[],renders=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',actor:'actor-A',
  cardEditingGeneration:1,cardHydrationSeq:1,editingId:'A',activeCard:{id:'A',updated_at:'A-r1'},
  cardTimerRequest:null,cardTimerSaving:false,cardTimerState:'inactive',activeCardIsFull:true,
};
const els={signalDays:{value:1},signalHours:{value:0}};
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function deadlineInput(){return {days:1,hours:0,minutes:0,seconds:0};}
function selectedTimerTotalSeconds(){return 86400;}
function renderCardTimerControls(){renders.push({id:state.editingId,saving:state.cardTimerSaving});}
function renderSignalPreview(){}
function scheduleCardSaveDirtyStateSync(){}
function applySavedCardLocalPatch(card){patches.push(card.id);}
function cardTimerState(card){return card.timer_state||'inactive';}
function secondsToParts(){return {days:1,hours:0};}
function syncCardSaveDirtyState(){}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + request_context
            + timer_actions
            + """
(async()=>{
  const staleStart=startCardTimerFromPanel();
  assert.equal(calls[0].path,'/api/start_card_timer');
  assert.equal(calls[0].options.body.card_id,'A');
  assert.equal(calls[0].options.body.actor_name,'actor-A');
  state.cardTimerRequest=null;state.cardTimerSaving=false;
  state.cardEditingGeneration++;state.cardHydrationSeq++;
  state.editingId='B';state.activeCard={id:'B',updated_at:'B-r1'};
  calls[0].resolve({card:{id:'A',updated_at:'A-r2',timer_state:'running'}});await staleStart;
  assert.equal(state.activeCard.id,'B');assert.deepEqual(patches,[]);assert.deepEqual(statuses,[]);

  const staleStop=stopCardTimerFromPanel();
  assert.equal(calls[1].path,'/api/stop_card_timer');
  assert.equal(calls[1].options.body.card_id,'B');
  state.viewerStateGeneration++;state.operatorSessionToken='session-C';state.actor='actor-C';
  state.cardTimerRequest=null;state.cardTimerSaving=false;
  calls[1].reject(new Error('private timer error'));await staleStop;
  assert.equal(state.activeCard.id,'B');assert.deepEqual(patches,[]);assert.deepEqual(statuses,[]);
  assert.equal(state.cardTimerSaving,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_create_and_link_client_cannot_mutate_replacement_card_or_session(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        request_context = section(
            source,
            "    function beginCardScopedRequest(",
            "    function captureRepairOrderMutationContext(",
        )
        create_client = section(
            source,
            "    async function createClientForCard(",
            "    async function createClientFromCardSuggestion(",
        )
        link_client = section(
            source,
            "    async function linkActiveCardToClient(",
            "    function archivedCardsTotal(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[],statuses=[],applied=[],refreshes=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',actor:'actor-A',
  cardEditingGeneration:1,cardHydrationSeq:1,editingId:'A',activeCard:{id:'A',updated_at:'A-r1'},
  cardClientMutationRequest:null,pendingCardClientId:'current-A',pendingCardClientVehicleId:'vehicle-A',
  pendingCreateClientVehicleFromCard:true,
};
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function cloneVehicleProfile(profile){return {...profile};}
function readVehicleProfileForm(){return {};}
function normalizedCardClientCreatePayload(payload){return {...payload};}
function profileHasVehicleIdentity(){return false;}
function vehiclePayloadFromProfile(){return {};}
function applyCardModalState(card){applied.push(card.id);state.activeCard=card;}
function hideClientSuggestions(){}
function refreshSnapshot(){refreshes.push(state.editingId);return Promise.resolve();}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + request_context
            + create_client
            + link_client
            + """
(async()=>{
  const staleCreate=createClientForCard({}, {display_name:'client-A'}, {createVehicleFromCard:false});
  assert.equal(calls[0].path,'/api/create_client');
  state.cardClientMutationRequest=null;state.cardEditingGeneration++;state.cardHydrationSeq++;
  state.editingId='B';state.activeCard={id:'B',updated_at:'B-r1'};
  state.pendingCardClientId='current-B';state.pendingCardClientVehicleId='vehicle-B';
  calls[0].resolve({client:{id:'created-A'}});assert.equal(await staleCreate,null);
  assert.equal(calls.length,1,'stale create issued a link write');
  assert.equal(state.pendingCardClientId,'current-B');assert.deepEqual(applied,[]);assert.deepEqual(statuses,[]);

  const currentThenStaleLink=createClientForCard({}, {display_name:'client-B'}, {createVehicleFromCard:false});
  calls[1].resolve({client:{id:'created-B'}});
  for(let index=0;index<5&&calls.length<3;index++)await Promise.resolve();
  assert.equal(calls[2].path,'/api/link_card_to_client');
  assert.equal(calls[2].options.body.card_id,'B');assert.equal(calls[2].options.body.client_id,'created-B');
  state.viewerStateGeneration++;state.operatorSessionToken='session-C';state.actor='actor-C';
  state.cardClientMutationRequest=null;state.cardEditingGeneration++;state.cardHydrationSeq++;
  state.editingId='C';state.activeCard={id:'C',updated_at:'C-r1'};
  state.pendingCardClientId='current-C';state.pendingCardClientVehicleId='vehicle-C';
  calls[2].resolve({card:{id:'B',client_id:'created-B'}});
  assert.equal(await currentThenStaleLink,null);
  assert.equal(state.activeCard.id,'C');assert.equal(state.pendingCardClientId,'current-C');
  assert.deepEqual(applied,[]);assert.deepEqual(refreshes,[]);assert.deepEqual(statuses,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_reversed_login_responses_cannot_restore_previous_credentials(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        login = section(
            source,
            "    async function loginOperator()",
            "    function handleIdentityCredentialInput(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[],rendered=[],feedback=[],statuses=[],busy=[];
const state={viewerStateGeneration:1,operatorLoginRequest:null};
const modalClasses=new Set(['is-open']);
const els={
  identityInput:{value:'operator-A',focus(){}},identityPassword:{value:'password-A',focus(){},select(){}},
  identityModal:{classList:{contains(name){return modalClasses.has(name);}}},
};
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function setOperatorLoginBusy(value){busy.push(value);}
function setOperatorLoginFeedback(message,tone){feedback.push({message,tone});}
function setStatus(message,isError){statuses.push({message,isError});}
function resetViewerScopedState(){state.viewerStateGeneration++;state.operatorLoginRequest=null;}
function renderOperatorProfile(data){rendered.push(data.user.username);}
function refreshSnapshot(){return Promise.resolve();}
function updateSnapshotStatusLine(){}
"""
            + login
            + """
(async()=>{
  const first=loginOperator();
  assert.equal(calls[0].options.body.username,'operator-A');

  state.viewerStateGeneration++;state.operatorLoginRequest=null;
  els.identityInput.value='operator-B';els.identityPassword.value='password-B';
  const second=loginOperator();
  assert.equal(calls[1].options.body.username,'operator-B');

  calls[1].resolve({user:{username:'operator-B'}});await second;
  calls[0].resolve({user:{username:'operator-A'}});await first;
  assert.deepEqual(rendered,['operator-B']);
  assert.deepEqual(statuses,[]);
  assert.equal(state.operatorLoginRequest,null);
  assert.equal(feedback.some(item=>String(item.message||'').includes('operator-A')),false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_attachment_download_is_discarded_after_session_change(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        download = section(
            source,
            "    async function downloadAttachment(",
            "    function setOperatorLoginGateOpen(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const pending=[],downloads=[];
const state={viewerStateGeneration:1,operatorSessionToken:'session-A',apiToken:'api-A'};
function fetchAttachmentBlob(url,options){const task=deferred();pending.push({url,options,...task});return task.promise;}
function extractDownloadName(response){return response.name;}
function triggerBlobDownload(blob,name){downloads.push({blob,name});}
"""
            + download
            + """
(async()=>{
  const stale=downloadAttachment('/private-A');
  state.viewerStateGeneration++;state.operatorSessionToken='session-B';state.apiToken='api-B';
  pending[0].resolve({response:{name:'private-A.pdf'},blob:{id:'blob-A'}});
  assert.equal(await stale,false);assert.deepEqual(downloads,[]);

  const current=downloadAttachment('/current-B');
  pending[1].resolve({response:{name:'current-B.pdf'},blob:{id:'blob-B'}});
  assert.equal(await current,true);
  assert.deepEqual(downloads,[{blob:{id:'blob-B'},name:'current-B.pdf'}]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_board_search_pending_response_and_cache_are_purged_by_reset(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        search = section(
            source,
            "    function boardSearchCacheKey(",
            "    async function openBoardSearchResult(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const BOARD_SEARCH_LIMIT=16,BOARD_SEARCH_CACHE_TTL_MS=20000,BOARD_SEARCH_DEBOUNCE_MS=90;
const calls=[],timers=[];
const state={viewerStateGeneration:1,operatorSessionToken:'session-A',boardSearch:{
  query:'secret',results:[{id:'private-cache'}],meta:{total_matches:1},loading:true,error:'',activeIndex:0,open:true,
  timer:null,controller:null,requestSeq:7,completedQuery:'secret',completedResults:[{id:'private-cache'}],
  completedMeta:{total_matches:1},completedAt:Date.now(),
}};
const els={boardSearchInput:{value:'secret',setAttribute(){}},boardSearchResults:null,boardSearchClearButton:null};
const window={
  clearTimeout(timer){const found=timers.find(item=>item.id===timer);if(found)found.cleared=true;},
  setTimeout(callback,delay){const timer={id:timers.length+1,callback,delay,cleared:false};timers.push(timer);return timer.id;},
};
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function finiteNumber(value,fallback=0){const number=Number(value);return Number.isFinite(number)?number:fallback;}
function finiteNonNegativeNumber(value,fallback=0){return Math.max(0,finiteNumber(value,fallback));}
function escapeHtml(value){return String(value);}
function boardCardDescription(){return '';}
"""
            + search
            + """
(async()=>{
  const stale=loadBoardSearch('secret',7);
  const oldController=state.boardSearch.controller;
  assert.equal(calls.length,1);

  state.viewerStateGeneration++;state.operatorSessionToken='session-B';
  clearBoardSearchState({keepCache:false});
  assert.equal(oldController.signal.aborted,true);
  assert.equal(state.boardSearch.requestSeq,8);
  assert.equal(state.boardSearch.completedQuery,'');
  assert.deepEqual(state.boardSearch.completedResults,[]);
  assert.deepEqual(state.boardSearch.results,[]);

  state.boardSearch.query='secret';state.boardSearch.loading=true;state.boardSearch.open=true;
  const current=loadBoardSearch('secret',8);
  const currentController=state.boardSearch.controller;
  calls[0].resolve({cards:[{id:'private-A'}],meta:{total_matches:1}});await stale;
  assert.equal(state.boardSearch.controller,currentController,'stale finally cleared current controller');
  assert.deepEqual(state.boardSearch.results,[],'stale search restored private results');
  assert.equal(state.boardSearch.completedQuery,'','stale search restored private cache');

  calls[1].resolve({cards:[{id:'current-B'}],meta:{total_matches:1}});await current;
  assert.deepEqual(state.boardSearch.results,[{id:'current-B'}]);
  assert.deepEqual(state.boardSearch.completedResults,[{id:'current-B'}]);
  assert.equal(state.boardSearch.completedQuery,'secret');

  clearBoardSearchState({keepCache:false});
  els.boardSearchInput.value='secret';scheduleBoardSearch();
  assert.equal(state.boardSearch.loading,true);
  assert.deepEqual(state.boardSearch.results,[],'reset reused a previous viewer cache');
  assert.equal(state.boardSearch.completedQuery,'');
  assert.ok(state.boardSearch.timer,'reset search did not schedule a fresh request');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_display_dashboard_open_cannot_complete_after_logout(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        viewer_context = section(
            source,
            "    function captureViewerRequestContext(",
            "    function operatorHasPermission(",
        )
        open_editor = section(
            source,
            "    async function openDisplayDashboardMessageEditor()",
            "    function rememberDisplayDashboardSelection(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[],opened=[],statuses=[],loadedImages=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',apiToken:'api-A',actor:'actor-A',
  displayDashboardContextGeneration:0,displayDashboardOpenRequest:null,
  displayDashboardMessage:{revision:'current'},displayDashboardExistingImageIds:['current-image'],
  displayDashboardSelectionRange:null,modalStack:[{key:'settings'}],
};
const els={
  displayDashboardMessageEditor:{innerHTML:'CURRENT',focus(){}},displayDashboardMessageSaveButton:{disabled:false},
  displayDashboardEmojiPalette:{hidden:false},displayDashboardEmojiButton:{setAttribute(){}},
  displayDashboardMessageModal:{},
};
function api(path){const task=deferred();calls.push({path,...task});return task.promise;}
function requireOperatorSession(){return Boolean(state.operatorSessionToken&&state.actor);}
function normalizedDisplayDashboardMessage(value){return {...value,image_file_ids:[...(value?.image_file_ids||[])]};}
function clearDisplayDashboardImageDrafts(){throw new Error('stale open cleared replacement dashboard drafts');}
function pushModal(...args){opened.push(args);}
function renderDisplayDashboardImageDrafts(){throw new Error('stale open rendered replacement dashboard');}
function loadDisplayDashboardExistingImages(...args){loadedImages.push(args);}
function setStatus(message,isError){statuses.push({message,isError});}
function requestAnimationFrame(callback){callback();}
"""
            + viewer_context
            + open_editor
            + """
(async()=>{
  const stale=openDisplayDashboardMessageEditor();
  assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/get_display_dashboard');
  const replacementRequest={replacement:true};
  state.viewerStateGeneration++;state.operatorSessionToken='';state.actor='';
  state.displayDashboardContextGeneration++;state.displayDashboardOpenRequest=replacementRequest;
  state.displayDashboardMessage={revision:'replacement'};
  state.displayDashboardExistingImageIds=['replacement-image'];
  els.displayDashboardMessageEditor.innerHTML='REPLACEMENT';
  calls[0].resolve({message_board:{revision:'private-A',body_html:'PRIVATE',image_file_ids:['private-image']}});
  await stale;
  assert.deepEqual(state.displayDashboardMessage,{revision:'replacement'});
  assert.deepEqual(state.displayDashboardExistingImageIds,['replacement-image']);
  assert.equal(els.displayDashboardMessageEditor.innerHTML,'REPLACEMENT');
  assert.equal(state.displayDashboardOpenRequest,replacementRequest,'stale finally cleared replacement request');
  assert.deepEqual(opened,[]);assert.deepEqual(loadedImages,[]);assert.deepEqual(statuses,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_display_dashboard_upload_captures_actor_and_close_stops_next_write(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        viewer_context = section(
            source,
            "    function captureViewerRequestContext(",
            "    function operatorHasPermission(",
        )
        close_editor = section(
            source,
            "    function closeDisplayDashboardMessageEditor()",
            "    function appendDisplayDashboardImagePreview(",
        )
        save_editor = section(
            source,
            "    async function uploadDisplayDashboardImage(",
            "    function handleBoardScaleInput(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const firstBuffer=deferred(),calls=[],statuses=[];
let secondBufferReads=0,modalOpen=true,closeCount=0;
const files=[
  {name:'one.png',type:'image/png',arrayBuffer(){return firstBuffer.promise;}},
  {name:'two.png',type:'image/png',arrayBuffer(){secondBufferReads++;return Promise.resolve(new Uint8Array([2]).buffer);}},
];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',apiToken:'api-A',actor:'actor-A',
  displayDashboardContextGeneration:4,displayDashboardOpenRequest:null,displayDashboardSaveRequest:null,
  displayDashboardMessage:{revision:'revision-A'},displayDashboardMessageSaving:false,
  displayDashboardPendingImages:files.map((file,index)=>({key:String(index),file,url:''})),
  displayDashboardExistingImageIds:['existing-A'],displayDashboardExistingImageUrls:new Map(),
  displayDashboardSelectionRange:null,snapshot:{settings:{old:true}},
};
const els={
  displayDashboardMessageEditor:{innerHTML:'<b>PRIVATE A</b>'},displayDashboardMessageSaveButton:{disabled:false},
  displayDashboardImageInput:{value:'selected'},displayDashboardMessageImages:{innerHTML:'drafts'},
  displayDashboardEmojiPalette:{hidden:false},displayDashboardEmojiButton:{setAttribute(){}},
};
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function isModalOpen(key){return key==='display-dashboard-message'&&modalOpen;}
function clearDisplayDashboardImageDrafts(){state.displayDashboardPendingImages=[];state.displayDashboardExistingImageIds=[];}
function popModal(key){if(key==='display-dashboard-message'){modalOpen=false;closeCount++;}}
function arrayBufferToBase64(){return 'AQ==';}
function normalizedDisplayDashboardMessage(value){return value||{revision:''};}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + viewer_context
            + close_editor
            + save_editor
            + """
(async()=>{
  const saving=saveDisplayDashboardMessage();
  await Promise.resolve();
  state.actor='actor-B';
  firstBuffer.resolve(new Uint8Array([1]).buffer);
  for(let index=0;index<5&&calls.length===0;index++)await Promise.resolve();
  assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/upload_shared_file');
  assert.equal(calls[0].options.body.actor_name,'actor-A','upload used a later actor');
  assert.equal(calls[0].options.body.file_name,'one.png');

  closeDisplayDashboardMessageEditor();
  state.displayDashboardMessage={revision:'replacement-B'};
  calls[0].resolve({file:{id:'uploaded-A'}});
  await saving;
  assert.equal(calls.length,1,'closed editor issued another upload or settings write');
  assert.equal(secondBufferReads,0,'closed editor read the next private image');
  assert.deepEqual(state.displayDashboardMessage,{revision:'replacement-B'});
  assert.equal(closeCount,1);assert.deepEqual(statuses,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_admin_users_and_report_responses_cannot_cross_logout(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        viewer_context = section(
            source,
            "    function captureViewerRequestContext(",
            "    function operatorHasPermission(",
        )
        modal_loader = section(
            source,
            "    async function loadModalData(",
            "    function clientDisplayName(",
        )
        admin_loaders = section(
            source,
            "    async function reloadOperatorAdminUsers(",
            "    function handleAdminUsersListClick(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[],rendered=[],opened=[],downloads=[],statuses=[];
const state={viewerStateGeneration:1,operatorSessionToken:'session-A',apiToken:'api-A',actor:'admin-A',operatorAdminRequestSeq:0};
const els={operatorAdminModal:{}};
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function perfMeasureAsync(_name,callback){return callback();}
function maybeOpenModal(modal,open){if(open)opened.push(modal);}
function setStatus(message,isError){statuses.push({message,isError});}
function loadEmployeesReference(){return Promise.resolve();}
function renderOperatorUsers(data){rendered.push(data);}
function loadOperatorProfile(){throw new Error('profile refresh was not requested');}
function setOperatorAdminTab(){throw new Error('stale admin response selected a tab');}
function pushModal(){throw new Error('stale admin response reopened the modal');}
function openTextBlobWindow(text,fileName){downloads.push({text,fileName});}
"""
            + viewer_context
            + modal_loader
            + admin_loaders
            + """
(async()=>{
  const users=refreshOperatorAdminSurfaces({openAdminModal:true});
  for(let index=0;index<5&&calls.length===0;index++)await Promise.resolve();
  assert.equal(calls[0].path,'/api/list_operator_users');
  state.viewerStateGeneration++;state.operatorSessionToken='';state.actor='';state.operatorAdminRequestSeq++;
  calls[0].resolve({users:[{username:'private-admin-A'}]});
  assert.equal(await users,false);
  assert.deepEqual(rendered,[]);assert.deepEqual(opened,[]);assert.deepEqual(statuses,[]);

  state.viewerStateGeneration++;state.operatorSessionToken='session-B';state.actor='admin-B';
  const report=openOperatorUserReport('private-admin-A');
  assert.match(calls[1].path,/get_operator_user_report/);
  state.viewerStateGeneration++;state.operatorSessionToken='';state.actor='';
  calls[1].resolve({text:'PRIVATE REPORT A',file_name:'private-A.txt'});await report;
  assert.deepEqual(downloads,[]);assert.deepEqual(statuses,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_client_profile_load_rejects_a_to_b_and_same_id_aba_responses(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        select_client = section(
            source,
            "    async function selectClient(",
            "    function activeClientVehicles(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[],profiles=[],statuses=[];
const state={viewerStateGeneration:1,clientsProfileRequestSeq:0,clientsActiveId:'',clientVehicleEditor:null};
function api(path){const task=deferred();calls.push({path,...task});return task.promise;}
function renderClientProfileEmptyState(){}
function renderClientProfile(data){profiles.push({id:data.client.id,revision:data.client.revision});}
function renderClientsList(){}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + select_client
            + """
(async()=>{
  const a=selectClient('A'),b=selectClient('B');
  calls[1].resolve({client:{id:'B',revision:'B-current'}});await b;
  calls[0].resolve({client:{id:'A',revision:'A-stale'}});await a;
  assert.deepEqual(profiles,[{id:'B',revision:'B-current'}]);assert.equal(state.clientsActiveId,'B');

  const oldSame=selectClient('same');
  const away=selectClient('away');
  const newSame=selectClient('same');
  calls[4].resolve({client:{id:'same',revision:'same-new'}});await newSame;
  calls[2].resolve({client:{id:'same',revision:'same-old'}});await oldSame;
  calls[3].resolve({client:{id:'away',revision:'away-stale'}});await away;
  assert.deepEqual(profiles,[{id:'B',revision:'B-current'},{id:'same',revision:'same-new'}]);
  assert.equal(state.clientsActiveId,'same');assert.deepEqual(statuses,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_client_vehicle_mutation_rejects_a_to_b_and_same_id_aba(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        viewer_context = section(
            source,
            "    function captureViewerRequestContext(",
            "    function operatorHasPermission(",
        )
        vehicle_mutation = section(
            source,
            "    function beginClientMutation(",
            "    async function deleteClientVehicle(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[],selected=[],statuses=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',apiToken:'api-A',actor:'actor-A',
  clientsActiveId:'A',clientsProfileRequestSeq:1,clientMutationRequest:null,clientVehicleEditor:{key:'__new__'},
};
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function findActiveClientVehicle(){return null;}
function readClientVehicleEditorPayload(){return {vehicle:'Vehicle A',vin:'VIN-A',license_plate:''};}
async function selectClient(id){selected.push(id);}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + viewer_context
            + vehicle_mutation
            + """
(async()=>{
  const staleA=saveClientVehicleFromEditor('__new__');
  assert.equal(calls[0].options.body.client_id,'A');
  state.clientsActiveId='B';state.clientsProfileRequestSeq++;
  calls[0].resolve({vehicle:{id:'vehicle-A'}});await staleA;
  assert.deepEqual(selected,[]);assert.deepEqual(statuses,[]);
  assert.deepEqual(state.clientVehicleEditor,{key:'__new__'});

  state.clientsActiveId='same';state.clientsProfileRequestSeq=10;state.clientMutationRequest=null;
  state.clientVehicleEditor={key:'__new__'};
  const oldSame=saveClientVehicleFromEditor('__new__');
  state.clientsActiveId='away';state.clientsProfileRequestSeq=11;
  state.clientsActiveId='same';state.clientsProfileRequestSeq=12;
  calls[1].resolve({vehicle:{id:'same-old'}});await oldSame;
  assert.deepEqual(selected,[]);assert.deepEqual(statuses,[]);
  assert.deepEqual(state.clientVehicleEditor,{key:'__new__'});
  assert.equal(state.clientMutationRequest,null);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )

    def test_sticky_double_create_and_late_edit_are_request_owned(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        viewer_context = section(
            source,
            "    function captureViewerRequestContext(",
            "    function operatorHasPermission(",
        )
        sticky_modal = section(
            source,
            "    function openStickyModal(",
            "    function handleEmployeesModalOverlayClick(",
        )
        sticky_save = section(
            source,
            "    function buildStickyPayload(",
            "    async function removeSticky(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
const calls=[],statuses=[],applied=[];
let modalOpen=false;
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',apiToken:'api-A',actor:'actor-A',
  stickyEditorGeneration:0,stickyMutationRequest:null,stickyDraft:null,
};
const els={
  stickyModal:{},stickyModalTitle:{textContent:''},stickyText:{value:'',focus(){}},
  stickyDays:{value:0},stickyHours:{value:4},saveStickyButton:{disabled:false},
};
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function secondsToParts(total){return {days:Math.floor(total/86400),hours:Math.floor((total%86400)/3600)};}
function stickyPayload(){return {text:String(els.stickyText.value||'').trim()};}
function stickyDeadlineInput(){return {days:Number(els.stickyDays.value),hours:Number(els.stickyHours.value)};}
function getStickyComposerPlacement(){return {x:10,y:20};}
function pushModal(){modalOpen=true;}
function popModal(){modalOpen=false;}
function isModalOpen(key){return key==='sticky'&&modalOpen;}
function setTimeout(callback){callback();return 1;}
function applyStickySnapshot(stickies){applied.push(stickies);return true;}
function refreshSnapshot(){throw new Error('snapshot fallback should not run');}
function setStatus(message,isError){statuses.push({message,isError});}
"""
            + viewer_context
            + sticky_modal
            + sticky_save
            + """
(async()=>{
  openStickyModal();els.stickyText.value='new sticky';
  const first=saveSticky(),duplicate=saveSticky();
  assert.equal(calls.length,1,'double click issued two create writes');
  assert.equal(calls[0].path,'/api/create_sticky');
  assert.equal(calls[0].options.body.actor_name,'actor-A');
  calls[0].resolve({stickies:[{id:'created',text:'new sticky'}]});
  await Promise.all([first,duplicate]);
  assert.equal(modalOpen,false);assert.equal(statuses.length,1);

  openStickyModal({id:'sticky-A',text:'A',x:1,y:2,deadline_total_seconds:3600});
  els.stickyText.value='edit A';
  const staleEdit=saveSticky();
  assert.equal(calls[1].path,'/api/update_sticky');assert.equal(calls[1].options.body.sticky_id,'sticky-A');
  closeStickyModal();
  openStickyModal({id:'sticky-B',text:'B',x:3,y:4,deadline_total_seconds:7200});
  calls[1].resolve({stickies:[{id:'sticky-A',text:'edited A'}]});await staleEdit;
  assert.equal(modalOpen,true);assert.equal(state.stickyDraft.id,'sticky-B');
  assert.equal(els.stickyText.value,'B');
  assert.equal(applied.length,1,'late edit patched the replacement sticky workspace');
  assert.equal(statuses.length,1,'late edit reported status in the replacement workspace');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )


if __name__ == "__main__":
    unittest.main()
