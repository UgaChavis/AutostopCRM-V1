"""Retired vehicle links remain visible without an implicit replacement."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from tests.browser_ui_support import UiBrowserTestCase


class CardLinkWarningBrowserTests(UiBrowserTestCase):
    def create_linked_card(self):
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
        return card_id, client, original_id

    def replace_client_vehicle(self, card_id, client, *, vehicle_id=None):
        replacement_id = vehicle_id or "SYNTHETIC_REPLACEMENT_" + card_id
        # Seed an old dangling link directly in the disposable fixture; current
        # mutation APIs correctly reject creating this state.
        with self.runtime.service._lock:
            bundle = self.runtime.state_store.read_bundle()
            stored_client = next(item for item in bundle["clients"] if item.id == client["id"])
            stored_client.vehicles[0].id = replacement_id
            stored_client.updated_at = datetime.now(UTC).isoformat()
            self.runtime.state_store.write_bundle(**bundle)
        vehicles = [{**client["vehicles"][0], "id": replacement_id}]
        return {**client, "vehicles": vehicles, "vehicles_preview": vehicles}, replacement_id

    def create_stale_link(self):
        card_id, client, original_id = self.create_linked_card()
        client, replacement_id = self.replace_client_vehicle(card_id, client)
        return card_id, client, original_id, replacement_id

    def test_reopening_cached_card_rechecks_client_changes_without_card_revision(self):
        card_id, client, original_id = self.create_linked_card()
        self.load_board()
        reads = []
        self.page.on(
            "request",
            lambda request: reads.append(request.url) if "/api/get_card?" in request.url else None,
        )
        self.open_card(card_id)
        self.assertFalse(self.page.locator("#cardLinkWarning").is_visible())
        revision = self.page.evaluate("state.activeCard.updated_at")
        self.page.locator("#cardModalCloseButtonTop").click()
        self.page.locator("#cardModal.is-open").wait_for(state="hidden")

        self.replace_client_vehicle(card_id, client)
        self.assertEqual(self.stored_card(card_id)["updated_at"], revision)
        before_poll = len(reads)
        self.page.evaluate("async () => { renderBoard(); await refreshSnapshot(); }")
        self.assertEqual(len(reads), before_poll, "Polling must not fetch every full card")
        self.open_card(card_id)
        self.assertEqual(len(reads), before_poll + 1)
        self.assertTrue(self.page.locator("#cardLinkWarning").is_visible())
        self.assertEqual(self.page.evaluate("state.activeCard.client_vehicle_id"), original_id)
        self.page.locator("#cardModalCloseButtonTop").click()
        self.page.locator("#cardModal.is-open").wait_for(state="hidden")

        self.replace_client_vehicle(card_id, client, vehicle_id=original_id)
        self.open_card(card_id)
        self.assertEqual(len(reads), before_poll + 2)
        self.assertFalse(self.page.locator("#cardLinkWarning").is_visible())
        self.assertNotIn("link_card_to_client", self.writes)
        self.assertNotIn("upsert_client_vehicle", self.writes)

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

    def test_mobile_detail_shows_warning_after_cached_reopen_and_ordinary_save(self):
        card_id, client, original_id = self.create_linked_card()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.wait_for_function("document.body.classList.contains('is-mobile-lite')")
        self.page.evaluate(
            "id => { state.mobileExpandedColumns.add(snapshotCardById(id).column); renderMobileBoard(); }",
            card_id,
        )
        card_button = self.page.locator(f'#mobileBoardColumns [data-mobile-card-id="{card_id}"]')
        card_button.click()
        self.page.wait_for_function("!state.mobileCardLoading && Boolean(state.mobileCard?.id)")
        warning = self.page.locator("#mobileCardLinkWarning")
        self.assertFalse(warning.is_visible())
        revision = self.page.evaluate("state.mobileCard.updated_at")
        self.page.locator("#mobileCardBackButton").click()
        self.replace_client_vehicle(card_id, client)
        self.assertEqual(self.stored_card(card_id)["updated_at"], revision)
        card_button.click()
        self.page.wait_for_function("!state.mobileCardLoading && Boolean(state.mobileCard?.id)")
        self.assertTrue(warning.is_visible())
        self.assertIn("Связь карточки с автомобилем клиента не найдена", warning.inner_text())
        box = warning.bounding_box()
        self.assertGreater(box["width"], 0)
        self.assertLessEqual(box["x"] + box["width"], 390)
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC mobile title safely edited")
        with self.page.expect_response("**/api/update_card") as response:
            self.page.locator("#mobileCardSaveButton").click()
        self.assertEqual(response.value.status, 200, response.value.json())
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertTrue(warning.is_visible())
        saved = self.stored_card(card_id)
        self.assertEqual(saved["client_id"], client["id"])
        self.assertEqual(saved["client_vehicle_id"], original_id)
        self.assertNotIn("link_card_to_client", self.writes)
        self.assertNotIn("upsert_client_vehicle", self.writes)
        screenshots = os.environ.get("AUTOSTOP_CRM_WARNING_SCREENSHOTS")
        if screenshots:
            directory = Path(screenshots)
            directory.mkdir(parents=True, exist_ok=True)
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
