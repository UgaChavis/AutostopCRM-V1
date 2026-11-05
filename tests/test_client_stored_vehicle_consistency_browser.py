"""Stored vehicle projections must match their editable source, not just an ID."""

import json
from pathlib import Path

from tests.browser_ui_support import UiBrowserTestCase


class ClientStoredVehicleConsistencyBrowserTests(UiBrowserTestCase):
    def test_projections_for_both_roles_and_views(self):
        service = self.runtime.service
        client = service.create_client(
            {
                "display_name": "SYNTHETIC CONSISTENCY",
                "vehicles": [
                    {"vehicle": "SYNTHETIC CAR A", "vin": "VIN-A", "license_plate": "А111АА77"},
                    {"vehicle": "SYNTHETIC CAR B", "vin": "VIN-B"},
                ],
            }
        )["client"]["id"]
        card = self.create_card("SYNTHETIC CARD", client_id=client, vehicle="SYNTHETIC CARD ONLY")
        state_path = Path(self.runtime.temp_dir.name) / "state.json"
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        legacy = next(item for item in raw["clients"] if item["id"] == client)["vehicles"][0]
        legacy["client_vehicle_id"] = legacy.pop("id")
        state_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        original = service.get_client({"client_id": client})
        self.assertEqual(len(original["client"]["vehicles"]), 2)
        self.assertTrue(any(v["card_id"] == card and not v["id"] for v in original["vehicles"]))

        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-consistency-operator",
                "password": "synthetic-password",
                "role": "operator",
            },
        )
        self.assertEqual(response.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-consistency-operator", "password": "synthetic-password"},
        ).json()["data"]["session"]["token"]
        restricted = self.browser.new_context(
            viewport={"width": 1440, "height": 960}, extra_http_headers=self.runtime.auth_headers
        )
        self.addCleanup(restricted.close)
        restricted.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ");"
        )
        viewer = restricted.new_page()
        viewer.on("pageerror", lambda error: self.page_errors.append(str(error)))
        viewer_writes = []
        viewer.on(
            "request",
            lambda request: (
                viewer_writes.append(request.url.rsplit("/", 1)[-1])
                if request.method == "POST"
                else None
            ),
        )
        self.load_board()
        viewer.goto(self.runtime.browser_url)
        viewer.wait_for_function(
            "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
        )
        viewer.evaluate("stopSnapshotPolling()")

        for page in (self.page, viewer):
            for mobile in (False, True):
                with self.subTest(restricted=page is viewer, mobile=mobile):
                    page.set_viewport_size({"width": 390 if mobile else 1440, "height": 900})
                    if mobile:
                        page.click('[data-mobile-view="more"]')
                        page.click('[data-mobile-open="clients"]')
                        row = page.locator(f'#mobileClientsList [data-mobile-client-id="{client}"]')
                    else:
                        page.locator("#clientsButton").click()
                        row = page.locator(f'#clientsList [data-client-id="{client}"]')
                    mode = [""]

                    def respond(route):
                        response = route.fetch()
                        result = response.json()
                        data = result["data"]
                        stored = [v for v in data["vehicles"] if not v["card_id"]]
                        if mode[0] == "swapped":
                            stored[0]["id"], stored[1]["id"] = stored[1]["id"], stored[0]["id"]
                        elif mode[0] == "changed_field":
                            stored[0]["vehicle"] = "SYNTHETIC WRONG"
                        elif mode[0] == "unknown_id":
                            stored[0]["id"] = "synthetic-missing-id"
                        elif mode[0] == "alias":
                            stored[0]["registration_plate"] = "SYNTHETIC WRONG"
                        elif mode[0] == "alias_empty":
                            stored[1]["registration_plate"] = "SYNTHETIC WRONG"
                        elif mode[0] == "alias_equal":
                            stored[0]["registration_plate"] = stored[0]["license_plate"]
                        elif mode[0] == "missing_field":
                            stored[0].pop("vin")
                        elif mode[0] == "omitted_stored":
                            data["vehicles"].remove(stored[1])
                            data["meta"]["vehicles_total"] -= 1
                            data["client"]["stats"]["vehicles_total"] -= 1
                        elif mode[0] == "card_only_unmatched":
                            next(v for v in data["vehicles"] if v["card_id"])["id"] = (
                                "unmatched-card-vehicle"
                            )
                        route.fulfill(response=response, json=result)

                    page.route("**/api/get_client?**", respond)
                    for mode[0] in (
                        "swapped",
                        "changed_field",
                        "unknown_id",
                        "alias",
                        "alias_empty",
                        "missing_field",
                    ):
                        page.evaluate("setStatus('', false)")
                        row.click()
                        page.wait_for_function(
                            "document.querySelector('#statusLine').textContent.includes('ПРОФИЛЬ КЛИЕНТА НЕ ПОДТВЕРЖДЁН')"
                        )
                        self.assertIsNone(page.evaluate("state.clientsActiveProfile"))
                        if not mobile:
                            self.assertEqual(
                                page.locator(
                                    "#clientVehiclesList [data-client-vehicle-edit]"
                                ).count(),
                                0,
                            )
                    for mode[0] in ("", "alias_equal", "omitted_stored", "card_only_unmatched"):
                        page.evaluate("setStatus('', false)")
                        row.click()
                        page.wait_for_function(
                            "id => state.clientsActiveProfile?.client?.id === id", arg=client
                        )
                        visible = page.locator(
                            "#mobileClientDetail" if mobile else "#clientVehiclesList"
                        ).inner_text()
                        self.assertIn("SYNTHETIC CAR A", visible)
                        self.assertIn("SYNTHETIC CARD ONLY", visible)
                    self.assertFalse(
                        any(
                            x in self.writes + viewer_writes
                            for x in (
                                "update_client",
                                "upsert_client_vehicle",
                                "delete_client_vehicle",
                            )
                        )
                    )
                    page.unroute("**/api/get_client?**", respond)
                    if not mobile:
                        page.locator('[data-close="clients"]').click()
