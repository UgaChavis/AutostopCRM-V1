"""Short client profiles must disclose unverified legacy associations."""

import json
import re

from tests.browser_ui_support import UiBrowserTestCase


class ClientProfileUnverifiedBrowserTests(UiBrowserTestCase):
    def test_short_profiles_for_both_roles_and_views(self):
        service = self.runtime.service
        legacy_name = "SYNTHETIC LEGACY ASSOCIATION"
        legacy = service.create_client({"display_name": legacy_name})["client"]["id"]
        confirmed = service.create_client({"display_name": "SYNTHETIC CONFIRMED ASSOCIATION"})[
            "client"
        ]["id"]
        old_card = self.create_card("SYNTHETIC OLD CARD", vehicle="SYNTHETIC OLD VEHICLE")
        service.update_card(
            {"card_id": old_card, "repair_order": {"client": legacy_name, "status": "open"}}
        )
        known_card = self.create_card(
            "SYNTHETIC KNOWN CARD", client_id=confirmed, vehicle="SYNTHETIC KNOWN VEHICLE"
        )
        service.get_repair_order({"card_id": known_card, "create_if_missing": True})
        legacy_profile = service.get_client({"client_id": legacy})
        confirmed_profile = service.get_client({"client_id": confirmed})
        self.assertIn(
            "related_card_client_match_unverified", legacy_profile["meta"]["read_warnings"]
        )
        self.assertNotIn(
            "related_card_client_match_unverified",
            confirmed_profile["meta"].get("read_warnings", []),
        )
        for profile in (legacy_profile, confirmed_profile):
            self.assertEqual(len(profile["vehicles"]), 1)
            self.assertEqual(len(profile["repair_orders"]), 1)

        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-profile-viewer",
                "password": "synthetic-profile-password",
                "role": "operator",
            },
        )
        self.assertEqual(response.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-profile-viewer", "password": "synthetic-profile-password"},
        ).json()["data"]["session"]["token"]
        warning = re.compile(r"СВЯЗ.{0,35}НЕ\s+ПОДТВЕРЖД", re.IGNORECASE)
        for role, session in (("admin", self.session_token), ("operator", token)):
            for device, width in (("desktop", 1440), ("mobile", 390)):
                with self.subTest(role=role, device=device):
                    context = self.browser.new_context(
                        viewport={"width": width, "height": 900},
                        extra_http_headers=self.runtime.auth_headers,
                    )
                    self.addCleanup(context.close)
                    context.add_init_script(
                        "localStorage.setItem('kanban-operator-session', "
                        + json.dumps(session)
                        + ");"
                    )
                    page = context.new_page()
                    page.on("pageerror", lambda error: self.page_errors.append(str(error)))
                    page.goto(self.runtime.browser_url)
                    page.wait_for_function(
                        "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
                    )
                    page.evaluate("stopSnapshotPolling()")
                    if device == "mobile":
                        page.click('[data-mobile-view="more"]')
                        page.click('[data-mobile-open="clients"]')
                        rows, pane = (
                            "#mobileClientsList [data-mobile-client-id=",
                            "#mobileClientDetail",
                        )
                        row_key = "data-mobile-client-id"
                    else:
                        page.click("#clientsButton")
                        rows, pane = "#clientsList [data-client-id=", ".clients-profile-pane"
                        row_key = "data-client-id"
                    page.wait_for_function(
                        "ids => state.clientsLoaded && ids.every(id => state.clients.some(client => client.id === id))",
                        arg=[legacy, confirmed],
                    )

                    def select(client_id):
                        page.locator(f'{rows}"{client_id}"]').click()
                        page.wait_for_function(
                            "id => state.clientsActiveProfile?.client?.id === id && !state.mobileClientProfileLoading",
                            arg=client_id,
                        )

                    select(legacy)
                    self.assertIn(
                        "related_card_client_match_unverified",
                        page.evaluate("state.clientsActiveProfile.meta.read_warnings"),
                    )
                    with self.subTest(case="legacy_warning"):
                        self.assertRegex(page.locator(pane).inner_text(), warning)
                    self.assertIn("SYNTHETIC OLD VEHICLE", page.locator(pane).inner_text())
                    select(confirmed)
                    with self.subTest(case="confirmed"):
                        self.assertNotRegex(page.locator(pane).inner_text(), warning)
                    page.evaluate(
                        """device => {
                          state.clientsActiveProfile.meta.read_warnings = ['repair_order_payment_identity_unverified'];
                          if (device === 'mobile') renderMobileClientDetail();
                          else renderClientProfile(state.clientsActiveProfile);
                        }""",
                        device,
                    )
                    with self.subTest(case="other_warning"):
                        self.assertNotRegex(page.locator(pane).inner_text(), warning)
                    self.assertEqual(page.locator(f'[{row_key}="{confirmed}"]').count(), 1)
                    context.close()
