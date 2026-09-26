from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/minimal_kanban/web_app_assets/source"


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class EmployeeReferenceRaceTests(unittest.TestCase):
    def run_reference_script(self, body: str) -> None:
        references = (SOURCE / "employees_reference.js").read_text(encoding="utf-8")
        script = (
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:1,operatorSessionToken:'S',employeesCashboxesAccessRevision:1,payrollMonth:'2026-09',employeesLoadedMonth:'',employees:[],activeEmployeeId:'E'};
const requests=[];
function api(path){return new Promise((resolve,reject)=>requests.push({path,resolve,reject}));}
function currentPayrollMonthValue(){return '2026-09';}
function operatorCanViewEmployees(){return true;}
"""
            + references
            + "\n(async()=>{"
            + body
            + """
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        )
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

    def test_lightweight_and_payroll_requests_and_caches_are_independent(self) -> None:
        self.run_reference_script("""
  const refs=loadEmployeesReference({referencesOnly:true});
  const refsAgain=loadEmployeesReference({referencesOnly:true});
  const full=loadEmployeesReference();
  assert.equal(requests.length,2);
  assert.match(requests[0].path,/references_only=true/);
  assert.doesNotMatch(requests[1].path,/references_only/);
  requests[0].resolve({employees:[{id:'E',name:'Reference name'}],meta:{references_only:true}});
  await Promise.all([refs,refsAgain]);
  assert.equal(state.employeeNames[0].name,'Reference name');
  assert.equal(state.employeeNames[0].balance_total,undefined);
  assert.equal(state.employeesLoadedMonth,'','lightweight data must not satisfy payroll');
  requests[1].resolve({employees:[{id:'E',balance_total:'321',base_salary:'1000'}]});
  await full;
  assert.equal(state.employeeNames,null,'fresh payroll response invalidates employee references');
  const freshRefs=loadEmployeesReference({referencesOnly:true});
  requests[2].resolve({employees:[{id:'E',name:'Fresh reference'}],meta:{references_only:true}});
  await freshRefs;
  const cachedFull=await loadEmployeesReference();
  const cachedRefs=await loadEmployeesReference({referencesOnly:true});
  assert.equal(requests.length,3);
  assert.equal(cachedFull.employees[0].balance_total,'321');
  assert.equal(cachedRefs.meta.references_only,true);
  invalidateEmployeesReference();
  assert.equal(state.employeesLoadedMonth,'');
  assert.equal(state.employeeNamesLoadedMonth,'');
  assert.equal(state.employeeNames,null);
""")

    def test_full_refresh_invalidates_light_cache_and_older_pending_read(self) -> None:
        self.run_reference_script("""
  state.employeesWorkspaceLoadGeneration=0;
  for(const preparedLoad of [false,true]) {
    invalidateEmployeesReference();
    const start=requests.length;
    const oldEmployee={id:'E',name:'Old name',position:'Mechanic',is_active:true,work_percent:'40'};
    const freshEmployee={...oldEmployee,name:'Updated name',is_active:false,work_percent:'25'};
    const initial=loadEmployeesReference({referencesOnly:true});
    requests[start].resolve({employees:[oldEmployee],meta:{references_only:true}});
    await initial;
    const oldLight=loadEmployeesReference({referencesOnly:true,force:true});
    const revision=state.employeesReferenceRevision;
    const prepared=preparedLoad
      ? prepareEmployeesWorkspaceData('2026-09',Promise.resolve(),{useEmbeddedPayrollReport:true})
      : null;
    const full=prepared ? prepared.promise : loadEmployeesReference({force:true});
    requests[start+2].resolve({
      employees:[{...freshEmployee,balance_total:'321',base_salary:'1000',salary_mode:'base',
        current_payroll_term:{work_percent:'25'},payroll_terms:[{base_salary:'1000'}]}],
      month:'2026-09',summary:{},detail_rows:[],
    });
    await full;
    assert.equal(state.employeesReferenceRevision,revision,'full apply must retain its workspace context');
    if(prepared) assert.equal(prepared.isCurrent(),true);
    assert.equal(state.employees[0].name,'Updated name');
    assert.equal(state.employeeNames,null,'full payroll objects must not enter the light cache');
    assert.equal(state.employeeNamesLoadedMonth,'');
    assert.equal(state.employeeNamesPromise,null);
    const freshLight=loadEmployeesReference({referencesOnly:true});
    assert.equal(requests.length,start+4,'full apply must detach the older lightweight request');
    const freshPending=state.employeeNamesPromise;
    requests[start+1].resolve({employees:[oldEmployee],meta:{references_only:true}});
    await oldLight;
    assert.equal(state.employeeNames,null,'late lightweight data must not restore old references');
    assert.equal(state.employeeNamesPromise,freshPending,'old completion must retain the new pending request');
    requests[start+3].resolve({employees:[freshEmployee],meta:{references_only:true}});
    await freshLight;
    const cached=await loadEmployeesReference({referencesOnly:true});
    assert.deepEqual(cached.employees,[freshEmployee]);
    assert.deepEqual(Object.keys(state.employeeNames[0]).sort(),
      ['id','is_active','name','position','work_percent']);
    assert.equal(state.employees[0].balance_total,'321');
  }
""")

    def test_reference_cache_never_satisfies_payroll_and_failed_read_can_retry(self) -> None:
        self.run_reference_script("""
  const refs=loadEmployeesReference({referencesOnly:true});
  requests[0].resolve({employees:[{id:'E',name:'Reference'}]});await refs;
  assert.equal(state.employeesLoadedMonth,'');
  const full=loadEmployeesReference();assert.equal(requests.length,2);
  requests[1].resolve({employees:[{id:'E',balance_total:'100'}]});await full;
  invalidateEmployeesReference();
  const failed=loadEmployeesReference({referencesOnly:true});
  requests[2].reject(new Error('temporary failure'));
  await assert.rejects(failed,/temporary failure/);
  const retried=loadEmployeesReference({referencesOnly:true});
  assert.equal(requests.length,4);
  requests[3].resolve({employees:[{id:'E',name:'Retried'}]});await retried;
  assert.equal(state.employeeNames[0].name,'Retried');
""")

    def test_stale_lightweight_reads_do_not_apply_or_clear_new_pending_request(self) -> None:
        self.run_reference_script("""
  for(const change of [()=>state.viewerStateGeneration++,()=>state.operatorSessionToken+='2',
    ()=>state.employeesCashboxesAccessRevision++,()=>invalidateEmployeesReference()]) {
    invalidateEmployeesReference();
    const start=requests.length;
    const old=loadEmployeesReference({referencesOnly:true});
    change();
    const current=loadEmployeesReference({referencesOnly:true});
    requests[start].resolve({employees:[{id:'E',name:'Old',work_percent:'99'}]});await old;
    assert.equal(state.employeeNames,null);
    assert.ok(state.employeeNamesPromise);
    requests[start+1].resolve({employees:[{id:'E',name:'Current'}]});await current;
    assert.equal(state.employeeNames[0].name,'Current');
  }
""")

    def test_reference_started_before_save_cannot_restore_old_employee(self) -> None:
        references = (SOURCE / "employees_reference.js").read_text(encoding="utf-8")
        workspace = (SOURCE / "payroll_workspace.js").read_text(encoding="utf-8")
        save = workspace[
            workspace.index("    async function saveEmployee()") : workspace.index(
                "    async function deleteEmployee()"
            )
        ]
        script = (
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:1,operatorSessionToken:'S',employeesCashboxesAccessRevision:1,payrollMonth:'2026-09',employeesLoadedMonth:'',employees:[],activeEmployeeId:'E'};
const els={};let reply;
function api(path){if(path.startsWith('/api/list_employees'))return new Promise(resolve=>reply=resolve);return Promise.resolve({employee:{id:'E'},employees:[{id:'E',name:'Saved name'}]});}
function currentPayrollMonthValue(){return '2026-09';}
function requireEmployeesCashboxesAccess(){return true;}
function employeeCombinedNameFromForm(){return 'Saved name';}
function employeeAsyncContext(){const c=()=>true;c.month='2026-09';c.rebindEntity=()=>c;c.release=()=>true;return c;}
function syncEmployeeEditMutationControls(){}
function readEmployeeFormPayload(){return {name:'Saved name'};}
function renderEmployeesWorkspace(){}
function refreshRepairOrderEmployeeSelects(){}
function setStatus(){}
"""
            + references
            + save
            + """
