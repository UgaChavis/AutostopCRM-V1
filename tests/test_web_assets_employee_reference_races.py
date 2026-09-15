from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/minimal_kanban/web_app_assets/source"


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class EmployeeReferenceRaceTests(unittest.TestCase):
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
