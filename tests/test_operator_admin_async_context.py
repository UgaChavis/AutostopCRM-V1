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


def section(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset)]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class OperatorAdminAsyncContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = read_board_source("app_main_before_printing.js")

    def run_node(self, body: str) -> None:
        prefix = """
const __watchdog=setTimeout(()=>{console.error('Node scenario did not settle');process.exit(124);},5000);
function runTest(promise){promise.then(()=>clearTimeout(__watchdog)).catch(error=>{clearTimeout(__watchdog);console.error(error);process.exitCode=1;});}
"""
        result = subprocess.run(
            ["node"],
            input=prefix + body,
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=ROOT,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_binding_open_intent_rejects_back_and_a_to_b_responses(self) -> None:
        context = section(
            self.source,
            "    function captureViewerRequestContext(",
            "    function operatorHasPermission(",
        )
        admin_context = section(
            self.source,
            "function operatorEmployeeById(",
            "    function isOperatorEmployeeBindingOpen(",
        )
        binding = section(
            self.source,
            "    function isOperatorEmployeeBindingOpen(",
            "    function renderOperatorUsers(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
function classes(){return {toggle(){},add(){},remove(){},contains(){return false;}};}
const loads=[],statuses=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',apiToken:'api-A',actor:'ADMIN',
  operatorUsers:[{username:'A',employee_id:'employee-A'},{username:'B',employee_id:'employee-B'}],
  employees:[
    {id:'employee-A',name:'Employee A',is_active:true},
    {id:'employee-B',name:'Employee B',is_active:true},
  ],
  operatorEmployeeBindingIntentGeneration:0,operatorEmployeeBindingMutationRequest:null,
  operatorEmployeeBindingUser:'',operatorUserEditorIntentGeneration:0,operatorUserSaveRequest:null,
};
const els={
  operatorUserEmployeeSelect:{value:'',innerHTML:'',disabled:false},
  operatorUserEmployeeSaveButton:{disabled:false},operatorUserEmployeeClearButton:{disabled:false},
  operatorUserEmployeeBindingPanel:{classList:classes()},operatorUserEditorPanel:{classList:classes()},
  operatorUsersListPanel:{classList:classes()},operatorAdminCloseButton:{textContent:'',setAttribute(){}},
  operatorUserEmployeeBindingTitle:{textContent:''},adminSaveUserButton:{disabled:false},
};
function escapeHtml(value){return String(value);}
function syncOperatorAdminSalaryResetPermission(){}
function loadEmployeesReference(){const task=deferred();loads.push(task);return task.promise;}
function api(){throw new Error('binding save was not expected');}
function setStatus(message,isError){statuses.push({message,isError});}
function refreshOperatorAdminSurfaces(){throw new Error('admin refresh was not expected');}
function renderOperatorUsers(data){state.operatorUsers=data.users;renderOperatorEmployeeBindingPanel();}
"""
            + context
            + admin_context
            + binding
            + """
runTest((async()=>{
  const abandoned=openOperatorEmployeeBinding('A');
  closeOperatorEmployeeBinding();
  loads[0].resolve();await abandoned;
  assert.equal(state.operatorEmployeeBindingUser,'','Back allowed a late A panel to open');

  const interrupted=openOperatorEmployeeBinding('A');
  claimOperatorUserEditorIntent();
  loads[1].resolve();await interrupted;
  assert.equal(state.operatorEmployeeBindingUser,'','Editor input allowed a late A panel to open');

  const a=openOperatorEmployeeBinding('A');
  const b=openOperatorEmployeeBinding('B');
  loads[3].resolve();await b;
  loads[2].resolve();await a;
  assert.equal(state.operatorEmployeeBindingUser,'B');
  assert.equal(els.operatorUserEmployeeSelect.value,'employee-B');
  assert.deepEqual(statuses,[]);
})());
"""
        )

    def test_binding_save_is_single_flight_and_preserves_new_intent(self) -> None:
        context = section(
            self.source,
            "    function captureViewerRequestContext(",
            "    function operatorHasPermission(",
        )
        admin_context = section(
            self.source,
            "function operatorEmployeeById(",
            "    function isOperatorEmployeeBindingOpen(",
        )
        binding = section(
            self.source,
            "    function isOperatorEmployeeBindingOpen(",
            "    function renderOperatorUsers(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
function classes(){return {toggle(){},add(){},remove(){},contains(){return false;}};}
const calls=[],statuses=[],refreshes=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',apiToken:'api-A',actor:'ADMIN',
  operatorUsers:[{username:'A',employee_id:'old-A'},{username:'B',employee_id:'employee-B'}],
  employees:[
    {id:'old-A',name:'Old A',is_active:true},{id:'new-A',name:'New A',is_active:true},
    {id:'employee-B',name:'Employee B',is_active:true},
  ],
  operatorEmployeeBindingIntentGeneration:1,operatorEmployeeBindingMutationRequest:null,
  operatorEmployeeBindingUser:'A',operatorUserEditorIntentGeneration:0,operatorUserSaveRequest:null,
};
const els={
  operatorUserEmployeeSelect:{value:'old-A',innerHTML:'',disabled:false},
  operatorUserEmployeeSaveButton:{disabled:false},operatorUserEmployeeClearButton:{disabled:false},
  operatorUserEmployeeBindingPanel:{classList:classes()},operatorUserEditorPanel:{classList:classes()},
  operatorUsersListPanel:{classList:classes()},operatorAdminCloseButton:{textContent:'',setAttribute(){}},
  operatorUserEmployeeBindingTitle:{textContent:''},adminSaveUserButton:{disabled:false},
};
function escapeHtml(value){return String(value);}
function syncOperatorAdminSalaryResetPermission(){}
function loadEmployeesReference(){return Promise.resolve();}
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function setStatus(message,isError){statuses.push({message,isError});}
async function refreshOperatorAdminSurfaces(options){refreshes.push(options);}
function renderOperatorUsers(data){state.operatorUsers=data.users;renderOperatorEmployeeBindingPanel();}
"""
            + context
            + admin_context
            + binding
            + """
runTest((async()=>{
  renderOperatorEmployeeBindingPanel();
  els.operatorUserEmployeeSelect.value='new-A';
  const staleA=saveOperatorEmployeeBinding();
  const duplicate=saveOperatorEmployeeBinding();
  assert.equal(calls.length,1,'duplicate binding write escaped single-flight');
  assert.equal(els.operatorUserEmployeeSaveButton.disabled,true);

  closeOperatorEmployeeBinding();
  await openOperatorEmployeeBinding('B');
  calls[0].resolve({user:{username:'A',employee_id:'new-A'},meta:{bound:true}});
  await Promise.all([staleA,duplicate]);
  assert.equal(state.operatorEmployeeBindingUser,'B');
  assert.equal(els.operatorUserEmployeeSelect.value,'employee-B');
  assert.equal(state.operatorUsers.find(user=>user.username==='A').employee_id,'new-A');
  assert.deepEqual(statuses,[]);assert.deepEqual(refreshes,[]);
  assert.equal(state.operatorEmployeeBindingMutationRequest,null);
  assert.equal(els.operatorUserEmployeeSaveButton.disabled,false);

  closeOperatorEmployeeBinding();await openOperatorEmployeeBinding('A');
  els.operatorUserEmployeeSelect.value='old-A';
  const sameA=saveOperatorEmployeeBinding();
  closeOperatorEmployeeBinding();await openOperatorEmployeeBinding('A');
  els.operatorUserEmployeeSelect.value='new-A';
  calls[1].resolve({user:{username:'A',employee_id:'old-A',updated_at:'2026-09-10T10:00:01+00:00'},meta:{bound:true}});await sameA;
  assert.equal(state.operatorEmployeeBindingUser,'A');
  assert.equal(state.operatorUsers.find(user=>user.username==='A').employee_id,'old-A');
  assert.equal(els.operatorUserEmployeeSelect.value,'new-A','stale success erased reopened A draft');
  assert.deepEqual(statuses,[]);assert.deepEqual(refreshes,[]);

  state.operatorUsers=state.operatorUsers.map(user=>user.username==='A'
    ? {...user,employee_id:'new-A',updated_at:'2026-09-10T10:00:02+00:00'}:user);
  assert.equal(applyOperatorUserSummary(
    {username:'A',employee_id:'old-A',updated_at:'2026-09-10T10:00:01+00:00'},
    {preserveBindingDraft:true},
  ),false);
  assert.equal(state.operatorUsers.find(user=>user.username==='A').employee_id,'new-A','older summary replaced newer state');
  assert.equal(els.operatorUserEmployeeSelect.value,'new-A');

  els.operatorUserEmployeeSelect.value='new-A';
  const current=saveOperatorEmployeeBinding();
  calls[2].resolve({user:{username:'A',employee_id:'new-A'},meta:{bound:true}});await current;
  assert.equal(state.operatorEmployeeBindingUser,'','current success did not close the child view');
  assert.equal(statuses.length,1);assert.equal(statuses[0].isError,false);
  assert.equal(refreshes.length,1);assert.equal(refreshes[0].openAdminModal,false);
  assert.equal(state.operatorEmployeeBindingMutationRequest,null);

  await openOperatorEmployeeBinding('A');els.operatorUserEmployeeSelect.value='old-A';
  const oldViewer=saveOperatorEmployeeBinding(),oldRequest=calls[3];
  state.viewerStateGeneration++;state.operatorSessionToken='session-B';state.actor='ADMIN-B';
  resetOperatorAdminViewerState();
  state.operatorUsers=[{username:'B',employee_id:'employee-B'}];
  state.employees=[{id:'employee-B',name:'Employee B',is_active:true}];
  state.operatorEmployeeBindingUser='B';els.operatorUserEmployeeSelect.value='employee-B';
  const newViewer=saveOperatorEmployeeBinding(),newToken=state.operatorEmployeeBindingMutationRequest;
  oldRequest.resolve({user:{username:'A',employee_id:'old-A'},meta:{bound:true}});await oldViewer;
  assert.equal(state.operatorEmployeeBindingMutationRequest,newToken,'old finally released new viewer binding token');
  assert.equal(els.operatorUserEmployeeSaveButton.disabled,true);
  calls[4].resolve({user:{username:'B',employee_id:'employee-B'},meta:{bound:true}});await newViewer;
  assert.equal(state.operatorEmployeeBindingMutationRequest,null);
})());
"""
        )

    def test_user_save_is_single_flight_and_cannot_overwrite_b_draft(self) -> None:
        context = section(
            self.source,
            "    function captureViewerRequestContext(",
            "    function operatorHasPermission(",
        )
        admin_context = section(
            self.source,
            "function operatorEmployeeById(",
            "    function isOperatorEmployeeBindingOpen(",
        )
        editor = section(
            self.source,
            "    function editOperatorUserPermissions(",
            "    function bindOperatorAdminPermissionUi(",
        )
        admin_close = section(
            self.source,
            "    function closeOperatorAdminModal(",
            "    function openTextBlobWindow(",
        )
        save = section(
            self.source,
            "    async function saveOperatorUser()",
            "    async function deleteOperatorUser(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
function check(value=false){return {checked:value,disabled:false,setAttribute(){}};}
const calls=[],statuses=[],refreshes=[],popped=[];
const state={
  viewerStateGeneration:1,operatorSessionToken:'session-A',apiToken:'api-A',actor:'ADMIN',
  operatorUsers:[
    {username:'A',employee_id:'',permissions:['employees_cashboxes_access']},
    {username:'B',employee_id:'',permissions:['employees_read_access']},
  ],
  operatorPermissionEditorUsername:'A',operatorUserEditorIntentGeneration:1,operatorUserSaveRequest:null,
  operatorEmployeeBindingIntentGeneration:0,operatorEmployeeBindingMutationRequest:null,operatorEmployeeBindingUser:'',
  operatorAdminRequestSeq:0,
};
const els={
  adminUserLogin:{value:'A'},adminUserPassword:{value:'secret-A',focus(){}},
  adminUserEmployeesCashboxesAccess:check(true),adminUserEmployeesReadAccess:check(false),
  adminUserSalaryBalanceReset:check(false),adminSaveUserButton:{disabled:false},
  operatorUserEditorPanel:{scrollIntoView(){}},operatorUserEmployeeSelect:{value:'',disabled:false},
  operatorUserEmployeeSaveButton:{disabled:false},operatorUserEmployeeClearButton:{disabled:false},
};
const EMPLOYEES_CASHBOXES_ACCESS_PERMISSION='employees_cashboxes_access';
const EMPLOYEES_READ_ACCESS_PERMISSION='employees_read_access';
const SALARY_BALANCE_RESET_PERMISSION='salary_balance_reset';
function escapeHtml(value){return String(value);}
function renderOperatorUsers(data){
  state.operatorUsers=data.users;
  const binding=(data.users||[]).find(user=>user.username===state.operatorEmployeeBindingUser);
  if(binding&&els.operatorUserEmployeeSelect)els.operatorUserEmployeeSelect.value=String(binding.employee_id||'');
}
function closeOperatorEmployeeBinding(){state.operatorEmployeeBindingIntentGeneration++;state.operatorEmployeeBindingUser='';}
function popModal(key){popped.push(key);}
function api(path,options){const task=deferred();calls.push({path,options,...task});return task.promise;}
function setStatus(message,isError){statuses.push({message,isError});}
async function refreshOperatorAdminSurfaces(options){refreshes.push(options);}
"""
            + context
            + admin_context
            + editor
            + admin_close
            + save
            + """
runTest((async()=>{
  const staleA=saveOperatorUser();
  const duplicate=saveOperatorUser();
  assert.equal(calls.length,1,'duplicate user write escaped single-flight');
  assert.equal(els.adminSaveUserButton.disabled,true);

  editOperatorUserPermissions('B');
  els.adminUserPassword.value='draft-B';
  claimOperatorUserEditorIntent();
  calls[0].resolve({user:{username:'A',employee_id:'',permissions:[]},meta:{created:false}});
  await Promise.all([staleA,duplicate]);
  assert.equal(els.adminUserLogin.value,'B');assert.equal(els.adminUserPassword.value,'draft-B');
  assert.equal(state.operatorPermissionEditorUsername,'B');
  assert.equal(els.adminUserEmployeesReadAccess.checked,true);
  assert.deepEqual(statuses,[]);assert.deepEqual(refreshes,[]);
  assert.equal(state.operatorUserSaveRequest,null);assert.equal(els.adminSaveUserButton.disabled,false);

  const closedB=saveOperatorUser();
  closeOperatorAdminModal();
  assert.equal(els.adminUserLogin.value,'');assert.equal(els.adminUserPassword.value,'');
  assert.equal(els.adminUserEmployeesCashboxesAccess.checked,false);
  assert.equal(els.adminUserEmployeesReadAccess.checked,false);
  assert.equal(els.adminUserSalaryBalanceReset.checked,false);
  assert.deepEqual(popped,['operator-admin']);
  calls[1].resolve({user:{username:'B',employee_id:'',permissions:[]},meta:{created:false}});await closedB;
  assert.equal(els.adminUserLogin.value,'');assert.equal(els.adminUserPassword.value,'');
  assert.deepEqual(statuses,[]);assert.deepEqual(refreshes,[]);
  assert.equal(state.operatorUserSaveRequest,null);assert.equal(els.adminSaveUserButton.disabled,false);

  editOperatorUserPermissions('A');
  els.adminUserPassword.value='next-A';claimOperatorUserEditorIntent();
  const userAIntoBindingDraft=saveOperatorUser();
  state.operatorEmployeeBindingUser='A';state.operatorEmployeeBindingIntentGeneration++;
  els.operatorUserEmployeeSelect.value='draft-employee';claimOperatorUserEditorIntent();
  calls[2].resolve({user:{username:'A',employee_id:'server-employee',permissions:[]},meta:{created:false}});
  await userAIntoBindingDraft;
  assert.equal(state.operatorUsers.find(user=>user.username==='A').employee_id,'server-employee');
  assert.equal(els.operatorUserEmployeeSelect.value,'draft-employee','stale user save erased binding draft');
  assert.deepEqual(statuses,[]);assert.deepEqual(refreshes,[]);

  closeOperatorEmployeeBinding();editOperatorUserPermissions('A');
  els.adminUserPassword.value='old-viewer';claimOperatorUserEditorIntent();
  const oldViewer=saveOperatorUser(),oldRequest=calls[3];
  state.viewerStateGeneration++;state.operatorSessionToken='session-B';state.actor='ADMIN-B';
  resetOperatorAdminViewerState();
  state.operatorUsers=[{username:'B',employee_id:'',permissions:[]}];
  editOperatorUserPermissions('B');els.adminUserPassword.value='new-viewer';claimOperatorUserEditorIntent();
  const newViewer=saveOperatorUser(),newToken=state.operatorUserSaveRequest;
  oldRequest.resolve({user:{username:'A',employee_id:'',permissions:[]},meta:{created:false}});await oldViewer;
  assert.equal(state.operatorUserSaveRequest,newToken,'old finally released new viewer user token');
  assert.equal(els.adminSaveUserButton.disabled,true);
  calls[4].resolve({user:{username:'B',employee_id:'',permissions:[]},meta:{created:false}});await newViewer;
  assert.equal(state.operatorUserSaveRequest,null);assert.equal(els.adminSaveUserButton.disabled,false);
})());
"""
        )

    def test_logout_reset_clears_private_editor_dom_and_pending_owners(self) -> None:
        admin_context = section(
            self.source,
            "function operatorEmployeeById(",
            "    function isOperatorEmployeeBindingOpen(",
        )
        reset = section(
            self.source,
            "    function resetViewerScopedState()",
            "    function clearOperatorSession(",
        )
        self.assertIn("resetOperatorAdminViewerState();", reset)
        self.run_node(
            """
const assert=require('node:assert/strict');
function check(value=true){return {checked:value,disabled:true,setAttribute(){}};}
const state={
  operatorUsers:[{username:'PRIVATE'}],operatorPermissionEditorUsername:'PRIVATE',
  operatorEmployeeBindingUser:'PRIVATE',operatorEmployeeBindingIntentGeneration:4,
  operatorEmployeeBindingMutationRequest:{private:true},operatorUserEditorIntentGeneration:8,
  operatorUserSaveRequest:{private:true},employees:[],
};
const els={
  adminUserLogin:{value:'PRIVATE'},adminUserPassword:{value:'secret-password'},
  adminUserEmployeesCashboxesAccess:check(),adminUserEmployeesReadAccess:check(),
  adminUserSalaryBalanceReset:check(),adminSaveUserButton:{disabled:true},
  operatorUserEmployeeSelect:{value:'private-employee',disabled:true},
  operatorUserEmployeeSaveButton:{disabled:true},operatorUserEmployeeClearButton:{disabled:true},
};
function escapeHtml(value){return String(value);}
function syncOperatorAdminSalaryResetPermission(){}
function renderOperatorUsers(){}
"""
            + admin_context
            + """
runTest((async()=>{
  resetOperatorAdminViewerState();
  assert.equal(els.adminUserLogin.value,'');assert.equal(els.adminUserPassword.value,'');
  assert.equal(els.adminUserEmployeesCashboxesAccess.checked,false);
  assert.equal(els.adminUserEmployeesReadAccess.checked,false);
  assert.equal(els.adminUserSalaryBalanceReset.checked,false);
  assert.equal(state.operatorPermissionEditorUsername,'');assert.equal(state.operatorEmployeeBindingUser,'');
  assert.deepEqual(state.operatorUsers,[]);
  assert.equal(state.operatorEmployeeBindingMutationRequest,null);assert.equal(state.operatorUserSaveRequest,null);
  assert.equal(state.operatorEmployeeBindingIntentGeneration,5);assert.equal(state.operatorUserEditorIntentGeneration,9);
  assert.equal(els.operatorUserEmployeeSelect.value,'');
  assert.equal(els.operatorUserEmployeeSaveButton.disabled,false);
  assert.equal(els.adminSaveUserButton.disabled,false);
})());
"""
        )

    def test_operator_admin_back_and_full_close_preserve_modal_stack_contract(self) -> None:
        binding_intent = section(
            self.source,
            "function claimOperatorBindingIntent(",
            "function claimOperatorUserEditorIntent(",
        )
        child = section(
            self.source,
            "    function isOperatorEmployeeBindingOpen(",
            "    function renderOperatorEmployeeBindingPanel(",
        )
        close_binding = section(
            self.source,
            "    function closeOperatorEmployeeBinding()",
            "    async function saveOperatorEmployeeBinding(",
        )
        admin_close = section(
            self.source,
            "    function closeOperatorAdminModal(",
            "    function openTextBlobWindow(",
        )
        modal_stack = section(
            self.source,
            "    function pushModal(",
            "    async function loadModalData(",
        )
        self.run_node(
            """
const assert=require('node:assert/strict');
function modal(){const open=new Set(['is-open']);return {classList:{add:key=>open.add(key),remove:key=>open.delete(key),contains:key=>open.has(key)}};}
const admin=modal(),childModal=modal();let editorClears=0,renders=0;
const state={
  operatorEmployeeBindingUser:'A',operatorEmployeeBindingIntentGeneration:1,operatorAdminRequestSeq:0,
  modalStack:[
    {key:'operator-admin',elementId:'operatorAdminModal',parentKey:'',openerSelector:''},
    {key:'operator-child',elementId:'operatorChildModal',parentKey:'operator-admin',openerSelector:''},
  ],
};
const els={operatorAdminCloseButton:{textContent:'',setAttribute(){}}};
function modalElementForKey(key){return key==='operator-admin'?admin:(key==='operator-child'?childModal:null);}
function modalKeyForElement(){return '';}
function modalOpenerSelector(){return '';}
function restoreModalFocus(){}
function renderOperatorEmployeeBindingPanel(){renders++;}
function clearOperatorUserEditor(){editorClears++;}
"""
            + binding_intent
            + child
            + close_binding
            + admin_close
            + modal_stack
            + """
runTest((async()=>{
  assert.equal(closeNamedModal('operator-admin'),false);
  assert.equal(state.operatorEmployeeBindingUser,'');assert.equal(renders,1);
  assert.equal(state.modalStack.length,2,'Back closed a modal instead of the nested binding view');
  assert.equal(editorClears,0,'Back unexpectedly discarded the editor draft');

  assert.equal(closeNamedModal('operator-admin'),true);
  assert.deepEqual(state.modalStack,[]);
  assert.equal(admin.classList.contains('is-open'),false);
  assert.equal(childModal.classList.contains('is-open'),false,'full close left a modal-stack child open');
  assert.equal(editorClears,1);
})());
"""
        )


if __name__ == "__main__":
    unittest.main()
