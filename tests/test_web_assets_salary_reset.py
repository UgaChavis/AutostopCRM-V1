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


class SalaryBalanceResetWebAssetTests(unittest.TestCase):
    def test_reset_button_and_operator_permission_contract_are_exposed(self) -> None:
        self.assertIn(
            '<button class="btn btn--danger hidden" id="employeeSalaryResetButton" '
            'type="button">ОБНУЛИТЬ БАЛАНС</button>',
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const SALARY_BALANCE_RESET_PERMISSION = 'salary_balance_reset';",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn('id="adminUserSalaryBalanceReset" type="checkbox"', BOARD_WEB_APP_HTML)
        self.assertIn("МОЖЕТ ОБНУЛЯТЬ БАЛАНС ЗАРПЛАТЫ", BOARD_WEB_APP_HTML)
        self.assertIn("data-edit-operator-permissions", BOARD_WEB_APP_HTML)
        self.assertIn(
            "Администратор создаёт пользователя или обновляет пароль и отдельные права.",
            BOARD_WEB_APP_HTML,
        )
        permission_editor = _asset_section(
            "function editOperatorUserPermissions(username)",
            "function renderOperatorActivityUserOptions()",
        )
        self.assertIn(
            "state.operatorPermissionEditorUsername = normalizedUsername;",
            permission_editor,
        )

        permission_save = _asset_section(
            "async function saveOperatorUser()",
            "async function deleteOperatorUser(",
        )
        self.assertIn("const existingUser = (state.operatorUsers || []).find(", permission_save)
        self.assertIn("if (!existingUser || editingPermissions)", permission_save)
        self.assertIn("payload.permissions = [];", permission_save)
        self.assertIn(
            "payload.permissions.push(EMPLOYEES_CASHBOXES_ACCESS_PERMISSION);",
            permission_save,
        )
        self.assertIn(
            "payload.permissions.push(SALARY_BALANCE_RESET_PERMISSION);",
            permission_save,
        )
        self.assertNotIn("body: {\n            username:", permission_save)
        self.assertIn("body: payload,", permission_save)
        self.assertIn("state.operatorPermissionEditorUsername = '';", permission_save)

        self.assertIn("els.adminUserLogin.addEventListener('input', () => {", BOARD_WEB_APP_HTML)
        self.assertIn(
            "state.operatorPermissionEditorUsername !== normalizedUsername",
            BOARD_WEB_APP_HTML,
        )

        permission_helper = _asset_section(
            "function operatorHasPermission(permission)",
            "function operatorStatHtml(",
        )
        self.assertIn("state.operatorProfile?.user?.permissions", permission_helper)
        self.assertIn("permissions.includes(normalized)", permission_helper)

    def test_reset_button_rendering_is_permission_and_snapshot_gated(self) -> None:
        salary_renderer = _asset_section(
            "function renderEmployeeSalaryModal()",
            "async function loadEmployeeSalarySheet(",
        )
        self.assertIn("operatorHasPermission(SALARY_BALANCE_RESET_PERMISSION)", salary_renderer)
        self.assertIn(
            "els.employeeSalaryResetButton.classList.toggle('hidden', !canResetBalance);",
            salary_renderer,
        )
        self.assertIn("state.employeeSalaryResetPending", salary_renderer)
        self.assertIn("!Number.isSafeInteger(balanceMinor)", salary_renderer)
        self.assertIn("balanceMinor === 0", salary_renderer)

    def test_reset_handler_preserves_snapshot_idempotency_and_non_cashbox_contract(self) -> None:
        reset_handler = _asset_section(
            "async function handleEmployeeSalaryReset()",
            "async function handleEmployeeSalaryActionConfirm()",
        )
        self.assertIn("if (state.employeeSalaryResetPending) return;", reset_handler)
        self.assertIn("if (!operatorHasPermission(SALARY_BALANCE_RESET_PERMISSION))", reset_handler)
        self.assertIn("window.confirm(", reset_handler)
        self.assertIn("employeeName", reset_handler)
        self.assertIn("Текущий баланс: ", reset_handler)
        self.assertIn("некассовая корректировка", reset_handler)
        self.assertIn("История выплат сохранится", reset_handler)
        self.assertIn("'/api/reset_employee_salary_balance'", reset_handler)
        self.assertIn("employee_id: employeeId,", reset_handler)
        self.assertIn("expected_balance_minor: balanceMinor,", reset_handler)
        self.assertIn("expected_balance_revision: balanceRevision,", reset_handler)
        self.assertIn("idempotency_key: intent.idempotencyKey,", reset_handler)
        self.assertIn("source: 'ui',", reset_handler)
        self.assertIn("error?.code === 'salary_balance_reset_conflict'", reset_handler)
        self.assertIn(
            "await loadEmployeeSalarySheet(employeeId, { openModal: true });", reset_handler
        )
        self.assertNotIn("refreshCashboxesAfterMoneyMutation", reset_handler)
        self.assertNotIn("cashboxMutationPath", reset_handler)

        reset_intent = _asset_section(
            "function createEmployeeSalaryResetIdempotencyKey()",
            "async function handleEmployeeSalaryReset()",
        )
        self.assertIn("existing.employeeId === employeeId", reset_intent)
        self.assertIn("existing.balanceMinor === balanceMinor", reset_intent)
        self.assertIn("existing.balanceRevision === balanceRevision", reset_intent)
        self.assertIn("idempotencyKey: createEmployeeSalaryResetIdempotencyKey()", reset_intent)


if __name__ == "__main__":
    unittest.main()
