# ruff: noqa: I001
from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.web_assets import BOARD_WEB_APP_HTML  # noqa: E402


class _EmployeesLayoutParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, str]] = []
        self.layout_children: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        class_name = attrs_map.get("class", "")
        parent = self.stack[-1] if self.stack else None
        if (
            parent
            and parent[0] == "div"
            and "employees-layout" in parent[1].split()
            and tag == "div"
        ):
            self.layout_children.append(class_name)
        self.stack.append((tag, class_name))

    def handle_endtag(self, tag: str) -> None:
        while self.stack:
            stack_tag, _ = self.stack.pop()
            if stack_tag == tag:
                break


class WebAssetsTests(unittest.TestCase):
    def test_web_assets_facade_exports_assembled_html(self) -> None:
        from minimal_kanban.web_app_assets.assembler import BOARD_WEB_APP_HTML as assembled_html

        self.assertEqual(BOARD_WEB_APP_HTML, assembled_html)

    def test_web_assets_are_loaded_from_packaged_source_chunks(self) -> None:
        source_dir = ROOT / "src" / "minimal_kanban" / "web_app_assets" / "source"
        self.assertTrue(source_dir.is_dir())
        self.assertLess(
            (ROOT / "src" / "minimal_kanban" / "web_app_assets" / "assembler.py").stat().st_size,
            4096,
        )
        self.assertIn(
            "minimal_kanban/web_app_assets/source",
            (ROOT / "scripts" / "build_app.ps1").read_text(encoding="utf-8"),
        )

    def test_board_brand_uses_autostop_name(self) -> None:
        self.assertIn("<title>AutoStop</title>", BOARD_WEB_APP_HTML)
        self.assertIn(
            'rel="icon" type="image/png" sizes="32x32" href="/favicon.png"', BOARD_WEB_APP_HTML
        )
        self.assertIn('rel="icon" type="image/x-icon" href="/favicon.ico"', BOARD_WEB_APP_HTML)
        self.assertIn('<div class="brand__title">AUTOSTOP</div>', BOARD_WEB_APP_HTML)
        self.assertNotIn('brand__sub">МИНИМУМ ИНТЕРФЕЙСА', BOARD_WEB_APP_HTML)
        self.assertIn('id="topbarStatusHost"', BOARD_WEB_APP_HTML)
        self.assertNotIn('<div class="brand__title">КАНБАН / ПУЛЬТ</div>', BOARD_WEB_APP_HTML)

    def test_inline_javascript_does_not_embed_raw_newline_in_string_literal(self) -> None:
        self.assertIn("markdown + '\\n'", BOARD_WEB_APP_HTML)
        self.assertNotIn("markdown + '\n'", BOARD_WEB_APP_HTML)

    @unittest.skipUnless(
        shutil.which("node"), "Node.js is required for generated browser JS syntax check"
    )
    def test_generated_inline_javascript_is_syntax_valid(self) -> None:
        script = ROOT / "scripts" / "check_web_assets_js.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_board_settings_keep_slider_but_remove_wheel_zoom_binding(self) -> None:
        self.assertIn('class="gear-button" id="boardSettingsButton"', BOARD_WEB_APP_HTML)
        self.assertIn('class="gear-button__logo" src="/favicon.png"', BOARD_WEB_APP_HTML)
        self.assertIn(".gear-button {", BOARD_WEB_APP_HTML)
        self.assertIn("width: 36px;", BOARD_WEB_APP_HTML)
        self.assertIn(".gear-button__logo {", BOARD_WEB_APP_HTML)
        self.assertIn("width: 22px;", BOARD_WEB_APP_HTML)
        self.assertIn('id="boardScaleInput"', BOARD_WEB_APP_HTML)
        self.assertIn('class="scale-track"', BOARD_WEB_APP_HTML)
        self.assertNotIn("addEventListener('wheel'", BOARD_WEB_APP_HTML)
        self.assertNotIn("function handleBoardWheel", BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr);", BOARD_WEB_APP_HTML)
        self.assertIn("--board-gutter-left: 0px;", BOARD_WEB_APP_HTML)
        self.assertIn("--board-gutter-top: 0px;", BOARD_WEB_APP_HTML)
        self.assertIn(".topbar__meta {", BOARD_WEB_APP_HTML)
        self.assertIn(".status-shell .message {", BOARD_WEB_APP_HTML)
        self.assertIn(".status-shell .message::before {", BOARD_WEB_APP_HTML)
        self.assertIn(
            '.status-shell .message[data-connection="pending"]::before', BOARD_WEB_APP_HTML
        )
        self.assertIn(
            '.status-shell .message[data-connection="offline"]::before', BOARD_WEB_APP_HTML
        )
        self.assertIn("width: max-content;", BOARD_WEB_APP_HTML)
        self.assertIn("white-space: nowrap;", BOARD_WEB_APP_HTML)
        self.assertIn(
            'id="statusLine" data-connection="pending" data-tone="normal"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function connectionStateFromStatusText(text, isError = false)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function showConnectionPendingStatus()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.statusLine.dataset.connection = nextConnectionState;", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.statusLine.dataset.tone = isError ? 'error' : 'normal';", BOARD_WEB_APP_HTML
        )
        self.assertIn(": 'СЕРВЕР АКТИВЕН'", BOARD_WEB_APP_HTML)
        self.assertNotIn("СЕРВЕР АКТИВЕН · КАРТОЧЕК:", BOARD_WEB_APP_HTML)
        self.assertIn(".topbar__actions .btn,", BOARD_WEB_APP_HTML)
        self.assertIn(".topbar__rare-actions .btn {", BOARD_WEB_APP_HTML)

    def test_shared_files_workspace_is_wired_to_api_routes(self) -> None:
        self.assertIn('id="sharedFilesButton">ФАЙЛЫ</button>', BOARD_WEB_APP_HTML)
        self.assertIn('id="sharedFilesModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="sharedFilesDesktop"', BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/list_shared_files'", BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/upload_shared_file'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const SHARED_FILE_UPLOAD_MAX_SIZE_BYTES = 25 * 1024 * 1024;", BOARD_WEB_APP_HTML
        )
        self.assertIn("if (file.size > SHARED_FILE_UPLOAD_MAX_SIZE_BYTES)", BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/rename_shared_file'", BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/delete_shared_file'", BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/paste_shared_file'", BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/update_shared_file_position'", BOARD_WEB_APP_HTML)

    def test_shared_files_desktop_supports_context_and_clipboard_paste(self) -> None:
        self.assertIn('class="shared-files-context-menu"', BOARD_WEB_APP_HTML)
        self.assertIn('id="sharedFilesDesktop" tabindex="0"', BOARD_WEB_APP_HTML)
        self.assertIn('data-shared-files-menu-action="paste-clipboard"', BOARD_WEB_APP_HTML)
        self.assertIn("ВСТАВИТЬ ФАЙЛ ИЗ БУФЕРА", BOARD_WEB_APP_HTML)
        self.assertIn(".shared-files-desktop.is-drop-target", BOARD_WEB_APP_HTML)
        self.assertIn(".shared-file-icon {", BOARD_WEB_APP_HTML)
        self.assertIn("cursor: grab;", BOARD_WEB_APP_HTML)
        self.assertIn(".shared-file-icon.is-dragging {", BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/paste_shared_files_from_clipboard'", BOARD_WEB_APP_HTML)
        self.assertIn("function sharedFilesDropPointFromEvent(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function sharedFilesGridSlotFromPoint(x, y)", BOARD_WEB_APP_HTML)
        self.assertIn("function sharedFilesGridPointFromSlot(slot)", BOARD_WEB_APP_HTML)
        self.assertIn("function sharedFilesSnapPointToGrid(x, y)", BOARD_WEB_APP_HTML)
        self.assertIn("function sharedFilesLayout(files)", BOARD_WEB_APP_HTML)
        self.assertIn("function sharedFilesStoredSlot(file)", BOARD_WEB_APP_HTML)
        self.assertIn("function sharedFilesStableTime(file)", BOARD_WEB_APP_HTML)
        self.assertIn("const orderedFiles = Array.from(files || []).sort(", BOARD_WEB_APP_HTML)
        self.assertIn("const laidOutFiles = sharedFilesLayout(files);", BOARD_WEB_APP_HTML)
        self.assertIn("function updateSharedFilesSelection()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function pasteSharedFilesFromLocalClipboard(dropPoint)", BOARD_WEB_APP_HTML
        )
        self.assertIn("async function pasteSharedFilesFromSystemClipboard()", BOARD_WEB_APP_HTML)
        self.assertIn("function filesFromSharedFilesPasteEvent(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleSharedFilesContextMenu(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleSharedFilesPaste(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleSharedFilesDrop(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.sharedFilesDesktop.addEventListener('contextmenu', handleSharedFilesContextMenu);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.sharedFilesDesktop.addEventListener('paste', handleSharedFilesPaste);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.sharedFilesDesktop.addEventListener('drop', handleSharedFilesDrop);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "uploadSharedFiles(files, { dropPoint: state.sharedFilesContextPoint",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("sharedFilesGridPointFromSlot(baseIndex + index)", BOARD_WEB_APP_HTML)
        self.assertIn("sharedFilesSnapPointToGrid(nextX, nextY)", BOARD_WEB_APP_HTML)

    def test_numeric_ui_helpers_reject_non_finite_values_before_dom_output(self) -> None:
        self.assertIn("function finiteNumber(value, fallback = 0)", BOARD_WEB_APP_HTML)
        self.assertIn("function finiteNonNegativeNumber(value, fallback = 0)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const totalSeconds = finiteNumber(card.deadline_total_seconds ?? card.remaining_seconds, 86400);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const amount = finiteNumber(value);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const x = finiteNonNegativeNumber(file.x);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const x = finiteNonNegativeNumber(icon.dataset.dragX);",
            BOARD_WEB_APP_HTML,
        )

    def test_topbar_splits_rare_and_primary_actions(self) -> None:
        match = re.search(
            r'<div class="topbar__rare-actions"[^>]*>(?P<rare>.*?)</div>\s*</div>\s*'
            r'<div class="topbar__actions">(?P<primary>.*?)</div>',
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(match)
        rare_html = match.group("rare")
        primary_html = match.group("primary")
        for button_id in ("operatorButton", "archiveButton", "sharedFilesButton"):
            self.assertIn(f'id="{button_id}"', rare_html)
            self.assertNotIn(f'id="{button_id}"', primary_html)
        for button_id in (
            "repairOrdersButton",
            "clientsButton",
            "cashboxesButton",
            "employeesButton",
        ):
            self.assertIn(f'id="{button_id}"', primary_html)
            self.assertNotIn(f'id="{button_id}"', rare_html)
        self.assertNotIn('id="columnButton"', primary_html)
        self.assertNotIn('id="cardButton"', primary_html)
        self.assertNotIn('id="columnButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="cardButton"', BOARD_WEB_APP_HTML)
        self.assertIn('class="board-add-column"', BOARD_WEB_APP_HTML)
        self.assertIn('data-create-column="true"', BOARD_WEB_APP_HTML)
        self.assertIn('aria-label="Добавить столбец"', BOARD_WEB_APP_HTML)
        self.assertIn("target.closest('[data-create-column]')", BOARD_WEB_APP_HTML)
        self.assertIn("async function createColumnFromBoard()", BOARD_WEB_APP_HTML)
        self.assertIn(".board-add-column {", BOARD_WEB_APP_HTML)
        self.assertIn(".board-add-column:hover", BOARD_WEB_APP_HTML)
        self.assertIn(".topbar__rare-actions {", BOARD_WEB_APP_HTML)
        self.assertIn("min-height: 27px;", BOARD_WEB_APP_HTML)
        self.assertIn("padding: 5px 8px;", BOARD_WEB_APP_HTML)

    def test_mobile_product_shell_exposes_primary_workspaces(self) -> None:
        self.assertIn('id="mobileAppShell"', BOARD_WEB_APP_HTML)
        self.assertIn('class="mobile-shell"', BOARD_WEB_APP_HTML)
        for view in ("board", "cashboxes", "repair-orders", "more"):
            self.assertIn(f'data-mobile-view="{view}"', BOARD_WEB_APP_HTML)
        for panel in ("board", "cashboxes", "repair-orders", "more"):
            self.assertIn(f'data-mobile-panel="{panel}"', BOARD_WEB_APP_HTML)
        for element_id in (
            "mobileStatusLine",
            "mobileBoardColumns",
            "mobileCashboxList",
            "mobileCashboxDetail",
            "mobileCashboxIncomeButton",
            "mobileCashboxExpenseButton",
            "mobileCashboxTransferButton",
            "mobileRepairOrdersList",
        ):
            self.assertIn(f'id="{element_id}"', BOARD_WEB_APP_HTML)
        self.assertIn("function setMobileView(view)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileShell()", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileCashboxes()", BOARD_WEB_APP_HTML)
        self.assertIn("function bindMobileShellEvents()", BOARD_WEB_APP_HTML)
        self.assertIn("body.is-mobile-lite .mobile-shell", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-bottom-nav", BOARD_WEB_APP_HTML)

    def test_mobile_repair_order_detail_supports_core_fill_workflow(self) -> None:
        for element_id in (
            "mobileRepairOrderDetail",
            "mobileRepairOrderBackButton",
            "mobileRepairOrderSaveButton",
            "mobileRepairOrderNumber",
            "mobileRepairOrderStatus",
            "mobileRepairOrderWorks",
            "mobileRepairOrderMaterials",
            "mobileRepairOrderTotals",
        ):
            self.assertIn(f'id="{element_id}"', BOARD_WEB_APP_HTML)
        for field in (
            "client",
            "phone",
            "vehicle",
            "license_plate",
            "vin",
            "mileage",
            "comment",
        ):
            self.assertIn(f'data-mobile-repair-order-field="{field}"', BOARD_WEB_APP_HTML)
        self.assertIn('data-mobile-repair-order-add-row="works"', BOARD_WEB_APP_HTML)
        self.assertIn('data-mobile-repair-order-add-row="materials"', BOARD_WEB_APP_HTML)
        self.assertIn("function openMobileRepairOrderDetail(cardId)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileRepairOrderDetail()", BOARD_WEB_APP_HTML)
        self.assertIn("function readMobileRepairOrderDraft()", BOARD_WEB_APP_HTML)
        self.assertIn("function saveMobileRepairOrder()", BOARD_WEB_APP_HTML)
        self.assertIn("function handleMobileRepairOrdersListClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/update_repair_order'", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-repair-order-detail", BOARD_WEB_APP_HTML)

    def test_mobile_repair_order_detail_supports_status_and_payments(self) -> None:
        for element_id in (
            "mobileRepairOrderStatusSelect",
            "mobileRepairOrderPayments",
            "mobileRepairOrderPaymentCashbox",
            "mobileRepairOrderPaymentAmount",
            "mobileRepairOrderPaymentNote",
            "mobileRepairOrderAddPaymentButton",
        ):
            self.assertIn(f'id="{element_id}"', BOARD_WEB_APP_HTML)
        self.assertIn('data-mobile-repair-order-field="status"', BOARD_WEB_APP_HTML)
        self.assertIn('data-mobile-repair-payment-field="cashbox_id"', BOARD_WEB_APP_HTML)
        self.assertIn('data-mobile-repair-payment-field="amount"', BOARD_WEB_APP_HTML)
        self.assertIn('data-mobile-repair-payment-field="note"', BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-repair-order-payment-remove", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileRepairOrderPayments(payments)", BOARD_WEB_APP_HTML)
        self.assertIn("function readMobileRepairOrderPayments()", BOARD_WEB_APP_HTML)
        self.assertIn("function addMobileRepairOrderPayment()", BOARD_WEB_APP_HTML)
        self.assertIn("function removeMobileRepairOrderPayment(paymentId)", BOARD_WEB_APP_HTML)
        self.assertIn("payments: readMobileRepairOrderPayments()", BOARD_WEB_APP_HTML)
        self.assertIn("payment_method: repairOrderPaymentMethodFromPayments(", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-repair-order-payment-row", BOARD_WEB_APP_HTML)

    def test_repair_order_payments_popup_uses_compact_single_line_controls(self) -> None:
        self.assertNotIn(
            "Пока нет оплат. Добавьте первое поступление в выбранную кассу.",
            BOARD_WEB_APP_HTML,
        )
        for label in ("Оплат:", "Внесено:", "К доплате:"):
            self.assertIn(f"<span>{label}</span><strong>", BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-columns: auto auto auto;", BOARD_WEB_APP_HTML)
        self.assertIn("white-space: nowrap;", BOARD_WEB_APP_HTML)
        self.assertIn(
            ".repair-order-payments-form .field--compact label {",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("min-height: 18px;", BOARD_WEB_APP_HTML)
        self.assertIn(
            '.repair-order-payments-form .field--compact input[type="text"],',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("min-height: 40px;", BOARD_WEB_APP_HTML)
        self.assertIn("#repairOrderPaymentNote {", BOARD_WEB_APP_HTML)
        self.assertIn("min-height: 38px;", BOARD_WEB_APP_HTML)
        self.assertIn("#repairOrderPaymentAddButton {", BOARD_WEB_APP_HTML)
        self.assertIn(
            'aria-label="Добавить оплату">+ Добавить оплату</button>',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("width: 176px;", BOARD_WEB_APP_HTML)
        self.assertIn(
            '<select id="repairOrderPaymentCashbox"></select>',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("const options = preferredItems.map((item) => {", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "const options = ['<option value=\"\">ВЫБЕРИ КАССУ</option>']"
            ".concat(preferredItems.map",
            BOARD_WEB_APP_HTML,
        )

    def test_mobile_repair_order_detail_uses_tabbed_sections_and_sticky_actions(self) -> None:
        self.assertIn('class="mobile-repair-order-detail__sticky"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="mobile-repair-order-tabs" id="mobileRepairOrderTabs"', BOARD_WEB_APP_HTML
        )
        for tab in ("client", "works", "materials", "payments", "totals"):
            self.assertIn(f'data-mobile-repair-order-tab="{tab}"', BOARD_WEB_APP_HTML)
            self.assertIn(f'data-mobile-repair-order-page="{tab}"', BOARD_WEB_APP_HTML)
        self.assertIn("mobileRepairOrderTab: 'client'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "mobileRepairOrderTabs: document.getElementById('mobileRepairOrderTabs')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function normalizeMobileRepairOrderTab(tab)", BOARD_WEB_APP_HTML)
        self.assertIn("function setMobileRepairOrderTab(tab)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileRepairOrderTabs()", BOARD_WEB_APP_HTML)
        self.assertIn("function handleMobileRepairOrderTabsClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.mobileRepairOrderTabs?.addEventListener('click', handleMobileRepairOrderTabsClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".mobile-repair-order-page.is-active", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-repair-order-detail__sticky", BOARD_WEB_APP_HTML)
        repair_order_tab_rule = re.search(
            r"\.mobile-repair-order-tab\s*\{(?P<body>.*?)\n\s*\}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(repair_order_tab_rule)
        repair_order_tab_body = repair_order_tab_rule.group("body")
        self.assertIn("letter-spacing: 0;", repair_order_tab_body)
        self.assertIn("white-space: nowrap;", repair_order_tab_body)
        specific_repair_order_tab_rule = re.search(
            r"\.mobile-shell \.mobile-repair-order-tab\s*\{(?P<body>.*?)\n\s*\}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(specific_repair_order_tab_rule)
        self.assertIn("font-size: 10.5px;", specific_repair_order_tab_rule.group("body"))

    def test_mobile_shell_supports_swipe_navigation_and_column_snap(self) -> None:
        self.assertIn(
            "const MOBILE_VIEW_ORDER = ['board', 'cashboxes', 'inventory', 'repair-orders', 'more'];",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("mobileSwipe:", BOARD_WEB_APP_HTML)
        self.assertIn("function mobileViewIndex(view)", BOARD_WEB_APP_HTML)
        self.assertIn("function shiftMobileView(direction)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleMobileShellTouchStart(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleMobileShellTouchEnd(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "mobileShellMain: document.getElementById('mobileShellMain')", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.mobileShellMain?.addEventListener('touchstart', handleMobileShellTouchStart",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.mobileShellMain?.addEventListener('touchend', handleMobileShellTouchEnd",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("scroll-snap-type: x mandatory;", BOARD_WEB_APP_HTML)
        self.assertIn("grid-auto-flow: column;", BOARD_WEB_APP_HTML)
        self.assertIn("grid-auto-columns:", BOARD_WEB_APP_HTML)
        self.assertIn("scroll-snap-align: start;", BOARD_WEB_APP_HTML)

    def test_mobile_detail_screens_allow_long_form_scrolling(self) -> None:
        for selector in (".mobile-card-detail", ".mobile-repair-order-detail"):
            match = re.search(
                rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\n\s*\}}", BOARD_WEB_APP_HTML, re.S
            )
            self.assertIsNotNone(match, selector)
            rule_body = match.group("body")
            self.assertIn("overflow: visible;", rule_body)
            self.assertNotIn("overflow: hidden;", rule_body)

    def test_mobile_board_supports_card_detail_editing(self) -> None:
        for element_id in (
            "mobileCardCreateButton",
            "mobileCardDetail",
            "mobileCardTabs",
            "mobileCardBackButton",
            "mobileCardSaveButton",
            "mobileCardTitleLine",
            "mobileCardRepairOrderButton",
        ):
            self.assertIn(f'id="{element_id}"', BOARD_WEB_APP_HTML)
        for field in ("vehicle", "title", "description"):
            self.assertIn(f'data-mobile-card-field="{field}"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="mobileCardColumnSelect"', BOARD_WEB_APP_HTML)
        self.assertNotIn('data-mobile-card-field="column"', BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-card-id", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileCardDetail()", BOARD_WEB_APP_HTML)
        self.assertIn("function openMobileCardDetail(cardId)", BOARD_WEB_APP_HTML)
        self.assertIn("function readMobileCardDraft()", BOARD_WEB_APP_HTML)
        self.assertIn("function mobileCardDeadlineInput(card)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "finiteNumber(card.deadline_total_seconds ?? card.remaining_seconds, 86400)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function saveMobileCardDetail()", BOARD_WEB_APP_HTML)
        self.assertIn("function handleMobileBoardClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/update_card'", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-card-detail", BOARD_WEB_APP_HTML)

    def test_mobile_card_overview_omits_column_timer_and_tag_controls(self) -> None:
        self.assertNotIn('id="mobileCardColumnSelect"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="mobileCardDeadlineDays"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="mobileCardDeadlineHours"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="mobileCardDeadlinePreview"', BOARD_WEB_APP_HTML)
        self.assertNotIn("data-mobile-card-deadline-field=", BOARD_WEB_APP_HTML)
        self.assertNotIn('class="mobile-card-section mobile-card-deadline"', BOARD_WEB_APP_HTML)
        for element_id in (
            "mobileCardTags",
            "mobileCardTagsLimit",
            "mobileCardTagInput",
            "mobileCardTagColor",
            "mobileCardTagAddButton",
        ):
            self.assertNotIn(f'id="{element_id}"', BOARD_WEB_APP_HTML)
        self.assertNotIn("data-mobile-card-remove-tag", BOARD_WEB_APP_HTML)
        self.assertNotIn('class="mobile-card-section mobile-card-tags"', BOARD_WEB_APP_HTML)
        self.assertNotIn(".mobile-card-tags", BOARD_WEB_APP_HTML)
        self.assertNotIn(".mobile-card-tag", BOARD_WEB_APP_HTML)
        self.assertIn(
            '<textarea id="mobileCardDescriptionInput" maxlength="12000" rows="10"',
            BOARD_WEB_APP_HTML,
        )

    def test_mobile_card_description_uses_available_screen_height(self) -> None:
        self.assertIn(
            'class="mobile-field mobile-card-description-field"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-workspace.is-card-detail-open {\n"
            "      align-content: stretch;\n"
            "      overflow: hidden;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            '.mobile-card-page[data-mobile-card-page="overview"].is-active {\n'
            "      grid-template-rows: auto auto minmax(260px, 1fr) auto;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-card-description-field {\n      min-height: 0;\n      height: 100%;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-card-description-field textarea {\n"
            "      height: 100%;\n"
            "      min-height: 260px;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".is-mobile-lite .mobile-card-description-field textarea {\n"
            "      height: 100%;\n"
            "      min-height: 260px;\n"
            "      resize: none;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "@media (max-height: 760px) {\n"
            '      .mobile-card-page[data-mobile-card-page="overview"].is-active {\n'
            "        grid-template-rows: auto auto minmax(220px, 1fr) auto;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "@media (max-height: 760px) {\n"
            "      .is-mobile-lite .mobile-card-description-field textarea {\n"
            "        min-height: 220px;",
            BOARD_WEB_APP_HTML,
        )

    def test_mobile_board_supports_card_creation(self) -> None:
        self.assertIn('id="mobileCardCreateButton"', BOARD_WEB_APP_HTML)
        self.assertIn("mobileCardCreating: false", BOARD_WEB_APP_HTML)
        self.assertIn("function emptyMobileCardDraft()", BOARD_WEB_APP_HTML)
        self.assertIn("function openMobileNewCard()", BOARD_WEB_APP_HTML)
        self.assertIn("state.mobileCardCreating = true;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const detailOpen = state.mobileCardCreating || Boolean(state.mobileCardId);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("const isNewCard = state.mobileCardCreating;", BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/create_card'", BOARD_WEB_APP_HTML)
        self.assertIn("state.mobileCardCreating = false;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.mobileCardCreateButton?.addEventListener('click', openMobileNewCard);",
            BOARD_WEB_APP_HTML,
        )

    def test_mobile_board_can_expand_columns_beyond_preview_limit(self) -> None:
        self.assertIn("mobileExpandedColumns: new Set()", BOARD_WEB_APP_HTML)
        self.assertIn("function mobileColumnIsExpanded(columnId)", BOARD_WEB_APP_HTML)
        self.assertIn("function toggleMobileColumnExpanded(columnId)", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-column-toggle", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const hiddenCount = Math.max(0, columnCards.length - previewCards.length);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("'ЕЩЁ ' + hiddenCount", BOARD_WEB_APP_HTML)
        self.assertIn("'СВЕРНУТЬ'", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-column-card__more", BOARD_WEB_APP_HTML)

    def test_mobile_more_workspace_shows_live_module_status_cards(self) -> None:
        self.assertIn('id="mobileMoreRefreshButton"', BOARD_WEB_APP_HTML)
        self.assertIn("mobileMoreLoaded: false", BOARD_WEB_APP_HTML)
        self.assertIn("mobileMoreLoading: false", BOARD_WEB_APP_HTML)
        self.assertIn(
            "mobileMoreRefreshButton: document.getElementById('mobileMoreRefreshButton')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function renderMobileMoreModules()", BOARD_WEB_APP_HTML)
        self.assertIn("function loadMobileMoreModules(", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-more-module", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-more-value", BOARD_WEB_APP_HTML)
        self.assertIn("loadClients({ openModal: false })", BOARD_WEB_APP_HTML)
        self.assertIn("loadEmployeesReference()", BOARD_WEB_APP_HTML)
        self.assertIn("loadArchive(false, { force })", BOARD_WEB_APP_HTML)
        self.assertIn("loadSharedFiles({ openModal: false })", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-module-card__value", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-module-card__detail", BOARD_WEB_APP_HTML)

    def test_mobile_more_clients_module_has_native_drilldown(self) -> None:
        self.assertIn("mobileMorePanel: ''", BOARD_WEB_APP_HTML)
        for element_id in (
            "mobileClientsPanel",
            "mobileClientsBackButton",
            "mobileClientsSearchInput",
            "mobileClientsMeta",
            "mobileClientsList",
            "mobileClientDetail",
        ):
            self.assertIn(element_id, BOARD_WEB_APP_HTML)
        self.assertIn("function openMobileClientsPanel()", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileClientsPanel()", BOARD_WEB_APP_HTML)
        self.assertIn("function loadMobileClients(", BOARD_WEB_APP_HTML)
        self.assertIn("function loadMobileClientProfile(clientId)", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-client-id", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-client-order-card", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (target === 'clients') return openMobileClientsPanel();", BOARD_WEB_APP_HTML
        )
        self.assertIn("els.mobileClientsSearchInput?.addEventListener('input'", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-client-row", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-client-detail", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-more-grid[hidden]", BOARD_WEB_APP_HTML)

    def test_mobile_more_employees_module_has_native_drilldown(self) -> None:
        self.assertIn("mobileEmployeesLoading: false", BOARD_WEB_APP_HTML)
        for element_id in (
            "mobileEmployeesPanel",
            "mobileEmployeesBackButton",
            "mobileEmployeesMeta",
            "mobileEmployeesList",
            "mobileEmployeeDetail",
        ):
            self.assertIn(element_id, BOARD_WEB_APP_HTML)
        self.assertIn("function openMobileEmployeesPanel()", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileEmployeesPanel()", BOARD_WEB_APP_HTML)
        self.assertIn("function loadMobileEmployees(", BOARD_WEB_APP_HTML)
        self.assertIn("function mobileEmployeeSummaryMap()", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-employee-id", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (target === 'employees') return openMobileEmployeesPanel();", BOARD_WEB_APP_HTML
        )
        self.assertIn(".mobile-employee-row", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-employee-detail", BOARD_WEB_APP_HTML)

    def test_mobile_more_archive_module_has_native_drilldown(self) -> None:
        self.assertIn("mobileArchiveLoading: false", BOARD_WEB_APP_HTML)
        for element_id in (
            "mobileArchivePanel",
            "mobileArchiveBackButton",
            "mobileArchiveSearchInput",
            "mobileArchiveMeta",
            "mobileArchiveList",
        ):
            self.assertIn(element_id, BOARD_WEB_APP_HTML)
        self.assertIn("function openMobileArchivePanel()", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileArchivePanel()", BOARD_WEB_APP_HTML)
        self.assertIn("function loadMobileArchive(", BOARD_WEB_APP_HTML)
        self.assertIn("function restoreMobileArchiveCard(cardId)", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-archive-card", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-archive-restore", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (target === 'archive') return openMobileArchivePanel();", BOARD_WEB_APP_HTML
        )
        self.assertNotIn("if (target === 'archive') return openArchiveModal();", BOARD_WEB_APP_HTML)
        self.assertIn("els.mobileArchiveSearchInput?.addEventListener('input'", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-archive-row", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-archive-panel", BOARD_WEB_APP_HTML)

    def test_mobile_more_files_module_has_native_drilldown(self) -> None:
        self.assertIn("mobileSharedFilesLoading: false", BOARD_WEB_APP_HTML)
        self.assertIn("mobileSharedFileRenamingId: ''", BOARD_WEB_APP_HTML)
        for element_id in (
            "mobileSharedFilesPanel",
            "mobileSharedFilesBackButton",
            "mobileSharedFilesUploadButton",
            "mobileSharedFilesInput",
            "mobileSharedFilesMeta",
            "mobileSharedFilesList",
        ):
            self.assertIn(element_id, BOARD_WEB_APP_HTML)
        self.assertIn("function openMobileSharedFilesPanel()", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileSharedFilesPanel()", BOARD_WEB_APP_HTML)
        self.assertIn("function loadMobileSharedFiles(", BOARD_WEB_APP_HTML)
        self.assertIn("function renameMobileSharedFile(fileId)", BOARD_WEB_APP_HTML)
        self.assertIn("function deleteMobileSharedFile(fileId)", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-shared-file-id", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-shared-file-action", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (target === 'files') return openMobileSharedFilesPanel();", BOARD_WEB_APP_HTML
        )
        self.assertNotIn(
            "if (target === 'files') return openSharedFilesModal();", BOARD_WEB_APP_HTML
        )
        self.assertIn("els.mobileSharedFilesInput?.addEventListener('change'", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-shared-file-row", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-shared-files-panel", BOARD_WEB_APP_HTML)

    def test_mobile_more_files_actions_are_touch_sized_and_readable(self) -> None:
        for label in ("ОТКРЫТЬ", "СКАЧАТЬ", "УДАЛИТЬ"):
            self.assertIn(f">{label}</button>", BOARD_WEB_APP_HTML)
        self.assertIn("'ПЕРЕИМ.'", BOARD_WEB_APP_HTML)
        self.assertIn(
            ".mobile-shared-file-row__actions {\n      display: grid;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "grid-template-columns: repeat(2, minmax(0, 1fr));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-shared-file-row__actions .mobile-action {\n      width: 100%;\n      min-height: 42px;",
            BOARD_WEB_APP_HTML,
        )

    def test_mobile_navigation_uses_clear_repair_order_label(self) -> None:
        self.assertIn(
            'data-mobile-view="repair-orders">НАРЯДЫ</button>',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('<div class="mobile-kicker">НАРЯДЫ</div>', BOARD_WEB_APP_HTML)
        self.assertNotIn('data-mobile-view="repair-orders">ЗН</button>', BOARD_WEB_APP_HTML)
        self.assertNotIn('<div class="mobile-kicker">ЗН</div>', BOARD_WEB_APP_HTML)

    def test_mobile_touch_targets_survive_desktop_form_overrides(self) -> None:
        self.assertIn(
            ".is-mobile-lite .mobile-field input,\n    .is-mobile-lite .mobile-field select {\n      min-height: 44px;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".is-mobile-lite .mobile-field textarea {\n      min-height: 128px;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-repair-order-remove,\n    .mobile-repair-order-payment-remove {\n      width: 42px;\n      height: 42px;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-archive-row__actions .mobile-action {\n      min-height: 42px;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-shared-files-actions .mobile-action {\n      min-width: 112px;\n      min-height: 42px;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-card-tab {\n      min-width: 0;\n      min-height: 42px;\n      border: 1px solid rgba(167, 178, 132, 0.2);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("font-size: 9.5px;", BOARD_WEB_APP_HTML)

    def test_mobile_card_detail_uses_tabbed_sections_and_sticky_actions(self) -> None:
        self.assertIn('class="mobile-card-detail__sticky"', BOARD_WEB_APP_HTML)
        self.assertIn('class="mobile-card-tabs" id="mobileCardTabs"', BOARD_WEB_APP_HTML)
        for tab in ("overview", "vehicle", "files", "journal"):
            self.assertIn(f'data-mobile-card-tab="{tab}"', BOARD_WEB_APP_HTML)
            self.assertIn(f'data-mobile-card-page="{tab}"', BOARD_WEB_APP_HTML)
        self.assertIn("mobileCardTab: 'overview'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "mobileCardTabs: document.getElementById('mobileCardTabs')", BOARD_WEB_APP_HTML
        )
        self.assertIn("function normalizeMobileCardTab(tab)", BOARD_WEB_APP_HTML)
        self.assertIn("function setMobileCardTab(tab)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileCardTabs()", BOARD_WEB_APP_HTML)
        self.assertIn("function handleMobileCardTabsClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.mobileCardTabs?.addEventListener('click', handleMobileCardTabsClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".mobile-card-page.is-active", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-card-detail__sticky", BOARD_WEB_APP_HTML)

    def test_mobile_card_detail_hides_board_header_for_clean_context(self) -> None:
        self.assertIn(
            "boardPanel?.classList.toggle('is-card-detail-open', detailOpen);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-workspace.is-card-detail-open > .mobile-workspace__head {",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-workspace__actions .mobile-action {\n      min-height: 42px;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-card-tab {\n      min-width: 0;\n      min-height: 42px;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-repair-order-tab {\n      min-width: 0;\n      min-height: 42px;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".mobile-field input,\n    .mobile-field textarea,\n    .mobile-field select {\n      width: 100%;\n      min-height: 42px;",
            BOARD_WEB_APP_HTML,
        )

    def test_mobile_card_detail_preserves_tags_without_resetting_timer(self) -> None:
        self.assertIn("function renderMobileCardDeadline(card)", BOARD_WEB_APP_HTML)
        self.assertIn("function mobileCardDeadlineFromUi(card)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (!els.mobileCardDeadlineDays || !els.mobileCardDeadlineHours)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function syncMobileCardDeadlinePreview()", BOARD_WEB_APP_HTML)
        self.assertIn("function readMobileCardTags()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "return normalizeDraftTags(currentMobileCard()?.tag_items || currentMobileCard()?.tags || []);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("function renderMobileCardTags(card)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function addMobileCardTag()", BOARD_WEB_APP_HTML)
        self.assertNotIn("function removeMobileCardTag(label)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function handleMobileCardTagInputKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertNotIn("els.mobileCardTagAddButton?.addEventListener", BOARD_WEB_APP_HTML)
        self.assertNotIn("els.mobileCardTagInput?.addEventListener", BOARD_WEB_APP_HTML)
        self.assertNotIn("deadline: mobileCardDeadlineFromUi(card),", BOARD_WEB_APP_HTML)
        self.assertIn("tags: readMobileCardTags(),", BOARD_WEB_APP_HTML)

    def test_mobile_card_detail_supports_vehicle_profile_and_client_fields(self) -> None:
        for element_id in (
            "mobileCardVehicleProfile",
            "mobileCardVehicleDisplayName",
            "mobileCardVehiclePlate",
            "mobileCardVehicleYear",
            "mobileCardVehicleMileage",
            "mobileCardVehicleCustomerPhone",
            "mobileCardVehicleCustomerName",
            "mobileCardVehicleVin",
            "mobileCardVehicleEngine",
            "mobileCardVehicleGearbox",
            "mobileCardVehicleDrivetrain",
        ):
            self.assertIn(f'id="{element_id}"', BOARD_WEB_APP_HTML)
        for field in (
            "display_name",
            "registration_plate",
            "production_year",
            "mileage",
            "customer_phone",
            "customer_name",
            "vin",
            "engine_model",
            "gearbox_model",
            "drivetrain",
        ):
            self.assertIn(f'data-mobile-vehicle-field="{field}"', BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileCardVehicleProfile(card)", BOARD_WEB_APP_HTML)
        self.assertIn("function readMobileCardVehicleProfile(card)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function mobileVehicleNumberFieldValue(fieldName, value)", BOARD_WEB_APP_HTML
        )
        self.assertIn("vehicle_profile: readMobileCardVehicleProfile(card),", BOARD_WEB_APP_HTML)
        self.assertIn(
            "profile.customer_phones = normalizePhoneList([profile.customer_phone]);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("profile.make_display = displayParts.make_display;", BOARD_WEB_APP_HTML)
        self.assertIn("profile.model_display = displayParts.model_display;", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-card-vehicle", BOARD_WEB_APP_HTML)

    def test_mobile_card_detail_supports_files_and_attachments(self) -> None:
        for element_id in (
            "mobileCardFiles",
            "mobileCardFileInput",
            "mobileCardFileAddButton",
            "mobileCardFileMeta",
        ):
            self.assertIn(f'id="{element_id}"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "mobileCardFiles: document.getElementById('mobileCardFiles')", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "mobileCardFileInput: document.getElementById('mobileCardFileInput')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "mobileCardFileAddButton: document.getElementById('mobileCardFileAddButton')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "mobileCardFileMeta: document.getElementById('mobileCardFileMeta')", BOARD_WEB_APP_HTML
        )
        self.assertIn("data-mobile-card-file-id", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-card-remove-file", BOARD_WEB_APP_HTML)
        self.assertIn("function mobileCardAttachmentRows(card)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function mobileCardAttachmentDownloadPath(cardId, attachmentId)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function renderMobileCardFiles(card)", BOARD_WEB_APP_HTML)
        self.assertIn("async function uploadMobileCardFiles()", BOARD_WEB_APP_HTML)
        self.assertIn("async function removeMobileCardFile(attachmentId)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const removeDisabledAttr = state.mobileCardFilesBusy ? ' disabled' : '';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("'/api/add_card_attachment'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/remove_card_attachment'", BOARD_WEB_APP_HTML)
        self.assertIn("renderMobileCardFiles(card);", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-card-files", BOARD_WEB_APP_HTML)

    def test_mobile_card_detail_supports_journal_events(self) -> None:
        for element_id in (
            "mobileCardJournal",
            "mobileCardJournalMeta",
            "mobileCardJournalRefreshButton",
        ):
            self.assertIn(f'id="{element_id}"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "mobileCardJournal: document.getElementById('mobileCardJournal')", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "mobileCardJournalMeta: document.getElementById('mobileCardJournalMeta')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "mobileCardJournalRefreshButton: document.getElementById('mobileCardJournalRefreshButton')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("mobileCardJournalPayload:", BOARD_WEB_APP_HTML)
        self.assertIn("mobileCardJournalLoadedFor:", BOARD_WEB_APP_HTML)
        self.assertIn("mobileCardJournalLimit:", BOARD_WEB_APP_HTML)
        self.assertIn("mobileCardJournalLoading:", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-card-journal-row", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-card-journal-more", BOARD_WEB_APP_HTML)
        self.assertIn("function mobileCardJournalRequestUrl(cardId", BOARD_WEB_APP_HTML)
        self.assertIn("function mobileCardJournalRows(payload)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileCardJournal()", BOARD_WEB_APP_HTML)
        self.assertIn("async function loadMobileCardJournal(", BOARD_WEB_APP_HTML)
        self.assertIn("function resetMobileCardJournal()", BOARD_WEB_APP_HTML)
        self.assertIn("cardJournalEntriesFromPayload(data)", BOARD_WEB_APP_HTML)
        self.assertIn("renderCardJournalDetails(entry)", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/get_card_log?card_id='", BOARD_WEB_APP_HTML)
        self.assertIn("renderMobileCardJournal();", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-card-journal", BOARD_WEB_APP_HTML)

    def test_topbar_card_search_workspace_is_wired_to_search_cards(self) -> None:
        self.assertIn('class="topbar-search"', BOARD_WEB_APP_HTML)
        self.assertIn('id="boardSearchInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="boardSearchResults"', BOARD_WEB_APP_HTML)
        self.assertIn("flex: 0 0 220px;", BOARD_WEB_APP_HTML)
        self.assertIn("width: 220px;", BOARD_WEB_APP_HTML)
        self.assertIn("height: 27px;", BOARD_WEB_APP_HTML)
        self.assertIn('aria-label="Поиск по доске"', BOARD_WEB_APP_HTML)
        self.assertIn('placeholder="поиск"', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="VIN, госномер, клиент"', BOARD_WEB_APP_HTML)
        self.assertIn(".topbar-search__input::placeholder {", BOARD_WEB_APP_HTML)
        self.assertIn("color: rgba(242, 240, 230, 0.28);", BOARD_WEB_APP_HTML)
        self.assertNotIn("topbar-search__label", BOARD_WEB_APP_HTML)
        self.assertNotIn("поиск по доске", BOARD_WEB_APP_HTML)
        self.assertNotIn("НАЙТИ КАРТОЧКУ", BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="НАЙТИ КАРТОЧКУ"', BOARD_WEB_APP_HTML)
        generic_search_input_index = BOARD_WEB_APP_HTML.index(
            'input[type="text"], input[type="password"], input[type="email"], input[type="search"]'
        )
        self.assertIn(".topbar-search .topbar-search__input {", BOARD_WEB_APP_HTML)
        compact_search_input_index = BOARD_WEB_APP_HTML.index(
            ".topbar-search .topbar-search__input {"
        )
        self.assertGreater(compact_search_input_index, generic_search_input_index)
        self.assertIn(".topbar-search__input::-webkit-search-cancel-button", BOARD_WEB_APP_HTML)
        self.assertIn("min-height: 0;", BOARD_WEB_APP_HTML)
        self.assertIn(".topbar-search__clear[hidden]", BOARD_WEB_APP_HTML)
        self.assertIn(".topbar-search__results.is-open", BOARD_WEB_APP_HTML)
        self.assertIn("width: min(760px, calc(100vw - 32px));", BOARD_WEB_APP_HTML)
        self.assertIn("max-height: min(680px, calc(100vh - 76px));", BOARD_WEB_APP_HTML)
        self.assertIn("-webkit-line-clamp: 3;", BOARD_WEB_APP_HTML)
        search_meta_rule = re.search(
            r"\.topbar-search__meta \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(search_meta_rule)
        assert search_meta_rule is not None
        self.assertIn("font-size: 10.5px;", search_meta_rule.group("body"))
        search_title_rule = re.search(
            r"\.topbar-search__title \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(search_title_rule)
        assert search_title_rule is not None
        self.assertIn("font-size: 15px;", search_title_rule.group("body"))
        search_summary_rule = re.search(
            r"\.topbar-search__summary \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(search_summary_rule)
        assert search_summary_rule is not None
        self.assertIn("font-size: 13px;", search_summary_rule.group("body"))
        self.assertIn("line-height: 1.36;", search_summary_rule.group("body"))
        search_badge_rule = re.search(
            r"\.topbar-search__column,\n    \.topbar-search__match \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(search_badge_rule)
        assert search_badge_rule is not None
        self.assertIn("font-size: 9.5px;", search_badge_rule.group("body"))
        search_empty_rule = re.search(
            r"\.topbar-search__empty,\n    \.topbar-search__error \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(search_empty_rule)
        assert search_empty_rule is not None
        self.assertIn("font-size: 13.5px;", search_empty_rule.group("body"))
        self.assertIn("body.is-mobile-lite .topbar-search {", BOARD_WEB_APP_HTML)
        self.assertIn("flex: 1 0 100%;", BOARD_WEB_APP_HTML)
        self.assertIn("body.is-mobile-lite .topbar-search__clear {", BOARD_WEB_APP_HTML)
        self.assertIn("width: 34px;", BOARD_WEB_APP_HTML)
        self.assertIn("height: 34px;", BOARD_WEB_APP_HTML)
        self.assertIn("body.is-mobile-lite .topbar__actions {", BOARD_WEB_APP_HTML)
        self.assertIn("order: 1;", BOARD_WEB_APP_HTML)
        self.assertIn("max-height: calc(100dvh - 180px);", BOARD_WEB_APP_HTML)
        self.assertIn("boardSearch: {", BOARD_WEB_APP_HTML)
        self.assertIn("function scheduleBoardSearch()", BOARD_WEB_APP_HTML)
        self.assertIn("function renderBoardSearchResults()", BOARD_WEB_APP_HTML)
        self.assertIn("function openBoardSearchOnFocus()", BOARD_WEB_APP_HTML)
        self.assertIn("scheduleBoardSearch();", BOARD_WEB_APP_HTML)
        self.assertIn("function handleBoardSearchKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.boardSearchInput.addEventListener('click', openBoardSearchOnFocus)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("const BOARD_SEARCH_LIMIT = 16;", BOARD_WEB_APP_HTML)
        self.assertIn("const BOARD_SEARCH_DEBOUNCE_MS = 90;", BOARD_WEB_APP_HTML)
        self.assertIn("const BOARD_SEARCH_CACHE_TTL_MS = 20000;", BOARD_WEB_APP_HTML)
        self.assertIn("completedQuery: '',", BOARD_WEB_APP_HTML)
        self.assertIn("completedAt: 0,", BOARD_WEB_APP_HTML)
        self.assertIn("controller: null,", BOARD_WEB_APP_HTML)
        self.assertIn("function boardSearchCacheKey(query)", BOARD_WEB_APP_HTML)
        self.assertIn("function abortBoardSearchRequest()", BOARD_WEB_APP_HTML)
        self.assertIn("function reuseBoardSearchCache(query)", BOARD_WEB_APP_HTML)
        self.assertIn("if (reuseBoardSearchCache(query)) return;", BOARD_WEB_APP_HTML)
        self.assertIn("if (options.signal) request.signal = options.signal;", BOARD_WEB_APP_HTML)
        self.assertIn("const controller = new AbortController();", BOARD_WEB_APP_HTML)
        self.assertIn(
            "api('/api/search_cards?query=' + encodeURIComponent(query) + '&limit=' + BOARD_SEARCH_LIMIT, { signal: controller.signal })",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("if (error.name === 'AbortError') return;", BOARD_WEB_APP_HTML)
        self.assertIn("rememberBoardSearchCache(query);", BOARD_WEB_APP_HTML)
        self.assertIn("state.boardSearch.results = [];", BOARD_WEB_APP_HTML)
        self.assertIn(
            "window.setTimeout(() => loadBoardSearch(query, requestSeq), BOARD_SEARCH_DEBOUNCE_MS)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("await openCardById(cardId);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.boardSearchInput.addEventListener('input', scheduleBoardSearch);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("String(card?.indicator || '').trim()", BOARD_WEB_APP_HTML)

    def test_card_enrichment_button_uses_open_card_context(self) -> None:
        self.assertIn(
            "const card = state.activeCard && typeof state.activeCard === 'object'",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const cardId = String(card?.id || state.editingId || '').trim();", BOARD_WEB_APP_HTML
        )
        self.assertIn("setStatus('ОТКРОЙ КАРТОЧКУ ДЛЯ AI-ОБОГАЩЕНИЯ.', true);", BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/run_full_card_enrichment'", BOARD_WEB_APP_HTML)
        self.assertNotIn("openAgentModal('card');", BOARD_WEB_APP_HTML)

    def test_card_tag_editor_uses_compact_tag_controls(self) -> None:
        self.assertIn(".tags-panel {", BOARD_WEB_APP_HTML)
        self.assertIn(".tags-panel__head {", BOARD_WEB_APP_HTML)
        self.assertIn(".tag-limit {", BOARD_WEB_APP_HTML)
        self.assertIn(".tag-controls {", BOARD_WEB_APP_HTML)
        self.assertIn(".tag-list .tag {", BOARD_WEB_APP_HTML)
        self.assertIn("--card-meta-panel-height: 132px;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "grid-template-columns: minmax(144px, 150px) minmax(0, 450px);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".overview-main__meta > .subpanel {", BOARD_WEB_APP_HTML)
        self.assertIn("height: var(--card-meta-panel-height);", BOARD_WEB_APP_HTML)
        self.assertIn("min-height: var(--card-meta-panel-height);", BOARD_WEB_APP_HTML)
        self.assertIn(".overview-main__meta .tag-list,", BOARD_WEB_APP_HTML)
        self.assertIn("max-height: 18px;", BOARD_WEB_APP_HTML)
        self.assertIn("text-overflow: ellipsis;", BOARD_WEB_APP_HTML)
        self.assertIn(".overview-main__meta .signal-stepper__button {", BOARD_WEB_APP_HTML)
        self.assertIn("min-height: 24px;", BOARD_WEB_APP_HTML)
        self.assertIn('class="tag-suggestions" id="tagSuggestions"', BOARD_WEB_APP_HTML)
        self.assertIn('class="tag-entry"', BOARD_WEB_APP_HTML)
        self.assertIn('class="tag-controls"', BOARD_WEB_APP_HTML)
        self.assertIn('class="field field--tags"', BOARD_WEB_APP_HTML)
        self.assertIn('id="tagMeta"', BOARD_WEB_APP_HTML)
        self.assertIn("МЕТОК НЕТ", BOARD_WEB_APP_HTML)
        self.assertIn(".column > * {", BOARD_WEB_APP_HTML)

    def test_card_timer_is_inactive_by_default_and_has_explicit_controls(self) -> None:
        for element_id in (
            "signalState",
            "signalRemaining",
            "signalActions",
            "signalStartButton",
            "signalStopButton",
            "signalDaysValue",
            "signalHoursValue",
        ):
            self.assertIn(f'id="{element_id}"', BOARD_WEB_APP_HTML)
        self.assertIn("cardTimerState: 'inactive'", BOARD_WEB_APP_HTML)
        self.assertIn("function startCardTimerFromPanel()", BOARD_WEB_APP_HTML)
        self.assertIn("function stopCardTimerFromPanel()", BOARD_WEB_APP_HTML)
        self.assertIn("function applyCardTimerOperationResult(card)", BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/start_card_timer'", BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/stop_card_timer'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "timer_state: state.editingId ? '' : state.cardTimerState", BOARD_WEB_APP_HTML
        )
        self.assertIn('data-indicator="inactive"', BOARD_WEB_APP_HTML)

    def test_vehicle_profile_fields_do_not_show_placeholder_hints(self) -> None:
        self.assertNotIn("Subaru Legacy", BOARD_WEB_APP_HTML)
        self.assertNotIn("3.0 TFSI / K12B", BOARD_WEB_APP_HTML)
        self.assertNotIn("ZF 8HP55 / Aisin", BOARD_WEB_APP_HTML)
        self.assertNotIn("передний / задний / полный", BOARD_WEB_APP_HTML)
        self.assertNotIn("Иван Иванов", BOARD_WEB_APP_HTML)
        self.assertNotIn("WAU...", BOARD_WEB_APP_HTML)

    def test_repair_order_fields_do_not_show_placeholder_hints(self) -> None:
        self.assertNotIn('placeholder="1"', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="04.04.26 14:30"', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="05.04.26 10:30"', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="05.04.26 18:20"', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="Имя и фамилия"', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="+7 900 123-45-67"', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="Volkswagen Tiguan"', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="А123АА124"', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="WAUZZZ..."', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="215 000"', BOARD_WEB_APP_HTML)
        self.assertNotIn(
            'placeholder="Кратко зафиксируйте суть обращения клиента."', BOARD_WEB_APP_HTML
        )
        self.assertNotIn(
            'placeholder="Краткая история ремонта для клиента: что проверили, что нашли, что сделали и что рекомендовано дальше."',
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            'placeholder="Внутренний комментарий мастера или примечание по заказ-наряду."',
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn('placeholder="МЕТКА"', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="Артикул / OEM"', BOARD_WEB_APP_HTML)
        self.assertNotIn('placeholder="Наименование"', BOARD_WEB_APP_HTML)

    def test_modal_uses_themed_scrollbars(self) -> None:
        self.assertIn("--scroll-track:", BOARD_WEB_APP_HTML)
        self.assertIn(
            "scrollbar-color: var(--scroll-thumb) var(--scroll-track);", BOARD_WEB_APP_HTML
        )
        self.assertIn("*::-webkit-scrollbar-thumb {", BOARD_WEB_APP_HTML)

    def test_columns_expose_hidden_delete_button_with_guarded_flow(self) -> None:
        self.assertIn(".column__delete {", BOARD_WEB_APP_HTML)
        self.assertIn(".column:hover .column__delete,", BOARD_WEB_APP_HTML)
        self.assertIn("data-delete-column", BOARD_WEB_APP_HTML)
        self.assertIn('&times;</button><div class="column__count">', BOARD_WEB_APP_HTML)
        self.assertNotIn('>?</button><div class="column__count">', BOARD_WEB_APP_HTML)
        self.assertIn("async function deleteColumnFromButton(button)", BOARD_WEB_APP_HTML)
        self.assertIn("Удалить пустой столбец", BOARD_WEB_APP_HTML)
        self.assertIn("window.confirm('Удалить пустой столбец", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/delete_column'", BOARD_WEB_APP_HTML)
        self.assertIn("await deleteColumnFromButton(deleteColumnButton);", BOARD_WEB_APP_HTML)

    def test_column_create_card_button_uses_compact_board_sizing(self) -> None:
        create_button_rule = re.search(
            r"^    \.column > \.btn\[data-create-in\]\s*\{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S | re.M,
        )
        self.assertIsNotNone(create_button_rule)
        assert create_button_rule is not None
        body = create_button_rule.group("body")
        self.assertIn("height: calc(25px * var(--board-scale));", body)
        self.assertIn("padding: 0 calc(12px * var(--board-scale));", body)
        self.assertIn("font-size: calc(10px * var(--board-scale));", body)
        self.assertIn("font-weight: 300;", body)
        self.assertNotIn("font-size: 12px;", body)
        self.assertNotIn("padding: 9px 12px;", body)

    def test_columns_expose_rename_flow(self) -> None:
        self.assertIn("data-rename-column", BOARD_WEB_APP_HTML)
        self.assertIn("async function renameColumnFromButton(button)", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/rename_column'", BOARD_WEB_APP_HTML)
        self.assertIn("window.prompt(", BOARD_WEB_APP_HTML)
        self.assertIn("await renameColumnFromButton(renameColumnButton);", BOARD_WEB_APP_HTML)

    def test_columns_support_drag_and_drop_reordering(self) -> None:
        self.assertIn('data-drag-column-handle="1"', BOARD_WEB_APP_HTML)
        self.assertIn('data-column-id="', BOARD_WEB_APP_HTML)
        self.assertIn('" draggable="true"><div class="column__head"', BOARD_WEB_APP_HTML)
        self.assertIn("function handleBoardColumnDragStart(event)", BOARD_WEB_APP_HTML)
        self.assertIn("if (target.closest('.card')) return;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (target.closest('button, input, textarea, select, a, label')) return;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function handleBoardColumnDragOver(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleBoardColumnDragLeave(event)", BOARD_WEB_APP_HTML)
        self.assertIn("async function handleBoardColumnDrop(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function moveColumn(columnId, beforeColumnId = '')", BOARD_WEB_APP_HTML
        )
        self.assertIn("'/api/move_column'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "document.addEventListener('dragstart', handleBoardColumnDragStart);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "document.addEventListener('drop', handleBoardColumnDrop);", BOARD_WEB_APP_HTML
        )
        self.assertIn("document.addEventListener('dragend', finishBoardDrag);", BOARD_WEB_APP_HTML)
        self.assertIn(".column.is-column-drop-target {", BOARD_WEB_APP_HTML)
        self.assertIn('.column[draggable="true"] {', BOARD_WEB_APP_HTML)

    def test_board_snapshot_polling_is_throttled_and_visibility_aware(self) -> None:
        self.assertIn("refreshInFlight: null", BOARD_WEB_APP_HTML)
        self.assertIn("const SNAPSHOT_POLL_INTERVAL_MS = 8000;", BOARD_WEB_APP_HTML)
        self.assertIn("const SNAPSHOT_POLL_HIDDEN_INTERVAL_MS = 120000;", BOARD_WEB_APP_HTML)
        self.assertIn("function snapshotPollIntervalMs()", BOARD_WEB_APP_HTML)
        self.assertIn("function scheduleNextSnapshotPoll()", BOARD_WEB_APP_HTML)
        self.assertIn("function handleSnapshotVisibilityChange()", BOARD_WEB_APP_HTML)
        self.assertIn("function refreshSnapshotRevision()", BOARD_WEB_APP_HTML)
        self.assertIn("if (!document.hidden) refreshSnapshotRevision();", BOARD_WEB_APP_HTML)
        self.assertIn(
            "document.addEventListener('visibilitychange', handleSnapshotVisibilityChange);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("state.pollHandle = window.setTimeout(async () => {", BOARD_WEB_APP_HTML)
        self.assertIn("await refreshSnapshotRevision();", BOARD_WEB_APP_HTML)
        self.assertIn("/api/get_board_revision?compact=1&include_archive=0", BOARD_WEB_APP_HTML)
        self.assertIn("scheduleNextSnapshotPoll();", BOARD_WEB_APP_HTML)
        self.assertIn("const SNAPSHOT_POLL_MODAL_INTERVAL_MS = 15000;", BOARD_WEB_APP_HTML)
        self.assertIn("function hasOpenWorkspaceModal()", BOARD_WEB_APP_HTML)
        self.assertIn("if (perfEnabled()) {\n        stopSnapshotPolling();", BOARD_WEB_APP_HTML)

    def test_perf_instrumentation_is_wired_for_slow_paths(self) -> None:
        self.assertIn("const PERF_STORAGE_KEY = 'autostop-perf';", BOARD_WEB_APP_HTML)
        self.assertIn("function perfStart(name)", BOARD_WEB_APP_HTML)
        self.assertIn("function perfEnd(token, detail = {})", BOARD_WEB_APP_HTML)
        self.assertIn("window.performance?.measure?.(token.name", BOARD_WEB_APP_HTML)
        self.assertIn("perfStart('api:' + String(path || '').split('?')[0])", BOARD_WEB_APP_HTML)
        self.assertIn("perfMeasureAsync('refreshSnapshot'", BOARD_WEB_APP_HTML)
        self.assertIn("perfMeasureAsync('openCardWorkspace'", BOARD_WEB_APP_HTML)
        self.assertIn("perfMeasureAsync('loadModalData:'", BOARD_WEB_APP_HTML)
        self.assertIn("perfMeasureAsync('moveCard'", BOARD_WEB_APP_HTML)
        self.assertIn("perfStart('hydrateCard')", BOARD_WEB_APP_HTML)
        self.assertIn("perfStart('renderBoard')", BOARD_WEB_APP_HTML)
        self.assertIn("perfStart('renderFiles')", BOARD_WEB_APP_HTML)
        self.assertIn("perfMeasureAsync('saveCard'", BOARD_WEB_APP_HTML)

    def test_api_retries_readonly_network_errors_without_repeating_writes(self) -> None:
        self.assertIn("const API_READ_RETRY_LIMIT = 1;", BOARD_WEB_APP_HTML)
        self.assertIn("const API_PERF_READ_TIMEOUT_MS = 6000;", BOARD_WEB_APP_HTML)
        self.assertIn("function apiReadTimeoutMs()", BOARD_WEB_APP_HTML)
        self.assertIn("function delay(ms)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const retryLimit = request.method === 'GET' ? API_READ_RETRY_LIMIT : 0;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("const requestAttempt = { ...request };", BOARD_WEB_APP_HTML)
        self.assertIn("timedOut = true;", BOARD_WEB_APP_HTML)
        self.assertIn("controller.abort();", BOARD_WEB_APP_HTML)
        self.assertIn("if (timedOut && attempt < retryLimit) {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "for (let attempt = 0; attempt <= retryLimit; attempt += 1)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "await delay(API_READ_RETRY_BASE_DELAY_MS * (attempt + 1));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("if (!payload || typeof payload !== 'object')", BOARD_WEB_APP_HTML)
        self.assertIn("error.code = 'invalid_json';", BOARD_WEB_APP_HTML)
        self.assertIn("finishApiPerf({ error: error.code });", BOARD_WEB_APP_HTML)
        self.assertIn("return true;", BOARD_WEB_APP_HTML)
        self.assertIn("return false;", BOARD_WEB_APP_HTML)

    def test_archive_modal_uses_last_30_compact_rows(self) -> None:
        self.assertIn("АРХИВ / ПОСЛЕДНИЕ 30", BOARD_WEB_APP_HTML)
        self.assertIn("/api/get_board_snapshot?compact=1&include_archive=0", BOARD_WEB_APP_HTML)
        self.assertIn(
            "/api/list_archived_cards?limit=' + ARCHIVE_PREVIEW_LIMIT + '&compact=1",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".archive-row--compact {", BOARD_WEB_APP_HTML)
        self.assertIn(".archive-row__summary {", BOARD_WEB_APP_HTML)
        self.assertIn("function renderArchive() {", BOARD_WEB_APP_HTML)
        self.assertIn("compactDescription.length > 180", BOARD_WEB_APP_HTML)
        self.assertIn("archive-row archive-row--compact", BOARD_WEB_APP_HTML)
        self.assertIn("await restoreCard(target.dataset.restoreCard);", BOARD_WEB_APP_HTML)

    def test_archive_loader_does_not_reopen_modal_after_async_close(self) -> None:
        start = BOARD_WEB_APP_HTML.index(
            "async function loadArchive(openModal = false, { force = false } = {})"
        )
        end = BOARD_WEB_APP_HTML.index("function renderArchive()", start)
        loader = BOARD_WEB_APP_HTML[start:end]

        self.assertIn(
            "if (openModal) maybeOpenModal(els.archiveModal, true);\n        await pending;",
            loader,
        )
        self.assertNotIn(
            "renderArchive();\n          if (openModal) maybeOpenModal(els.archiveModal, true);",
            loader,
        )
        self.assertNotIn(
            "НЕ УДАЛОСЬ ЗАГРУЗИТЬ АРХИВ.</div>';\n          if (openModal) maybeOpenModal(els.archiveModal, true);",
            loader,
        )

    def test_operator_login_gate_hides_workspace_until_session_is_valid(self) -> None:
        self.assertIn("function setOperatorLoginGateOpen(isOpen)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "document.body.classList.toggle('operator-login-gate-open'", BOARD_WEB_APP_HTML
        )
        self.assertIn(".operator-login-gate-open .shell", BOARD_WEB_APP_HTML)
        self.assertIn("#identityModal.operator-login-gate", BOARD_WEB_APP_HTML)
        self.assertIn("setOperatorLoginGateOpen(false);", BOARD_WEB_APP_HTML)
        self.assertIn("setOperatorLoginGateOpen(true);", BOARD_WEB_APP_HTML)
        self.assertIn("window.__AUTOSTOP_UI_BOUND__ = true;", BOARD_WEB_APP_HTML)
        self.assertIn('#identityMeta[data-tone="error"]', BOARD_WEB_APP_HTML)
        self.assertIn("function setOperatorLoginFeedback(", BOARD_WEB_APP_HTML)
        self.assertIn("function setOperatorLoginBusy(isBusy)", BOARD_WEB_APP_HTML)
        self.assertIn("setOperatorLoginFeedback(message, { tone: 'error' });", BOARD_WEB_APP_HTML)
        self.assertIn("els.identitySave.disabled = busy;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "String(path || '').split('?')[0] === '/api/login_operator'",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "payload?.error?.message || 'Неверный логин или пароль.'",
            BOARD_WEB_APP_HTML,
        )

        ensure_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index("function ensureActor()") : BOARD_WEB_APP_HTML.index(
                "function configureOperatorIdentityUi()"
            )
        ]
        self.assertNotIn("els.identityModal.classList.add('is-open');", ensure_fragment)
        self.assertIn("openOperatorLoginModal();", ensure_fragment)

    def test_real_data_client_employee_archive_and_files_ui_have_scanability_hooks(self) -> None:
        self.assertIn('id="archiveSearchInput"', BOARD_WEB_APP_HTML)
        self.assertIn("function archiveCardSearchText(card)", BOARD_WEB_APP_HTML)
        self.assertIn("function filteredArchiveCards()", BOARD_WEB_APP_HTML)
        self.assertIn("archive-search-meta", BOARD_WEB_APP_HTML)
        self.assertIn('aria-label="Вернуть карточку ', BOARD_WEB_APP_HTML)

        self.assertIn("function clientRowAriaLabel(client)", BOARD_WEB_APP_HTML)
        self.assertIn("client-row__name", BOARD_WEB_APP_HTML)
        self.assertIn("client-row__chips", BOARD_WEB_APP_HTML)
        self.assertIn('aria-label="Клиент ', BOARD_WEB_APP_HTML)
        self.assertIn("function renderClientProfileEmptyState()", BOARD_WEB_APP_HTML)

        self.assertIn("function employeeRowAriaLabel(employee, summaryValue)", BOARD_WEB_APP_HTML)
        self.assertNotIn("employees-row__scanline", BOARD_WEB_APP_HTML)
        self.assertNotIn("employees-row__scan-chip", BOARD_WEB_APP_HTML)
        self.assertIn('aria-label="Сотрудник ', BOARD_WEB_APP_HTML)

        self.assertIn("function sharedFileMetaParts(file)", BOARD_WEB_APP_HTML)
        self.assertIn("shared-file-icon__meta-chip", BOARD_WEB_APP_HTML)
        self.assertIn('aria-label="Файл ', BOARD_WEB_APP_HTML)

    def test_sticky_dock_uses_single_icon_button_without_dropdown(self) -> None:
        self.assertIn('class="sticky-dock__button" id="stickyDockButton"', BOARD_WEB_APP_HTML)
        self.assertIn('aria-label="Новый стикер"', BOARD_WEB_APP_HTML)
        self.assertIn('title="Новый стикер"', BOARD_WEB_APP_HTML)
        self.assertIn("width: 44px;", BOARD_WEB_APP_HTML)
        self.assertIn("height: 44px;", BOARD_WEB_APP_HTML)
        self.assertIn(".sticky-dock__button svg {", BOARD_WEB_APP_HTML)
        self.assertIn("width: 22px;", BOARD_WEB_APP_HTML)
        self.assertIn("height: 22px;", BOARD_WEB_APP_HTML)
        self.assertIn(".sticky__text {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: calc(22px * var(--board-scale));", BOARD_WEB_APP_HTML)
        self.assertIn("font-weight: 800;", BOARD_WEB_APP_HTML)
        self.assertIn("color: #d61f1f;", BOARD_WEB_APP_HTML)
        self.assertNotIn("stickyDockMenu", BOARD_WEB_APP_HTML)
        self.assertNotIn("stickyCreateButton", BOARD_WEB_APP_HTML)
        self.assertNotIn("toggleStickyMenu", BOARD_WEB_APP_HTML)
        self.assertNotIn("closeStickyMenu", BOARD_WEB_APP_HTML)
        self.assertIn("const rawTarget = event.target;", BOARD_WEB_APP_HTML)
        self.assertIn("const target = rawTarget instanceof Element", BOARD_WEB_APP_HTML)
        self.assertIn(
            ": (rawTarget instanceof Node ? rawTarget.parentElement : null);", BOARD_WEB_APP_HTML
        )
        self.assertIn("if (!(target instanceof Element)) return;", BOARD_WEB_APP_HTML)
        self.assertIn("const closeTrigger = target.closest('[data-close]');", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const createInTrigger = target.closest('[data-create-in]');", BOARD_WEB_APP_HTML
        )
        self.assertIn("async function handleAuxiliaryBoardClick(target, event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleStickyModalOverlayClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function applyStickySnapshot(stickies)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (target === els.stickyDockButton || target.closest('#stickyDockButton')) {",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "if (await handleAuxiliaryBoardClick(target, event)) return;", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.stickyModal.addEventListener('click', handleStickyModalOverlayClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("if (applyStickySnapshot(data?.stickies || [])) {", BOARD_WEB_APP_HTML)
        self.assertIn("function handleStickyModalOverlayClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleRepairOrderModalOverlayClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function handleRepairOrderPaymentsModalOverlayClick(event)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function handleAgentModalOverlayClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleOperatorProfileModalOverlayClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleOperatorAdminModalOverlayClick(event)", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "if (event.target.classList.contains('modal')) closeStickyModal();", BOARD_WEB_APP_HTML
        )
        self.assertNotIn(
            "if (event.target.classList.contains('modal')) closeRepairOrderModal();",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "if (event.target.classList.contains('modal')) closeRepairOrderPaymentsModal();",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "if (event.target.classList.contains('modal')) closeAgentModal();", BOARD_WEB_APP_HTML
        )

    def test_ai_ui_exposes_new_entry_surface_and_legacy_fallback(self) -> None:
        self.assertIn('id="cardAgentButton"', BOARD_WEB_APP_HTML)
        self.assertIn('title="Индикатор карточки"', BOARD_WEB_APP_HTML)
        self.assertIn("function renderCardCleanupIndicator()", BOARD_WEB_APP_HTML)
        self.assertIn(
            'Старый AI-режим отключён. Используй кнопку "Индикатор карточки".',
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn('id="aiChatButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="agentDockButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="aiSurfaceModal"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="aiChatWindow"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="boardControlSettingsRow"', BOARD_WEB_APP_HTML)
        self.assertNotIn('data-entry-surface="full_card_enrichment"', BOARD_WEB_APP_HTML)
        self.assertNotIn("els.aiChatButton?.addEventListener(", BOARD_WEB_APP_HTML)
        self.assertNotIn("els.agentDockButton?.addEventListener(", BOARD_WEB_APP_HTML)
        self.assertNotIn("document.getElementById('aiSurfaceModal')", BOARD_WEB_APP_HTML)
        self.assertNotIn("document.getElementById('aiChatWindow')", BOARD_WEB_APP_HTML)
        self.assertNotIn("document.getElementById('aiChatButton')", BOARD_WEB_APP_HTML)
        self.assertNotIn(".agent-dock", BOARD_WEB_APP_HTML)
        self.assertNotIn(".dialog--ai-entry", BOARD_WEB_APP_HTML)
        self.assertNotIn(".ai-entry-", BOARD_WEB_APP_HTML)
        self.assertNotIn(".ai-chat-window", BOARD_WEB_APP_HTML)
        self.assertNotIn("els.boardControlToggle?.addEventListener(", BOARD_WEB_APP_HTML)
        self.assertNotIn("els.boardControlIntervalInput?.addEventListener(", BOARD_WEB_APP_HTML)
        self.assertNotIn("els.boardControlCooldownInput?.addEventListener(", BOARD_WEB_APP_HTML)
        self.assertNotIn("async function runCardCleanup()", BOARD_WEB_APP_HTML)
        self.assertNotIn("'/api/cleanup_card_content'", BOARD_WEB_APP_HTML)
        self.assertIn("async function runFullCardEnrichment()", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/run_full_card_enrichment'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cardAgentButton.addEventListener('click', runFullCardEnrichment);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("state.cardCleanupState = 'running';", BOARD_WEB_APP_HTML)
        self.assertIn("function stopCardCleanupPolling()", BOARD_WEB_APP_HTML)
        self.assertIn("function scheduleCardCleanupPolling(", BOARD_WEB_APP_HTML)
        self.assertIn("async function refreshCardCleanupState()", BOARD_WEB_APP_HTML)
        self.assertNotIn("Карточка приведена в порядок.", BOARD_WEB_APP_HTML)
        self.assertNotIn("Явных изменений для карточки не найдено.", BOARD_WEB_APP_HTML)

    def test_employees_module_is_exposed_in_topbar_and_repair_order_rows(self) -> None:
        self.assertIn('id="employeesButton">СОТРУДНИКИ</button>', BOARD_WEB_APP_HTML)
        self.assertIn("function ensureEmployeesUi()", BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesList"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesCardMode"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeNameInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeMiddleNameInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeePositionInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryModeInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeMaterialPercentInput"', BOARD_WEB_APP_HTML)
        self.assertNotIn('<select id="employeeSalaryModeInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeIncentivesPanel"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeIncentivesList"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeIncentiveAddChoices"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeShiftAccrualButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeShiftAccrualDialog"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeShiftAccrualAmountInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeShiftAccrualConfirmButton"', BOARD_WEB_APP_HTML)
        self.assertIn("+ СМЕНЫ", BOARD_WEB_APP_HTML)
        self.assertIn("ВЫПЛАТА ЗА СМЕНЫ", BOARD_WEB_APP_HTML)
        self.assertNotIn('id="employeeNoteDetails"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="employeeNoteInput"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="employeesSummaryStrip"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesDetailTable"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesDetailsPanel"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesReportShell"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesReportMeta"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesDetailsMeta"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryBalance"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryJournalTable"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryActionDialog"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryCashboxSelect"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryPayoutButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryAdvanceButton"', BOARD_WEB_APP_HTML)
        self.assertIn("<th>ТИП</th>", BOARD_WEB_APP_HTML)
        self.assertIn("<th>ПОЗИЦИЯ</th>", BOARD_WEB_APP_HTML)
        self.assertNotIn('id="employeesSearchInput"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="employeesVisibilityFilters"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="employeesListMeta"', BOARD_WEB_APP_HTML)
        self.assertIn("function openEmployeesModal()", BOARD_WEB_APP_HTML)
        self.assertIn("function saveEmployee()", BOARD_WEB_APP_HTML)
        self.assertIn("function deleteEmployee()", BOARD_WEB_APP_HTML)
        self.assertIn("function filteredEmployeesList()", BOARD_WEB_APP_HTML)
        self.assertIn("function renderEmployeesListPanel()", BOARD_WEB_APP_HTML)
        self.assertNotIn("function handleEmployeesSearchInput(event)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function handleEmployeesVisibilityFilterClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function confirmDiscardEmployeeChanges()", BOARD_WEB_APP_HTML)
        self.assertIn("function openEmployeeSalaryModal(", BOARD_WEB_APP_HTML)
        self.assertIn("function ensureEmployeeSalaryCashboxes()", BOARD_WEB_APP_HTML)
        self.assertIn("cashbox_id: cashboxId,", BOARD_WEB_APP_HTML)
        self.assertIn("function loadEmployeeSalarySheet(", BOARD_WEB_APP_HTML)
        self.assertIn("function renderEmployeeSalaryModal()", BOARD_WEB_APP_HTML)
        self.assertIn("function handleEmployeeSalaryActionConfirm()", BOARD_WEB_APP_HTML)
        salary_handler = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "async function handleEmployeeSalaryActionConfirm()"
            ) : BOARD_WEB_APP_HTML.index("async function handleEmployeeShiftAccrualConfirm()")
        ]
        self.assertIn("state.employeesLoadedMonth = '';", salary_handler)
        self.assertLess(
            salary_handler.index("state.employeesLoadedMonth = '';"),
            salary_handler.index("await loadEmployeesReference();"),
        )
        self.assertIn("function handleEmployeeShiftAccrualConfirm()", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/create_employee_shift_accrual'", BOARD_WEB_APP_HTML)
        self.assertIn("note: 'Выплата за смены за текущую неделю'", BOARD_WEB_APP_HTML)
        self.assertIn("await loadEmployeesReference();", BOARD_WEB_APP_HTML)
        self.assertIn("<th>ТИП</th>", BOARD_WEB_APP_HTML)
        self.assertIn("employeesLoadedMonth: ''", BOARD_WEB_APP_HTML)
        self.assertIn("employeesReferencePromise: null", BOARD_WEB_APP_HTML)
        self.assertIn("state.employeesLoadedMonth = month;", BOARD_WEB_APP_HTML)
        self.assertIn("await loadPayrollReport();", BOARD_WEB_APP_HTML)
        self.assertIn("renderEmployeesWorkspace();", BOARD_WEB_APP_HTML)
        shift_handler = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "async function handleEmployeeShiftAccrualConfirm()"
            ) : BOARD_WEB_APP_HTML.index("function renderEmployeesWorkspace()")
        ]
        self.assertLess(
            shift_handler.index("renderEmployeesWorkspace();"),
            shift_handler.index("await loadPayrollReport();"),
        )
        self.assertIn(
            "employee.balance_total ?? summary?.balance_total ?? summary?.total_salary",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function renderEmployeeProfileMeta()", BOARD_WEB_APP_HTML)
        self.assertNotIn("function renderEmployeesSummaryStrip()", BOARD_WEB_APP_HTML)
        self.assertIn("function handleEmployeesDetailClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function syncEmployeeSalaryModeUi()", BOARD_WEB_APP_HTML)
        self.assertIn("function renderEmployeeIncentives()", BOARD_WEB_APP_HTML)
        self.assertIn("function setEmployeeIncentiveActive(", BOARD_WEB_APP_HTML)
        self.assertIn("function syncEmployeeSalaryModeFromIncentives(", BOARD_WEB_APP_HTML)
        self.assertIn("function employeeIncentiveSummaryLabel(employee)", BOARD_WEB_APP_HTML)
        self.assertIn("Начисляется по пятницам в 20:00.", BOARD_WEB_APP_HTML)
        self.assertIn("function employeeIncentiveDefinition(kind)", BOARD_WEB_APP_HTML)
        self.assertIn("function employeeIncentiveInput(kind)", BOARD_WEB_APP_HTML)
        self.assertIn("function syncEmployeesReportPanelUi()", BOARD_WEB_APP_HTML)
        self.assertIn("function hydrateEmployeesUiRefs()", BOARD_WEB_APP_HTML)
        self.assertIn("function bindEmployeesUiEvents()", BOARD_WEB_APP_HTML)
        self.assertIn("function addEmployeeFromForm()", BOARD_WEB_APP_HTML)
        self.assertIn("function employeeCombinedNameFromForm()", BOARD_WEB_APP_HTML)
        self.assertIn("employeeCreateMode: false", BOARD_WEB_APP_HTML)
        self.assertIn("employeesReportDetailsOpen: false", BOARD_WEB_APP_HTML)
        self.assertIn("state.employeeCreateMode = true;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (state.employeeCreateMode && employeeFormHasUnsavedChanges())",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("create_mode: Boolean(state.employeeCreateMode)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "employee_id: state.employeeCreateMode ? '' : (state.activeEmployeeId || '')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("setStatus('УКАЖИ ИМЯ СОТРУДНИКА.', true);", BOARD_WEB_APP_HTML)
        self.assertNotIn('class="employees-search" id="employeesSearchInput"', BOARD_WEB_APP_HTML)
        self.assertNotIn(
            'class="employees-filterbar" id="employeesVisibilityFilters"', BOARD_WEB_APP_HTML
        )
        self.assertNotIn('class="employees-list-meta" id="employeesListMeta"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="employeeToggleButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSaveButton"', BOARD_WEB_APP_HTML)
        self.assertIn(".employees-layout {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "grid-template-columns: minmax(360px, 390px) minmax(0, 1fr);", BOARD_WEB_APP_HTML
        )
        self.assertIn(".employees-list-tools {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-search {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-filterbar {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-list-meta,", BOARD_WEB_APP_HTML)
        self.assertIn("align-items: stretch;", BOARD_WEB_APP_HTML)
        self.assertIn("height: 100%;", BOARD_WEB_APP_HTML)
        self.assertIn("flex: 0 0 auto;", BOARD_WEB_APP_HTML)
        self.assertIn("flex: 1 1 auto;", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-card-head-main {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-card-actions {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-report-shell {", BOARD_WEB_APP_HTML)
        self.assertIn('[data-details-open="false"]', BOARD_WEB_APP_HTML)
        self.assertIn(".employees-report-panel--details {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-kpi--accent {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-row__summary {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-row__summary-label {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-row {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-row__actions {", BOARD_WEB_APP_HTML)
        self.assertIn("color: var(--text);", BOARD_WEB_APP_HTML)
        self.assertIn(
            ".employees-row__salary,\n"
            "    .employees-row__report {\n"
            "      display: flex;\n"
            "      align-items: center;\n"
            "      justify-content: center;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("      text-align: center;\n      align-self: stretch;", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-card-head {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-field--compact {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-field--salary {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-form-grid {", BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-columns: repeat(12, minmax(0, 1fr));", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-incentives {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-incentive-row {", BOARD_WEB_APP_HTML)
        self.assertIn('type="hidden" value="percent_only"', BOARD_WEB_APP_HTML)
        self.assertIn('data-employee-incentive-add="', BOARD_WEB_APP_HTML)
        self.assertIn('data-employee-incentive-remove="', BOARD_WEB_APP_HTML)
        self.assertIn('data-employee-incentive-value="', BOARD_WEB_APP_HTML)
        self.assertIn("inputKey: 'employeeBaseSalaryInput'", BOARD_WEB_APP_HTML)
        self.assertIn("inputKey: 'employeeWorkPercentInput'", BOARD_WEB_APP_HTML)
        self.assertIn("inputKey: 'employeeMaterialPercentInput'", BOARD_WEB_APP_HTML)
        self.assertIn("defaultValue: '10'", BOARD_WEB_APP_HTML)
        self.assertIn("activeModes: ['salary_only', 'salary_plus_percent']", BOARD_WEB_APP_HTML)
        self.assertIn("return 'none';", BOARD_WEB_APP_HTML)
        self.assertIn("Выплата с работ", BOARD_WEB_APP_HTML)
        self.assertIn("Материалы и запчасти", BOARD_WEB_APP_HTML)
        self.assertNotIn("Сначала добавьте другую форму начисления", BOARD_WEB_APP_HTML)
        self.assertIn("ОТЧЕСТВО", BOARD_WEB_APP_HTML)
        self.assertIn("name: employeeCombinedNameFromForm(),", BOARD_WEB_APP_HTML)
        self.assertIn("material_percent: normalizeEmployeeComparableNumber", BOARD_WEB_APP_HTML)
        self.assertIn("employeeMaterialPercentInput.value", BOARD_WEB_APP_HTML)
        self.assertNotIn('id="employeesSummaryPanel"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="employeesSummaryTable"', BOARD_WEB_APP_HTML)
        self.assertNotIn("employees-summary-strip", BOARD_WEB_APP_HTML)
        self.assertNotIn("employees-note-details", BOARD_WEB_APP_HTML)
        self.assertNotIn("note: els.employeeNoteInput.value", BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesDetailsPanel"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesReportShell"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesReportMeta"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesDetailsMeta"', BOARD_WEB_APP_HTML)
        self.assertIn("Выберите сотрудника слева, чтобы открыть детализацию.", BOARD_WEB_APP_HTML)
        self.assertIn("Детализация появится после выбора сотрудника.", BOARD_WEB_APP_HTML)
        self.assertIn("Выберите сотрудника слева, чтобы увидеть его наряды.", BOARD_WEB_APP_HTML)
        self.assertIn('placeholder="0"', BOARD_WEB_APP_HTML)
        self.assertIn('data-employee-salary="', BOARD_WEB_APP_HTML)
        self.assertIn('data-employee-report="', BOARD_WEB_APP_HTML)
        self.assertIn("К ВЫПЛАТЕ", BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryTitle"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalarySummary"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReportModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReportTitle"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReportText"', BOARD_WEB_APP_HTML)
        self.assertIn("employee-salary-report__text", BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReportDownloadButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReconciliationPeriodModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReconciliationPeriodTitle"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReconciliationOpenButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReconciliationCancelButton"', BOARD_WEB_APP_HTML)
        self.assertIn("ОТЧЁТ ПО НАЧИСЛЕНИЯМ", BOARD_WEB_APP_HTML)
        self.assertIn("ЗАГРУЗКА...", BOARD_WEB_APP_HTML)
        self.assertIn("СКАЧАТЬ .MD", BOARD_WEB_APP_HTML)
        self.assertIn("function currentEmployeeSalaryReportMonth()", BOARD_WEB_APP_HTML)
        self.assertIn("function openEmployeeSalaryReport(", BOARD_WEB_APP_HTML)
        self.assertIn("function loadEmployeeSalaryReport(", BOARD_WEB_APP_HTML)
        self.assertIn("&month=' + encodeURIComponent(month)", BOARD_WEB_APP_HTML)
        self.assertIn("els.employeeSalaryReportText.textContent", BOARD_WEB_APP_HTML)
        self.assertIn("function renderEmployeeSalaryReportModal()", BOARD_WEB_APP_HTML)
        self.assertIn("function downloadEmployeeSalaryReport()", BOARD_WEB_APP_HTML)
        self.assertIn("function loadEmployeeSalaryReconciliation(", BOARD_WEB_APP_HTML)
        self.assertIn("function employeeSalaryReconciliationPrintUrl(", BOARD_WEB_APP_HTML)
        self.assertIn("function employeeSalaryReconciliationApiPath(", BOARD_WEB_APP_HTML)
        self.assertIn("function employeeSalaryReconciliationQueryParams(", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function openEmployeeSalaryReconciliationPeriodDialog(",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function openSelectedEmployeeSalaryReconciliationPrint(",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function handleEmployeeSalaryReconciliationPeriodChange(", BOARD_WEB_APP_HTML
        )
        self.assertIn('id="employeeSalaryReconciliationPeriod"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReconciliationPeriodMode"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReconciliationDaysInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReconciliationDateFromInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryReconciliationDateToInput"', BOARD_WEB_APP_HTML)
        self.assertIn("ПЕРИОД АКТА", BOARD_WEB_APP_HTML)
        self.assertIn("params.set('days', String(days));", BOARD_WEB_APP_HTML)
        self.assertIn("params.set('date_from', dateFrom);", BOARD_WEB_APP_HTML)
        self.assertIn("params.set('date_to', dateTo);", BOARD_WEB_APP_HTML)
        self.assertIn("/employee_salary_reconciliation_print?", BOARD_WEB_APP_HTML)
        self.assertIn('type="button" data-employee-report="', BOARD_WEB_APP_HTML)
        self.assertIn(
            "ВЫБРАТЬ ПЕРИОД И ОТКРЫТЬ ПЕЧАТНЫЙ АКТ СВЕРКИ ЗАРПЛАТЫ",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "employeeSalaryReconciliationPeriodModal: 'employee-salary-reconciliation-period'",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "'employee-salary-reconciliation-period': els.employeeSalaryReconciliationPeriodModal",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function employeeSalaryReconciliationPrintDate(", BOARD_WEB_APP_HTML)
        self.assertIn("function employeeSalaryReconciliationEmptyText(", BOARD_WEB_APP_HTML)
        self.assertNotIn("За последние 30 дней движений нет.", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function createEmployeeSalaryReconciliationPrintWindow(",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function buildEmployeeSalaryReconciliationPrintHtml(", BOARD_WEB_APP_HTML)
        self.assertIn("function openEmployeeSalaryReconciliationPrint(", BOARD_WEB_APP_HTML)
        self.assertIn(
            "'/api/get_employee_salary_reconciliation?'",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("Акт сверки зарплаты", BOARD_WEB_APP_HTML)
        self.assertIn("Загрузка акта сверки зарплаты", BOARD_WEB_APP_HTML)
        self.assertIn("@media print", BOARD_WEB_APP_HTML)
        self.assertIn("window.print()", BOARD_WEB_APP_HTML)
        self.assertIn("Бухгалтер", BOARD_WEB_APP_HTML)
        self.assertIn("Сотрудник", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const generatedAt = employeeSalaryReconciliationPrintDate", BOARD_WEB_APP_HTML
        )
        self.assertNotIn(
            "const generatedAt = employeeSalaryReconciliationText(period.generated_at",
            BOARD_WEB_APP_HTML,
        )
        report_handler_start = BOARD_WEB_APP_HTML.index("async function openEmployeeSalaryReport")
        report_handler_end = BOARD_WEB_APP_HTML.index(
            "async function handleEmployeeSalaryActionConfirm", report_handler_start
        )
        report_handler = BOARD_WEB_APP_HTML[report_handler_start:report_handler_end]
        self.assertIn("loadEmployeeSalaryReconciliation", report_handler)
        self.assertLess(
            report_handler.index("createEmployeeSalaryReconciliationPrintWindow"),
            report_handler.index("loadEmployeeSalaryReconciliation"),
        )
        self.assertNotIn("get_employee_salary_report", report_handler)
        list_handler_start = BOARD_WEB_APP_HTML.index("function handleEmployeesListClick")
        list_handler_end = BOARD_WEB_APP_HTML.index(
            "function handleEmployeesModalFormInput", list_handler_start
        )
        list_handler = BOARD_WEB_APP_HTML[list_handler_start:list_handler_end]
        self.assertIn("openEmployeeSalaryReconciliationPeriodDialog(employeeId)", list_handler)
        self.assertNotIn("reportButton instanceof HTMLAnchorElement", list_handler)
        self.assertNotIn("window.open(printUrl", list_handler)
        self.assertNotIn("window.location.href = printUrl", list_handler)
        self.assertNotIn('id="employeeSalaryReportSummary"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="employeeSalaryReportSections"', BOARD_WEB_APP_HTML)
        self.assertNotIn("function employeeSalaryReportSummaryItems(", BOARD_WEB_APP_HTML)
        self.assertNotIn("function employeeSalaryReportGroupHtml(", BOARD_WEB_APP_HTML)
        self.assertIn("bindEmployeesUiEvents();", BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesCreateButton"', BOARD_WEB_APP_HTML)
        self.assertIn(">ДОБАВИТЬ<", BOARD_WEB_APP_HTML)
        self.assertIn("employees-dialog-actions", BOARD_WEB_APP_HTML)
        self.assertIn(">СОХРАНИТЬ<", BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeDeleteButton"', BOARD_WEB_APP_HTML)
        self.assertIn("<th", BOARD_WEB_APP_HTML)
        self.assertIn(">Закупка<", BOARD_WEB_APP_HTML)
        self.assertIn(">Списал<", BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-cell="cost_price"', BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "els.employeeToggleButton?.addEventListener('click', toggleEmployee);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "els.employeesSearchInput?.addEventListener('input', handleEmployeesSearchInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "els.employeesVisibilityFilters?.addEventListener('click', handleEmployeesVisibilityFilterClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.employeesMonthInput?.addEventListener('change', handleEmployeesMonthChange);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.employeeSalaryReconciliationPeriodMode?.addEventListener('change', handleEmployeeSalaryReconciliationPeriodChange);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.employeeSalaryReconciliationDaysInput?.addEventListener('input', handleEmployeeSalaryReconciliationPeriodChange);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.employeeSalaryReconciliationOpenButton?.addEventListener('click', openSelectedEmployeeSalaryReconciliationPrint);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.employeeSalaryReconciliationCancelButton?.addEventListener('click', closeEmployeeSalaryReconciliationPeriodDialog);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "els.employeesSummaryTable?.addEventListener('dblclick', handleEmployeesSummaryTableDoubleClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("state.employeesReportDetailsOpen = false;", BOARD_WEB_APP_HTML)
        self.assertIn("state.employeesReportDetailsOpen = true;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.employeesCreateButton?.addEventListener('click', addEmployeeFromForm);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.employeeSaveButton?.addEventListener('click', saveEmployee);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.employeeDeleteButton?.addEventListener('click', deleteEmployee);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "els.employeesReportTabs?.addEventListener('click', handleEmployeesReportTabClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("if (!confirmDiscardEmployeeChanges()) return;", BOARD_WEB_APP_HTML)
        self.assertIn("renderEmployeesWorkspace();", BOARD_WEB_APP_HTML)
        self.assertIn("els.employeeNameInput.focus();", BOARD_WEB_APP_HTML)
        self.assertIn("await saveEmployee();", BOARD_WEB_APP_HTML)
        self.assertIn("target === els.employeeMiddleNameInput", BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-cell="executor_id"', BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderExecutorOptionsHtml", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.employeesButton.addEventListener('click', openEmployeesModal);", BOARD_WEB_APP_HTML
        )
        self.assertNotIn("function updateEmployeesListMeta()", BOARD_WEB_APP_HTML)

    def test_employee_incentives_can_activate_all_defined_types(self) -> None:
        incentives_script = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "const EMPLOYEE_INCENTIVE_DEFINITIONS = ["
            ) : BOARD_WEB_APP_HTML.index("function employeeComparableSnapshot")
        ]
        self.assertIn("kind: 'base_salary'", incentives_script)
        self.assertIn("kind: 'work_percent'", incentives_script)
        self.assertIn("kind: 'material_percent'", incentives_script)
        self.assertIn(
            "return EMPLOYEE_INCENTIVE_DEFINITIONS.reduce((flags, item) =>", incentives_script
        )
        self.assertIn("const definition = employeeIncentiveDefinition(kind);", incentives_script)
        self.assertIn("definition.defaultValue", incentives_script)
        self.assertIn("const wasActive = Boolean(flags[kind]);", incentives_script)
        self.assertIn(
            "if (active && (!wasActive || !employeeIncentiveFieldValue(kind)))",
            incentives_script,
        )
        self.assertIn("return 'none';", incentives_script)
        self.assertIn("БЕЗ НАЧИСЛЕНИЙ", incentives_script)
        self.assertNotIn("activeRequiredGroupCounts", incentives_script)
        self.assertNotIn("requiredGroup", incentives_script)
        self.assertNotIn("Сначала добавьте другую форму начисления", incentives_script)
        self.assertNotIn("if (!(kind in flags)) return;", incentives_script)
        self.assertNotIn("activeDefinitions.slice", incentives_script)
        self.assertNotIn("inactiveDefinitions.slice", incentives_script)

    def test_employee_add_button_lives_in_modal_header_and_opens_clean_form(self) -> None:
        modal_head = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "+ '<div class=\"dialog__head dialog__floating-actions\">'"
            ) : BOARD_WEB_APP_HTML.index("+ '<div class=\"dialog__body-scroll employees-layout\">'")
        ]
        self.assertIn('id="employeesCreateButton" type="button">ДОБАВИТЬ</button>', modal_head)
        self.assertLess(
            modal_head.index('id="employeesCreateButton"'),
            modal_head.index('data-close="employees"'),
        )

        profile_actions = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "+ '<div class=\"employees-card-actions\">'"
            ) : BOARD_WEB_APP_HTML.index("+ '<div class=\"employees-form-grid\">'")
        ]
        self.assertNotIn('id="employeesCreateButton"', profile_actions)
        self.assertIn('id="employeeSaveButton"', profile_actions)

        add_handler = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "async function addEmployeeFromForm()"
            ) : BOARD_WEB_APP_HTML.index("function openEmployeesModal()")
        ]
        self.assertIn(
            "if (state.employeeCreateMode && employeeFormHasUnsavedChanges())",
            add_handler,
        )
        self.assertIn("state.activeEmployeeId = '';", add_handler)
        self.assertIn(
            "setStatus('ЗАПОЛНИТЕ НОВОГО СОТРУДНИКА И НАЖМИТЕ ДОБАВИТЬ.'",
            add_handler,
        )

        self.assertIn("employeeMiddleNameInput", BOARD_WEB_APP_HTML)
        self.assertIn("employeeCombinedNameFromForm()", BOARD_WEB_APP_HTML)

    def test_employee_delete_button_uses_dismissal_label(self) -> None:
        self.assertIn(
            '<button class="btn btn--ghost" id="employeeDeleteButton" type="button">УВОЛИТЬ СОТРУДНИКА</button>',
            BOARD_WEB_APP_HTML,
        )

    def test_employee_detail_repair_order_keeps_employee_modal_parent(self) -> None:
        detail_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "async function handleEmployeesDetailClick(event)"
            ) : BOARD_WEB_APP_HTML.index("function boardAgentContext()")
        ]

        self.assertIn("const shouldOpenRepairOrder", detail_fragment)
        self.assertNotIn("els.employeesModal.classList.remove('is-open');", detail_fragment)
        self.assertNotIn("await openCardById(cardId);", detail_fragment)
        self.assertIn(
            "await openRepairOrderCard(cardId, { parentLayer: 'employees' });",
            detail_fragment,
        )
        self.assertIn(
            "await openCardWorkspace(cardId, { openCardModalEl: true });",
            detail_fragment,
        )

    def test_modal_ladder_stack_and_parent_close_cascade_are_wired(self) -> None:
        self.assertIn("modalStack: []", BOARD_WEB_APP_HTML)
        self.assertIn("function modalKeyForElement(modalEl)", BOARD_WEB_APP_HTML)
        self.assertIn("function pushModal(key, modalEl, options = {})", BOARD_WEB_APP_HTML)
        self.assertIn("function popModal(key, options = {})", BOARD_WEB_APP_HTML)
        self.assertIn("function closeModalAndChildren(closeKey)", BOARD_WEB_APP_HTML)
        self.assertIn("function closeTopModal()", BOARD_WEB_APP_HTML)
        self.assertIn("function isModalOpen(key)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "document.addEventListener('keydown', handleModalStackKeydown);", BOARD_WEB_APP_HTML
        )
        self.assertIn("if (event.key !== 'Escape') return;", BOARD_WEB_APP_HTML)

        close_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "function closeNamedModal(closeKey)"
            ) : BOARD_WEB_APP_HTML.index("async function loadModalData(")
        ]
        self.assertIn("closeModalAndChildren(normalizedKey);", close_fragment)
        self.assertIn("closeEmployeeSalaryReportModal();", close_fragment)
        self.assertIn("closeAgentTasksModal();", close_fragment)
        self.assertIn("closeRepairOrderPaymentsModal();", close_fragment)
        self.assertIn("closeCashboxTransferModal();", close_fragment)
        self.assertIn("closeCashJournalModal();", close_fragment)

        workspace_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index("function hasOpenWorkspaceModal()") : BOARD_WEB_APP_HTML.index(
                "function snapshotPollIntervalMs()"
            )
        ]
        self.assertIn("state.modalStack.some(", workspace_fragment)
        for key in ("clients", "shared-files", "cashbox-transfer", "employeeSalary"):
            self.assertIn(f"'{key}'", workspace_fragment)

    def test_card_description_textarea_allows_extended_text(self) -> None:
        self.assertIn(
            'id="cardDescriptionEditor" class="description-editor" contenteditable="true"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'id="cardDescription" maxlength="20000" class="description-source" hidden',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".field--description .description-editor {", BOARD_WEB_APP_HTML)
        self.assertIn("--card-description-target-height: 550px;", BOARD_WEB_APP_HTML)
        self.assertIn("--card-description-max-height: 620px;", BOARD_WEB_APP_HTML)
        self.assertIn("min-height: 220px;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "height: min(var(--card-description-target-height), 48vh);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "max-height: min(var(--card-description-max-height), 58vh);", BOARD_WEB_APP_HTML
        )
        self.assertIn("function syncCardDescriptionHeight()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const preferredRows = text ? Math.max(8, Math.min(12, lineCount + 2)) : 7;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const configuredTargetHeight = cssPixelNumber(style.getPropertyValue('--card-description-target-height'));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const reservedMaxHeight = overviewHeight > reserveHeight ? overviewHeight - reserveHeight : Infinity;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("window.innerHeight * 0.48", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cardDescriptionEditor.addEventListener('input', handleCardDescriptionInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "requestAnimationFrame(() => syncCardDescriptionHeight());", BOARD_WEB_APP_HTML
        )

    def test_card_modal_header_does_not_render_created_updated_meta_line(self) -> None:
        self.assertNotIn('id="cardMetaLine"', BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "cardMetaLine: document.getElementById('cardMetaLine')", BOARD_WEB_APP_HTML
        )
        self.assertNotIn("els.cardMetaLine.textContent", BOARD_WEB_APP_HTML)

    def test_empty_card_description_editor_has_no_placeholder_hint(self) -> None:
        editor_match = re.search(r'<div id="cardDescriptionEditor"[^>]*>', BOARD_WEB_APP_HTML)
        self.assertIsNotNone(editor_match)
        editor_html = editor_match.group(0)
        self.assertNotIn("data-placeholder", editor_html)
        self.assertNotIn(".description-editor:empty::before", BOARD_WEB_APP_HTML)
        self.assertNotIn("Описание карточки", BOARD_WEB_APP_HTML)

    def test_vehicle_passport_identity_fields_are_equal_and_centered(self) -> None:
        for field_name in ("registration_plate", "production_year", "mileage"):
            self.assertRegex(
                BOARD_WEB_APP_HTML,
                r"\{ name: '" + field_name + r"'.*centered: true",
            )
        self.assertIn(
            "field.centered ? ' vehicle-field--centered' : ''",
            BOARD_WEB_APP_HTML,
        )
        centered_label_rule = re.search(
            r"\.vehicle-field--centered \.vehicle-field__label\s*\{(?P<body>.*?)\n\s*\}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(centered_label_rule)
        self.assertIn("justify-content: center;", centered_label_rule.group("body"))
        self.assertIn("text-align: center;", centered_label_rule.group("body"))

        centered_input_rule = re.search(
            r"\.vehicle-field--centered input\s*\{(?P<body>.*?)\n\s*\}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(centered_input_rule)
        self.assertIn("height: 32px;", centered_input_rule.group("body"))
        self.assertIn("text-align: center;", centered_input_rule.group("body"))
        self.assertIn("min-width: 0;", centered_input_rule.group("body"))

    def test_vehicle_passport_header_title_is_centered(self) -> None:
        self.assertIn('class="vehicle-panel__title-block"', BOARD_WEB_APP_HTML)
        title_block_rule = re.search(
            r"\.vehicle-panel__title-block\s*\{(?P<body>.*?)\n\s*\}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(title_block_rule)
        self.assertIn("justify-items: center;", title_block_rule.group("body"))
        self.assertIn("text-align: center;", title_block_rule.group("body"))

    def test_archive_button_reflects_repair_order_archive_availability(self) -> None:
        self.assertIn("function cardArchiveAvailability(card)", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderIsEmptyForArchive(order)", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderMoneyHasValue(value)", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderTextHasMeaning(value)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const archiveAvailable = cardArchiveAvailability(currentCard);", BOARD_WEB_APP_HTML
        )
        self.assertIn("els.archiveAction.disabled = !archiveAvailable;", BOARD_WEB_APP_HTML)
        self.assertIn("els.archiveAction.dataset.archiveAvailable", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (!repairOrderHasAnyData(card?.repair_order) || repairOrderIsEmptyForArchive(card?.repair_order)) return true;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("order?.is_empty_for_archive === true", BOARD_WEB_APP_HTML)
        self.assertIn("repairOrderMoneyHasValue(normalized.prepayment)", BOARD_WEB_APP_HTML)
        self.assertIn("normalized !== '-' && normalized !== '—'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "normalizeRepairOrderStatus(card?.repair_order?.status) === 'closed'",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".btn--danger:disabled", BOARD_WEB_APP_HTML)

    def test_card_description_supports_minimal_markdown_formatting(self) -> None:
        self.assertIn('id="cardDescriptionToolbar"', BOARD_WEB_APP_HTML)
        self.assertIn('data-description-format="bold"', BOARD_WEB_APP_HTML)
        self.assertIn('data-description-format="italic"', BOARD_WEB_APP_HTML)
        self.assertIn('data-description-format="underline"', BOARD_WEB_APP_HTML)
        self.assertIn("function applyDescriptionFormat(kind)", BOARD_WEB_APP_HTML)
        self.assertIn("function descriptionMarkdownToHtml(value)", BOARD_WEB_APP_HTML)
        self.assertIn("function descriptionEditorToMarkdown(editor)", BOARD_WEB_APP_HTML)
        self.assertIn("function setCardDescriptionValue(value)", BOARD_WEB_APP_HTML)
        self.assertIn("function syncCardDescriptionSourceFromEditor()", BOARD_WEB_APP_HTML)
        self.assertIn("function stripDescriptionFormatting(value)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const wrapper = document.createElement(tagName);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cardDescriptionEditor.addEventListener('beforeinput', handleDescriptionBeforeInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cardDescriptionEditor.addEventListener('keydown', handleDescriptionKeyboardShortcut);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cardDescriptionEditor.addEventListener('paste', handleDescriptionPaste);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cardDescriptionToolbar.addEventListener('mousedown', handleDescriptionToolbarMouseDown);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cardDescriptionToolbar.addEventListener('click', handleDescriptionFormatClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "return stripDescriptionFormatting(card?.board_summary || card?.description_preview || card?.description || 'Описание не указано');",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn('data-description-format="emoji"', BOARD_WEB_APP_HTML)
        self.assertNotIn("emoji-picker", BOARD_WEB_APP_HTML)
        self.assertNotIn('id="cardDescriptionPreview"', BOARD_WEB_APP_HTML)
        self.assertNotIn(".description-preview", BOARD_WEB_APP_HTML)
        self.assertNotIn("function renderDescriptionPreview()", BOARD_WEB_APP_HTML)
        self.assertNotIn("function scheduleDescriptionPreview()", BOARD_WEB_APP_HTML)

    def test_card_form_semantics_distinguish_make_model_and_short_essence(self) -> None:
        self.assertIn("const CARD_VEHICLE_FIELD_LABEL = 'Марка / модель';", BOARD_WEB_APP_HTML)
        self.assertIn("const CARD_TITLE_FIELD_LABEL = 'Краткая суть';", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const CARD_TITLE_REQUIRED_MESSAGE = 'УКАЖИ КРАТКУЮ СУТЬ КАРТОЧКИ.';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function configureCardFieldSemantics()", BOARD_WEB_APP_HTML)
        self.assertIn("vehicleLabel.textContent = 'МАРКА / МОДЕЛЬ';", BOARD_WEB_APP_HTML)
        self.assertIn("els.cardVehicle.placeholder = 'Nissan Teana J32';", BOARD_WEB_APP_HTML)
        self.assertIn("titleLabel.textContent = 'КРАТКАЯ СУТЬ';", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cardTitle.placeholder = 'Краткая суть проблемы, задачи или результата';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("configureCardFieldSemantics();", BOARD_WEB_APP_HTML)

    def test_board_cards_show_five_lines_of_description_preview(self) -> None:
        self.assertIn(".card__desc {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: calc(13px * var(--board-scale));", BOARD_WEB_APP_HTML)
        self.assertIn("-webkit-line-clamp: 5;", BOARD_WEB_APP_HTML)
        self.assertIn("function boardCardDescription(card)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "card?.board_summary || card?.description_preview || card?.description",
            BOARD_WEB_APP_HTML,
        )

    def test_card_modal_includes_centered_work_zone_and_separate_vehicle_panel(self) -> None:
        self.assertIn('class="dialog dialog--card dialog--fixed-actions"', BOARD_WEB_APP_HTML)
        self.assertIn(".dialog--card {", BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="dialog__head dialog__head--card dialog__floating-actions"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('class="dialog__title-row dialog__title-row--card"', BOARD_WEB_APP_HTML)
        self.assertIn(
            '<div class="dialog__title dialog__title--card" id="cardModalTitle">РАБОЧАЯ КАРТОЧКА</div>\n'
            '            <div class="dialog__tabs dialog__tabs--card">',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('class="dialog__tabs dialog__tabs--card"', BOARD_WEB_APP_HTML)
        self.assertIn(".dialog__title-row--card {", BOARD_WEB_APP_HTML)
        self.assertIn(".dialog__tabs--card .tab-btn {", BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto;", BOARD_WEB_APP_HTML)
        self.assertIn(".dialog__title--card {", BOARD_WEB_APP_HTML)
        self.assertIn("text-overflow: ellipsis;", BOARD_WEB_APP_HTML)
        self.assertIn("function limitCardModalHeading(value, maxLength = 92)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) minmax(246px, 284px);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("justify-content: stretch;", BOARD_WEB_APP_HTML)
        self.assertIn("max-width: none;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const configuredMetaReserve = metaStyle ? cssPixelNumber(metaStyle.getPropertyValue('--card-meta-panel-height')) : 0;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const metaReserveHeight = configuredMetaReserve > 0 ? configuredMetaReserve : measuredMetaReserve;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('class="subpanel vehicle-panel"', BOARD_WEB_APP_HTML)
        self.assertIn("z-index: 2;", BOARD_WEB_APP_HTML)
        self.assertIn("isolation: isolate;", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-panel::before {", BOARD_WEB_APP_HTML)
        self.assertIn('id="vehiclePanelSummary"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="vehiclePanelFlags"', BOARD_WEB_APP_HTML)
        self.assertIn('id="vehicleProfileFields"', BOARD_WEB_APP_HTML)
        self.assertIn(".overview-main__meta {", BOARD_WEB_APP_HTML)
        self.assertIn(".field--description .description-editor {", BOARD_WEB_APP_HTML)
        self.assertIn(".signal-panel {", BOARD_WEB_APP_HTML)
        self.assertIn(".tag-entry {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function applyCardModalState(card, { descriptionLoading = false, cardIsFull = true, preserveLazyPanels = false } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function resetCardModalState()", BOARD_WEB_APP_HTML)
        self.assertIn("async function persistCardPayload(payload)", BOARD_WEB_APP_HTML)
        self.assertIn("state.cardCreateColumnId = ''", BOARD_WEB_APP_HTML)
        self.assertIn("state.cardSaveInFlight = false", BOARD_WEB_APP_HTML)
        self.assertIn("cardSavePromise: null", BOARD_WEB_APP_HTML)
        self.assertIn("cardCloseAfterSave: false", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (state.cardSaveInFlight) return state.cardSavePromise || false;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("state.cardSaveInFlight = true;", BOARD_WEB_APP_HTML)
        self.assertIn("state.cardCreateColumnId || state.activeCard?.column", BOARD_WEB_APP_HTML)
        self.assertNotIn('id="cardButton" type="button"', BOARD_WEB_APP_HTML)
        self.assertIn('data-create-in="', BOARD_WEB_APP_HTML)
        self.assertIn('id="saveCardButton" type="button"', BOARD_WEB_APP_HTML)
        self.assertIn("fullCardCache: new Map()", BOARD_WEB_APP_HTML)
        self.assertIn("cardFetchInFlight: new Map()", BOARD_WEB_APP_HTML)
        self.assertIn("function cachedFullCardForSnapshot(card)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function fetchFullCard(cardId, expectedUpdatedAt = '')", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "api('/api/get_card?card_id=' + encodeURIComponent(normalizedCardId))",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("const cachedCard = snapshotCardById(normalizedCardId);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "openCardModal(cachedCard, { descriptionLoading: true, cardIsFull: false });",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("fullCard = cachedFullCard || await fetchFullCard", BOARD_WEB_APP_HTML)
        self.assertIn("function setCardDescriptionLoading(isLoading", BOARD_WEB_APP_HTML)
        self.assertIn("recordCardOpenSideEffects(normalizedCardId);", BOARD_WEB_APP_HTML)
        self.assertIn("function recordCardOpenSideEffects(cardId)", BOARD_WEB_APP_HTML)
        self.assertIn("CARD_OPEN_SIDE_EFFECT_DELAY_MS = 700", BOARD_WEB_APP_HTML)
        self.assertIn("api('/api/open_card'", BOARD_WEB_APP_HTML)
        save_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index("async function saveCard()") : BOARD_WEB_APP_HTML.index(
                "configureCardFieldSemantics();"
            )
        ]
        self.assertIn("clearCardOpenSideEffectTimer();", save_fragment)
        self.assertIn("return_card: false", BOARD_WEB_APP_HTML)
        self.assertIn("mark_seen: false", BOARD_WEB_APP_HTML)
        self.assertIn("function loadActiveCardTab(tabName)", BOARD_WEB_APP_HTML)
        self.assertIn("if (tabName === 'files') {", BOARD_WEB_APP_HTML)
        self.assertIn("if (tabName !== 'journal') return;", BOARD_WEB_APP_HTML)
        self.assertIn("renderActiveCardFiles();", BOARD_WEB_APP_HTML)
        self.assertIn("function cardJournalRequestUrl(cardId", BOARD_WEB_APP_HTML)
        self.assertIn("&compact=1&limit=' + safeLimit", BOARD_WEB_APP_HTML)
        self.assertIn("data-card-journal-load-more", BOARD_WEB_APP_HTML)
        self.assertNotIn("if (card?.id) loadLogs(card.id);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (els.cardModal?.classList.contains('is-open')) {\n        requestAnimationFrame(() => syncCardDescriptionHeight());\n      }",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "async function openCardWorkspace(cardId, { closeModalEl = null, openCardModalEl = true, openRepairOrder = false, repairOrderParentLayer = '' } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function openNewCardInColumn(columnId)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function openDefaultNewCard()", BOARD_WEB_APP_HTML)
        self.assertIn("function focusCardModalInitialControl()", BOARD_WEB_APP_HTML)
        self.assertIn("focusCardModalInitialControl();", BOARD_WEB_APP_HTML)
        self.assertIn("async function archiveActiveCard()", BOARD_WEB_APP_HTML)
        self.assertIn("setStatus(archiveBlockedMessage(message), true);", BOARD_WEB_APP_HTML)
        self.assertNotIn("window.alert(", BOARD_WEB_APP_HTML)
        self.assertIn("async function restoreActiveCard()", BOARD_WEB_APP_HTML)
        self.assertIn("async function handleCardWorkspaceClick(target)", BOARD_WEB_APP_HTML)
        self.assertIn("applyCardModalState(card, { descriptionLoading", BOARD_WEB_APP_HTML)
        self.assertIn("resetCardModalState();", BOARD_WEB_APP_HTML)
        self.assertIn("function cardModalHasUnsavedChanges()", BOARD_WEB_APP_HTML)
        self.assertIn("function syncCardSaveDirtyState()", BOARD_WEB_APP_HTML)
        self.assertIn("function scheduleCardSaveDirtyStateSync()", BOARD_WEB_APP_HTML)
        self.assertIn("function cardModalHeading(card)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const modalHeading = currentCard?.id ? cardModalHeading(currentCard) : 'Новая карточка';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.saveCardButton.classList.toggle('is-dirty', hasUnsavedChanges);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cardModal.addEventListener('input', scheduleCardSaveDirtyStateSync);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cardModal.addEventListener('change', scheduleCardSaveDirtyStateSync);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("#saveCardButton.is-dirty:not(:disabled) {", BOARD_WEB_APP_HTML)
        self.assertIn("Есть несохраненные изменения", BOARD_WEB_APP_HTML)
        self.assertIn("function closeCardModal({ force = false } = {})", BOARD_WEB_APP_HTML)
        self.assertIn("state.cardCloseAfterSave = true;", BOARD_WEB_APP_HTML)
        self.assertIn("СОХРАНЯЮ КАРТОЧКУ. ЗАКРОЮ ПОСЛЕ СОХРАНЕНИЯ.", BOARD_WEB_APP_HTML)
        self.assertIn("const data = await persistCardPayload(payload);", BOARD_WEB_APP_HTML)
        self.assertIn("const savedCard = data?.card || null;", save_fragment)
        self.assertIn("applySavedCardLocalPatch(savedCard);", save_fragment)
        self.assertIn(
            "applyCardModalState(savedCard, { preserveLazyPanels: true });",
            save_fragment,
        )
        self.assertIn("rememberCardModalCleanState(payload);", save_fragment)
        self.assertIn(
            "const shouldCloseAfterSave = saveSucceeded && els.cardModal?.classList.contains('is-open');",
            save_fragment,
        )
        self.assertIn("state.cardSavePromise = savePromise;", save_fragment)
        self.assertIn("if (shouldCloseAfterSave) closeCardModal({ force: true });", save_fragment)
        self.assertNotIn(
            "setStatus('КАРТОЧКА СОХРАНЕНА.', false);\n          closeCardModal({ force: true });",
            save_fragment,
        )
        self.assertIn(
            "state.cardSaveInFlight = true;\n      if (els.saveCardButton) els.saveCardButton.disabled = true;\n      syncCardSaveDirtyState();",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "expected_updated_at: state.editingId ? String(state.activeCard?.updated_at || '') : undefined",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "scheduleBackgroundSnapshotRefresh({ showSuccess: false, delay: 900 });",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "closeCardModal();\n        await refreshSnapshot(true);", BOARD_WEB_APP_HTML
        )
        self.assertIn("await openCardWorkspace(cardId);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const createInTrigger = target.closest('[data-create-in]');", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "if (createInTrigger instanceof HTMLElement) openNewCardInColumn(createInTrigger.dataset.createIn);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("if (await handleCardWorkspaceClick(target)) return;", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "els.cardButton.addEventListener('click', openDefaultNewCard);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.archiveAction.addEventListener('click', archiveActiveCard);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.restoreAction.addEventListener('click', restoreActiveCard);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            'class="dialog__foot dialog__foot--card dialog__floating-actions"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('class="dialog__foot-group dialog__foot-group--danger"', BOARD_WEB_APP_HTML)
        self.assertIn('class="dialog__foot-group dialog__foot-group--main"', BOARD_WEB_APP_HTML)

    def test_card_vehicle_panel_stays_inside_overview_scrollport(self) -> None:
        self.assertIn(
            '.dialog--card > .dialog__body-scroll[data-panel="overview"] {',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto;", BOARD_WEB_APP_HTML)
        self.assertIn(
            ".dialog--card > .dialog__body-scroll {\n      grid-row: 2;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            '.dialog--card > .dialog__body-scroll[data-panel="overview"] {\n      overflow: auto;\n    }',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("align-items: stretch;", BOARD_WEB_APP_HTML)
        self.assertIn(
            ".overview-main {\n      display: grid;\n      gap: 9px;\n      align-content: start;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto auto;", BOARD_WEB_APP_HTML)
        self.assertIn(
            ".vehicle-panel__fields {\n      display: grid;\n      gap: 5px;", BOARD_WEB_APP_HTML
        )
        self.assertIn("overflow-y: auto;", BOARD_WEB_APP_HTML)

    def test_long_modals_keep_action_buttons_outside_scroll_regions(self) -> None:
        self.assertIn('class="dialog dialog--card dialog--fixed-actions"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="dialog__head dialog__head--card dialog__floating-actions"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('class="dialog__body-scroll" data-panel="overview"', BOARD_WEB_APP_HTML)
        self.assertIn('class="dialog__body-scroll hidden" data-panel="files"', BOARD_WEB_APP_HTML)
        self.assertIn('class="dialog__body-scroll hidden" data-panel="journal"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="dialog__foot dialog__foot--card dialog__floating-actions"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".dialog--card > .dialog__body-scroll {", BOARD_WEB_APP_HTML)
        self.assertIn("grid-row: 3;", BOARD_WEB_APP_HTML)

        self.assertIn(
            'class="dialog dialog--repair-order dialog--fixed-actions"', BOARD_WEB_APP_HTML
        )
        self.assertIn(
            ".dialog--repair-order {\n      width: min(1320px, calc(100% - 16px));\n"
            "      height: min(93vh, 940px);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('class="repair-order-shell dialog__body-scroll"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="dialog__foot repair-order-footer dialog__floating-actions"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            '      </div>\n        <div class="dialog__foot repair-order-footer dialog__floating-actions">',
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            '      </div>\n        </div>\n        <div class="dialog__foot repair-order-footer dialog__floating-actions">',
            BOARD_WEB_APP_HTML,
        )

        self.assertIn('class="dialog dialog--clients dialog--fixed-actions"', BOARD_WEB_APP_HTML)
        self.assertIn('class="dialog__body-scroll clients-layout"', BOARD_WEB_APP_HTML)
        self.assertIn('class="clients-profile-head dialog__floating-actions"', BOARD_WEB_APP_HTML)
        self.assertIn("position: sticky;", BOARD_WEB_APP_HTML)

        self.assertIn('class="dialog dialog--employees dialog--fixed-actions"', BOARD_WEB_APP_HTML)
        self.assertIn('class="dialog__body-scroll employees-layout"', BOARD_WEB_APP_HTML)
        self.assertIn('class="employees-card-head dialog__floating-actions"', BOARD_WEB_APP_HTML)
        self.assertIn(".dialog--employees {", BOARD_WEB_APP_HTML)
        self.assertIn(
            ".dialog--employees .employees-card-head.dialog__floating-actions {",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("body.is-mobile-lite .dialog--fixed-actions {", BOARD_WEB_APP_HTML)
        self.assertIn("position: fixed;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "body.is-mobile-lite .modal {\n      padding: 0;\n      z-index: 60;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "body.is-mobile-lite #repairOrderModal {\n      z-index: 64;", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "body.is-mobile-lite .dialog--card {\n      width: 100vw;\n"
            "      height: 100dvh;\n      max-height: 100dvh;\n"
            "      padding: 10px;\n      grid-template-rows: auto minmax(0, 1fr) auto;",
            BOARD_WEB_APP_HTML,
        )

        self.assertIn(
            'class="dialog dialog--repair-order-print dialog--fixed-actions"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="dialog__head dialog__floating-actions"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="dialog__body dialog__body-scroll repair-order-print-layout"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="dialog__foot repair-order-print-footer dialog__floating-actions"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="dialog dialog--print-template-editor dialog--fixed-actions"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="dialog__body dialog__body-scroll print-template-editor"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="dialog dialog--inspection-sheet-form dialog--fixed-actions"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="dialog__body dialog__body-scroll inspection-sheet-form"',
            BOARD_WEB_APP_HTML,
        )

    def test_vehicle_panel_exposes_only_minimal_profile_fields(self) -> None:
        self.assertNotIn('id="vehicleAutofillButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn("function configureVehicleAutofillUi()", BOARD_WEB_APP_HTML)
        self.assertNotIn("function buildVehicleAutofillRawText()", BOARD_WEB_APP_HTML)
        self.assertNotIn("function autofillVehicleProfile()", BOARD_WEB_APP_HTML)
        self.assertNotIn("/api/autofill_vehicle_data", BOARD_WEB_APP_HTML)
        self.assertNotIn("АВТОЗАПОЛНИТЬ", BOARD_WEB_APP_HTML)
        self.assertIn(
            "await copyVehicleFieldValue(target.dataset.copyVehicleField);", BOARD_WEB_APP_HTML
        )
        self.assertIn("function syncVehicleCopyButtons()", BOARD_WEB_APP_HTML)
        self.assertIn("data-copy-available", BOARD_WEB_APP_HTML)
        self.assertIn("button.disabled = !value;", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-copy:disabled", BOARD_WEB_APP_HTML)
        self.assertNotIn("payload.image_base64", BOARD_WEB_APP_HTML)
        self.assertNotIn("vehicleAutofillImage.files?.[0]", BOARD_WEB_APP_HTML)
        self.assertNotIn('id="vehicleAutofillText"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="vehicleAutofillImage"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="vehicleAutofillStatus"', BOARD_WEB_APP_HTML)
        self.assertNotIn("Доп. данные:", BOARD_WEB_APP_HTML)
        self.assertNotIn("АНАЛИЗ ПОЛЕЙ КАРТОЧКИ", BOARD_WEB_APP_HTML)
        self.assertIn("const VEHICLE_FIELD_GROUPS = [", BOARD_WEB_APP_HTML)
        self.assertIn("display_name", BOARD_WEB_APP_HTML)
        self.assertIn("registration_plate", BOARD_WEB_APP_HTML)
        self.assertIn("production_year", BOARD_WEB_APP_HTML)
        self.assertIn("mileage", BOARD_WEB_APP_HTML)
        self.assertIn("customer_phone", BOARD_WEB_APP_HTML)
        self.assertIn("customer_name", BOARD_WEB_APP_HTML)
        self.assertIn(
            'autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("vin", BOARD_WEB_APP_HTML)
        self.assertIn("engine_model", BOARD_WEB_APP_HTML)
        self.assertIn("gearbox_model", BOARD_WEB_APP_HTML)
        self.assertIn("drivetrain", BOARD_WEB_APP_HTML)
        self.assertNotIn("oem_notes', label: 'Короткая заметка'", BOARD_WEB_APP_HTML)
        self.assertNotIn("{ name: 'engine_code'", BOARD_WEB_APP_HTML)
        self.assertNotIn("{ name: 'generation_or_platform'", BOARD_WEB_APP_HTML)
        self.assertIn("vehicle_profile: vehicleProfile,", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function splitVehicleDisplayName(value, productionYear = null)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function vehicleDisplayNameInputValue(profile)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "normalized.display_name = vehicleDisplayNameInputValue(normalized);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("profile.make_display = displayParts.make_display;", BOARD_WEB_APP_HTML)
        self.assertIn("profile.model_display = displayParts.model_display;", BOARD_WEB_APP_HTML)

    def test_vehicle_panel_hides_empty_summary_and_first_group_title(self) -> None:
        self.assertIn("VEHICLE_FIELD_GROUPS[0].title = '';", BOARD_WEB_APP_HTML)
        self.assertIn("vehicle-group--aggregates", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-group__title::after {", BOARD_WEB_APP_HTML)
        self.assertIn("height: 32px;", BOARD_WEB_APP_HTML)
        self.assertIn("Пробег", BOARD_WEB_APP_HTML)
        self.assertIn("Телефон клиента", BOARD_WEB_APP_HTML)
        self.assertIn("ФИО клиента", BOARD_WEB_APP_HTML)
        self.assertIn(
            "(group.title ? '<div class=\"vehicle-group__title\">' + escapeHtml(group.title) + '</div>' : '')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("const summaryLines = [];", BOARD_WEB_APP_HTML)
        self.assertNotIn("summaryLines.push('Пробег: ' + profile.mileage)", BOARD_WEB_APP_HTML)
        self.assertNotIn("const display = vehicleDisplayFromProfile(profile);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.vehiclePanelSummary.style.display = summaryLines.length ? '' : 'none';",
            BOARD_WEB_APP_HTML,
        )

    def test_vehicle_panel_places_mileage_before_customer_contact_fields(self) -> None:
        identity_grid_index = BOARD_WEB_APP_HTML.index("vehicle-group--identity")
        display_index = BOARD_WEB_APP_HTML.index("{ name: 'display_name'")
        plate_index = BOARD_WEB_APP_HTML.index("{ name: 'registration_plate'")
        year_index = BOARD_WEB_APP_HTML.index("{ name: 'production_year'")
        mileage_index = BOARD_WEB_APP_HTML.index("{ name: 'mileage'")
        customer_phone_index = BOARD_WEB_APP_HTML.index("{ name: 'customer_phone'")
        self.assertLess(identity_grid_index, display_index)
        self.assertLess(display_index, plate_index)
        self.assertLess(plate_index, year_index)
        self.assertLess(year_index, mileage_index)
        self.assertLess(mileage_index, customer_phone_index)

    def test_vehicle_panel_collapses_cleanly_on_narrow_screens(self) -> None:
        self.assertIn("@media (max-width: 760px) {", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-group__grid { grid-template-columns: 1fr; }", BOARD_WEB_APP_HTML)
        self.assertIn(
            ".vehicle-panel__fields { max-height: none; overflow: visible; padding-right: 0; }",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".vehicle-panel::before { display: none; }", BOARD_WEB_APP_HTML)
        self.assertIn(".dialog--card { width: min(1080px, 100%); }", BOARD_WEB_APP_HTML)

    def test_mobile_lite_mode_collapses_board_and_hides_heavy_controls(self) -> None:
        self.assertIn("const MOBILE_LITE_BREAKPOINT = 760;", BOARD_WEB_APP_HTML)
        self.assertIn("function detectMobileLiteMode()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function applyMobileLiteMode(nextMode = detectMobileLiteMode())", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "if (state.mobileLite) return applyBoardScale(1, { syncInput: false });",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("body.is-mobile-lite .board {", BOARD_WEB_APP_HTML)
        self.assertIn("body.is-mobile-lite .column__head-actions {", BOARD_WEB_APP_HTML)
        self.assertIn("body.is-mobile-lite .dialog--card {", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "body.is-mobile-lite .topbar__actions .btn:not(#cardButton):not(#archiveButton) {\n      display: none;",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "body.is-mobile-lite .topbar__rare-actions .btn:not(#archiveButton) {\n      display: none;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "body.is-mobile-lite .topbar__rare-actions,\n    body.is-mobile-lite .topbar__actions {",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("overflow-x: auto;", BOARD_WEB_APP_HTML)
        self.assertIn(
            'body.is-mobile-lite .dialog__tabs--card .tab-btn[data-tab="journal"] {',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'body.is-mobile-lite .dialog__tabs--card .tab-btn[data-tab="journal"] {\n      display: inline-block;',
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("state.mobileLite && name === 'journal'", BOARD_WEB_APP_HTML)
        self.assertIn("body.is-mobile-lite .vehicle-panel__fields {", BOARD_WEB_APP_HTML)
        self.assertIn("applyMobileLiteMode(detectMobileLiteMode());", BOARD_WEB_APP_HTML)
        self.assertIn("window.addEventListener('resize', syncMobileLiteMode);", BOARD_WEB_APP_HTML)

    def test_clients_module_ui_and_card_suggestions_are_available(self) -> None:
        self.assertIn('id="clientsButton">КЛИЕНТЫ</button>', BOARD_WEB_APP_HTML)
        self.assertIn('id="clientsModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="clientsList"', BOARD_WEB_APP_HTML)
        self.assertIn("ПОИСК: ФИО, телефон, госномер, авто", BOARD_WEB_APP_HTML)
        self.assertIn("clients-field--type", BOARD_WEB_APP_HTML)
        self.assertIn("clients-name-field", BOARD_WEB_APP_HTML)
        self.assertIn("clientProfilePhone", BOARD_WEB_APP_HTML)
        self.assertIn("function clientTypeDisplayLabel(value)", BOARD_WEB_APP_HTML)
        self.assertIn("'Физ. лицо'", BOARD_WEB_APP_HTML)
        self.assertIn("'Юр. лицо'", BOARD_WEB_APP_HTML)
        self.assertIn('<option value="person">Физ. лицо</option>', BOARD_WEB_APP_HTML)
        self.assertIn('<option value="company">Юр. лицо</option>', BOARD_WEB_APP_HTML)
        card_client_type_select = re.search(
            r'<select id="cardClientCreateTypeInput">(?P<body>.*?)</select>',
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(card_client_type_select)
        assert card_client_type_select is not None
        self.assertIn(
            '<option value="person">Физ. лицо</option>', card_client_type_select.group("body")
        )
        self.assertIn(
            '<option value="company">Юр. лицо</option>', card_client_type_select.group("body")
        )
        self.assertIn("function clientFormDirtySnapshot()", BOARD_WEB_APP_HTML)
        self.assertIn("function updateClientSaveButtonState()", BOARD_WEB_APP_HTML)
        self.assertIn("state.clientsDraftBaseline", BOARD_WEB_APP_HTML)
        self.assertIn("els.clientSaveButton.disabled = !dirty;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.clientSaveButton.classList.toggle('is-dirty', dirty);", BOARD_WEB_APP_HTML
        )
        self.assertIn("clientPhoneMatchKeys", BOARD_WEB_APP_HTML)
        self.assertIn("clientPhoneSearchVariants", BOARD_WEB_APP_HTML)
        self.assertIn("const CLIENT_PHONE_LIMIT = 3;", BOARD_WEB_APP_HTML)
        self.assertIn("const CLIENT_EMAIL_LIMIT = 3;", BOARD_WEB_APP_HTML)
        self.assertIn('id="clientPhoneFields"', BOARD_WEB_APP_HTML)
        self.assertIn('id="clientPhoneAddButton"', BOARD_WEB_APP_HTML)
        self.assertIn("function renderClientPhoneFields(values = [''])", BOARD_WEB_APP_HTML)
        self.assertIn('id="clientEmailFields"', BOARD_WEB_APP_HTML)
        self.assertIn('id="clientEmailAddButton"', BOARD_WEB_APP_HTML)
        self.assertIn("function renderClientEmailFields(values = [''])", BOARD_WEB_APP_HTML)
        self.assertIn("function readClientEmailFields()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function renderVehicleCustomerPhoneFields(values = [''])", BOARD_WEB_APP_HTML
        )
        self.assertIn("customer_phones", BOARD_WEB_APP_HTML)
        self.assertIn("clientDebtCard", BOARD_WEB_APP_HTML)
        self.assertIn("clientDebtValue", BOARD_WEB_APP_HTML)
        self.assertIn("const CLIENTS_INITIAL_LIMIT = 35;", BOARD_WEB_APP_HTML)
        self.assertIn("const CLIENTS_SEARCH_LIMIT = 50;", BOARD_WEB_APP_HTML)
        self.assertIn("const CARD_CLIENT_SUGGESTION_LIMIT = 6;", BOARD_WEB_APP_HTML)
        self.assertIn("clientsRequestSeq", BOARD_WEB_APP_HTML)
        self.assertIn("clientsMetaState", BOARD_WEB_APP_HTML)
        self.assertIn("clientsLoaded: false,", BOARD_WEB_APP_HTML)
        self.assertIn("state.clientsLoaded = true;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (state.clientsLoaded && !String(state.clientsQuery || '').trim()) {",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("maybeOpenModal(els.clientsModal, true);", BOARD_WEB_APP_HTML)
        self.assertIn("ПОИСК ПО ВСЕМ КЛИЕНТАМ", BOARD_WEB_APP_HTML)
        self.assertIn(
            "'/api/list_clients?limit=' + CLIENTS_INITIAL_LIMIT + '&include_stats=false'",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("'&limit=' + CLIENTS_SEARCH_LIMIT", BOARD_WEB_APP_HTML)
        load_clients_fragment = BOARD_WEB_APP_HTML.split("async function saveClientProfile()", 1)[0]
        self.assertNotIn(
            "if (state.clientsActiveId) await selectClient(state.clientsActiveId);",
            load_clients_fragment,
        )
        self.assertNotIn("state.clientsActiveId = state.clients[0].id;", load_clients_fragment)
        self.assertIn(
            "if (openModal && !state.clientsActiveId && !state.clientsActiveProfile) {",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("applyClientSearchFilter", BOARD_WEB_APP_HTML)
        self.assertNotIn("clientsAll", BOARD_WEB_APP_HTML)
        self.assertIn("client-mini__order-number", BOARD_WEB_APP_HTML)
        self.assertIn("client-mini__order-status", BOARD_WEB_APP_HTML)
        self.assertIn("client-mini__order-total-value", BOARD_WEB_APP_HTML)
        self.assertIn('id="clientVehicleAddButton"', BOARD_WEB_APP_HTML)
        self.assertIn("function renderClientVehiclesList(vehicles)", BOARD_WEB_APP_HTML)
        self.assertIn(".client-mini__vehicle-actions {", BOARD_WEB_APP_HTML)
        self.assertIn("flex-direction: column;", BOARD_WEB_APP_HTML)
        self.assertIn("data-client-vehicle-edit", BOARD_WEB_APP_HTML)
        self.assertIn("data-client-vehicle-delete", BOARD_WEB_APP_HTML)
        self.assertIn('data-client-vehicle-field="vin"', BOARD_WEB_APP_HTML)
        self.assertIn("'/api/delete_client_vehicle'", BOARD_WEB_APP_HTML)
        self.assertIn("sync_linked_cards: true", BOARD_WEB_APP_HTML)
        self.assertIn("client-match-item__vehicles", BOARD_WEB_APP_HTML)
        self.assertIn("НАЙДЕННЫЕ КЛИЕНТЫ И АВТОМОБИЛИ", BOARD_WEB_APP_HTML)
        self.assertIn("data-select-client-vehicle", BOARD_WEB_APP_HTML)
        self.assertIn("data-select-client-new-vehicle", BOARD_WEB_APP_HTML)
        self.assertIn("data-load-client-vehicles", BOARD_WEB_APP_HTML)
        self.assertIn("async function createClientFromCardSuggestion()", BOARD_WEB_APP_HTML)
        self.assertIn('id="cardClientCreateModal"', BOARD_WEB_APP_HTML)
        self.assertIn('data-open-card-client-create="true"', BOARD_WEB_APP_HTML)
        self.assertIn('class="vehicle-client-row"', BOARD_WEB_APP_HTML)
        self.assertIn("if (field.name === 'customer_name') {", BOARD_WEB_APP_HTML)
        self.assertIn('id="cardClientCreateNameInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cardClientCreatePhoneFields"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cardClientCreateVehicleInput"', BOARD_WEB_APP_HTML)
        self.assertIn("function openCardClientCreateModal()", BOARD_WEB_APP_HTML)
        self.assertIn("async function saveCardClientFromPopup()", BOARD_WEB_APP_HTML)
        self.assertIn("async function createClientForCard(profile, payload,", BOARD_WEB_APP_HTML)
        self.assertIn(
            "await createClientForCard(profile, payload, { createVehicleFromCard });",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "pushModal('card-client-create', els.cardClientCreateModal, { parentKey: 'card' });",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "'<div class=\"vehicle-field__label\"><span>' + escapeHtml(field.label) + '</span>' + copyButton + '</div>'",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("copyButton + createClientButton", BOARD_WEB_APP_HTML)
        self.assertIn("await saveCardClientFromPopup();", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/create_client'", BOARD_WEB_APP_HTML)
        self.assertIn("vehiclePayloadFromProfile(profile)", BOARD_WEB_APP_HTML)
        self.assertIn("await createClientFromCardSuggestion();", BOARD_WEB_APP_HTML)
        self.assertIn("return data;", BOARD_WEB_APP_HTML)
        self.assertIn("pendingCardClientVehicleId", BOARD_WEB_APP_HTML)
        self.assertIn("pendingCreateClientVehicleFromCard", BOARD_WEB_APP_HTML)
        self.assertIn("pendingCardClientId", BOARD_WEB_APP_HTML)
        self.assertNotIn("clientProfileMeta", BOARD_WEB_APP_HTML)
        self.assertIn(".clients-list-pane {", BOARD_WEB_APP_HTML)
        self.assertIn("width: min(77vw, 1680px);", BOARD_WEB_APP_HTML)
        self.assertIn("height: min(74vh, 944px);", BOARD_WEB_APP_HTML)
        self.assertNotIn("width: min(96vw, 2100px);", BOARD_WEB_APP_HTML)
        self.assertNotIn("height: min(92vh, 1180px);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "grid-template-columns: minmax(420px, 40%) minmax(0, 1fr);", BOARD_WEB_APP_HTML
        )
        self.assertIn("grid-template-rows: auto minmax(0, 1fr);", BOARD_WEB_APP_HTML)
        self.assertIn("overflow: hidden;", BOARD_WEB_APP_HTML)
        self.assertIn("overflow: auto;", BOARD_WEB_APP_HTML)
        self.assertIn("display: flex;", BOARD_WEB_APP_HTML)
        self.assertIn("flex: 1 1 auto;", BOARD_WEB_APP_HTML)
        client_row_chips_fragment = BOARD_WEB_APP_HTML.split(
            "function clientRowChipsHtml(client)", 1
        )[1].split("function clientDebtAmountText", 1)[0]
        self.assertIn("function clientMetaLine(client)", BOARD_WEB_APP_HTML)
        self.assertIn("clientMetaLine(client)", BOARD_WEB_APP_HTML)
        self.assertNotIn("compactPhoneLine(client, '')", client_row_chips_fragment)
        self.assertIn("'ЗН: ' + stats.repair_orders_total", client_row_chips_fragment)
        self.assertIn("'АВТО: ' + stats.vehicles_total", client_row_chips_fragment)
        self.assertIn("'ПОСЛЕДНИЙ: ' + formatDate(stats.last_visit)", client_row_chips_fragment)
        self.assertIn(
            "clientProfileTitle) els.clientProfileTitle.textContent = clientDisplayName(client);",
            BOARD_WEB_APP_HTML,
        )
        client_type_badge_rule = re.search(
            r"\.client-type-badge \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(client_type_badge_rule)
        assert client_type_badge_rule is not None
        self.assertIn("font-size: 11.5px;", client_type_badge_rule.group("body"))
        self.assertIn("min-width: 68px;", client_type_badge_rule.group("body"))
        client_profile_phone_rule = re.search(
            r"\.client-profile-phone \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(client_profile_phone_rule)
        assert client_profile_phone_rule is not None
        self.assertIn("font-size: 22px;", client_profile_phone_rule.group("body"))
        self.assertIn("color: #eef5d7;", client_profile_phone_rule.group("body"))
        client_comment_rule = re.search(
            r"#clientCommentInput \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(client_comment_rule)
        assert client_comment_rule is not None
        self.assertIn("min-height: 72px;", client_comment_rule.group("body"))
        self.assertIn("height: 72px;", client_comment_rule.group("body"))
        self.assertIn('id="clientRequisitesDetails"', BOARD_WEB_APP_HTML)
        self.assertIn('id="clientMatchPanel"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "id=\"' + inputId + '\" data-client-phone-input=\"' + index + '\"", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "id=\"' + inputId + '\" data-client-email-input=\"' + index + '\"", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            'input[type="text"], input[type="password"], input[type="email"], input[type="search"]',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('.field--compact input[type="email"],', BOARD_WEB_APP_HTML)
        self.assertIn("email: emails[0] || '',", BOARD_WEB_APP_HTML)
        self.assertIn("emails,", BOARD_WEB_APP_HTML)
        self.assertIn("clients-field--email", BOARD_WEB_APP_HTML)
        self.assertIn("justify-self: start;", BOARD_WEB_APP_HTML)
        self.assertIn("width: min(100%, 360px);", BOARD_WEB_APP_HTML)
        self.assertIn("align-self: start;", BOARD_WEB_APP_HTML)
        self.assertIn(
            'id="clientLegalNameInput" type="text" maxlength="160" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("async function openClientsModal()", BOARD_WEB_APP_HTML)
        self.assertIn("async function linkActiveCardToClient(clientId,", BOARD_WEB_APP_HTML)
        self.assertIn("async function loadClientSuggestionVehicles(clientId)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const fullProfileLoaded = Array.isArray(state.clientSuggestionProfiles?.[client?.id]?.vehicles);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const visibleVehicles = fullProfileLoaded ? vehicles : vehicles.slice(0, 3);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const loadMore = !fullProfileLoaded && total > visibleVehicles.length",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const loadedProfiles = state.clientSuggestionProfiles || {};", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "if (clientId && loadedProfiles[clientId]) profiles[clientId] = loadedProfiles[clientId];",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("window.clearTimeout(state.clientSuggestTimer);", BOARD_WEB_APP_HTML)
        self.assertIn("state.clientSuggestTimer = null;", BOARD_WEB_APP_HTML)
        self.assertIn("function clientSuggestionQueryCandidates(profile)", BOARD_WEB_APP_HTML)
        self.assertIn("add('client-name', profile.customer_name);", BOARD_WEB_APP_HTML)
        self.assertIn("add('client-phone', profile.customer_phone);", BOARD_WEB_APP_HTML)
        self.assertIn("profile.display_name,", BOARD_WEB_APP_HTML)
        self.assertIn("function mergeClientSuggestionResults(groups)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const suggestionGroups = await Promise.all(queryCandidates.map(async (candidate) => {",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('class="client-match-panel__head"', BOARD_WEB_APP_HTML)
        self.assertIn('data-close-client-suggestions="true"', BOARD_WEB_APP_HTML)
        self.assertIn(".client-match-panel.is-visible {", BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr);", BOARD_WEB_APP_HTML)
        self.assertIn("max-height: min(42vh, 360px);", BOARD_WEB_APP_HTML)
        self.assertIn(".client-match-list {\n      display: flex;", BOARD_WEB_APP_HTML)
        self.assertIn("overflow-y: auto;", BOARD_WEB_APP_HTML)
        self.assertIn("target.closest('[data-close-client-suggestions]')", BOARD_WEB_APP_HTML)
        self.assertIn(
            "target.closest('[data-client-suggestion], [data-select-client-suggestion]')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("window.setTimeout(refreshClientSuggestionsForCard, 450)", BOARD_WEB_APP_HTML)
        self.assertIn("function clientSuggestionVehicleKey(vehicle)", BOARD_WEB_APP_HTML)
        self.assertIn("return cardId ? ('card:' + cardId) : '';", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function findClientSuggestionVehicle(vehicles, vehicleKey = '')", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "async function ensureStableClientSuggestionVehicle(clientId, vehicle)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("'/api/upsert_client_vehicle'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "selectedVehicle = await ensureStableClientSuggestionVehicle(clientId, selectedVehicle);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("'/api/link_card_to_client'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/search_clients?query='", BOARD_WEB_APP_HTML)
        self.assertIn("client_vehicle_id: state.pendingCardClientVehicleId", BOARD_WEB_APP_HTML)

    def test_vehicle_panel_uses_larger_readable_typography(self) -> None:
        self.assertIn(".vehicle-panel__summary {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 11px;", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-group__title {", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-group--identity .vehicle-group__grid {", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-field input,", BOARD_WEB_APP_HTML)
        self.assertIn("height: 32px;", BOARD_WEB_APP_HTML)
        self.assertIn("min-height: 32px;", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-field__label label,", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-copy {", BOARD_WEB_APP_HTML)

    def test_employees_modal_keeps_both_panes_inside_the_layout(self) -> None:
        start = BOARD_WEB_APP_HTML.index("+ '<div class=\"dialog__body-scroll employees-layout\">'")
        end = BOARD_WEB_APP_HTML.index('+ \'<div class="modal" id="employeeSalaryModal">\'')
        fragment = BOARD_WEB_APP_HTML[start:end]
        html = "".join(re.findall(r"\+\s*'([^']*)'", fragment))

        parser = _EmployeesLayoutParser()
        parser.feed(html)

        self.assertEqual(2, len(parser.layout_children))
        self.assertEqual("employees-pane employees-pane--list", parser.layout_children[0])
        self.assertEqual("employees-pane", parser.layout_children[1])

    def test_cards_use_stepwise_deadline_heat_variables(self) -> None:
        self.assertIn("--deadline-heat-border:", BOARD_WEB_APP_HTML)
        self.assertIn("--deadline-heat-ring:", BOARD_WEB_APP_HTML)
        self.assertIn("--deadline-heat-glow:", BOARD_WEB_APP_HTML)
        self.assertIn("data-deadline-bucket", BOARD_WEB_APP_HTML)
        self.assertIn("data-deadline-step", BOARD_WEB_APP_HTML)
        self.assertIn("cards.map(renderBoardCardHtml).join('')", BOARD_WEB_APP_HTML)

    def test_cards_stack_vehicle_above_short_essence(self) -> None:
        self.assertIn(".card__heading {", BOARD_WEB_APP_HTML)
        self.assertIn("display: grid;", BOARD_WEB_APP_HTML)
        vehicle_rule = re.search(
            r"\.card__vehicle\s*\{(?P<body>.*?)\n    \}", BOARD_WEB_APP_HTML, re.S
        )
        self.assertIsNotNone(vehicle_rule)
        assert vehicle_rule is not None
        self.assertIn("font-size: calc(16px * var(--board-scale));", vehicle_rule.group("body"))
        self.assertIn("font-weight: 700;", vehicle_rule.group("body"))
        self.assertNotIn("font-size: calc(15px * var(--board-scale));", vehicle_rule.group("body"))
        self.assertNotIn("font-weight: 800;", vehicle_rule.group("body"))
        self.assertIn("font-size: calc(14px * var(--board-scale));", BOARD_WEB_APP_HTML)
        self.assertIn("color: #373227;", BOARD_WEB_APP_HTML)
        self.assertIn("color: #454034;", BOARD_WEB_APP_HTML)
        self.assertIn("function buildCardHeadingHtml(card)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "return '<div class=\"card__heading\"><div class=\"card__vehicle\">' + escapeHtml(vehicle) + '</div><div class=\"card__title\">' + escapeHtml(title) + '</div></div>';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function renderBoardCardHtml(card)", BOARD_WEB_APP_HTML)
        self.assertIn("cards.map(renderBoardCardHtml).join('')", BOARD_WEB_APP_HTML)

    def test_card_timer_panel_uses_compact_stepper_fields(self) -> None:
        self.assertIn('<div class="panel-title">ТАЙМЕР</div>', BOARD_WEB_APP_HTML)
        self.assertIn(".signal-grid--timer {", BOARD_WEB_APP_HTML)
        self.assertIn(
            ".signal-grid--timer > .signal-cell:not(.signal-cell--timer) {", BOARD_WEB_APP_HTML
        )
        self.assertIn(".signal-stepper {", BOARD_WEB_APP_HTML)
        self.assertIn(".signal-stepper__button {", BOARD_WEB_APP_HTML)
        self.assertIn(".signal-input--hidden {", BOARD_WEB_APP_HTML)
        self.assertIn('class="signal-grid signal-grid--timer"', BOARD_WEB_APP_HTML)
        self.assertIn('class="signal-cell signal-cell--timer"', BOARD_WEB_APP_HTML)
        self.assertIn('id="signalDaysDecrementButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="signalDaysIncrementButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="signalHoursDecrementButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="signalHoursIncrementButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="signalDaysValue" for="signalDays">1 Д</output>', BOARD_WEB_APP_HTML)
        self.assertIn('id="signalHoursValue" for="signalHours">0 Ч</output>', BOARD_WEB_APP_HTML)
        self.assertIn('role="group" aria-label="Дни"', BOARD_WEB_APP_HTML)
        self.assertIn('role="group" aria-label="Часы"', BOARD_WEB_APP_HTML)
        self.assertIn(">&minus;</button>", BOARD_WEB_APP_HTML)
        self.assertIn(">+</button>", BOARD_WEB_APP_HTML)
        self.assertIn('id="signalDays" type="number" min="0" max="365"', BOARD_WEB_APP_HTML)
        self.assertIn('id="signalHours" type="number" min="0" max="23"', BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-columns: minmax(24px, 0.72fr)", BOARD_WEB_APP_HTML)
        self.assertIn(".signal-stepper__value {", BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-rows: 13px 25px 11px 25px 24px;", BOARD_WEB_APP_HTML)
        self.assertIn('.signal-actions[data-layout="split"] {', BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.signalActions.dataset.layout = splitActions ? 'split' : 'single';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function timerRemainingToMarkup(total)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "? timerRemainingToMarkup(timerRemainingSeconds(state.activeCard))", BOARD_WEB_APP_HTML
        )
        self.assertIn("els.signalDaysValue.value = days + ' Д';", BOARD_WEB_APP_HTML)
        self.assertIn("els.signalHoursValue.value = hours + ' Ч';", BOARD_WEB_APP_HTML)

    def test_card_preview_clamps_to_five_description_lines(self) -> None:
        self.assertIn(".card__desc {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: calc(13px * var(--board-scale));", BOARD_WEB_APP_HTML)
        self.assertIn("-webkit-line-clamp: 5;", BOARD_WEB_APP_HTML)

    def test_card_preview_uses_readable_russian_meta_labels(self) -> None:
        self.assertIn("Описание не указано", BOARD_WEB_APP_HTML)
        self.assertNotIn("СИГН", BOARD_WEB_APP_HTML)
        self.assertIn(".card__footer {", BOARD_WEB_APP_HTML)
        self.assertIn('class="card__footer"', BOARD_WEB_APP_HTML)
        self.assertIn("ФАЙЛЫ ", BOARD_WEB_APP_HTML)
        self.assertIn("ЖУРНАЛ ", BOARD_WEB_APP_HTML)

    def test_card_preview_omits_empty_tag_placeholder(self) -> None:
        self.assertNotIn("БЕЗ МЕТОК", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const tagsHtml = previewTags.length",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ": '';",
            BOARD_WEB_APP_HTML,
        )

    def test_empty_board_columns_render_without_placeholder_text(self) -> None:
        self.assertNotIn("ЗДЕСЬ ПОКА ПУСТО.", BOARD_WEB_APP_HTML)
        self.assertIn(
            "cards.length ? cards.map(renderBoardCardHtml).join('') : ''",
            BOARD_WEB_APP_HTML,
        )

    def test_board_card_preview_keeps_signal_lamp_without_timer_value(self) -> None:
        self.assertIn(
            '<div class="card__signal"><span class="lamp" data-indicator="',
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("card__signal-value", BOARD_WEB_APP_HTML)
        self.assertNotIn("durationToMarkup(card.remaining_seconds, false)", BOARD_WEB_APP_HTML)

    def test_red_deadline_indicator_pulses_subtly(self) -> None:
        self.assertIn('.lamp[data-indicator="red"] {', BOARD_WEB_APP_HTML)
        self.assertIn("animation: lamp-red-pulse 1.8s ease-in-out infinite;", BOARD_WEB_APP_HTML)
        self.assertIn("@keyframes lamp-red-pulse {", BOARD_WEB_APP_HTML)
        self.assertIn("@media (prefers-reduced-motion: reduce) {", BOARD_WEB_APP_HTML)

    def test_unread_cards_expose_corner_badge_and_hover_seen_flow(self) -> None:
        self.assertEqual(BOARD_WEB_APP_HTML.count("function cardUnreadBadgeHtml(card)"), 1)
        self.assertEqual(BOARD_WEB_APP_HTML.count("function renderBoardCardHtml(card)"), 1)
        self.assertIn(".card__unread-badge {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "data-unread=\"' + (card.is_unread ? 'true' : 'false') + '\"", BOARD_WEB_APP_HTML
        )
        self.assertIn('title="Не прочитано"', BOARD_WEB_APP_HTML)
        self.assertIn("const CARD_UNREAD_HOVER_DELAY_MS = 260;", BOARD_WEB_APP_HTML)
        self.assertIn("await api('/api/mark_card_seen'", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCardSeenPointerOver(event)", BOARD_WEB_APP_HTML)
        self.assertIn("scheduleCardSeen(card.dataset.cardId);", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCardSeenPointerOut(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "document.addEventListener('pointerover', handleCardSeenPointerOver);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "document.addEventListener('pointerout', handleCardSeenPointerOut);", BOARD_WEB_APP_HTML
        )

    def test_mobile_card_creation_uses_valid_ui_audit_source(self) -> None:
        self.assertNotIn("mobile-ui", BOARD_WEB_APP_HTML)
        self.assertIn("source: 'ui'", BOARD_WEB_APP_HTML)

    def test_web_assets_do_not_ship_dead_legacy_shadow_helpers(self) -> None:
        stale_helpers = [
            "legacyRenderRepairOrderRowsExpandedShadow",
            "legacyRepairOrdersMetaTextExpandedShadow",
            "legacySaveCardShadow",
            "legacyRefreshVehiclePanelShadow",
            "legacyCardHtmlBase",
            "legacyRenderCardHtmlBase",
            "legacyCardHtmlShadow",
            "legacyRenderCardHtmlShadow",
        ]
        for helper_name in stale_helpers:
            self.assertNotIn(helper_name, BOARD_WEB_APP_HTML)

    def test_updated_cards_expose_yellow_badge_and_hover_seen_flow(self) -> None:
        self.assertIn(".card__updated-badge {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "data-updated-unseen=\"' + (card.has_unseen_update ? 'true' : 'false') + '\"",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('title="Обновлено"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (!force && currentCard && !currentCard.is_unread && !currentCard.has_unseen_update) return;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "if (!currentCard || (!currentCard.is_unread && !currentCard.has_unseen_update)) return;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const hasUpdatedMarker = card.dataset.updatedUnseen === 'true';", BOARD_WEB_APP_HTML
        )

    def test_updated_badge_is_cleared_optimistically_for_fast_hover_and_open(self) -> None:
        self.assertIn("const CARD_UPDATED_HOVER_DELAY_MS = 80;", BOARD_WEB_APP_HTML)
        self.assertIn("function markCardSeenOptimistically(cardId)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const delayMs = currentCard.has_unseen_update && !currentCard.is_unread ? CARD_UPDATED_HOVER_DELAY_MS : CARD_UNREAD_HOVER_DELAY_MS;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("markCardSeenOptimistically(normalizedCardId);", BOARD_WEB_APP_HTML)
        self.assertIn("cardElement.dataset.updatedUnseen = 'false';", BOARD_WEB_APP_HTML)
        self.assertIn(
            "cardElement.querySelectorAll('.card__unread-badge, .card__updated-badge').forEach((badge) => badge.remove());",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "markCardSeenOptimistically(boardCard.dataset.cardId);",
            BOARD_WEB_APP_HTML,
        )

    def test_seen_badge_suppression_prevents_stale_snapshot_reappearing(self) -> None:
        self.assertIn("const CARD_SEEN_SUPPRESSION_TTL_MS = 60000;", BOARD_WEB_APP_HTML)
        self.assertIn("cardSeenSuppressions: new Map(),", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function recordCardSeenSuppression(cardId, updatedAt = '')", BOARD_WEB_APP_HTML
        )
        self.assertIn("function applyCardSeenSuppression(card)", BOARD_WEB_APP_HTML)
        self.assertIn("function applyCardSeenSuppressionsToSnapshot(snapshot)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const nextSnapshot = applyCardSeenSuppressionsToSnapshot(await api('/api/get_board_snapshot?compact=1&include_archive=0'));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const suppressedNextCards = applyCardSeenSuppressionsToCards(nextCards);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const suppressedNextCard = applyCardSeenSuppression(nextCard);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const suppressedCard = applyCardSeenSuppression(card);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "recordCardSeenSuppression(normalizedCardId, suppressionUpdatedAt);",
            BOARD_WEB_APP_HTML,
        )

    def test_board_drag_drop_supports_reordering_inside_column(self) -> None:
        self.assertIn(".card.is-drop-before::before {", BOARD_WEB_APP_HTML)
        self.assertIn("function updateBoardDragAutoScroll(clientX, clientY)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const edgeThresholdX = Math.max(48, Math.min(96, Math.round(rect.width * 0.12)));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const edgeThresholdY = Math.max(48, Math.min(96, Math.round(rect.height * 0.12)));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "clampBoardScroll(els.boardScroll.scrollLeft + deltaX, els.boardScroll.scrollTop + deltaY);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function resolveDropBeforeCardId(column, clientY, draggedCardId)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function handleBoardCardDragStart(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleBoardCardDragOver(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleBoardCardDragLeave(event)", BOARD_WEB_APP_HTML)
        self.assertIn("async function handleBoardCardDrop(event)", BOARD_WEB_APP_HTML)
        self.assertIn("state.boardDropBeforeCardId = beforeCardId || '';", BOARD_WEB_APP_HTML)
        self.assertIn(
            "updateBoardDragAutoScroll(event.clientX, event.clientY);", BOARD_WEB_APP_HTML
        )
        self.assertIn("before_card_id: beforeCardId || undefined,", BOARD_WEB_APP_HTML)
        self.assertIn("await moveCard(cardId, columnId, beforeCardId);", BOARD_WEB_APP_HTML)
        move_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "async function moveCard(cardId, columnId, beforeCardId = '')"
            ) : BOARD_WEB_APP_HTML.index("async function moveColumn")
        ]
        self.assertIn("clearCardOpenSideEffectTimer();", move_fragment)
        self.assertIn(
            "document.addEventListener('dragstart', handleBoardCardDragStart);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "document.addEventListener('dragover', handleBoardCardDragOver);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "document.addEventListener('dragleave', handleBoardCardDragLeave);", BOARD_WEB_APP_HTML
        )
        self.assertIn("document.addEventListener('drop', handleBoardCardDrop);", BOARD_WEB_APP_HTML)
        self.assertIn("(!state.boardDragCardId && !state.boardDragColumnId)", BOARD_WEB_APP_HTML)
        self.assertIn("left.position ?? 0", BOARD_WEB_APP_HTML)

    def test_card_files_panel_uses_dropzone_and_clipboard_upload_flow(self) -> None:
        self.assertIn('class="file-dropzone" id="fileDropzone"', BOARD_WEB_APP_HTML)
        self.assertIn('contenteditable="plaintext-only"', BOARD_WEB_APP_HTML)
        self.assertIn('id="fileDropMeta"', BOARD_WEB_APP_HTML)
        self.assertIn('id="filePreviewPanel"', BOARD_WEB_APP_HTML)
        self.assertIn('id="filePreviewImage"', BOARD_WEB_APP_HTML)
        self.assertIn('id="filePreviewCloseButton"', BOARD_WEB_APP_HTML)
        self.assertIn("body.is-file-preview-open", BOARD_WEB_APP_HTML)
        self.assertIn("position: fixed;", BOARD_WEB_APP_HTML)
        self.assertIn("0 0 0 9999px rgba(4, 6, 5, 0.68)", BOARD_WEB_APP_HTML)
        self.assertIn(".file-row__thumb", BOARD_WEB_APP_HTML)
        self.assertIn('class="file-row__thumb-image"', BOARD_WEB_APP_HTML)
        self.assertIn('loading="lazy" decoding="async"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "document.body.classList.toggle('is-file-preview-open', isVisible);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            'accept=".png,.jpg,.jpeg,.webp,.gif,.txt,.pdf,.doc,.docx,.xls,.xlsx', BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "const ATTACHMENT_ALLOWED_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif', '.doc', '.docx', '.xls', '.xlsx', '.txt', '.pdf']);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const ATTACHMENT_PREVIEWABLE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif']);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function syncFileDropzone(card = state.activeCard)", BOARD_WEB_APP_HTML)
        self.assertIn("function attachmentDownloadPath(cardId, attachmentId)", BOARD_WEB_APP_HTML)
        self.assertIn("function attachmentExistsOnDisk(attachment)", BOARD_WEB_APP_HTML)
        self.assertIn("function attachmentIsPreviewable(attachment)", BOARD_WEB_APP_HTML)
        self.assertIn("if (!attachmentExistsOnDisk(attachment)) return false;", BOARD_WEB_APP_HTML)
        self.assertIn("ФАЙЛ<br>НЕ НАЙДЕН", BOARD_WEB_APP_HTML)
        self.assertIn("file-row__missing-note", BOARD_WEB_APP_HTML)
        self.assertIn("ФАЙЛ ЕСТЬ В СПИСКЕ, НО ОТСУТСТВУЕТ НА ДИСКЕ СЕРВЕРА.", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function renderAttachmentThumbnailHtml(attachment, downloadUrl)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function clearFilePreview({ sync = true } = {})", BOARD_WEB_APP_HTML)
        self.assertIn("function syncFilePreview(card = state.activeCard)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleFilePreviewKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleAttachmentThumbnailError(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "document.addEventListener('keydown', handleFilePreviewKeydown);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "document.addEventListener('error', handleAttachmentThumbnailError, true);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "async function previewActiveCardAttachment(attachmentId)", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "function requireSavedCardForFiles({ syncDropzone = false } = {})", BOARD_WEB_APP_HTML
        )
        self.assertIn("async function refreshActiveCardFiles()", BOARD_WEB_APP_HTML)
        self.assertIn("async function removeActiveCardAttachment(attachmentId)", BOARD_WEB_APP_HTML)
        self.assertIn("function collectClipboardAttachmentFiles(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function clipboardAttachmentName(prefix, extension)", BOARD_WEB_APP_HTML)
        self.assertIn("function normalizeUploadableAttachmentFile(file)", BOARD_WEB_APP_HTML)
        self.assertIn("function attachmentValidationMessage()", BOARD_WEB_APP_HTML)
        self.assertIn("new File([text], clipboardTextAttachmentName()", BOARD_WEB_APP_HTML)
        self.assertIn("async function uploadProvidedFiles(files)", BOARD_WEB_APP_HTML)
        self.assertIn("function openFilePickerFromDropzone()", BOARD_WEB_APP_HTML)
        self.assertIn("function handleFileDropzoneKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleFileDropzoneDragEnter(event)", BOARD_WEB_APP_HTML)
        self.assertIn("async function handleFileDropzoneDrop(event)", BOARD_WEB_APP_HTML)
        self.assertIn("async function handleFileDropzonePaste(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (!requireSavedCardForFiles({ syncDropzone: true })) return;", BOARD_WEB_APP_HTML
        )
        self.assertIn("await refreshActiveCardFiles();", BOARD_WEB_APP_HTML)
        self.assertIn(
            "await refreshActiveCardFiles();\n"
            "      state.cardJournalLoadedFor = '';\n"
            "      await refreshSnapshot(true);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "await refreshActiveCardFiles();\n"
            "        state.cardJournalLoadedFor = '';\n"
            "        setStatus(normalizedFiles.length > 1 ? 'ФАЙЛЫ ЗАГРУЖЕНЫ.' : 'ФАЙЛ ЗАГРУЖЕН.', false);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "await previewActiveCardAttachment(previewFileTarget.dataset.previewFile);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "await removeActiveCardAttachment(target.dataset.removeFile);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.fileInput.addEventListener('change', () => uploadProvidedFiles(els.fileInput.files));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.fileDropzone.addEventListener('click', openFilePickerFromDropzone);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.fileDropzone.addEventListener('drop', handleFileDropzoneDrop);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.fileDropzone.addEventListener('paste', handleFileDropzonePaste);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const normalizedFiles = selectedFiles.map((file) => normalizeUploadableAttachmentFile(file));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const attachmentLink = target.closest('a[href*=\"/api/attachment\"]');",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('data-preview-file="', BOARD_WEB_APP_HTML)
        self.assertIn(
            "const previewFileTarget = target.closest('[data-preview-file]');", BOARD_WEB_APP_HTML
        )
        self.assertIn("if (previewFileTarget && state.editingId) {", BOARD_WEB_APP_HTML)
        self.assertIn("if (target.dataset.closeFilePreview) {", BOARD_WEB_APP_HTML)
        self.assertIn("if (target.dataset.removeFile && state.editingId) {", BOARD_WEB_APP_HTML)

    def test_operator_ui_exposes_login_profile_and_admin_routes(self) -> None:
        self.assertNotIn("const ACTOR_STORAGE_KEY = 'kanban-actor';", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const OPERATOR_SESSION_STORAGE_KEY = 'kanban-operator-session';", BOARD_WEB_APP_HTML
        )
        self.assertEqual(BOARD_WEB_APP_HTML.count("function ensureActor()"), 1)
        self.assertNotIn("localStorage.setItem(ACTOR_STORAGE_KEY", BOARD_WEB_APP_HTML)
        self.assertNotIn("localStorage.removeItem(ACTOR_STORAGE_KEY", BOARD_WEB_APP_HTML)
        self.assertNotIn("Legacy pre-session operator", BOARD_WEB_APP_HTML)
        self.assertNotIn("legacy-operator-unused", BOARD_WEB_APP_HTML)
        self.assertNotIn("sessionStorage.setItem", BOARD_WEB_APP_HTML)
        self.assertIn("'X-Operator-Session'", BOARD_WEB_APP_HTML)
        self.assertIn('id="operatorProfileModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="operatorAdminModal"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="operatorSecurityWarning"', BOARD_WEB_APP_HTML)
        self.assertNotIn(".operator-security-warning {", BOARD_WEB_APP_HTML)
        self.assertNotIn("operatorSecurityWarning", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "const securityWarning = data?.security?.warning || '';", BOARD_WEB_APP_HTML
        )
        self.assertIn('id="identityPassword"', BOARD_WEB_APP_HTML)
        self.assertIn('id="adminUserLogin"', BOARD_WEB_APP_HTML)
        self.assertIn('id="adminUserPassword"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="adminUserRole"', BOARD_WEB_APP_HTML)
        self.assertNotIn("role: els.adminUserRole.value", BOARD_WEB_APP_HTML)
        self.assertNotIn("els.adminUserRole.value = 'operator';", BOARD_WEB_APP_HTML)
        self.assertIn(
            "Администратор создает пользователя или обновляет ему пароль.", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            'input[type="text"], input[type="password"], input[type="email"], input[type="search"], input[type="month"], textarea, select, input[type="number"]',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("color-scheme: dark;", BOARD_WEB_APP_HTML)
        self.assertIn('.field--compact input[type="password"]', BOARD_WEB_APP_HTML)
        self.assertIn("input:-webkit-autofill", BOARD_WEB_APP_HTML)
        self.assertIn("async function loginOperator()", BOARD_WEB_APP_HTML)
        self.assertIn("function handleIdentityInputKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleIdentityPasswordKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertIn("async function loadOperatorProfile(openModal = false)", BOARD_WEB_APP_HTML)
        self.assertIn("async function openOperatorWorkspace()", BOARD_WEB_APP_HTML)
        self.assertIn("async function openOperatorAdminModal()", BOARD_WEB_APP_HTML)
        self.assertIn("async function saveOperatorUser()", BOARD_WEB_APP_HTML)
        self.assertIn("async function saveOperatorEmployeeBinding", BOARD_WEB_APP_HTML)
        self.assertIn(
            "closeOperatorEmployeeBinding();\n        await refreshOperatorAdminSurfaces({",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function closeOperatorAdminChildView()", BOARD_WEB_APP_HTML)
        self.assertIn("async function deleteOperatorUser(username)", BOARD_WEB_APP_HTML)
        self.assertIn("async function openOperatorUserReport(username)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleAdminUsersListClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.operatorButton.addEventListener('click', openOperatorWorkspace);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.identityInput.addEventListener('keydown', handleIdentityInputKeydown);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.identityInput.addEventListener('input', handleIdentityCredentialInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.identityPassword.addEventListener('input', handleIdentityCredentialInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.adminUsersList.addEventListener('click', handleAdminUsersListClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("'/api/login_operator'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/get_operator_profile'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/list_operator_users'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/set_operator_user_employee'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/get_operator_user_report?username='", BOARD_WEB_APP_HTML)
        self.assertIn("data-open-operator-report", BOARD_WEB_APP_HTML)
        self.assertIn("data-bind-operator-employee", BOARD_WEB_APP_HTML)
        self.assertIn('id="operatorAdminCloseButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="operatorUserEmployeeBindingPanel"', BOARD_WEB_APP_HTML)
        self.assertIn('id="operatorUserEditorPanel"', BOARD_WEB_APP_HTML)
        self.assertIn('id="operatorUsersListPanel"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.operatorAdminCloseButton.textContent = isNested ? 'НАЗАД' : 'ЗАКРЫТЬ';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("if (closeOperatorAdminChildView()) return false;", BOARD_WEB_APP_HTML)
        self.assertIn("СОТРУДНИК: НЕ ПРИВЯЗАН", BOARD_WEB_APP_HTML)
        self.assertIn("СТАТИСТИКА: 15 ДНЕЙ", BOARD_WEB_APP_HTML)
        self.assertIn("bootstrapOperatorSession();", BOARD_WEB_APP_HTML)

    def test_operator_admin_opens_only_users_module(self) -> None:
        self.assertIn('id="operatorAdminUsersPanel"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="operator-admin-secondary operator-admin-tab-panel is-active"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("setOperatorAdminTab('users');", BOARD_WEB_APP_HTML)
        self.assertIn("refreshOperatorAdminSurfaces({ openAdminModal: true });", BOARD_WEB_APP_HTML)
        self.assertNotIn('id="operatorAdminTabs"', BOARD_WEB_APP_HTML)
        self.assertNotIn('data-operator-admin-tab="journal"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="operatorAdminJournalPanel"', BOARD_WEB_APP_HTML)

    def test_tag_editor_exposes_minimal_color_picker(self) -> None:
        self.assertIn('class="tag-color-picker" id="tagColorPicker"', BOARD_WEB_APP_HTML)
        self.assertIn(".tag-color-option {", BOARD_WEB_APP_HTML)
        self.assertIn("data-tag-color-choice", BOARD_WEB_APP_HTML)
        self.assertIn("draftTagColor: 'green'", BOARD_WEB_APP_HTML)
        self.assertIn("normalizeTagColor(", BOARD_WEB_APP_HTML)
        self.assertIn("function handleTagInputKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.tagInput.addEventListener('keydown', handleTagInputKeydown);", BOARD_WEB_APP_HTML
        )

    def test_tag_editor_shows_updated_suggested_tags(self) -> None:
        self.assertIn("{ label: 'ждет очереди', color: 'green' }", BOARD_WEB_APP_HTML)
        self.assertIn("{ label: 'В работе', color: 'yellow' }", BOARD_WEB_APP_HTML)
        self.assertIn("{ label: 'надо что то сделать', color: 'red' }", BOARD_WEB_APP_HTML)
        self.assertNotIn("{ label: 'ЗАКАЗАТЬ', color: 'green' }", BOARD_WEB_APP_HTML)

    def test_tag_editor_limits_cards_to_three_tags(self) -> None:
        self.assertIn("const CARD_TAG_LIMIT = 3;", BOARD_WEB_APP_HTML)
        self.assertIn("slice(0, CARD_TAG_LIMIT)", BOARD_WEB_APP_HTML)
        self.assertIn("НА КАРТОЧКЕ МОЖЕТ БЫТЬ НЕ БОЛЕЕ 3 МЕТОК.", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.tagMeta.textContent = state.draftTags.length + ' / ' + CARD_TAG_LIMIT;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("els.tagInput.disabled = atLimit;", BOARD_WEB_APP_HTML)
        self.assertIn("els.tagAddButton.disabled = atLimit;", BOARD_WEB_APP_HTML)
        self.assertIn("const disabledClass = disabled ? ' is-disabled' : '';", BOARD_WEB_APP_HTML)

    def test_green_tags_use_high_contrast_palette(self) -> None:
        self.assertIn("border-color: rgba(67, 126, 79, 0.82);", BOARD_WEB_APP_HTML)
        self.assertIn("background: rgba(111, 173, 116, 0.52);", BOARD_WEB_APP_HTML)
        self.assertIn("color: #1d1a14;", BOARD_WEB_APP_HTML)
        self.assertIn(
            '.tag-color-option[data-tag-color="green"] { color: #3f8b52; }', BOARD_WEB_APP_HTML
        )
        self.assertIn("border-color: rgba(67, 126, 79, 0.62);", BOARD_WEB_APP_HTML)
        self.assertIn("background: rgba(111, 173, 116, 0.22);", BOARD_WEB_APP_HTML)

    def test_repair_order_modal_exposes_minimal_form_and_print_flow(self) -> None:
        self.assertIn(
            'id="repairOrderButton" data-open-repair-order-modal="true"', BOARD_WEB_APP_HTML
        )
        self.assertIn('id="repairOrderModal"', BOARD_WEB_APP_HTML)
        self.assertIn("#repairOrderModal {", BOARD_WEB_APP_HTML)
        self.assertIn("z-index: 14;", BOARD_WEB_APP_HTML)
        self.assertIn("width: min(1320px, calc(100% - 16px));", BOARD_WEB_APP_HTML)
        self.assertIn("[data-open-repair-order-modal]", BOARD_WEB_APP_HTML)
        self.assertIn("openRepairOrderModal();", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/get_repair_order'", BOARD_WEB_APP_HTML)
        self.assertIn(
            'id="repairOrderNumber" class="repair-order-number-display"',
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn('<input id="repairOrderNumber"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.repairOrderNumber.textContent = normalized.number || '-';", BOARD_WEB_APP_HTML
        )
        self.assertNotIn("number: els.repairOrderNumber.value", BOARD_WEB_APP_HTML)
        self.assertIn(
            "pushModal('repair-order', els.repairOrderModal, { parentKey: state.repairOrderParentLayer || '' });",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function renderRepairOrderRows(section, rows, { syncTotals = true } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("renderRepairOrderPayments({ syncTotals: false });", BOARD_WEB_APP_HTML)
        self.assertIn(
            "renderRepairOrderRows('works', normalized.works, { syncTotals: false });",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "renderRepairOrderRows('materials', normalized.materials, { syncTotals: false });",
            BOARD_WEB_APP_HTML,
        )
        self.assertLess(
            BOARD_WEB_APP_HTML.index(
                "pushModal('repair-order', els.repairOrderModal, { parentKey: state.repairOrderParentLayer || '' });"
            ),
            BOARD_WEB_APP_HTML.index("const employeesRequest = loadEmployeesReference();"),
        )
        self.assertNotIn('id="repairOrderEntryNote"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderDate"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-field="date" type="text"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-section="document"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-section="client"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-section="vehicle"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "repair-order-card__grid repair-order-card__grid--document", BOARD_WEB_APP_HTML
        )
        self.assertIn("repair-order-card__grid repair-order-card__grid--client", BOARD_WEB_APP_HTML)
        self.assertIn(
            "repair-order-card__grid repair-order-card__grid--vehicle", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "grid-template-columns: minmax(168px, 0.56fr) minmax(324px, 1.08fr) minmax(520px, 1.78fr);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("padding: 11px 13px 8px;", BOARD_WEB_APP_HTML)
        self.assertIn("padding: 7px 9px 9px;", BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderClient"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderPhone"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderVehicle"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('id="repairOrderComment"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="repairOrderModalNote"', BOARD_WEB_APP_HTML)
        self.assertNotIn('<div class="dialog__title-prefix">ЗАКАЗ-НАРЯД</div>', BOARD_WEB_APP_HTML)
        self.assertNotIn('<label for="repairOrderClient">КЛИЕНТ</label>', BOARD_WEB_APP_HTML)
        self.assertNotIn('<label for="repairOrderVehicle">АВТОМОБИЛЬ</label>', BOARD_WEB_APP_HTML)
        self.assertNotIn(
            '<label for="repairOrderComment">ИНФОРМАЦИЯ ДЛЯ КЛИЕНТА</label>', BOARD_WEB_APP_HTML
        )
        self.assertIn(".repair-order-client-info textarea {", BOARD_WEB_APP_HTML)
        self.assertIn("min-height: 70px;", BOARD_WEB_APP_HTML)
        self.assertIn("height: 70px;", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 14.5px;", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 14.75px;", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 15.25px;", BOARD_WEB_APP_HTML)
        self.assertIn('.repair-order-field--phone input[type="text"] {', BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 15px;", BOARD_WEB_APP_HTML)
        self.assertIn("font-weight: 700;", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-cell-total {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 18px;", BOARD_WEB_APP_HTML)
        self.assertIn("repairOrderFormatRubles", BOARD_WEB_APP_HTML)
        self.assertIn("minimumFractionDigits: 0,", BOARD_WEB_APP_HTML)
        self.assertIn("+ ' ₽';", BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderAddWorkRowButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderAddMaterialRowButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="repairOrderAutofillButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn("els.repairOrderAutofillButton", BOARD_WEB_APP_HTML)
        self.assertNotIn("autofillRepairOrder", BOARD_WEB_APP_HTML)
        self.assertNotIn(".repair-order-headline-actions", BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderPrintButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderTagList"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderTagInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderTagAddButton"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-total="works"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-total="materials"', BOARD_WEB_APP_HTML)
        self.assertIn("function operatorDefaultMaterialExecutor()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const defaults = section === 'materials' ? operatorDefaultMaterialExecutor() : {};",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('id="repairOrderPaymentMethod"', BOARD_WEB_APP_HTML)
        self.assertIn('<option value="card">На карту</option>', BOARD_WEB_APP_HTML)
        self.assertIn('class="repair-order-hidden-fields"', BOARD_WEB_APP_HTML)
        self.assertIn("document.getElementById('repairOrderPaymentsButton')", BOARD_WEB_APP_HTML)
        self.assertIn("document.getElementById('repairOrderPaymentsModal')", BOARD_WEB_APP_HTML)
        self.assertIn("document.getElementById('repairOrderPaymentsMeta')", BOARD_WEB_APP_HTML)
        self.assertIn("document.getElementById('repairOrderPaymentsList')", BOARD_WEB_APP_HTML)
        self.assertIn("button.textContent = '₽';", BOARD_WEB_APP_HTML)
        self.assertNotIn('id="repairOrderPaymentsMethod"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderPaymentCashbox"', BOARD_WEB_APP_HTML)
        self.assertIn("Кат. №", BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-total="subtotal"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-total="cashless_due"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-total="cash_due"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-total-block="taxes"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-total="taxes"', BOARD_WEB_APP_HTML)
        self.assertIn("ИТОГО ПО НАРЯДУ", BOARD_WEB_APP_HTML)
        self.assertNotIn("Итого по наряду", BOARD_WEB_APP_HTML)
        self.assertIn("К ДОПЛАТЕ БЕЗНАЛ", BOARD_WEB_APP_HTML)
        self.assertIn("К ДОПЛАТЕ НАЛ", BOARD_WEB_APP_HTML)
        self.assertIn("grid-auto-columns: 148px;", BOARD_WEB_APP_HTML)
        self.assertIn("min-height: 46px;", BOARD_WEB_APP_HTML)
        self.assertIn("justify-items: center;", BOARD_WEB_APP_HTML)
        self.assertIn("Артикул / OEM", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-table__input {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 14.25px;", BOARD_WEB_APP_HTML)
        self.assertIn('[data-repair-order-cell="name"]', BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 17px;", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-table__select {", BOARD_WEB_APP_HTML)
        self.assertIn("height: 38px;", BOARD_WEB_APP_HTML)
        self.assertIn("min-height: 38px;", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 10.75px;", BOARD_WEB_APP_HTML)
        self.assertIn("body.is-mobile-lite .repair-order-table {", BOARD_WEB_APP_HTML)
        self.assertIn("min-width: 760px;", BOARD_WEB_APP_HTML)
        self.assertIn("body.is-mobile-lite .repair-order-table__select {", BOARD_WEB_APP_HTML)
        self.assertIn("min-width: 138px;", BOARD_WEB_APP_HTML)
        self.assertIn('data-add-repair-order-row="works"', BOARD_WEB_APP_HTML)
        self.assertIn('data-add-repair-order-row="materials"', BOARD_WEB_APP_HTML)
        self.assertIn("function currentRepairOrderDateTime()", BOARD_WEB_APP_HTML)
        self.assertIn("function normalizeRepairOrder(", BOARD_WEB_APP_HTML)
        self.assertIn("function normalizeRepairOrderPaymentMethod(value)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function repairOrderPaymentMethodFromCashboxName(value, fallback = 'cash')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("return 'card';", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function repairOrderPaymentMethodFromPayments(payments, fallback = 'cash')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function normalizeRepairOrderPayment(payment, fallbackId = '')", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "function normalizeRepairOrderPayments(payments, legacyPrepayment = '', defaultPaidAt = '')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function repairOrderPaymentsTotalValue(payments)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function repairOrderPaymentsValueByMethod(payments, method)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function repairOrderCashPaymentsValue(payments)", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderCardPaymentsValue(payments)", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderTaxRate(value)", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderCashlessGrossValue(netAmount)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function repairOrderProjectedTaxesValue(subtotal, paymentMethod)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function repairOrderRowsTotalValue(", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const REPAIR_ORDER_ROW_TOTAL_ROUNDING_TOLERANCE = 0.011;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function repairOrderResolvedRowTotalValue(row)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "Math.abs(roundedFallback - computed) <= REPAIR_ORDER_ROW_TOTAL_ROUNDING_TOLERANCE",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function repairOrderSummaryValue(baseTotal, payments)", BOARD_WEB_APP_HTML)
        self.assertIn("function syncRepairOrderTotals()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const paymentMethod = syncRepairOrderPaymentMethodFromPayments();", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "const summary = repairOrderSummaryValue(subtotal, state.repairOrderPayments);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("state.repairOrderSummary = summary;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const basePaidCard = repairOrderCardPaymentsValue(normalizedPayments);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "Math.max(normalizedBaseTotal - basePaidCash - basePaidCard - basePaidNoncash, 0)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("base_paid_card: basePaidCard", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const totalPaid = repairOrderRoundMoney(basePaidCash + basePaidCard + basePaidNoncash);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const cashDue = repairOrderRoundMoney(Math.max(normalizedBaseTotal + taxesAndFees - totalPaid, 0));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "noncash_due: repairOrderCashlessGrossValue(cashDue),",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "total_paid: totalPaid",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".format(rounded).replace(/,00$/, '') + ' ₽';", BOARD_WEB_APP_HTML)
        self.assertIn(
            "node.textContent = repairOrderFormatMoney(summary.base_total);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "node.textContent = repairOrderFormatMoney(summary.noncash_due);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "node.textContent = repairOrderFormatMoney(summary.cash_due);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "node.textContent = repairOrderFormatMoney(summary.taxes_and_fees);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "function renderRepairOrderPayments({ syncTotals = true } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const summary = repairOrderSummaryValue(subtotal, payments);", BOARD_WEB_APP_HTML
        )
        self.assertIn("const due = summary.cash_due;", BOARD_WEB_APP_HTML)
        self.assertIn("function openRepairOrderPaymentsModal()", BOARD_WEB_APP_HTML)
        self.assertIn("async function addRepairOrderPayment()", BOARD_WEB_APP_HTML)
        self.assertIn("async function deleteRepairOrderPayment(paymentId)", BOARD_WEB_APP_HTML)
        delete_payment_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "async function deleteRepairOrderPayment(paymentId)"
            ) : BOARD_WEB_APP_HTML.index("async function addRepairOrderPayment()")
        ]
        self.assertIn(
            "const previousPayments = (state.repairOrderPayments || []).slice();",
            delete_payment_fragment,
        )
        self.assertIn(
            "const persisted = await persistRepairOrderRecord({ silent: true });",
            delete_payment_fragment,
        )
        self.assertIn("applyRepairOrderToForm(persisted.repairOrder);", delete_payment_fragment)
        self.assertIn("state.repairOrderPayments = previousPayments;", delete_payment_fragment)
        self.assertIn("сохранено в кассу", BOARD_WEB_APP_HTML)
        self.assertIn("legacy без движения", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const persisted = await persistRepairOrderRecord({ silent: true });",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('data-repair-order-cell="catalog_number"', BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-total--subtotal {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-total--cashless-due {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-total--cash-due {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-total--taxes {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function addRepairOrderRowFromButton(section, event)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function handleRepairOrderModalInput(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderRepairOrderTags()", BOARD_WEB_APP_HTML)
        self.assertIn("function editRepairOrderTag(label)", BOARD_WEB_APP_HTML)
        self.assertIn("function removeRepairOrderTag(label)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleRepairOrderTagInputKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function saveRepairOrderDraft()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "printRepairOrderDraft = function() { return openRepairOrderPrintWorkspace(); };",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("async function ensureRepairOrderCard()", BOARD_WEB_APP_HTML)
        self.assertIn("async function requireRepairOrderCardId()", BOARD_WEB_APP_HTML)
        self.assertIn("const data = await persistCardPayload(payload);", BOARD_WEB_APP_HTML)
        self.assertIn("applyCardModalState(savedCard);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function applyRepairOrderCardUpdate(updatedCard, fallbackOrder = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "body.insertAdjacentHTML('beforeend', repairOrderRowHtml(section, emptyRepairOrderRow(defaults), rowIndex));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("saveRepairOrder = async function(", BOARD_WEB_APP_HTML)
        self.assertIn("const cardId = await requireRepairOrderCardId();", BOARD_WEB_APP_HTML)
        self.assertIn("source.client_information ?? source.comment", BOARD_WEB_APP_HTML)
        self.assertNotIn("comment: currentCard.description || ''", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "comment: item.comment ?? snapshotCard.description ?? ''", BOARD_WEB_APP_HTML
        )
        self.assertIn("comment: currentCard.repair_order?.comment || ''", BOARD_WEB_APP_HTML)
        self.assertIn(
            "comment: item.comment ?? snapshotCard.repair_order?.comment ?? ''",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function repairOrderCanonicalDateValue(value)", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderFormDateDisplayValue(value)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "syncRepairOrderPaymentMethod(repairOrderPaymentMethodFromPayments(state.repairOrderPayments, normalized.payment_method));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "state.repairOrderPayments = normalizeRepairOrderPayments(normalized.payments, normalized.prepayment, normalized.opened_at || normalized.date);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "payment_method: repairOrderPaymentMethodFromCashboxName(", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "prepayment: repairOrderNumberToRaw(repairOrderPaymentsTotalValue(state.repairOrderPayments)),",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "payments: (state.repairOrderPayments || []).map((item, index) => normalizeRepairOrderPayment({",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".repair-order-footer {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-total--subtotal {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-total--cashless-due {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-total--cash-due {", BOARD_WEB_APP_HTML)
        self.assertIn('aria-label="Удалить оплату">&times;</button>', BOARD_WEB_APP_HTML)
        self.assertIn('aria-label="Удалить метку">&times;</button>', BOARD_WEB_APP_HTML)
        self.assertIn("#repairOrderPaymentsModal {", BOARD_WEB_APP_HTML)
        self.assertIn(".dialog--repair-order-payments {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-money-button {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-payments-layout {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-payments-stats {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-payments-form__note {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-payment-row__body {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-payment-row__line {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-payment-row__subline {", BOARD_WEB_APP_HTML)
        self.assertIn("payments.slice().reverse().map((item) => {", BOARD_WEB_APP_HTML)
        self.assertIn("Кем: ", BOARD_WEB_APP_HTML)
        self.assertIn("Касса: ", BOARD_WEB_APP_HTML)
        self.assertNotIn("'/api/autofill_repair_order'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.repairOrderModal.addEventListener('change', handleRepairOrderModalInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("function buildRepairOrderPrintHtml(order)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function openRepairOrderPrint(order)", BOARD_WEB_APP_HTML)
        self.assertNotIn("printWindow.print();", BOARD_WEB_APP_HTML)

    def test_repair_order_materials_executor_column_is_after_name(self) -> None:
        materials_table = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                '<section class="repair-order-table-card repair-order-materials-card" data-repair-order-section="materials">'
            ) : BOARD_WEB_APP_HTML.index('<tbody id="repairOrderMaterialsBody"></tbody>')
        ]
        self.assertLess(materials_table.index(">Наименование<"), materials_table.index(">Списал<"))
        self.assertLess(materials_table.index(">Списал<"), materials_table.index(">Кат. №<"))
        self.assertLess(materials_table.index(">Кат. №<"), materials_table.index(">Кол-во<"))

        row_renderer = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "function repairOrderRowHtml(section, row, index)"
            ) : BOARD_WEB_APP_HTML.index("function readRepairOrderRowElement(row)")
        ]
        self.assertLess(
            row_renderer.index("repairOrderRowInputHtml('name'"),
            row_renderer.index("materialExecutorCell +"),
        )
        self.assertLess(
            row_renderer.index("materialExecutorCell +"), row_renderer.index("catalogCell +")
        )
        self.assertLess(
            row_renderer.index("catalogCell +"),
            row_renderer.index("repairOrderRowInputHtml('quantity'"),
        )

    def test_repair_order_work_executor_salary_override_popover_is_wired(self) -> None:
        self.assertIn('id="repairOrderWorkSalaryPopover"', BOARD_WEB_APP_HTML)
        self.assertIn("data-repair-order-work-salary-menu", BOARD_WEB_APP_HTML)
        self.assertIn("data-repair-order-work-salary-gear", BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-cell="work_salary_guarantee"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-cell="work_salary_percent_override"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-cell="work_salary_cost_price"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderWorkSalaryCostPrice"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderWorkSalaryCostPreview"', BOARD_WEB_APP_HTML)
        self.assertIn("data-repair-order-work-salary-cost-price", BOARD_WEB_APP_HTML)
        self.assertNotIn('id="repairOrderWorkSalaryNote"', BOARD_WEB_APP_HTML)
        self.assertNotIn('data-repair-order-cell="work_salary_note"', BOARD_WEB_APP_HTML)
        self.assertNotIn("els.repairOrderWorkSalaryNote", BOARD_WEB_APP_HTML)
        self.assertIn("function openRepairOrderWorkSalaryPopover(", BOARD_WEB_APP_HTML)
        self.assertIn("function applyRepairOrderWorkSalaryPopover()", BOARD_WEB_APP_HTML)
        self.assertIn("function resetRepairOrderWorkSalaryOverride()", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderWorkSalaryPreview(", BOARD_WEB_APP_HTML)
        self.assertIn(
            "repairOrderNormalizeBool(rowData?.work_salary_override_enabled || '') === 'true'",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "return repairOrderNormalizePercentRaw(rawOverridePercent || '0') || '0';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("work_salary_override_enabled", BOARD_WEB_APP_HTML)
        self.assertIn("work_salary_guarantee", BOARD_WEB_APP_HTML)
        self.assertIn("work_salary_percent_override", BOARD_WEB_APP_HTML)
        self.assertIn("work_salary_cost_price", BOARD_WEB_APP_HTML)
        self.assertIn("work_executor_id_snapshot", BOARD_WEB_APP_HTML)
        self.assertIn("data-repair-order-work-executor-id", BOARD_WEB_APP_HTML)
        self.assertIn("row.dataset.repairOrderWorkExecutorId", BOARD_WEB_APP_HTML)
        self.assertIn("work_quantity_snapshot", BOARD_WEB_APP_HTML)
        self.assertIn("work_price_snapshot", BOARD_WEB_APP_HTML)
        self.assertIn("work_total_snapshot", BOARD_WEB_APP_HTML)
        self.assertIn("data-repair-order-work-quantity", BOARD_WEB_APP_HTML)
        self.assertIn("row.dataset.repairOrderWorkQuantity", BOARD_WEB_APP_HTML)
        self.assertIn("ВЫПЛАТА ИСПОЛНИТЕЛЮ", BOARD_WEB_APP_HTML)
        self.assertIn("СЕБЕСТОИМОСТЬ РАБОТЫ", BOARD_WEB_APP_HTML)
        self.assertNotIn("ГАРАНТИЯ ИСПОЛНИТЕЛЮ", BOARD_WEB_APP_HTML)
        self.assertIn("К НАЧИСЛЕНИЮ", BOARD_WEB_APP_HTML)
        self.assertIn("ПРОЦЕНТ СЕРВИСА", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const percentBase = Math.max(total - safeGuarantee - safeCostPrice, 0);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const effectiveQuantity = quantityParsed === null && !String(quantityValue ?? '').trim() ? 1 : quantityParsed;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("total: liveTotalValue,", BOARD_WEB_APP_HTML)

    def test_repair_order_manual_form_does_not_expose_autofill(self) -> None:
        repair_order_modal_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                '<div class="modal" id="repairOrderModal">'
            ) : BOARD_WEB_APP_HTML.index('<div class="modal" id="repairOrderPrintModal">')
        ]
        self.assertNotIn("repairOrderAutofillButton", repair_order_modal_fragment)
        self.assertNotIn("АВТОЗАПОЛНЕНИЕ", repair_order_modal_fragment)
        self.assertNotIn("repair-order-headline-actions", repair_order_modal_fragment)

    def test_repair_order_print_module_exposes_preview_template_editor_and_routes(self) -> None:
        self.assertIn('id="repairOrderPrintModal"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="manualDocumentPrintButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="mobileManualDocumentPrintButton"', BOARD_WEB_APP_HTML)
        topbar_actions = re.search(
            r'<div class="topbar__actions">(?P<body>.*?)</div>',
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(topbar_actions)
        self.assertNotIn('id="manualDocumentPrintButton"', topbar_actions.group("body"))
        self.assertNotIn('id="mobileManualDocumentPrintButton"', topbar_actions.group("body"))
        print_footer_actions = re.search(
            r'<div class="repair-order-print-footer__actions">(?P<body>.*?)</div>',
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(print_footer_actions)
        print_footer_html = print_footer_actions.group("body")
        self.assertNotIn('id="manualDocumentPrintButton"', print_footer_html)
        self.assertIn('id="repairOrderPrintExportButton"', print_footer_html)
        self.assertIn('id="repairOrderPrintRunButton"', print_footer_html)
        self.assertIn("body.is-mobile-lite #repairOrderPrintModal", BOARD_WEB_APP_HTML)
        self.assertIn("body.is-mobile-lite #inspectionSheetFormModal", BOARD_WEB_APP_HTML)
        self.assertIn(
            "grid-template-columns: 220px minmax(0, 1fr);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('grid-template-areas: "docs preview"', BOARD_WEB_APP_HTML)
        self.assertIn('grid-template-areas: "docs" "preview"', BOARD_WEB_APP_HTML)
        self.assertIn(
            ".repair-order-print-panel.repair-order-print-panel--settings {\n      display: none;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("grid-auto-rows: auto", BOARD_WEB_APP_HTML)
        self.assertIn(
            ".repair-order-print-layout > .repair-order-print-panel { min-height: auto; overflow: visible; }",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            ".repair-order-print-layout > .repair-order-print-panel:nth-child(2) { min-height: 420px; overflow: hidden; }",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('id="repairOrderPrintPreviewStage"', BOARD_WEB_APP_HTML)
        self.assertIn("REPAIR_ORDER_PRINT_PREVIEW_VIEWPORTS", BOARD_WEB_APP_HTML)
        self.assertIn("landscape: { width: 1180, height: 860 }", BOARD_WEB_APP_HTML)
        self.assertIn("pageHtml.includes('regulated-page--landscape')", BOARD_WEB_APP_HTML)
        self.assertIn("Math.min(1, availableWidth / viewport.width)", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            ".repair-order-print-layout,\n      .print-template-editor { grid-template-columns: 1fr; grid-template-areas: none; }",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('id="repairOrderPrintModeSelect"', BOARD_WEB_APP_HTML)
        self.assertIn('value="manual">Документ без карточки</option>', BOARD_WEB_APP_HTML)
        self.assertIn('id="manualPrintDocumentForm"', BOARD_WEB_APP_HTML)
        self.assertIn('id="manualPrintClientName"', BOARD_WEB_APP_HTML)
        self.assertIn('id="manualPrintClientInn"', BOARD_WEB_APP_HTML)
        self.assertIn('id="manualPrintVehicle"', BOARD_WEB_APP_HTML)
        self.assertIn('id="manualPrintWorks"', BOARD_WEB_APP_HTML)
        self.assertIn('id="manualPrintMaterials"', BOARD_WEB_APP_HTML)
        self.assertIn('id="manualPrintPayments"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="repair-order-print-panel repair-order-print-panel--settings" hidden',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('id="regulatedPrintOverridesForm"', BOARD_WEB_APP_HTML)
        self.assertIn('id="regulatedPrintBuyerName"', BOARD_WEB_APP_HTML)
        self.assertIn('id="regulatedPrintBuyerInn"', BOARD_WEB_APP_HTML)
        self.assertIn('id="regulatedPrintBuyerKpp"', BOARD_WEB_APP_HTML)
        self.assertIn('id="regulatedPrintBasis"', BOARD_WEB_APP_HTML)
        self.assertIn('id="regulatedPrintTransportDetails"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderPrintDocuments"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderPrintPreviewFrame"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="repairOrderPrintTemplateSelect"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="repairOrderPrintTemplateEditorButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn("Шаблон активного документа", BOARD_WEB_APP_HTML)
        self.assertNotIn("ШАБЛОНЫ</button>", BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderPrintPrinterSelect"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inspectionSheetFormModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inspectionSheetFormAutofillButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inspectionSheetFormApplyButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inspectionSheetWorkRows"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inspectionSheetMaterialRows"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inspectionSheetAddWorkRowButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inspectionSheetAddMaterialRowButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="printProfileOgrn"', BOARD_WEB_APP_HTML)
        self.assertIn('id="printTemplateEditorModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="printTemplateContent"', BOARD_WEB_APP_HTML)
        self.assertIn('id="printTemplateVisualEditorFrame"', BOARD_WEB_APP_HTML)
        self.assertIn('id="printTemplateTokenSelect"', BOARD_WEB_APP_HTML)
        self.assertIn('id="printTemplatePreviewFrame"', BOARD_WEB_APP_HTML)
        self.assertIn("async function openRepairOrderPrintWorkspace()", BOARD_WEB_APP_HTML)
        self.assertIn("async function openManualDocumentPrintWorkspace()", BOARD_WEB_APP_HTML)
        self.assertIn("function blankManualPrintDocument()", BOARD_WEB_APP_HTML)
        self.assertIn("function manualPrintLocalDateValue()", BOARD_WEB_APP_HTML)
        self.assertIn("document_date: manualPrintLocalDateValue(),", BOARD_WEB_APP_HTML)
        self.assertNotIn("document_date: new Date().toISOString().slice(0, 10)", BOARD_WEB_APP_HTML)
        self.assertIn("function readManualPrintDocumentFromInputs()", BOARD_WEB_APP_HTML)
        self.assertIn("function readRegulatedPrintOverridesFromInputs()", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderPrintIsManualMode()", BOARD_WEB_APP_HTML)
        self.assertIn("function syncRepairOrderPrintPrinterState()", BOARD_WEB_APP_HTML)
        self.assertIn("function runRepairOrderBrowserPrint()", BOARD_WEB_APP_HTML)
        self.assertIn("isPrintRunning: false,", BOARD_WEB_APP_HTML)
        self.assertIn("let printStarted = false;", BOARD_WEB_APP_HTML)
        self.assertIn("if (printStarted) return;", BOARD_WEB_APP_HTML)
        self.assertIn("frame.onload = null;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "frame.setAttribute('sandbox', 'allow-same-origin allow-modals');", BOARD_WEB_APP_HTML
        )
        self.assertIn("if (repairOrderPrintState.isPrintRunning) return;", BOARD_WEB_APP_HTML)
        self.assertIn("function buildPrintTemplateVisualEditorHtml(content)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const PRINT_TEMPLATE_UPLOAD_MAX_SIZE_BYTES = 256 * 1024;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "if (file.size > PRINT_TEMPLATE_UPLOAD_MAX_SIZE_BYTES)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function buildPrintTemplateEditorFallbackHtml(title, message, detail = '')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function schedulePrintTemplatePreview()", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/get_repair_order_print_workspace'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/get_inspection_sheet_form'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/save_inspection_sheet_form'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/autofill_inspection_sheet_form'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/preview_repair_order_print_documents'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/export_repair_order_print_pdf'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/print_repair_order_documents'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/save_print_template'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/set_default_print_template'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "printRepairOrderDraft = function() { return openRepairOrderPrintWorkspace(); };",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("printManualDocumentDraft", BOARD_WEB_APP_HTML)
        self.assertNotIn("manualDocumentPrintButton?.addEventListener", BOARD_WEB_APP_HTML)
        self.assertIn("document_without_card: repairOrderPrintIsManualMode()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "document_overrides: readRegulatedPrintOverridesFromInputs()", BOARD_WEB_APP_HTML
        )
        self.assertIn("manual_document: readManualPrintDocumentFromInputs()", BOARD_WEB_APP_HTML)
        self.assertIn('id="manualPrintTaxLabel"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'id="repairOrderPrintPreviewFrame" title="Предпросмотр документа" sandbox="allow-same-origin"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'id="printTemplateVisualEditorFrame" title="Визуальный редактор шаблона" sandbox="allow-same-origin"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'id="printTemplatePreviewFrame" title="Предпросмотр шаблона" sandbox="allow-same-origin"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("tax_label: printEls.manualTaxLabel?.value || ''", BOARD_WEB_APP_HTML)
        self.assertIn("if (parts.length >= 3) {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "return { name: parts[0], quantity: parts[1] || '1', price: parts[2] || '', total: parts[3] || '' };",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "printEls.documents.addEventListener('change', handleRepairOrderPrintDocumentsChange);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('role="tablist"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "return '<button class=\"repair-order-print-doc' + activeClass + '\" data-print-document=\"' + escapeHtml(item.id) + '\" type=\"button\" role=\"tab\" aria-selected=\"' + (isActive ? 'true' : 'false') + '\">' +",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("data-print-document-toggle", BOARD_WEB_APP_HTML)
        self.assertIn("repairOrderPrintDocumentsCount", BOARD_WEB_APP_HTML)
        self.assertIn(
            "repairOrderPrintState.selectedDocumentIds = [documentId];", BOARD_WEB_APP_HTML
        )
        self.assertIn("repairOrderPrintDocumentsAction", BOARD_WEB_APP_HTML)
        self.assertNotIn("data-print-inspection-fill", BOARD_WEB_APP_HTML)
        self.assertIn("async function openInspectionSheetForm()", BOARD_WEB_APP_HTML)
        self.assertIn("async function saveInspectionSheetFormDraft", BOARD_WEB_APP_HTML)
        self.assertIn("async function autofillInspectionSheetFormDraft()", BOARD_WEB_APP_HTML)
        self.assertIn("function normalizeInspectionSheetTableRows(value)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderInspectionSheetTableRows(kind, rows)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleInspectionSheetTableRowsClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "planned_work_rows: readInspectionSheetTableRows('works')", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "planned_material_rows: readInspectionSheetTableRows('materials')", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "printEls.templateVisualEditorFrame.addEventListener('load', handlePrintTemplateVisualEditorLoad);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("schedulePrintTemplatePreview();", BOARD_WEB_APP_HTML)
        self.assertIn(
            "printEls.templatePreviewFrame.srcdoc = buildPrintTemplateEditorFallbackHtml(",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("win.print();", BOARD_WEB_APP_HTML)
        self.assertIn("#repairOrderPrintModal {", BOARD_WEB_APP_HTML)
        self.assertIn("#printTemplateEditorModal {", BOARD_WEB_APP_HTML)
        self.assertIn("z-index: 16;", BOARD_WEB_APP_HTML)
        self.assertIn("z-index: 17;", BOARD_WEB_APP_HTML)

    def test_topbar_repair_orders_list_uses_compact_row_open_flow(self) -> None:
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
            "await openRepairOrderModal({ preloadedRepairOrderData: data });",
            BOARD_WEB_APP_HTML,
        )
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
        self.assertIn(".dialog--repair-orders {", BOARD_WEB_APP_HTML)
        self.assertIn("width: min(1940px, calc(100vw - 24px));", BOARD_WEB_APP_HTML)
        repair_orders_modal_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                '<div class="modal" id="repairOrdersModal">'
            ) : BOARD_WEB_APP_HTML.index(
                '<div class="repair-orders-table-head" id="repairOrdersTableHead">'
            )
        ]
        self.assertIn("dialog__head-actions--repair-orders", repair_orders_modal_fragment)
        self.assertIn(
            'class="repair-orders-controls repair-orders-controls--header"',
            repair_orders_modal_fragment,
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
        head_left_rule = re.search(
            r"\.dialog__head-left--repair-orders \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(head_left_rule)
        assert head_left_rule is not None
        self.assertIn("flex: 0 0 auto;", head_left_rule.group("body"))
        head_actions_rule = re.search(
            r"\.dialog__head-actions--repair-orders \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(head_actions_rule)
        assert head_actions_rule is not None
        self.assertIn("align-items: center;", head_actions_rule.group("body"))
        self.assertIn("justify-content: flex-start;", head_actions_rule.group("body"))
        self.assertIn("margin-left: 12px;", head_actions_rule.group("body"))
        close_button_rule = re.search(
            r"\.dialog__head-actions--repair-orders \[data-close=\"repair-orders\"\] \{(?P<body>.*?)\n    \}",
            BOARD_WEB_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(close_button_rule)
        assert close_button_rule is not None
        self.assertIn("margin-left: auto;", close_button_rule.group("body"))
        self.assertIn("min-width: 96px;", close_button_rule.group("body"))
        self.assertIn("min-height: 36px;", close_button_rule.group("body"))
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
        self.assertIn(
            "function filterRepairOrdersItems(items = state.repairOrdersItems)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function handleRepairOrdersSearchFieldClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const REPAIR_ORDER_SEARCH_FIELDS = ['number', 'date', 'client', 'phone', 'vehicle', 'summary', 'license_plate'];",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("if (normalized === 'license_plate') return 'ГОСНОМЕР';", BOARD_WEB_APP_HTML)
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

    def test_repair_order_modal_supports_status_and_extended_vehicle_fields(self) -> None:
        self.assertIn('id="repairOrderOpenedAt"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderClosedAt"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderStatus"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="dialog__head dialog__head--card dialog__head--repair-order dialog__floating-actions"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('class="repair-order-headline"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderVin"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderMileage"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderReason"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderNote"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'repair-order-card repair-order-card--wide hidden" data-repair-order-section="reason"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'repair-order-card repair-order-card--wide hidden" data-repair-order-section="note"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'repair-order-card repair-order-card--wide repair-order-tags-card hidden" data-repair-order-section="tags"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('id="repairOrderCloseButton"', BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderStatusLabel(status)", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderCloseBlockedMessage()", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderIsFullyPaid(order)", BOARD_WEB_APP_HTML)
        self.assertIn("function syncRepairOrderCloseButtonState(order = null)", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderCardDraft(card, order = {})", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function syncRepairOrderStatusUi(status, orderForClose = null)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.repairOrderCloseButton.dataset.closeAvailable = closeAvailable ? 'true' : 'false';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('#repairOrderCloseButton[data-close-available="false"],', BOARD_WEB_APP_HTML)
        self.assertIn("opacity: 0.42;", BOARD_WEB_APP_HTML)
        self.assertIn('#repairOrderCloseButton[data-close-available="true"] {', BOARD_WEB_APP_HTML)
        self.assertIn("border-color: rgba(186, 197, 146, 0.82);", BOARD_WEB_APP_HTML)
        self.assertIn("animation: repair-order-close-ready-pulse", BOARD_WEB_APP_HTML)
        self.assertIn("@keyframes repair-order-close-ready-pulse", BOARD_WEB_APP_HTML)
        self.assertIn(
            '#repairOrderCloseButton[data-close-available="true"]:hover', BOARD_WEB_APP_HTML
        )
        self.assertIn("transform: translateY(-1px);", BOARD_WEB_APP_HTML)
        self.assertIn("rgba(49, 58, 47, 0.99);", BOARD_WEB_APP_HTML)
        self.assertIn("rgba(68, 77, 63, 1);", BOARD_WEB_APP_HTML)
        self.assertIn(
            '#repairOrderCloseButton[data-close-available="true"]:active,',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            '#repairOrderCloseButton[data-close-available="true"]:focus-visible',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "async function persistRepairOrderRecord({ statusMessage = '', silent = false } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("'/api/update_repair_order'", BOARD_WEB_APP_HTML)
        self.assertIn("repairOrderInitialPayloadKey: ''", BOARD_WEB_APP_HTML)
        self.assertIn("state.repairOrderSaveInFlight = false", BOARD_WEB_APP_HTML)
        self.assertIn("state.repairOrderSavePromise = null", BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderModalHasUnsavedChanges()", BOARD_WEB_APP_HTML)
        self.assertIn("function syncRepairOrderSaveDirtyState()", BOARD_WEB_APP_HTML)
        self.assertIn("function scheduleRepairOrderSaveDirtyStateSync()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.repairOrderSaveButton.classList.toggle('is-dirty', hasUnsavedChanges);",
            BOARD_WEB_APP_HTML,
        )
        footer_actions_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                '<div class="repair-order-footer__actions">'
            ) : BOARD_WEB_APP_HTML.index(
                '<button class="btn repair-order-save" id="repairOrderSaveButton"'
            )
        ]
        self.assertNotIn("ОТМЕНА", footer_actions_fragment)
        self.assertNotIn('data-close="repair-order"', footer_actions_fragment)
        self.assertIn("height: 36px;", BOARD_WEB_APP_HTML)
        self.assertIn("#repairOrderSaveButton.is-dirty:not(:disabled) {", BOARD_WEB_APP_HTML)
        self.assertIn("animation: repair-order-save-pulse", BOARD_WEB_APP_HTML)
        self.assertIn(
            "license_plate: currentCard.repair_order?.license_plate || profile.registration_plate || profile.license_plate || ''",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "vehicle: currentCard.vehicle || vehicleDisplayFromProfile(profile)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("async function toggleRepairOrderStatus()", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/set_repair_order_status'", BOARD_WEB_APP_HTML)
        self.assertIn("setStatus(repairOrderCloseBlockedMessage(), true);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.repairOrderCloseButton.addEventListener('click', toggleRepairOrderStatus);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".repair-order-status {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 10.5px;", BOARD_WEB_APP_HTML)
        self.assertIn("background: rgba(88, 138, 70, 0.28);", BOARD_WEB_APP_HTML)
        self.assertIn('.repair-order-status[data-status="closed"] {', BOARD_WEB_APP_HTML)
        self.assertIn('.repair-order-status[data-status="ready"] {', BOARD_WEB_APP_HTML)

    def test_repair_orders_menu_supports_open_ready_and_closed_filters(self) -> None:
        self.assertIn('id="repairOrdersOpenTab"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrdersReadyTab"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrdersClosedTab"', BOARD_WEB_APP_HTML)
        self.assertIn("function updateRepairOrdersTabs()", BOARD_WEB_APP_HTML)
        self.assertIn("#repairOrdersOpenTab.is-active {", BOARD_WEB_APP_HTML)
        self.assertIn("#repairOrdersReadyTab.is-active {", BOARD_WEB_APP_HTML)
        self.assertIn("#repairOrdersClosedTab.is-active {", BOARD_WEB_APP_HTML)
        self.assertIn("0 0 12px rgba(105, 196, 95, 0.18)", BOARD_WEB_APP_HTML)
        self.assertIn("0 0 12px rgba(174, 181, 181, 0.18)", BOARD_WEB_APP_HTML)
        self.assertIn("0 0 12px rgba(207, 112, 100, 0.18)", BOARD_WEB_APP_HTML)
        self.assertIn("data-repair-orders-filter", BOARD_WEB_APP_HTML)
        self.assertIn("renderRepairOrderListRows = function(items)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function setRepairOrdersFilter(status, { openModal = false } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("syncRepairOrdersLayout(normalizedFilter);", BOARD_WEB_APP_HTML)
        repair_orders_meta_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "repairOrdersMetaText = function(items, meta)"
            ) : BOARD_WEB_APP_HTML.index("renderRepairOrderListRows = function(items)")
        ]
        self.assertIn("return '';", repair_orders_meta_fragment)
        self.assertNotIn("ОТКРЫТЫЕ: ", repair_orders_meta_fragment)
        self.assertNotIn("ГОТОВЫЕ: ", repair_orders_meta_fragment)
        self.assertNotIn("АРХИВ: ", repair_orders_meta_fragment)
        self.assertIn("repairOrdersIsClosedView(status)", BOARD_WEB_APP_HTML)
        self.assertIn("const phoneText = phone || '-';", BOARD_WEB_APP_HTML)
        self.assertIn("repairOrdersTableHeadSearchableHtml('Даты', 'date')", BOARD_WEB_APP_HTML)
        self.assertIn(
            "repairOrdersTableHeadSearchableHtml('Госномер', 'license_plate')", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "const displayedDate = isClosedView ? (closedAt || openedAt || '-') : (openedAt || closedAt || '-')",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("const closedMeta = rawStatus === 'closed'", BOARD_WEB_APP_HTML)
        self.assertIn("Статус", BOARD_WEB_APP_HTML)
        self.assertIn("Телефон", BOARD_WEB_APP_HTML)
        self.assertIn(
            "item.opened_at || item.created_at || item.date || item.updated_at", BOARD_WEB_APP_HTML
        )
        self.assertIn("item.grand_total, item.works_total", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.repairOrdersOpenTab.addEventListener('click', () => setRepairOrdersFilter('open'));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.repairOrdersClosedTab.addEventListener('click', () => setRepairOrdersFilter('closed'));",
            BOARD_WEB_APP_HTML,
        )

    def test_cashbox_journal_ledger_layout_is_polished(self) -> None:
        self.assertIn(
            "--cash-journal-operation-grid: 58px minmax(142px, 176px) 82px minmax(280px, 1fr) 86px minmax(104px, 124px);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("scroll-padding-top: 42px;", BOARD_WEB_APP_HTML)
        self.assertIn("top: 0;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "box-shadow: 0 1px 0 rgba(255,255,255,0.08), 0 8px 16px rgba(0,0,0,0.28);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("min-height: 42px;", BOARD_WEB_APP_HTML)
        self.assertIn("justify-self: start;", BOARD_WEB_APP_HTML)
        self.assertIn("min-width: 104px;", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 13px;", BOARD_WEB_APP_HTML)
        self.assertIn("max-width: min(100%, 180px);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "background: linear-gradient(90deg, rgba(167, 178, 132, 0.38) 0 2px, transparent 2px);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("padding-left: 7px;", BOARD_WEB_APP_HTML)

    def test_cashboxes_modal_exposes_minimal_accounting_workspace(self) -> None:
        self.assertIn('id="cashboxesButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxesModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransferModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxJournalModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxesList"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="cashboxCreateButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxJournalButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxJournalLedgerButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxJournalStatsButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxJournalDownloadButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="cashboxDeleteButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="cashboxCancelLastButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="cashboxStats"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxCancelPopover"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxCancelReasonInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxCancelConfirmButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxIncomeButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransferButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransferTargets"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransferAmountInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransferConfirmButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxExpenseButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransactions"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxJournalText"', BOARD_WEB_APP_HTML)
        self.assertIn(
            ".cashbox-composer__row .field--compact textarea.is-invalid", BOARD_WEB_APP_HTML
        )
        self.assertIn('title="Перетащите, чтобы изменить порядок касс"', BOARD_WEB_APP_HTML)
        self.assertIn(".cashboxes-layout {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashboxes-pane__foot {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashboxes-list.is-drag-active .cashbox-row {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashboxes-list.is-drop-end::after {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-transactions-card {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-transaction__cancel {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-cancel-popover {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-text {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-view {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-mode-switch {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-mode-button.is-active {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-toolbar {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-toolbar__status {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-filter {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-period-segments {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-active-filters {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-reset {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-filter-reset {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-balance-strip {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-balance-strip.is-expanded {", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            ".cashbox-journal-balance-toggle[data-balance-warning=", BOARD_WEB_APP_HTML
        )
        self.assertIn(".cashbox-journal-day-divider {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-day-details {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-operation-head {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-operation-row {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "grid-template-columns: var(--cash-journal-operation-grid);", BOARD_WEB_APP_HTML
        )
        self.assertIn(".cashbox-journal-operation-row__type {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-operation-row__amount {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-operation-tags {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-sticky {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-load-more {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-opening {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-stats-section {", BOARD_WEB_APP_HTML)
        self.assertIn("--cash-journal-stats-grid:", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-stats-head {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-stats-table {", BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-columns: var(--cash-journal-stats-grid);", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-download-button {", BOARD_WEB_APP_HTML)
        self.assertIn(".card-journal-text {", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-delete-button {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-detail__identity {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-composer__actions {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-transfer-grid {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-transfer-target {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-transfer-preview {", BOARD_WEB_APP_HTML)
        self.assertIn('.cashbox-row[draggable="true"] {', BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-row.is-drop-target {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-row.is-drop-target::before {", BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="btn btn--accent cashbox-journal-main-button" id="cashboxJournalButton">ЖУРНАЛ',
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("+ ДОБАВИТЬ", BOARD_WEB_APP_HTML)
        self.assertNotIn("- УДАЛИТЬ", BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="btn btn--accent" id="cashboxTransferConfirmButton">ПЕРЕМЕСТИТЬ',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function ensureCashboxesUi()", BOARD_WEB_APP_HTML)
        self.assertIn("function openCashboxesModal()", BOARD_WEB_APP_HTML)
        self.assertIn("cashboxesLoadController: null", BOARD_WEB_APP_HTML)
        self.assertIn("function abortCashboxesLoad()", BOARD_WEB_APP_HTML)
        self.assertIn("state.cashboxesLoadController.abort();", BOARD_WEB_APP_HTML)
        self.assertIn(
            "abortCashboxesLoad();\n          closeCashboxTransferModal();", BOARD_WEB_APP_HTML
        )
        self.assertIn("signal: loadContext.controller.signal", BOARD_WEB_APP_HTML)
        self.assertIn("if (!isCurrentCashboxesLoad(loadContext)) return null;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cashboxDetailTitle.textContent = 'ЗАГРУЖАЮ КАССЫ...';", BOARD_WEB_APP_HTML
        )
        self.assertNotIn("els.cashboxStats", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cashboxTransactions.innerHTML = '<div class=\"cashboxes-empty\">ЗАГРУЖАЮ ДВИЖЕНИЯ...</div>';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "async function loadCashJournalData({ includeMarkdown = false } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("async function openCashJournalModal()", BOARD_WEB_APP_HTML)
        self.assertIn("async function loadCashJournalText()", BOARD_WEB_APP_HTML)
        self.assertIn("function renderCashJournal(data)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function renderCashJournalCurrentBalances(data)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderCashJournalStats(data)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderCashJournalLoading()", BOARD_WEB_APP_HTML)
        self.assertIn("const CASH_JOURNAL_RENDER_BATCH_SIZE = 250;", BOARD_WEB_APP_HTML)
        self.assertIn("cashboxJournalVisibleRowLimit", BOARD_WEB_APP_HTML)
        self.assertNotIn("cashboxJournalBalancesExpanded", BOARD_WEB_APP_HTML)
        self.assertNotIn("data-cash-journal-toggle-balances", BOARD_WEB_APP_HTML)
        self.assertIn("data-cash-journal-load-more", BOARD_WEB_APP_HTML)
        self.assertIn("ЗАГРУЖАЮ ЖУРНАЛ", BOARD_WEB_APP_HTML)
        self.assertIn("include_markdown=false", BOARD_WEB_APP_HTML)
        self.assertIn("compact_groups=true", BOARD_WEB_APP_HTML)
        self.assertIn("loadCashJournalData({ includeMarkdown: true })", BOARD_WEB_APP_HTML)
        self.assertNotIn("function renderCashJournalOpening(day)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalDefaultFilters()", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalEntryNoteText(item)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalCleanOperationNote(note)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalDisplayTypeLabel(direction)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalDayCompactSummaryHtml(day)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalLinkFlags(item)", BOARD_WEB_APP_HTML)
        self.assertIn("поступление|списание|приход|расход", BOARD_WEB_APP_HTML)
        self.assertNotIn("function cashJournalFiltersAreActive(", BOARD_WEB_APP_HTML)
        self.assertNotIn("function cashJournalOperationCountText(", BOARD_WEB_APP_HTML)
        self.assertNotIn("function cashJournalActiveFiltersHtml(data)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalOperationHeaderHtml()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function cashJournalLoadMoreText(renderedRowCount, filteredRowCount)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function cashJournalTransferSummaryText(transferMinor, count)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function cashJournalOperationTagsHtml(tags)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalLegacyTransferPeerName(item)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function cashJournalLegacyTransferPairMatches(item, candidate)", BOARD_WEB_APP_HTML
        )
        self.assertIn("const legacyCandidates = entries.filter", BOARD_WEB_APP_HTML)
        self.assertIn("legacyCandidates.length === 1", BOARD_WEB_APP_HTML)
        self.assertIn(
            "cashJournalEntryDateKey(candidate) === cashJournalEntryDateKey(item)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "finiteNumber(candidate?.amount_minor) === amountMinor",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "String(candidate?.time_short || '') === String(item?.time_short || '')",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("source === 'перемещение' ? ['нет пары'] : []", BOARD_WEB_APP_HTML)
        self.assertNotIn("cashJournalOperationTagsHtml(['перемещение'])", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalEntryMatchesExactPeriod(", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalFilteredEntries(data)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalRebuildDays(data, entries)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalStatsHeaderHtml()", BOARD_WEB_APP_HTML)
        self.assertIn("function applyCashJournalStatsPeriodFilter(", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalLedgerParts(data)", BOARD_WEB_APP_HTML)
        self.assertIn("function refreshCashJournalLedgerBody()", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCashJournalLoadMoreClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("totalRowCount", BOARD_WEB_APP_HTML)
        self.assertNotIn("function renderCashJournalFilters(data)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function handleCashJournalFilterInput(event)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function handleCashJournalResetClick(event)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function handleCashJournalBalancesToggle(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCashJournalModeKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCashJournalModeClick(event)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function handleCashJournalPeriodClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCashJournalStatsPeriodClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function syncCashJournalModeButtons()", BOARD_WEB_APP_HTML)
        self.assertIn("function setCashJournalView(view)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashJournalDisplayRows(entries)", BOARD_WEB_APP_HTML)
        self.assertIn('data-cash-journal-view="journal"', BOARD_WEB_APP_HTML)
        self.assertIn('data-cash-journal-view="stats"', BOARD_WEB_APP_HTML)
        self.assertNotIn("data-cash-journal-period=", BOARD_WEB_APP_HTML)
        self.assertNotIn("data-cash-journal-clear-filter=", BOARD_WEB_APP_HTML)
        self.assertIn("data-cash-journal-compact-day=", BOARD_WEB_APP_HTML)
        self.assertIn("data-cash-journal-period-kind=", BOARD_WEB_APP_HTML)
        self.assertIn("data-cash-journal-period-key=", BOARD_WEB_APP_HTML)
        self.assertIn('aria-controls="cashboxJournalText"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cashboxJournalLedgerButton.addEventListener('click', handleCashJournalModeClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxJournalStatsButton.addEventListener('click', handleCashJournalModeClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("opening_balances", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "els.cashboxJournalText.addEventListener('input', handleCashJournalFilterInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "els.cashboxJournalText.addEventListener('change', handleCashJournalFilterInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "els.cashboxJournalText.addEventListener('click', handleCashJournalResetClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "els.cashboxJournalText.addEventListener('click', handleCashJournalPeriodClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxJournalText.addEventListener('click', handleCashJournalStatsPeriodClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn(
            "els.cashboxJournalText.addEventListener('click', handleCashJournalBalancesToggle);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxJournalText.innerHTML = renderCashJournal(data);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("async function downloadCashJournal()", BOARD_WEB_APP_HTML)
        self.assertIn(".card-journal-view {", BOARD_WEB_APP_HTML)
        self.assertIn("data?.markdown || data?.text", BOARD_WEB_APP_HTML)
        self.assertIn("text/markdown;charset=utf-8", BOARD_WEB_APP_HTML)
        self.assertIn("'.md'", BOARD_WEB_APP_HTML)
        self.assertIn("function filteredCashboxTransactions()", BOARD_WEB_APP_HTML)
        self.assertNotIn("async function createCashbox()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function reorderCashboxes(cashboxId, beforeCashboxId = '')", BOARD_WEB_APP_HTML
        )
        self.assertIn("async function createCashboxTransfer()", BOARD_WEB_APP_HTML)
        self.assertIn("async function createCashboxTransaction(direction)", BOARD_WEB_APP_HTML)
        self.assertIn("const CASHBOX_EXPENSE_NOTE_MIN_LENGTH = 10;", BOARD_WEB_APP_HTML)
        self.assertIn("function setCashboxNoteInvalid(isInvalid)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashboxExpenseNoteIsValid(note)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cashboxNoteInput.setAttribute('aria-invalid', 'true');", BOARD_WEB_APP_HTML
        )
        self.assertIn("els.cashboxNoteInput.focus();", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cashboxNoteInput.addEventListener('input', handleCashboxNoteInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function openCashboxCancelPopover(transactionId)", BOARD_WEB_APP_HTML)
        self.assertIn("async function submitCashboxTransactionCancellation()", BOARD_WEB_APP_HTML)
        self.assertIn("CASHBOX_CANCEL_REASON_MIN_LENGTH", BOARD_WEB_APP_HTML)
        self.assertIn("data-cashbox-transaction-cancel", BOARD_WEB_APP_HTML)
        self.assertIn("function invalidateCashboxesCache()", BOARD_WEB_APP_HTML)
        self.assertIn("function cashboxMutationPath(path)", BOARD_WEB_APP_HTML)
        self.assertIn("notifyCashboxesMutation(path, request.method);", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/update_repair_order',", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/create_employee_salary_transaction',", BOARD_WEB_APP_HTML)
        self.assertIn('class="btn btn--accent" id="employeeSalaryPayoutButton"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="btn btn--accent" id="employeeSalaryAdvanceButton"', BOARD_WEB_APP_HTML
        )
        self.assertIn('id="employeeSalaryAdvanceDialog"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryAdvanceAmountInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryAdvanceCashboxSelect"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryAdvanceCommentInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryAdvanceConfirmButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryAdvanceCancelButton"', BOARD_WEB_APP_HTML)
        self.assertIn("КОММЕНТАРИЙ", BOARD_WEB_APP_HTML)
        self.assertIn("function renderEmployeeSalaryAdvanceDialog()", BOARD_WEB_APP_HTML)
        self.assertIn("employeeSalaryAdvanceOpen: false,", BOARD_WEB_APP_HTML)
        self.assertIn("employeeSalaryAdvanceNoteDraft: '',", BOARD_WEB_APP_HTML)
        self.assertIn("handleEmployeeSalaryAdvanceConfirm()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function refreshCashboxesAfterMoneyMutation({ openModal = false, deferDetail = true } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "await refreshCashboxesAfterMoneyMutation({ openModal: true, deferDetail: false });",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "await refreshCashboxesAfterMoneyMutation({ deferDetail: true });",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "state.cashboxesLoaded && Array.isArray(state.cashboxes) && state.cashboxes.length",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "async function loadCashboxes(openModal = false, { deferDetail = false } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("const CASHBOX_DETAIL_DEFER_DELAY_MS = 120;", BOARD_WEB_APP_HTML)
        self.assertIn("cashboxesLoaded: false,", BOARD_WEB_APP_HTML)
        self.assertIn("function scheduleCashboxDetailLoad(", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (!els.cashboxesModal?.classList.contains('is-open')) return;", BOARD_WEB_APP_HTML
        )
        self.assertIn("if (state.cashboxesLoaded) {", BOARD_WEB_APP_HTML)
        self.assertIn("loadCashboxes(false, { deferDetail: true });", BOARD_WEB_APP_HTML)
        self.assertIn("scheduleCashboxDetailLoad(nextId, { openModal });", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function loadCashboxDetail(cashboxId, { openModal = false, offset = 0, append = false, loadContext = null } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const transactionOffset = finiteNonNegativeNumber(offset);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "'&transaction_limit=' + CASHBOX_TRANSACTION_PAGE_SIZE + '&transaction_offset=' + transactionOffset + '&compact=true'",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const canAppend = append && state.activeCashbox?.cashbox?.id === (data?.cashbox?.id || normalizedId);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("async function loadMoreCashboxTransactions()", BOARD_WEB_APP_HTML)
        self.assertIn("offset: filteredCashboxTransactions().length,", BOARD_WEB_APP_HTML)
        self.assertIn("append: true,", BOARD_WEB_APP_HTML)
        self.assertIn("data-cashbox-transactions-load-more", BOARD_WEB_APP_HTML)
        self.assertIn("function cashboxTransactionCanBeCancelled(item)", BOARD_WEB_APP_HTML)
        self.assertIn("function cashboxTransactionIsTransfer(item)", BOARD_WEB_APP_HTML)
        self.assertIn("note.toLowerCase().startsWith('перемещение')", BOARD_WEB_APP_HTML)
        self.assertNotIn("перемещение\x08", BOARD_WEB_APP_HTML)
        self.assertIn("function resetCashboxDragState()", BOARD_WEB_APP_HTML)
        self.assertIn("function syncCashboxDragClasses()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cashboxesList.classList.toggle('is-drag-active', isDragActive);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("cashboxId !== state.cashboxDragId", BOARD_WEB_APP_HTML)
        self.assertIn("function cashboxDropBeforeIdFromRow(row, clientY)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCashboxesListDragStart(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCashboxesListDragOver(event)", BOARD_WEB_APP_HTML)
        self.assertIn("async function handleCashboxesListDrop(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCashboxesListDragEnd()", BOARD_WEB_APP_HTML)
        self.assertIn("const rounded = Math.round(Math.abs(amount) / 100);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "maximumFractionDigits: 0,",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("function renderCashboxStats()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cashboxesButton.addEventListener('click', openCashboxesModal);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.cashboxJournalButton.addEventListener('click', openCashJournalModal);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn('id="cashboxFinanceAuditButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="cashboxJournalAuditButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn(">СВЕРКА<", BOARD_WEB_APP_HTML)
        self.assertNotIn("Финансовая сверка", BOARD_WEB_APP_HTML)
        self.assertNotIn("function renderFinanceAudit(data)", BOARD_WEB_APP_HTML)
        self.assertNotIn("async function openFinanceAuditModal()", BOARD_WEB_APP_HTML)
        self.assertNotIn("async function applyFinanceAuditSafeFixes()", BOARD_WEB_APP_HTML)
        self.assertNotIn("handleFinanceAuditClick", BOARD_WEB_APP_HTML)
        self.assertNotIn("finance-audit-view", BOARD_WEB_APP_HTML)
        self.assertNotIn("требуется сверка", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cashboxJournalDownloadButton.addEventListener('click', downloadCashJournal);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxJournalText.addEventListener('click', handleCashJournalLoadMoreClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxTransferButton.addEventListener('click', createCashboxTransfer);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxCancelConfirmButton?.addEventListener('click', submitCashboxTransactionCancellation);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxTransferConfirmButton.addEventListener('click', submitCashboxTransfer);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxTransactions.addEventListener('click', handleCashboxTransactionsClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxesList.addEventListener('dragstart', handleCashboxesListDragStart);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxesList.addEventListener('dragover', handleCashboxesListDragOver);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxesList.addEventListener('drop', handleCashboxesListDrop);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxesList.addEventListener('dragend', handleCashboxesListDragEnd);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('data-close="cashboxes"', BOARD_WEB_APP_HTML)
        self.assertIn('data-close="cashbox-journal"', BOARD_WEB_APP_HTML)
        self.assertIn('data-close="cashbox-transfer"', BOARD_WEB_APP_HTML)
        self.assertNotIn('data-cash-journal-filter="query"', BOARD_WEB_APP_HTML)
        self.assertNotIn('data-cash-journal-filter="cashbox"', BOARD_WEB_APP_HTML)
        self.assertNotIn('data-cash-journal-filter="type"', BOARD_WEB_APP_HTML)
        self.assertNotIn('data-cash-journal-filter="period"', BOARD_WEB_APP_HTML)
        self.assertNotIn("data-cash-journal-reset", BOARD_WEB_APP_HTML)
        self.assertNotIn('aria-label="Сбросить фильтры"', BOARD_WEB_APP_HTML)
        self.assertNotIn(">Сбросить</button>", BOARD_WEB_APP_HTML)
        self.assertNotIn(".cashbox-journal-filter-reset[hidden]", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            'data-cash-journal-reset title="Сбросить фильтры" aria-label="Сбросить фильтры">×</button>',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('aria-label="Операция кассы ', BOARD_WEB_APP_HTML)
        self.assertIn('title="Отменить платеж"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransferPreview"', BOARD_WEB_APP_HTML)
        self.assertIn("function renderCashboxTransferPreview(", BOARD_WEB_APP_HTML)
        self.assertNotIn('data-cash-journal-region="toolbar"', BOARD_WEB_APP_HTML)
        self.assertNotIn('data-cash-journal-region="active-filters"', BOARD_WEB_APP_HTML)
        self.assertIn('data-cash-journal-region="body"', BOARD_WEB_APP_HTML)
        self.assertNotIn("Остатки на начало дня", BOARD_WEB_APP_HTML)
        self.assertIn("Журнал</button>", BOARD_WEB_APP_HTML)
        self.assertIn("Сводка</button>", BOARD_WEB_APP_HTML)
        self.assertNotIn(">Журнал / Сводка<", BOARD_WEB_APP_HTML)
        self.assertNotIn("cashbox-journal-toolbar__title", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "+ ' из ' + escapeHtml(String(filteredRowCount - renderedRowCount))", BOARD_WEB_APP_HTML
        )
        self.assertNotIn("function cashJournalMetricHtml(label, value", BOARD_WEB_APP_HTML)
        self.assertNotIn('id="cashboxNameInput"', BOARD_WEB_APP_HTML)
        self.assertNotIn("ОТКУДА", BOARD_WEB_APP_HTML)
        self.assertNotIn("КУДА ПЕРЕВЕСТИ", BOARD_WEB_APP_HTML)
        self.assertIn("Баланс:", BOARD_WEB_APP_HTML)
        self.assertNotIn("1000 или 1000,50", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const yy = String(date.getFullYear() % 100).padStart(2, '0');", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "return dd + '.' + mm + '.' + yy + ', ' + hh + ':' + min;", BOARD_WEB_APP_HTML
        )
        self.assertNotIn("window.prompt('Куда перевести деньги?", BOARD_WEB_APP_HTML)

    def test_cashbox_cancellation_exposes_inline_feedback(self) -> None:
        self.assertIn('id="cashboxCancelFeedback"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "function setCashboxCancelFeedback(message = '', isError = false)", BOARD_WEB_APP_HTML
        )
        self.assertIn("setCashboxCancelFeedback('Отменяю операцию…');", BOARD_WEB_APP_HTML)

    def test_inventory_module_exposes_minimal_warehouse_workspace(self) -> None:
        self.assertIn('id="inventoryButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inventoryModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inventorySearchInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inventoryPositionsTab"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inventoryMovementsTab"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inventoryStockFilter"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inventoryItemsList"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inventoryTableBody"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inventoryMovementsBody"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inventoryMovementsRefreshButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inventorySaveButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="inventoryReplenishButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderInventoryToggleButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderInventoryPanel"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderInventorySearchInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderInventoryStock"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderInventoryIssueButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderInventoryReturnButton"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-row-field="inventory_item_id"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-row-field="inventory_movement_id"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-row-field="inventory_unit"', BOARD_WEB_APP_HTML)
        self.assertIn('class="inventory-workspace"', BOARD_WEB_APP_HTML)
        self.assertIn('class="inventory-table"', BOARD_WEB_APP_HTML)
        self.assertIn(".inventory-layout {", BOARD_WEB_APP_HTML)
        self.assertIn(".inventory-movements-table {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-inventory-panel {", BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="repair-order-inventory-actions repair-order-inventory-actions--sticky"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("inventoryLoaded: false,", BOARD_WEB_APP_HTML)
        self.assertIn("inventoryView: 'positions',", BOARD_WEB_APP_HTML)
        self.assertIn("inventoryStockFilter: 'all',", BOARD_WEB_APP_HTML)
        self.assertIn("repairOrderInventoryOpen: false,", BOARD_WEB_APP_HTML)
        self.assertIn("async function loadInventoryItems", BOARD_WEB_APP_HTML)
        self.assertIn("async function loadInventoryMovements", BOARD_WEB_APP_HTML)
        self.assertIn("async function saveInventoryItem", BOARD_WEB_APP_HTML)
        self.assertIn("async function replenishInventoryItem", BOARD_WEB_APP_HTML)
        self.assertIn("async function writeOffInventoryItem", BOARD_WEB_APP_HTML)
        self.assertIn("async function returnInventoryMovement", BOARD_WEB_APP_HTML)
        self.assertIn("function renderInventoryMovements", BOARD_WEB_APP_HTML)
        self.assertIn("function renderRepairOrderInventoryPanel", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.inventoryPositionsTab?.addEventListener('click'",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.inventoryMovementsTab?.addEventListener('click'",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.inventoryStockFilter?.addEventListener('change'",
            BOARD_WEB_APP_HTML,
        )
        for route in (
            "/api/list_inventory_items",
            "/api/list_inventory_movements",
            "/api/search_inventory_items",
            "/api/save_inventory_item",
            "/api/replenish_inventory_item",
            "/api/write_off_inventory_item",
            "/api/return_inventory_movement",
        ):
            self.assertIn(route, BOARD_WEB_APP_HTML)
        self.assertIn(
            "const MOBILE_VIEW_ORDER = ['board', 'cashboxes', 'inventory', 'repair-orders', 'more'];",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('data-mobile-view="inventory"', BOARD_WEB_APP_HTML)
        self.assertIn('id="mobileInventoryNewButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="mobileInventoryStatusLine"', BOARD_WEB_APP_HTML)
        self.assertIn('id="mobileInventoryRecentMovements"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.mobileInventoryNewButton?.addEventListener('click', resetInventoryForm);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function resetInventoryForm() {\n      state.inventoryActiveId = '';\n      renderInventory();",
            BOARD_WEB_APP_HTML,
        )

    def test_modal_data_loader_helpers_drive_active_archive_and_gpt_paths(self) -> None:
        self.assertIn("function maybeOpenModal(modalEl, openModal)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderLogs(payload)", BOARD_WEB_APP_HTML)
        self.assertIn("function buildCardJournalFallbackText(events)", BOARD_WEB_APP_HTML)
        self.assertIn("function buildCardJournalHtml(payload)", BOARD_WEB_APP_HTML)
        self.assertIn("function cardJournalEventSentence(entry)", BOARD_WEB_APP_HTML)
        self.assertIn("function cardJournalBlockParts(block)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderCardJournalBlock(block)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderCardJournalDetails(entry)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderCardJournalDetailLines(detailLines)", BOARD_WEB_APP_HTML)
        self.assertIn(".card-journal-block {", BOARD_WEB_APP_HTML)
        self.assertIn(".card-journal-entry__sentence {", BOARD_WEB_APP_HTML)
        self.assertIn(".card-journal-header__meta {", BOARD_WEB_APP_HTML)
        self.assertIn("const blockParts = renderCardJournalDetails(entry);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "inlineHtml: inlineText ? ' <span class=\"card-journal-detail\">'",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const totalLabel = cardJournalCountText(total, 'событие', 'события', 'событий');",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("function renderCardJournalStat(label, value)", BOARD_WEB_APP_HTML)
        self.assertNotIn("card-journal-stats", BOARD_WEB_APP_HTML)
        self.assertNotIn("card-journal-stat", BOARD_WEB_APP_HTML)
        self.assertNotIn("card-journal-day__head", BOARD_WEB_APP_HTML)
        self.assertNotIn("card-journal-day__meta", BOARD_WEB_APP_HTML)
        self.assertNotIn("function cardJournalGroupSummary(group)", BOARD_WEB_APP_HTML)
        self.assertNotIn("card-journal-entry__title", BOARD_WEB_APP_HTML)
        self.assertNotIn("Было до изменения", BOARD_WEB_APP_HTML)
        self.assertNotIn("Стало после изменения", BOARD_WEB_APP_HTML)
        self.assertIn(
            "data?.markdown || data?.text || buildCardJournalFallbackText", BOARD_WEB_APP_HTML
        )
        self.assertIn("els.logList.className = 'card-journal-view';", BOARD_WEB_APP_HTML)
        self.assertIn("els.logList.innerHTML = buildCardJournalHtml(data);", BOARD_WEB_APP_HTML)
        self.assertIn("els.logList.textContent = text;", BOARD_WEB_APP_HTML)
        self.assertIn("async function openArchiveModal()", BOARD_WEB_APP_HTML)
        self.assertIn("await loadArchive(true);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function loadArchive(openModal = false, { force = false } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("state.archiveCards = [];", BOARD_WEB_APP_HTML)
        self.assertIn("function handleBoardScaleInput()", BOARD_WEB_APP_HTML)
        self.assertIn("async function resetBoardScaleToDefault()", BOARD_WEB_APP_HTML)
        self.assertIn("async function persistBoardScaleChange()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const BOARD_SCALE_STORAGE_KEY_PREFIX = 'kanban-board-scale:';", BOARD_WEB_APP_HTML
        )
        self.assertIn("function boardScaleStorageKey(actor = state.actor)", BOARD_WEB_APP_HTML)
        self.assertIn("function readStoredBoardScale(actor = state.actor)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function persistStoredBoardScale(value, actor = state.actor)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function applyBoardScalePreference(", BOARD_WEB_APP_HTML)
        self.assertIn("function openBoardSettings()", BOARD_WEB_APP_HTML)
        self.assertIn("function refreshGptWallView()", BOARD_WEB_APP_HTML)
        self.assertIn("async function createColumnFromBoard()", BOARD_WEB_APP_HTML)
        self.assertIn("function closeNamedModal(closeKey)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function loadModalData(path, { method = 'GET', body = null, openModal = false, modalEl = null, onSuccess, onError } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "async function reloadOperatorAdminUsers({ openModal = false } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("loadGptWall = async function(openModal = false)", BOARD_WEB_APP_HTML)
        self.assertIn("renderCompactArchiveRows(cards)", BOARD_WEB_APP_HTML)
        self.assertIn("renderRepairOrderListRows(items)", BOARD_WEB_APP_HTML)
        self.assertIn("repairOrdersMetaText = function(items, meta)", BOARD_WEB_APP_HTML)
        self.assertIn("function gptWallMetaText(meta)", BOARD_WEB_APP_HTML)
        self.assertIn("function normalizeGptWallView(value)", BOARD_WEB_APP_HTML)
        self.assertIn("function buildReadableGptWallEvents(data)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderGptWallView()", BOARD_WEB_APP_HTML)
        self.assertIn('id="gptWallBoardTab"', BOARD_WEB_APP_HTML)
        self.assertIn('id="gptWallEventsTab"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "function setModalListError(metaEl, listEl, metaText, bodyText)", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "function setModalTextError(metaEl, textEl, metaText, bodyText)", BOARD_WEB_APP_HTML
        )
        self.assertIn("lastSnapshotRevision: ''", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const previousRevision = String(state.lastSnapshotRevision || '');", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "const nextRevision = String(nextSnapshot?.meta?.revision || '');", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "const boardChanged = !previousRevision || !nextRevision || previousRevision !== nextRevision;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("if (boardChanged) {", BOARD_WEB_APP_HTML)
        self.assertIn("state.lastSnapshotRevision = nextRevision;", BOARD_WEB_APP_HTML)
        self.assertIn("function buildBoardCardsByColumn(snapshot)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function sortedCardsForBoardColumn(snapshot, columnId, cardsByColumn = null)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function renderBoardColumnHtml(column, index, snapshot, cardsByColumn = null)",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "function renderBoardColumnById(columnId, cardsByColumn = null)", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "const cardsByColumn = buildBoardCardsByColumn(snapshot);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "renderBoardColumnHtml(column, index, snapshot, cardsByColumn)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function boardCardElementById(cardId)", BOARD_WEB_APP_HTML)
        self.assertIn("function replaceBoardCardElement(nextCard)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function applyBoardColumnCardsPatch(nextCards, affectedColumnIds)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function applyArchivedCardPatch(nextCard)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const previousCard = snapshotCardById(suppressedNextCard.id);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "if (previousColumnId && previousColumnId === nextColumnId) {", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "const samePosition = previousPosition === nextPosition || (Number.isNaN(previousPosition) && Number.isNaN(nextPosition));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "if (samePosition && replaceBoardCardElement(suppressedNextCard)) return;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("renderBoardColumnById(previousColumnId, cardsByColumn)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const patched = applyBoardColumnCardsPatch(data?.affected_cards || [], data?.affected_column_ids || []);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("if (!patched && data?.card) {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (data?.card && applyArchivedCardPatch(data.card)) return;", BOARD_WEB_APP_HTML
        )

    def test_web_assets_do_not_keep_duplicate_active_function_names(self) -> None:
        named_functions = re.findall(
            r"(?:^|\n)\s*(?:function\s+([A-Za-z_$][\w$]*)\s*\(|([A-Za-z_$][\w$]*)\s*=\s*function\s*\()",
            BOARD_WEB_APP_HTML,
        )
        counts: dict[str, int] = {}
        for declaration_name, assignment_name in named_functions:
            name = declaration_name or assignment_name
            counts[name] = counts.get(name, 0) + 1
        duplicates = {name: count for name, count in sorted(counts.items()) if count > 1}
        self.assertEqual(duplicates, {})

        self.assertEqual(BOARD_WEB_APP_HTML.count("function buildVehicleAutofillRawText()"), 0)
        self.assertEqual(BOARD_WEB_APP_HTML.count("function refreshVehiclePanel()"), 1)
        self.assertEqual(BOARD_WEB_APP_HTML.count("async function saveCard()"), 1)
        self.assertEqual(
            BOARD_WEB_APP_HTML.count("repairOrdersMetaText = function(items, meta)"), 1
        )
        self.assertEqual(BOARD_WEB_APP_HTML.count("function renderRepairOrderRows(items)"), 0)
        self.assertEqual(
            BOARD_WEB_APP_HTML.count(
                "function renderRepairOrderRows(section, rows, { syncTotals = true } = {})"
            ),
            1,
        )
        self.assertEqual(BOARD_WEB_APP_HTML.count("renderRepairOrderListRows = function(items)"), 1)
        self.assertEqual(
            BOARD_WEB_APP_HTML.count("loadRepairOrders = async function(openModal = false)"), 1
        )
        self.assertIn("const closeTrigger = target.closest('[data-close]');", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (closeTrigger instanceof HTMLElement) {",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("closeNamedModal(closeTrigger.dataset.close);", BOARD_WEB_APP_HTML)
        self.assertIn("function bindDirectCardModalCloseButtons()", BOARD_WEB_APP_HTML)
        self.assertIn('id="cardModalCloseButtonTop"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cardModalCloseButtonBottom"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'onclick="window.__closeCardModal && window.__closeCardModal(); return false;"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "cardModalCloseButtonTop: document.getElementById('cardModalCloseButtonTop')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "cardModalCloseButtonBottom: document.getElementById('cardModalCloseButtonBottom')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "[els.cardModalCloseButtonTop, els.cardModalCloseButtonBottom].forEach((button) => {",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("event.stopPropagation();", BOARD_WEB_APP_HTML)
        self.assertIn("bindDirectCardModalCloseButtons();", BOARD_WEB_APP_HTML)
        self.assertIn("window.__closeCardModal = closeCardModal;", BOARD_WEB_APP_HTML)
        self.assertIn("popModal('repair-order-payments');", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.archiveButton.addEventListener('click', openArchiveModal);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.boardSettingsButton.addEventListener('click', openBoardSettings);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.boardScaleInput.addEventListener('input', handleBoardScaleInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.boardScaleInput.addEventListener('change', persistBoardScaleChange);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.boardScaleReset.addEventListener('click', resetBoardScaleToDefault);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("await api('/api/update_board_settings'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (!els.boardControlToggle && !els.boardControlIntervalInput && !els.boardControlCooldownInput) return null;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "if (aiBoardControl) body.ai_board_control = aiBoardControl;", BOARD_WEB_APP_HTML
        )
        self.assertNotIn("ai_board_control: aiBoardControl", BOARD_WEB_APP_HTML)
        self.assertNotIn('id="boardControlSettingsRow"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="boardControlToggle"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="boardControlIntervalInput"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="boardControlCooldownInput"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="gptWallButton"', BOARD_WEB_APP_HTML)
        self.assertNotIn(
            '<button class="btn btn--ghost" id="gptWallButton">СТЕНА</button>', BOARD_WEB_APP_HTML
        )
        self.assertNotIn("document.getElementById('gptWallButton')", BOARD_WEB_APP_HTML)
        self.assertNotIn(
            "els.gptWallButton.addEventListener('click', openGptWallModal);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.gptWallBoardTab.addEventListener('click', () => setGptWallView('board_content'));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.gptWallEventsTab.addEventListener('click', () => setGptWallView('event_log'));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.gptWallRefresh.addEventListener('click', refreshGptWallView);", BOARD_WEB_APP_HTML
        )
        self.assertIn("els.gptWallText.dataset.wallView = view;", BOARD_WEB_APP_HTML)
        self.assertIn("target.closest('[data-create-column]')", BOARD_WEB_APP_HTML)
        self.assertIn("await createColumnFromBoard();", BOARD_WEB_APP_HTML)

    def test_blob_helpers_drive_download_and_text_report_paths(self) -> None:
        self.assertIn(
            "function withObjectUrl(blob, callback, { revokeDelay = 1500 } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function attachmentRequestHeaders()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function fetchAttachmentBlob(url, { networkErrorMessage = 'НЕ УДАЛОСЬ ЗАГРУЗИТЬ ФАЙЛ. ПРОВЕРЬ СЕТЬ И ДОСТУП К ДОСКЕ.' } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function triggerBlobDownload(blob, fileName)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "triggerBlobDownload(blob, extractDownloadName(response, 'attachment.bin'));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("withObjectUrl(blob, (objectUrl) => {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const opened = window.open(objectUrl, '_blank', 'noopener');", BOARD_WEB_APP_HTML
        )

    def test_card_preview_clean_russian_labels_override_broken_legacy_copy(self) -> None:
        self.assertIn("Описание не указано", BOARD_WEB_APP_HTML)
        self.assertNotIn("СИГН", BOARD_WEB_APP_HTML)
        self.assertIn(".card__footer {", BOARD_WEB_APP_HTML)
        self.assertIn('class="card__footer"', BOARD_WEB_APP_HTML)
        self.assertIn("ФАЙЛЫ ", BOARD_WEB_APP_HTML)
        self.assertIn("ЖУРНАЛ ", BOARD_WEB_APP_HTML)
        self.assertIn('title="Не прочитано"', BOARD_WEB_APP_HTML)


if __name__ == "__main__":
    unittest.main()
