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


if __name__ == "__main__":
    unittest.main()
