from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = 'src/minimal_kanban/web_app_assets/source/';
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture(name = 'payroll', canManage = true) {
  const requests = [], scripts = [], renders = [], statuses = [];
  const state = {viewerStateGeneration:1,operatorSessionToken:'A',editingId:'a',
    employeesCashboxesAccessRevision:0,employeesWorkspaceLoadGeneration:0,
    payrollMonth:'2026-09',employeesLoadedMonth:'',employees:[],
    inventoryLoaded:false,inventoryItems:[],inventoryQuery:'',inventoryView:'items',modalStack:[]};
  const workspace={inert:false}, classes=new Set();
  const inventoryModal={id:'inventoryModal',querySelector:()=>workspace,
    classList:{add:name=>classes.add(name),remove:name=>classes.delete(name),contains:name=>classes.has(name)}};
  const els = {employeesMonthInput:{value:''},employeesModal:{querySelector:()=>null},inventoryModal,
    inventoryStatusLine:{textContent:'',dataset:{}}};
  let canView = true;
  const context = vm.createContext({state,els,console,window:{},HTMLElement:class {},
    BOARD_MODULE_MANIFEST:{payroll:'/payroll.js',inventory:'/inventory.js'},
    document:{activeElement:null,createElement:()=>({remove(){}}),head:{appendChild:script=>scripts.push(script)}},
    currentPayrollMonthValue:()=> '2026-09',operatorCanAccessEmployeesCashboxes:()=>canManage,
    operatorCanViewEmployees:()=>canView,requireEmployeesViewAccess:()=>canView,
    api(path,options) {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});
      requests.push({path,options,resolve,reject});return promise;},
    setStatus:message=>statuses.push(message),inventoryStatus:()=>{},
    modalKeyForElement:()=> 'inventory',modalOpenerSelector:()=> '#inventoryButton',
    modalElementForKey:()=>inventoryModal,restoreModalFocus:()=>renders.push('restore-focus'),
  });
  for(const file of ['module_loader.js','employees_reference.js','inventory_reference.js']) {
    if(fs.existsSync(source+file)) vm.runInContext(fs.readFileSync(source+file,'utf8'),context);
  }
  const modalSource=fs.readFileSync(source+'app_main_before_printing.js','utf8');
  for(const name of ['pushModal','popModal','maybeOpenModal']) {
    vm.runInContext(modalSource.match(new RegExp('^    function '+name+'\\(.*?^    }','ms'))[0],context);
  }
  const push=context.pushModal;
  context.pushModal=(key,...args)=>{if(key==='inventory')push(key,...args);renders.push('open-'+(key==='employees'?'payroll':key));};
  const method = name === 'payroll' ? 'openEmployeesModal' : 'openInventoryModal';
  function finishModule() {
    vm.runInContext(fs.readFileSync(source+(name==='payroll'?'payroll_workspace.js':'inventory_workspace.js'),'utf8'),context);
    Object.assign(context, {
      ensureEmployeesUi:()=>{},hydrateEmployeesUiRefs:()=>{},bindEmployeesUiEvents:()=>{},
      renderEmployeesWorkspace:()=>renders.push('payroll'),refreshRepairOrderEmployeeSelects:()=>{},
      renderInventory:()=>renders.push('inventory'),renderInventoryItems:()=>{},
    });
    context.window.registerBoardModule(name,()=>({[method]:context[method]}));scripts.at(-1).onload();
  }
  function employeePayload(marker='ready') {return {
    employees:[{id:marker}],marker,month:'2026-09',
    summary:{[marker]:{employee_id:marker}},detail_rows:[{employee_id:marker}],
  };}
  function reply(marker='ready') {for(const request of requests) request.resolve({
    ...employeePayload(marker),items:[{id:marker}],
  });}
  return {context,state,els,workspace,requests,scripts,renders,statuses,finishModule,reply,
    employeePayload,invoke:()=>context.invokeBoardModule(name,method,[]),deny:()=>{canView=false;}};
}
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class ColdModuleOverlapTests(unittest.TestCase):
    def run_node(self, body: str) -> None:
        result = subprocess.run(
            ["node"],
            input=HARNESS
            + "\nconst watchdog=setTimeout(()=>{throw new Error('unfinished scenario')},2000);"
            + "\n(async()=>{\n"
            + body
            + "\n})().then(()=>clearTimeout(watchdog),error=>{clearTimeout(watchdog);console.error(error);process.exitCode=1});",
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cold_reads_overlap_script_without_early_apply_or_duplicate_calls(self) -> None:
        self.run_node(r"""
for(const name of ['payroll','inventory']) for(const dataFirst of [true,false]) {
  const f=fixture(name);assert.equal(f.requests.length,0);assert.equal(f.scripts.length,0);
  const pending=f.invoke();assert.equal(f.requests.length,1,'one read must start before script completion');
  if(name==='payroll')assert.match(f.requests[0].path,/list_employees/);
  assert.equal(f.invoke(),pending,'cold invocation must be deduplicated');assert.equal(f.scripts.length,1);
  const earlyRenders=f.renders.length;
  if(dataFirst){f.reply();await tick();assert.equal(f.renders.length,earlyRenders);assert.equal(f.state.employees.length,0);assert.equal(f.state.inventoryItems.length,0);}
  f.finishModule();await tick();
  if(!dataFirst){assert.equal(f.invoke(),pending,'factory-ready/data-pending invocation must still be deduplicated');f.reply();}
  await pending;assert.equal(f.requests.length,1);
  assert.equal(f.renders.filter(value=>value==='open-'+name).length,1);
  assert.equal((name==='payroll'?f.state.employees:f.state.inventoryItems)[0].id,'ready');
  if(name==='payroll') {
    assert.equal(f.state.payrollReport.month,'2026-09');
    assert.equal(f.state.payrollReport.summary.ready.employee_id,'ready');
    assert.equal(f.state.payrollReport.detail_rows[0].employee_id,'ready');
  }
  const count=f.requests.length,warm=f.invoke();assert.equal(f.requests.length,count+1,'warm direct path performs its existing read');
  if(name==='payroll')assert.match(f.requests.at(-1).path,/get_payroll_report/);
  f.reply();await warm;
}
""")

    def test_cold_payroll_uses_fresh_embedded_report_even_with_cached_references(self) -> None:
        self.run_node(r"""
const f=fixture('payroll');
f.state.employeesLoadedMonth='2026-09';f.state.employees=[{id:'cached'}];
const pending=f.invoke();assert.equal(f.requests.length,1);
assert.equal(f.requests[0].path,'/api/list_employees?month=2026-09');
f.requests[0].resolve({employees:[{id:'fresh'}],month:'2026-09',
  summary:{fresh:{employee_id:'fresh'}},detail_rows:[{employee_id:'fresh'}]});
f.finishModule();await pending;
assert.equal(f.state.employees[0].id,'fresh');
assert.equal(f.state.payrollReport.summary.fresh.employee_id,'fresh');
assert.equal(f.state.payrollReport.detail_rows[0].employee_id,'fresh');
""")

    def test_default_loader_used_by_mobile_keeps_separate_payroll_read(self) -> None:
        self.run_node(r"""
const f=fixture('payroll');
assert.match(fs.readFileSync(source+'employees_mobile.js','utf8'),/loadEmployeesWorkspaceData\(month\)/);
const pending=f.context.loadEmployeesWorkspaceData('2026-09');
assert.deepEqual(f.requests.map(request=>request.path),[
  '/api/list_employees?month=2026-09','/api/get_payroll_report?month=2026-09',
]);
f.reply('mobile');await pending;
assert.equal(f.state.employees[0].id,'mobile');assert.equal(f.state.payrollReport.marker,'mobile');
""")

    def test_cold_invalid_embedded_report_falls_back_to_guarded_payroll_read(self) -> None:
        self.run_node(r"""
for(const invalid of [
  {employees:[{id:'wrong-month'}],month:'2026-08',summary:{},detail_rows:[]},
  {employees:[{id:'wrong-shape'}],month:'2026-09',summary:null,detail_rows:[]},
  {employees:[{id:'wrong-details'}],month:'2026-09',summary:{},detail_rows:null},
]) {
  const f=fixture('payroll'),pending=f.invoke();f.requests[0].resolve(invalid);await tick();
  assert.equal(f.requests.length,2);assert.equal(f.requests[1].path,'/api/get_payroll_report?month=2026-09');
  f.requests[1].resolve({month:'2026-09',summary:{fallback:{}},detail_rows:[]});
  f.finishModule();await pending;
  assert.equal(f.state.employees[0].id,invalid.employees[0].id);
  assert.ok(f.state.payrollReport.summary.fallback);assert.equal(f.statuses.length,0);
}
""")

    def test_cold_reference_only_response_keeps_guarded_payroll_error_behavior(self) -> None:
        self.run_node(r"""
const f=fixture('payroll'),pending=f.invoke();
f.requests[0].resolve({employees:[{id:'restricted'}],month:'2026-09',summary:{},detail_rows:[],
  meta:{references_only:true}});await tick();
assert.equal(f.requests.length,2);assert.equal(f.requests[1].path,'/api/get_payroll_report?month=2026-09');
f.requests[1].reject(new Error('forbidden'));f.finishModule();await pending;
assert.deepEqual(f.state.employees,[]);assert.equal(f.state.payrollReport ?? null,null);
assert.equal(f.renders.length,0);assert.match(f.statuses[0],/forbidden/);
""")

    def test_cold_viewer_access_month_and_request_changes_do_not_apply_or_open(self) -> None:
        self.run_node(r"""
for(const name of ['payroll','inventory']) for(const change of ['viewer','session','request',...(name==='payroll'?['access','month']:[])]) {
  const f=fixture(name),pending=f.invoke();assert.ok(f.requests.length);const initialRenders=f.renders.length;
  if(change==='viewer')f.state.viewerStateGeneration++;
  if(change==='session')f.state.operatorSessionToken='B';
  if(change==='access')f.state.employeesCashboxesAccessRevision++;
  if(change==='month')f.state.payrollMonth='2026-10';
  if(change==='request') {
    if(name==='payroll')f.state.employeesWorkspaceLoadGeneration++;
    else f.state.inventoryRequests.items={};
  }
  f.reply('stale');f.finishModule();await pending;
  assert.equal(f.state.employees.length,0,name+': stale employees');assert.equal(f.state.inventoryItems.length,0,name+': stale inventory');
  if(name==='payroll')assert.equal(f.state.payrollReport ?? null,null,name+': stale payroll');
  assert.equal(f.renders.length,initialRenders,name+': stale open/render');assert.equal(f.statuses.length,0,name+': stale status');
}
""")

    def test_stale_invalid_embedded_report_does_not_start_fallback_in_new_context(self) -> None:
        self.run_node(r"""
for(const change of ['viewer','session','request','access','month']) {
  const f=fixture('payroll'),pending=f.invoke();
  if(change==='viewer')f.state.viewerStateGeneration++;
  if(change==='session')f.state.operatorSessionToken='B';
  if(change==='access')f.state.employeesCashboxesAccessRevision++;
  if(change==='month')f.state.payrollMonth='2026-10';
  if(change==='request')f.state.employeesWorkspaceLoadGeneration++;
  f.requests[0].resolve({employees:[{id:'stale'}],month:'2026-08',summary:null,detail_rows:null});
  await tick();assert.equal(f.requests.length,1,'stale fallback crossed '+change);
  f.finishModule();await pending;
  assert.deepEqual(f.state.employees,[]);assert.equal(f.state.payrollReport ?? null,null);
  assert.equal(f.renders.length,0);assert.equal(f.statuses.length,0);
}
""")

    def test_cold_data_and_script_failures_are_consumed_and_retry_without_duplicates(self) -> None:
        self.run_node(r"""
for(const name of ['payroll','inventory']) for(const failure of ['data','script']) {
  const f=fixture(name),pending=f.invoke();assert.ok(f.requests.length);
  if(failure==='data'){for(const request of f.requests)request.reject(new Error('data failed'));await tick();f.finishModule();}
  else {f.scripts[0].onerror();f.reply();}
  await pending;await tick();assert.ok(f.statuses.length);
  const count=f.requests.length,retry=f.invoke();
  const expected = name==='payroll' && failure==='data' ? 2 : 1;
  assert.equal(f.requests.length-count,expected,'retry must perform one fresh request set');
  if(failure==='script')f.finishModule();f.reply('retry');await retry;
  assert.equal((name==='payroll'?f.state.employees:f.state.inventoryItems)[0].id,'retry');
}
""")

    def test_read_only_and_denied_payroll_keep_existing_read_permissions(self) -> None:
        self.run_node(r"""
const readOnly=fixture('payroll',false),pending=readOnly.invoke();
assert.equal(readOnly.requests.length,1);assert.match(readOnly.requests[0].path,/list_employees/);
readOnly.reply();readOnly.finishModule();await pending;assert.equal(readOnly.state.payrollReport,null);
const denied=fixture();denied.deny();const rejected=denied.invoke();
assert.equal(denied.requests.length,0);denied.finishModule();await rejected;assert.equal(denied.renders.length,0);
""")

    def test_cold_inventory_query_uses_existing_search_contract(self) -> None:
        self.run_node(r"""
const f=fixture('inventory');f.state.inventoryQuery='  brake  ';const pending=f.invoke();
assert.equal(f.requests.length,1);assert.equal(f.requests[0].path,'/api/search_inventory_items');
assert.equal(f.requests[0].options.method,'POST');assert.equal(f.requests[0].options.body.query,'brake');
assert.equal(f.requests[0].options.body.limit,200);f.finishModule();f.reply();await pending;
""")

    def test_late_old_data_and_error_cannot_change_a_new_viewer_after_factory_ready(self) -> None:
        self.run_node(r"""
for(const name of ['payroll','inventory']) for(const reject of [true,false]) {
  const f=fixture(name),old=f.invoke();f.finishModule();await tick();
  f.state.operatorSessionToken='B';const oldRequests=f.requests.slice(),offset=f.requests.length;
  const current=f.invoke();assert.equal(f.requests.length-offset,name==='payroll'?2:1);
  for(const request of f.requests.slice(offset))request.resolve({...f.employeePayload('B'),items:[{id:'B'}]});
  await current;const renders=f.renders.length,statuses=f.statuses.length;
  for(const request of oldRequests) {
    if(reject)request.reject(new Error('old A error'));else request.resolve({...f.employeePayload('A'),items:[{id:'A'}]});
  }
  await old;assert.equal((name==='payroll'?f.state.employees:f.state.inventoryItems)[0].id,'B');
  assert.equal(f.renders.length,renders);assert.equal(f.statuses.length,statuses);
}
""")

    def test_script_failure_still_observes_late_data_rejection(self) -> None:
        self.run_node(r"""
for(const name of ['payroll','inventory']) {
  const f=fixture(name),pending=f.invoke(),initialRenders=f.renders.length;f.scripts[0].onerror();await pending;
  for(const request of f.requests)request.reject(new Error('late read failure'));
  await tick();assert.equal(f.renders.length,initialRenders);assert.equal(f.state.employeesReferencePromise ?? null,null);
}
""")

    def test_inventory_shell_opens_once_and_blocks_actions_but_not_close(self) -> None:
        self.run_node(r"""
const f=fixture('inventory'),pending=f.invoke();
assert.equal(f.renders.filter(value=>value==='open-inventory').length,1,'shell must open before module');
assert.equal(f.workspace.inert,true,'uninitialized controls must not enqueue lazy writes');
assert.match(f.els.inventoryStatusLine.textContent,/ЗАГРУЖАЮ/);
if(!f.workspace.inert)f.context.invokeBoardModule('inventory','saveInventoryItem',[]);
assert.equal(f.requests.length,1);assert.equal(f.invoke(),pending);
f.finishModule();await tick();assert.equal(f.workspace.inert,true,'data-pending shell remains inert');
f.reply();await pending;assert.equal(f.workspace.inert,false);assert.equal(f.els.inventoryStatusLine.textContent,'');
assert.equal(f.renders.filter(value=>value==='open-inventory').length,1);
assert.equal(f.renders.filter(value=>value==='inventory').length,1,'cold shell renders populated workspace once');
assert.equal(f.renders.includes('restore-focus'),false);
""")

    def test_inventory_close_and_reopen_before_factory_preserve_the_new_invocation(self) -> None:
        self.run_node(r"""
for(const reopen of [false,true]) for(const reject of [false,true]) {
 const f=fixture('inventory'),old=f.invoke(),entry=f.state.modalStack[0];
 assert.ok(entry);f.context.popModal('inventory');assert.equal(f.state.modalStack.length,0);
 const current=reopen?f.invoke():null,afterCloseRenders=f.renders.length;
 if(reopen){assert.notEqual(current,old);assert.notEqual(f.state.modalStack[0],entry);assert.equal(f.requests.length,2);}
 f.finishModule();if(reject)f.requests[0].reject(new Error('old A'));else f.requests[0].resolve({items:[{id:'A'}]});
 await old;await tick();assert.equal(f.renders.length,afterCloseRenders);assert.equal(f.statuses.length,0);
 if(reopen){assert.equal(f.invoke(),current,'old finally deleted new invocation');assert.equal(f.workspace.inert,true);
   f.requests[1].resolve({items:[{id:'B'}]});await current;assert.equal(f.state.inventoryItems[0].id,'B');assert.equal(f.workspace.inert,false);}
 else {assert.equal(f.els.inventoryModal.classList.contains('is-open'),false);assert.equal(f.state.inventoryItems.length,0);}
}
""")

    def test_inventory_late_data_and_script_failure_keep_close_retry_usable(self) -> None:
        self.run_node(r"""
const f=fixture('inventory'),failed=f.invoke();f.scripts[0].onerror();await failed;
assert.equal(f.workspace.inert,true);assert.match(f.els.inventoryStatusLine.textContent,/Повторите открытие/);
f.context.popModal('inventory');const retry=f.invoke();assert.equal(f.requests.length,2);
f.requests[0].reject(new Error('old script read'));f.finishModule();f.requests[1].resolve({items:[{id:'retry'}]});
await retry;assert.equal(f.workspace.inert,false);assert.equal(f.state.inventoryItems[0].id,'retry');
for(const reject of [false,true]) {
 const late=fixture('inventory'),old=late.invoke();late.finishModule();await tick();late.context.popModal('inventory');
 const newer=late.invoke();assert.equal(late.workspace.inert,false,'warm reopen clears closed shell inert');
 late.requests[1].resolve({items:[{id:'new'}]});await newer;const count=late.renders.length;
 if(reject)late.requests[0].reject(new Error('old data'));else late.requests[0].resolve({items:[{id:'old'}]});
 await old;assert.equal(late.state.inventoryItems[0].id,'new');assert.equal(late.renders.length,count);
}
""")
