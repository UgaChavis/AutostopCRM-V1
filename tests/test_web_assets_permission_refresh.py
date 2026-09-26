from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

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

    def test_profile_responses_cannot_roll_back_permissions(self) -> None:
        source = read_board_source("app_main_before_printing.js")
        code = section(
            source,
            "    async function loadOperatorProfile(",
            "    function refreshOperatorProfileAfterPermissionMismatch(",
        )
        self.run_js(
            """
const assert=require('node:assert/strict');
const state={viewerStateGeneration:1,operatorSessionToken:'S',personalBoardPreferencesRevision:1};
const calls=[],rendered=[];
function api(){return new Promise(resolve=>calls.push(resolve));}
function renderOperatorProfile(data){state.operatorProfile=data;rendered.push(data);}
const els={operatorProfileModal:{}};let opens=0;
function pushModal(){opens++;}
"""
            + code
            + """
(async()=>{
  const old=loadOperatorProfile(true);const newer=loadOperatorProfile();
  calls[1]({user:{updated_at:'2026-09-14T12:00:01+00:00',permissions:['employees_read_access']}});await newer;
  calls[0]({user:{updated_at:'2026-09-14T12:00:00+00:00',permissions:[]}});await old;
  assert.equal(rendered.length,1);assert.deepEqual(state.operatorProfile.user.permissions,['employees_read_access']);
  assert.equal(opens,1,'background refresh must not swallow a profile-open click');
})().catch(e=>{console.error(e);process.exitCode=1});
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
function beginOperatorUserSaveMutation(){return {viewer:{isCurrent:()=>true},isCurrent:()=>true};}
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
