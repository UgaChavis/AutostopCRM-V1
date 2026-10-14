"""Pointer lifecycle regressions against the real disposable constructor."""

from __future__ import annotations

import os
import unittest

from tests import test_manager_structure_browser as support


class ManagerCanvasNavigationBrowserTests(unittest.TestCase):
    setUpClass = classmethod(support.ManagerStructureBrowserTests.setUpClass.__func__)
    setUp = support.ManagerStructureBrowserTests.setUp
    read = support.ManagerStructureBrowserTests.read
    automotive_fixture = support.ManagerStructureBrowserTests.automotive_fixture

    def stage(self):
        return self.page.locator("#stage").get_attribute("transform")

    def background(self):
        box = self.page.locator("#diagram").bounding_box()
        return box["x"] + 15, box["y"] + 15

    def test_background_click_does_not_start_hover_pan(self):
        before = self.stage()
        x, y = self.background()
        self.page.mouse.click(x, y)
        self.page.mouse.move(x + 100, y + 60)
        self.assertEqual(self.stage(), before)
        self.assertFalse(
            self.page.locator("#diagram").evaluate("el => el.classList.contains('dragging')")
        )
        self.assertEqual(self.errors, [])

    def test_pan_release_stops_movement(self):
        before = self.stage()
        x, y = self.background()
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x + 100, y + 60, steps=3)
        moved = self.stage()
        self.assertNotEqual(moved, before)
        self.page.mouse.up()
        self.page.mouse.move(x + 150, y + 100)
        self.assertEqual(self.stage(), moved)
        self.assertFalse(
            self.page.locator("#diagram").evaluate("el => el.classList.contains('dragging')")
        )

    def test_cancel_and_capture_loss_stop_pan(self):
        for event in ("pointercancel", "lostpointercapture"):
            with self.subTest(event=event):
                self.page.evaluate(
                    "document.querySelector('#diagram').addEventListener('pointerdown', e => window.probePointerId = e.pointerId, {once:true})"
                )
                x, y = self.background()
                self.page.mouse.move(x, y)
                self.page.mouse.down()
                self.page.mouse.move(x + 70, y + 50)
                moved = self.stage()
                self.page.evaluate(
                    "name => document.querySelector('#diagram').dispatchEvent(new PointerEvent(name, {bubbles:true,pointerId:window.probePointerId,pointerType:'mouse'}))",
                    event,
                )
                self.page.mouse.move(x + 120, y + 90)
                self.assertEqual(self.stage(), moved)
                self.page.mouse.up()
                self.assertFalse(
                    self.page.locator("#diagram").evaluate(
                        "el => el.classList.contains('dragging')"
                    )
                )
        self.assertEqual(self.errors, [])

    def test_other_pointer_cancellation_does_not_end_pan(self):
        self.page.evaluate(
            "document.querySelector('#diagram').addEventListener('pointerdown', e => window.probePointerId = e.pointerId, {once:true})"
        )
        x, y = self.background()
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x + 50, y + 30)
        moved = self.stage()
        self.page.evaluate(
            "document.querySelector('#diagram').dispatchEvent(new PointerEvent('pointercancel',{bubbles:true,pointerId:window.probePointerId+10}))"
        )
        self.page.mouse.move(x + 90, y + 60)
        self.assertNotEqual(self.stage(), moved)
        self.page.mouse.up()

    def test_wheel_during_move_and_resize_preserves_geometry(self):
        self.automotive_fixture()
        self.page.locator("#modeToggle").click()
        self.page.locator("#mode").get_by_text("Владелец", exact=False).wait_for()
        for target in ('.node[data-id="E1"] .body', '.node[data-id="E1"] .resize-handle'):
            with self.subTest(target=target):
                box = self.page.locator(target).bounding_box()
                x, y = box["x"] + 8, box["y"] + 8
                self.page.mouse.move(x, y)
                self.page.mouse.down()
                self.page.mouse.move(x + 30, y + 20)
                before = self.page.locator('.node[data-id="E1"]').get_attribute("transform")
                dimensions = self.page.locator('.node[data-id="E1"] .body').evaluate(
                    "el => [el.getAttribute('width'),el.getAttribute('height')]"
                )
                transform = self.stage()
                self.page.mouse.wheel(0, -500)
                self.page.mouse.move(x + 30, y + 20)
                self.assertEqual(self.stage(), transform)
                self.assertEqual(
                    self.page.locator('.node[data-id="E1"]').get_attribute("transform"), before
                )
                self.assertEqual(
                    self.page.locator('.node[data-id="E1"] .body').evaluate(
                        "el => [el.getAttribute('width'),el.getAttribute('height')]"
                    ),
                    dimensions,
                )
                self.page.locator("#diagram").dispatch_event("pointercancel", {"pointerId": 1})
                self.page.mouse.up()
        self.assertEqual(self.errors, [])

    def test_clean_view_zoom_controls_and_keyboard_do_not_write_graph(self):
        self.automotive_fixture()
        before = self.read()
        fitted = self.stage()
        self.assertTrue(self.page.locator("#mapNavigation").is_visible())
        self.page.get_by_role("button", name="Увеличить масштаб", exact=True).click()
        larger = self.stage()
        self.assertNotEqual(larger, fitted)
        self.assertIn("%", self.page.locator("#mapScale").inner_text())
        self.page.get_by_role("button", name="Уменьшить масштаб", exact=True).click()
        self.assertEqual(self.stage(), fitted)
        self.page.locator("#diagram").focus()
        self.page.keyboard.press("+")
        self.assertNotEqual(self.stage(), fitted)
        self.page.keyboard.press("0")
        self.assertEqual(self.stage(), fitted)
        self.page.get_by_role("button", name="Вместить", exact=True).click()
        self.assertEqual(self.read(), before)
        self.assertEqual(self.errors, [])

    def test_mobile_zoom_targets_are_visible_and_at_least_44_pixels(self):
        self.automotive_fixture()
        self.page.set_viewport_size({"width": 390, "height": 844})
        navigation = self.page.locator("#mapNavigation").bounding_box()
        self.assertGreaterEqual(navigation["x"], 0)
        self.assertLessEqual(navigation["x"] + navigation["width"], 390)
        self.assertLessEqual(navigation["y"] + navigation["height"], 844)
        for name in ("mapZoomOut", "mapZoomIn", "mapFit", "modeToggle"):
            box = self.page.locator("#" + name).bounding_box()
            self.assertGreaterEqual(box["height"], 44)
            self.assertGreaterEqual(box["width"], 44)
        fitted = self.stage()
        self.page.get_by_role("button", name="Увеличить масштаб", exact=True).click()
        self.assertNotEqual(self.stage(), fitted)
        self.page.get_by_role("button", name="Вместить", exact=True).click()
        self.assertEqual(self.stage(), fitted)
        self.assertEqual(self.errors, [])

    def test_zoom_keys_do_not_intercept_editor_inputs(self):
        self.automotive_fixture()
        self.page.locator("#modeToggle").click()
        self.page.locator("#jump").select_option("element:E1")
        fitted = self.stage()
        self.page.locator('[name="title"]').focus()
        self.page.keyboard.press("+")
        self.page.keyboard.press("-")
        self.page.keyboard.press("0")
        self.assertEqual(self.stage(), fitted)
        self.assertEqual(self.errors, [])

    def test_initial_read_failure_is_visible_in_clean_view_and_recoverable(self):
        attempts = []

        def fail_once(route):
            attempts.append(route.request.method)
            if len(attempts) == 1:
                route.fulfill(
                    status=503, json={"ok": False, "error": {"message": "Temporary graph failure"}}
                )
            else:
                route.continue_()

        self.page.route(self.runtime.base_url + "/api/manager_structure", fail_once)
        self.page.reload()
        self.page.locator("#canvasLoadError").wait_for()
        self.assertTrue(self.page.locator("body").evaluate("el => el.classList.contains('clean')"))
        self.assertIn(
            "Temporary graph failure", self.page.locator("#canvasLoadMessage").inner_text()
        )
        self.page.get_by_role("button", name="Повторить загрузку схемы", exact=True).click()
        self.page.locator("#canvasLoadError").wait_for(state="hidden")
        self.page.locator("#stage").wait_for(state="attached")
        self.assertTrue(self.page.locator("#empty").is_visible())
        self.assertFalse(self.page.locator("#mapFit").is_disabled())
        self.assertEqual(attempts, ["GET", "GET"])
        self.assertEqual(self.errors, [])

    def test_initial_expired_session_offers_login_without_write_or_hidden_error(self):
        attempts = []

        def unauthorized(route):
            attempts.append(route.request.method)
            route.fulfill(status=401, json={"ok": False, "error": {"message": "Expired session"}})

        self.page.route(self.runtime.base_url + "/api/manager_structure", unauthorized)
        self.page.reload()
        self.page.locator("#canvasLoadError").wait_for()
        self.assertIn("Войдите в CRM", self.page.locator("#canvasLoadMessage").inner_text())
        self.assertTrue(self.page.get_by_role("link", name="Открыть CRM", exact=True).is_visible())
        self.assertFalse(self.page.locator("#canvasLoadRetry").is_visible())
        self.assertTrue(self.page.locator("#mapZoomIn").is_disabled())
        self.assertEqual(attempts, ["GET"])
        self.assertEqual(self.errors, [])

    def canvas_scale(self):
        return self.page.locator("#stage").evaluate(
            "el => el.transform.baseVal.consolidate().matrix.a"
        )

    def test_fitted_scale_below_minimum_does_not_increase_for_zoom_out_or_zero_wheel(self):
        baseline = self.read()

        def restore_graph():
            restored = self.context.request.post(
                self.runtime.base_url + "/api/manager_structure/apply",
                headers={"X-Operator-Session": self.admin},
                data={
                    "operation": "replace",
                    "diagram": {
                        field: baseline[field]
                        for field in ("schema_version", "canvas", "elements", "relations")
                    },
                    "expected_version": self.read()["version"],
                    "idempotency_key": "restore-zoom-floor-" + os.urandom(8).hex(),
                },
            )
            self.assertTrue(restored.ok, restored.text())

        self.addCleanup(restore_graph)
        response = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "replace",
                "diagram": {
                    "schema_version": "autostopcrm.manager-structure.v1",
                    "canvas": {"width": 3200, "height": 600},
                    "elements": [
                        {
                            "id": "M1",
                            "title": "Left",
                            "kind": "module",
                            "x": 40,
                            "y": 80,
                            "width": 200,
                            "height": 100,
                        },
                        {
                            "id": "M2",
                            "title": "Right",
                            "kind": "module",
                            "x": 2800,
                            "y": 80,
                            "width": 200,
                            "height": 100,
                        },
                    ],
                    "relations": [],
                },
                "expected_version": self.read()["version"],
                "idempotency_key": "zoom-floor-" + os.urandom(8).hex(),
            },
        )
        self.assertTrue(response.ok, response.text())
        self.page.set_viewport_size({"width": 320, "height": 568})
        self.page.reload()
        self.page.locator('.node[data-id="M2"]').wait_for()
        self.page.locator("#mapFit").click()
        fitted = self.canvas_scale()
        self.assertLess(fitted, 0.12)
        before = self.read()
        observations = []
        for action in ("minus_key", "wheel_out", "zero_y_horizontal_wheel"):
            self.page.locator("#mapFit").click()
            initial = self.canvas_scale()
            if action == "minus_key":
                self.page.locator("#diagram").focus()
                self.page.keyboard.press("-")
            else:
                box = self.page.locator("#diagram").bounding_box()
                self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 100)
                self.page.mouse.wheel(
                    300 if action == "zero_y_horizontal_wheel" else 0,
                    0 if action == "zero_y_horizontal_wheel" else 300,
                )
                self.page.wait_for_timeout(100)
            observations.append(
                {
                    "action": action,
                    "fitted": initial,
                    "after": self.canvas_scale(),
                    "zoom_out_disabled": self.page.locator("#mapZoomOut").is_disabled(),
                }
            )
        self.page.locator("#mapFit").click()
        before_in = self.canvas_scale()
        self.page.locator("#mapZoomIn").click()
        after_in = self.canvas_scale()
        graph_unchanged = self.read() == before
        self.assertTrue(graph_unchanged)
        self.assertGreater(after_in, before_in)
        self.assertEqual(self.errors, [])
        for item in observations:
            with self.subTest(action=item["action"]):
                self.assertAlmostEqual(item["after"], item["fitted"], places=7)


if __name__ == "__main__":
    unittest.main()
