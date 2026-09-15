"""Real browser inventory mutations against the disposable smoke runtime."""

from __future__ import annotations

from typing import Any

from browser_smoke_runtime import TempRuntime
from browser_smoke_support import (
    _api_data,
    _login_successfully,
    _wait_modal_closed,
    _wait_modal_open,
)


async def exercise_inventory_write_integrity(page: Any, runtime: TempRuntime) -> dict[str, bool]:
    item = runtime.service.save_inventory_item(
        {
            "name": "Review inventory integrity",
            "quantity": "10",
            "cost_price": "100",
            "sale_price": "150",
        }
    )["item"]
    card = runtime.service.create_card({"title": "Review inventory target"})["card"]
    await page.goto(runtime.browser_url)
    await _login_successfully(page)
    await page.click("#inventoryButton")
    await _wait_modal_open(page, "#inventoryModal")
    selector = f'button[data-inventory-item-id="{item["id"]}"]'
    await page.click(selector)
    await page.fill("#inventoryNameInput", "Stale draft must not overwrite peer")
    peer = runtime.service.save_inventory_item(
        {"item_id": item["id"], "name": "Peer inventory revision", "cost_price": "300"}
    )["item"]
    async with page.expect_response("**/api/save_inventory_item") as info:
        await page.click("#inventorySaveButton")
    response = await info.value
    assert response.status == 409, await response.text()
    assert response.request.post_data_json["expected_updated_at"] == item["updated_at"]
    assert runtime.service.get_inventory_item({"item_id": item["id"]})["item"] == peer
    await page.wait_for_function("() => !document.querySelector('#inventorySaveButton').disabled")
    await page.click('[data-close="inventory"]')
    await _wait_modal_closed(page, "#inventoryModal")

    # Reopening refreshes the item; incoming stock uses the visible price draft.
    await page.click("#inventoryButton")
    await _wait_modal_open(page, "#inventoryModal")
    await page.click(selector)
    await page.wait_for_function(
        "() => document.querySelector('#inventoryCostPriceInput').value==='300'"
    )
    await page.fill("#inventoryCostPriceInput", "321")
    await page.fill("#inventorySalePriceInput", "456")
    await page.fill("#inventoryReplenishQuantityInput", "2")
    async with page.expect_response("**/api/replenish_inventory_item") as info:
        await page.click("#inventoryReplenishButton")
    response = await info.value
    assert response.ok, await response.text()
    payload = response.request.post_data_json
    assert (payload["cost_price"], payload["sale_price"], payload["expected_updated_at"]) == (
        "321",
        "456",
        peer["updated_at"],
    )
    current_item = _api_data(await response.json())["item"]
    assert (current_item["quantity"], current_item["cost_price"], current_item["sale_price"]) == (
        "12",
        "321",
        "456",
    )
    await page.wait_for_function(
        "() => !document.querySelector('#inventoryReplenishButton').disabled"
    )
    await page.click('[data-close="inventory"]')
    await _wait_modal_closed(page, "#inventoryModal")

    await page.click(f'.card[data-card-id="{card["id"]}"]')
    await _wait_modal_open(page, "#cardModal")
    await page.click("#repairOrderButton")
    await _wait_modal_open(page, "#repairOrderModal")
    await page.click("#repairOrderInventoryToggleButton")
    await page.click(f'[data-repair-order-inventory-item-id="{item["id"]}"]')
    await page.fill("#repairOrderInventoryQuantityInput", "1")
    peer_card = runtime.service.update_card(
        {"card_id": card["id"], "title": "Peer card revision must survive"}
    )["card"]
    async with page.expect_response("**/api/write_off_inventory_item") as info:
        await page.click("#repairOrderInventoryIssueButton")
    response = await info.value
    assert response.status == 409, await response.text()
    payload = response.request.post_data_json
    assert payload["expected_card_updated_at"] != peer_card["updated_at"]
    assert payload["expected_updated_at"] == current_item["updated_at"]
    assert runtime.service.get_inventory_item({"item_id": item["id"]})["item"] == current_item
    assert runtime.service.get_card({"card_id": card["id"]})["card"]["title"] == peer_card["title"]
    assert not runtime.service.get_repair_order({"card_id": card["id"]})["repair_order"][
        "materials"
    ]
    return {"inventory_write_integrity": True}
