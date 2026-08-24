from __future__ import annotations

from typing import Any

from browser_smoke_runtime import TempRuntime
from browser_smoke_support import (
    _api_data,
    _close_card_modal_if_open,
    _is_modal_open,
    _wait_clients_search_ready,
    _wait_modal_closed,
    _wait_modal_open,
)


async def _anonymous_write_rejected(page: Any, runtime: TempRuntime) -> bool:
    before_ids = {
        card.get("id") for card in runtime.service.get_board_snapshot({"compact": True})["cards"]
    }
    response = await page.request.post(
        f"{runtime.base_url}/api/create_card",
        data={"title": "anonymous browser smoke must be rejected"},
    )
    after_ids = {
        card.get("id") for card in runtime.service.get_board_snapshot({"compact": True})["cards"]
    }
    return response.status in {401, 403} and after_ids == before_ids


async def _exercise_board_create_roundtrip(page: Any, runtime: TempRuntime) -> bool:
    create_button = page.locator("[data-create-in]").first
    await create_button.wait_for(state="visible")
    column_id = str(await create_button.get_attribute("data-create-in") or "")
    if not column_id:
        return False
    await create_button.click()
    await _wait_modal_open(page, "#cardModal")
    await page.fill("#cardVehicle", "Core Smoke Vehicle")
    await page.fill("#cardTitle", "Core smoke created card")
    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/create_card")
        )
    ) as response_info:
        await page.click("#saveCardButton")
    response = _api_data(await (await response_info.value).json())
    created = response.get("card") if isinstance(response, dict) else None
    card_id = str((created or {}).get("id") or "")
    if not card_id:
        return False
    await _wait_modal_closed(page, "#cardModal")
    persisted = runtime.service.get_card({"card_id": card_id})["card"]
    await page.wait_for_selector(f'[data-card-id="{card_id}"]')
    return bool(
        persisted.get("title") == "Core smoke created card"
        and persisted.get("vehicle") == "Core Smoke Vehicle"
        and persisted.get("column") == column_id
    )


async def _exercise_client_link_roundtrip(page: Any, runtime: TempRuntime) -> dict[str, bool]:
    client_count_before = len(runtime.service.list_clients({"limit": 500})["clients"])
    created_card = next(
        (
            card
            for card in runtime.service.get_board_snapshot({"compact": True})["cards"]
            if card.get("title") == "Core smoke created card"
        ),
        None,
    )
    created_card_id = str((created_card or {}).get("id") or "")
    if not created_card_id or (created_card or {}).get("client_id"):
        return {
            "clients_search_selects_realistic_row": False,
            "clients_repair_order_returns_to_client": False,
        }
    await page.click(f'[data-card-id="{created_card_id}"]')
    await _wait_modal_open(page, "#cardModal")
    await page.fill("#vehicleField_customer_name", "Smoke Клиент")
    suggestion = page.locator(f'[data-client-suggestion="{runtime.client_id}"]')
    await suggestion.wait_for(state="visible")
    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/link_card_to_client")
        )
    ):
        await suggestion.click()
    linked_card = runtime.service.get_card({"card_id": created_card_id})["card"]
    link_exact_readback_ok = linked_card.get("client_id") == runtime.client_id
    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/update_card")
        )
    ):
        await page.click("#saveCardButton")
    await _wait_modal_closed(page, "#cardModal")

    await page.click("#clientsButton")
    await _wait_modal_open(page, "#clientsModal")
    await page.wait_for_selector("#clientNewButton")
    await page.fill("#clientsSearchInput", "Smoke")
    await _wait_clients_search_ready(page, client_id=runtime.client_id, query="Smoke")
    search_ok = bool(
        await page.evaluate(
            """(clientId) => {
              const row = document.querySelector('[data-client-id="' + clientId + '"]');
              return Boolean(row?.getAttribute('aria-label')?.startsWith('Клиент '));
            }""",
            runtime.client_id,
        )
    )
    await page.click(f'[data-client-id="{runtime.client_id}"]')
    await page.wait_for_selector(f'[data-open-repair-order-card="{runtime.client_card_id}"]')
    await page.click(f'[data-open-repair-order-card="{runtime.client_card_id}"]')
    await _wait_modal_open(page, "#repairOrderModal")
    await page.click('[data-close="repair-order"]')
    await _wait_modal_closed(page, "#repairOrderModal")
    link_ok = await _is_modal_open(page, "#clientsModal")
    await page.click('[data-close="clients"]')
    await _wait_modal_closed(page, "#clientsModal")
    client_count_after = len(runtime.service.list_clients({"limit": 500})["clients"])
    return {
        "clients_search_selects_realistic_row": bool(search_ok),
        "clients_repair_order_returns_to_client": bool(
            link_exact_readback_ok and link_ok and client_count_after == client_count_before
        ),
    }


