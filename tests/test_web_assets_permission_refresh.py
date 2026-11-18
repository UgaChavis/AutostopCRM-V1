from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from tests.web_asset_function_support import functions

ROOT = Path(__file__).resolve().parents[1]
if __package__:
    from tests.source_path_support import prepend_source_path
else:
    from source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.web_app_assets.module_assets import read_board_source


def section(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset)]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class PermissionRefreshRuntimeTests(unittest.TestCase):
    def run_js(self, script: str) -> None:
        if "persistOperatorUserSave(" in script:
            script = functions("app_main_before_printing.js", "cardFilesApi") + script
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

    def test_profile_reads_coalesce_and_confirmed_mismatch_immediately_replaces_pending_read(
        self,
    ) -> None:
        source = read_board_source("app_main_before_printing.js")
        code = section(
            source,
            "    async function loadOperatorProfile(",
            "    async function openOperatorWorkspace(",
        )
        self.run_js(
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:1,operatorSessionToken:'S',personalBoardPreferencesRevision:1};
const calls=[],rendered=[];
const document={hidden:false};
function api(path,{readController}){return new Promise(resolve=>calls.push({resolve,signal:readController.signal}));}
function renderOperatorProfile(data,options){state.operatorProfile=data;rendered.push(options);}
"""
            + code
            + """
