"""Retired vehicle links remain visible without an implicit replacement."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

from tests.browser_ui_support import UiBrowserTestCase


class CardLinkWarningBrowserTests(UiBrowserTestCase):
    def create_stale_link(self):
        service = self.runtime.service
        client = service.create_client(
            {"display_name": "SYNTHETIC LINK CLIENT", "vehicles": [{"vehicle": "SYNTHETIC CAR"}]}
        )["client"]
        original_id = client["vehicles"][0]["id"]
        card_id = self.create_card(
            "SYNTHETIC retired vehicle link",
            client_id=client["id"],
            client_vehicle_id=original_id,
        )
        store = service._store
        bundle = store.read_bundle()
        clients = deepcopy(bundle["clients"])
        replacement = next(item for item in clients if item.id == client["id"])
        replacement.vehicles[0].id = "SYNTHETIC_REPLACEMENT_" + card_id
        store.write_cached_bundle(bundle, **{**bundle, "clients": clients})
        client["vehicles"] = [vehicle.to_dict() for vehicle in replacement.vehicles]
        client["vehicles_preview"] = client["vehicles"]
        return card_id, client, original_id, replacement.vehicles[0].id

    def test_stale_link_warning_survives_ordinary_save_without_rebinding(self):
        card_id, client, original_id, _replacement_id = self.create_stale_link()
        self.load_board()
        self.open_card(card_id)
        warning = self.page.locator("#cardLinkWarning")
        self.assertTrue(warning.is_visible())
        self.assertIn("Связь карточки с автомобилем клиента не найдена", warning.inner_text())
        self.assertEqual(self.page.evaluate("state.pendingCardClientVehicleId"), original_id)
        self.assertTrue(self.page.locator("#cardTitle").is_editable())
        screenshots = os.environ.get("AUTOSTOP_CRM_WARNING_SCREENSHOTS")
        if screenshots:
            directory = Path(screenshots)
            directory.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(directory / "stale-link-desktop.png"))
        self.page.locator("#cardTitle").fill("SYNTHETIC title safely edited")
        with self.page.expect_response("**/api/update_card") as response:
            self.page.locator("#saveCardButton").click()
        self.assertEqual(response.value.status, 200, response.value.json())
        self.finish_save()
        self.page.wait_for_function("!els.cardModal.classList.contains('is-open')")
        self.assert_card_closed()
        saved = self.stored_card(card_id)
        self.assertEqual(saved["client_id"], client["id"])
        self.assertEqual(saved["client_vehicle_id"], original_id)
        self.assertNotIn("link_card_to_client", self.writes)
        self.assertNotIn("upsert_client_vehicle", self.writes)
        self.open_card(card_id)
        self.assertTrue(warning.is_visible())
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertTrue(warning.is_visible())
        box = warning.bounding_box()
        self.assertGreater(box["width"], 0)
        self.assertLessEqual(box["x"] + box["width"], 390)
        if screenshots:
            self.page.screenshot(path=str(directory / "stale-link-mobile.png"))

    def test_only_explicit_vehicle_selection_replaces_the_stale_link(self):
        card_id, client, original_id, replacement_id = self.create_stale_link()
        self.load_board()
        self.open_card(card_id)
        self.assertEqual(self.stored_card(card_id)["client_vehicle_id"], original_id)
        self.page.evaluate(
            "client => renderClientSuggestions([client], {query: 'SYNTHETIC LINK', showEmpty: true})",
            client,
        )
        # The button is an operator choice; loading or ordinary save never chooses it.
        self.page.locator(
            '#clientMatchList [data-select-client-vehicle][data-client-id="' + client["id"] + '"]'
        ).first.click()
        self.page.wait_for_function(
            "id => state.activeCard?.client_vehicle_id === id", arg=replacement_id
        )
        self.assertEqual(self.stored_card(card_id)["client_vehicle_id"], replacement_id)
        self.assertFalse(self.page.locator("#cardLinkWarning").is_visible())
        self.assertEqual(self.writes.count("link_card_to_client"), 1)
