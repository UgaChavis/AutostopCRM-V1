"""Real sticky editor, receipt and failure checks on disposable CRM data."""

from __future__ import annotations

import json
import time

from tests.browser_ui_support import UiBrowserTestCase


class StickyActionsBrowserTests(UiBrowserTestCase):
    def open_editor(self, sticky: dict | None = None) -> None:
        self.load_board()
        if sticky:
            self.page.evaluate("sticky => openStickyModal(sticky)", sticky)
        else:
            self.page.locator("#stickyDockButton").click()
        self.page.locator("#stickyModal.is-open").wait_for()
        self.page.locator("#stickyText").fill("Synthetic sticky draft")

    def stored(self) -> list[dict]:
        return self.runtime.service.get_board_snapshot()["stickies"]

    def create_sticky(self, text: str = "Synthetic existing sticky") -> dict:
        return self.runtime.service.create_sticky(
            {"text": text, "x": 24, "y": 30, "deadline": {"hours": 4}, "source": "ui"}
        )["sticky"]

    def save(self) -> None:
        self.page.locator("#saveStickyButton").click()
        self.page.wait_for_function("!state.stickyMutationRequest")

    def assert_closed(self) -> None:
        self.assertFalse(
            self.page.locator("#stickyModal").evaluate("e=>e.classList.contains('is-open')"),
            self.page.locator("#statusLine").inner_text(),
        )

    def assert_open_draft(self, text: str = "Synthetic sticky draft") -> None:
        self.assertTrue(self.page.locator("#stickyModal.is-open").is_visible())
        self.assertEqual(self.page.locator("#stickyText").input_value(), text)
        self.assertTrue(self.page.locator("#stickyText").is_editable())
        self.assertTrue(self.page.locator("#saveStickyButton").is_enabled())

    def hold_route(self, pattern: str) -> list:
        held = []
        self.page.route(pattern, lambda route: held.append(route))
        self.addCleanup(lambda: [route.abort("failed") for route in held])
        return held

    def wait_held(self, held: list) -> None:
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)

    def lose_reply(self, route) -> None:
        response = route.fetch()
        self.assertEqual(response.status, 200)
        route.abort("failed")

    def test_create_one_click_closes_after_confirmed_write_and_keeps_native_values(self) -> None:
        self.open_editor()
        before = {item["id"] for item in self.stored()}
        self.page.locator("#stickyDays").fill("1")
        self.page.locator("#stickyHours").fill("2")
        self.save()
        self.assert_closed()
        created = [item for item in self.stored() if item["id"] not in before]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["text"], "Synthetic sticky draft")
        self.assertEqual(created[0]["deadline_total_seconds"], 26 * 3600)
        self.assertEqual(self.writes.count("create_sticky"), 1)
        self.assertTrue(
            self.page.locator(f'.sticky[data-sticky-id="{created[0]["id"]}"]').is_visible()
        )

    def test_cold_module_and_pending_post_hold_all_editor_actions(self) -> None:
        self.open_editor()
        module_path = self.page.evaluate("BOARD_MODULE_MANIFEST.auxiliary")
        scripts = self.hold_route("**" + module_path)
        posts = self.hold_route("**/api/create_sticky")
        self.page.locator("#saveStickyButton").click()
        self.wait_held(scripts)
        self.assertFalse(self.page.locator("#stickyText").is_editable())
        self.assertTrue(self.page.locator("#stickyHours").is_disabled())
        self.assertTrue(self.page.locator("#stickyDays").is_disabled())
        self.assertTrue(self.page.locator("#stickyModal [data-close]").first.is_disabled())
        self.assertFalse(self.page.evaluate("closeNamedModal('sticky')"))
        self.page.keyboard.press("Escape")
        self.assertEqual(self.writes.count("create_sticky"), 0)
        scripts.pop().continue_()
        self.wait_held(posts)
        self.page.evaluate("saveSticky()")
        self.assertEqual(self.writes.count("create_sticky"), 1)
        posts.pop().continue_()
        self.page.wait_for_function("!state.stickyMutationRequest")
        self.assert_closed()

    def test_definite_create_error_keeps_draft_then_retries_once(self) -> None:
        self.open_editor()
        before = len(self.stored())
        self.page.route(
            "**/api/create_sticky",
            lambda route: route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"message": "Synthetic validation failure"}}
                ),
            ),
        )
        self.save()
        self.assert_open_draft()
        self.assertEqual(len(self.stored()), before)
        self.page.unroute("**/api/create_sticky")
        self.save()
        self.assert_closed()
        self.assertEqual(len(self.stored()), before + 1)
        self.assertEqual(self.writes.count("create_sticky"), 2)

    def test_stalled_module_releases_controls_and_late_load_cannot_create(self) -> None:
        self.open_editor()
        self.page.evaluate(
            """() => {
              const original = window.setTimeout;
              window.setTimeout = (callback, delay, ...args) => original(callback, delay===15000 ? 80 : delay, ...args);
            }"""
        )
        scripts = self.hold_route("**" + self.page.evaluate("BOARD_MODULE_MANIFEST.auxiliary"))
        self.save()
        self.assert_open_draft()
        self.assertEqual(len(scripts), 1)
        scripts.pop().continue_()
        self.page.wait_for_timeout(150)
        self.assert_open_draft()
        self.assertEqual(self.writes.count("create_sticky"), 0)

    def test_missing_create_receipt_does_not_guess_an_id_or_repeat_write(self) -> None:
        self.open_editor()
        before = len(self.stored())

        def omit_id(route):
            response = route.fetch()
            body = response.json()
            body["data"].pop("sticky")
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

        self.page.route("**/api/create_sticky", omit_id)
        self.save()
        self.assert_open_draft()
        self.assertEqual(len(self.stored()), before + 1)
        self.assertFalse(self.page.evaluate("Boolean(state.stickyDraft?.id)"))
        self.page.unroute("**/api/create_sticky")
        self.save()
        self.assert_open_draft()
        self.assertEqual(self.writes.count("create_sticky"), 1)

    def test_lost_create_reply_and_new_draft_do_not_duplicate_unknown_creation(self) -> None:
        self.open_editor()
        before = len(self.stored())
        self.page.route("**/api/create_sticky", self.lose_reply)
        self.save()
        self.assert_open_draft()
        self.assertEqual(len(self.stored()), before + 1)
        self.page.unroute("**/api/create_sticky")
        self.save()
        self.assert_open_draft()
        self.page.locator("#stickyModal [data-close]").first.click()
        self.page.locator("#stickyDockButton").click()
        self.page.locator("#stickyText").fill("Different synthetic create intent")
        self.save()
        self.assert_open_draft("Different synthetic create intent")
        self.assertEqual(self.writes.count("create_sticky"), 1)
        self.assertEqual(len(self.stored()), before + 1)

    def test_confirmed_create_with_render_error_retries_read_and_preserves_known_id(self) -> None:
        self.open_editor()
        before = {item["id"] for item in self.stored()}
        self.page.evaluate(
            """() => {
              const original = applyStickySnapshot;
              window.stickyRenderFails = true;
              applyStickySnapshot = list => {
                if (window.stickyRenderFails) throw new Error('Synthetic render failure');
                return original(list);
              };
            }"""
        )
        self.save()
        self.assert_open_draft()
        known_id = self.page.evaluate("state.stickyDraft.id")
        self.assertNotIn(known_id, before)
        peer = self.create_sticky("Synthetic peer after confirmed write")
        self.page.evaluate("window.stickyRenderFails=false")
        self.save()
        self.assert_closed()
        self.assertEqual(self.writes.count("create_sticky"), 1)
        self.assertEqual(self.writes.count("update_sticky"), 0)
        self.assertTrue(self.page.locator(f'.sticky[data-sticky-id="{peer["id"]}"]').is_visible())

    def test_edited_draft_after_confirmed_create_updates_same_id(self) -> None:
        self.open_editor()
        self.page.evaluate(
            """() => {
              const original = applyStickySnapshot;
              window.stickyRenderFails = true;
              applyStickySnapshot = list => {
                if (window.stickyRenderFails) throw new Error('Synthetic render failure');
                return original(list);
              };
            }"""
        )
        self.save()
        self.assert_open_draft()
        known_id = self.page.evaluate("state.stickyDraft.id")
        self.page.locator("#stickyText").fill("Newer synthetic sticky draft")
        self.page.evaluate("window.stickyRenderFails=false")
        self.save()
        self.assert_closed()
        self.assertEqual(self.writes.count("create_sticky"), 1)
        self.assertEqual(self.writes.count("update_sticky"), 1)
        self.assertEqual(
            next(item for item in self.stored() if item["id"] == known_id)["text"],
            "Newer synthetic sticky draft",
        )

    def test_lost_update_reply_is_confirmed_by_known_id_and_exact_deadline(self) -> None:
        sticky = self.create_sticky()
        self.open_editor(sticky)
        self.page.locator("#stickyHours").fill("2")
        self.page.route("**/api/update_sticky", self.lose_reply)
        self.save()
        self.assert_closed()
        stored = next(item for item in self.stored() if item["id"] == sticky["id"])
        self.assertEqual(stored["text"], "Synthetic sticky draft")
        self.assertEqual(stored["deadline_total_seconds"], 7200)
        self.assertEqual(self.writes.count("update_sticky"), 1)

    def test_readback_does_not_round_a_different_native_deadline_into_confirmation(self) -> None:
        sticky = self.create_sticky()
        self.open_editor(sticky)
        self.page.locator("#stickyHours").fill("2")
        self.runtime.service.update_sticky(
            {
                "sticky_id": sticky["id"],
                "text": "Synthetic sticky draft",
                "deadline": {"total_seconds": 3700},
            }
        )
        self.page.route("**/api/update_sticky", lambda route: route.abort("failed"))
        self.save()
        self.assert_open_draft()
        self.page.unroute("**/api/update_sticky")
        self.save()
        self.assert_open_draft()
        self.assertEqual(self.writes.count("update_sticky"), 1)
        self.assertEqual(
            next(item for item in self.stored() if item["id"] == sticky["id"])[
                "deadline_total_seconds"
            ],
            3700,
        )

    def test_create_deadline_releases_controls_without_blind_repeat(self) -> None:
        self.open_editor()
        self.page.evaluate(
            """() => {
              const original = globalThis.setTimeout;
              globalThis.setTimeout = (callback, delay, ...args) => original(callback, delay===90000 ? 80 : delay, ...args);
            }"""
        )
        posts = self.hold_route("**/api/create_sticky")
        self.save()
        self.assert_open_draft()
        self.assertEqual(len(posts), 1)
        posts.pop().abort("failed")
        self.page.unroute("**/api/create_sticky")
        self.save()
        self.assert_open_draft()
        self.assertEqual(self.writes.count("create_sticky"), 1)

    def test_logout_releases_controls_and_discards_old_reply_and_private_checkpoint(self) -> None:
        self.open_editor()
        posts = self.hold_route("**/api/create_sticky")
        self.page.locator("#saveStickyButton").click()
        self.wait_held(posts)
        self.page.evaluate("clearOperatorSession({openLogin:false})")
        self.assertTrue(self.page.locator("#stickyText").is_editable())
        self.assertTrue(self.page.locator("#saveStickyButton").is_enabled())
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        self.page.evaluate(
            "token=>{state.operatorSessionToken=token;state.actor='admin';openStickyModal();}",
            token,
        )
        self.page.locator("#stickyText").fill("Replacement sticky draft")
        posts.pop().fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"ok": True, "data": {"sticky": {"id": "synthetic-old-sticky"}, "stickies": []}}
            ),
        )
        self.page.wait_for_timeout(100)
        self.assert_open_draft("Replacement sticky draft")
        self.page.unroute("**/api/create_sticky")
        self.save()
        self.assert_closed()
        self.assertEqual(self.writes.count("create_sticky"), 2)

    def test_lost_delete_reply_is_reconciled_without_another_delete(self) -> None:
        sticky = self.create_sticky()
        self.load_board()
        self.page.route("**/api/delete_sticky", self.lose_reply)
        self.page.locator(f'.sticky[data-sticky-id="{sticky["id"]}"] .sticky__close').click()
        self.page.wait_for_function("!state.stickyMutationRequest")
        self.assertFalse(any(item["id"] == sticky["id"] for item in self.stored()))
        self.assertEqual(self.writes.count("delete_sticky"), 1)
        self.assertEqual(self.page.locator(f'.sticky[data-sticky-id="{sticky["id"]}"]').count(), 0)

    def test_lost_move_reply_is_reconciled_with_native_coordinates(self) -> None:
        sticky = self.create_sticky()
        self.load_board()
        self.page.route("**/api/move_sticky", self.lose_reply)
        self.page.evaluate(
            """id => {
              state.stickyDrag={active:true,pointerId:7,stickyId:id,startX:24,startY:30,startClientX:0,startClientY:0,moved:true};
              return endStickyDrag({pointerId:7,clientX:120,clientY:60});
            }""",
            sticky["id"],
        )
        stored = next(item for item in self.stored() if item["id"] == sticky["id"])
        self.assertEqual((stored["x"], stored["y"]), (144, 90))
        self.assertEqual(self.writes.count("move_sticky"), 1)
        self.assertFalse(self.page.evaluate("Boolean(state.stickyMutationRequest)"))
