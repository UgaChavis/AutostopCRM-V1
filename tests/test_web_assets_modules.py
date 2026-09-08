from __future__ import annotations

import gzip
import hashlib
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.web_app_assets.assembler import (  # noqa: E402
    BOARD_WEB_APP_JS,
    BOARD_WEB_APP_MODULE_MANIFEST,
    BOARD_WEB_APP_MODULES,
)


class BoardModuleAssetsTests(unittest.TestCase):
    def test_optional_modules_are_hashed_served_compressed_and_not_in_startup(self) -> None:
        from minimal_kanban.api.server import _board_asset_bytes, _board_asset_gzip_bytes

        self.assertEqual(
            set(BOARD_WEB_APP_MODULE_MANIFEST), {"printing", "payroll", "inventory", "cash_journal"}
        )
        for path, source in BOARD_WEB_APP_MODULES.items():
            raw = source.encode("utf-8")
            self.assertEqual(path, f"/assets/board.{hashlib.sha256(raw).hexdigest()}.js")
            self.assertEqual(
                _board_asset_bytes(path), (raw, "application/javascript; charset=utf-8")
            )
            self.assertEqual(gzip.decompress(_board_asset_gzip_bytes(path)), raw)
            self.assertNotIn(source, BOARD_WEB_APP_JS)
        self.assertLess(len(BOARD_WEB_APP_JS.encode("utf-8")), 1_191_218 * 0.75)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_loader_once_initialization_retry_and_viewer_invalidation(self) -> None:
        loader = (ROOT / "src/minimal_kanban/web_app_assets/source/module_loader.js").read_text(
            encoding="utf-8"
        )
        script = (
            """
const assert = require('node:assert/strict');
const BOARD_MODULE_MANIFEST = {inventory:'/inventory.js',payroll:'/payroll.js',printing:'/printing.js',cash_journal:'/journal.js'};
const state={viewerStateGeneration:0,editingId:'A'}, els={};
const scripts=[],statuses=[],calls=[];
let initialized=0,resets=0;
const window={};
const document={createElement(){return {remove(){this.removed=true;}};},head:{appendChild(script){scripts.push(script);}}};
async function api(){return {};}
function setStatus(message){statuses.push(message);}
"""
            + loader
            + """
(async()=>{
  const first=ensureBoardModule('inventory'),second=ensureBoardModule('inventory');
  assert.equal(first,second);assert.equal(scripts.length,1);
  window.registerBoardModule('inventory',context=>{assert.equal(context.state,state); initialized++;return {open:value=>calls.push(value),resetViewer:()=>resets++};});
  scripts[0].onload(); await first;
  invokeBoardModule('inventory','open',['loaded']);
  assert.deepEqual(calls,['loaded']);assert.equal(initialized,1);
  resetBoardModules();await ensureBoardModule('inventory');assert.equal(resets,1);assert.equal(scripts.length,1);
  const failed=ensureBoardModule('payroll');scripts.at(-1).onerror();await assert.rejects(failed);
  const retry=ensureBoardModule('payroll');assert.equal(scripts.length,3);
  window.registerBoardModule('payroll',()=>({open:()=>calls.push('payroll')}));scripts.at(-1).onload();await retry;
  const stale=invokeBoardModule('printing','open',['old']);
  state.viewerStateGeneration++;
  const current=invokeBoardModule('printing','open',['current']);
  window.registerBoardModule('printing',()=>({open:value=>calls.push(value)}));scripts.at(-1).onload();
  await Promise.all([stale,current]);assert.equal(calls.includes('old'),false);assert.equal(calls.includes('current'),true);
  const obsolete=invokeBoardModule('cash_journal','open',[]);state.viewerStateGeneration++;
  scripts.at(-1).onerror();await obsolete;assert.deepEqual(statuses,[]);
  const generation=state.viewerStateGeneration;
  assert.equal(invokeBoardModule('cash_journal','close',[],true),true);
  assert.equal(state.viewerStateGeneration,generation);
  console.log('module lifecycle verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )
        result = subprocess.run(
            ["node"], input=script, text=True, capture_output=True, cwd=ROOT, timeout=15
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
