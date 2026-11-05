"""A file removal is confirmed only by its unique explicit tombstone."""

import base64
import json

from tests.attachment_samples import PNG_1X1_BYTES
from tests.browser_ui_support import UiBrowserTestCase


class CardFileRemovalEvidenceBrowserTests(UiBrowserTestCase):
    def test_removal_evidence_across_roles_and_views(self):
        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-file-delete",
                "password": "synthetic-file-password",
                "role": "operator",
            },
        )
        self.assertEqual(response.status, 200)
        operator = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-file-delete", "password": "synthetic-file-password"},
        ).json()["data"]["session"]["token"]
        scenarios = (
            ("lost", "active", "unknown"),
            ("lost", "empty", "unknown"),
            ("unknown4xx", "active", "unknown"),
            ("success", "active", "unknown"),
            ("success", "empty", "unknown"),
            ("success", "duplicate", "unknown"),
            ("lost", "fresh", "confirmed"),
            ("success", "fresh", "success"),
            ("rejected", "active", "rejected"),
        )
        for role, token in (("admin", self.session_token), ("operator", operator)):
            for mobile in (False, True):
                cards = []
                for _ in scenarios:
                    card = self.create_card("SYNTHETIC DELETE")
                    attachment = self.runtime.service.add_card_attachment(
                        {
                            "card_id": card,
                            "file_name": "synthetic.png",
                            "mime_type": "image/png",
                            "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
                            "source": "ui",
                        }
                    )["attachment"]["id"]
                    cards.append((card, attachment))
                context = self.browser.new_context(
                    viewport={"width": 390 if mobile else 1440, "height": 900},
                    extra_http_headers=self.runtime.auth_headers,
                )
                try:
                    context.add_init_script(
                        "localStorage.setItem('kanban-operator-session', "
                        + json.dumps(token)
                        + ");"
                    )
                    page = context.new_page()
                    page.goto(self.runtime.browser_url)
                    page.wait_for_function(
                        "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
                    )
                    page.evaluate("stopSnapshotPolling()")
                    page.evaluate(
                        """() => { const original = setStatus; window.syntheticStatuses = []; setStatus = (message, ...args) => { window.syntheticStatuses.push(message); return original(message, ...args); }; }"""
                    )
                    for (card, attachment), (outcome, read, expected) in zip(
                        cards, scenarios, strict=True
                    ):
                        stale = self.stored_card(card)
                        if mobile:
                            page.evaluate("id => openMobileCardDetail(id)", card)
                            page.wait_for_function(
                                "id => state.mobileCardId === id && !state.mobileCardLoading",
                                arg=card,
                            )
                            page.locator("#mobileCardTitleInput").fill("SYNTHETIC DRAFT")
                            page.click('[data-mobile-card-tab="files"]')
                            remove = page.locator(f'[data-mobile-card-remove-file="{attachment}"]')
                        else:
                            page.locator(
                                f'.card[data-card-id="{card}"]:not([data-virtual-card="true"])'
                            ).click()
                            page.wait_for_function("state.activeCardIsFull")
                            page.evaluate(
                                "clearCardOpenSideEffectTimer(); cancelDeferredCardSeen(state.editingId)"
                            )
                            page.locator("#cardTitle").fill("SYNTHETIC DRAFT")
                            page.click('#cardModal [data-tab="files"]')
                            remove = page.locator(f'#fileList [data-remove-file="{attachment}"]')
                        remove.wait_for()

                        def deletion(route):
                            if outcome == "rejected":
                                route.fulfill(
                                    status=400,
                                    json={
                                        "ok": False,
                                        "error": {
                                            "code": "validation_error",
                                            "message": "SYNTHETIC REJECTED",
                                        },
                                    },
                                )
                            elif outcome == "lost":
                                self.assertEqual(route.fetch().status, 200)
                                route.abort("failed")
                            elif outcome == "unknown4xx":
                                self.assertEqual(route.fetch().status, 200)
                                route.fulfill(
                                    status=403,
                                    json={
                                        "ok": False,
                                        "error": {
                                            "code": "synthetic_unknown",
                                            "message": "SYNTHETIC",
                                        },
                                    },
                                )
                            else:
                                route.continue_()

                        def readback(route):
                            if read in ("active", "empty"):
                                payload = {"ok": True, "data": {"card": dict(stale)}}
                                if read == "empty":
                                    payload["data"]["card"]["attachments"] = []
                            else:
                                payload = route.fetch().json()
                                if read == "duplicate":
                                    item = payload["data"]["card"]["attachments"][0]
                                    payload["data"]["card"]["attachments"] = [item, item]
                            route.fulfill(json=payload)

                        page.route("**/api/remove_card_attachment", deletion)
                        page.route("**/api/get_card?card_id=*", readback)
                        page.evaluate("window.syntheticStatuses = []")
                        remove.click()
                        page.wait_for_function(
                            "!state.mobileCardFilesBusy"
                            if mobile
                            else "!state.cardFilesMutationRequest"
                        )
                        status = "\n".join(page.evaluate("window.syntheticStatuses"))
                        if expected == "unknown":
                            self.assertIn("НЕ ОПРЕДЕЛЕН", status)
                        elif expected == "confirmed":
                            self.assertIn("ПРИ ПРОВЕРКЕ ФАЙЛ ОТМЕЧЕН УДАЛЁННЫМ", status)
                        elif expected == "success":
                            self.assertIn("ФАЙЛ УДАЛ", status)
                        else:
                            self.assertIn("SYNTHETIC REJECTED", status)
                        self.assertEqual(
                            page.locator(
                                "#mobileCardTitleInput" if mobile else "#cardTitle"
                            ).input_value(),
                            "SYNTHETIC DRAFT",
                        )
                        self.assertEqual(
                            self.stored_card(card)["attachments"][0]["removed"],
                            outcome != "rejected",
                        )
                        page.unroute("**/api/remove_card_attachment", deletion)
                        page.unroute("**/api/get_card?card_id=*", readback)
                        if mobile:
                            page.once("dialog", lambda dialog: dialog.accept())
                            page.evaluate("closeMobileCardDetail()")
                        else:
                            page.evaluate("closeCardModal()")
                finally:
                    context.close()
