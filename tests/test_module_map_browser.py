"""Browser regression checks on an isolated CRM; no production data or requests."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

if __package__:
    from tests.source_path_support import prepend_scripts_path
else:
    from source_path_support import prepend_scripts_path

prepend_scripts_path()

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
        self.assertEqual(links.count(), 6)
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
            self.assertEqual(self.page.locator("#instructionLinks a").count(), 6)
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
        self.assertEqual(len(identifiers), 64)
        self.assertEqual(len(set(identifiers)), 64)
        for code in identifiers:
            with self.subTest(code=code):
                if code == "G1":
                    continue
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

    def test_automation_center_control_requires_server_readback(self) -> None:
        self.login()
        state = {
            "generated_at": "2026-09-21T12:00:00Z",
            "can_manage": True,
            "controller": {"state": "healthy", "heartbeat_at": "2026-09-21T12:00:00Z"},
            "jobs": [
                {
                    "job_id": "crm-digest",
                    "name": "Сводка изменений CRM",
                    "template_id": "crm_digest_v1",
                    "desired_state": "off",
                    "actual_state": "disabled",
                    "revision": 7,
                    "schedule": {
                        "kind": "interval",
                        "every_minutes": 20,
                        "timezone": "Asia/Krasnoyarsk",
                        "active_window": "24/7",
                    },
                    "next_run_at": "2026-09-21T12:20:00Z",
                }
            ],
            "system_timers": [
                {
                    "id": "managed-health",
                    "name": "Проверка рабочих компьютеров",
                    "control_mode": "managed",
                    "mutable": True,
                    "desired_state": "on",
                    "actual_state": "healthy",
                    "revision": 3,
                    "schedule": {"kind": "interval", "every_minutes": 15},
                },
                {
                    "id": "backup",
                    "name": "Резервная копия CRM",
                    "control_mode": "read_only",
                    "locked": True,
                    "desired_state": "on",
                    "actual_state": "healthy",
                    "schedule": {"kind": "text", "label": "Ежедневно"},
                },
            ],
            "readiness": [{"id": "telegram", "label": "Telegram-доставка", "state": "healthy"}],
            "templates": [
                {
                    "id": "crm_digest_v1",
                    "name": "Сводка изменений CRM",
                    "description": "Краткая сводка новых событий CRM.",
                    "default_every_minutes": 20,
                    "min_every_minutes": 5,
                    "max_every_minutes": 1440,
                    "default_timezone": "Asia/Krasnoyarsk",
                    "default_active_window": "24/7",
                }
            ],
        }
        traffic: list[tuple[str, object]] = []

        def automation_route(route, request) -> None:
            if request.method == "POST":
                payload = request.post_data_json
                traffic.append(("POST", payload))
                self.assertIn("command_id", payload)
                self.assertNotIn("source", payload)
                if payload["operation"] == "set_enabled" and "timer_id" in payload:
                    self.assertEqual(payload["timer_id"], "managed-health")
                    self.assertEqual(payload["expected_revision"], 3)
                    self.assertFalse(payload["enabled"])
                    state["system_timers"][0].update(
                        desired_state="off", actual_state="disabled", revision=4
                    )
                elif payload["operation"] == "set_schedule" and "timer_id" in payload:
                    self.assertEqual(payload["timer_id"], "managed-health")
                    self.assertEqual(payload["expected_revision"], 4)
                    self.assertEqual(payload["schedule"]["every_minutes"], 10)
                    state["system_timers"][0]["schedule"]["every_minutes"] = 10
                    state["system_timers"][0]["revision"] = 5
                elif payload["operation"] == "set_enabled":
                    self.assertEqual(payload["job_id"], "crm-digest")
                    self.assertEqual(payload["expected_revision"], 7)
                    self.assertTrue(payload["enabled"])
                    state["jobs"][0].update(desired_state="on", actual_state="idle", revision=8)
                elif payload["operation"] == "set_schedule":
                    self.assertEqual(payload["job_id"], "crm-digest")
                    self.assertEqual(payload["expected_revision"], 8)
                    self.assertEqual(payload["schedule"]["every_minutes"], 30)
                    self.assertEqual(payload["schedule"]["timezone"], "Asia/Krasnoyarsk")
                    self.assertEqual(
                        payload["schedule"]["active_window"],
                        {"start": "09:00", "end": "18:30"},
                    )
                    state["jobs"][0]["schedule"] = payload["schedule"]
                    state["jobs"][0]["revision"] = 9
                elif payload["operation"] == "create_from_template":
                    self.assertEqual(payload["template_id"], "crm_digest_v1")
                    self.assertEqual(payload["name"], "Вечерняя сводка")
                    self.assertEqual(payload["schedule"]["every_minutes"], 45)
                    self.assertEqual(payload["schedule"]["timezone"], "Asia/Krasnoyarsk")
                    self.assertEqual(payload["schedule"]["active_window"], "24/7")
                    state["jobs"].append(
                        {
                            "job_id": "evening-digest",
                            "name": payload["name"],
                            "template_id": payload["template_id"],
                            "desired_state": "off",
                            "actual_state": "disabled",
                            "revision": 1,
                            "schedule": payload["schedule"],
                        }
                    )
                else:
                    self.fail(f"unexpected operation: {payload['operation']}")
                route.fulfill(json={"ok": True, "data": {"accepted": True}})
                return
            traffic.append(("GET", None))
            route.fulfill(json={"ok": True, "data": state})

        self.page.route("**/api/automation_center/**", automation_route)
        self.select_node("G1")
        drawer = self.page.locator("#automationDrawer")
        drawer.wait_for(state="visible")
        self.page.get_by_text("Состояние подтверждено").wait_for()
        toggle = self.page.locator('[data-job-id="crm-digest"] [role="switch"]')
        self.assertEqual(toggle.get_attribute("aria-checked"), "false")
        self.assertEqual(
            self.page.locator('[data-job-id="crm-digest"] .actual-lamp').first.get_attribute(
                "data-state"
            ),
            "off",
        )
        toggle.click()
        self.page.wait_for_function(
            "document.querySelector('[data-job-id=\"crm-digest\"] [role=\"switch\"]').getAttribute('aria-checked')==='true'"
        )
        self.assertEqual([method for method, _payload in traffic[:3]], ["GET", "POST", "GET"])
        self.assertEqual(
            self.page.locator('[data-job-id="crm-digest"] .actual-lamp').first.get_attribute(
                "data-state"
            ),
            "on",
        )
        self.assertEqual(
            self.page.locator('[data-id="G1"] .status-indicator title').text_content(),
            "Включён",
        )
        self.page.locator('[data-job-id="crm-digest"] .automation-parameters summary').click()
        self.page.locator('[data-job-id="crm-digest"]').get_by_text("Изменить расписание").click()
        schedule_form = self.page.locator('[data-job-id="crm-digest"] .schedule-form')
        schedule_input = schedule_form.locator('input[type="number"]')
        schedule_input.fill("30")
        schedule_form.locator("select").select_option("custom")
        schedule_form.locator('input[type="time"]').nth(0).fill("09:00")
        schedule_form.locator('input[type="time"]').nth(1).fill("18:30")
        schedule_form.get_by_text("Сохранить").click()
        self.page.locator('[data-job-id="crm-digest"]').get_by_text(
            "Каждые 30 мин.", exact=True
        ).wait_for()

        self.page.locator("#systemTimersGroup summary").first.click()
        timer = self.page.locator('[data-timer-id="managed-health"]')
        timer_toggle = timer.get_by_role("switch")
        self.assertEqual(timer_toggle.get_attribute("aria-checked"), "true")
        timer_toggle.click()
        self.page.wait_for_function(
            "document.querySelector('[data-timer-id=\"managed-health\"] [role=\"switch\"]').getAttribute('aria-checked')==='false'"
        )
        timer.locator(".automation-parameters summary").click()
        timer.get_by_text("Изменить период").click()
        timer.locator(".schedule-form input").fill("10")
        timer.locator(".schedule-form").get_by_text("Сохранить").click()
        timer.get_by_text("Каждые 10 мин.", exact=True).wait_for()
        self.assertEqual(self.page.locator('[data-timer-id="backup"] [role="switch"]').count(), 0)
        self.page.locator('[data-timer-id="backup"] .automation-parameters summary').click()
        self.assertIn("только чтение", self.page.locator('[data-timer-id="backup"]').inner_text())

        self.page.locator("#addAutomation").click()
        self.assertEqual(self.page.locator("#templateSelect option").count(), 1)
        self.page.locator("#templateName").fill("Вечерняя сводка")
        self.page.locator("#templateMinutes").fill("45")
        self.page.locator("#automationWizard").get_by_text("Создать выключенным").click()
        self.page.locator('.automation-card[data-job-id="evening-digest"]').wait_for()
        self.assertEqual(
            self.page.locator('[data-job-id="evening-digest"] [role="switch"]').get_attribute(
                "aria-checked"
            ),
            "false",
        )
        self.assertEqual(
            [method for method, _payload in traffic[:11]],
            [
                "GET",
                "POST",
                "GET",
                "POST",
                "GET",
                "POST",
                "GET",
                "POST",
                "GET",
                "POST",
                "GET",
            ],
        )
        self.page.keyboard.press("Escape")
        self.assertFalse(drawer.is_visible())
        self.assertEqual(self.page.evaluate("document.activeElement?.dataset.id"), "G1")
        self.page.keyboard.press("Enter")
        drawer.wait_for(state="visible")
        self.page.locator("#automationBackdrop").click(position={"x": 4, "y": 4})
        self.assertFalse(drawer.is_visible())
        self.assertEqual(self.errors, [])

    def test_automation_center_mobile_read_only_stale_and_offline_states(self) -> None:
        self.login()
        self.page.set_viewport_size({"width": 390, "height": 844})
        mode = {"offline": False, "state": "healthy", "can_manage": False}
        status = {
            "generated_at": "2026-09-21T12:00:00Z",
            "controller": {"state": "healthy"},
            "jobs": [
                {
                    "id": "crm-digest",
                    "name": "Сводка изменений CRM",
                    "desired_state": "on",
                    "actual_state": "idle",
                    "revision": 2,
                    "schedule": {"kind": "interval", "every_minutes": 20},
                }
            ],
            "system_timers": [],
            "readiness": [],
            "templates": [{"id": "crm_digest_v1", "name": "Сводка изменений CRM"}],
        }

        def automation_route(route, _request) -> None:
            if mode["offline"]:
                route.fulfill(status=503, json={"ok": False, "message": "unavailable"})
                return
            status["can_manage"] = mode["can_manage"]
            status["controller"]["state"] = mode["state"]
            route.fulfill(json={"ok": True, "data": status})

        self.page.route("**/api/automation_center/status", automation_route)
        self.select_node("G1")
        self.page.get_by_text("Режим просмотра").wait_for()
        drawer_box = self.page.locator("#automationDrawer").bounding_box()
        self.assertIsNotNone(drawer_box)
        self.assertAlmostEqual(drawer_box["x"], 0, delta=1)
        self.assertAlmostEqual(drawer_box["width"], 390, delta=1)
        self.assertAlmostEqual(drawer_box["height"], 844, delta=1)
        self.assertTrue(
            self.page.locator('[data-job-id="crm-digest"] [role="switch"]').is_disabled()
        )
        self.assertTrue(self.page.locator("#addAutomation").is_disabled())

        mode.update(state="stale", can_manage=True)
        self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        self.page.get_by_text("Данные устарели").wait_for()
        self.assertTrue(
            self.page.locator('[data-job-id="crm-digest"] [role="switch"]').is_disabled()
        )

        mode["state"] = "healthy"
        status["jobs"][0]["actual_state"] = "applying"
        self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        self.page.get_by_text("Изменение применяется").wait_for()
        self.assertTrue(
            self.page.locator('[data-job-id="crm-digest"] [role="switch"]').is_disabled()
        )

        status["jobs"][0]["actual_state"] = "error"
        self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        self.page.locator("#automationStateTitle").get_by_text("Есть ошибка").wait_for()
        self.assertTrue(
            self.page.locator('.automation-card[data-job-id="crm-digest"]').evaluate(
                "element => element.classList.contains('is-error')"
            )
        )

        mode["offline"] = True
        self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        self.page.get_by_text("Состояние не подтверждено").wait_for()
        self.assertEqual(
            self.page.locator('[data-id="G1"] .status-indicator title').text_content(),
            "Нет связи",
        )
        self.page.keyboard.press("Escape")
        self.assertFalse(self.page.locator("#automationDrawer").is_visible())
        self.assertEqual(self.errors, [])

    def test_automation_center_unknown_runtime_is_yellow_and_inactive_timer_is_off(self) -> None:
        self.login()
        status = {
            "generated_at": "2026-09-21T12:00:00Z",
            "can_manage": True,
            "controller": {"state": "healthy"},
            "jobs": [
                {
                    "id": "crm-digest",
                    "name": "Сводка изменений CRM",
                    "desired_state": "off",
                    "actual_state": "unknown",
                    "revision": 1,
                    "applied_revision": 1,
                    "schedule": {"kind": "interval", "every_minutes": 20},
                }
            ],
            "system_timers": [
                {
                    "id": "managed-health",
                    "name": "Проверка рабочих компьютеров",
                    "desired_state": "off",
                    "actual_state": "unknown",
                    "control_mode": "managed",
                    "revision": 2,
                    "schedule": {"kind": "interval", "every_minutes": 15},
                },
                {
                    "id": "managed-cleanup",
                    "name": "Очистка рабочих компьютеров",
                    "desired_state": "off",
                    "actual_state": "inactive",
                    "control_mode": "managed",
                    "revision": 1,
                    "schedule": {"kind": "interval", "every_minutes": 60},
                },
            ],
            "readiness": [
                {
                    "id": "crm_change_feed",
                    "label": "Лента CRM",
                    "state": "ready",
                }
            ],
            "templates": [],
        }

        self.page.route(
            "**/api/automation_center/status",
            lambda route, _request: route.fulfill(json={"ok": True, "data": status}),
        )
        self.select_node("G1")
        self.page.locator("#automationStateTitle").get_by_text("Изменение применяется").wait_for()
        self.assertEqual(
            self.page.locator('[data-id="G1"] .status-indicator title').text_content(),
            "Применение изменений",
        )
        self.assertEqual(
            self.page.locator('[data-timer-id="managed-health"] .actual-lamp').first.get_attribute(
                "data-state"
            ),
            "stale",
        )
        self.assertEqual(
            self.page.locator('[data-timer-id="managed-cleanup"] .actual-lamp').first.get_attribute(
                "data-state"
            ),
            "off",
        )

        self.assertEqual(self.errors, [])

    def test_e1_child_purposes_are_visible_below_their_diagram(self) -> None:
        from minimal_kanban.web_assets import MODULE_MAP_INFRASTRUCTURE

        self.login()
        purposes = {
            item["id"]: item["purpose"]
            for item in MODULE_MAP_INFRASTRUCTURE["elements"]
            if item.get("parent") == "E1"
        }
        self.assertEqual(set(purposes), {"E4", "E5", "E7", "E6", "E9"})
        for code, purpose in purposes.items():
            with self.subTest(code=code):
                self.select_node(code)
                purpose_element = self.page.locator("#detailPurpose")
                self.assertTrue(purpose_element.is_visible())
                self.assertEqual(purpose_element.inner_text(), purpose)
                diagram_box = self.page.locator("#detailDiagram").bounding_box()
                purpose_box = purpose_element.bounding_box()
                self.assertIsNotNone(diagram_box)
                self.assertIsNotNone(purpose_box)
                self.assertGreater(
                    purpose_box["y"],
                    diagram_box["y"] + diagram_box["height"],
                )
        self.assertEqual(self.errors, [])

    def test_j1_is_a_direct_independent_research_module(self) -> None:
        self.login()
        j1 = self.page.locator('[data-id="J1"]')
        self.assertEqual(j1.locator("title").first.text_content(), "J1 · Интернет-исследования")
        self.select_node("J1")
        self.assertEqual(self.page.locator("#detailTitle").inner_text(), "Интернет-исследования")
        self.assertEqual(
            self.page.locator("#detailDiagram .flow-step").all_inner_texts(),
            ["Профиль", "Поиск", "Корпус", "Отчёт A–D"],
        )
        self.reveal_details()
        related = self.page.locator("#related button").all_inner_texts()
        self.assertTrue(any(text.startswith("A2 ·") for text in related))
        self.assertTrue(any(text.startswith("L25 ·") for text in related))
        self.assertFalse(any(text.startswith("E1 ·") for text in related))
        self.assertEqual(self.errors, [])

    def test_instagram_uses_connected_plugin_without_event_wake(self) -> None:
        self.login()
        self.select_node("H2")
        self.assertEqual(self.page.locator("#detailTitle").inner_text(), "Instagram AutoStop")
        self.assertIn("@auto.repair.parts", self.page.locator("#detailDescription").inner_text())
        self.assertEqual(
            self.page.locator("#detailDiagram .flow-step").all_inner_texts(),
            ["Windsor.ai", "Instagram AutoStop"],
        )
        self.reveal_details()
        related = self.page.locator("#related button").all_inner_texts()
        self.assertTrue(any(text.startswith("H1 ·") for text in related))
        self.assertTrue(any(text.startswith("L30 ·") for text in related))
        self.assertFalse(any(text.startswith("B4 ·") for text in related))
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
                e1 = self.page.locator('[data-id="E1"]').bounding_box()
                e8 = self.page.locator('[data-id="E8"]').bounding_box()
                f1 = self.page.locator('[data-id="F1"]').bounding_box()
                l19_label = self.page.locator('[data-id="L19"] .edge-label').bounding_box()
                self.assertIsNotNone(e1)
                self.assertIsNotNone(e8)
                self.assertIsNotNone(f1)
                self.assertIsNotNone(l19_label)
                self.assertGreater(e8["x"] - (e1["x"] + e1["width"]), 10)
                self.assertGreater(e8["y"] - (f1["y"] + f1["height"]), 10)
                self.assertGreater(l19_label["y"] - (e8["y"] + e8["height"]), 10)
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
        self.assertEqual(self.page.locator("[data-id]").count(), 64)
        self.assertFalse(self.page.locator("#detail").is_visible())
        self.assertEqual(self.page.evaluate("location.hash"), "")
        for code in ("L8", "L9"):
            with self.subTest(code=code):
                self.page.evaluate("code => location.hash=code", code)
                self.page.wait_for_function(
                    "location.hash==='' && document.querySelector('#detail').hidden"
                )
                self.assertEqual(self.page.locator("[data-id]").count(), 64)
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
        self.assertEqual(self.page.locator("[data-id]").count(), 64)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
