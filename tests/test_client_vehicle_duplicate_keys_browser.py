"""Ambiguous vehicle keys must not select or modify another vehicle."""

import json
from copy import deepcopy
from pathlib import Path

from tests.browser_ui_support import UiBrowserTestCase


class ClientVehicleDuplicateKeysBrowserTests(UiBrowserTestCase):
    def test_ambiguous_keys_and_distinct_sources(self):
        service = self.runtime.service
        client = service.create_client({"display_name": "SYNTHETIC KEY CLIENT"})["client"]["id"]
        vehicle = service.upsert_client_vehicle(
            {"client_id": client, "vehicle": {"vehicle": "SYNTHETIC STORED"}}
        )["vehicle"]["id"]
        card = self.create_card("SYNTHETIC CARD", client_id=client, vehicle="SYNTHETIC FROM CARD")
        state_path = Path(self.runtime.temp_dir.name) / "state.json"
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        legacy = next(item for item in raw["clients"] if item["id"] == client)["vehicles"][0]
        legacy["client_vehicle_id"] = legacy.pop("id")
        state_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        original = service.get_client({"client_id": client})
        self.assertEqual(original["client"]["vehicles"][0]["id"], vehicle)
        self.assertTrue(any(v["card_id"] == card and not v["id"] for v in original["vehicles"]))

        created = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-keys-operator",
                "password": "synthetic-password",
                "role": "operator",
            },
        )
        self.assertEqual(created.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-keys-operator", "password": "synthetic-password"},
        ).json()["data"]["session"]["token"]
        viewer = self.browser.new_context(
            viewport={"width": 1440, "height": 960}, extra_http_headers=self.runtime.auth_headers
        )
        self.addCleanup(viewer.close)
        viewer.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ");"
        )
        viewer_page = viewer.new_page()
        viewer_page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        viewer_writes = []
        viewer_page.on(
            "request",
            lambda req: (
                viewer_writes.append(req.url.rsplit("/", 1)[-1]) if req.method == "POST" else None
            ),
        )
        self.load_board()
        viewer_page.goto(self.runtime.browser_url)
        viewer_page.wait_for_function(
            "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
        )
        viewer_page.evaluate("stopSnapshotPolling()")

        for page in (self.page, viewer_page):
            for mobile in (False, True):
                with self.subTest(restricted=page is viewer_page, mobile=mobile):
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
                        if mode[0] == "stored_duplicate":
                            data["client"]["vehicles"].append(
                                deepcopy(data["client"]["vehicles"][0])
                            )
                        elif mode[0] in (
                            "projection_stored_duplicate",
                            "projection_card_duplicate",
                        ):
                            index = 0 if mode[0] == "projection_stored_duplicate" else 1
                            duplicate = deepcopy(data["vehicles"][index])
                            duplicate["vehicle"] = "SYNTHETIC OTHER"
                            data["vehicles"].append(duplicate)
                            data["meta"]["vehicles_total"] += 1
                            data["client"]["stats"]["vehicles_total"] += 1
                        elif mode[0] == "same_raw_different_prefix":
                            data["vehicles"][1]["card_id"] = vehicle
                        route.fulfill(response=response, json=result)

                    page.route("**/api/get_client?**", respond)
                    for mode[0] in (
                        "stored_duplicate",
                        "projection_stored_duplicate",
                        "projection_card_duplicate",
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
                    for mode[0] in ("same_raw_different_prefix", ""):
                        page.evaluate("setStatus('', false)")
                        row.click()
                        page.wait_for_function(
                            "id => state.clientsActiveProfile?.client?.id === id", arg=client
                        )
                        text = page.locator(
                            "#mobileClientDetail" if mobile else "#clientVehiclesList"
                        ).inner_text()
                        self.assertIn("SYNTHETIC STORED", text)
                        self.assertIn("SYNTHETIC FROM CARD", text)
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
