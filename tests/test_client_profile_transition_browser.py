"""Desktop and mobile client selection share state on a disposable CRM."""

from __future__ import annotations

from tests.browser_ui_support import UiBrowserTestCase


class ClientProfileTransitionBrowserTests(UiBrowserTestCase):
    def prepare_clients(self) -> tuple[str, str]:
        self.page.set_default_timeout(8000)
        first, second = [
            self.runtime.service.create_client({"display_name": name})["client"]["id"]
            for name in ("AAA SYNTHETIC A", "AAB SYNTHETIC B")
        ]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.wait_profile(first)
        return first, second

    def wait_profile(self, client_id: str) -> None:
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client_id
        )

    def open_mobile_clients(self) -> None:
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("document.body.classList.contains('is-mobile-lite')")
        self.page.click('[data-mobile-view="more"]')
        if self.page.locator("#mobileClientsPanel").is_hidden():
            self.page.click('[data-mobile-open="clients"]')

    def open_desktop_clients(self) -> None:
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.wait_for_function("!document.body.classList.contains('is-mobile-lite')")
        self.page.locator("#clientsButton").click()

    def test_clean_transition_loads_the_selected_client_for_desktop(self) -> None:
        first, second = self.prepare_clients()
        self.page.locator('[data-close="clients"]').click()
        self.open_mobile_clients()
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{second}"]').click()
        self.wait_profile(second)
        self.assertTrue(
            self.page.evaluate(
                "() => beginClientMutation({allowNew:true}) === null && beginClientMutation() === null"
            )
        )
        with self.page.expect_request(
            lambda request: "/api/get_client?" in request.url and "order_limit=30" in request.url
        ):
            self.open_desktop_clients()
        self.wait_profile(second)
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "AAB SYNTHETIC B"
        )
        self.assertNotEqual(first, second)
        self.page.locator('[data-close="clients"]').click()
        self.open_mobile_clients()
        self.assertIn("AAB SYNTHETIC B", self.page.locator("#mobileClientDetail").inner_text())

    def test_mobile_first_open_loads_a_full_desktop_profile(self) -> None:
        client_id = self.runtime.service.create_client({"display_name": "AAA SYNTHETIC FIRST"})[
            "client"
        ]["id"]
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.open_mobile_clients()
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{client_id}"]').click()
        self.wait_profile(client_id)
        with self.page.expect_request(
            lambda request: "/api/get_client?" in request.url and "order_limit=30" in request.url
        ):
            self.open_desktop_clients()
        self.wait_profile(client_id)
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "AAA SYNTHETIC FIRST"
        )

    def test_mobile_selection_survives_empty_search_results(self) -> None:
        _first, second = self.prepare_clients()
        self.page.locator('[data-close="clients"]').click()
        self.open_mobile_clients()
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{second}"]').click()
        self.wait_profile(second)
        self.page.locator("#mobileClientsSearchInput").fill("NO SYNTHETIC MATCH")
        self.page.wait_for_function("() => state.clientsLoaded && state.clients.length === 0")
        with self.page.expect_request(
            lambda request: "/api/get_client?" in request.url and "order_limit=30" in request.url
        ):
            self.open_desktop_clients()
        self.wait_profile(second)
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "AAB SYNTHETIC B"
        )

    def test_same_client_mobile_refresh_reloads_clean_desktop(self) -> None:
        first, _second = self.prepare_clients()
        self.page.locator('[data-close="clients"]').click()
        self.runtime.service.upsert_client_vehicle(
            {"client_id": first, "vehicle": {"vehicle": "SYNTHETIC NEW VEHICLE"}}
        )
        self.open_mobile_clients()
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{first}"]').click()
        self.wait_profile(first)
        with self.page.expect_request(
            lambda request: "/api/get_client?" in request.url and "order_limit=30" in request.url
        ):
            self.open_desktop_clients()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id && state.clientsActiveProfile === state.clientsDesktopProfile",
            arg=first,
        )
        self.page.locator("#clientVehiclesList").get_by_text("SYNTHETIC NEW VEHICLE").wait_for()

    def test_same_client_mobile_refresh_preserves_dirty_snapshot(self) -> None:
        first, _second = self.prepare_clients()
        self.page.locator("#clientCommentInput").fill("SYNTHETIC UNSAVED DRAFT")
        self.page.locator('[data-close="clients"]').click()
        self.runtime.service.upsert_client_vehicle(
            {"client_id": first, "vehicle": {"vehicle": "SYNTHETIC NEW VEHICLE"}}
        )
        self.open_mobile_clients()
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{first}"]').click()
        self.wait_profile(first)
        self.assertFalse(self.page.evaluate("clientDesktopSelectionMatches()"))
        self.open_desktop_clients()
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC UNSAVED DRAFT"
        )
        self.assertTrue(
            self.page.evaluate("state.clientsActiveProfile === state.clientsDesktopProfile")
        )
        self.assertEqual(self.page.evaluate("activeClientVehicles().length"), 0)

    def test_dirty_desktop_draft_wins_over_delayed_mobile_selection(self) -> None:
        first, second = self.prepare_clients()
        self.page.locator("#clientCommentInput").fill("SYNTHETIC UNSAVED DRAFT")
        self.page.locator('[data-close="clients"]').click()
        self.open_mobile_clients()
        held = []
        self.page.route("**/api/get_client?**", lambda route: held.append(route))
        with self.page.expect_request("**/api/get_client?**"):
            self.page.locator(f'#mobileClientsList [data-mobile-client-id="{second}"]').click()
        self.open_desktop_clients()
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC UNSAVED DRAFT"
        )
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), first)
        with self.page.expect_response("**/api/get_client?**"):
            held.pop().continue_()
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), first)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC UNSAVED DRAFT"
        )

    def test_new_client_draft_survives_mobile_selection(self) -> None:
        _first, second = self.prepare_clients()
        self.page.locator("#clientNewButton").click()
        self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC NEW DRAFT")
        self.page.locator('[data-close="clients"]').click()
        self.open_mobile_clients()
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{second}"]').click()
        self.wait_profile(second)
        self.open_desktop_clients()
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC NEW DRAFT"
        )
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), "")

    def test_vehicle_editor_draft_survives_mobile_selection(self) -> None:
        first, second = self.prepare_clients()
        self.page.locator("#clientVehicleAddButton").click()
        vehicle = self.page.locator(
            '[data-client-vehicle-editor="__new__"] [data-client-vehicle-field="vehicle"]'
        )
        vehicle.fill("SYNTHETIC UNSAVED VEHICLE")
        self.page.locator('[data-close="clients"]').click()
        self.open_mobile_clients()
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{second}"]').click()
        self.wait_profile(second)
        self.page.evaluate(
            """async () => handleClientVehiclesClick({
              target: document.querySelector('[data-client-vehicle-cancel="__new__"]'),
              preventDefault() {}
            })"""
        )
        self.assertEqual(self.page.evaluate("state.clientVehicleEditor?.key"), "__new__")
        self.open_desktop_clients()
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), first)
        self.assertEqual(vehicle.input_value(), "SYNTHETIC UNSAVED VEHICLE")

    def test_failed_mobile_and_desktop_reads_hide_the_previous_client(self) -> None:
        _first, second = self.prepare_clients()
        self.page.locator('[data-close="clients"]').click()
        self.open_mobile_clients()
        self.page.route(
            "**/api/get_client?**",
            lambda route: route.fulfill(
                status=500, json={"ok": False, "error": {"message": "synthetic"}}
            ),
        )
        with self.page.expect_response("**/api/get_client?**"):
            self.page.locator(f'#mobileClientsList [data-mobile-client-id="{second}"]').click()
        self.page.wait_for_function("() => !state.mobileClientProfileLoading")
        with self.page.expect_response("**/api/get_client?**"):
            self.open_desktop_clients()
        self.assertFalse(self.page.locator("#clientDisplayNameInput").is_visible())
        self.assertTrue(self.page.evaluate("() => beginClientMutation({allowNew:true}) === null"))

    def test_mobile_cannot_change_client_during_desktop_write(self) -> None:
        first, second = self.prepare_clients()
        self.page.locator("#clientCommentInput").fill("SYNTHETIC UPDATE")
        held = []
        self.page.route("**/api/update_client", lambda route: held.append(route))
        with self.page.expect_request("**/api/update_client"):
            self.page.locator("#clientSaveButton").click()
        self.page.locator('[data-close="clients"]').click()
        self.open_mobile_clients()
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{second}"]').click()
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), first)
        with self.page.expect_response("**/api/update_client"):
            held.pop().continue_()
        self.page.wait_for_function("() => !state.clientMutationRequest")
        self.assertEqual(
            self.runtime.service.get_client({"client_id": second})["client"]["comment"], ""
        )
