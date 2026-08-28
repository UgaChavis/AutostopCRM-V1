from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.web_assets import BOARD_WEB_APP_CONTRACT_TEXT as BOARD_WEB_APP_HTML


def _asset_section(start_marker: str, end_marker: str) -> str:
    start = BOARD_WEB_APP_HTML.index(start_marker)
    end = BOARD_WEB_APP_HTML.index(end_marker, start)
    return BOARD_WEB_APP_HTML[start:end]


class EmployeeCashboxAccessWebAssetTests(unittest.TestCase):
    def test_protected_entries_are_hidden_until_profile_permission_is_loaded(self) -> None:
        self.assertIn(
            'class="btn hidden" id="cashboxesButton" disabled aria-hidden="true"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="btn hidden" id="employeesButton" disabled aria-hidden="true"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="mobile-bottom-nav__item hidden" type="button" '
            'data-mobile-view="cashboxes" disabled aria-hidden="true"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            'class="mobile-module-button hidden" type="button" '
            'data-mobile-open="employees" disabled aria-hidden="true"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const EMPLOYEES_CASHBOXES_ACCESS_PERMISSION = 'employees_cashboxes_access';",
            BOARD_WEB_APP_HTML,
        )

    def test_permission_sync_guards_all_desktop_and_mobile_entry_paths(self) -> None:
        permission_helpers = _asset_section(
            "function operatorHasPermission(permission)",
            "function operatorStatHtml(",
        )
        self.assertIn("function operatorCanAccessEmployeesCashboxes()", permission_helpers)
        self.assertIn("function requireEmployeesCashboxesAccess()", permission_helpers)
        self.assertIn("function syncEmployeesCashboxesAccessUi(", permission_helpers)
        self.assertIn("closeModalAndChildren('cashboxes');", permission_helpers)
        self.assertIn("closeModalAndChildren('employees');", permission_helpers)
        self.assertIn("clearEmployeesCashboxesModuleState();", permission_helpers)

        for start, end in (
            ("function openEmployeesModal()", "async function saveEmployee()"),
            ("function openCashboxesModal()", "async function handleCashboxesListClick("),
            ("function openMobileEmployeesPanel()", "function handleMobileEmployeesClick("),
        ):
            section = _asset_section(start, end)
            self.assertIn("if (!requireEmployeesCashboxesAccess()) return;", section)

        mobile_navigation = _asset_section(
            "function mobileViewOrder()",
            "function mobileSwipeIgnoredTarget(",
        )
        self.assertIn("MOBILE_VIEW_ORDER.filter((item) => item !== 'cashboxes')", mobile_navigation)
        set_mobile_view = _asset_section(
            "function setMobileView(view)", "function setMobileCashboxAction("
        )
        self.assertIn(
            "requestedView === 'cashboxes' && !requireEmployeesCashboxesAccess()",
            set_mobile_view,
        )

    def test_background_fetch_and_mobile_more_do_not_open_protected_workspaces(self) -> None:
        notification = _asset_section(
            "async function refreshCashboxNotification()",
            "function syncCashboxInList(",
        )
        self.assertIn("if (!operatorCanAccessEmployeesCashboxes())", notification)
        self.assertIn("return null;", notification)

        rows = _asset_section(
            "function mobileMoreModuleRows()", "function renderMobileMoreModules("
        )
        self.assertIn("if (operatorCanAccessEmployeesCashboxes())", rows)
        loader = _asset_section(
            "async function loadMobileMoreModules(", "function renderMobileShell("
        )
        self.assertIn(
            "if (operatorCanAccessEmployeesCashboxes()) tasks.push(loadEmployeesReference());",
            loader,
        )

    def test_permission_mismatch_refreshes_profile_and_revokes_cached_ui_access(self) -> None:
        api_client = _asset_section("async function api(path", "function setApiToken(")
        self.assertIn(
            "response.status === 403 && payload?.error?.code === 'forbidden'",
            api_client,
        )
        self.assertIn("payload?.data?.meta?.references_only === true", api_client)
        self.assertIn("refreshOperatorProfileAfterPermissionMismatch(", api_client)

        refresh_helper = _asset_section(
            "function refreshOperatorProfileAfterPermissionMismatch(",
            "async function openOperatorWorkspace()",
        )
        self.assertIn("requestSessionToken !== state.operatorSessionToken", refresh_helper)
        self.assertIn("state.operatorPermissionRefreshPromise", refresh_helper)
        self.assertIn("loadOperatorProfile(false)", refresh_helper)

    def test_admin_editor_preserves_both_registered_permissions(self) -> None:
        self.assertIn(
            'id="adminUserEmployeesCashboxesAccess" type="checkbox"',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn("СОТРУДНИКИ И КАССЫ: ", BOARD_WEB_APP_HTML)
        save = _asset_section(
            "async function saveOperatorUser()", "async function deleteOperatorUser("
        )
        self.assertIn("payload.permissions = [];", save)
        self.assertIn(
            "payload.permissions.push(EMPLOYEES_CASHBOXES_ACCESS_PERMISSION);",
            save,
        )
        self.assertIn("payload.permissions.push(SALARY_BALANCE_RESET_PERMISSION);", save)

    def test_admin_editor_enforces_salary_reset_permission_dependency(self) -> None:
        dependency = _asset_section(
            "function syncOperatorAdminSalaryResetPermission()",
            "function renderOperatorActivityUserOptions()",
        )
        self.assertIn(
            "const canAccessEmployeesCashboxes = Boolean("
            "els.adminUserEmployeesCashboxesAccess?.checked);",
            dependency,
        )
        self.assertIn(
            "if (!canAccessEmployeesCashboxes) els.adminUserSalaryBalanceReset.checked = false;",
            dependency,
        )
        self.assertIn(
            "els.adminUserSalaryBalanceReset.disabled = !canAccessEmployeesCashboxes;",
            dependency,
        )
        self.assertIn(
            "els.adminUserEmployeesCashboxesAccess?.addEventListener(",
            dependency,
        )

        opener = _asset_section(
            "async function openOperatorAdminModal()",
            "async function saveOperatorUser()",
        )
        self.assertIn("bindOperatorAdminPermissionUi();", opener)

        save = _asset_section(
            "async function saveOperatorUser()", "async function deleteOperatorUser("
        )
        self.assertIn(
            "if (canAccessEmployeesCashboxes && els.adminUserSalaryBalanceReset?.checked)",
            save,
        )

    def test_salary_reset_render_and_handler_require_both_permissions(self) -> None:
        permission_helpers = _asset_section(
            "function operatorHasPermission(permission)",
            "function requireEmployeesCashboxesAccess()",
        )
        self.assertIn("function operatorCanResetSalaryBalance()", permission_helpers)
        self.assertIn(
            "return operatorCanAccessEmployeesCashboxes()",
            permission_helpers,
        )
        self.assertIn(
            "&& operatorHasPermission(SALARY_BALANCE_RESET_PERMISSION);",
            permission_helpers,
        )

        renderer = _asset_section(
            "function renderEmployeeSalaryModal()",
            "async function loadEmployeeSalarySheet(",
        )
        self.assertIn(
            "const canResetBalance = operatorCanResetSalaryBalance();",
            renderer,
        )

        handler = _asset_section(
            "async function handleEmployeeSalaryReset()",
            "async function handleEmployeeSalaryActionConfirm()",
        )
        self.assertIn("if (!operatorCanResetSalaryBalance())", handler)
        self.assertLess(
            handler.index("if (!operatorCanResetSalaryBalance())"),
            handler.index("/api/reset_employee_salary_balance"),
        )

        permission_sync = _asset_section(
            "function syncEmployeesCashboxesAccessUi(",
            "function operatorStatHtml(",
        )
        self.assertIn("if (!canResetSalaryBalance)", permission_sync)
        self.assertIn("state.employeeSalaryResetPending = false;", permission_sync)
        self.assertIn("state.employeeSalaryResetIntent = null;", permission_sync)

    def test_reference_only_cashboxes_never_render_a_fake_zero_balance(self) -> None:
        balance = _asset_section(
            "function cashboxBalanceDisplay(cashbox)", "function cashboxBalanceSign("
        )
        self.assertIn("if (", balance)
        self.assertIn("!statistics", balance)
        self.assertIn("return '';", balance)
        selector = _asset_section(
            "function renderMobileRepairOrderPaymentCashboxes(",
            "async function ensureMobileRepairOrderPaymentCashboxes()",
        )
        self.assertIn("const label = balance ? (name + ' · ' + balance) : name;", selector)

    def test_repair_order_salary_controls_require_employees_cashboxes_access(self) -> None:
        renderer = _asset_section(
            "function repairOrderWorkExecutorCellHtml(normalized)",
            "function repairOrderRowHtml(",
        )
        self.assertIn(
            "const salaryGearHtml = operatorCanAccessEmployeesCashboxes()",
            renderer,
        )
        self.assertIn(
            'data-repair-order-cell="executor_id"',
            renderer,
        )
        self.assertIn("+ salaryGearHtml", renderer)

        row_renderer = _asset_section(
            "function repairOrderRowHtml(section, row, index)",
            "function readRepairOrderRowElement(",
        )
        self.assertIn("repairOrderWorkExecutorCellHtml(normalized)", row_renderer)
        for editable_field in ("name", "quantity", "price"):
            self.assertIn(
                "repairOrderRowInputHtml('" + editable_field + "'",
                row_renderer,
            )

        for start, end, guarded_action in (
            (
                "function openRepairOrderWorkSalaryPopover(button)",
                "function applyRepairOrderWorkSalaryPopover()",
                "state.repairOrderWorkSalaryRow = row;",
            ),
            (
                "function applyRepairOrderWorkSalaryPopover()",
                "function resetRepairOrderWorkSalaryOverride()",
                "row.dataset.repairOrderWorkSalaryOverrideEnabled = 'true';",
            ),
            (
                "function resetRepairOrderWorkSalaryOverride()",
                "function syncRepairOrderSectionTotals(",
                "row.dataset.repairOrderWorkSalaryOverrideEnabled = '';",
            ),
        ):
            section = _asset_section(start, end)
            self.assertIn("if (!operatorCanAccessEmployeesCashboxes())", section)
            self.assertIn("closeRepairOrderWorkSalaryPopover();", section)
            self.assertIn("return;", section)
            self.assertLess(
                section.index("if (!operatorCanAccessEmployeesCashboxes())"),
                section.index(guarded_action),
            )

        permission_sync = _asset_section(
            "function syncEmployeesCashboxesAccessUi(",
            "function operatorStatHtml(",
        )
        self.assertIn(
            "closeRepairOrderWorkSalaryPopover();",
            permission_sync,
        )


if __name__ == "__main__":
    unittest.main()
