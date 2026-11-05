"""Client vehicle projections require an identifiable source before editing."""

import json
from pathlib import Path

from tests.browser_ui_support import UiBrowserTestCase


class ClientVehicleIdentityBrowserTests(UiBrowserTestCase):
    def test_identity_guard_with_legacy_and_card_projection(self):
        service = self.runtime.service
        client = service.create_client({"display_name": "SYNTHETIC IDENTITY CLIENT"})["client"][
            "id"
        ]
        vehicle = service.upsert_client_vehicle(
            {"client_id": client, "vehicle": {"vehicle": "SYNTHETIC STORED CAR"}}
        )["vehicle"]["id"]
        card = self.create_card(
            "SYNTHETIC CARD SOURCE", client_id=client, vehicle="SYNTHETIC CARD ONLY"
        )
        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-identity-viewer",
                "password": "synthetic-password",
                "role": "operator",
            },
        )
        self.assertEqual(response.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-identity-viewer", "password": "synthetic-password"},
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

        # Keep the raw legacy alias in the disposable state; the service returns canonical id.
        state_path = Path(self.runtime.temp_dir.name) / "state.json"
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        legacy_client = next(item for item in raw["clients"] if item["id"] == client)
        legacy_vehicle = next(item for item in legacy_client["vehicles"] if item["id"] == vehicle)
        legacy_vehicle["client_vehicle_id"] = legacy_vehicle.pop("id")
        state_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        profile = service.get_client({"client_id": client})
        self.assertEqual(profile["client"]["vehicles"][0]["id"], vehicle)
        self.assertTrue(any(v["card_id"] == card and not v["id"] for v in profile["vehicles"]))

        self.load_board()
        viewer.goto(self.runtime.browser_url)
        viewer.wait_for_function(
            "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
        )
        viewer.evaluate("stopSnapshotPolling()")
        for page in (self.page, viewer):
            for mobile in (False, True):
                with self.subTest(restricted=page is viewer, mobile=mobile):
                    page.set_viewport_size(
                        {"width": 390, "height": 844} if mobile else {"width": 1440, "height": 960}
                    )
                    if mobile:
                        page.click('[data-mobile-view="more"]')
                        page.click('[data-mobile-open="clients"]')
                        row = page.locator(f'#mobileClientsList [data-mobile-client-id="{client}"]')
                    else:
                        page.locator("#clientsButton").click()
                        row = page.locator(f'#clientsList [data-client-id="{client}"]')
                    mode = [""]

                    def mutate(route):
                        original = route.fetch()
                        result = original.json()
                        data = result["data"]
                        if mode[0].startswith("stored_"):
                            data["client"]["vehicles"][0] = {
                                "stored_empty": {},
                                "stored_nonstring": {"id": 123},
                                "stored_spaces": {"id": " x "},
                            }[mode[0]]
                        elif mode[0]:
                            data["vehicles"][0] = {
                                "projection_empty": {},
                                "projection_blank": {"id": "", "card_id": ""},
                                "projection_missing_id": {"card_id": card},
                                "projection_missing_card": {"id": vehicle},
                                "projection_nonstring": {"id": 123, "card_id": ""},
                                "projection_null": {"id": None, "card_id": card},
                                "projection_spaces": {"id": "", "card_id": " x "},
                            }[mode[0]]
                        route.fulfill(response=original, json=result)

                    page.route("**/api/get_client?**", mutate)
                    for corrupt in (
                        "stored_empty",
                        "stored_nonstring",
                        "stored_spaces",
                        "projection_empty",
                        "projection_blank",
                        "projection_missing_id",
                        "projection_missing_card",
                        "projection_nonstring",
                        "projection_null",
                        "projection_spaces",
                    ):
                        mode[0] = corrupt
                        page.evaluate("setStatus('', false)")
                        row.click()
                        page.wait_for_function(
                            "document.querySelector('#statusLine').textContent.includes('ПРОФИЛЬ КЛИЕНТА НЕ ПОДТВЕРЖДЁН')"
                        )
                        self.assertIsNone(page.evaluate("state.clientsActiveProfile"))
                        if mobile:
                            self.assertIn(
                                "ПРОФИЛЬ НЕ ЗАГРУЖЕН",
                                page.locator("#mobileClientDetail").inner_text(),
                            )
                        else:
                            self.assertEqual(
                                page.locator(
                                    "#clientVehiclesList [data-client-vehicle-edit]"
                                ).count(),
                                0,
                            )
                    mode[0] = ""
                    row.click()
                    page.wait_for_function(
                        "id => state.clientsActiveProfile?.client?.id === id", arg=client
                    )
                    text = page.locator(
                        "#mobileClientDetail" if mobile else "#clientVehiclesList"
                    ).inner_text()
                    self.assertIn("SYNTHETIC STORED CAR", text)
                    self.assertIn("SYNTHETIC CARD ONLY", text)
                    if not mobile:
                        card_entry = page.locator(f'[data-client-vehicle-key="c:{card}"]')
                        card_entry.locator("[data-client-vehicle-edit]").click()
                        page.locator('[data-client-vehicle-field="vehicle"]').fill(
                            "SYNTHETIC DRAFT"
                        )
                        page.locator("[data-client-vehicle-save]").click()
                        page.wait_for_function(
                            "document.querySelector('#statusLine').textContent.includes('ИЗМЕНИТЕ АВТОМОБИЛЬ В СВЯЗАННОЙ КАРТОЧКЕ')"
                        )
                        self.assertEqual(
                            page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
                            "SYNTHETIC DRAFT",
                        )
                        page.locator("[data-client-vehicle-cancel]").click()
                        page.locator(
                            f'[data-client-vehicle-key="v:{vehicle}"] [data-client-vehicle-edit]'
                        ).click()
                        self.assertEqual(
                            page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
                            "SYNTHETIC STORED CAR",
                        )
                        page.locator("[data-client-vehicle-cancel]").click()
                    self.assertFalse(
                        any(
                            item in self.writes + viewer_writes
                            for item in (
                                "upsert_client_vehicle",
                                "delete_client_vehicle",
                                "update_client",
                            )
                        )
                    )
                    page.unroute("**/api/get_client?**", mutate)
                    if not mobile:
                        page.locator('[data-close="clients"]').click()