(async()=>{
  const startup=loadOperatorProfile();const foreground=loadOperatorProfile();
  const visibility=refreshOperatorProfileIfDue({force:true});
  assert.equal(calls.length,1);
  const newer=refreshOperatorProfileAfterPermissionMismatch('S','/api/list_cashboxes');
  const duplicate=refreshOperatorProfileAfterPermissionMismatch('S','/api/list_employees');
  assert.equal(calls.length,2,'one fresh permission fetch must start immediately');
  assert.equal(calls[0].signal.aborted,true,'the pre-change fetch is obsolete');
  calls[0].resolve({user:{permissions:[]}});
  await Promise.resolve();
  assert.deepEqual(rendered,[],'pre-change permissions must not be applied');
  calls[1].resolve({user:{permissions:['employees_read_access']}});
  await Promise.all([startup,foreground,visibility,newer,duplicate]);
  assert.equal(rendered.length,1);assert.deepEqual(state.operatorProfile.user.permissions,['employees_read_access']);
  const preWrite=refreshOperatorProfileAfterPermissionMismatch('S','/api/list_employees');
  const afterWrite=loadOperatorProfile({fresh:true,afterChange:true});
  assert.equal(calls.length,4,'a confirmed write must supersede a pre-write read');
  assert.equal(calls[2].signal.aborted,true);
  calls[2].resolve({user:{permissions:['employees_read_access']}});
  calls[3].resolve({user:{permissions:[]}});
  await Promise.all([preWrite,afterWrite]);
  assert.equal(rendered.length,2);
  assert.deepEqual(state.operatorProfile.user.permissions,[]);
  const firstDenial=refreshOperatorProfileAfterPermissionMismatch('S','/api/get_cashbox',true);
  const secondDenial=refreshOperatorProfileAfterPermissionMismatch('S','/api/get_payroll_report',true);
  const repeatDenial=refreshOperatorProfileAfterPermissionMismatch('S','/api/get_payroll_report',false);
  assert.equal(calls.length,6,'replace a profile sampled before the second confirmed permission change');
  assert.equal(calls[4].signal.aborted,true);
  calls[4].resolve({user:{permissions:['employees_read_access']}});
  calls[5].resolve({user:{permissions:[]}});
  await Promise.all([firstDenial,secondDenial,repeatDenial]);
  assert.equal(rendered.length,3);
  assert.deepEqual(state.operatorProfile.user.permissions,[]);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        )

    def test_api_only_reacts_to_current_session_auth_failures(self) -> None:
        source = read_board_source("app_main_before_printing.js")
        code = section(source, "    async function api(", "    function setApiToken(")
        permissions = section(
            source,
            "    function operatorHasPermission(",
            "    function operatorHasEmployeesReadOnlyAccess(",
        )
        self.run_js(
            """
const assert=require('node:assert/strict');
const EMPLOYEES_CASHBOXES_ACCESS_PERMISSION='employees_cashboxes_access';
const EMPLOYEES_READ_ACCESS_PERMISSION='employees_read_access';
const SALARY_BALANCE_RESET_PERMISSION='salary_balance_reset';
const grants=[EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,EMPLOYEES_READ_ACCESS_PERMISSION];
const state={viewerStateGeneration:1,operatorSessionToken:'S',operatorProfileDenials:new Set(),operatorProfile:{user:{permissions:grants}}};
const requests=[],effects=[];
const window=globalThis;
const API_READ_RETRY_LIMIT=1;
function apiReadTimeoutMs(){return 10000;}
function perfStart(){}
function perfEnd(){}
function clearOperatorSession(){effects.push('logout');}
function openOperatorLoginModal(){effects.push('login');}
function refreshOperatorProfileAfterPermissionMismatch(){effects.push('profile');return new Promise(()=>{});}
function notifyCashboxesMutation(){}
function syncEmployeesCashboxesAccessUi(){effects.push('sync');}
function fetch(){return new Promise(resolve=>requests.push(resolve));}
function errorResponse(status,code,permission){
  return {status,headers:new Headers(),text:async()=>JSON.stringify({
    ok:false,error:{code,message:code,details:{auth_type:'operator_session',permission}},
  })};
}
function referencesResponse(){
  return {ok:true,status:200,headers:new Headers(),text:async()=>JSON.stringify({
    ok:true,data:{meta:{references_only:true}},
  })};
}
"""
            + code
            + permissions
            + """
(async()=>{
  const stale=api('/api/list_cashboxes');state.viewerStateGeneration++;
  requests.shift()(errorResponse(401,'unauthorized'));
  await assert.rejects(stale,{status:401});
  const stalePermission=api('/api/list_cashboxes');state.operatorSessionToken='T';
  requests.shift()(errorResponse(403,'forbidden'));
  await assert.rejects(stalePermission,{status:403});
  assert.deepEqual(effects,[]);
  const superseded=new AbortController();
  const staleProfile=api('/api/get_operator_profile',{readController:superseded,retryRead:false});
  superseded.abort();
  requests.shift()(errorResponse(401,'unauthorized'));
  await assert.rejects(staleProfile,{status:401});
  assert.deepEqual(effects,[],'a superseded read must not log out the same token');
  const permission=api('/api/list_cashboxes');
  requests.shift()(errorResponse(403,'forbidden'));
  await assert.rejects(permission,{status:403});
  assert.deepEqual(effects,['profile']);
  assert.equal(operatorCanAccessEmployeesCashboxes(),true);
  const confirmed=api('/api/get_cashbox');
  requests.shift()(errorResponse(403,'forbidden',EMPLOYEES_CASHBOXES_ACCESS_PERMISSION));
  await assert.rejects(confirmed,{status:403});
  assert.deepEqual(effects,['profile','sync','profile']);
  assert.equal(operatorCanAccessEmployeesCashboxes(),false);
  assert.equal(operatorHasPermission(EMPLOYEES_READ_ACCESS_PERMISSION),true);
  state.operatorProfile={user:{permissions:grants}};
  const explicitReferences=api('/api/list_employees?references_only=true');
  requests.shift()(referencesResponse());
  await explicitReferences;
  assert.equal(operatorCanAccessEmployeesCashboxes(),true);
  const unexpectedReferences=api('/api/list_cashboxes?limit=200');
  requests.shift()(referencesResponse());
  await unexpectedReferences;
  assert.equal(operatorCanAccessEmployeesCashboxes(),false);
  assert.equal(operatorHasPermission(EMPLOYEES_READ_ACCESS_PERMISSION),true);
  const oldReferences=api('/api/list_cashboxes');
  const oldDenial=api('/api/get_payroll_report');
  state.operatorProfileDenials=new Set();
  state.operatorProfile={user:{permissions:grants}};
  requests.shift()(referencesResponse());
  await oldReferences;
  requests.shift()(errorResponse(403,'forbidden',EMPLOYEES_READ_ACCESS_PERMISSION));
  await assert.rejects(oldDenial,{status:403});
  assert.equal(operatorCanAccessEmployeesCashboxes(),true,'late evidence cannot erase a fresh permission grant');
  state.operatorProfile={user:{permissions:[EMPLOYEES_CASHBOXES_ACCESS_PERMISSION]}};
  const inherited=api('/api/get_payroll_report');
  requests.shift()(errorResponse(403,'forbidden',EMPLOYEES_READ_ACCESS_PERMISSION));
  await assert.rejects(inherited,{status:403});
  assert.equal(operatorCanViewEmployees(),false,'both possible grants must disappear after employee read denial');
  assert.equal(operatorCanAccessEmployeesCashboxes(),false);
  state.operatorProfile={user:{permissions:[EMPLOYEES_READ_ACCESS_PERMISSION]}};
  const readOnlyRevoked=api('/api/list_employees');
  requests.shift()(referencesResponse());
  await readOnlyRevoked;
  assert.equal(operatorCanViewEmployees(),false,'reference fallback also confirms loss of read-only access');
  const expired=api('/api/list_cashboxes');
  requests.shift()(errorResponse(401,'unauthorized'));
  await assert.rejects(expired,{status:401});
  assert.deepEqual(effects,['profile','sync','profile','sync','profile','profile','profile','sync','profile','sync','profile','logout']);
  state.operatorProfileDenials=new Set();
  state.operatorProfile={user:{permissions:[EMPLOYEES_CASHBOXES_ACCESS_PERMISSION]}};
  assert.equal(revokeOperatorPermission(EMPLOYEES_CASHBOXES_ACCESS_PERMISSION),true);
  assert.equal(revokeOperatorPermission(EMPLOYEES_READ_ACCESS_PERMISSION),true,
    'a distinct denial invalidates a pending profile even if access was already hidden');
  assert.equal(revokeOperatorPermission(EMPLOYEES_READ_ACCESS_PERMISSION),false);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )

    def test_bootstrap_keeps_session_on_network_error_and_ignores_stale_failure(self) -> None:
        source = read_board_source("app_main_before_printing.js")
        viewer = section(
            source,
            "    function captureViewerRequestContext(",
            "    function operatorHasPermission(",
        )
        bootstrap = section(
            source,
            "    async function bootstrapOperatorSession(",
            "    function normalizeConnectionState(",
        )
        self.run_js(
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:1,operatorSessionToken:'S'};
const requests=[],events=[];
function loadOperatorProfile(){return new Promise((_resolve,reject)=>requests.push(reject));}
function updateOperatorButton(){}
function setStatus(message){events.push(message);}
function clearOperatorSession(){throw new Error('network failures must not clear an active session');}
"""
            + viewer
            + bootstrap
            + """
(async()=>{
  const startup=bootstrapOperatorSession();
  requests.shift()(new Error('network'));
  await startup;
  assert.equal(state.operatorSessionToken,'S');
  assert.deepEqual(events,['network']);
  const stale=bootstrapOperatorSession();
  state.viewerStateGeneration++;
  state.operatorSessionToken='T';
  requests.shift()(new Error('old network error'));
  await stale;
  assert.equal(state.operatorSessionToken,'T');
  assert.deepEqual(events,['network']);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )

    def test_delayed_logout_cannot_clear_replacement_session(self) -> None:
        self.run_js(
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:1,operatorSessionToken:'S'};
let finishLogout;const cleared=[];
function api(){return new Promise(resolve=>{finishLogout=resolve;});}
function clearOperatorSession(){cleared.push(state.operatorSessionToken);}
"""
            + functions(
                "app_main_before_printing.js", "captureViewerRequestContext", "logoutOperator"
            )
            + """
(async()=>{
  const old=logoutOperator();
  state.viewerStateGeneration++;
  state.operatorSessionToken='T';
  finishLogout();
  await old;
  assert.deepEqual(cleared,[]);
  const current=logoutOperator();
  finishLogout();
  await current;
  assert.deepEqual(cleared,['T']);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )

    def test_cash_notification_poll_yields_to_foreground_load_and_receipt(self) -> None:
        self.run_js(
            """
const assert=require('node:assert/strict');
const state={
  actor:'A',operatorSessionToken:'S',viewerStateGeneration:1,
  employeesCashboxesAccessRevision:1,cashboxNotificationRevision:1,cashboxesRequestSeq:0,
};
const document={hidden:false};
const requests=[],notifications=[];
function api(){return new Promise(resolve=>requests.push(resolve));}
function operatorCanAccessEmployeesCashboxes(){return true;}
function applyCashboxNotification(notification){notifications.push(notification);}
"""
            + functions(
                "app_main_before_printing.js",
                "captureViewerRequestContext",
                "refreshCashboxNotification",
            )
            + """
(async()=>{
  const data={notification:{initialized:true}};
  const beforeForeground=refreshCashboxNotification();
  state.cashboxesRequestSeq++;
  requests.shift()(data);
  await beforeForeground;
  const beforeReceipt=refreshCashboxNotification();
  state.cashboxNotificationSeenPromise=Promise.resolve();
  requests.shift()(data);
  await beforeReceipt;
  assert.deepEqual(notifications,[]);
  state.cashboxNotificationSeenPromise=null;
  const unavailable=refreshCashboxNotification();
  requests.shift()({notification:null,meta:{notification_unavailable:true}});
  await unavailable;
  assert.deepEqual(notifications,[],'temporary server degradation must leave the indicator untouched');
  const current=refreshCashboxNotification();
  requests.shift()(data);
  await current;
  assert.deepEqual(notifications,[data.notification]);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )

    def test_poll_reads_only_changed_detail_and_leaves_covered_drafts_alone(self) -> None:
        self.run_js(
            """
const assert=require('node:assert/strict');
const state={
  actor:'A',operatorSessionToken:'S',viewerStateGeneration:1,employeesCashboxesAccessRevision:1,
  cashboxNotificationRevision:0,cashboxesRequestSeq:0,activeCashboxId:'second',modalStack:[{key:'cashboxes'}],
  activeCashbox:{cashbox:{id:'second',updated_at:'v1'},meta:{transaction_limit:100}},
};
const document={hidden:false}, requests=[], details=[];
function operatorCanAccessEmployeesCashboxes(){return true;}
function cashboxWorkspaceIsOpen(){return true;}
function applyCashboxNotification(n){state.cashboxNotification=n;state.cashboxNotificationRevision++;}
function api(path){return new Promise(resolve=>requests.push({path,resolve}));}
async function loadCashboxDetail(id,opts){details.push([id,opts.background]);}
"""
            + functions(
                "app_main_before_printing.js",
                "captureViewerRequestContext",
                "cashboxDetailIsExposed",
                "refreshCashboxNotification",
            )
            + """
(async()=>{
  const response=rev=>({notification:{initialized:true},meta:{active_cashbox_updated_at:rev}});
  const first=refreshCashboxNotification();
  const duplicate=refreshCashboxNotification();
  assert.equal(requests.length,1);
  assert.equal(requests[0].path,'/api/list_cashboxes?limit=1&cashbox_id=second');
  requests.shift().resolve(response('v1'));await Promise.all([first,duplicate]);
  assert.deepEqual(details,[]);
  const covered=refreshCashboxNotification();
  state.modalStack.push({key:'cashbox-transfer'});
  requests.shift().resolve(response('v2'));await covered;
  assert.deepEqual(details,[]);
  state.modalStack.pop();
  const fresh=refreshCashboxNotification();
  requests.shift().resolve(response('v2'));await fresh;
  assert.deepEqual(details,[['second',true]]);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )

    def test_cash_notification_frames_and_conflict_or_uncertain_readback(self) -> None:
        self.run_js(
            """
const assert=require('node:assert/strict');
const notice=(through,ids=[through],total=ids.length)=>({
  initialized:true,by_cashbox:{box:{through_transaction_id:through,needs_ack:true,unread_transaction_ids:ids,unread_count:total}}
});
const state={
  actor:'A',operatorSessionToken:'S',viewerStateGeneration:1,activeCashboxId:'box',mobileView:'cashboxes',
  employeesCashboxesAccessRevision:1,cashboxNotificationRevision:0,cashboxesRequestSeq:0,
  cashboxNotification:{initialized:true,has_unread:true},
  activeCashbox:{transactions:[{id:'tx'}]},
  cashboxNotificationForOpen:{cashboxId:'box',notification:notice('tx')},
};
const document={hidden:false};
const CASHBOX_NOTIFICATION_ACK_TIMEOUT_MS=10000;
const MOBILE_CASHBOX_TRANSACTION_LIMIT=5;
const requests=[],frames=[];
const window={setTimeout,clearTimeout,requestAnimationFrame:callback=>frames.push(callback)};
function api(path,options){return new Promise((resolve,reject)=>requests.push({path,body:options.body,resolve,reject}));}
function operatorCanAccessEmployeesCashboxes(){return true;}
function renderCashboxNotificationIndicator(){}
function cashboxWorkspaceIsOpen(){return true;}
function isCurrentCashboxesLoadIntent(){return true;}
"""
            + functions(
                "app_main_before_printing.js",
                "captureViewerRequestContext",
                "cashboxNotificationViewerGuard",
                "applyCashboxNotification",
                "filteredCashboxTransactions",
                "visibleCashboxTransactions",
                "paintedCashboxBoundary",
                "cashboxDetailIsExposed",
                "acknowledgeCashboxNotifications",
                "scheduleCashboxNotificationAcknowledgement",
                "prepareCashboxAcknowledgement",
                "refreshCashboxNotification",
            )
            + """
(async()=>{
  const oldPoll=refreshCashboxNotification();
  scheduleCashboxNotificationAcknowledgement({},'box');
  frames.shift()();
  requests[0].resolve({notification:{initialized:true,has_unread:true}});
  await oldPoll;
  await refreshCashboxNotification();
  assert.equal(requests.length,1);
  assert.equal(state.cashboxNotificationRevision,0);
  frames.shift()();
  assert.equal(requests[1].path,'/api/mark_cashbox_notifications_seen');
  assert.equal(requests[1].body.cashbox_id,'box');
  requests[1].resolve({notification:{initialized:true,has_unread:false}});
  await state.cashboxNotificationSeenPromise;
  assert.equal(state.cashboxNotification.has_unread,false);

  state.activeCashbox={cashbox:{id:'box',updated_at:'stable'},meta:{transaction_limit:100},transactions:[{id:'unknown-tx'}]};
  state.cashboxNotificationForOpen={
    cashboxId:'box',notification:notice('unknown-tx'),
  };
  scheduleCashboxNotificationAcknowledgement({},'box');
  frames.shift()();
  frames.shift()();
  assert.equal(requests[2].path,'/api/mark_cashbox_notifications_seen');
  assert.equal(state.cashboxNotificationForOpen,null,'the attempted receipt must be consumed on send');
  scheduleCashboxNotificationAcknowledgement({},'box');
  assert.equal(frames.length,0,'another detail load must not schedule the same receipt');
  requests[2].reject(new DOMException('timed out','AbortError'));
  await new Promise(setImmediate);
  assert.equal(requests[3].path,'/api/list_cashboxes?limit=1&cashbox_id=box');
  const stillUnread={notification:notice('unknown-tx'),meta:{active_cashbox_updated_at:'stable'}};
  requests[3].resolve(stillUnread);
  await state.cashboxNotificationRefreshPromise;
  scheduleCashboxNotificationAcknowledgement({},'box');
  assert.equal(frames.length,0,'retrying a detail after the uncertain result must only read');
  assert.equal(requests.length,4,'authoritative reconciliation must not send another POST');
  const nextPoll=refreshCashboxNotification();
  requests[4].resolve(stillUnread);await nextPoll;
  assert.equal(frames.length,1,'a later independent poll must recover a receipt which never reached the server');
  frames.shift()();frames.shift()();
  assert.equal(requests[5].body.through_transaction_id,'unknown-tx');
  requests[5].resolve({notification:{initialized:true,has_unread:false}});
  await new Promise(setImmediate);

  for (const [readbackBoundary, visibleRows, retry, mobile=false] of [
    ['newer-row',['painted-row'],false], ['painted-row',['other-cashbox-row'],false],
    ['painted-row',['a','b','c','d','e','painted-row'],false,true],
    ['painted-row',['painted-row'],true],
  ]) {
    const start=requests.length;
    state.mobileLite=mobile;
    state.activeCashbox={transactions:[{id:'painted-row'}]};
    state.cashboxNotificationForOpen={
      cashboxId:'box',notification:notice('painted-row'),
    };
    scheduleCashboxNotificationAcknowledgement({},'box');
    frames.shift()();frames.shift()();
    const conflict=Object.assign(new Error('changed'),{status:409,code:'state_write_conflict'});
    state.activeCashbox={transactions:visibleRows.map(id=>({id}))};
    requests[start].reject(conflict);
    await new Promise(setImmediate);
    assert.equal(requests[start+1].path,'/api/list_cashboxes?limit=1&cashbox_id=box');
    requests[start+1].resolve({notification:notice(readbackBoundary)});
    await new Promise(setImmediate);
    assert.equal(requests.length,start+(retry?3:2));
    if (retry) {
      assert.equal(requests[start+2].body.through_transaction_id,'painted-row');
      assert.equal(requests[start+2].body.initialize_only,undefined);
      requests[start+2].reject(conflict);
      await new Promise(setImmediate);
      requests[start+3].resolve({notification:notice(readbackBoundary)});
      await new Promise(setImmediate);
      assert.equal(requests.length,start+4,'a second conflict is read back without a retry loop');
    }
  }
  state.mobileLite=false;
  state.activeCashbox={transactions:[{id:'later-visible'}]};
  assert.equal(paintedCashboxBoundary(notice('later-visible',['hidden-backdate','later-visible']),'box'),'');
  assert.equal(paintedCashboxBoundary(notice('later-visible',['later-visible','hidden-backdate']),'box'),'later-visible');
  assert.equal(paintedCashboxBoundary(notice('later-visible',['later-visible'],120),'another'),'');
  const start=requests.length;
  state.activeCashbox={transactions:['one','two','three'].map(id=>({id}))};
  state.cashboxNotificationForOpen={cashboxId:'box',
    notification:notice('three',['one','two'],3)};
  scheduleCashboxNotificationAcknowledgement({},'box');
  frames.shift()();frames.shift()();
  assert.equal(requests[start].body.through_transaction_id,'two');
  requests[start].resolve({meta:{changed:true},notification:notice('three',['three'])});
  await new Promise(setImmediate);
  frames.shift()();frames.shift()();
  assert.equal(requests[start+1].body.through_transaction_id,'three');
  requests[start+1].resolve({meta:{changed:true},notification:notice('three',[])});
  await new Promise(setImmediate);
  assert.equal(frames.length,0);
  state.mobileLite=true;
  state.activeCashbox={transactions:['a','b','c','d','e','hidden'].map(id=>({id}))};
  assert.equal(paintedCashboxBoundary(notice('hidden'),'box'),'');
  state.activeCashbox.mobileExpanded=true;
  assert.equal(paintedCashboxBoundary(notice('hidden'),'box'),'hidden');

  const queued=requests.length;
  const both={initialized:true,by_cashbox:{box:notice('first').by_cashbox.box,other:notice('second').by_cashbox.box}};
  for(const [id,tx] of [['box','first'],['other','second']]){
    state.activeCashboxId=id;
    state.activeCashbox={transactions:[{id:tx}]};
    prepareCashboxAcknowledgement(id,both);
    scheduleCashboxNotificationAcknowledgement({},id);
    frames.shift()();frames.shift()();
  }
  assert.equal(requests.length,queued+1,'the second box waits for the first receipt');
  requests[queued].resolve({notification:{unread_count:1}});
  await new Promise(setImmediate);
  assert.equal(requests[queued+1].body.cashbox_id,'other');
  requests[queued+1].resolve({notification:{unread_count:0}});
  await new Promise(setImmediate);
  assert.equal(state.cashboxNotification.unread_count,0,'the indicator must reflect the last queued receipt');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        )

    def test_password_only_with_stale_user_list_never_sends_permissions(self) -> None:
        source = read_board_source("app_main_before_printing.js")
        code = section(
            source,
            "    async function saveOperatorUser()",
            "    async function deleteOperatorUser(",
        )
        self.run_js(
            """
const assert=require('node:assert/strict');
const state={operatorUsers:[],operatorPermissionEditorUsername:''};
const els={adminUserLogin:{value:'EXISTING'},adminUserPassword:{value:'new-password'},adminUserEmployeesCashboxesAccess:{checked:false},adminUserEmployeesReadAccess:{checked:false}};
const calls=[];
function beginOperatorUserSaveMutation(){return {viewer:{isCurrent:()=>true},isCurrent:()=>true,ownsRequest:()=>true};}
function syncOperatorUserSaveControl(){}
async function api(path,{body}){calls.push(body);return {user:{username:'EXISTING'}};}
function applyOperatorUserSummary(){}
function clearOperatorUserEditor(){}
function setStatus(){}
async function refreshOperatorAdminSurfaces(){}
function finishOperatorUserSaveMutation(){}
"""
            + code
            + """
(async()=>{await saveOperatorUser();assert.equal(calls.length,1);assert.equal(Object.hasOwn(calls[0],'permissions'),false);})().catch(e=>{console.error(e);process.exitCode=1});
"""
        )

    def test_clearing_employee_access_releases_payroll_detail_index(self) -> None:
        source = read_board_source("app_main_before_printing.js")
        code = section(
            source,
            "    function clearEmployeesCashboxesModuleState() {",
            "    function syncEmployeesCashboxesAccessUi(",
        )
        self.run_js(
            """
const assert=require('node:assert/strict');
const rows=[{employee_id:'E1',amount:'100.00'}];
const state={employeesWorkspaceLoadGeneration:0,payrollReport:{detail_rows:rows},payrollDetailRowsIndexSource:rows,payrollDetailRowsIndex:new Map([['E1',rows]])};
const els={};
"""
            + code
            + """
clearEmployeesCashboxesModuleState();
assert.equal(state.payrollReport,null);
assert.equal(state.payrollDetailRowsIndexSource,null);
assert.equal(state.payrollDetailRowsIndex,null);
"""
        )
