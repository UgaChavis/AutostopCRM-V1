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
    inventoryLoaded:false,inventoryItems:[],inventoryQuery:'',inventoryView:'items'};
  const els = {employeesMonthInput:{value:''},employeesModal:{querySelector:()=>null},inventoryModal:{}};
  let canView = true;
  const context = vm.createContext({state,els,console,window:{},HTMLElement:class {},
    BOARD_MODULE_MANIFEST:{payroll:'/payroll.js',inventory:'/inventory.js'},
    document:{activeElement:null,createElement:()=>({remove(){}}),head:{appendChild:script=>scripts.push(script)}},
    currentPayrollMonthValue:()=> '2026-09',operatorCanAccessEmployeesCashboxes:()=>canManage,
    operatorCanViewEmployees:()=>canView,requireEmployeesViewAccess:()=>canView,
    api(path,options) {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});
      requests.push({path,options,resolve,reject});return promise;},
    setStatus:message=>statuses.push(message),inventoryStatus:()=>{},
    pushModal:()=>renders.push('open-payroll'),maybeOpenModal:(_,open)=>{if(open)renders.push('open-inventory');},
  });
  for(const file of ['module_loader.js','employees_reference.js','inventory_reference.js']) {
    if(fs.existsSync(source+file)) vm.runInContext(fs.readFileSync(source+file,'utf8'),context);
  }
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
  function reply(marker='ready') {for(const request of requests) request.resolve({employees:[{id:marker}],items:[{id:marker}],marker});}
  return {context,state,els,requests,scripts,renders,statuses,finishModule,reply,
    invoke:()=>context.invokeBoardModule(name,method,[]),deny:()=>{canView=false;}};
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
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cold_reads_overlap_script_without_early_apply_or_duplicate_calls(self) -> None:
        self.run_node(r"""
for(const name of ['payroll','inventory']) for(const dataFirst of [true,false]) {
  const f=fixture(name);assert.equal(f.requests.length,0);assert.equal(f.scripts.length,0);
  const pending=f.invoke();assert.equal(f.requests.length,name==='payroll'?2:1,'reads must start before script completion');
  assert.equal(f.invoke(),pending,'cold invocation must be deduplicated');assert.equal(f.scripts.length,1);
  if(dataFirst){f.reply();await tick();assert.equal(f.renders.length,0);assert.equal(f.state.employees.length,0);assert.equal(f.state.inventoryItems.length,0);}
  f.finishModule();await tick();
  if(!dataFirst){assert.equal(f.invoke(),pending,'factory-ready/data-pending invocation must still be deduplicated');f.reply();}
  await pending;assert.equal(f.requests.length,name==='payroll'?2:1);
  assert.equal(f.renders.filter(value=>value==='open-'+name).length,1);
  assert.equal((name==='payroll'?f.state.employees:f.state.inventoryItems)[0].id,'ready');
  const count=f.requests.length,warm=f.invoke();assert.equal(f.requests.length,count+1,'warm direct path performs its existing read');
  f.reply();await warm;
}
""")

    def test_cold_viewer_access_month_and_request_changes_do_not_apply_or_open(self) -> None:
        self.run_node(r"""
for(const name of ['payroll','inventory']) for(const change of ['viewer','session','request',...(name==='payroll'?['access','month']:[])]) {
  const f=fixture(name),pending=f.invoke();assert.ok(f.requests.length);
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
  assert.equal(f.renders.length,0,name+': stale open/render');assert.equal(f.statuses.length,0,name+': stale status');
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
  assert.equal(f.requests.length-count,name==='payroll'?2:1,'retry must perform one fresh request set');
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
  for(const request of f.requests.slice(offset))request.resolve({employees:[{id:'B'}],items:[{id:'B'}]});
  await current;const renders=f.renders.length,statuses=f.statuses.length;
  for(const request of oldRequests) {
    if(reject)request.reject(new Error('old A error'));else request.resolve({employees:[{id:'A'}],items:[{id:'A'}]});
  }
  await old;assert.equal((name==='payroll'?f.state.employees:f.state.inventoryItems)[0].id,'B');
  assert.equal(f.renders.length,renders);assert.equal(f.statuses.length,statuses);
}
""")

    def test_script_failure_still_observes_late_data_rejection(self) -> None:
        self.run_node(r"""
for(const name of ['payroll','inventory']) {
  const f=fixture(name),pending=f.invoke();f.scripts[0].onerror();await pending;
  for(const request of f.requests)request.reject(new Error('late read failure'));
  await tick();assert.equal(f.renders.length,0);assert.equal(f.state.employeesReferencePromise ?? null,null);
}
""")
