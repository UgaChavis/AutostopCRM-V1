# ruff: noqa: I001
from __future__ import annotations

import re
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
        if parent and parent[0] == "div" and parent[1] == "employees-layout" and tag == "div":
            self.layout_children.append(class_name)
        self.stack.append((tag, class_name))

    def handle_endtag(self, tag: str) -> None:
        while self.stack:
            stack_tag, _ = self.stack.pop()
            if stack_tag == tag:
                break


class WebAssetsTests(unittest.TestCase):
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

    def test_board_settings_keep_slider_but_remove_wheel_zoom_binding(self) -> None:
        self.assertIn('class="gear-button" id="boardSettingsButton"', BOARD_WEB_APP_HTML)
        self.assertIn('class="gear-button__logo" src="/favicon.png"', BOARD_WEB_APP_HTML)
        self.assertIn(".gear-button {", BOARD_WEB_APP_HTML)
        self.assertIn("width: 48px;", BOARD_WEB_APP_HTML)
        self.assertIn(".gear-button__logo {", BOARD_WEB_APP_HTML)
        self.assertIn("width: 28px;", BOARD_WEB_APP_HTML)
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
        self.assertIn("width: max-content;", BOARD_WEB_APP_HTML)
        self.assertIn(".topbar__actions .btn {", BOARD_WEB_APP_HTML)

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
        self.assertIn('class="tag-suggestions" id="tagSuggestions"', BOARD_WEB_APP_HTML)
        self.assertIn('class="tag-entry"', BOARD_WEB_APP_HTML)
        self.assertIn('class="tag-controls"', BOARD_WEB_APP_HTML)
        self.assertIn('class="field field--tags"', BOARD_WEB_APP_HTML)
        self.assertIn('id="tagMeta"', BOARD_WEB_APP_HTML)
        self.assertIn("МЕТОК НЕТ", BOARD_WEB_APP_HTML)
        self.assertIn(".column > * {", BOARD_WEB_APP_HTML)

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
        self.assertIn("async function deleteColumnFromButton(button)", BOARD_WEB_APP_HTML)
        self.assertIn("Удалить пустой столбец", BOARD_WEB_APP_HTML)
        self.assertIn("window.confirm('Удалить пустой столбец", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/delete_column'", BOARD_WEB_APP_HTML)
        self.assertIn("await deleteColumnFromButton(deleteColumnButton);", BOARD_WEB_APP_HTML)

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
        self.assertIn("if (!document.hidden) refreshSnapshot(false);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "document.addEventListener('visibilitychange', handleSnapshotVisibilityChange);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("state.pollHandle = window.setTimeout(async () => {", BOARD_WEB_APP_HTML)
        self.assertIn("await refreshSnapshot(false);", BOARD_WEB_APP_HTML)
        self.assertIn("scheduleNextSnapshotPoll();", BOARD_WEB_APP_HTML)
        self.assertIn("const SNAPSHOT_POLL_MODAL_INTERVAL_MS = 15000;", BOARD_WEB_APP_HTML)
        self.assertIn("function hasOpenWorkspaceModal()", BOARD_WEB_APP_HTML)

    def test_archive_modal_uses_last_30_compact_rows(self) -> None:
        self.assertIn("АРХИВ / ПОСЛЕДНИЕ 30", BOARD_WEB_APP_HTML)
        self.assertIn("/api/get_board_snapshot?compact=1&include_archive=0", BOARD_WEB_APP_HTML)
        self.assertIn(
            "/api/list_archived_cards?limit=' + ARCHIVE_PREVIEW_LIMIT + '&compact=1",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(".archive-row--compact {", BOARD_WEB_APP_HTML)
        self.assertIn(".archive-row__summary {", BOARD_WEB_APP_HTML)
        self.assertIn("renderArchive = function() {", BOARD_WEB_APP_HTML)
        self.assertIn("compactDescription.length > 180", BOARD_WEB_APP_HTML)
        self.assertIn("archive-row archive-row--compact", BOARD_WEB_APP_HTML)
        self.assertIn("await restoreCard(target.dataset.restoreCard);", BOARD_WEB_APP_HTML)

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
        self.assertIn('id="employeeSalaryModeInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeNoteDetails"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesSummaryStrip"', BOARD_WEB_APP_HTML)
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
        self.assertIn("await loadEmployeesReference();", BOARD_WEB_APP_HTML)
        self.assertIn("employeesLoadedMonth: ''", BOARD_WEB_APP_HTML)
        self.assertIn("employeesReferencePromise: null", BOARD_WEB_APP_HTML)
        self.assertIn("state.employeesLoadedMonth = month;", BOARD_WEB_APP_HTML)
        self.assertIn("await loadPayrollReport();", BOARD_WEB_APP_HTML)
        self.assertIn("renderEmployeesWorkspace();", BOARD_WEB_APP_HTML)
        self.assertIn(
            "employee.balance_total ?? summary?.balance_total ?? summary?.total_salary",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function renderEmployeeProfileMeta()", BOARD_WEB_APP_HTML)
        self.assertIn("function handleEmployeesDetailClick(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function syncEmployeeSalaryModeUi()", BOARD_WEB_APP_HTML)
        self.assertIn("function syncEmployeesReportPanelUi()", BOARD_WEB_APP_HTML)
        self.assertIn("function hydrateEmployeesUiRefs()", BOARD_WEB_APP_HTML)
        self.assertIn("function bindEmployeesUiEvents()", BOARD_WEB_APP_HTML)
        self.assertIn("function addEmployeeFromForm()", BOARD_WEB_APP_HTML)
        self.assertIn("employeeCreateMode: false", BOARD_WEB_APP_HTML)
        self.assertIn("employeesReportDetailsOpen: false", BOARD_WEB_APP_HTML)
        self.assertIn("state.employeeCreateMode = true;", BOARD_WEB_APP_HTML)
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
        self.assertNotIn('id="employeeSaveButton"', BOARD_WEB_APP_HTML)
        self.assertIn(".employees-layout {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "grid-template-columns: minmax(360px, 390px) minmax(0, 1fr);", BOARD_WEB_APP_HTML
        )
        self.assertIn(".employees-list-tools {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-search {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-filterbar {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-list-meta,", BOARD_WEB_APP_HTML)
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
        self.assertIn(".employees-row__salary,\n    .employees-row__report {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-card-head {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-field--compact {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-field--salary {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-field--percent {", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-form-grid {", BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-columns: repeat(12, minmax(0, 1fr));", BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="field employees-field--span-2 employees-field--compact employees-field--salary"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="field employees-field--span-2 employees-field--compact employees-field--percent"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="field employees-field--span-6 employees-field--compact employees-field--mode"',
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn('id="employeesSummaryPanel"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="employeesSummaryTable"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesDetailsPanel"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesReportShell"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesReportMeta"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesDetailsMeta"', BOARD_WEB_APP_HTML)
        self.assertIn("Выберите сотрудника слева, чтобы открыть детализацию.", BOARD_WEB_APP_HTML)
        self.assertIn("Детализация появится после выбора сотрудника.", BOARD_WEB_APP_HTML)
        self.assertIn('placeholder="0"', BOARD_WEB_APP_HTML)
        self.assertIn('data-employee-salary="', BOARD_WEB_APP_HTML)
        self.assertIn('data-employee-report="', BOARD_WEB_APP_HTML)
        self.assertIn("К ВЫПЛАТЕ", BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalaryTitle"', BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeSalarySummary"', BOARD_WEB_APP_HTML)
        self.assertIn("function openEmployeeSalaryReport(", BOARD_WEB_APP_HTML)
        self.assertIn("bindEmployeesUiEvents();", BOARD_WEB_APP_HTML)
        self.assertIn('id="employeesCreateButton"', BOARD_WEB_APP_HTML)
        self.assertIn(">ДОБАВИТЬ<", BOARD_WEB_APP_HTML)
        self.assertIn('id="employeeDeleteButton"', BOARD_WEB_APP_HTML)
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
        self.assertIn('data-repair-order-cell="executor_id"', BOARD_WEB_APP_HTML)
        self.assertIn("function repairOrderExecutorOptionsHtml", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.employeesButton.addEventListener('click', openEmployeesModal);", BOARD_WEB_APP_HTML
        )
        self.assertNotIn("function updateEmployeesListMeta()", BOARD_WEB_APP_HTML)

    def test_card_description_textarea_allows_extended_text(self) -> None:
        self.assertIn('id="cardDescription" maxlength="20000"', BOARD_WEB_APP_HTML)
        self.assertIn(".field--description textarea {", BOARD_WEB_APP_HTML)
        self.assertIn("min-height: 180px;", BOARD_WEB_APP_HTML)
        self.assertIn("height: 180px;", BOARD_WEB_APP_HTML)
        self.assertIn("max-height: clamp(480px, 62vh, 760px);", BOARD_WEB_APP_HTML)
        self.assertIn("function syncCardDescriptionHeight()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const minRows = text ? Math.max(8, Math.min(18, lineCount + 2)) : 7;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("window.innerHeight * 0.62", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cardDescription.addEventListener('input', syncCardDescriptionHeight);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "requestAnimationFrame(() => syncCardDescriptionHeight());", BOARD_WEB_APP_HTML
        )

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
        self.assertIn("parts.push(CARD_VEHICLE_FIELD_LABEL + ': ' + vehicle);", BOARD_WEB_APP_HTML)
        self.assertIn("parts.push(CARD_TITLE_FIELD_LABEL + ': ' + title);", BOARD_WEB_APP_HTML)
        self.assertIn("configureCardFieldSemantics();", BOARD_WEB_APP_HTML)

    def test_board_cards_show_five_lines_of_description_preview(self) -> None:
        self.assertIn(".card__desc {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: calc(13px * var(--board-scale));", BOARD_WEB_APP_HTML)
        self.assertIn("-webkit-line-clamp: 5;", BOARD_WEB_APP_HTML)
        self.assertIn("function boardCardDescription(card)", BOARD_WEB_APP_HTML)
        self.assertIn("card?.description_preview || card?.description", BOARD_WEB_APP_HTML)

    def test_card_modal_includes_centered_work_zone_and_separate_vehicle_panel(self) -> None:
        self.assertIn('class="dialog dialog--card"', BOARD_WEB_APP_HTML)
        self.assertIn(".dialog--card {", BOARD_WEB_APP_HTML)
        self.assertIn('class="dialog__head dialog__head--card"', BOARD_WEB_APP_HTML)
        self.assertIn('class="dialog__tabs dialog__tabs--card"', BOARD_WEB_APP_HTML)
        self.assertIn(".dialog__title--card {", BOARD_WEB_APP_HTML)
        self.assertIn("text-overflow: ellipsis;", BOARD_WEB_APP_HTML)
        self.assertIn("function limitCardModalHeading(value, maxLength = 92)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "grid-template-columns: minmax(648px, 756px) minmax(264px, 308px);", BOARD_WEB_APP_HTML
        )
        self.assertIn('class="subpanel vehicle-panel"', BOARD_WEB_APP_HTML)
        self.assertIn("z-index: 2;", BOARD_WEB_APP_HTML)
        self.assertIn("isolation: isolate;", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-panel::before {", BOARD_WEB_APP_HTML)
        self.assertIn('id="vehiclePanelSummary"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="vehiclePanelFlags"', BOARD_WEB_APP_HTML)
        self.assertIn('id="vehicleProfileFields"', BOARD_WEB_APP_HTML)
        self.assertIn(".overview-main__meta {", BOARD_WEB_APP_HTML)
        self.assertIn(".field--description textarea {", BOARD_WEB_APP_HTML)
        self.assertIn(".signal-panel {", BOARD_WEB_APP_HTML)
        self.assertIn(".tag-entry {", BOARD_WEB_APP_HTML)
        self.assertIn("function applyCardModalState(card)", BOARD_WEB_APP_HTML)
        self.assertIn("function resetCardModalState()", BOARD_WEB_APP_HTML)
        self.assertIn("async function persistCardPayload(payload)", BOARD_WEB_APP_HTML)
        self.assertIn("state.cardCreateColumnId = ''", BOARD_WEB_APP_HTML)
        self.assertIn("state.cardSaveInFlight = false", BOARD_WEB_APP_HTML)
        self.assertIn("if (state.cardSaveInFlight) return;", BOARD_WEB_APP_HTML)
        self.assertIn("state.cardSaveInFlight = true;", BOARD_WEB_APP_HTML)
        self.assertIn("state.cardCreateColumnId || state.activeCard?.column", BOARD_WEB_APP_HTML)
        self.assertIn('id="cardButton" type="button"', BOARD_WEB_APP_HTML)
        self.assertIn('data-create-in="', BOARD_WEB_APP_HTML)
        self.assertIn('id="saveCardButton" type="button"', BOARD_WEB_APP_HTML)
        self.assertNotIn("openCardModal(cachedCard);", BOARD_WEB_APP_HTML)
        self.assertNotIn("applyCardModalState(cachedCard);", BOARD_WEB_APP_HTML)
        self.assertIn("const data = await api('/api/open_card'", BOARD_WEB_APP_HTML)
        self.assertIn("requestAnimationFrame(() => renderFiles(currentCard));", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (els.cardModal?.classList.contains('is-open')) {\n        requestAnimationFrame(() => syncCardDescriptionHeight());\n      }",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "async function openCardWorkspace(cardId, { closeModalEl = null, openCardModalEl = true, openRepairOrder = false, repairOrderParentLayer = '' } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function openNewCardInColumn(columnId)", BOARD_WEB_APP_HTML)
        self.assertIn("function openDefaultNewCard()", BOARD_WEB_APP_HTML)
        self.assertIn("async function archiveActiveCard()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (message.includes('открыт заказ-наряд')) window.alert(message);", BOARD_WEB_APP_HTML
        )
        self.assertIn("async function restoreActiveCard()", BOARD_WEB_APP_HTML)
        self.assertIn("async function handleCardWorkspaceClick(target)", BOARD_WEB_APP_HTML)
        self.assertIn("applyCardModalState(card);", BOARD_WEB_APP_HTML)
        self.assertIn("resetCardModalState();", BOARD_WEB_APP_HTML)
        self.assertIn("await persistCardPayload(payload);", BOARD_WEB_APP_HTML)
        self.assertIn("await openCardWorkspace(cardId);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const createInTrigger = target.closest('[data-create-in]');", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "if (createInTrigger instanceof HTMLElement) openNewCardInColumn(createInTrigger.dataset.createIn);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("if (await handleCardWorkspaceClick(target)) return;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.cardButton.addEventListener('click', openDefaultNewCard);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.archiveAction.addEventListener('click', archiveActiveCard);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.restoreAction.addEventListener('click', restoreActiveCard);", BOARD_WEB_APP_HTML
        )
        self.assertIn('class="dialog__foot dialog__foot--card"', BOARD_WEB_APP_HTML)
        self.assertIn('class="dialog__foot-group dialog__foot-group--danger"', BOARD_WEB_APP_HTML)
        self.assertIn('class="dialog__foot-group dialog__foot-group--main"', BOARD_WEB_APP_HTML)

    def test_vehicle_panel_exposes_only_minimal_profile_fields(self) -> None:
        self.assertIn('id="vehicleAutofillButton"', BOARD_WEB_APP_HTML)
        self.assertIn("function configureVehicleAutofillUi()", BOARD_WEB_APP_HTML)
        self.assertIn("function buildVehicleAutofillRawText()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "await copyVehicleFieldValue(target.dataset.copyVehicleField);", BOARD_WEB_APP_HTML
        )
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
        self.assertIn("function autofillVehicleProfile()", BOARD_WEB_APP_HTML)
        self.assertIn("vehicle_profile: vehicleProfile,", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function splitVehicleDisplayName(value, productionYear = null)", BOARD_WEB_APP_HTML
        )
        self.assertIn("profile.make_display = displayParts.make_display;", BOARD_WEB_APP_HTML)
        self.assertIn("profile.model_display = displayParts.model_display;", BOARD_WEB_APP_HTML)

    def test_vehicle_panel_hides_empty_summary_and_first_group_title(self) -> None:
        self.assertIn("VEHICLE_FIELD_GROUPS[0].title = '';", BOARD_WEB_APP_HTML)
        self.assertIn("Пробег", BOARD_WEB_APP_HTML)
        self.assertIn("Телефон клиента", BOARD_WEB_APP_HTML)
        self.assertIn("ФИО клиента", BOARD_WEB_APP_HTML)
        self.assertIn(
            "(group.title ? '<div class=\"vehicle-group__title\">' + escapeHtml(group.title) + '</div>' : '')",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "if (profile.mileage) summaryLines.push('Пробег: ' + profile.mileage);",
            BOARD_WEB_APP_HTML,
        )
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
        self.assertIn(".vehicle-panel__fields { max-height: none; }", BOARD_WEB_APP_HTML)
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
        self.assertIn(
            'body.is-mobile-lite .dialog__tabs--card .tab-btn[data-tab="journal"] {',
            BOARD_WEB_APP_HTML,
        )
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
        self.assertIn("clientPhoneMatchKeys", BOARD_WEB_APP_HTML)
        self.assertIn("clientPhoneSearchVariants", BOARD_WEB_APP_HTML)
        self.assertIn("clientDebtCard", BOARD_WEB_APP_HTML)
        self.assertIn("clientDebtValue", BOARD_WEB_APP_HTML)
        self.assertIn("const CLIENTS_INITIAL_LIMIT = 35;", BOARD_WEB_APP_HTML)
        self.assertIn("const CLIENTS_SEARCH_LIMIT = 50;", BOARD_WEB_APP_HTML)
        self.assertIn("clientsRequestSeq", BOARD_WEB_APP_HTML)
        self.assertIn("clientsMetaState", BOARD_WEB_APP_HTML)
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
        self.assertIn("data-client-vehicle-field=\"vin\"", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/delete_client_vehicle'", BOARD_WEB_APP_HTML)
        self.assertIn("sync_linked_cards: true", BOARD_WEB_APP_HTML)
        self.assertIn("client-match-item__vehicles", BOARD_WEB_APP_HTML)
        self.assertIn("НАЙДЕННЫЕ КЛИЕНТЫ И АВТОМОБИЛИ", BOARD_WEB_APP_HTML)
        self.assertIn("data-select-client-vehicle", BOARD_WEB_APP_HTML)
        self.assertIn("data-select-client-new-vehicle", BOARD_WEB_APP_HTML)
        self.assertIn("data-load-client-vehicles", BOARD_WEB_APP_HTML)
        self.assertIn("async function createClientFromCardSuggestion()", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/create_client'", BOARD_WEB_APP_HTML)
        self.assertIn("vehiclePayloadFromProfile(profile)", BOARD_WEB_APP_HTML)
        self.assertIn("await createClientFromCardSuggestion();", BOARD_WEB_APP_HTML)
        self.assertIn("return data;", BOARD_WEB_APP_HTML)
        self.assertIn("pendingCardClientVehicleId", BOARD_WEB_APP_HTML)
        self.assertIn("pendingCreateClientVehicleFromCard", BOARD_WEB_APP_HTML)
        self.assertIn("pendingCardClientId", BOARD_WEB_APP_HTML)
        self.assertNotIn("clientProfileMeta", BOARD_WEB_APP_HTML)
        self.assertIn(".clients-list-pane {", BOARD_WEB_APP_HTML)
        self.assertIn("height: min(88vh, 900px);", BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr);", BOARD_WEB_APP_HTML)
        self.assertIn("overflow: hidden;", BOARD_WEB_APP_HTML)
        self.assertIn("overflow: auto;", BOARD_WEB_APP_HTML)
        self.assertIn("display: flex;", BOARD_WEB_APP_HTML)
        self.assertIn("flex: 1 1 auto;", BOARD_WEB_APP_HTML)
        self.assertIn(
            "clientProfileTitle) els.clientProfileTitle.textContent = clientDisplayName(client);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('id="clientRequisitesDetails"', BOARD_WEB_APP_HTML)
        self.assertIn('id="clientMatchPanel"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'id="clientPhoneInput" type="text" maxlength="80" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'id="clientLegalNameInput" type="text" maxlength="160" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("async function openClientsModal()", BOARD_WEB_APP_HTML)
        self.assertIn("async function linkActiveCardToClient(clientId,", BOARD_WEB_APP_HTML)
        self.assertIn("async function loadClientSuggestionVehicles(clientId)", BOARD_WEB_APP_HTML)
        self.assertIn("const fullProfileLoaded = Array.isArray(state.clientSuggestionProfiles?.[client?.id]?.vehicles);", BOARD_WEB_APP_HTML)
        self.assertIn("const visibleVehicles = fullProfileLoaded ? vehicles : vehicles.slice(0, 3);", BOARD_WEB_APP_HTML)
        self.assertIn("const loadMore = !fullProfileLoaded && total > visibleVehicles.length", BOARD_WEB_APP_HTML)
        self.assertIn("const loadedProfiles = state.clientSuggestionProfiles || {};", BOARD_WEB_APP_HTML)
        self.assertIn("if (clientId && loadedProfiles[clientId]) profiles[clientId] = loadedProfiles[clientId];", BOARD_WEB_APP_HTML)
        self.assertIn("window.clearTimeout(state.clientSuggestTimer);", BOARD_WEB_APP_HTML)
        self.assertIn("state.clientSuggestTimer = null;", BOARD_WEB_APP_HTML)
        self.assertIn("if (trimmedQuery.length < 3 && queryDigits.length < 3)", BOARD_WEB_APP_HTML)
        self.assertIn("window.setTimeout(refreshClientSuggestionsForCard, 450)", BOARD_WEB_APP_HTML)
        self.assertIn("function clientSuggestionVehicleKey(vehicle)", BOARD_WEB_APP_HTML)
        self.assertIn("return cardId ? ('card:' + cardId) : '';", BOARD_WEB_APP_HTML)
        self.assertIn("function findClientSuggestionVehicle(vehicles, vehicleKey = '')", BOARD_WEB_APP_HTML)
        self.assertIn("async function ensureStableClientSuggestionVehicle(clientId, vehicle)", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/upsert_client_vehicle'", BOARD_WEB_APP_HTML)
        self.assertIn("selectedVehicle = await ensureStableClientSuggestionVehicle(clientId, selectedVehicle);", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/link_card_to_client'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/search_clients?query='", BOARD_WEB_APP_HTML)
        self.assertIn("client_vehicle_id: state.pendingCardClientVehicleId", BOARD_WEB_APP_HTML)

    def test_vehicle_panel_uses_larger_readable_typography(self) -> None:
        self.assertIn(".vehicle-panel__summary {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 11px;", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-group__title {", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-group--identity .vehicle-group__grid {", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-field__label label,", BOARD_WEB_APP_HTML)
        self.assertIn(".vehicle-copy {", BOARD_WEB_APP_HTML)

    def test_employees_modal_keeps_both_panes_inside_the_layout(self) -> None:
        start = BOARD_WEB_APP_HTML.index("+ '<div class=\"employees-layout\">'")
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
        self.assertIn("font-size: calc(15px * var(--board-scale));", BOARD_WEB_APP_HTML)
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
        self.assertIn('<div class="panel-title">ОБРАТНЫЙ ОТСЧЁТ</div>', BOARD_WEB_APP_HTML)
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
        self.assertIn(">&minus;</button>", BOARD_WEB_APP_HTML)
        self.assertIn(">+</button>", BOARD_WEB_APP_HTML)
        self.assertIn('id="signalDays" type="number" min="0" max="365"', BOARD_WEB_APP_HTML)
        self.assertIn('id="signalHours" type="number" min="0" max="23"', BOARD_WEB_APP_HTML)
        self.assertNotIn("signalDaysDisplay", BOARD_WEB_APP_HTML)
        self.assertNotIn("signalHoursDisplay", BOARD_WEB_APP_HTML)

    def test_card_preview_clamps_to_five_description_lines(self) -> None:
        self.assertIn(".card__desc {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: calc(13px * var(--board-scale));", BOARD_WEB_APP_HTML)
        self.assertIn("-webkit-line-clamp: 5;", BOARD_WEB_APP_HTML)

    def test_card_preview_uses_readable_russian_meta_labels(self) -> None:
        self.assertIn("БЕЗ МЕТОК", BOARD_WEB_APP_HTML)
        self.assertIn("Описание не указано", BOARD_WEB_APP_HTML)
        self.assertIn("СИГН", BOARD_WEB_APP_HTML)
        self.assertIn("ФАЙЛЫ ", BOARD_WEB_APP_HTML)
        self.assertIn("ЖУРНАЛ ", BOARD_WEB_APP_HTML)

    def test_red_deadline_indicator_pulses_subtly(self) -> None:
        self.assertIn('.lamp[data-indicator="red"] {', BOARD_WEB_APP_HTML)
        self.assertIn("animation: lamp-red-pulse 1.8s ease-in-out infinite;", BOARD_WEB_APP_HTML)
        self.assertIn("@keyframes lamp-red-pulse {", BOARD_WEB_APP_HTML)
        self.assertIn("@media (prefers-reduced-motion: reduce) {", BOARD_WEB_APP_HTML)

    def test_unread_cards_expose_corner_badge_and_hover_seen_flow(self) -> None:
        self.assertEqual(BOARD_WEB_APP_HTML.count("function cardHtml(card)"), 1)
        self.assertEqual(BOARD_WEB_APP_HTML.count("function renderCardHtml(card)"), 1)
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

    def test_updated_cards_expose_yellow_badge_and_hover_seen_flow(self) -> None:
        self.assertIn(".card__updated-badge {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "data-updated-unseen=\"' + (card.has_unseen_update ? 'true' : 'false') + '\"",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('title="Обновлено"', BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (currentCard && !currentCard.is_unread && !currentCard.has_unseen_update) return;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "if (!currentCard || (!currentCard.is_unread && !currentCard.has_unseen_update)) return;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const hasUpdatedMarker = card.dataset.updatedUnseen === 'true';", BOARD_WEB_APP_HTML
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
        self.assertIn("function attachmentIsPreviewable(attachment)", BOARD_WEB_APP_HTML)
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
        self.assertIn("'X-Operator-Session'", BOARD_WEB_APP_HTML)
        self.assertIn('id="operatorProfileModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="operatorAdminModal"', BOARD_WEB_APP_HTML)
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
            'input[type="text"], input[type="password"], input[type="search"], input[type="month"], textarea, select, input[type="number"]',
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
            "els.adminUsersList.addEventListener('click', handleAdminUsersListClick);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("'/api/login_operator'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/get_operator_profile'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/list_operator_users'", BOARD_WEB_APP_HTML)
        self.assertIn("'/api/get_operator_user_report?username='", BOARD_WEB_APP_HTML)
        self.assertIn("data-open-operator-report", BOARD_WEB_APP_HTML)
        self.assertIn("СТАТИСТИКА: 15 ДНЕЙ", BOARD_WEB_APP_HTML)
        self.assertIn("bootstrapOperatorSession();", BOARD_WEB_APP_HTML)

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
        self.assertIn("els.repairOrderModal.classList.add('is-open');", BOARD_WEB_APP_HTML)
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
            BOARD_WEB_APP_HTML.index("els.repairOrderModal.classList.add('is-open');"),
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
        self.assertIn("min-height: 112px;", BOARD_WEB_APP_HTML)
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
        self.assertIn('id="repairOrderAutofillButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderPrintButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderTagList"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderTagInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderTagAddButton"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-total="works"', BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-total="materials"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderPaymentMethod"', BOARD_WEB_APP_HTML)
        self.assertIn('<option value="card">На карту</option>', BOARD_WEB_APP_HTML)
        self.assertIn('class="repair-order-hidden-fields"', BOARD_WEB_APP_HTML)
        self.assertIn("document.getElementById('repairOrderPaymentsButton')", BOARD_WEB_APP_HTML)
        self.assertIn("document.getElementById('repairOrderPaymentsModal')", BOARD_WEB_APP_HTML)
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
        self.assertIn("ИТОГО ПО ЗАКАЗ-НАРЯДУ", BOARD_WEB_APP_HTML)
        self.assertIn("К ДОПЛАТЕ БЕЗНАЛ", BOARD_WEB_APP_HTML)
        self.assertIn("К ДОПЛАТЕ НАЛ", BOARD_WEB_APP_HTML)
        self.assertIn("Артикул / OEM", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-table__input {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 14.25px;", BOARD_WEB_APP_HTML)
        self.assertIn('[data-repair-order-cell="name"]', BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 17px;", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-table__select {", BOARD_WEB_APP_HTML)
        self.assertIn("font-size: 10.75px;", BOARD_WEB_APP_HTML)
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
        self.assertIn("function repairOrderTaxRate(value)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function repairOrderProjectedTaxesValue(subtotal, paymentMethod)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function repairOrderRowsTotalValue(", BOARD_WEB_APP_HTML)
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
        self.assertIn("const due = summary.base_remaining;", BOARD_WEB_APP_HTML)
        self.assertIn("function openRepairOrderPaymentsModal()", BOARD_WEB_APP_HTML)
        self.assertIn("function addRepairOrderPayment()", BOARD_WEB_APP_HTML)
        self.assertIn('data-repair-order-cell="catalog_number"', BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-total--subtotal {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-total--cashless-due {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-total--cash-due {", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-order-total--taxes {", BOARD_WEB_APP_HTML)
        self.assertIn("function addRepairOrderRowFromButton(section, event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleRepairOrderModalInput(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function renderRepairOrderTags()", BOARD_WEB_APP_HTML)
        self.assertIn("function editRepairOrderTag(label)", BOARD_WEB_APP_HTML)
        self.assertIn("function removeRepairOrderTag(label)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleRepairOrderTagInputKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function saveRepairOrderDraft()", BOARD_WEB_APP_HTML)
        self.assertIn("function printRepairOrderDraft()", BOARD_WEB_APP_HTML)
        self.assertIn("async function ensureRepairOrderCard()", BOARD_WEB_APP_HTML)
        self.assertIn("async function requireRepairOrderCardId()", BOARD_WEB_APP_HTML)
        self.assertIn("const data = await persistCardPayload(payload);", BOARD_WEB_APP_HTML)
        self.assertIn("applyCardModalState(savedCard);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "function applyRepairOrderCardUpdate(updatedCard, fallbackOrder = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "body.insertAdjacentHTML('beforeend', repairOrderRowHtml(section, emptyRepairOrderRow(), rowIndex));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("saveRepairOrder = async function(", BOARD_WEB_APP_HTML)
        self.assertIn("const cardId = await requireRepairOrderCardId();", BOARD_WEB_APP_HTML)
        self.assertIn("source.client_information ?? source.comment", BOARD_WEB_APP_HTML)
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
        self.assertIn("'/api/autofill_repair_order'", BOARD_WEB_APP_HTML)
        self.assertIn(
            "els.repairOrderModal.addEventListener('change', handleRepairOrderModalInput);",
            BOARD_WEB_APP_HTML,
        )
        self.assertNotIn("function buildRepairOrderPrintHtml(order)", BOARD_WEB_APP_HTML)
        self.assertNotIn("function openRepairOrderPrint(order)", BOARD_WEB_APP_HTML)
        self.assertNotIn("printWindow.print();", BOARD_WEB_APP_HTML)

    def test_repair_order_autofill_status_uses_report_hints(self) -> None:
        self.assertIn("function buildRepairOrderAutofillStatus(data)", BOARD_WEB_APP_HTML)
        self.assertIn("data?.meta?.autofill_report", BOARD_WEB_APP_HTML)
        self.assertIn("filled_fields", BOARD_WEB_APP_HTML)
        self.assertIn("информация для клиента", BOARD_WEB_APP_HTML)

    def test_repair_order_print_module_exposes_preview_template_editor_and_routes(self) -> None:
        self.assertIn('id="repairOrderPrintModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderPrintDocuments"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderPrintPreviewFrame"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderPrintTemplateSelect"', BOARD_WEB_APP_HTML)
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
        self.assertIn("function syncRepairOrderPrintPrinterState()", BOARD_WEB_APP_HTML)
        self.assertIn("function runRepairOrderBrowserPrint()", BOARD_WEB_APP_HTML)
        self.assertIn("isPrintRunning: false,", BOARD_WEB_APP_HTML)
        self.assertIn("let printStarted = false;", BOARD_WEB_APP_HTML)
        self.assertIn("if (printStarted) return;", BOARD_WEB_APP_HTML)
        self.assertIn("frame.onload = null;", BOARD_WEB_APP_HTML)
        self.assertIn("if (repairOrderPrintState.isPrintRunning) return;", BOARD_WEB_APP_HTML)
        self.assertIn("function buildPrintTemplateVisualEditorHtml(content)", BOARD_WEB_APP_HTML)
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
        self.assertIn("async function handleRepairOrdersListKeydown(event)", BOARD_WEB_APP_HTML)
        self.assertIn("loadRepairOrders = async function(openModal = false)", BOARD_WEB_APP_HTML)
        self.assertIn("function openRepairOrderCard(cardId)", BOARD_WEB_APP_HTML)
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
        self.assertIn("'/api/open_card'", BOARD_WEB_APP_HTML)
        self.assertIn("data-open-repair-order-card", BOARD_WEB_APP_HTML)
        repair_order_card_fragment = BOARD_WEB_APP_HTML[
            BOARD_WEB_APP_HTML.index(
                "async function openRepairOrderCard(cardId)"
            ) : BOARD_WEB_APP_HTML.index("function updateRepairOrdersTabs()")
        ]
        self.assertIn("'/api/get_repair_order'", repair_order_card_fragment)
        self.assertIn("preloadedRepairOrderData", repair_order_card_fragment)
        self.assertNotIn("openCardWorkspace", repair_order_card_fragment)
        self.assertIn(".dialog--repair-orders {", BOARD_WEB_APP_HTML)
        self.assertIn("width: min(1940px, calc(100vw - 24px));", BOARD_WEB_APP_HTML)
        self.assertIn(
            "repairOrderListTotalText(item.grand_total, item.works_total)", BOARD_WEB_APP_HTML
        )
        self.assertIn("function repairOrderListDateDisplayValue(value)", BOARD_WEB_APP_HTML)
        self.assertIn("renderRepairOrderListRows = function(items)", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__number", BOARD_WEB_APP_HTML)
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
        self.assertIn(".repair-orders-table-head__searchable", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-table-head__searchable-group", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-table-head__sum", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__total", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__payment-status", BOARD_WEB_APP_HTML)
        self.assertIn(".repair-orders-row__paid", BOARD_WEB_APP_HTML)
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

    def test_repair_order_modal_supports_status_and_extended_vehicle_fields(self) -> None:
        self.assertIn('id="repairOrderOpenedAt"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderClosedAt"', BOARD_WEB_APP_HTML)
        self.assertIn('id="repairOrderStatus"', BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="dialog__head dialog__head--card dialog__head--repair-order"', BOARD_WEB_APP_HTML
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
        self.assertIn("data-repair-orders-filter", BOARD_WEB_APP_HTML)
        self.assertIn("renderRepairOrderListRows = function(items)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function setRepairOrdersFilter(status, { openModal = false } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("syncRepairOrdersLayout(normalizedFilter);", BOARD_WEB_APP_HTML)
        self.assertIn("ОТКРЫТЫЕ: ", BOARD_WEB_APP_HTML)
        self.assertIn("ГОТОВЫЕ: ", BOARD_WEB_APP_HTML)
        self.assertIn("АРХИВ: ", BOARD_WEB_APP_HTML)
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

    def test_cashboxes_modal_exposes_minimal_accounting_workspace(self) -> None:
        self.assertIn('id="cashboxesButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxesModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransferModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxJournalModal"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxesList"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxCreateButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxJournalButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxJournalDownloadButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxDeleteButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxCancelLastButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxIncomeButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransferButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransferTargets"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransferAmountInput"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransferConfirmButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxExpenseButton"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxTransactions"', BOARD_WEB_APP_HTML)
        self.assertIn('id="cashboxJournalText"', BOARD_WEB_APP_HTML)
        self.assertIn('title="Перетащите, чтобы изменить порядок касс"', BOARD_WEB_APP_HTML)
        self.assertIn(".cashboxes-layout {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashboxes-pane__foot {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashboxes-list.is-drop-end {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-transactions-card {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-cancel-last-button[disabled] {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-text {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-journal-download-button {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-delete-button {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-detail__identity {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-composer__actions {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-transfer-grid {", BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-transfer-target {", BOARD_WEB_APP_HTML)
        self.assertIn('.cashbox-row[draggable="true"] {', BOARD_WEB_APP_HTML)
        self.assertIn(".cashbox-row.is-drop-target {", BOARD_WEB_APP_HTML)
        self.assertIn(
            'class="btn btn--accent" id="cashboxCreateButton">+ ДОБАВИТЬ', BOARD_WEB_APP_HTML
        )
        self.assertIn(
            'class="btn btn--ghost cashbox-delete-button" id="cashboxDeleteButton">- УДАЛИТЬ',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="btn btn--accent" id="cashboxTransferConfirmButton">ПЕРЕМЕСТИТЬ',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function ensureCashboxesUi()", BOARD_WEB_APP_HTML)
        self.assertIn("function openCashboxesModal()", BOARD_WEB_APP_HTML)
        self.assertIn("async function openCashJournalModal()", BOARD_WEB_APP_HTML)
        self.assertIn("async function loadCashJournalText()", BOARD_WEB_APP_HTML)
        self.assertIn("async function downloadCashJournal()", BOARD_WEB_APP_HTML)
        self.assertIn("function filteredCashboxTransactions()", BOARD_WEB_APP_HTML)
        self.assertIn("async function createCashbox()", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function reorderCashboxes(cashboxId, beforeCashboxId = '')", BOARD_WEB_APP_HTML
        )
        self.assertIn("async function createCashboxTransfer()", BOARD_WEB_APP_HTML)
        self.assertIn("async function createCashboxTransaction(direction)", BOARD_WEB_APP_HTML)
        self.assertIn("async function cancelLastCashboxTransaction()", BOARD_WEB_APP_HTML)
        self.assertIn("async function loadCashboxes(openModal = false)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "async function loadCashboxDetail(cashboxId, { openModal = false } = {})",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("function activeCashboxLatestTransaction()", BOARD_WEB_APP_HTML)
        self.assertIn("function cashboxTransactionIsTransfer(item)", BOARD_WEB_APP_HTML)
        self.assertIn("function resetCashboxDragState()", BOARD_WEB_APP_HTML)
        self.assertIn("function cashboxDropBeforeIdFromRow(row, clientY)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCashboxesListDragStart(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCashboxesListDragOver(event)", BOARD_WEB_APP_HTML)
        self.assertIn("async function handleCashboxesListDrop(event)", BOARD_WEB_APP_HTML)
        self.assertIn("function handleCashboxesListDragEnd()", BOARD_WEB_APP_HTML)
        self.assertIn("Math.round(Math.abs(amount) / 100)", BOARD_WEB_APP_HTML)
        self.assertIn(
            "toLocaleString('ru-RU', { maximumFractionDigits: 0 }) + ' ₽'", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.cashboxesButton.addEventListener('click', openCashboxesModal);", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "els.cashboxJournalButton.addEventListener('click', openCashJournalModal);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxJournalDownloadButton.addEventListener('click', downloadCashJournal);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxTransferButton.addEventListener('click', createCashboxTransfer);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxCancelLastButton.addEventListener('click', cancelLastCashboxTransaction);",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "els.cashboxTransferConfirmButton.addEventListener('click', submitCashboxTransfer);",
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
        self.assertNotIn('id="cashboxPeriodFilters"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="cashboxDirectionFilters"', BOARD_WEB_APP_HTML)
        self.assertNotIn('id="cashboxNameInput"', BOARD_WEB_APP_HTML)
        self.assertNotIn("cashboxPeriodFilter", BOARD_WEB_APP_HTML)
        self.assertNotIn("cashboxDirectionFilter", BOARD_WEB_APP_HTML)
        self.assertNotIn("ОТКУДА", BOARD_WEB_APP_HTML)
        self.assertNotIn("КУДА ПЕРЕВЕСТИ", BOARD_WEB_APP_HTML)
        self.assertNotIn("Баланс:", BOARD_WEB_APP_HTML)
        self.assertNotIn("1000 или 1000,50", BOARD_WEB_APP_HTML)
        self.assertIn(
            "const yy = String(date.getFullYear() % 100).padStart(2, '0');", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "return dd + '.' + mm + '.' + yy + ', ' + hh + ':' + min;", BOARD_WEB_APP_HTML
        )
        self.assertNotIn("window.prompt('Куда перевести деньги?", BOARD_WEB_APP_HTML)

    def test_modal_data_loader_helpers_drive_active_archive_and_gpt_paths(self) -> None:
        self.assertIn("function maybeOpenModal(modalEl, openModal)", BOARD_WEB_APP_HTML)
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
        self.assertIn("async function createColumnFromTopbar()", BOARD_WEB_APP_HTML)
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
        self.assertIn("const previousCard = snapshotCardById(nextCard.id);", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (previousColumnId && previousColumnId === nextColumnId) {", BOARD_WEB_APP_HTML
        )
        self.assertIn(
            "const samePosition = previousPosition === nextPosition || (Number.isNaN(previousPosition) && Number.isNaN(nextPosition));",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "if (samePosition && replaceBoardCardElement(nextCard)) return;", BOARD_WEB_APP_HTML
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
        self.assertEqual(BOARD_WEB_APP_HTML.count("function buildVehicleAutofillRawText()"), 1)
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
            "if (closeTrigger instanceof HTMLElement) closeNamedModal(closeTrigger.dataset.close);",
            BOARD_WEB_APP_HTML,
        )
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
        self.assertIn(
            "els.repairOrderPaymentsModal?.classList.remove('is-open');", BOARD_WEB_APP_HTML
        )
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
        self.assertIn(
            "els.columnButton.addEventListener('click', createColumnFromTopbar);",
            BOARD_WEB_APP_HTML,
        )

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
        self.assertIn("БЕЗ МЕТОК", BOARD_WEB_APP_HTML)
        self.assertIn("Описание не указано", BOARD_WEB_APP_HTML)
        self.assertIn("СИГН", BOARD_WEB_APP_HTML)
        self.assertIn("ФАЙЛЫ ", BOARD_WEB_APP_HTML)
        self.assertIn("ЖУРНАЛ ", BOARD_WEB_APP_HTML)
        self.assertIn('title="Не прочитано"', BOARD_WEB_APP_HTML)


if __name__ == "__main__":
    unittest.main()
