"""Browser regressions for ordered drops and live employee permission changes.

All writes use the browser smoke's disposable runtime. DOM events execute the
shipped handlers, HTTP routes and storage; delayed responses are real reads.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

from browser_smoke_inventory import exercise_inventory_write_integrity
from browser_smoke_runtime import TempRuntime, start_temp_runtime
from browser_smoke_support import _api_data, _login_successfully, _wait_modal_closed

from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


async def _drop(page: Any, card_id: str, column: str, before: str = "") -> None:
    await page.evaluate(
        """({cardId,columnId,beforeId}) => {
          const column=document.querySelector('.column[data-column-id="'+columnId+'"]');
          const source=document.querySelector('.card[data-card-id="'+cardId+'"]:not([data-virtual-card="true"])');
          const target=column?.querySelector('.column__cards');
          if(!source||!target) throw new Error('Drop fixture is not rendered');
          const siblings=[...target.querySelectorAll('.card')].filter(c=>c.dataset.cardId!==cardId);
          const anchor=beforeId ? siblings.find(c=>c.dataset.cardId===beforeId) : siblings.at(-1);
          const rect=(anchor||target).getBoundingClientRect();
          const y=beforeId ? rect.top+1 : rect.bottom+8;
          const transfer=new DataTransfer();
          const options={bubbles:true,cancelable:true,dataTransfer:transfer,clientX:rect.left+10,clientY:y};
          source.dispatchEvent(new DragEvent('dragstart',options));
          target.dispatchEvent(new DragEvent('dragover',options));
          target.dispatchEvent(new DragEvent('drop',options));
          source.dispatchEvent(new DragEvent('dragend',options));
        }""",
        {"cardId": card_id, "columnId": column, "beforeId": before},
    )


def _persisted_order(runtime: TempRuntime, column: str) -> list[str]:
    cards = runtime.service.get_board_snapshot({"compact": True})["cards"]
    return [c["id"] for c in sorted(cards, key=lambda c: c["position"]) if c["column"] == column]


async def _assert_order(page: Any, runtime: TempRuntime, column: str, expected: list[str]) -> None:
    await page.wait_for_function(
        """({column,expected}) => {
          const cards=[...document.querySelectorAll('.column[data-column-id="'+column+'"] .column__cards .card')];
          return JSON.stringify(cards.map(c=>c.dataset.cardId))===JSON.stringify(expected);
        }""",
        arg={"column": column, "expected": expected},
        timeout=10000,
    )
    actual = _persisted_order(runtime, column)
    if actual != expected:
        raise AssertionError(f"Persisted order differs from rendered order: {actual} != {expected}")


async def _move(page: Any, card: str, column: str, before: str = "") -> dict:
    async with page.expect_response("**/api/move_card") as info:
        await _drop(page, card, column, before)
    response = await info.value
    data = _api_data(await response.json())
    assert response.ok, data
    payload = response.request.post_data_json
    assert payload.get("placement") == ("start" if before else "end"), payload
    assert data["meta"]["response_mode"] == "delta"
    return data


async def exercise_board_drop_order(page: Any, runtime: TempRuntime) -> dict[str, bool]:
    columns = [
        runtime.service.create_column({"label": f"Review order {i}"})["column"]["id"]
        for i in range(3)
    ]
    left, right, empty = columns
    for column, count in ((left, 3), (right, 2)):
        for i in range(count):
            runtime.service.create_card({"column": column, "title": f"Review card {i}"})
    a, b, c = _persisted_order(runtime, left)
    d, e = _persisted_order(runtime, right)
    await page.goto(runtime.browser_url)
    await _login_successfully(page)
    await _assert_order(page, runtime, left, [a, b, c])
    # Both directions in one column, including a no-op at the end.
    await _move(page, a, left)
    await _assert_order(page, runtime, left, [b, c, a])
    await _move(page, a, left, b)
    await _assert_order(page, runtime, left, [a, b, c])
    await _move(page, c, left)
    await _assert_order(page, runtime, left, [a, b, c])
    # Top/middle/bottom of another column, and a genuinely empty target.
    await _move(page, a, right, d)
    await _assert_order(page, runtime, right, [a, d, e])
    await _move(page, b, right, e)
    await _assert_order(page, runtime, right, [a, d, b, e])
    await _move(page, c, right)
    await _assert_order(page, runtime, right, [a, d, b, e, c])
    await _move(page, a, empty)
    await _assert_order(page, runtime, empty, [a])
    await _assert_order(page, runtime, left, [])

    # First write is durable, but its response is held while another drop occurs.
    committed, release = asyncio.Event(), asyncio.Event()
    move_count = 0

    async def delay_first(route: Any) -> None:
        nonlocal move_count
        move_count += 1
        response = await route.fetch()
        if move_count == 1:
            committed.set()
            await release.wait()
        await route.fulfill(response=response)

    await page.route("**/api/move_card", delay_first)
    try:
        await _drop(page, d, right)
        await asyncio.wait_for(committed.wait(), timeout=10)
        await _drop(page, d, right, b)
        await page.wait_for_timeout(100)
        assert move_count == 1, "Second gesture raced the first write"
        async with page.expect_response(
            lambda response: (
                response.url.endswith("/api/move_card")
                and response.request.post_data_json.get("before_card_id") == b
            )
        ):
            release.set()
        await _assert_order(page, runtime, right, [d, b, e, c])
        assert move_count == 2
    finally:
        release.set()
        await page.unroute("**/api/move_card", delay_first)

    # A full snapshot sampled before the move must not restore the old order.
    sampled, publish = asyncio.Event(), asyncio.Event()

    async def delay_snapshot(route: Any) -> None:
        response = await route.fetch()
        sampled.set()
        await publish.wait()
        await route.fulfill(response=response)

    await page.route("**/api/get_board_snapshot?*", delay_snapshot)
    try:
        await page.locator("#mobileBoardRefreshButton").dispatch_event("click")
        await asyncio.wait_for(sampled.wait(), timeout=10)
        await _move(page, d, right)
        await _assert_order(page, runtime, right, [b, e, c, d])
        publish.set()
        await page.wait_for_timeout(150)
        await _assert_order(page, runtime, right, [b, e, c, d])
    finally:
        publish.set()
        await page.unroute("**/api/get_board_snapshot?*", delay_snapshot)

    # Force a genuine JsonStore CAS conflict with a second service instance.
    peer = CardService(
        JsonStore(runtime.state_store._state_file, runtime.service._logger), runtime.service._logger
    )
    original_save = runtime.service._save_bundle
    injected = False

    def concurrent_save(*args: Any, **kwargs: Any) -> Any:
        nonlocal injected
        if not injected:
            injected = True
            peer.move_card({"card_id": c, "column": right, "before_card_id": b})
        return original_save(*args, **kwargs)

    with patch.object(runtime.service, "_save_bundle", side_effect=concurrent_save):
        async with page.expect_response("**/api/move_card") as info:
            await _drop(page, b, right)
        conflict = await info.value
        assert conflict.status == 409
        assert (await conflict.json())["error"]["code"] == "state_write_conflict"
    await _assert_order(page, runtime, right, [c, b, e, d])

    async def failed_write(route: Any) -> None:
        await route.fulfill(
            status=503,
            json={
                "ok": False,
                "error": {"code": "synthetic_failure", "message": "Synthetic move failure"},
            },
        )

    await page.route("**/api/move_card", failed_write)
    try:
        async with page.expect_response("**/api/move_card"):
            await _drop(page, b, right)
        await page.wait_for_function(
            "() => document.querySelector('#statusLine').textContent.includes('Synthetic move failure')"
        )
        await _assert_order(page, runtime, right, [c, b, e, d])
    finally:
        await page.unroute("**/api/move_card", failed_write)
    return {"board_drop_order_and_conflicts": True}


async def exercise_employee_permission_refresh(
    browser: Any, runtime: TempRuntime
) -> dict[str, bool]:
    admin_context = await browser.new_context(viewport={"width": 1440, "height": 960})
    viewer_context = await browser.new_context(viewport={"width": 1440, "height": 960})
    try:
        admin = await admin_context.new_page()
        await admin.goto(runtime.browser_url)
        await _login_successfully(admin)
        await admin.click("#operatorButton")
        await admin.click("#operatorAdminButton")
        await admin.wait_for_selector("#adminSaveUserButton")
        await admin.fill("#adminUserLogin", "review-roster")
        await admin.fill("#adminUserPassword", "review-roster-password")
        async with admin.expect_response("**/api/save_operator_user"):
            await admin.click("#adminSaveUserButton")
        await admin.wait_for_selector('[data-edit-operator-permissions="REVIEW-ROSTER"]')
        await admin.click('[data-edit-operator-permissions="REVIEW-ROSTER"]')

        viewer = await viewer_context.new_page()
        requests = []
        viewer.on("request", lambda request: requests.append(request.url))
        await viewer.goto(runtime.browser_url)
        await viewer.wait_for_function("() => window.__AUTOSTOP_UI_BOUND__ === true")
        await viewer.fill("#identityInput", "review-roster")
        await viewer.fill("#identityPassword", "review-roster-password")
        await viewer.click("#identitySave")
        await _wait_modal_closed(viewer, "#identityModal")
        await viewer.click('[data-close="operator-profile"]')
        assert not await viewer.locator("#employeesButton").is_visible()

        await admin.check("#adminUserEmployeesReadAccess")
        async with admin.expect_response("**/api/save_operator_user") as info:
            await admin.click("#adminSaveUserButton")
        saved = await info.value
        assert saved.request.post_data_json["expected_permissions"] == []
        assert _api_data(await saved.json())["user"]["permissions"] == ["employees_read_access"]
        # Normal background polling, with no reload, focus event or manual profile open.
        await viewer.wait_for_selector("#employeesButton", state="visible", timeout=50000)
        async with viewer.expect_response("**/api/list_employees?*") as roster_info:
            await viewer.click("#employeesButton")
        roster = _api_data(await (await roster_info.value).json())
        assert not roster.get("meta", {}).get("references_only")
        assert all("balance_total" in employee for employee in roster["employees"])
        assert isinstance(roster["summary"], list)
        assert isinstance(roster["detail_rows"], list)
        await viewer.wait_for_selector("#employeesReadOnlyNotice", state="visible")
        await viewer.click(f'[data-employee-id="{runtime.employee_id}"]')
        assert "Lada Payroll Smoke" in await viewer.locator("#employeesDetailTable").inner_text()
        assert not await viewer.locator("#employeeSaveButton").is_visible()
        await viewer.wait_for_selector("#employeesReportPanel", state="visible")
        assert not await viewer.locator("#cashboxesButton").is_visible()
        await viewer.click(f'[data-employee-salary="{runtime.employee_id}"]')
        await viewer.wait_for_selector("#employeeSalaryModal.is-open")
        assert (
            "Smoke payroll work" in await viewer.locator("#employeeSalaryJournalTable").inner_text()
        )
        for button in (
            "employeeSalaryPayoutButton",
            "employeeSalaryAdvanceButton",
            "employeeSalaryResetButton",
        ):
            assert not await viewer.locator("#" + button).is_visible()
        await viewer.click('[data-close="employeeSalary"]')
        module_path = await viewer.evaluate("() => BOARD_MODULE_MANIFEST.payroll")
        assert any(url.endswith(module_path) for url in requests)
        # Revocation closes the loaded payroll view and clears its rows.
        await admin.click('[data-edit-operator-permissions="REVIEW-ROSTER"]')
        await admin.uncheck("#adminUserEmployeesReadAccess")
        async with admin.expect_response("**/api/save_operator_user"):
            await admin.click("#adminSaveUserButton")
        await viewer.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        await _wait_modal_closed(viewer, "#employeesModal")
        assert not await viewer.locator("#employeesButton").is_visible()
        assert await viewer.locator("#employeesList").inner_text() == ""
        return {"employee_live_permission_roster": True}
    finally:
        await viewer_context.close()
        await admin_context.close()


async def run_review_scenarios(browser: Any, runtime: TempRuntime) -> dict[str, bool]:
    results = {}
    for scenario in (exercise_board_drop_order, exercise_inventory_write_integrity):
        context = await browser.new_context(viewport={"width": 1440, "height": 960})
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            results.update(await scenario(page, runtime))
            assert not errors, errors
        finally:
            await context.close()
    results.update(await exercise_employee_permission_refresh(browser, runtime))
    return results


async def _main() -> None:
    from playwright.async_api import async_playwright

    runtime = start_temp_runtime(start_port=43731)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                print(json.dumps(await run_review_scenarios(browser, runtime)))
            finally:
                await browser.close()
    finally:
        runtime.close()


if __name__ == "__main__":
    asyncio.run(_main())