(async()=>{
  const old=loadEmployeesReference();
  await saveEmployee();assert.equal(state.employees[0].name,'Saved name');
  reply({employees:[{id:'E',name:'Old name'}]});await old;
  assert.equal(state.employees[0].name,'Saved name','late pre-save reference replaced acknowledged employee data');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        )
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

    def test_payroll_detail_index_matches_report_order_and_rebuilds_on_report_change(self) -> None:
        workspace = (SOURCE / "payroll_workspace.js").read_text(encoding="utf-8")
        helper = workspace[
            workspace.index("    function payrollDetailRowsForEmployee(") : workspace.index(
                "    function renderEmployeeProfileMeta()"
            )
        ]
        script = (
            """
const assert=require('node:assert/strict');
const firstRows=[
  {employee_id:' E1 ',row:1},{employee_id:'E2',row:2},{employee_id:'E1',row:3},
  {employee_id:'',row:4},{}
];
const state={payrollReport:{detail_rows:firstRows}};
"""
            + helper
            + """
assert.deepEqual(payrollDetailRowsForEmployee('E1').map(row=>row.row),[1,3]);
assert.deepEqual(payrollDetailRowsForEmployee(' E2 ').map(row=>row.row),[2]);
assert.deepEqual(payrollDetailRowsForEmployee('missing'),[]);
const secondRows=[{employee_id:'E1',row:5}];
state.payrollReport={detail_rows:secondRows};
assert.deepEqual(payrollDetailRowsForEmployee('E1'),secondRows);
"""
        )
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


if __name__ == "__main__":
    unittest.main()
