from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/minimal_kanban/web_app_assets/source/app_main_before_printing.js"
NODE = shutil.which("node")


def section(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset)]


@unittest.skipUnless(NODE, "Node.js is required for card workspace ownership regressions")
class CardWorkspaceContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        cls.functions = "\n".join(
            section(source, start, end)
            for start, end in (
                (
                    "    async function openCardWorkspace(",
                    "    function updateRepairOrdersTabs(",
                ),
                (
                    "    function repairOrderResponseCard(",
                    "    async function ensureRepairOrderCard(",
                ),
                ("    function cacheFullCard(", "    function boardCardElementsById("),
                (
                    "    function setCardDescriptionLoading(",
                    "    function handleCardDescriptionInput(",
                ),
                ("    async function openCardById(", "    function clearCardDropState("),
            )
        )

    def run_node(self, body: str) -> None:
        harness = r"""
const assert = require('node:assert/strict');
const state = {
  viewerStateGeneration: 1, operatorSessionToken: 'synthetic-session-a',
  cardHydrationSeq: 0, cardEditingGeneration: 0, cardHydratingId: '',
  repairOrderContextGeneration: 0, modalStack: [],
  fullCardCache: new Map(), cardFetchInFlight: new Map(),
  editingId: null, activeCard: null, cardDescriptionLoading: false,
};
const FULL_CARD_CACHE_LIMIT = 20;
const snapshots = new Map();
const requests = [], rendered = [], sideEffects = [], statuses = [], popped = [];
let modalOpen = false, repairOrderOpens = 0;
let repairOrderOpening = null;
const repairOrderData = [];
const toolbarButton = {disabled: false};
const els = {
  cardModal: {classList: {contains() { return modalOpen; }}},
  cardDescription: {value: ''}, saveCardButton: {disabled: false},
  cardDescriptionEditor: {
    textContent: '', contentEditable: 'true',
    classList: {add() {}, remove() {}}, setAttribute() {}, removeAttribute() {},
  },
  cardDescriptionToolbar: {querySelectorAll() { return [toolbarButton]; }},
};
function perfMeasureAsync(_name, callback) { return callback(); }
function snapshotCardById(id) { return snapshots.get(id) || null; }
function applyCardSeenSuppression(card) { return card; }
function syncCardDescriptionHeight() {}
function applyCardModalState(card, options = {}) {
  state.activeCard = card;
  state.editingId = card.id;
  setCardDescriptionLoading(Boolean(options.descriptionLoading));
  if (!options.descriptionLoading) {
    els.cardDescription.value = card.description || '';
    els.cardDescriptionEditor.textContent = card.description || '';
  }
  rendered.push({id: card.id, ...options});
}
function openCardModal(card, options) {
  state.cardEditingGeneration += 1;
  applyCardModalState(card, options);
  modalOpen = true;
}
function recordCardOpenSideEffects(id) { sideEffects.push(id); }
function setStatus(message, error) { statuses.push({message, error}); }
function popModal(key) { popped.push(key); }
function modalKeyForElement(element) { return element.key; }
async function openRepairOrderModal({preloadedRepairOrderData = null} = {}) {
  state.repairOrderContextGeneration += 1;
  repairOrderOpens += 1;
  repairOrderData.push(preloadedRepairOrderData);
  if (repairOrderOpening) await repairOrderOpening;
}
function api(path, options) {
  return new Promise((resolve, reject) => requests.push({path, options, resolve, reject}));
}
function full(id) { return {id, updated_at: 'revision', description: 'full-' + id}; }
function summary(id) { return {id, updated_at: 'revision'}; }
function repairOrderResponse(id) {
  return {card: {...full(id), client_id: 'client-' + id}, repair_order: {number: 'RO-' + id}};
}
function prepareCached(id) { snapshots.set(id, summary(id)); cacheFullCard(full(id)); }
function visibleState() {
  return {
    editingId: state.editingId, description: els.cardDescription.value,
    editor: els.cardDescriptionEditor.textContent,
    editable: els.cardDescriptionEditor.contentEditable,
    saveDisabled: els.saveCardButton.disabled, toolbarDisabled: toolbarButton.disabled,
    loading: state.cardDescriptionLoading, hydrating: state.cardHydratingId,
    statuses: statuses.slice(), rendered: rendered.slice(), modalOpen,
    activeCard: state.activeCard, clientId: state.pendingCardClientId,
    parent: state.repairOrderParentLayer, repairOrderOpens,
  };
}
"""
        result = subprocess.run(
            [NODE],
            input=harness
            + self.functions
            + "\n(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n",
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=ROOT,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_old_session_same_card_error_preserves_new_editor_and_pending_request(self) -> None:
        for current_completed in (False, True):
            with self.subTest(current_completed=current_completed):
                self.run_node(
                    f"const currentCompleted = {str(current_completed).lower()};\n"
                    + r"""
snapshots.set('same', summary('same'));
const old = openCardById('same');
assert.equal(requests.length, 1);
// These are the relevant real resetViewerScopedState effects, not an API shortcut.
state.viewerStateGeneration += 1;
state.cardHydrationSeq += 1;
state.operatorSessionToken = 'synthetic-session-b';
state.fullCardCache.clear();
state.cardFetchInFlight.clear();
state.activeCard = null;
state.editingId = null;
state.cardHydratingId = '';
const current = openCardWorkspace('same');
assert.equal(requests.length, 2);
if (currentCompleted) {
  requests[1].resolve({card: full('same')});
  await current;
  els.cardDescription.value = 'new-session-unsaved-text';
  els.cardDescriptionEditor.textContent = 'new-session-unsaved-text';
}
const before = visibleState();
const pending = state.cardFetchInFlight.get('same');
requests[0].reject(new Error('obsolete-session-error'));
await old;
assert.deepEqual(visibleState(), before);
assert.equal(state.cardFetchInFlight.get('same'), pending);
if (!currentCompleted) {
  requests[1].resolve({card: full('same')});
  await current;
}
assert.equal(state.cardFetchInFlight.size, 0);
"""
                )

    def test_uncached_late_success_cannot_replace_new_card_or_open_repair_order(self) -> None:
        for open_card_modal in (False, True):
            with self.subTest(open_card_modal=open_card_modal):
                self.run_node(
                    f"const openCardModalEl = {str(open_card_modal).lower()};\n"
                    + r"""
const old = openCardWorkspace('a', {
  closeModalEl: {key: 'old-parent'}, openCardModalEl, openRepairOrder: true,
});
prepareCached('b');
await openCardWorkspace('b');
const before = visibleState();
requests[0].resolve({card: full('a')});
const obsoleteResult = await old;
assert.deepEqual(visibleState(), before);
assert.deepEqual(sideEffects, ['b']);
assert.deepEqual(popped, []);
assert.equal(repairOrderOpens, 0);
assert.equal(obsoleteResult, null);
"""
                )

    def test_cached_late_success_does_not_schedule_obsolete_side_effects(self) -> None:
        self.run_node(r"""
snapshots.set('a', summary('a'));
const old = openCardWorkspace('a');
prepareCached('b');
await openCardWorkspace('b');
const before = visibleState();
requests[0].resolve({card: full('a')});
const obsoleteResult = await old;
assert.deepEqual(visibleState(), before);
assert.deepEqual(sideEffects, ['b']);
assert.equal(obsoleteResult, null);
""")

    def test_newer_pending_uncached_request_owns_hydration_before_either_modal_opens(self) -> None:
        self.run_node(r"""
const old = openCardWorkspace('a');
const current = openCardWorkspace('b');
assert.equal(state.cardEditingGeneration, 0);
assert.equal(state.cardHydratingId, 'b');
requests[0].resolve({card: full('a')});
assert.equal(await old, null);
assert.equal(state.cardHydratingId, 'b');
assert.equal(rendered.length, 0);
assert.deepEqual(sideEffects, []);
requests[1].resolve({card: full('b')});
assert.deepEqual(await current, full('b'));
assert.equal(rendered.length, 1);
assert.equal(state.editingId, 'b');
assert.equal(state.cardHydratingId, '');
assert.deepEqual(sideEffects, ['b']);
""")

    def test_closed_workspace_does_not_reopen_or_schedule_side_effects(self) -> None:
        self.run_node(r"""
const old = openCardWorkspace('a');
// resetCardModalState changes editing generation, but does not increment hydration.
state.cardEditingGeneration += 1;
state.editingId = null;
state.activeCard = null;
state.cardHydratingId = '';
modalOpen = false;
const before = visibleState();
requests[0].resolve({card: full('a')});
const obsoleteResult = await old;
assert.deepEqual(visibleState(), before);
assert.deepEqual(sideEffects, []);
assert.equal(obsoleteResult, null);
""")

    def test_current_failure_still_rejects_and_displays_description_error(self) -> None:
        self.run_node(r"""
snapshots.set('a', summary('a'));
const current = openCardWorkspace('a');
const error = new Error('current-error');
requests[0].reject(error);
await assert.rejects(current, candidate => candidate === error);
assert.equal(state.cardHydratingId, '');
assert.equal(state.cardDescriptionLoading, true);
assert.equal(els.cardDescriptionEditor.textContent, 'Не удалось загрузить описание.');
assert.equal(els.saveCardButton.disabled, true);
assert.deepEqual(sideEffects, []);
""")

    def test_current_failure_still_reaches_caller_status(self) -> None:
        self.run_node(r"""
const current = openCardById('a');
requests[0].reject(new Error('current-error'));
await current;
assert.deepEqual(statuses, [{message: 'current-error', error: true}]);
""")

    def test_cached_full_card_opens_once_without_fetch(self) -> None:
        self.run_node(r"""
prepareCached('a');
assert.deepEqual(await openCardWorkspace('a'), full('a'));
assert.equal(rendered.length, 1);
assert.equal(requests.length, 0);
assert.equal(state.cardHydratingId, '');
assert.equal(els.saveCardButton.disabled, false);
assert.deepEqual(sideEffects, ['a']);
""")

    def test_summary_opens_then_hydrates_once_for_current_request(self) -> None:
        self.run_node(r"""
snapshots.set('a', summary('a'));
const current = openCardWorkspace('a');
assert.equal(rendered.length, 1);
assert.equal(rendered[0].descriptionLoading, true);
assert.equal(requests[0].path, '/api/get_card?card_id=a');
requests[0].resolve({card: full('a')});
assert.deepEqual(await current, full('a'));
assert.equal(rendered.length, 2);
assert.equal(rendered[1].cardIsFull, true);
assert.equal(rendered[1].preserveTab, true);
assert.equal(state.cardHydratingId, '');
assert.equal(els.cardDescription.value, 'full-a');
assert.equal(els.saveCardButton.disabled, false);
assert.deepEqual(sideEffects, ['a']);
""")

    def test_current_workspace_direct_repair_order_mode_does_not_require_card_modal(self) -> None:
        self.run_node(r"""
const current = openCardWorkspace('a', {
  openCardModalEl: false, openRepairOrder: true, repairOrderParentLayer: 'employees',
});
requests[0].resolve({card: full('a')});
assert.deepEqual(await current, full('a'));
assert.equal(modalOpen, false);
assert.equal(state.editingId, 'a');
assert.equal(state.repairOrderParentLayer, 'employees');
assert.equal(repairOrderOpens, 1);
assert.deepEqual(sideEffects, ['a']);
""")

    def test_repair_order_old_session_success_and_error_preserve_current_workspace(self) -> None:
        for fails in (False, True):
            with self.subTest(fails=fails):
                self.run_node(
                    f"const fails = {str(fails).lower()};\n"
                    + r"""
const old = openRepairOrderCard('a');
state.viewerStateGeneration += 1;
state.operatorSessionToken = 'synthetic-session-b';
state.cardHydrationSeq += 1;
prepareCached('b');
await openCardWorkspace('b');
const before = visibleState();
if (fails) requests[0].reject(new Error('obsolete-session-error'));
else requests[0].resolve(repairOrderResponse('a'));
await old;
assert.deepEqual(visibleState(), before);
"""
                )

    def test_repair_order_requests_share_workspace_hydration_ownership(self) -> None:
        self.run_node(r"""
const old = openRepairOrderCard('a');
const current = openRepairOrderCard('b');
const hydration = state.cardHydrationSeq;
requests[0].resolve(repairOrderResponse('a'));
await old;
assert.equal(state.activeCard, null);
assert.equal(repairOrderOpens, 0);
assert.equal(state.cardHydrationSeq, hydration);
requests[1].resolve(repairOrderResponse('b'));
await current;
assert.equal(state.activeCard.id, 'b');
assert.equal(state.pendingCardClientId, 'client-b');
assert.equal(repairOrderOpens, 1);
assert.deepEqual(repairOrderData, [repairOrderResponse('b')]);
""")

    def test_repair_order_and_regular_workspace_supersede_each_other(self) -> None:
        for repair_order_first in (False, True):
            with self.subTest(repair_order_first=repair_order_first):
                self.run_node(
                    f"const repairOrderFirst = {str(repair_order_first).lower()};\n"
                    + r"""
const old = repairOrderFirst ? openRepairOrderCard('a') : openCardWorkspace('a');
const current = repairOrderFirst ? openCardWorkspace('b') : openRepairOrderCard('b');
const before = visibleState();
requests[0].resolve(repairOrderFirst ? repairOrderResponse('a') : {card: full('a')});
await old;
assert.deepEqual(visibleState(), before);
requests[1].resolve(repairOrderFirst ? {card: full('b')} : repairOrderResponse('b'));
await current;
assert.equal(state.activeCard.id, 'b');
"""
                )

    def test_repair_order_close_or_context_change_discards_success_and_error(self) -> None:
        for changed in ("editing", "repair_order", "session", "parent_close", "parent_reopen"):
            for fails in (False, True):
                with self.subTest(changed=changed, fails=fails):
                    self.run_node(
                        f"const changed = '{changed}', fails = {str(fails).lower()};\n"
                        + r"""
state.modalStack = [{key: 'repair-orders'}];
const old = openRepairOrderCard('a');
if (changed === 'editing') state.cardEditingGeneration += 1;
if (changed === 'repair_order') state.repairOrderContextGeneration += 1;
if (changed === 'session') state.operatorSessionToken = 'synthetic-session-b';
if (changed === 'parent_close') state.modalStack = [];
if (changed === 'parent_reopen') state.modalStack = [{key: 'repair-orders'}];
const before = visibleState();
if (fails) requests[0].reject(new Error('obsolete-context-error'));
else requests[0].resolve(repairOrderResponse('a'));
await old;
assert.deepEqual(visibleState(), before);
"""
                    )

    def test_current_repair_order_direct_and_parent_modes_keep_single_mutating_request(
        self,
    ) -> None:
        for with_parent in (False, True):
            with self.subTest(with_parent=with_parent):
                self.run_node(
                    f"const withParent = {str(with_parent).lower()};\n"
                    + r"""
state.actor = 'synthetic-operator';
if (withParent) state.modalStack = [{key: 'employees'}];
const current = openRepairOrderCard('  a  ', {parentLayer: 'employees'});
assert.equal(requests.length, 1);
assert.equal(requests[0].path, '/api/get_repair_order');
assert.deepEqual(requests[0].options, {method: 'POST', body: {
  card_id: 'a', actor_name: 'synthetic-operator', source: 'ui', create_if_missing: true,
}});
requests[0].resolve(repairOrderResponse('a'));
assert.equal(await current, undefined);
assert.equal(requests.length, 1);
assert.equal(state.activeCard.id, 'a');
assert.equal(state.editingId, 'a');
assert.equal(state.pendingCardClientId, 'client-a');
assert.equal(state.repairOrderParentLayer, 'employees');
assert.deepEqual(repairOrderData, [repairOrderResponse('a')]);
assert.deepEqual(statuses, []);
assert.equal(modalOpen, false);
"""
                )

    def test_current_repair_order_request_failure_is_displayed(self) -> None:
        self.run_node(r"""
const current = openRepairOrderCard('a');
requests[0].reject(new Error('current-request-error'));
await current;
assert.deepEqual(statuses, [{message: 'current-request-error', error: true}]);
assert.equal(repairOrderOpens, 0);
assert.equal(requests.length, 1);
""")

    def test_repair_order_delegate_error_accounts_for_own_epoch_but_not_new_context(self) -> None:
        for obsolete in (False, True):
            with self.subTest(obsolete=obsolete):
                self.run_node(
                    f"const obsolete = {str(obsolete).lower()};\n"
                    + r"""
let rejectOpening;
repairOrderOpening = new Promise((_resolve, reject) => { rejectOpening = reject; });
const current = openRepairOrderCard('a');
requests[0].resolve(repairOrderResponse('a'));
await Promise.resolve();
await Promise.resolve();
assert.equal(repairOrderOpens, 1);
assert.equal(state.repairOrderContextGeneration, 1);
if (obsolete) {
  prepareCached('b');
  await openCardWorkspace('b');
}
const before = visibleState();
rejectOpening(new Error('opening-error'));
await current;
if (obsolete) assert.deepEqual(visibleState(), before);
else assert.deepEqual(statuses, [{message: 'opening-error', error: true}]);
"""
                )


if __name__ == "__main__":
    unittest.main()
