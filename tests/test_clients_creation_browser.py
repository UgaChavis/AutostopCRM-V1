"""Confirmed client IDs, draft ownership and vehicle stages on real disposable UI."""

from __future__ import annotations

import json
import time

from tests.browser_ui_support import UiBrowserTestCase


class ClientsCreationBrowserTests(UiBrowserTestCase):
    def test_lost_link_reply_is_read_back_without_second_link_or_vehicle(self) -> None:
        card_id = self.open_creation("Client lost link once")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/link_card_to_client", lose_reply)
        self.save_client()
        self.assertFalse(self.page.locator("#cardClientCreateModal.is-open").count())
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)
        self.assertTrue(self.stored_card(card_id)["client_vehicle_id"])

    def test_lost_create_reply_keeps_draft_and_blocks_blind_repeat(self) -> None:
        self.open_creation("Client uncertain create once")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/create_client", lose_reply)
        self.save_client()
        self.save_client()
        self.assertTrue(self.page.locator("#cardClientCreateModal.is-open").is_visible())
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
        self.assertEqual(
            self.page.locator("#cardClientCreateNameInput").input_value(),
            "Client uncertain create once",
        )

    def open_creation(self, name: str, *, saved_card: bool = True) -> str:
        card_id = (
            self.create_card("Client workflow card", vehicle="Toyota UI review")
            if saved_card
            else ""
        )
        self.load_board()
        if card_id:
            self.open_card(card_id)
        else:
            self.page.locator("[data-create-in]").first.click()
            self.page.locator("#cardTitle").fill("Client workflow new draft")
        self.page.evaluate(
            "applyVehicleProfileToForm({...readVehicleProfileForm(),display_name:'Toyota UI review',make_display:'Toyota',model_display:'UI review'}); openCardClientCreateModal();"
        )
        self.page.locator("#cardClientCreateNameInput").fill(name)
        self.page.locator("#cardClientCreateVehicleInput").check()
        return card_id

    def save_client(self) -> None:
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("state.cardClientCreateSaving === false")

    @staticmethod
    def fail_stage(route) -> None:
        route.fulfill(
            status=422,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "validation_error",
                        "message": "Synthetic second-stage failure",
                    },
                }
            ),
        )

    def test_failed_link_retries_same_client_and_preserves_popup_draft(self) -> None:
        card_id = self.open_creation("Client link retry once")
        self.page.route("**/api/link_card_to_client", self.fail_stage)
        self.save_client()
        self.assertTrue(self.page.locator("#cardClientCreateModal.is-open").is_visible())
        self.assertEqual(
            self.page.locator("#cardClientCreateNameInput").input_value(), "Client link retry once"
        )
        self.page.unroute("**/api/link_card_to_client", self.fail_stage)
        self.save_client()
        self.assertFalse(self.page.locator("#cardClientCreateModal.is-open").count())
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("link_card_to_client"), 2)
        self.assertTrue(self.stored_card(card_id)["client_id"])

    def test_unsaved_card_vehicle_failure_retries_vehicle_not_client(self) -> None:
        self.open_creation("Client vehicle retry once", saved_card=False)
        self.page.route("**/api/upsert_client_vehicle", self.fail_stage)
        self.save_client()
        self.assertTrue(self.page.locator("#cardClientCreateModal.is-open").is_visible())
        self.page.unroute("**/api/upsert_client_vehicle", self.fail_stage)
        self.save_client()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 2)
        self.assertTrue(self.page.evaluate("state.pendingCardClientVehicleId"))
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_closed()
        self.assertEqual(self.writes.count("create_card"), 1)

    def test_link_result_preserves_unsaved_card_text(self) -> None:
        card_id = self.open_creation("Client link retains card draft")
        self.page.evaluate("closeCardClientCreateModal()")
        self.page.locator("#cardTitle").fill("Card draft before client link")
        self.page.locator("#cardDescriptionEditor").fill("Unsaved description before client link")
        self.page.evaluate("openCardClientCreateModal()")
        self.page.locator("#cardClientCreateNameInput").fill("Client link retains card draft")
        self.save_client()
        self.assertEqual(
            self.page.locator("#cardTitle").input_value(), "Card draft before client link"
        )
        self.assertEqual(
            self.page.locator("#cardDescriptionEditor").inner_text(),
            "Unsaved description before client link",
        )
        self.assertEqual(self.stored_card(card_id)["title"], "Client workflow card")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card_id)["title"], "Card draft before client link")

    def wait_for_intercepted_creation(self, held: list) -> None:
        deadline = time.monotonic() + 5.0
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(10)
        self.assertEqual(len(held), 1, "creation request did not reach the held network boundary")

    def test_pending_creation_blocks_edits_and_duplicate_submit(self) -> None:
        self.open_creation("Client pending controls")
        held = []
        self.page.route("**/api/create_client", lambda route: held.append(route))
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("state.cardClientCreateSaving === true")
        self.wait_for_intercepted_creation(held)
        self.assertTrue(self.page.locator("#cardClientCreateNameInput").is_disabled())
        self.page.evaluate("els.cardClientCreateSaveButton.click()")
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function("state.cardClientCreateSaving === false")
        self.assertEqual(self.writes.count("create_client"), 1)

    def test_closed_popup_cannot_receive_old_result_or_close_new_draft(self) -> None:
        self.open_creation("Old client popup")
        held = []
        self.page.route("**/api/create_client", lambda route: held.append(route))
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("state.cardClientCreateSaving === true")
        self.wait_for_intercepted_creation(held)
        self.page.locator('[data-close="card-client-create"]').first.click()
        self.page.evaluate("openCardClientCreateModal()")
        self.page.locator("#cardClientCreateNameInput").fill("New client popup draft")
        held.pop().continue_()
        self.page.wait_for_timeout(180)
        self.assertTrue(self.page.locator("#cardClientCreateModal.is-open").is_visible())
        self.assertEqual(
            self.page.locator("#cardClientCreateNameInput").input_value(), "New client popup draft"
        )
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
