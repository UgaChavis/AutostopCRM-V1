"""Pinned documentation links against a disposable CRM, without external reads."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

if __package__:
    from tests import test_manager_structure_browser as browser_support
else:
    import test_manager_structure_browser as browser_support


class ManagerInstructionLinksBrowserTests(unittest.TestCase):
    setUpClass = classmethod(browser_support.ManagerStructureBrowserTests.setUpClass.__func__)
    read = browser_support.ManagerStructureBrowserTests.read
    automotive_fixture = browser_support.ManagerStructureBrowserTests.automotive_fixture

    def setUp(self):
        browser_support.ManagerStructureBrowserTests.setUp(self)
        # The documentation fixture has no automation controller. Keep its poll
        # read-only and deterministic instead of logging a missing-config 422.
        self.page.route(
            self.runtime.base_url + "/api/automation_center/status",
            lambda route: route.fulfill(json={"ok": True, "data": {"overall_state": "unknown"}}),
        )
        self.console_issues = []
        self.page.on(
            "console",
            lambda message: (
                self.console_issues.append(message.text)
                if message.type in {"error", "warning"}
                else None
            ),
        )

    def test_pinned_relative_links_literals_and_desktop_mobile_navigation(self):
        bundle = self.automotive_fixture()
        revision = bundle["source_revision"]
        base = f"https://github.com/UgaChavis/AutostopManager/blob/{revision}/"
        module = next(item for item in bundle["modules"] if item["element_id"] == "E2")
        module["instruction_text"] = "\n".join(
            [
                "# Safe links",
                "[Sibling](E3.md) [Root](../../../AGENTS.md#autostop-manager)",
                "[Guide](../references/host-operations.md)",
                "[Line](E3.md:001?version=2#section) [EncodedLine](E3%2Emd:%31%32)",
                "[Colon](E3.md%3A12) [Once](encoded%2520name.md)",
                "[Full][nav] [Collapsed][] [Shortcut]",
                '[nav]: E3.md "Navigation"',
                "[collapsed]: ../references/host-operations.md",
                "[shortcut]: ../../../AGENTS.md",
                "[External](https://example.com/read) [HTTP](http://example.com/read)",
                "`[Inline](E3.md)`",
                "``multi line",
                "[CodeSpan](E3.md)",
                "``",
                "```markdown",
                "[Fence](E3.md)",
                "```",
                "~~~~",
                "[Tilde](E3.md)",
                "~~~~",
                "    [Indented](E3.md)",
                r"\[Escaped](E3.md) ![Image](E3.md)",
                "[Script](javascript:alert(1)) [Data](data:text/html,<b>x</b>)",
                "[File](file:///etc/passwd) [Protocol](//example.com/read)",
                "[Absolute](/etc/passwd) [Escape](../../../../etc/passwd)",
                "[EncodedEscape](..%2F..%2F..%2F..%2Fetc/passwd)",
                "[Zero](E3.md:0) [Negative](E3.md:-1) [PositiveSign](E3.md:+1)",
                "[UnicodeLine](E3.md:١٢) [EncodedScript](javascript%3Aalert(1))",
                '<img src="x" onerror="window.instructionInjected=true">',
            ]
        )
        tool = next(item for item in bundle["tools"] if item["tool_id"] == module["tool_ids"][0])
        tool["instruction_text"] = "Tool context: [Module](../modules/E2.md)."
        self.page.route(
            self.runtime.base_url + "/api/manager_structure/tool_catalog",
            lambda route: route.fulfill(json={"ok": True, "data": bundle}),
        )
        self.context.route(
            "https://github.com/UgaChavis/AutostopManager/blob/**",
            lambda route: route.fulfill(body="<!doctype html><title>Pinned documentation</title>"),
        )
        self.page.reload()
        node = self.page.locator('.node[data-id="E2"]')
        node.focus()
        self.page.keyboard.press("Enter")
        dialog = self.page.locator("#toolDialog")
        dialog.wait_for(state="visible")
        content = self.page.locator("#toolDialogInstruction")
        expected = {
            "Sibling": base + "docs/agent/modules/E3.md",
            "Root": base + "AGENTS.md#autostop-manager",
            "Guide": base + "docs/agent/references/host-operations.md",
            "Line": base + "docs/agent/modules/E3.md#L1",
            "EncodedLine": base + "docs/agent/modules/E3.md#L12",
            "Colon": base + "docs/agent/modules/E3.md#L12",
            "Once": base + "docs/agent/modules/encoded%2520name.md",
            "Full": base + "docs/agent/modules/E3.md",
            "Collapsed": base + "docs/agent/references/host-operations.md",
            "Shortcut": base + "AGENTS.md",
            "External": "https://example.com/read",
            "HTTP": "http://example.com/read",
        }
        for name, href in expected.items():
            with self.subTest(link=name):
                link = content.get_by_role("link", name=name, exact=True)
                self.assertEqual(link.get_attribute("href"), href)
                self.assertEqual(link.get_attribute("target"), "_blank")
                self.assertEqual(link.get_attribute("rel"), "noopener noreferrer")
        self.assertEqual(content.get_by_role("link").count(), len(expected))
        self.assertIn("[Script](javascript:alert(1))", content.inner_text())
        self.assertIn("`[Inline](E3.md)`", content.inner_text())
        self.assertEqual(content.locator("img,script,b").count(), 0)
        self.assertFalse(self.page.evaluate("Boolean(window.instructionInjected)"))
        self.assertEqual(
            self.page.locator("#toolDialogMeta a").get_attribute("href"),
            base + module["instruction_ref"],
        )
        card = self.page.locator(".tool-card").first
        card.locator("summary").click()
        self.assertEqual(
            card.locator(".tool-mini-instruction").get_by_role("link").get_attribute("href"),
            base + "docs/agent/modules/E2.md",
        )
        self.assertEqual(
            card.locator(".tool-technical a").get_attribute("href"),
            base + tool["instruction_ref"],
        )
        screenshots = os.environ.get("AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR")
        for label, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
            with self.subTest(viewport=label):
                self.page.set_viewport_size({"width": width, "height": height})
                self.assertEqual(self.page.url, self.runtime.base_url + "/manager-structure")
                self.assertEqual(self.page.title(), "Конструктор структуры менеджера · AutoStop")
                self.assertFalse(dialog.evaluate("el => el.scrollWidth > el.clientWidth"))
                if screenshots:
                    output = Path(screenshots)
                    output.mkdir(parents=True, exist_ok=True)
                    self.page.screenshot(path=str(output / f"instruction-links-{label}.png"))
                with self.page.expect_popup() as popup:
                    content.get_by_role("link", name="Sibling", exact=True).click()
                document = popup.value
                document.wait_for_load_state()
                self.assertEqual(document.url, expected["Sibling"])
                self.assertEqual(document.title(), "Pinned documentation")
                document.close()
                self.assertTrue(dialog.is_visible())
        self.page.keyboard.press("Escape")
        self.assertTrue(node.evaluate("el => el === document.activeElement"))
        for source in (
            "docs/agent/modules/../modules/E2.md",
            "../outside.md",
            "docs/%2e%2e/%2e%2e/outside.md",
            "/etc/passwd",
            "javascript%3Aoutside.md",
            "docs/%0aoutside.md",
        ):
            with self.subTest(source=source):
                module["instruction_ref"] = source
                self.page.reload()
                self.page.locator('.node[data-id="E2"]').click()
                dialog.wait_for(state="visible")
                self.page.locator("#toolDialogMeta").get_by_text(
                    " · Manager", exact=False
                ).wait_for()
                sibling = content.get_by_role("link", name="Sibling", exact=True)
                if source.startswith("docs/agent/"):
                    self.assertEqual(sibling.get_attribute("href"), expected["Sibling"])
                    self.assertEqual(
                        self.page.locator("#toolDialogMeta a").get_attribute("href"),
                        base + "docs/agent/modules/E2.md",
                    )
                else:
                    self.assertEqual(sibling.count(), 0)
                    self.assertEqual(self.page.locator("#toolDialogMeta a").count(), 0)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.console_issues, [])

    def test_saved_module_links_keep_viewer_access_and_graph_unchanged(self):
        bundle = self.automotive_fixture()
        response = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "upsert_element",
                "element": {
                    "id": "A2",
                    "title": "Инструкция агента",
                    "kind": "module",
                    "x": 430,
                    "y": 30,
                    "width": 320,
                    "height": 200,
                    "instruction": "[Navigation](docs/agent/modules/A1.md)",
                },
                "expected_version": self.read()["version"],
                "idempotency_key": "instruction-viewer-fixture",
            },
        )
        self.assertEqual(response.status, 200)
        created = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.admin},
            data={
                "username": "documentation-viewer",
                "password": "documentation-viewer-password",
                "role": "operator",
            },
        )
        self.assertEqual(created.status, 200)
        viewer = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "documentation-viewer", "password": "documentation-viewer-password"},
        ).json()["data"]["session"]["token"]
        before = self.read(viewer)
        self.page.evaluate("token => localStorage.setItem('kanban-operator-session',token)", viewer)
        self.page.reload()
        self.page.locator('.node[data-id="A2"]').click()
        self.page.locator("#toolDialog").wait_for(state="visible")
        base = (
            "https://github.com/UgaChavis/AutostopManager/blob/" + bundle["source_revision"] + "/"
        )
        self.assertEqual(
            self.page.locator("#toolDialogMeta a").get_attribute("href"), base + "AGENTS.md"
        )
        self.assertEqual(
            self.page.locator("#toolDialogInstruction").get_by_role("link").get_attribute("href"),
            base + "docs/agent/modules/A1.md",
        )
        self.assertFalse(self.page.locator("#toolStatusHelp").is_visible())
        self.assertEqual(self.page.locator(".tool-card").count(), 0)
        self.assertFalse(self.page.locator("#actions").is_visible())
        self.assertFalse(before["can_edit"])
        self.assertEqual(self.read(viewer), before)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.console_issues, [])
