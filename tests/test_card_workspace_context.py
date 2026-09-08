from __future__ import annotations

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.web_assets import (  # noqa: E402
    BOARD_WEB_APP_CONTRACT_TEXT as BOARD_WEB_APP_HTML,
)

SOURCE = ROOT / "src/minimal_kanban/web_app_assets/source/app_main_before_printing.js"
NODE = shutil.which("node")


def section(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset)]


class RepairOrderWorkspaceContractTests(unittest.TestCase):
    def test_topbar_repair_orders_list_uses_compact_row_open_flow(self) -> None:
        modal_backdrop_rule = re.search(
            r"\.modal \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(modal_backdrop_rule)
        assert modal_backdrop_rule is not None
        self.assertIn("background: rgba(7, 10, 8, 0.96);", modal_backdrop_rule.group("body"))
        self.assertIn('id="repairOrdersButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrdersModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrdersList"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrdersTableHead"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrdersSearchInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrdersSearchSpinner"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrdersSortBy"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrdersSortDir"', BOARD_WEB_APP_HTML)
        self.assertIn("function openRepairOrdersModal()", BOARD_WEB_APP_HTML)
        self.assertIn("async function handleRepairOrdersListClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("async function handleRepairOrdersListKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertIn("loadRepairOrders = async function(openModal = false)", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrdersHasReusableOpenList()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const canReuseOpenList = repairOrdersHasReusableOpenList();", BOARD_WEB_APP_HTML
        )
        self.assertIn("if (canReuseOpenList) {", BOARD_WEB_APP_HTML)
        self.assertIn("maybeOpenModal(els.repairOrdersModal, true);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function openRepairOrderCard(cardId, { parentLayer = 'repair-orders' } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("repairOrderParentLayer: ''", BOARD_WEB_APP_HTML)
        self.assertIn("state.repairOrderParentLayer = 'card';", BOARD_WEB_APP_HTML)
        self.assertIn("if (parentLayer === 'repair-orders') {", BOARD_WEB_APP_HTML)
        self.assertIn("resetCardModalState();", BOARD_WEB_APP_HTML)
        self.assertIn("const data = await api('/api/get_repair_order'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const opening = openRepairOrderModal({ preloadedRepairOrderData: data });",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("await opening;", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "await openCardWorkspace(cardId, { closeModalEl: els.repairOrdersModal, openRepairOrder: true });",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function repairOrdersRequestPath()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "params.set('status', normalizeRepairOrderStatus(state.repairOrdersFilter));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "params.set('sort_by', normalizeRepairOrdersSortBy(state.repairOrdersSortBy));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "params.set('sort_dir', normalizeRepairOrdersSortDir(state.repairOrdersSortDir));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "if (state.repairOrdersRemoteQuery) params.set('query', state.repairOrdersRemoteQuery);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("repairOrdersSortBy: 'number'", BOARD_WEB_APP_HTML)
        self.assertIn("repairOrdersSortDir: 'desc'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "return REPAIR_ORDER_SORT_FIELDS.includes(normalized) ? normalized : 'number';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("state.repairOrdersSortBy = 'number';", BOARD_WEB_APP_HTML)
        self.assertIn('<option value="number" selected>Номер</option>', BOARD_WEB_APP_HTML)
        self.assertIn("'/api/open_card'", BOARD_WEB_APP_HTML)
        self.assertIn("data-open-repair-order-card", BOARD_WEB_APP_HTML)
        repair_order_card_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "async function openRepairOrderCard(cardId, { parentLayer = 'repair-orders' } = {})"
            ) : BOARD_WEB_APP_HTML.index("function updateRepairOrdersTabs()")
        ]
        self.assertIn("'/api/get_repair_order'", repair_order_card_fragment)
        self.assertIn("preloadedRepairOrderData", repair_order_card_fragment)
        self.assertNotIn("openCardWorkspace", repair_order_card_fragment)
        self.assertNotIn(
            "state.repairOrderParentLayer = 'repair-orders';", repair_order_card_fragment
        )
        self.assertIn(
            "state.repairOrderParentLayer = String(parentLayer || 'repair-orders').trim();",
            repair_order_card_fragment,
        )
        self.assertIn(".repair-orders-workspace {", BOARD_WEB_APP_HTML)
        self.assertIn("width: min(1940px, 100%);", BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr);", BOARD_WEB_APP_HTML)
        repair_orders_modal_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                '<div class="modal" id="repairOrdersModal">'
            ) : BOARD_WEB_APP_HTML.index('<div class="modal" id="clientsModal">')
        ]
        self.assertIn('class="repair-orders-workspace"', repair_orders_modal_fragment)
        self.assertIn(
            'class="repair-orders-toolbar dialog__floating-actions"', repair_orders_modal_fragment
        )
        self.assertIn(
            'class="repair-orders-controls repair-orders-controls--header"',
            repair_orders_modal_fragment,
        )
        self.assertIn(
            'class="dialog dialog--repair-orders dialog--fixed-actions"',
            repair_orders_modal_fragment,
        )
        self.assertIn(
            'class="dialog__body-scroll repair-orders-body-scroll"', repair_orders_modal_fragment
        )
        self.assertIn(
            'id="repairOrdersPanelTitle">ЗАКАЗ-НАРЯДЫ</div>', repair_orders_modal_fragment
        )
        self.assertLess(
            repair_orders_modal_fragment.index('class="repair-orders-toolbar'),
            repair_orders_modal_fragment.index('class="dialog dialog--repair-orders'),
        )
        self.assertLess(
            repair_orders_modal_fragment.index(
                'class="dialog__body-scroll repair-orders-body-scroll"'
            ),
            repair_orders_modal_fragment.index('id="repairOrdersTableHead"'),
        )
        self.assertLess(
            repair_orders_modal_fragment.index('id="repairOrdersTableHead"'),
            repair_orders_modal_fragment.index('id="repairOrdersList"'),
        )
        self.assertLess(
            repair_orders_modal_fragment.index("repair-orders-controls--header"),
            repair_orders_modal_fragment.index('data-close="repair-orders"'),
        )
        self.assertNotIn(
            '<div class="repair-orders-controls">\n        <div class="field field--compact">',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="wall-meta repair-orders-meta" id="repairOrdersMeta" hidden aria-hidden="true"',
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("ПОКАЗАНО: ' + items.length", BOARD_WEB_APP_HTML)
        self.assertIn("els.repairOrdersMeta.textContent = '';", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-controls--header {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "grid-template-columns: repeat(3, minmax(160px, 172px));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("box-sizing: border-box;", BOARD_WEB_APP_HTML)
        self.assertIn("height: 30px;", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-toolbar {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-toolbar__content {", BOARD_WEB_APP_HTML)
        toolbar_rule = re.search(
            r"\.repair-orders-toolbar \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(toolbar_rule)
        assert toolbar_rule is not None
        self.assertIn("background: transparent;", toolbar_rule.group("body"))
        self.assertIn("box-shadow: none;", toolbar_rule.group("body"))
        toolbar_action_surface_rule = re.search(
            r"\.repair-orders-toolbar \.tab-btn,\n    \.repair-orders-toolbar \[data-close=\"repair-orders\"\] \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(toolbar_action_surface_rule)
        assert toolbar_action_surface_rule is not None
        self.assertIn("background: #151c17;", toolbar_action_surface_rule.group("body"))
        self.assertIn("pointer-events: auto;", toolbar_action_surface_rule.group("body"))
        self.assertIn("transform 120ms ease;", toolbar_action_surface_rule.group("body"))
        self.assertIn(
            ".repair-orders-toolbar .tab-btn:hover:not(:disabled),",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".repair-orders-toolbar .repair-orders-controls input:focus,",
            BOARD_WEB_APP_HTML,
        )
        for selector, background in (
            ("#repairOrdersOpenTab", "#233024"),
            ("#repairOrdersOpenTab.is-active", "#4d8749"),
            ("#repairOrdersReadyTab", "#28302b"),
            ("#repairOrdersReadyTab.is-active", "#5a6862"),
            ("#repairOrdersClosedTab", "#272822"),
            ("#repairOrdersClosedTab.is-active", "#70443b"),
        ):
            tab_rule = re.search(
                rf"{re.escape(selector)} \{{(?P<body>.*?)\n    \}}",
                BOARD_WEB_APP_HTML,
                re.S,
            )
            self.assertIsNotNone(tab_rule)
            assert tab_rule is not None
            self.assertIn(f"background: {background};", tab_rule.group("body"))
        self.assertIn(".dialog--repair-orders > .repair-orders-body-scroll {", BOARD_WEB_APP_HTML)
        self.assertIn("height: min(calc(94vh + 36px), 1016px);", BOARD_WEB_APP_HTML)
        self.assertIn("scrollbar-gutter: stable;", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-body-scroll .repair-orders-table-head {", BOARD_WEB_APP_HTML)
        self.assertIn("top: 0;", BOARD_WEB_APP_HTML)
        toolbar_close_rule = re.search(
            r"\.repair-orders-toolbar \[data-close=\"repair-orders\"\] \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(toolbar_close_rule)
        assert toolbar_close_rule is not None
        self.assertIn("min-width: 96px;", toolbar_close_rule.group("body"))
        self.assertIn("min-height: 36px;", toolbar_close_rule.group("body"))
        self.assertIn(
            "repairOrderListTotalText(item.grand_total, item.works_total)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function repairOrderListDateDisplayValue(value)", BOARD_WEB_APP_HTML)
        self.assertIn("renderRepairOrderListRows = function(items)", BOARD_WEB_APP_HTML)
        repair_orders_row_rule = re.search(
            r"@keyframes repair-orders-search-spin \{.*?\n    \.repair-orders-row \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(repair_orders_row_rule)
        assert repair_orders_row_rule is not None
        self.assertIn("height: 58px;", repair_orders_row_rule.group("body"))
        self.assertIn("min-height: 58px;", repair_orders_row_rule.group("body"))
        self.assertIn("overflow: hidden;", repair_orders_row_rule.group("body"))
        self.assertIn(".repair-orders-row__title-cell {", BOARD_WEB_APP_HTML)
        repair_order_title_cell_rule = re.search(
            r"\.repair-orders-row__title-cell \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(repair_order_title_cell_rule)
        assert repair_order_title_cell_rule is not None
        self.assertIn("overflow: hidden;", repair_order_title_cell_rule.group("body"))
        self.assertIn(".repair-orders-row__number", BOARD_WEB_APP_HTML)
        repair_order_number_rule = re.search(
            r"\.repair-orders-row__number \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(repair_order_number_rule)
        assert repair_order_number_rule is not None
        self.assertIn("font-size: 16px;", repair_order_number_rule.group("body"))
        self.assertIn("font-weight: 800;", repair_order_number_rule.group("body"))
        self.assertIn(".repair-orders-row__dates", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__date-meta", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__opened", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__closed", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__status", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__client", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__phone", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__vehicle", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__title", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__tags", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-table-head", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-search-label", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-search-spinner", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-search-scope", BOARD_WEB_APP_HTML)
        search_scope_rule = re.search(
            r"\.repair-orders-search-scope \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(search_scope_rule)
        assert search_scope_rule is not None
        self.assertIn("width: 12px;", search_scope_rule.group("body"))
        self.assertIn("height: 12px;", search_scope_rule.group("body"))
        self.assertIn("flex: 0 0 12px;", search_scope_rule.group("body"))
        self.assertNotIn("width: 28px;", search_scope_rule.group("body"))
        self.assertIn(".repair-orders-table-head__searchable", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-table-head__searchable-group", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-table-head__sum", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-table-head > div {", BOARD_WEB_APP_HTML)
        self.assertIn("padding-bottom: 14px;", BOARD_WEB_APP_HTML)
        self.assertIn("scroll-padding-bottom: 14px;", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__total", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__payment-status", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__paid", BOARD_WEB_APP_HTML)
        repair_order_title_rule = re.search(
            r"\.repair-orders-row__title-cell \{.*?\n    \}\n    \.repair-orders-row__title \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(repair_order_title_rule)
        assert repair_order_title_rule is not None
        self.assertIn("-webkit-line-clamp: 2;", repair_order_title_rule.group("body"))
        repair_order_payment_status_rule = re.search(
            r"\.repair-orders-row__payment-status \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(repair_order_payment_status_rule)
        assert repair_order_payment_status_rule is not None
        self.assertIn("min-width: 108px;", repair_order_payment_status_rule.group("body"))
        self.assertIn("min-height: 28px;", repair_order_payment_status_rule.group("body"))
        self.assertIn("font-size: 12.5px;", repair_order_payment_status_rule.group("body"))
        repair_order_money_rule = re.search(
            r"\.repair-orders-row__paid,\n    \.repair-orders-row__total \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(repair_order_money_rule)
        assert repair_order_money_rule is not None
        self.assertIn("font-size: 15px;", repair_order_money_rule.group("body"))
        self.assertIn("font-weight: 800;", repair_order_money_rule.group("body"))
        self.assertIn("minmax(152px, 184px)", BOARD_WEB_APP_HTML)
        self.assertIn("minmax(109px, 1.013fr)", BOARD_WEB_APP_HTML)
        self.assertIn("minmax(168px, 203px)", BOARD_WEB_APP_HTML)
        self.assertIn("minmax(239px, 2.223fr)", BOARD_WEB_APP_HTML)
        self.assertIn("minmax(72px, 84px)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function repairOrdersColumnsValue(status = state.repairOrdersFilter)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function repairOrdersTableHeadHtml(status = state.repairOrdersFilter)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function syncRepairOrdersLayout(status = state.repairOrdersFilter)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function normalizeRepairOrdersSearchField(value)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function repairOrdersSearchFieldLabel(", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function filterRepairOrdersItems(items = state.repairOrdersItems)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function handleRepairOrdersSearchFieldClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const REPAIR_ORDER_SEARCH_FIELDS = ['number', 'date', 'client', 'phone', 'vehicle', 'summary', 'license_plate'];",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "repairOrdersTableHeadSearchableHtml('Госномер', 'license_plate')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "if (normalized === 'license_plate') return 'поиск по госномеру';", BOARD_WEB_APP_HTML
        )
        self.assertIn("СПИСОК: ДАТА / АВТО / СУТЬ / СУММА", BOARD_WEB_APP_HTML)
        self.assertIn("Даты", BOARD_WEB_APP_HTML)
        self.assertIn("Телефон", BOARD_WEB_APP_HTML)
        self.assertIn("Автомобиль", BOARD_WEB_APP_HTML)
        self.assertIn("Смысл карточки", BOARD_WEB_APP_HTML)
        self.assertIn("Госномер", BOARD_WEB_APP_HTML)
        self.assertIn("Сумма", BOARD_WEB_APP_HTML)
        self.assertIn("const datePart = canonical.split(' ')[0] || canonical;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "item.opened_at || item.created_at || item.date || item.updated_at", BOARD_WEB_APP_HTML
        )
        self.assertIn("item.grand_total, item.works_total", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.repairOrdersButton.addEventListener('click', openRepairOrdersModal);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.repairOrdersList.addEventListener('click', handleRepairOrdersListClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.repairOrdersList.addEventListener('keydown', handleRepairOrdersListKeydown);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.repairOrdersSearchInput.addEventListener('input', handleRepairOrdersSearchInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.repairOrdersTableHead.addEventListener('click', handleRepairOrdersSearchFieldClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.repairOrdersSortBy.addEventListener('change', handleRepairOrdersSortChange);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.repairOrdersSortDir.addEventListener('change', handleRepairOrdersSortChange);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function repairOrdersModalIsOpen()", BOARD_WEB_APP_HTML)
        self.assertIn("function invalidateRepairOrdersListCache()", BOARD_WEB_APP_HTML)
        self.assertIn("async function refreshRepairOrdersListAfterMutation()", BOARD_WEB_APP_HTML)
        self.assertIn("await refreshRepairOrdersListAfterMutation();", BOARD_WEB_APP_HTML)
        refresh_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "async function refreshRepairOrdersListAfterMutation()"
            ) : BOARD_WEB_APP_HTML.index("async function persistRepairOrderRecord")
        ]
        self.assertIn("if (repairOrdersModalIsOpen()) {", refresh_fragment)
        self.assertIn("await loadRepairOrders(false);", refresh_fragment)
        self.assertIn("invalidateRepairOrdersListCache();", refresh_fragment)
        persist_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "async function persistRepairOrderRecord"
            ) : BOARD_WEB_APP_HTML.index("saveRepairOrder = async function")
        ]
        self.assertIn("await refreshRepairOrdersListAfterMutation();", persist_fragment)


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
  cardHydrationSeq: 0, cardEditingGeneration: 0,
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
    loading: state.cardDescriptionLoading,
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
requests[0].resolve({card: full('a')});
assert.equal(await old, null);
assert.equal(rendered.length, 0);
assert.deepEqual(sideEffects, []);
requests[1].resolve({card: full('b')});
assert.deepEqual(await current, full('b'));
assert.equal(rendered.length, 1);
assert.equal(state.editingId, 'b');
assert.deepEqual(sideEffects, ['b']);
""")

    def test_closed_workspace_does_not_reopen_or_schedule_side_effects(self) -> None:
        self.run_node(r"""
const old = openCardWorkspace('a');
// resetCardModalState changes editing generation, but does not increment hydration.
state.cardEditingGeneration += 1;
state.editingId = null;
state.activeCard = null;
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
