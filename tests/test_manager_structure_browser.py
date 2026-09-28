"""Owner/viewer interactions against a disposable local CRM."""

from __future__ import annotations

import asyncio
import sys
import unittest
from unittest.mock import patch

if __package__:
    from tests.source_path_support import prepend_scripts_path
else:
    from source_path_support import prepend_scripts_path

prepend_scripts_path()

from browser_smoke_runtime import start_temp_runtime  # noqa: E402


class ManagerStructureBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        owner = patch.dict("os.environ", {"AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN": "ADMIN"})
        owner.start()
        cls.addClassCleanup(owner.stop)
        if sys.platform == "win32":
            prior = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            cls.addClassCleanup(asyncio.set_event_loop_policy, prior)
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        cls.addClassCleanup(cls.browser.close)
        cls.runtime = start_temp_runtime(start_port=43131)
        cls.addClassCleanup(cls.runtime.close)

    def setUp(self) -> None:
        self.context = self.browser.new_context(
            viewport={"width": 1440, "height": 900}, extra_http_headers=self.runtime.auth_headers
        )
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.admin = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        self.page.goto(self.runtime.base_url + "/manager-structure")
        self.page.evaluate(
            "token => localStorage.setItem('kanban-operator-session',token)", self.admin
        )
        self.page.reload()
        self.page.locator("#mode").get_by_text("Владелец", exact=False).wait_for()

    def read(self, token: str | None = None) -> dict:
        result = self.context.request.get(
            self.runtime.base_url + "/api/manager_structure",
            headers={"X-Operator-Session": token or self.admin},
        )
        self.assertEqual(result.status, 200)
        return result.json()["data"]

    def test_create_nested_text_relation_reload_and_version_conflict(self) -> None:
        self.page.get_by_role("button", name="Внешний модуль").click()
        self.page.locator('[name="title"]').fill("Руководитель")
        self.page.locator('[name="instruction"]').fill("Полная инструкция внешнего модуля")
        self.page.locator('[name="lines"]').fill("Первая подпись\nВторая подпись")
        self.page.locator("#save").click()
        self.page.locator('.node[data-id="M1"]').wait_for()
        self.assertEqual(self.page.locator('.node[data-id="M1"] text.sub').count(), 2)
        self.page.get_by_role("button", name="Вложенный модуль").click()
        self.page.locator('[name="title"]').fill("Приём заявки")
        self.page.locator('[name="instruction"]').fill("Полная инструкция вложенного модуля")
        self.page.locator('[name="height"]').fill("60")
        self.page.locator('[name="lines"]').fill("Шаг работы")
        self.page.locator("#save").click()
        self.page.locator('.node[data-id="M2"]').wait_for()
        self.assertEqual(self.page.locator('.node[data-id="M2"] text.sub').count(), 1)
        self.page.get_by_role("button", name="Связь", exact=True).click()
        self.page.locator('[name="label"]').fill("Заявка")
        self.page.locator('[name="direction"]').select_option("both")
        self.page.locator("#save").click()
        self.page.locator('.edge[data-id="R1"]').wait_for()
        snapshot = self.read()
        self.assertEqual(
            [node["instruction"] for node in snapshot["elements"]],
            ["Полная инструкция внешнего модуля", "Полная инструкция вложенного модуля"],
        )
        self.assertEqual(snapshot["relations"][0]["direction"], "both")
        self.page.reload()
        self.page.locator('.node[data-id="M2"]').click()
        self.assertEqual(
            self.page.locator('[name="instruction"]').input_value(),
            "Полная инструкция вложенного модуля",
        )
        self.page.locator('[name="instruction"]').fill("Несохранённое")
        self.page.locator("#cancel").click()
        self.page.locator('.node[data-id="M2"]').click()
        self.assertEqual(
            self.page.locator('[name="instruction"]').input_value(),
            "Полная инструкция вложенного модуля",
        )
        response = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "upsert_element",
                "element": {"id": "M2", "title": "Устаревшая правка"},
                "expected_version": snapshot["version"] - 1,
                "idempotency_key": "stale-version-001",
            },
        )
        self.assertEqual(response.status, 409)
        self.assertEqual(response.json()["error"]["code"], "manager_structure_version_conflict")
        self.assertEqual(self.errors, [])

    def test_viewer_can_read_but_cannot_write(self) -> None:
        created = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.admin},
            data={"username": "viewer", "password": "viewer-password-001", "role": "operator"},
        )
        self.assertEqual(created.status, 200)
        viewer = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "viewer", "password": "viewer-password-001"},
        ).json()["data"]["session"]["token"]
        self.page.evaluate("token => localStorage.setItem('kanban-operator-session',token)", viewer)
        self.page.reload()
        self.page.locator("#mode").get_by_text("Просмотр").wait_for()
        self.assertTrue(self.page.get_by_role("button", name="Внешний модуль").is_disabled())
        self.assertFalse(self.read(viewer)["can_edit"])
        response = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": viewer},
            data={
                "operation": "set_canvas",
                "canvas": {"width": 900, "height": 900},
                "expected_version": self.read()["version"],
                "idempotency_key": "viewer-write-001",
            },
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(self.errors, [])

    def test_indicator_and_clean_view(self) -> None:
        self.page.get_by_role("button", name="Внешний модуль").click()
        self.page.locator('[name="title"]').fill("Индикатор")
        self.page.locator('[name="indicator"]').select_option("green")
        self.page.locator("#save").click()
        created = next(item for item in self.read()["elements"] if item["title"] == "Индикатор")
        node = self.page.locator(f'.node[data-id="{created["id"]}"]')
        node.wait_for()
        self.assertEqual(created["indicator"], "green")
        self.assertEqual(node.locator(".indicator-lamp").get_attribute("fill"), "#69d5a3")
        self.page.reload()
        node.click()
        self.assertEqual(self.page.locator('[name="indicator"]').input_value(), "green")
        self.page.locator('[name="indicator"]').select_option("red")
        self.page.locator("#save").click()
        self.assertEqual(
            next(item for item in self.read()["elements"] if item["id"] == created["id"])[
                "indicator"
            ],
            "red",
        )
        self.page.locator("#pureToggle").click()
        self.assertFalse(self.page.locator(".toolbar").is_visible())
        self.assertFalse(self.page.locator(".panel").is_visible())
        self.page.keyboard.press("Escape")
        self.assertTrue(self.page.locator(".toolbar").is_visible())
        self.assertEqual(self.errors, [])

    def test_board_button_opens_manager_constructor(self) -> None:
        self.page.goto(self.runtime.base_url + "/")
        self.page.locator("#boardSettingsButton").click()
        button = self.page.locator("#openManagerStructureSettingsButton")
        self.assertEqual(button.inner_text(), "КОНСТРУКТОР СТРУКТУРЫ МЕНЕДЖЕРА")
        with self.page.expect_popup() as opened:
            button.click()
        self.assertTrue(opened.value.url.endswith("/manager-structure"))
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
