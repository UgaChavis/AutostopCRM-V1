"""Editor durability and ownership against a disposable synthetic CRM."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.source_path_support import prepend_scripts_path
else:
    from source_path_support import prepend_scripts_path

prepend_scripts_path()

from browser_smoke_runtime import start_temp_runtime  # noqa: E402


class ManagerEditorRecoveryBrowserTests(unittest.TestCase):
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
                raise RuntimeError("Playwright is required for editor recovery checks") from error
            raise unittest.SkipTest("Playwright is not installed") from error
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        if not Path(cls.playwright.chromium.executable_path).exists():
            if os.environ.get("AUTOSTOP_REQUIRE_MANAGER_STRUCTURE_BROWSER") == "1":
                raise RuntimeError("Playwright Chromium is required for editor recovery checks")
            raise unittest.SkipTest("Playwright Chromium is not installed")
        cls.browser = cls.playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        cls.addClassCleanup(cls.browser.close)
        cls.runtime = start_temp_runtime(start_port=43201)
        cls.addClassCleanup(cls.runtime.close)

    def setUp(self) -> None:
        self.context = self.browser.new_context(
            viewport={"width": 1440, "height": 900},
            extra_http_headers=self.runtime.auth_headers,
        )
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(self.errors, []))
        self.admin = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        self.fixture()

    def read(self, token: str | None = None) -> dict:
        response = self.context.request.get(
            self.runtime.base_url + "/api/manager_structure",
            headers={"X-Operator-Session": token or self.admin},
        )
        self.assertTrue(response.ok, response.text())
        return response.json()["data"]

    def write(self, **change) -> dict:
        response = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                **change,
                "expected_version": self.read()["version"],
                "idempotency_key": "recovery-fixture-" + os.urandom(8).hex(),
            },
        )
        self.assertTrue(response.ok, response.text())
        return response.json()["data"]

    def fixture(self) -> None:
        graph = {
            "schema_version": "autostopcrm.manager-structure.v1",
            "canvas": {"width": 1000, "height": 800},
            "elements": [
                {
                    "id": "T1",
                    "kind": "module",
                    "title": "Original one",
                    "instruction": "Original instruction",
                    "x": 60,
                    "y": 60,
                    "width": 220,
                    "height": 100,
                },
                {
                    "id": "T2",
                    "kind": "module",
                    "title": "Original two",
                    "x": 400,
                    "y": 60,
                    "width": 220,
                    "height": 100,
                },
            ],
            "relations": [],
        }
        self.write(operation="replace", diagram=graph)
        self.page.goto(self.runtime.base_url + "/manager-structure")
        self.page.evaluate(
            "token => localStorage.setItem('kanban-operator-session',token)", self.admin
        )
        self.page.reload()
        self.page.locator("#modeToggle").click()
        self.page.locator("#mode").get_by_text("Владелец", exact=False).wait_for()
        self.page.locator('.node[data-id="T1"]').click()

    def wait_message(self, value: str) -> None:
        self.page.wait_for_function(
            "text => document.getElementById('message').textContent.includes(text)",
            arg=value,
        )

    def viewer_token(self) -> str:
        username = "viewer-" + os.urandom(4).hex()
        created = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.admin},
            data={"username": username, "password": "viewer-password-001", "role": "operator"},
        )
        self.assertTrue(created.ok, created.text())
        return self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": username, "password": "viewer-password-001"},
        ).json()["data"]["session"]["token"]

    def test_acknowledged_write_failed_readback_only_retries_get(self) -> None:
        before = self.read()["version"]
        posts: list[dict] = []
        block = {"read": True}

        def observe_post(route):
            posts.append(route.request.post_data_json)
            route.continue_()

        def block_read(route):
            if block["read"]:
                route.abort("failed")
            else:
                route.continue_()

        self.page.route("**/api/manager_structure/apply", observe_post)
        self.page.route("**/api/manager_structure", block_read)
        self.page.locator('[name="title"]').fill("Acknowledged title")
        self.page.locator('[name="instruction"]').fill("Retained instruction")
        self.page.locator("#save").click()
        self.wait_message("не подтверждён")
        self.assertEqual(self.page.locator('[name="title"]').input_value(), "Acknowledged title")
        self.assertEqual(
            self.page.locator('[name="instruction"]').input_value(), "Retained instruction"
        )
        self.assertTrue(self.page.locator('[name="instruction"]').is_disabled())
        self.assertEqual(self.read()["version"], before + 1)
        self.assertEqual(self.page.locator("#save").inner_text(), "Проверить запись")
        block["read"] = False
        self.page.locator("#save").click()
        self.wait_message("Сохранено")
        self.assertEqual(len(posts), 1)
        self.assertEqual(self.read()["version"], before + 1)
        self.assertTrue(self.page.locator('[name="instruction"]').is_enabled())

    def test_lost_or_malformed_receipt_replays_exact_original_request(self) -> None:
        for failure in ("lost", "malformed"):
            with self.subTest(failure=failure):
                if failure == "malformed":
                    self.fixture()
                before = self.read()["version"]
                posts: list[dict] = []

                def lose_first_receipt(route):
                    posts.append(route.request.post_data_json)
                    response = route.fetch()
                    if len(posts) == 1:
                        if failure == "lost":
                            route.abort("failed")
                        else:
                            route.fulfill(
                                status=200,
                                content_type="application/json",
                                body=json.dumps(
                                    {
                                        "ok": True,
                                        "data": {"version": response.json()["data"]["version"]},
                                    }
                                ),
                            )
                    else:
                        route.fulfill(response=response)

                self.page.route("**/api/manager_structure/apply", lose_first_receipt)
                self.page.locator('[name="title"]').fill("Saved once")
                self.page.locator("#save").click()
                self.wait_message("не подтверждён")
                self.assertEqual(self.read()["version"], before + 1)
                self.page.locator("#save").click()
                self.wait_message("Сохранено")
                self.assertEqual(len(posts), 2)
                self.assertEqual(posts[0], posts[1])
                self.assertEqual(self.read()["version"], before + 1)
                self.assertEqual(self.read()["elements"][0]["title"], "Saved once")
                self.page.unroute("**/api/manager_structure/apply")

    def test_incomplete_readback_retains_cached_graph_and_draft(self) -> None:
        before = self.read()["version"]
        self.page.route(
            "**/api/manager_structure",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "data": {"version": before + 1}}),
            ),
        )
        self.page.locator('[name="title"]').fill("Saved with incomplete readback")
        self.page.locator("#save").click()
        self.wait_message("не подтверждён")
        self.assertEqual(self.page.locator('.node[data-id="T1"]').count(), 1)
        self.assertEqual(
            self.page.locator('[name="title"]').input_value(), "Saved with incomplete readback"
        )
        self.assertEqual(self.read()["version"], before + 1)
        self.page.unroute("**/api/manager_structure")
        self.page.locator("#save").click()
        self.wait_message("Сохранено")
        self.assertEqual(self.read()["version"], before + 1)

    def test_partial_relation_receipt_replays_exact_body(self) -> None:
        before = self.read()["version"]
        posts: list[dict] = []

        def strip_relation_field(route):
            posts.append(route.request.post_data_json)
            response = route.fetch()
            if len(posts) == 1:
                result = response.json()
                del result["data"]["accepted_relation"]["label"]
                route.fulfill(status=200, content_type="application/json", body=json.dumps(result))
            else:
                route.fulfill(response=response)

        self.page.route("**/api/manager_structure/apply", strip_relation_field)
        self.page.locator('[data-create="relation"]').click()
        self.page.locator('[name="label"]').fill("Required retained label")
        self.page.locator("#save").click()
        self.wait_message("не подтверждён")
        self.assertEqual(self.read()["version"], before + 1)
        self.assertEqual(
            self.page.locator('[name="label"]').input_value(), "Required retained label"
        )
        self.assertTrue(self.page.locator("#routeAuto").is_disabled())
        self.page.locator("#save").click()
        self.wait_message("Сохранено")
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0], posts[1])
        self.assertEqual(self.read()["version"], before + 1)
        self.assertEqual(self.read()["relations"][0]["label"], "Required retained label")

    def test_lost_layout_response_without_editor_has_reachable_check(self) -> None:
        before = self.read()["version"]
        posts: list[dict] = []

        def lose_layout_response(route):
            if route.request.post_data_json.get("preview"):
                route.continue_()
                return
            posts.append(route.request.post_data_json)
            response = route.fetch()
            if len(posts) == 1:
                route.abort("failed")
            else:
                route.fulfill(response=response)

        self.page.locator("#cancel").click()
        self.assertFalse(self.page.locator("#editor").is_visible())
        self.page.route("**/api/manager_structure/apply", lose_layout_response)
        box = self.page.locator('.node[data-id="T1"]').bounding_box()
        self.assertIsNotNone(box)
        self.page.mouse.move(box["x"] + 35, box["y"] + 45)
        self.page.mouse.down()
        self.page.mouse.move(box["x"] + 70, box["y"] + 60, steps=3)
        self.page.mouse.up()
        self.wait_message("не подтверждён")
        self.assertFalse(self.page.locator("#editor").is_visible())
        self.assertTrue(self.page.locator("#reloadSchema").is_visible())
        self.assertEqual(self.page.locator("#reloadSchema").inner_text(), "Проверить запись")
        self.assertTrue(self.page.locator("#reloadSchema").is_enabled())
        self.page.locator("#reloadSchema").click()
        self.wait_message("Сохранено")
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0], posts[1])
        self.assertEqual(self.read()["version"], before + 1)
        self.assertTrue(self.page.locator("#editor").is_visible())

    def test_navigation_requires_discard_and_external_delete_invalidates_clean_editor(self):
        self.page.locator('[name="instruction"]').fill("Unsaved instruction")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#jump").select_option("element:T2")
        self.assertEqual(self.page.locator('[name="id"]').input_value(), "T1")
        self.assertEqual(self.page.locator("#jump").input_value(), "element:T1")
        self.assertEqual(
            self.page.locator('[name="instruction"]').input_value(), "Unsaved instruction"
        )
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#jump").select_option("element:T2")
        self.assertEqual(self.page.locator('[name="id"]').input_value(), "T2")
        self.page.locator("#jump").select_option("element:T1")
        self.assertEqual(self.page.locator('[name="id"]').input_value(), "T1")
        deleted = self.write(operation="remove_element", id="T1")
        self.page.wait_for_function(
            "version => document.getElementById('count').textContent.includes('v'+version)",
            arg=deleted["version"],
            timeout=8000,
        )
        self.assertFalse(self.page.locator("#editor").is_visible())
        self.assertFalse(self.page.locator("#save").is_visible())
        self.assertFalse(any(node["id"] == "T1" for node in self.read()["elements"]))

    def test_session_switch_hides_owner_draft_and_restores_it_for_same_owner(self) -> None:
        viewer = self.viewer_token()
        self.page.locator('[name="instruction"]').fill("Owner private draft")
        self.page.evaluate("token => localStorage.setItem('kanban-operator-session',token)", viewer)
        self.page.locator("#mode").get_by_text("Просмотр", exact=True).wait_for(timeout=8000)
        self.assertFalse(self.page.locator("#editor").is_visible())
        self.assertFalse(self.page.locator("#save").is_visible())
        self.assertTrue(self.page.locator('[data-create="module"]').is_disabled())
        self.assertTrue(self.page.locator("#expandCanvas").is_disabled())
        self.assertNotEqual(
            self.page.locator('[name="instruction"]').input_value(), "Owner private draft"
        )
        self.page.evaluate(
            "token => localStorage.setItem('kanban-operator-session',token)", self.admin
        )
        self.page.locator("#mode").get_by_text("Владелец", exact=False).wait_for(timeout=8000)
        self.assertEqual(
            self.page.locator('[name="instruction"]').input_value(), "Owner private draft"
        )
        self.assertTrue(self.page.locator('[name="instruction"]').is_enabled())
        self.assertEqual(self.read()["elements"][0]["instruction"], "Original instruction")

    def test_unconfirmed_owner_attempt_survives_session_switch(self) -> None:
        viewer = self.viewer_token()
        before = self.read()["version"]
        posts: list[dict] = []

        def lose_first(route):
            posts.append(route.request.post_data_json)
            response = route.fetch()
            if len(posts) == 1:
                route.abort("failed")
            else:
                route.fulfill(response=response)

        self.page.route("**/api/manager_structure/apply", lose_first)
        self.page.locator('[name="instruction"]').fill("Pending owner instruction")
        self.page.locator("#save").click()
        self.wait_message("не подтверждён")
        self.page.evaluate("token => localStorage.setItem('kanban-operator-session',token)", viewer)
        self.page.locator("#mode").get_by_text("Просмотр", exact=True).wait_for(timeout=8000)
        self.assertFalse(self.page.locator("#save").is_visible())
        self.page.evaluate(
            "token => localStorage.setItem('kanban-operator-session',token)", self.admin
        )
        self.page.locator("#mode").get_by_text("Владелец", exact=False).wait_for(timeout=8000)
        self.assertEqual(self.page.locator("#save").inner_text(), "Проверить запись")
        self.assertEqual(
            self.page.locator('[name="instruction"]').input_value(), "Pending owner instruction"
        )
        self.page.locator("#save").click()
        self.wait_message("Сохранено")
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0], posts[1])
        self.assertEqual(self.read()["version"], before + 1)
        self.assertTrue(self.page.locator('[name="instruction"]').is_enabled())

    def test_double_click_freezes_fields_and_has_one_write(self) -> None:
        before = self.read()["version"]
        observed: list[dict] = []
        states: list[bool] = []

        def observe_pending(route):
            observed.append(route.request.post_data_json)
            states.append(self.page.locator('[name="instruction"]').is_disabled())
            route.continue_()

        self.page.route("**/api/manager_structure/apply", observe_pending)
        self.page.locator('[name="title"]').fill("One intended update")
        self.page.evaluate(
            "() => {const save=document.getElementById('save');save.click();save.click()}"
        )
        self.wait_message("Сохранено")
        self.assertEqual(len(observed), 1)
        self.assertEqual(states, [True])
        self.assertEqual(self.read()["version"], before + 1)

    def test_confirmed_clean_save_blocks_second_click_and_edits_reenable_it(self) -> None:
        before = self.read()["version"]
        observed: list[dict] = []

        def observe_post(route):
            observed.append(route.request.post_data_json)
            route.continue_()

        self.page.route("**/api/manager_structure/apply", observe_post)
        self.assertTrue(self.page.locator("#save").is_disabled())
        self.page.locator('[name="title"]').fill("One confirmed intended update")
        self.assertTrue(self.page.locator("#save").is_enabled())
        self.page.locator("#save").click()
        self.wait_message("Сохранено")
        self.assertTrue(self.page.locator("#save").is_disabled())
        self.page.locator("#save").evaluate("button => button.click()")
        self.assertEqual(len(observed), 1)
        self.assertEqual(self.read()["version"], before + 1)
        self.page.locator('[name="kind"]').select_option("storage")
        self.assertTrue(self.page.locator("#save").is_enabled())
        self.page.locator("#cancel").click()
        self.page.locator('[data-create="module"]').click()
        self.assertTrue(self.page.locator("#save").is_enabled())
        self.page.locator("#cancel").click()
        self.assertEqual(len(observed), 1)

    def test_instruction_upload_and_explicit_route_rebuild_reenable_save(self) -> None:
        self.assertTrue(self.page.locator("#save").is_disabled())
        self.page.locator("#instructionFile").set_input_files(
            {"name": "current.md", "mimeType": "text/markdown", "buffer": b"Imported current text"}
        )
        self.wait_message("загружен в поле")
        self.assertTrue(self.page.locator("#save").is_enabled())
        self.page.locator("#save").click()
        self.wait_message("Сохранено")
        self.assertEqual(self.read()["elements"][0]["instruction"], "Imported current text")
        self.write(
            operation="layout_relation",
            relation={
                "id": "R1",
                "from": "T1",
                "to": "T2",
                "kind": "exchange",
                "direction": "forward",
                "label": "Existing automatic route",
            },
        )
        self.page.reload()
        self.page.locator("#modeToggle").click()
        self.page.locator("#jump").select_option("relation:R1")
        before = self.read()["version"]
        observed: list[dict] = []

        def observe_post(route):
            observed.append(route.request.post_data_json)
            route.continue_()

        self.page.route("**/api/manager_structure/apply", observe_post)
        self.assertTrue(self.page.locator("#save").is_disabled())
        self.page.locator("#routeAuto").click()
        self.assertTrue(self.page.locator("#save").is_enabled())
        self.page.locator("#save").click()
        self.wait_message("Сохранено")
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["operation"], "layout_relation")
        self.assertEqual(self.read()["version"], before + 1)
        self.assertTrue(self.page.locator("#save").is_disabled())

    def test_new_object_cannot_silently_replace_existing_id(self) -> None:
        before = self.read()["version"]
        self.page.locator('[data-create="module"]').click()
        self.page.locator('[name="id"]').fill("T1")
        self.page.locator('[name="title"]').fill("Accidental overwrite")
        self.page.locator("#save").click()
        self.wait_message("ID уже существует")
        self.assertEqual(self.read()["version"], before)
        self.assertEqual(self.read()["elements"][0]["title"], "Original one")
        self.assertEqual(self.page.locator('[name="title"]').input_value(), "Accidental overwrite")

    def test_nested_module_on_compact_parent_starts_with_valid_dimensions(self) -> None:
        self.write(operation="upsert_element", element={"id": "T1", "height": 60, "compact": True})
        self.page.reload()
        self.page.locator("#modeToggle").click()
        self.page.locator('.node[data-id="T1"]').click()
        self.page.locator('[data-create="item"]').click()
        self.assertTrue(
            self.page.locator('[name="width"]').evaluate("field => field.validity.valid")
        )
        self.assertTrue(
            self.page.locator('[name="height"]').evaluate("field => field.validity.valid")
        )
        self.page.locator('[name="title"]').fill("Compact parent child")
        self.page.locator("#save").click()
        self.wait_message("Сохранено")
        child = next(
            node for node in self.read()["elements"] if node["title"] == "Compact parent child"
        )
        self.assertEqual(child["parent"], "T1")
        self.assertGreaterEqual(child["height"], 24)

    def delay_instruction_file(self, contents: bytes = b"Late imported text") -> None:
        self.page.evaluate(
            """() => {
                const original=File.prototype.arrayBuffer;
                File.prototype.arrayBuffer=function(){
                    if(this.name!=='delayed.md')return original.call(this);
                    return new Promise(resolve=>{
                        window.releaseInstructionFile=async()=>{
                            resolve(await original.call(this));
                            await new Promise(done=>setTimeout(done,0));
                        };
                    });
                };
            }"""
        )
        self.page.locator("#instructionFile").set_input_files(
            {"name": "delayed.md", "mimeType": "text/markdown", "buffer": contents}
        )
        self.page.wait_for_function("typeof window.releaseInstructionFile==='function'")

    def test_delayed_file_decode_cannot_replace_saved_or_other_session_form(self) -> None:
        self.delay_instruction_file()
        self.page.locator('[name="title"]').fill("Saved before file decoding")
        self.page.locator("#save").click()
        self.wait_message("Сохранено")
        self.page.evaluate("async () => await window.releaseInstructionFile()")
        self.assertEqual(
            self.page.locator('[name="instruction"]').input_value(), "Original instruction"
        )
        self.assertEqual(self.page.locator("#message").inner_text(), "Сохранено")
        self.assertEqual(self.read()["elements"][0]["instruction"], "Original instruction")
        self.page.reload()
        self.page.locator("#modeToggle").click()
        self.page.locator('.node[data-id="T1"]').click()
        viewer = self.viewer_token()
        self.delay_instruction_file()
        self.page.evaluate("token => localStorage.setItem('kanban-operator-session',token)", viewer)
        self.page.locator("#mode").get_by_text("Просмотр", exact=True).wait_for(timeout=8000)
        self.page.evaluate("async () => await window.releaseInstructionFile()")
        self.assertFalse(self.page.locator("#editor").is_visible())
        self.assertNotEqual(
            self.page.locator('[name="instruction"]').input_value(), "Late imported text"
        )
        self.assertEqual(self.read()["elements"][0]["instruction"], "Original instruction")

    def test_newer_instruction_file_wins_out_of_order_decoding(self) -> None:
        for older in (b"Older imported text", b"\xff"):
            with self.subTest(older_is_valid_utf8=older != b"\xff"):
                self.page.evaluate(
                    """() => {
                        const original=File.prototype.arrayBuffer;
                        window.releaseManagerFiles={};
                        File.prototype.arrayBuffer=function(){
                            if(!['older.md','newer.md'].includes(this.name))return original.call(this);
                            return new Promise(resolve=>{
                                window.releaseManagerFiles[this.name]=async()=>{
                                    resolve(await original.call(this));
                                    await new Promise(done=>setTimeout(done,0));
                                };
                            });
                        };
                    }"""
                )
                self.page.locator("#instructionFile").set_input_files(
                    {"name": "older.md", "mimeType": "text/markdown", "buffer": older}
                )
                self.page.locator("#instructionFile").set_input_files(
                    {
                        "name": "newer.md",
                        "mimeType": "text/markdown",
                        "buffer": b"Newest imported text",
                    }
                )
                self.page.wait_for_function("Object.keys(window.releaseManagerFiles).length===2")
                self.assertTrue(self.page.locator("#save").is_disabled())
                self.page.evaluate("async () => await window.releaseManagerFiles['newer.md']()")
                self.wait_message("newer.md")
                self.assertEqual(
                    self.page.locator('[name="instruction"]').input_value(), "Newest imported text"
                )
                message = self.page.locator("#message").inner_text()
                self.page.evaluate("async () => await window.releaseManagerFiles['older.md']()")
                self.assertEqual(
                    self.page.locator('[name="instruction"]').input_value(), "Newest imported text"
                )
                self.assertEqual(self.page.locator("#message").inner_text(), message)
                self.assertTrue(self.page.locator("#save").is_enabled())
                self.assertEqual(self.read()["elements"][0]["instruction"], "Original instruction")
                self.page.locator("#cancel").click()
                if older != b"\xff":
                    self.fixture()

    def test_late_invalid_file_error_does_not_change_another_session_notice(self) -> None:
        viewer = self.viewer_token()
        self.delay_instruction_file(b"\xff")
        self.page.evaluate("token => localStorage.setItem('kanban-operator-session',token)", viewer)
        self.page.locator("#mode").get_by_text("Просмотр", exact=True).wait_for(timeout=8000)
        message = self.page.locator("#message").inner_text()
        self.page.evaluate("async () => await window.releaseInstructionFile()")
        self.assertEqual(self.page.locator("#message").inner_text(), message)
        self.assertFalse(self.page.locator("#editor").is_visible())
        self.assertTrue(self.page.locator("#instructionFile").is_disabled())

    def test_fractional_saved_geometry_anchors_and_label_allow_property_edits(self) -> None:
        self.write(operation="upsert_element", element={"id": "T1", "x": 60.5, "width": 220.5})
        self.page.reload()
        self.page.locator("#modeToggle").click()
        self.page.locator('.node[data-id="T1"]').click()
        self.page.locator('[name="instruction"]').fill("Edited with fractional geometry")
        self.assertTrue(self.page.locator('[name="x"]').evaluate("field => field.validity.valid"))
        self.assertTrue(
            self.page.locator('[name="width"]').evaluate("field => field.validity.valid")
        )
        self.page.locator("#save").click()
        self.wait_message("Сохранено")
        node = self.read()["elements"][0]
        self.assertEqual(node["x"], 60.5)
        self.assertEqual(node["width"], 220.5)
        self.assertEqual(node["instruction"], "Edited with fractional geometry")
        self.write(
            operation="layout_relation",
            relation={
                "id": "R1",
                "from": "T1",
                "to": "T2",
                "kind": "exchange",
                "direction": "forward",
                "label": "Fractional anchors",
                "from_anchor": {"side": "right", "offset": 0.333},
                "to_anchor": {"side": "left", "offset": 0.666},
                "label_mode": "manual",
                "label_x": 320.5,
                "label_y": 35.5,
                "label_max_width": 220.5,
            },
        )
        self.page.reload()
        self.page.locator("#modeToggle").click()
        self.page.locator("#jump").select_option("relation:R1")
        for name in ("from_offset", "to_offset", "label_max_width"):
            self.assertTrue(
                self.page.locator(f'[name="{name}"]').evaluate("field => field.validity.valid")
            )
        self.page.locator('[name="label"]').fill("Edited with fractional anchors")
        self.page.locator("#save").click()
        self.wait_message("Сохранено")
        relation = self.read()["relations"][0]
        self.assertEqual(relation["label"], "Edited with fractional anchors")
        self.assertEqual(relation["from_anchor"]["offset"], 0.333)
        self.assertEqual(relation["to_anchor"]["offset"], 0.666)
        self.assertEqual(relation["label_max_width"], 220.5)

    def test_failed_explicit_reload_keeps_draft_and_unauthorized_read_locks_editor(self):
        self.page.locator('[name="instruction"]').fill("Draft before conflict")
        changed = self.write(operation="upsert_element", element={"id": "T2", "title": "External"})
        self.page.wait_for_function(
            "version => document.getElementById('count').textContent.includes('v'+version)",
            arg=changed["version"],
            timeout=8000,
        )
        self.page.route("**/api/manager_structure", lambda route: route.abort("failed"))
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#reloadSchema").click()
        self.wait_message("Ваш текст оставлен")
        self.assertEqual(
            self.page.locator('[name="instruction"]').input_value(), "Draft before conflict"
        )
        self.page.unroute("**/api/manager_structure")
        self.page.route(
            "**/api/manager_structure",
            lambda route: route.fulfill(
                status=401,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"code": "session_expired", "message": "Expired"}}
                ),
            ),
        )
        self.page.locator("#mode").get_by_text("Нет доступа", exact=True).wait_for(timeout=8000)
        self.assertTrue(self.page.locator('[name="instruction"]').is_disabled())
        self.assertTrue(self.page.locator('[data-create="module"]').is_disabled())
        self.assertTrue(self.page.locator("#expandCanvas").is_disabled())
        self.assertFalse(self.page.locator("#save").is_visible())
