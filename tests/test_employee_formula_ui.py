from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.web_assets import BOARD_WEB_APP_HTML


class EmployeeFormulaUiTests(unittest.TestCase):
    def test_employee_cards_show_effective_formula(self) -> None:
        self.assertIn("function employeeCurrentPayrollTerm(employee)", BOARD_WEB_APP_HTML)
        self.assertIn("employee.current_payroll_term", BOARD_WEB_APP_HTML)
        self.assertIn("function employeePayrollFormulaLabel(employee)", BOARD_WEB_APP_HTML)
        self.assertIn("% с работ", BOARD_WEB_APP_HTML)
        self.assertIn("% с прибыли материалов", BOARD_WEB_APP_HTML)
        self.assertIn("% от заказ-наряда", BOARD_WEB_APP_HTML)
        self.assertIn('class="employees-row__formula"', BOARD_WEB_APP_HTML)
        self.assertIn("employeePayrollFormulaLabel(employee)", BOARD_WEB_APP_HTML)
        self.assertIn(".employees-row__formula {", BOARD_WEB_APP_HTML)

    def test_employee_form_uses_effective_term_instead_of_flat_compatibility_fields(self) -> None:
        self.assertIn(
            "const current = employee ? employeeCurrentPayrollTerm(employee) : null;",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const current = employeeCurrentPayrollTerm(employee);\n      return {",
            BOARD_WEB_APP_HTML,
        )
        self.assertIn(
            "const currentTerm = employeeCurrentPayrollTerm(selectedEmployee);",
            BOARD_WEB_APP_HTML,
        )


if __name__ == "__main__":
    unittest.main()
