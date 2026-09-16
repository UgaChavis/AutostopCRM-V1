"""Browser regression checks on an isolated CRM; no production data or requests."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from browser_smoke_runtime import start_temp_runtime


class ManagerMapBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform == "win32":
            previous_policy = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            cls.addClassCleanup(asyncio.set_event_loop_policy, previous_policy)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise unittest.SkipTest("Playwright is not installed") from error
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        if not Path(cls.playwright.chromium.executable_path).exists():
            raise unittest.SkipTest("Playwright Chromium is not installed")
        cls.browser = cls.playwright.chromium.launch(
            headless=True,
            executable_path=cls.playwright.chromium.executable_path,
            args=["--no-sandbox"],
        )
        cls.addClassCleanup(cls.browser.close)
        cls.runtime = start_temp_runtime(start_port=42971)
        cls.addClassCleanup(cls.runtime.close)

    def setUp(self) -> None:
        # The test transport bearer represents the local API/reverse-proxy layer.
        # It does not bypass the independently required operator session.
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            extra_http_headers=self.runtime.auth_headers,
        )
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.runtime.base_url + "/module-map")

    def login(self) -> None:
        response = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()
        self.assertTrue(response["ok"])
        self.page.evaluate(
            "token => localStorage.setItem('kanban-operator-session', token)",
            response["data"]["session"]["token"],
        )
        self.page.locator("#retry").click()
        self.page.wait_for_selector('[data-id="A2"]')

    def select_node(self, value: str) -> None:
        self.close_detail()
        self.page.locator(f'[data-id="{value}"]').focus()
        self.page.keyboard.press("Enter")

    def close_detail(self) -> None:
        if self.page.locator("#detail").is_visible():
            self.page.keyboard.press("Escape")
            self.page.wait_for_function("document.querySelector('#detail').hidden")

    def reveal_details(self) -> None:
        self.page.locator("#detailMore summary").click()

    def test_simplified_map_instructions_and_indicator_states(self) -> None:
        import copy

        from minimal_kanban.web_assets import MODULE_MAP_INFRASTRUCTURE

        self.login()
        self.assertEqual(
            self.page.locator('[data-id="N1"],[data-id="L6"] .edge-label,.legend').count(), 0
        )
        self.select_node("A1")
        self.assertEqual(self.page.locator("#detailTitle").inner_text(), "Инструкции")
        self.assertEqual(self.page.locator('[data-id="A1"] .subtitle').count(), 0)
        links = self.page.locator("#instructionLinks a")
        self.assertEqual(links.count(), 5)
        for link in links.all():
            self.assertTrue(
                link.get_attribute("href").startswith(
                    "https://github.com/UgaChavis/AutostopManager/blob/AutostopManager/"
                )
            )
            self.assertEqual(link.get_attribute("rel"), "noopener noreferrer")
        self.select_node("B4")
        self.assertIn("включён", self.page.locator("#detailStatus").inner_text())
        self.assertFalse(self.page.locator("#instructions").is_visible())
        colors = {}
        for state, label in (
            ("off", "Выключен"),
            ("on", "Включён"),
            ("unknown", "Состояние неизвестно"),
            ("invalid", "Состояние неизвестно"),
            (None, "Состояние неизвестно"),
        ):
            data = copy.deepcopy(MODULE_MAP_INFRASTRUCTURE)
            node = next(n for n in data["elements"] if n["id"] == "B4")
            if state is None:
                node.pop("indicator")
            else:
                node["indicator"] = state
            instructions = next(n for n in data["elements"] if n["id"] == "A1")
            instructions["links"].append({"title": "Unsafe", "url": "javascript:alert(1)"})
            self.page.route(
                "**/api/get_module_map_infrastructure",
                lambda route, _request, data=data: route.fulfill(json={"ok": True, "data": data}),
            )
            self.page.reload()
            lamp = self.page.locator('[data-id="B4"] .status-indicator')
            lamp.wait_for(state="attached")
            self.assertEqual(lamp.locator("title").text_content(), label)
            colors[state] = lamp.locator(".status-light").get_attribute("fill")
            self.select_node("A1")
            self.assertEqual(self.page.locator("#instructionLinks a").count(), 5)
            self.page.unroute("**/api/get_module_map_infrastructure")
        self.assertEqual(len({colors[state] for state in ("on", "off", "unknown")}), 3)
        self.assertEqual(colors["invalid"], colors["unknown"])
        self.assertEqual(colors[None], colors["unknown"])
        self.assertEqual(self.errors, [])

    def test_operator_access_all_elements_and_read_only_interactions(self) -> None:
        self.page.get_by_text(
            "Для просмотра карты войдите в CRM под учётной записью оператора."
        ).wait_for()
        self.assertEqual(self.page.locator("[data-id]").count(), 0)
        self.login()
        requests: list[tuple[str, str]] = []
        self.page.on("request", lambda request: requests.append((request.method, request.url)))
        identifiers = self.page.locator("[data-id]").evaluate_all(
            "elements => elements.map(el => el.dataset.id)"
        )
        self.assertEqual(len(identifiers), 54)
        self.assertEqual(len(set(identifiers)), 54)
        for code in identifiers:
            with self.subTest(code=code):
                self.close_detail()
                self.page.keyboard.press("Home")
                self.page.evaluate(
                    "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                )
                element = self.page.locator(f'[data-id="{code}"]')
                if element.locator("text").count():
                    element.locator("text").first.click()
                else:
                    point = element.locator(".wire-hit").evaluate("""path => {
                        const p=path.getPointAtLength(path.getTotalLength()/2);
                        const screen=p.matrixTransform(path.getScreenCTM());
                        return {x:screen.x,y:screen.y};
                    }""")
                    self.page.mouse.click(point["x"], point["y"])
                self.assertEqual(self.page.locator("#detailCode").inner_text(), code)
                self.assertTrue(self.page.locator("#detailDescription").inner_text())
        self.select_node("L19")
        self.reveal_details()
        self.assertIn("F1 ↔ F2", self.page.locator("#detailFacts").inner_text())
        self.page.locator("#related button").filter(has_text="F2 ·").click()
        self.assertEqual(self.page.locator("#detailCode").inner_text(), "F2")
        self.page.keyboard.press("Escape")
        self.assertFalse(self.page.locator("#detail").is_visible())
        self.page.keyboard.press("Home")
        self.page.locator('[data-id="E4"]').focus()
        self.page.keyboard.press("Space")
        self.assertEqual(self.page.locator("#detailCode").inner_text(), "E4")
        self.select_node("E7")
        self.reveal_details()
        related = self.page.locator("#related button").all_inner_texts()
        for code in ("L20 ·", "L21 ·", "L22 ·", "E8 ·"):
            self.assertTrue(any(item.startswith(code) for item in related), related)
        self.assertEqual(
            [request for request in requests if "/api/" in request[1]],
            [],
            "Map interactions must not call business APIs",
        )
        self.assertEqual(self.errors, [])

    def test_e1_modal_diagram_and_close_interactions(self) -> None:
        self.login()
        self.select_node("E1")
        dialog = self.page.locator("#detail")
        self.assertTrue(dialog.is_visible())
        self.assertEqual(dialog.get_attribute("role"), "dialog")
        self.assertEqual(dialog.get_attribute("aria-modal"), "true")
        diagram = self.page.locator("#detailDiagram")
        stages = diagram.locator("[data-step-id]").evaluate_all(
            "elements => elements.map(element => element.dataset.stepId)"
        )
        self.assertEqual(stages, ["E4", "E5", "E7", "E6", "E9"])
        self.assertTrue(self.page.locator(".detail-backdrop").is_visible())
        self.page.keyboard.press("Escape")
        self.assertFalse(dialog.is_visible())
        self.assertEqual(self.page.evaluate("document.activeElement?.dataset.id"), "E1")
        self.select_node("E1")
        self.page.locator(".detail-backdrop").click(position={"x": 5, "y": 5})
        self.assertFalse(dialog.is_visible())
        self.assertEqual(self.errors, [])

    def test_fit_text_bounds_pan_zoom_and_hash(self) -> None:
        self.login()
        self.assertEqual(self.page.locator("nav.toolbar, #search, #fullscreen").count(), 0)
        self.assertEqual(self.page.locator("#viewport").bounding_box()["y"], 0)
        for width, height in ((1366, 768), (1920, 1080), (3840, 2160)):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.keyboard.press("Home")
                self.page.wait_for_timeout(100)
                outside = self.page.evaluate("""() => {
                    const v=document.querySelector('#viewport').getBoundingClientRect();
                    return [...document.querySelectorAll('[data-id]')].filter(el => {
                        const r=el.getBoundingClientRect();
                        return r.left<v.left-1 || r.top<v.top-1 || r.right>v.right+1 || r.bottom>v.bottom+1;
                    }).map(el => el.dataset.id);
                }""")
                self.assertEqual(outside, [])
                overflow = self.page.evaluate("""() => {
                    const bad=[];
                    for(const group of document.querySelectorAll('.node,.edge')) {
                        const frame=group.querySelector('.card,.edge-label');
                        if(!frame) continue;
                        const card=frame.getBBox();
                        for(const text of group.querySelectorAll('text:not(.code)')) {
                            const r=text.getBBox();
                            if(r.x<card.x-1 || r.x+r.width>card.x+card.width+1 || r.y+r.height>card.y+card.height+1)
                                bad.push(group.dataset.id+': '+text.textContent);
                        }
                    }
                    return bad;
                }""")
                self.assertEqual(overflow, [])
        self.page.set_viewport_size({"width": 1920, "height": 1080})
        self.page.keyboard.press("Home")
        self.page.wait_for_timeout(100)
        before = self.page.locator("#stage").get_attribute("transform")
        self.page.mouse.move(30, 110)
        self.page.mouse.down()
        self.page.mouse.move(130, 140, steps=5)
        self.page.mouse.up()
        self.assertNotEqual(self.page.locator("#stage").get_attribute("transform"), before)
        zoom = self.page.locator("#map").get_attribute("data-zoom")
        self.page.keyboard.press("+")
        self.assertNotEqual(self.page.locator("#map").get_attribute("data-zoom"), zoom)
        zoom_before_wheel = float(self.page.locator("#map").get_attribute("data-zoom"))
        self.page.mouse.move(550, 350)
        anchor_script = """() => new DOMPoint(550,350).matrixTransform(
            document.querySelector('#stage').getScreenCTM().inverse()).toJSON()"""
        anchor_before = self.page.evaluate(anchor_script)
        self.page.mouse.wheel(0, -250)
        self.page.wait_for_function(
            "before => Number(document.querySelector('#map').dataset.zoom)>before",
            arg=zoom_before_wheel,
        )
        anchor_after = self.page.evaluate(anchor_script)
        self.assertAlmostEqual(anchor_before["x"], anchor_after["x"], places=3)
        self.assertAlmostEqual(anchor_before["y"], anchor_after["y"], places=3)
        self.page.keyboard.press("Home")
        self.page.wait_for_timeout(100)
        point = self.page.locator('[data-id="L19"] .wire-hit').evaluate("""path => {
            const p=path.getPointAtLength(path.getTotalLength()*.2);
            const screen=p.matrixTransform(path.getScreenCTM());
            return {x:screen.x,y:screen.y};
        }""")
        self.page.mouse.click(point["x"], point["y"])
        self.assertEqual(self.page.locator("#detailCode").inner_text(), "L19")
        self.page.evaluate("location.hash='L7'")
        self.page.wait_for_function("document.querySelector('#detailCode').textContent==='L7'")
        self.reveal_details()
        self.assertIn("B4 → A2", self.page.locator("#detailFacts").inner_text())
        self.page.evaluate("location.hash='unknown'")
        self.page.wait_for_function("document.querySelector('#detail').hidden")
        self.assertEqual(self.errors, [])

    def test_removed_hashes_open_current_map(self) -> None:
        self.page.goto(self.runtime.base_url + "/module-map#C1")
        self.login()
        self.assertEqual(self.page.locator("[data-id]").count(), 54)
        self.assertFalse(self.page.locator("#detail").is_visible())
        self.assertEqual(self.page.evaluate("location.hash"), "")
        for code in ("L8", "L9"):
            with self.subTest(code=code):
                self.page.evaluate("code => location.hash=code", code)
                self.page.wait_for_function(
                    "location.hash==='' && document.querySelector('#detail').hidden"
                )
                self.assertEqual(self.page.locator("[data-id]").count(), 54)
        self.assertEqual(self.errors, [])

    def test_failed_load_can_retry_without_exposing_partial_map(self) -> None:
        self.page.route(
            "**/api/get_module_map_infrastructure",
            lambda route: route.fulfill(status=503, body="unavailable"),
        )
        self.page.locator("#retry").click()
        self.page.get_by_text(
            "Не удалось загрузить карту. Проверьте соединение и повторите попытку."
        ).wait_for()
        self.assertEqual(self.page.locator("[data-id]").count(), 0)
        self.page.unroute("**/api/get_module_map_infrastructure")
        self.login()
        self.assertEqual(self.page.locator("[data-id]").count(), 54)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