async def _exercise_repair_order_preview_roundtrip(page: Any, runtime: TempRuntime) -> bool:
    await page.click(f'[data-card-id="{runtime.client_card_id}"]')
    await _wait_modal_open(page, "#cardModal")
    await page.click("#repairOrderButton")
    await _wait_modal_open(page, "#repairOrderModal")
    await page.click("#repairOrderAddWorkRowButton")
    work_row = page.locator("#repairOrderWorksBody tr[data-repair-order-row]").last
    await work_row.locator('[data-repair-order-cell="name"]').fill("Core preview work")
    await work_row.locator('[data-repair-order-cell="quantity"]').fill("1")
    await work_row.locator('[data-repair-order-cell="price"]').fill("123")
    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/update_repair_order")
        )
    ):
        await page.click("#repairOrderSaveButton")
    await page.wait_for_function(
        "() => !document.querySelector('#repairOrderSaveButton')?.disabled"
    )
    persisted = runtime.service.get_repair_order({"card_id": runtime.client_card_id})[
        "repair_order"
    ]
    persisted_ok = any(
        work.get("name") == "Core preview work"
        and str(work.get("quantity")) == "1"
        and str(work.get("price")) == "123"
        for work in persisted.get("works") or []
    )
    await page.click("#repairOrderPrintButton")
    await _wait_modal_open(page, "#repairOrderPrintModal")
    await page.wait_for_selector("#repairOrderPrintDocuments [data-print-document]")
    await page.wait_for_function(
        """() => {
          const frame = document.querySelector('#repairOrderPrintPreviewFrame');
          return (frame?.contentDocument?.body?.innerText || '').includes('Core preview work');
        }"""
    )
    await page.click("#repairOrderPrintCloseX")
    await _wait_modal_closed(page, "#repairOrderPrintModal")
    await page.click('[data-close="repair-order"]')
    await _wait_modal_closed(page, "#repairOrderModal")
    await _close_card_modal_if_open(page)
    return bool(persisted_ok)


async def _exercise_inventory_item_roundtrip(page: Any, runtime: TempRuntime) -> bool:
    await page.click("#inventoryButton")
    await _wait_modal_open(page, "#inventoryModal")
    await page.wait_for_selector("#inventoryNewButton")
    await page.click("#inventoryNewButton")
    await page.fill("#inventoryNameInput", "Core smoke inventory")
    await page.fill("#inventoryCatalogInput", "CORE-SMOKE-001")
    await page.fill("#inventoryQuantityInput", "2")
    await page.fill("#inventoryCostPriceInput", "100")
    await page.fill("#inventorySalePriceInput", "150")
    async with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/save_inventory_item")
        )
    ) as response_info:
        await page.click("#inventorySaveButton")
    response = _api_data(await (await response_info.value).json())
    item = response.get("item") if isinstance(response, dict) else None
    item_id = str((item or {}).get("id") or "")
    if not item_id:
        return False
    await page.wait_for_selector(f'[data-inventory-item-id="{item_id}"]')
    persisted = runtime.service.get_inventory_item({"item_id": item_id})["item"]
    await page.click('[data-close="inventory"]')
    await _wait_modal_closed(page, "#inventoryModal")
    return bool(
        persisted.get("name") == "Core smoke inventory"
        and persisted.get("catalog_number") == "CORE-SMOKE-001"
        and str(persisted.get("quantity")) == "2"
    )


async def _exercise_files_modal(page: Any) -> dict[str, bool]:
    await page.click("#sharedFilesButton")
    await _wait_modal_open(page, "#sharedFilesModal")
    await page.wait_for_selector("#sharedFilesDesktop")
    await page.wait_for_selector(".shared-file-icon")
    scanability_ok = bool(
        await page.evaluate(
            """() => {
              const icon = document.querySelector('.shared-file-icon');
              return Boolean(
                icon?.getAttribute('aria-label')?.startsWith('Файл ') &&
                icon.querySelectorAll('.shared-file-icon__meta-chip').length >= 2
              );
            }"""
        )
    )
    await page.click('[data-close="shared-files"]')
    await _wait_modal_closed(page, "#sharedFilesModal")
    return {"files_modal": True, "shared_files_scanability_markup": scanability_ok}
