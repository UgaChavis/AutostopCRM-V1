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

    def automotive_fixture(self) -> dict:
        baseline = self.read()

        def restore_graph():
            self.context.request.post(
                self.runtime.base_url + "/api/manager_structure/apply",
                headers={"X-Operator-Session": self.admin},
                data={
                    "operation": "replace",
                    "diagram": {
                        field: baseline[field]
                        for field in ("schema_version", "canvas", "elements", "relations")
                    },
                    "expected_version": self.read()["version"],
                    "idempotency_key": "restore-fixture-" + os.urandom(8).hex(),
                },
            )

        self.addCleanup(restore_graph)
        catalog = self.context.request.get(
            self.runtime.base_url + "/api/manager_structure/tool_catalog",
            headers={"X-Operator-Session": self.admin},
        )
        self.assertEqual(catalog.status, 200)
        bundle = catalog.json()["data"]
        graph = {
            "schema_version": "autostopcrm.manager-structure.v1",
            "canvas": {"width": 900, "height": 600},
            "elements": [
                {
                    "id": "E1",
                    "title": "Автомобильные данные",
                    "kind": "module",
                    "x": 30,
                    "y": 30,
                    "width": 320,
                    "height": 220,
                },
                {
                    "id": "E2",
                    "title": "Автомобиль и модификация",
                    "kind": "item",
                    "parent": "E1",
                    "x": 50,
                    "y": 130,
                    "width": 270,
                    "height": 55,
                },
            ],
            "relations": [],
        }
        result = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "replace",
                "diagram": graph,
                "expected_version": self.read()["version"],
                "idempotency_key": "fixture-" + os.urandom(8).hex(),
            },
        )
        self.assertEqual(result.status, 200)
        self.page.reload()
        self.page.locator('.node[data-id="E2"]').wait_for()
        self.assertEqual(self.page.url, self.runtime.base_url + "/manager-structure")
        self.assertEqual(self.page.title(), "Конструктор структуры менеджера · AutoStop")
        return bundle

    def test_automotive_view_dialog_keyboard_owner_status_poll_conflict_and_mobile(self):
        bundle = self.automotive_fixture()
        target = self.page.locator('.node[data-id="E2"]')
        original_transform = self.page.locator("#stage").get_attribute("transform")
        target.focus()
        self.page.keyboard.press("Enter")
        dialog = self.page.locator("#toolDialog")
        dialog.wait_for(state="visible")
        self.page.locator(".tool-card").first.wait_for()
        module = next(item for item in bundle["modules"] if item["element_id"] == "E2")
        self.assertEqual(self.page.locator(".tool-card").count(), len(module["tool_ids"]))
        self.assertIn(
            module["instruction_text"].splitlines()[0],
            self.page.locator("#toolDialogInstruction").inner_text(),
        )
        card = self.page.locator(".tool-card").first
        ident = card.get_attribute("data-tool-id")
        select = card.locator("select")
        select.select_option("temporarily_unavailable")
        card.get_by_role("button", name="Сохранить отметку", exact=True).click()
        card.locator(".tool-status-error").get_by_text("Сохранено", exact=True).wait_for()
        self.assertEqual(self.read()["tool_statuses"][ident]["state"], "temporarily_unavailable")
        self.page.keyboard.press("Escape")
        self.assertFalse(dialog.is_visible())
        self.assertEqual(target.evaluate("el => document.activeElement === el"), True)
        self.assertIn("clean", self.page.locator("body").get_attribute("class"))
        self.assertEqual(self.page.locator("#stage").get_attribute("transform"), original_transform)
        self.page.reload()
        target.click()
        self.page.locator(".tool-card").first.wait_for()
        card = self.page.locator(".tool-card").first
        card.get_by_text("Временно не работает", exact=True).first.wait_for()
        select = card.locator("select")
        select.select_option("not_commissioned")
        self.page.evaluate("document.getElementById('toolDialogScroll').scrollTop=220")
        scroll = self.page.locator("#toolDialogScroll").evaluate("el => el.scrollTop")
        result = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "set_tool_status",
                "expected_version": self.read()["version"],
                "idempotency_key": "poll-" + os.urandom(8).hex(),
                "tool_status": {"operation_id": ident, "state": "working"},
            },
        )
        self.assertEqual(result.status, 200)
        card.get_by_text("Работает нормально", exact=True).first.wait_for(timeout=8000)
        self.assertEqual(select.input_value(), "not_commissioned")
        self.assertEqual(
            self.page.locator("#toolDialogScroll").evaluate("el => el.scrollTop"), scroll
        )
        self.assertTrue(dialog.is_visible())
        result = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "set_tool_status",
                "expected_version": self.read()["version"],
                "idempotency_key": "conflict-" + os.urandom(8).hex(),
                "tool_status": {"operation_id": ident, "state": "temporarily_unavailable"},
            },
        )
        self.assertEqual(result.status, 200)
        card.get_by_role("button", name="Сохранить отметку", exact=True).click()
        card.get_by_role("button", name="Перечитать состояние", exact=True).wait_for()
        self.assertEqual(select.input_value(), "not_commissioned")
        card.get_by_role("button", name="Перечитать состояние", exact=True).click()
        card.get_by_role("button", name="Сохранить отметку", exact=True).click()
        card.locator(".tool-status-error").get_by_text("Сохранено", exact=True).wait_for()
        self.assertEqual(self.read()["tool_statuses"][ident]["state"], "not_commissioned")
        screenshots = os.environ.get("AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR")
        if screenshots:
            output = Path(screenshots)
            output.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(output / "e1-constructor-desktop.png"))
        self.page.set_viewport_size({"width": 390, "height": 844})
        box = dialog.bounding_box()
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["x"] + box["width"], 390)
        self.assertFalse(
            self.page.locator("#toolDialogScroll").evaluate("el => el.scrollWidth > el.clientWidth")
        )
        if screenshots:
            self.page.screenshot(path=str(output / "e1-constructor-mobile.png"))
        self.page.keyboard.press("Escape")
        self.page.locator("#modeToggle").click()
        target.click()
        self.assertFalse(dialog.is_visible())
        self.assertTrue(self.page.locator("#editor").is_visible())
        self.assertEqual(self.errors, [])

    def test_automotive_all_module_cards_share_one_status(self):
        bundle = self.automotive_fixture()
        graph = {
            "schema_version": "autostopcrm.manager-structure.v1",
            "canvas": {"width": 900, "height": 1000},
            "elements": [
                {
                    "id": "E1",
                    "title": "Автомобильные данные",
                    "kind": "module",
                    "x": 20,
                    "y": 20,
                    "width": 390,
                    "height": 940,
                },
                *[
                    {
                        "id": module["element_id"],
                        "title": module["title"],
                        "kind": "item",
                        "parent": "E1",
                        "x": 35,
                        "y": 100 + index * 52,
                        "width": 355,
                        "height": 40,
                    }
                    for index, module in enumerate(bundle["modules"])
                    if module["element_id"] != "E1"
                ],
            ],
            "relations": [],
        }
        result = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "replace",
                "diagram": graph,
                "expected_version": self.read()["version"],
                "idempotency_key": "all-modules-" + os.urandom(8).hex(),
            },
        )
        self.assertEqual(result.status, 200)
        self.page.reload()
        ownership = {}
        for module in bundle["modules"]:
            node = self.page.locator(f'.node[data-id="{module["element_id"]}"]')
            node.focus()
            self.page.keyboard.press("Enter")
            self.page.locator("#toolDialogTitle").get_by_text(
                f"{module['element_id']} · {module['title']}", exact=True
            ).wait_for()
            self.page.wait_for_function(
                "count => document.querySelectorAll('.tool-card').length === count",
                arg=len(module["tool_ids"]),
            )
            actual = self.page.locator(".tool-card").evaluate_all(
                "cards => cards.map(card => card.dataset.toolId)"
            )
            self.assertEqual(actual, module["tool_ids"])
            for ident in actual:
                ownership.setdefault(ident, []).append(module["element_id"])
            self.page.keyboard.press("Escape")
        ident, modules = next(
            (ident, modules) for ident, modules in ownership.items() if len(modules) > 1
        )
        self.page.locator(f'.node[data-id="{modules[0]}"]').click()
        card = self.page.locator(f'.tool-card[data-tool-id="{ident}"]')
        card.locator("select").select_option("working")
        card.get_by_role("button", name="Сохранить отметку", exact=True).click()
        card.locator(".tool-status-error").get_by_text("Сохранено", exact=True).wait_for()
        self.page.keyboard.press("Escape")
        self.page.locator(f'.node[data-id="{modules[1]}"]').click()
        self.page.locator(f'.tool-card[data-tool-id="{ident}"] .tool-status-label').get_by_text(
            "Работает нормально", exact=True
        ).wait_for()
        self.assertEqual(sum(key == ident for key in self.read()["tool_statuses"]), 1)
        self.assertEqual(self.errors, [])

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
        self.automotive_fixture()
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
        self.page.locator("#modeToggle").click()
        self.page.locator('.node[data-id="E2"]').click()
        self.page.locator(".tool-card").first.wait_for()
        self.assertFalse(self.page.locator(".tool-card select").first.is_visible())
        self.assertFalse(
            self.page.get_by_role("button", name="Сохранить отметку").first.is_visible()
        )
        catalog = self.context.request.get(
            self.runtime.base_url + "/api/manager_structure/tool_catalog",
            headers={"X-Operator-Session": viewer},
        )
        self.assertEqual(catalog.status, 200)
        first = catalog.json()["data"]["tools"][0]["tool_id"]
        denied = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": viewer},
            data={
                "operation": "set_tool_status",
                "expected_version": self.read()["version"],
                "idempotency_key": "viewer-status-001",
                "tool_status": {"operation_id": first, "state": "working"},
            },
        )
        self.assertEqual(denied.status, 403)
        anonymous = self.context.request.get(
            self.runtime.base_url + "/api/manager_structure/tool_catalog"
        )
        self.assertEqual(anonymous.status, 401)
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

    def test_manual_curve_controls_canvas_and_api_screen_readback(self) -> None:
        diagram = {
            "schema_version": "autostopcrm.manager-structure.v1",
            "canvas": {"width": 3200, "height": 1800},
            "elements": [
                {
                    "id": "M1",
                    "title": "Источник",
                    "kind": "module",
                    "x": 100,
                    "y": 100,
                    "width": 140,
                    "height": 100,
                },
                {
                    "id": "M2",
                    "title": "Получатель",
                    "kind": "module",
                    "x": 520,
                    "y": 320,
                    "width": 140,
                    "height": 100,
                },
            ],
            "relations": [],
        }
        replaced = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "replace",
                "diagram": diagram,
                "expected_version": self.read()["version"],
                "idempotency_key": "manual-ui-replace-001",
            },
        )
        self.assertEqual(replaced.status, 200)
        self.page.reload()
        self.page.locator("#modeToggle").click()
        self.page.get_by_role("button", name="+ Поле").click()
        target_version = replaced.json()["data"]["version"] + 1
        self.page.wait_for_function(
            f"() => document.querySelector('#count')?.textContent.includes('v{target_version}')"
        )
        grown = self.read()
        self.assertEqual(grown["canvas"], {"width": 4200, "height": 2400})
        self.assertEqual(
            [(node["x"], node["y"]) for node in grown["elements"]], [(100, 100), (520, 320)]
        )

        self.page.get_by_role("button", name="Связь", exact=True).click()
        self.page.locator('[name="route_mode"]').select_option("manual")
        self.page.locator('[name="from_side"]').select_option("top")
        self.page.locator('[name="from_offset"]').fill("0.5")
        self.page.locator('[name="to_side"]').select_option("bottom")
        self.page.locator('[name="to_offset"]').fill("0.5")
        self.page.locator('[name="path"]').fill("M170 100 C260 80 430 350 590 420")
        self.page.locator('[name="label_mode"]').select_option("manual")
        self.page.locator('[name="label_x"]').fill("360")
        self.page.locator('[name="label_y"]').fill("210")
        self.page.locator('[name="direction"]').select_option("reverse")
        self.assertTrue(self.page.locator('[name="from_side"]').is_visible())
        self.page.locator("#save").click()
        self.page.locator('.edge[data-id="R1"] path.wire[d*="C"]').wait_for()
        saved = self.read()
        edge = saved["relations"][0]
        self.assertEqual(edge["route_mode"], "manual")
        self.assertEqual(edge["label_mode"], "manual")
        self.assertEqual(edge["direction"], "reverse")
        self.assertEqual(edge["path"], "M170 100 C260 80 430 350 590 420")
        self.assertEqual((edge["label_x"], edge["label_y"]), (360, 210))
        wire = self.page.locator('.edge[data-id="R1"] path.wire')
        self.assertEqual(wire.get_attribute("d"), edge["path"])
        self.assertIsNotNone(wire.get_attribute("marker-start"))
        self.assertIsNone(wire.get_attribute("marker-end"))

        self.page.locator('.node[data-id="M2"]').click()
        self.page.locator('[name="x"]').fill("600")
        self.page.locator("#save").click()
        target_version = saved["version"] + 1
        self.page.wait_for_function(
            f"() => document.querySelector('#count')?.textContent.includes('v{target_version}')"
        )
        moved = self.read()
        moved_edge = moved["relations"][0]
        self.assertEqual(moved_edge["path"], "M170 100 C260 80 510 350 670 420")
        self.assertEqual((moved_edge["label_x"], moved_edge["label_y"]), (360, 210))
        self.assertEqual(
            self.page.locator('.edge[data-id="R1"] path.wire').get_attribute("d"),
            moved_edge["path"],
        )
        self.page.get_by_role("button", name="Вместить схему").click()
        self.assertIn("scale(", self.page.locator("#stage").get_attribute("transform"))
        self.assertEqual(self.errors, [])

    def test_z_reference_short_labels_and_crossing_bridges(self) -> None:
        svg_errors = []
        self.page.on(
            "console",
            lambda message: (
                svg_errors.append(message.text)
                if message.type == "error" and "<path>" in message.text
                else None
            ),
        )
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
        self.assertGreater(self.page.locator(".bridge[d*='Q']").count(), 0)
        self.assertFalse(self.page.locator(".bridge[d*='NaN']").count())
        self.assertTrue(
            self.page.locator(".bridge").evaluate_all(
                "paths => paths.every(path => Number.isFinite(path.getTotalLength()) && path.getTotalLength() > 0)"
            )
        )
        self.assertEqual(svg_errors, [])
        self.assertEqual(self.page.locator(".edge path.hit[d*='Q']").count(), 0)
        reference_ids = {edge["id"] for edge in reference["relations"]}
        self.assertEqual(len(reference_ids), 37)
        self.assertCountEqual(
            self.page.locator(".edge").evaluate_all("nodes => nodes.map(node => node.dataset.id)"),
            reference_ids,
        )
        rendered_labels = self.page.locator(".edge text").evaluate_all(
            "nodes => nodes.map(node => ({id: node.closest('.edge').dataset.id, text: node.textContent}))"
        )
        self.assertCountEqual(
            [label["id"] for label in rendered_labels],
            [
                edge["id"]
                for edge in reference["relations"]
                if edge.get("show_label") is not False and not edge.get("auto_hidden_label")
            ],
        )
        self.assertTrue(all(label["text"] == label["id"] for label in rendered_labels))
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
        l29 = next(edge for edge in saved["relations"] if edge["id"] == "L29")
        self.assertEqual((l29["from"], l29["to"]), ("A2", "H1"))
        self.assertEqual(
            self.page.locator('.edge[data-id="L29"] path.hit').get_attribute("d"), l29["path"]
        )
        self.page.locator('.edge[data-id="L29"]').focus()
        self.page.locator("#relationTooltip").wait_for(state="visible")
        self.assertIn(l29["label"], self.page.locator("#relationTooltip").inner_text())
        self.assertTrue(
            self.page.locator("marker").evaluate_all(
                "nodes => nodes.every(node => node.getAttribute('markerUnits') === 'userSpaceOnUse' && node.getAttribute('markerWidth') === '14')"
            )
        )
        for edge in saved["relations"]:
            wire = self.page.locator(f'.edge[data-id="{edge["id"]}"] path.wire')
            self.assertEqual(
                bool(wire.get_attribute("marker-end")), edge["direction"] in {"forward", "both"}
            )
            self.assertEqual(
                bool(wire.get_attribute("marker-start")), edge["direction"] in {"reverse", "both"}
            )
        self.page.locator("#modeToggle").click()
        self.page.locator("#jump").select_option("element:A2")
        self.page.locator('[name="instruction"]').fill("Открытый черновик владельца")
        viewport = self.page.locator("#stage").get_attribute("transform")
        updated = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "upsert_element",
                "element": {"id": "J1", "title": "Внешняя агентская правка"},
                "expected_version": saved["version"],
                "idempotency_key": "external-dialogue-write-001",
            },
        )
        self.assertEqual(updated.status, 200)
        self.page.wait_for_function(
            "() => document.querySelector('.node[data-id=J1]')?.getAttribute('aria-label').includes('Внешняя агентская правка')",
            timeout=5700,
        )
        self.assertEqual(
            self.page.locator('[name="instruction"]').input_value(), "Открытый черновик владельца"
        )
        self.assertEqual(self.page.locator("#stage").get_attribute("transform"), viewport)
        self.assertTrue(self.page.locator("#reloadSchema").is_visible())
        self.page.locator("#cancel").click()
        self.page.locator("#modeToggle").click()
        self.page.locator("#fit").evaluate("node => node.click()")
        screenshot_dir = os.environ.get("AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR")
        if screenshot_dir:
            destination = Path(screenshot_dir) / "manager-structure-updated-local.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(destination))
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
