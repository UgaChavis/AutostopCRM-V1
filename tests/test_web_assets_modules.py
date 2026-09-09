from __future__ import annotations

import gzip
import hashlib
import json
import re
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
    def test_lazy_modules_export_only_invoked_entrypoints(self) -> None:
        for group, path in BOARD_WEB_APP_MODULE_MANIFEST.items():
            module = BOARD_WEB_APP_MODULES[path]
            match = re.search(r"\n    return \{([^}]*)\};\n\}\);\n$", module)
            self.assertIsNotNone(match, group)
            actual = {name.strip() for name in match.group(1).split(",") if name.strip()}
            expected = set(
                re.findall(
                    rf"invokeBoardModule\(['\"]{re.escape(group)}['\"], ['\"]([A-Za-z_$][\w$]*)",
                    BOARD_WEB_APP_JS,
                )
            )
            if group == "printing":
                expected.add("resetViewer")
            self.assertEqual(actual, expected, group)

    def test_cold_overlap_keeps_only_read_helpers_eager(self) -> None:
        for group, helper, lazy_entry in (
            ("payroll", "prepareEmployeesWorkspaceData", "renderEmployeesWorkspace"),
            ("inventory", "readInventoryItems", "renderInventory"),
        ):
            module = BOARD_WEB_APP_MODULES[BOARD_WEB_APP_MODULE_MANIFEST[group]]
            self.assertEqual(BOARD_WEB_APP_JS.count(f"function {helper}("), 1)
            self.assertNotIn(f"function {helper}(", module)
            self.assertIn(f"function {lazy_entry}(", module)
            self.assertNotIn(f"function {lazy_entry}(", BOARD_WEB_APP_JS)

    def test_optional_modules_are_hashed_served_compressed_and_not_in_startup(self) -> None:
        from minimal_kanban.api.server import _board_asset_bytes, _board_asset_gzip_bytes

        self.assertEqual(
            set(BOARD_WEB_APP_MODULE_MANIFEST),
            {"printing", "payroll", "inventory", "cash_journal", "auxiliary"},
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

    def test_auxiliary_workspaces_are_lazy_while_popup_entrypoints_stay_eager(self) -> None:
        module = BOARD_WEB_APP_MODULES[BOARD_WEB_APP_MODULE_MANIFEST["auxiliary"]]
        for lazy_signature in (
            "function normalizedDisplayDashboardMessage(value)",
            "async function openDisplayDashboardMessageEditor()",
            "function sharedFileById(fileId)",
            "async function openSharedFilesModal()",
            "async function uploadSharedFiles(files,",
            "function renderMobileArchivePanel()",
            "function renderMobileSharedFilesPanel()",
        ):
            self.assertIn(lazy_signature, module)
            self.assertNotIn(lazy_signature, BOARD_WEB_APP_JS)
        for popup_name in ("openDisplayDashboard", "openModuleMap"):
            self.assertIn(f"function {popup_name}(", BOARD_WEB_APP_JS)
            self.assertNotIn(f"function {popup_name}(", module)
        for passive_name in (
            "clearDisplayDashboardImageDrafts",
            "hideSharedFilesContextMenu",
            "handleSharedFilesPaste",
            "handleSharedFilesDocumentClick",
            "handleSharedFilesGlobalKeydown",
            "renderMobileArchivePanel",
            "renderMobileSharedFilesPanel",
        ):
            self.assertIn(
                f'function {passive_name}(...args) {{ return invokeBoardModule("auxiliary", '
                f'"{passive_name}", args, true); }}',
                BOARD_WEB_APP_JS,
            )

    def test_auxiliary_lexical_dependencies_are_explicit(self) -> None:
        source_dir = ROOT / "src/minimal_kanban/web_app_assets/source"
        eager_source = (source_dir / "app_main_before_printing.js").read_text(encoding="utf-8")
        auxiliary_source = "\n".join(
            (source_dir / name).read_text(encoding="utf-8")
            for name in (
                "display_dashboard_workspace.js",
                "shared_files_workspace.js",
                "mobile_auxiliary_workspace.js",
            )
        )

        def top_level_definitions(source: str) -> set[str]:
            names = set(
                re.findall(
                    r"^    (?:async )?function\s+([A-Za-z_$][\w$]*)\s*\(",
                    source,
                    re.MULTILINE,
                )
            )
            names.update(
                re.findall(
                    r"^    (?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
                    source,
                    re.MULTILINE,
                )
            )
            return names

        eager_definitions = top_level_definitions(eager_source)
        auxiliary_definitions = top_level_definitions(auxiliary_source)
        auxiliary_identifiers = set(re.findall(r"\b[A-Za-z_$][\w$]*\b", auxiliary_source))
        lexical_dependencies = (auxiliary_identifiers & eager_definitions) - auxiliary_definitions

        module = BOARD_WEB_APP_MODULES[BOARD_WEB_APP_MODULE_MANIFEST["auxiliary"]]
        shared_match = re.search(
            r"    const \{\n(?P<body>.*?)    \} = context\.shared;",
            module,
            re.DOTALL,
        )
        self.assertIsNotNone(shared_match)
        shared_names = set(
            re.findall(r"^      ([A-Za-z_$][\w$]*),$", shared_match["body"], re.MULTILINE)
        )
        self.assertEqual(
            lexical_dependencies,
            shared_names | {"api", "els", "setStatus", "state"},
        )

        builder_start = eager_source.index("    function buildBoardModuleSharedContext(name) {")
        builder_end = eager_source.index("    const SNAPSHOT_POLL_INTERVAL_MS", builder_start)
        builder_names = set(
            re.findall(
                r"^        ([A-Za-z_$][\w$]*),$",
                eager_source[builder_start:builder_end],
                re.MULTILINE,
            )
        )
        self.assertEqual(builder_names, shared_names)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_auxiliary_module_uses_explicit_context_for_live_entrypoints(self) -> None:
        module = BOARD_WEB_APP_MODULES[BOARD_WEB_APP_MODULE_MANIFEST["auxiliary"]]
        script = f"""
const assert = require('node:assert/strict');
let auxiliaryFactory = null;
const window = {{
  registerBoardModule(name, factory) {{
    assert.equal(name, 'auxiliary');
    auxiliaryFactory = factory;
  }},
  getSelection() {{ return null; }},
}};
const URL = {{createObjectURL() {{ return 'blob:test'; }}, revokeObjectURL() {{}}}};
const requestAnimationFrame = (callback) => callback();
class HTMLButtonElement {{}}
eval({json.dumps(module)});
assert.equal(typeof auxiliaryFactory, 'function');

const calls = [];
const modalCalls = [];
let dashboardFocused = false;
let sharedFilesFocused = false;
let deferredDashboardResolve = null;
let deferDashboard = false;
const state = {{
  actor: 'operator', apiToken: '', operatorSessionToken: 'session-a', viewerStateGeneration: 0,
  displayDashboardContextGeneration: 0, displayDashboardOpenRequest: null,
  displayDashboardSaveRequest: null, displayDashboardMessage: null,
  displayDashboardMessageSaving: false, displayDashboardExistingImageIds: [],
  displayDashboardExistingImageUrls: new Map(), displayDashboardPendingImages: [],
  displayDashboardSelectionRange: null, snapshot: null,
  sharedFiles: [], sharedFilesStorage: null, sharedFilesActiveId: '',
  sharedFilesClipboardId: '', sharedFilesRequestSeq: 0, sharedFilesMutationRequest: null,
  modalStack: [{{key: 'settings'}}],
}};
const els = {{
  displayDashboardMessageEditor: {{innerHTML: '', focus() {{ dashboardFocused = true; }}}},
  displayDashboardMessageSaveButton: {{disabled: true}},
  displayDashboardEmojiPalette: {{hidden: false}},
  displayDashboardEmojiButton: {{setAttribute() {{}}}},
  displayDashboardMessageModal: {{}},
  displayDashboardMessageImages: {{innerHTML: '', appendChild() {{}}}},
  displayDashboardImageInput: {{value: ''}},
  displayDashboardMessageMeta: {{textContent: ''}},
  sharedFilesDesktop: {{
    innerHTML: '', querySelectorAll() {{ return []; }},
    focus() {{ sharedFilesFocused = true; }},
  }},
  sharedFilesMeta: {{textContent: ''}},
  sharedFilesModal: {{}},
}};
async function api(path) {{
  calls.push(path);
  if (path === '/api/get_display_dashboard') {{
    if (deferDashboard) return new Promise((resolve) => {{ deferredDashboardResolve = resolve; }});
    return {{message_board: {{revision: 'dashboard-r1', body_html: '<b>ready</b>', image_file_ids: []}}}};
  }}
  if (path === '/api/list_shared_files') {{
    return {{files: [], storage: {{used_bytes: 0, limit_bytes: 1024}}}};
  }}
  throw new Error('unexpected API path: ' + path);
}}
const shared = {{
  ATTACHMENT_MIME_TO_EXTENSION: {{}},
  DISPLAY_DASHBOARD_MAX_IMAGES: 8,
  SHARED_FILE_UPLOAD_MAX_SIZE_BYTES: 25 * 1024 * 1024,
  applyArchivedCardPatch() {{}}, archivedCardsTotal() {{ return 0; }},
  arrayBufferToBase64() {{ return ''; }}, attachmentExtension() {{ return ''; }},
  attachmentMimeTypeFromExtension() {{ return ''; }},
  captureViewerRequestContext() {{
    return {{
      actorName: state.actor, apiToken: state.apiToken,
      operatorSessionToken: state.operatorSessionToken,
      isCurrent: () => state.viewerStateGeneration === 0 && state.operatorSessionToken === 'session-a',
    }};
  }},
  cardHeading() {{ return ''; }}, clipboardAttachmentName() {{ return ''; }},
  columnLabelById() {{ return ''; }}, downloadAttachment() {{}},
  escapeHtml(value) {{ return String(value ?? ''); }}, filteredArchiveCards() {{ return []; }},
  finiteNonNegativeNumber(value) {{ return Math.max(0, Number(value) || 0); }},
  finiteNumber(value, fallback = 0) {{ const result = Number(value); return Number.isFinite(result) ? result : fallback; }},
  formatBytes(value) {{ return String(value) + ' B'; }}, formatDate() {{ return 'DATE'; }},
  isModalOpen() {{ return true; }}, loadArchive() {{}},
  maybeOpenModal(_modal, openModal) {{ if (openModal) modalCalls.push('shared-files'); }},
  normalizeAttachmentMimeType(value) {{ return String(value || ''); }},
  openMobileCardDetail() {{}}, popModal() {{}},
  pushModal(name) {{ modalCalls.push(name); }}, refreshSnapshot() {{}},
  renderMobileMore() {{}}, renderMobileMoreModules() {{}},
  requireOperatorSession() {{ return true; }}, setMobileView() {{}},
  stripDescriptionFormatting(value) {{ return String(value || ''); }},
  syncMobileMorePanelChrome() {{}}, withAccessToken(value) {{ return value; }},
}};
const exported = auxiliaryFactory({{state, els, api, setStatus() {{}}, shared}});

(async () => {{
  await exported.openDisplayDashboardMessageEditor();
  assert.equal(state.displayDashboardMessage.revision, 'dashboard-r1');
  assert.equal(els.displayDashboardMessageEditor.innerHTML, '<b>ready</b>');
  assert.equal(dashboardFocused, true);
  assert.equal(modalCalls.includes('display-dashboard-message'), true);

  deferDashboard = true;
  state.modalStack = [{{key: 'settings'}}];
  const staleDashboard = exported.openDisplayDashboardMessageEditor();
  state.modalStack = [];
  deferredDashboardResolve({{message_board: {{revision: 'stale-r2', body_html: 'stale', image_file_ids: []}}}});
  await staleDashboard;
  assert.equal(state.displayDashboardMessage.revision, 'dashboard-r1');
  assert.equal(modalCalls.filter((name) => name === 'display-dashboard-message').length, 1);

  await exported.loadSharedFiles({{openModal: true}});
  assert.deepEqual(state.sharedFiles, []);
  assert.equal(els.sharedFilesMeta.textContent.startsWith('0 B / 1024 B'), true);
  await exported.openSharedFilesModal();
  assert.equal(sharedFilesFocused, true);
  assert.equal(modalCalls.includes('shared-files'), true);
      assert.deepEqual(calls, [
        '/api/get_display_dashboard',
        '/api/get_display_dashboard',
        '/api/list_shared_files',
    '/api/list_shared_files',
  ]);
  console.log('auxiliary explicit context verified');
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
        result = subprocess.run(
            ["node"], input=script, text=True, capture_output=True, cwd=ROOT, timeout=15
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_loader_once_initialization_retry_and_viewer_invalidation(self) -> None:
        loader = (ROOT / "src/minimal_kanban/web_app_assets/source/module_loader.js").read_text(
            encoding="utf-8"
        )
        script = (
            """
const assert = require('node:assert/strict');
const BOARD_MODULE_MANIFEST = {inventory:'/inventory.js',payroll:'/payroll.js',printing:'/printing.js',cash_journal:'/journal.js',auxiliary:'/auxiliary.js'};
const state={viewerStateGeneration:0,editingId:'A',modalStack:[],mobileView:'more'}, els={};
const scripts=[],statuses=[],calls=[];
let initialized=0,resets=0;
const window={};
const document={createElement(){return {remove(){this.removed=true;}};},head:{appendChild(script){scripts.push(script);}}};
async function api(){return {};}
function setStatus(message){statuses.push(message);}
function buildBoardModuleSharedContext(){return {};}
function isModalOpen(key){return state.modalStack.some(entry=>entry.key===key);}
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
  const scriptCount=scripts.length;
  assert.equal(invokeBoardModule('auxiliary','handleSharedFilesGlobalKeydown',[],true),true);
  assert.equal(scripts.length,scriptCount,'passive auxiliary handler loaded the module');
  const firstSettings={key:'settings'};state.modalStack=[firstSettings];
  const staleEditor=invokeBoardModule('auxiliary','openDisplayDashboardMessageEditor',[]);
  state.modalStack=[{key:'settings'}];
  window.registerBoardModule('auxiliary',(context)=>{assert.deepEqual(context.shared,{});return {openDisplayDashboardMessageEditor:()=>calls.push('stale-editor'),openSharedFilesModal:()=>calls.push('auxiliary')};});scripts.at(-1).onload();
  await staleEditor;assert.equal(calls.includes('stale-editor'),false);
  const auxiliary=invokeBoardModule('auxiliary','openSharedFilesModal',[]);
  await auxiliary;assert.equal(calls.includes('auxiliary'),true);
  console.log('module lifecycle verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        )
        result = subprocess.run(
            ["node"], input=script, text=True, capture_output=True, cwd=ROOT, timeout=15
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
