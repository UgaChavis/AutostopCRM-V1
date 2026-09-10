from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function fixture() {
  const requests = [], statuses = [], renders = [], timers = [];
  const state = {
    viewerStateGeneration: 1, operatorSessionToken: 'A', employeesCashboxesAccessRevision: 0,
    payrollMonth: '2026-09', employeesWorkspaceLoadGeneration: 0,
    activeEmployeeId: 'a', activeEmployeeSalaryId: 'a', employeeCreateMode: false,
    employees: [{id:'a',name:'Alice'},{id:'b',name:'Bob'}], employeesLoadedMonth: '',
    employeeSalarySheet: {balance_minor:100,balance_revision:'r1'},
    employeeSalaryActionKind: 'salary_payout', employeeSalaryCashboxId: 'cash',
    employeeSalaryResetPending: false, employeeSalaryAdvanceOpen: true,
    employeeMoneyMutationOperation: null, employeeEditOperation: null,
  };
  const els = new Proxy({}, {get(target,key) {
    return target[key] ||= {value: key.includes('Cashbox') ? 'cash' : key === 'employeesMonthInput' ? '2026-09' : '10',
      disabled:false,focus(){renders.push('focus:'+key);}};
  }});
  const context = vm.createContext({state,els,console,URLSearchParams,
    window:{confirm:()=>true,crypto:{randomUUID:()=> 'key'},setTimeout:fn=>timers.push(fn)},
    setTimeout:fn=>timers.push(fn),
    api(path,options={}) { let resolve,reject; const promise=new Promise((a,b)=>{resolve=a;reject=b;});
      requests.push({path,options,resolve,reject,session:state.operatorSessionToken}); return promise; },
    setStatus:message=>statuses.push(message),
    currentPayrollMonthValue:()=> '2026-09',
    requireEmployeesCashboxesAccess:()=>true, operatorCanResetSalaryBalance:()=>true,
    operatorCanAccessEmployeesCashboxes:()=>true,
    maybeOpenModal:()=>renders.push('open'), popModal:()=>renders.push('close'),
  });
  for (const name of ['payroll_workspace.js','employees_reference.js','employees_mobile.js']) {
    vm.runInContext(fs.readFileSync('src/minimal_kanban/web_app_assets/source/'+name,'utf8'),context);
  }
  Object.assign(context, {
    renderEmployeeSalaryModal:()=>renders.push('salary'),
    renderEmployeesWorkspace:()=>renders.push('workspace'),
    renderMobileEmployeesPanel:()=>renders.push('mobile'),
    refreshRepairOrderEmployeeSelects:()=>renders.push('selects'),
    renderEmployeeShiftAccrualDialog:()=>{}, renderEmployeeSalaryCashboxOptions:()=>renders.push('cashboxes'),
    selectedEmployeeSalaryRecord:()=>state.employees.find(row=>row.id===state.activeEmployeeSalaryId),
    selectedEmployeeRecord:()=>state.employees.find(row=>row.id===state.activeEmployeeId),
    employeeCombinedNameFromForm:()=> 'Alice', employeeFormHasUnsavedChanges:()=>false,
    confirmDiscardEmployeeChanges:()=>true,
    readEmployeeFormPayload:()=>({employee_id:state.activeEmployeeId,name:'Alice'}),
    refreshCashboxesAfterMoneyMutation:async()=>renders.push('cashbox-refresh'),
  });
  return {context,state,els,requests,statuses,renders,timers};
}
const reply = {employee:{id:'a'},employees:[{id:'a',name:'Alice'},{id:'b',name:'Bob'}],ledger:{balance_minor:0,balance_revision:'r2'}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function switchViewer(f) {
  f.state.viewerStateGeneration++; f.state.operatorSessionToken='B';
  f.state.employeeMoneyMutationOperation=null; f.state.employeeEditOperation=null;
  f.state.employeeSalaryResetPending=true;
  for (const key of ['employeeSalaryActionConfirmButton','employeeSalaryAdvanceConfirmButton','employeeShiftAccrualConfirmButton']) f.els[key].disabled=true;
  for (const key of ['employeeSaveButton','employeeDeleteButton']) f.els[key].disabled=true;
  f.state.employeeSalarySheet={marker:'B'}; f.state.employees=[{id:'b',name:'B'}];
  f.statuses.length=0; f.renders.length=0;
}
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class PayrollAsyncContextTests(unittest.TestCase):
    def run_node(self, scenario: str) -> None:
        result = subprocess.run(
            ["node"],
            input=HARNESS
            + "\nconst scenarioWatchdog=setTimeout(()=>{console.error('payroll async scenario timed out');process.exitCode=1;},5000);\n"
            + "(async()=>{\n"
            + scenario
            + "\n})().then(()=>clearTimeout(scenarioWatchdog),error=>{clearTimeout(scenarioWatchdog);console.error(error);process.exitCode=1;});",
            text=True,
            capture_output=True,
            cwd=ROOT,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mutation_continuations_and_finally_belong_to_the_original_viewer(self) -> None:
        self.run_node(r"""
const handlers = {handleEmployeeSalaryReset:3,handleEmployeeSalaryActionConfirm:4,
  handleEmployeeSalaryAdvanceConfirm:4,handleEmployeeShiftAccrualConfirm:3,saveEmployee:2,deleteEmployee:2};
for (const [name,steps] of Object.entries(handlers)) {
  for (let staleAt=0;staleAt<steps;staleAt++) for (const reject of [false,true]) {
    const f=fixture(), pending=f.context[name]();
    for(let index=0;index<staleAt;index++){assert.ok(f.requests[index],name+': missing read '+index); f.requests[index].resolve(reply); await tick();}
    assert.ok(f.requests[staleAt],name+': missing pending '+staleAt);
    switchViewer(f);
    if(reject) f.requests[staleAt].reject(new Error('old A error')); else f.requests[staleAt].resolve(reply);
    await tick();
    assert.equal(f.requests.length,staleAt+1,name+': continued a stale request chain');
    await pending;
    assert.equal(f.state.employeeSalaryResetPending,true,name+': cleared B pending');
    for(const key of ['employeeSalaryActionConfirmButton','employeeSalaryAdvanceConfirmButton','employeeShiftAccrualConfirmButton']) assert.equal(f.els[key].disabled,true,name+': cleared B button');
    for(const key of ['employeeSaveButton','employeeDeleteButton']) assert.equal(f.els[key].disabled,true,name+': cleared B edit button');
    assert.equal(f.state.employeeSalarySheet.marker,'B',name+': overwrote B sheet');
    assert.equal(f.state.employees[0].name,'B',name+': overwrote B employees');
    assert.equal(f.statuses.length,0,name+': stale status'); assert.equal(f.renders.length,0,name+': stale render');
  }
}
""")

    def test_sheet_rejects_older_same_employee_requests_and_months(self) -> None:
        self.run_node(r"""
const f=fixture(), first=f.context.loadEmployeeSalarySheet('a',{openModal:true}), second=f.context.loadEmployeeSalarySheet('a',{openModal:true});
f.requests[1].resolve({marker:'new'}); await second;
f.requests[0].resolve({marker:'old'}); await first;
assert.equal(f.state.employeeSalarySheet.marker,'new','same-ID stale response');
const g=fixture(), originalSheet=g.state.employeeSalarySheet, sheet=g.context.loadEmployeeSalarySheet('a');
g.state.payrollMonth='2026-10'; g.els.employeesMonthInput.value='2026-10';
g.requests[0].resolve({marker:'old-month'}); await sheet;
assert.equal(g.state.employeeSalarySheet,originalSheet,'sheet from an older month applied');
""")

    def test_salary_dialog_load_errors_and_deferred_focus_do_not_cross_viewers(self) -> None:
        self.run_node(r"""
for(const name of ['openEmployeeSalaryDialog','openEmployeeSalaryAdvanceDialog']) for(const reject of [true,false]) {
  const f=fixture(), pending=f.context[name]('salary_payout');
  switchViewer(f);
  if(reject) f.requests[0].reject(new Error('old dialog')); else f.requests[0].resolve({cashboxes:[{id:'private-A'}]});
  await pending; for(const timer of f.timers) timer();
  assert.equal(f.statuses.length,0,name+': stale error'); assert.equal(f.renders.length,0,name+': stale render/focus');
  assert.equal(f.state.cashboxes,undefined,name+': leaked old cashboxes');
}
const f=fixture(), pending=f.context.openEmployeeSalaryDialog('salary_payout');
f.requests[0].resolve({cashboxes:[]}); await pending;
switchViewer(f); for(const timer of f.timers) timer();
assert.equal(f.renders.length,0,'scheduled focus crossed a session change');
""")

    def test_mobile_older_load_does_not_clear_the_newer_loading_indicator(self) -> None:
        self.run_node(r"""
const f=fixture(); const first=f.context.loadMobileEmployees({force:true});
switchViewer(f); const second=f.context.loadMobileEmployees({force:true});
assert.equal(f.requests.length,4);
f.requests[0].resolve(reply); f.requests[1].resolve({marker:'A'}); await first;
assert.equal(f.state.mobileEmployeesLoading,true,'A finally cleared B loading');
f.requests[2].resolve(reply); f.requests[3].resolve({marker:'B'}); await second;
assert.equal(f.state.mobileEmployeesLoading,false); assert.equal(f.state.payrollReport.marker,'B');
""")

    def test_current_operations_complete_once_and_current_failures_release_controls(self) -> None:
        self.run_node(r"""
for(const name of ['handleEmployeeSalaryReset','handleEmployeeSalaryActionConfirm','handleEmployeeSalaryAdvanceConfirm','handleEmployeeShiftAccrualConfirm','saveEmployee','deleteEmployee']) {
  const f=fixture(); let completed=false; const pending=f.context[name]().then(()=>completed=true);
  let resolved=0;
  for(let step=0;step<10 && !completed;step++) {
    while(resolved<f.requests.length) f.requests[resolved++].resolve(reply);
    await tick();
  }
  assert.equal(completed,true,name+': did not complete'); await pending;
  assert.equal(f.requests.filter(row=>row.options.method==='POST').length,1,name+': repeated mutation');
  assert.ok(f.statuses.length,name+': missing completion status');
  assert.equal(f.state.employeeSalaryResetPending,false);
  for(const key of ['employeeSalaryActionConfirmButton','employeeSalaryAdvanceConfirmButton','employeeShiftAccrualConfirmButton']) assert.equal(f.els[key].disabled,false);
}
for(const name of ['handleEmployeeSalaryReset','handleEmployeeSalaryActionConfirm','handleEmployeeSalaryAdvanceConfirm','handleEmployeeShiftAccrualConfirm']) {
  const f=fixture(), pending=f.context[name](); f.requests[0].reject(new Error('current failure')); await pending;
  assert.deepEqual(f.statuses,['current failure']); assert.equal(f.state.employeeSalaryResetPending,false);
  for(const key of ['employeeSalaryActionConfirmButton','employeeSalaryAdvanceConfirmButton','employeeShiftAccrualConfirmButton']) assert.equal(f.els[key].disabled,false);
}
""")

    def test_session_access_entity_and_month_changes_stop_mutation_reads(self) -> None:
        self.run_node(r"""
for(const change of [f=>f.state.operatorSessionToken='B',f=>f.state.employeesCashboxesAccessRevision++,
  f=>f.state.activeEmployeeSalaryId='b',f=>f.state.payrollMonth='2026-10']) {
  const f=fixture(), pending=f.context.handleEmployeeSalaryActionConfirm(); change(f);
  f.requests[0].resolve(reply); await pending;
  assert.equal(f.requests.length,1,'stale mutation launched follow-up reads'); assert.equal(f.statuses.length,0);
}
""")

    def test_money_mutations_are_single_flight_and_release_for_retry(self) -> None:
        self.run_node(r"""
for (const [name,button] of [
  ['handleEmployeeSalaryActionConfirm','employeeSalaryActionConfirmButton'],
  ['handleEmployeeSalaryAdvanceConfirm','employeeSalaryAdvanceConfirmButton'],
  ['handleEmployeeShiftAccrualConfirm','employeeShiftAccrualConfirmButton'],
]) {
  const f=fixture();
  const first=f.context[name]();
  const duplicate=f.context[name]();
  assert.equal(f.requests.length,1,name+': duplicate submit started a second write');
  await duplicate;
  assert.equal(f.els[button].disabled,true,name+': pending control was re-enabled');
  f.requests[0].reject(new Error('first failure'));
  await first;
  assert.equal(f.state.employeeMoneyMutationOperation,null,name+': completed operation retained its single-flight token');
  assert.equal(f.els[button].disabled,false,name+': completed operation did not release its control');
  const retry=f.context[name]();
  assert.equal(f.requests.length,2,name+': completed operation blocked a safe retry');
  f.requests[1].reject(new Error('retry failure'));
  await retry;
}
""")

    def test_money_single_flight_spans_salary_and_shift_dialogs(self) -> None:
        self.run_node(r"""
const f=fixture();
f.state.cashboxesLoaded=true; f.state.cashboxes=[{id:'cash',name:'Cash'}];
const payout=f.context.handleEmployeeSalaryActionConfirm();
await f.context.openEmployeeSalaryAdvanceDialog();
f.els.employeeSalaryAdvanceAmountInput.value='10';
const advance=f.context.handleEmployeeSalaryAdvanceConfirm();
f.context.openEmployeeShiftAccrualDialog();
f.els.employeeShiftAccrualAmountInput.value='10';
const shift=f.context.handleEmployeeShiftAccrualConfirm();
assert.equal(f.requests.filter(row=>row.options.method==='POST').length,1,'dialog switch bypassed money single-flight');
await Promise.all([advance,shift]);
for(const key of ['employeeSalaryActionConfirmButton','employeeSalaryAdvanceConfirmButton','employeeShiftAccrualConfirmButton']) {
  assert.equal(f.els[key].disabled,true,key+': active money operation was not reflected in a newly opened dialog');
}
f.requests[0].reject(new Error('payout failed'));
await payout;
assert.equal(f.state.employeeMoneyMutationOperation,null);
for(const key of ['employeeSalaryActionConfirmButton','employeeSalaryAdvanceConfirmButton','employeeShiftAccrualConfirmButton']) {
  assert.equal(f.els[key].disabled,false,key+': released money operation left a switched dialog disabled');
}
""")

    def test_employee_create_and_edit_writes_are_single_flight(self) -> None:
        self.run_node(r"""
const created=fixture(); created.state.employeeCreateMode=true; created.state.activeEmployeeId='';
const firstCreate=created.context.saveEmployee();
const duplicateCreate=created.context.saveEmployee();
assert.equal(created.requests.filter(row=>row.options.method==='POST').length,1,'double create started two writes');
await duplicateCreate;
assert.equal(created.els.employeeSaveButton.disabled,true);
created.requests[0].reject(new Error('create failed')); await firstCreate;
assert.equal(created.state.employeeEditOperation,null);
assert.equal(created.els.employeeSaveButton.disabled,false);
const createRetry=created.context.saveEmployee();
assert.equal(created.requests.filter(row=>row.options.method==='POST').length,2,'released create did not retry');
created.requests[1].resolve({employee:{id:'new-employee'},employees:[{id:'new-employee',name:'Alice'}],created:true});
await tick();
assert.equal(created.requests[2].path,'/api/get_payroll_report?month=2026-09');
created.requests[2].resolve({summary:[],detail_rows:[]});
await createRetry;
assert.equal(created.state.activeEmployeeId,'new-employee','successful create did not rebind its workspace');
assert.equal(created.state.employeeEditOperation,null);

const edited=fixture();
const firstEdit=edited.context.saveEmployee();
const collidingDelete=edited.context.deleteEmployee();
assert.equal(edited.requests.filter(row=>row.options.method==='POST').length,1,'save/delete used separate write ownership');
await collidingDelete;
edited.requests[0].reject(new Error('edit failed')); await firstEdit;
assert.equal(edited.state.employeeEditOperation,null);
""")

    def test_old_finally_cannot_release_new_viewer_mutation_tokens(self) -> None:
        self.run_node(r"""
const money=fixture();
const oldMoney=money.context.handleEmployeeSalaryActionConfirm();
const oldMoneyRequest=money.requests[0];
switchViewer(money);
const newMoney=money.context.handleEmployeeSalaryActionConfirm();
assert.equal(money.requests.length,2);
const newMoneyToken=money.state.employeeMoneyMutationOperation;
oldMoneyRequest.reject(new Error('old viewer money failure')); await oldMoney;
assert.equal(money.state.employeeMoneyMutationOperation,newMoneyToken,'old money finally released new viewer token');
assert.equal(money.els.employeeSalaryActionConfirmButton.disabled,true,'old money finally changed new viewer controls');
money.requests[1].reject(new Error('new viewer money failure')); await newMoney;
assert.equal(money.state.employeeMoneyMutationOperation,null);

const edit=fixture();
const oldEdit=edit.context.saveEmployee();
const oldEditRequest=edit.requests[0];
switchViewer(edit);
const newEdit=edit.context.saveEmployee();
assert.equal(edit.requests.length,2);
const newEditToken=edit.state.employeeEditOperation;
oldEditRequest.reject(new Error('old viewer edit failure')); await oldEdit;
assert.equal(edit.state.employeeEditOperation,newEditToken,'old edit finally released new viewer token');
assert.equal(edit.els.employeeSaveButton.disabled,true,'old edit finally changed new viewer controls');
edit.requests[1].reject(new Error('new viewer edit failure')); await newEdit;
assert.equal(edit.state.employeeEditOperation,null);
""")

    def test_viewer_reset_explicitly_discards_employee_mutation_tokens(self) -> None:
        source = (
            ROOT / "src/minimal_kanban/web_app_assets/source/app_main_before_printing.js"
        ).read_text(encoding="utf-8")
        start = source.index("    function clearEmployeesCashboxesModuleState()")
        end = source.index("    function syncEmployeesCashboxesAccessUi(", start)
        reset = source[start:end]
        self.assertIn("state.employeeMoneyMutationOperation = null;", reset)
        self.assertIn("state.employeeEditOperation = null;", reset)

    def test_closed_or_superseded_salary_views_cannot_reopen_or_continue(self) -> None:
        self.run_node(r"""
const f=fixture(), first=f.context.loadEmployeeSalarySheet('a',{openModal:true});
f.context.closeEmployeeSalaryModal(); f.renders.length=0;
f.requests[0].resolve({marker:'closed'}); assert.equal(await first,null);
assert.equal(f.renders.length,0,'closed view reopened');
const g=fixture(), mutation=g.context.handleEmployeeSalaryActionConfirm();
g.requests[0].resolve(reply); await tick();
const newer=g.context.loadEmployeeSalarySheet('a');
g.requests[2].resolve({marker:'newer'}); await newer;
g.requests[1].resolve({marker:'older'}); await mutation;
assert.equal(g.requests.length,3,'superseded ledger continued mutation refresh');
assert.equal(g.state.employeeSalarySheet.marker,'newer');
const h=fixture(), dialog=h.context.openEmployeeSalaryDialog('salary_payout');
h.context.closeEmployeeSalaryDialog(); h.renders.length=0;
h.requests[0].resolve({cashboxes:[]}); await dialog; for(const timer of h.timers) timer();
assert.equal(h.renders.length,0,'closed dialog rendered or focused');
const j=fixture(), earlier=j.context.handleEmployeeSalaryActionConfirm();
j.state.cashboxesLoaded=true; j.state.cashboxes=[{id:'cash'}];
await j.context.openEmployeeSalaryDialog('salary_payout');
j.state.employeeSalaryActionDraft='new dialog';
j.requests[0].resolve(reply); await earlier;
assert.equal(j.requests.length,1,'old operation refreshed a newer dialog');
assert.equal(j.state.employeeSalaryActionDraft,'new dialog');
""")


if __name__ == "__main__":
    unittest.main()
