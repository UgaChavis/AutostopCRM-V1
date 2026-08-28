from __future__ import annotations

import asyncio
from typing import Any

from browser_smoke_runtime import TempRuntime
from browser_smoke_support import _wait_modal_closed, _wait_modal_open

SCENARIO_NAME = "employee_salary_balance_reset_non_cash"


def _cash_state_snapshot(runtime: TempRuntime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bundle = runtime.state_store.read_bundle()
    return (
        [item.to_dict() for item in bundle["cashboxes"]],
        [item.to_dict() for item in bundle["cash_transactions"]],
    )


async def _exercise_employee_salary_balance_reset(page: Any, runtime: TempRuntime) -> bool:
    employee_id = runtime.salary_reset_employee_id
    ledger_before = runtime.service.get_employee_salary_ledger({"employee_id": employee_id})
    balance_before_minor = ledger_before.get("balance_minor")
    cashboxes_before, cash_transactions_before = _cash_state_snapshot(runtime)

    await page.click("#employeesButton")
    await _wait_modal_open(page, "#employeesModal")
    await page.wait_for_selector(f'[data-employee-salary="{employee_id}"]')
    await page.click(f'[data-employee-salary="{employee_id}"]')
    await _wait_modal_open(page, "#employeeSalaryModal")
    await page.wait_for_selector("#employeeSalaryResetButton", state="visible")

    permission_visible_ok = bool(
        await page.evaluate(
            """() => {
              const button = document.querySelector('#employeeSalaryResetButton');
              return Boolean(button && !button.classList.contains('hidden') && !button.disabled);
            }"""
        )
    )
    confirmation_message = ""
    response_ok = False
    if permission_visible_ok:
        dialog_accept_task: asyncio.Task[Any] | None = None

        def accept_salary_reset_dialog(dialog: Any) -> None:
            nonlocal confirmation_message, dialog_accept_task
            confirmation_message = dialog.message
            dialog_accept_task = asyncio.create_task(dialog.accept())

        page.once("dialog", accept_salary_reset_dialog)
        async with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.split("?", 1)[0].endswith("/api/reset_employee_salary_balance")
            )
        ) as response_info:
            await page.click("#employeeSalaryResetButton")
        response = await response_info.value
        if dialog_accept_task is not None:
            await dialog_accept_task
        response_ok = response.status == 200

    await page.wait_for_function(
        r"""() => {
          const balance = String(
            document.querySelector('#employeeSalaryBalance')?.textContent || ''
          ).replace(/[\s\u00a0]/g, '');
          const journal = document.querySelector('#employeeSalaryJournalTable')?.textContent || '';
          const status = document.querySelector('#statusLine')?.textContent || '';
          return /^(?:0(?:[,.]00)?)(?:₽)?$/.test(balance)
            && journal.includes('ОБНУЛЕНИЕ БАЛАНСА')
            && journal.toLowerCase().includes('некассовая корректировка')
            && status.includes('БАЛАНС ОБНУЛЁН');
        }"""
    )
    ui_zero_ok = bool(
        await page.evaluate(
            r"""() => /^(?:0(?:[,.]00)?)(?:₽)?$/.test(
              String(document.querySelector('#employeeSalaryBalance')?.textContent || '')
                .replace(/[\s\u00a0]/g, '')
            )"""
        )
    )
    ui_journal_ok = bool(
        await page.evaluate(
            """() => {
              const journal = document.querySelector('#employeeSalaryJournalTable')?.textContent || '';
              return journal.includes('ОБНУЛЕНИЕ БАЛАНСА')
                && journal.toLowerCase().includes('некассовая корректировка');
            }"""
        )
    )
    await page.click('[data-close="employeeSalary"]')
    await _wait_modal_closed(page, "#employeeSalaryModal")
    await page.click('[data-close="employees"]')
    await _wait_modal_closed(page, "#employeesModal")

    ledger_after = runtime.service.get_employee_salary_ledger({"employee_id": employee_id})
    reset_rows = [
        row
        for row in ledger_after.get("journal_rows") or []
        if row.get("kind") == "salary_balance_reset"
    ]
    cashboxes_after, cash_transactions_after = _cash_state_snapshot(runtime)
    confirmation_ok = bool(
        "Smoke Обнуление Баланса" in confirmation_message
        and "Текущий баланс:" in confirmation_message
        and "некассовая корректировка" in confirmation_message
        and "ОБНУЛЕНИЕ БАЛАНСА" in confirmation_message
    )
    ledger_ok = bool(
        isinstance(balance_before_minor, int)
        and balance_before_minor > 0
        and ledger_after.get("balance_minor") == 0
        and len(reset_rows) == 1
        and reset_rows[0].get("kind_label") == "ОБНУЛЕНИЕ БАЛАНСА"
        and reset_rows[0].get("amount_minor") == -balance_before_minor
        and reset_rows[0].get("source_label") == "некассовая корректировка"
    )
    cash_state_unchanged = bool(
        cashboxes_after == cashboxes_before and cash_transactions_after == cash_transactions_before
    )
    return bool(
        permission_visible_ok
        and confirmation_ok
        and response_ok
        and ui_zero_ok
        and ui_journal_ok
        and ledger_ok
        and cash_state_unchanged
    )


async def run(page: Any, runtime: TempRuntime, scenarios: dict[str, bool]) -> dict[str, bool]:
    scenarios[SCENARIO_NAME] = await _exercise_employee_salary_balance_reset(page, runtime)
    return scenarios
