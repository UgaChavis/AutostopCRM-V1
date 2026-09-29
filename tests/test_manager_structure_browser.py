"""Owner/viewer interactions against a disposable local CRM."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
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
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            if os.environ.get("AUTOSTOP_REQUIRE_MANAGER_STRUCTURE_BROWSER") == "1":
                raise RuntimeError(
                    "Playwright is required for manager structure browser tests"
                ) from error
            raise unittest.SkipTest("Playwright is not installed") from error

        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        if not Path(cls.playwright.chromium.executable_path).exists():
            if os.environ.get("AUTOSTOP_REQUIRE_MANAGER_STRUCTURE_BROWSER") == "1":
                raise RuntimeError(
                    "Playwright Chromium is required for manager structure browser tests"
                )
            raise unittest.SkipTest("Playwright Chromium is not installed")
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
        self.page.locator("#modeToggle").click()
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
        self.page.locator("#save").click()
        self.page.locator('.node[data-id="M1"]').wait_for()
        self.page.get_by_role("button", name="Вложенный модуль").click()
        self.page.locator('[name="title"]').fill("Приём заявки")
        self.page.locator('[name="instruction"]').fill("Полная инструкция вложенного модуля")
        self.page.locator("#save").click()
        self.page.locator('.node[data-id="M2"]').wait_for()
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
        self.page.locator("#modeToggle").click()
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
        self.page.locator("#modeToggle").click()
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
        self.page.locator('[name="indicator_mode"]').select_option("manual")
        self.page.locator('[name="indicator_state"]').select_option("green")
        self.page.locator("#save").click()
        created = next(item for item in self.read()["elements"] if item["title"] == "Индикатор")
        node = self.page.locator(f'.node[data-id="{created["id"]}"]')
        node.wait_for()
        self.assertEqual(created["indicator_state"], "green")
        self.assertEqual(node.locator(".status-light").get_attribute("fill"), "#65d9a0")
        self.page.reload()
        self.page.locator("#modeToggle").click()
        node.click()
        self.assertEqual(self.page.locator('[name="indicator_state"]').input_value(), "green")
        self.page.locator('[name="indicator_state"]').select_option("red")
        self.page.locator("#save").click()
        self.assertEqual(
            next(item for item in self.read()["elements"] if item["id"] == created["id"])[
                "indicator_state"
            ],
            "red",
        )
        self.page.locator("#modeToggle").click()
        self.assertFalse(self.page.locator(".toolbar").is_visible())
        self.assertFalse(self.page.locator(".panel").is_visible())
        self.page.locator("#modeToggle").click()
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

    def test_instruction_file_and_auto_route_require_save(self) -> None:
        if len(self.read()["elements"]) < 2:
            self.page.get_by_role("button", name="Внешний модуль").click()
            self.page.locator('[name="title"]').fill("Первый")
            self.page.locator("#save").click()
            self.page.locator('.node[data-id="M1"]').wait_for()
            self.page.get_by_role("button", name="Внешний модуль").click()
            self.page.locator('[name="title"]').fill("Второй")
            self.page.locator('[name="x"]').fill("440")
            self.page.locator("#save").click()
            self.page.locator('.node[data-id="M2"]').wait_for()
        first, second = self.read()["elements"][:2]
        self.page.locator(f'.node[data-id="{first["id"]}"]').focus()
        self.page.keyboard.press("Enter")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instruction.md"
            path.write_text("# Файл\nИнструкция в UTF-8", encoding="utf-8")
            self.page.locator("#instructionFile").set_input_files(str(path))
            self.page.wait_for_function(
                "() => document.querySelector('[name=instruction]').value.startsWith('# Файл')"
            )
            self.assertEqual(
                self.page.locator('[name="instruction"]').input_value(),
                "# Файл\nИнструкция в UTF-8",
            )
            self.assertNotEqual(
                self.read()["elements"][0].get("instruction"),
                "# Файл\nИнструкция в UTF-8",
            )
            self.page.locator("#save").click()
            self.page.get_by_text("Сохранено", exact=True).wait_for()
        self.assertEqual(self.read()["elements"][0]["instruction"], "# Файл\nИнструкция в UTF-8")
        self.page.get_by_role("button", name="Связь", exact=True).click()
        self.assertEqual(self.page.locator('[name="from"]').input_value(), first["id"])
        self.page.locator('[name="to"]').select_option(second["id"])
        self.page.locator("#save").click()
        self.page.get_by_text("Сохранено", exact=True).wait_for()
        self.assertTrue(self.read()["relations"][-1]["path"].startswith("M"))
        before = self.page.locator("#stage").get_attribute("transform")
        self.page.locator("#jump").select_option(f"element:{second['id']}")
        self.assertEqual(self.page.locator("#panelTitle").inner_text(), f"Модуль {second['id']}")
        self.assertNotEqual(self.page.locator("#stage").get_attribute("transform"), before)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator("#jump").select_option(f"element:{first['id']}")
        selected_box = self.page.locator(f'.node.selected[data-id="{first["id"]}"]').bounding_box()
        panel_box = self.page.locator("#panel").bounding_box()
        self.assertEqual(self.page.evaluate("window.scrollY"), 0)
        self.assertLessEqual(selected_box["y"] + selected_box["height"], panel_box["y"])
        self.assertEqual(self.errors, [])

    def test_mouse_move_resize_clean_mode_and_agent_refresh(self) -> None:
        version = self.read()["version"]
        diagram = {
            "schema_version": "autostopcrm.manager-structure.v1",
            "canvas": {"width": 1000, "height": 600},
            "elements": [
                {
                    "id": "M1",
                    "title": "Менеджер",
                    "kind": "module",
                    "x": 80,
                    "y": 100,
                    "width": 220,
                    "height": 100,
                    "indicator_mode": "manual",
                    "indicator_state": "green",
                },
                {
                    "id": "M2",
                    "title": "Заявка",
                    "kind": "module",
                    "x": 600,
                    "y": 300,
                    "width": 220,
                    "height": 100,
                },
            ],
            "relations": [
                {
                    "id": "R1",
                    "from": "M1",
                    "to": "M2",
                    "kind": "exchange",
                    "direction": "forward",
                    "path": "M300 150 H450 V350 H600",
                    "label_x": 450,
                    "label_y": 250,
                },
            ],
        }
        replaced = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "replace",
                "diagram": diagram,
                "expected_version": version,
                "idempotency_key": "mouse-fixture-001",
            },
        )
        self.assertEqual(replaced.status, 200)
        self.page.reload()
        self.assertTrue(self.page.locator("body").evaluate("el => el.classList.contains('clean')"))
        self.page.locator("#modeToggle").click()
        self.page.locator('.node[data-id="M1"]').wait_for()
        code = self.page.locator('.node[data-id="M1"] .code-big')
        self.assertEqual(code.text_content(), "M1")
        self.assertEqual(self.page.locator('.node[data-id="M1"] .status-light').count(), 1)
        first = self.page.locator('.node[data-id="M1"]').bounding_box()
        self.page.mouse.move(first["x"] + 70, first["y"] + 45)
        self.page.mouse.down()
        self.page.mouse.move(first["x"] + 150, first["y"] + 45, steps=5)
        self.page.mouse.up()
        self.page.wait_for_timeout(600)
        moved = self.read()
        self.assertGreater(moved["elements"][0]["x"], 80)
        self.assertNotEqual(moved["relations"][0]["path"], diagram["relations"][0]["path"])
        handle = self.page.locator('.node[data-id="M1"] .resize-handle').bounding_box()
        self.page.mouse.move(handle["x"] + 5, handle["y"] + 5)
        self.page.mouse.down()
        self.page.mouse.move(handle["x"] + 45, handle["y"] + 25, steps=4)
        self.page.mouse.up()
        self.page.wait_for_timeout(600)
        self.assertGreater(self.read()["elements"][0]["width"], 220)
        current = self.read()
        changed = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "upsert_element",
                "element": {"id": "M2", "title": "Заявка агента"},
                "expected_version": current["version"],
                "idempotency_key": "agent-refresh-001",
            },
        )
        self.assertEqual(changed.status, 200)
        self.page.locator('.node[data-id="M2"] .title-main').get_by_text("Заявка агента").wait_for(
            timeout=10000
        )
        self.page.locator('.node[data-id="M2"]').click()
        self.page.locator('[name="title"]').fill("Мой несохранённый текст")
        changed_again = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "upsert_element",
                "element": {"id": "M1", "title": "Изменено агентом"},
                "expected_version": self.read()["version"],
                "idempotency_key": "agent-during-draft-001",
            },
        )
        self.assertEqual(changed_again.status, 200)
        self.page.locator('.node[data-id="M1"] .title-main').get_by_text(
            "Изменено агентом"
        ).wait_for(timeout=10000)
        self.assertEqual(
            self.page.locator('[name="title"]').input_value(), "Мой несохранённый текст"
        )
        self.page.locator("#save").click()
        self.page.locator("#reloadSchema").wait_for()
        self.assertEqual(
            self.page.locator('[name="title"]').input_value(), "Мой несохранённый текст"
        )
        self.page.locator("#reloadSchema").click()
        self.page.wait_for_function(
            "() => document.querySelector('[name=title]').value === 'Заявка агента'"
        )
        self.assertEqual(self.page.locator('[name="title"]').input_value(), "Заявка агента")
        self.page.locator("#modeToggle").click()
        self.assertTrue(self.page.locator("body").evaluate("el => el.classList.contains('clean')"))
        self.assertFalse(self.page.locator("#panel").is_visible())
        self.assertEqual(self.errors, [])

    def test_drag_connection_and_reconnect_endpoint(self) -> None:
        nodes = [
            {
                "id": ident,
                "title": ident,
                "kind": "module",
                "x": x,
                "y": y,
                "width": 180,
                "height": 90,
            }
            for ident, x, y in (("M1", 60, 100), ("M2", 480, 320), ("M3", 750, 100))
        ]
        response = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "replace",
                "diagram": {
                    "schema_version": "autostopcrm.manager-structure.v1",
                    "canvas": {"width": 1100, "height": 620},
                    "elements": nodes,
                    "relations": [],
                },
                "expected_version": self.read()["version"],
                "idempotency_key": "connect-fixture-001",
            },
        )
        self.assertEqual(response.status, 200)
        self.page.reload()
        self.page.locator("#modeToggle").click()
        self.page.wait_for_timeout(150)
        handle = self.page.locator('.node[data-id="M1"] .connect-handle').bounding_box()
        target = self.page.locator('.node[data-id="M2"] rect.body').bounding_box()
        self.page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
        self.page.mouse.down()
        self.page.mouse.move(
            target["x"] + target["width"] / 2, target["y"] + target["height"] / 2, steps=4
        )
        self.page.locator('.edge[data-id="R1"] path.wire[stroke-dasharray="6 5"]').wait_for(
            state="attached"
        )
        self.assertEqual(self.read()["relations"], [])
        self.page.mouse.up()
        self.page.locator('.edge[data-id="R1"]').wait_for()
        self.assertEqual(
            (self.read()["relations"][0]["from"], self.read()["relations"][0]["to"]), ("M1", "M2")
        )
        handle = self.page.locator('.reconnect-handle[data-edge-id="R1"]').bounding_box()
        target = self.page.locator('.node[data-id="M3"] rect.body').bounding_box()
        self.page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
        self.page.mouse.down()
        self.page.mouse.move(
            target["x"] + target["width"] / 2, target["y"] + target["height"] / 2, steps=4
        )
        self.page.locator('.edge[data-id="R1"] path.wire[stroke-dasharray="6 5"]').wait_for(
            state="attached"
        )
        self.assertEqual(self.read()["relations"][0]["to"], "M2")
        self.page.mouse.up()
        self.page.wait_for_function(
            "() => document.querySelector('.edge[data-id=R1]')?.getAttribute('aria-label')?.includes('M3')"
        )
        self.assertEqual(self.read()["relations"][0]["to"], "M3")
        self.assertEqual(self.errors, [])

    def test_z_reference_short_labels_and_crossing_bridges(self) -> None:
        reference = json.loads(
            (
                Path(__file__).resolve().parents[1] / "templates" / "manager_structure.json"
            ).read_text(encoding="utf-8")
        )
        response = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "replace",
                "diagram": reference,
                "expected_version": self.read()["version"],
                "idempotency_key": "crossing-reference-001",
            },
        )
        self.assertEqual(response.status, 200)
        self.page.reload()
        self.page.locator('.edge[data-id="L29"]').wait_for()
        self.assertGreater(self.page.locator(".edge path.wire[d*='Q']").count(), 0)
        self.assertEqual(self.page.locator(".edge path.hit[d*='Q']").count(), 0)
        self.assertTrue(
            self.page.locator(".edge text").evaluate_all(
                "nodes => nodes.every(node => /^L\\d+$/.test(node.textContent))"
            )
        )
        self.assertEqual(
            self.page.locator('.node[data-id="A2"] .code-big').evaluate(
                "node => getComputedStyle(node).fontSize"
            ),
            "17.6px",
        )
        self.assertEqual(
            self.page.locator(".node .code-small").first.evaluate(
                "node => getComputedStyle(node).fontSize"
            ),
            "13.6px",
        )
        saved = self.read()
        self.assertTrue(all("Q" not in edge["path"] for edge in saved["relations"]))
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
