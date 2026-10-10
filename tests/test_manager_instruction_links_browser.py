"""Pinned documentation links against a disposable CRM, without external reads."""

from __future__ import annotations

import json
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
                "",
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
                "![sample [NestedImageLink](E3.md)](img.png)",
                "![sample [NestedImageReference](E3.md)][image-ref] ![ImageShortcut]",
                "",
                "[image-ref]: img.png",
                "[imageshortcut]: img.png",
                r"\![EscapedBang](E3.md)",
                "Inline <!-- [HiddenInline](E3.md) --> [AfterInlineComment](E3.md)",
                "<!--",
                "[HiddenComment](E3.md)",
                "[hidden-ref]: E3.md",
                "```",
                "[HiddenCommentFence](E3.md)",
                "--> [HiddenClosingSuffix](E3.md)",
                "[AfterComment](E3.md) [HiddenReference][hidden-ref]",
                "[Script](javascript:alert(1)) [Data](data:text/html,<b>x</b>)",
                "[File](file:///etc/passwd) [Protocol](//example.com/read)",
                "[Absolute](/etc/passwd) [Escape](../../../../etc/passwd)",
                "[EncodedEscape](..%2F..%2F..%2F..%2Fetc/passwd)",
                "[Zero](E3.md:0) [Negative](E3.md:-1) [PositiveSign](E3.md:+1)",
                "[UnicodeLine](E3.md:١٢) [EncodedScript](javascript%3Aalert(1))",
                '<img src="x" onerror="window.instructionInjected=true">',
                "<!-- Unclosed [HiddenUnclosed](E3.md)",
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
            "EscapedBang": base + "docs/agent/modules/E3.md",
            "AfterInlineComment": base + "docs/agent/modules/E3.md",
            "AfterComment": base + "docs/agent/modules/E3.md",
        }
        for name, href in expected.items():
            with self.subTest(link=name):
                link = content.get_by_role("link", name=name, exact=True)
                self.assertEqual(link.get_attribute("href"), href)
                self.assertEqual(link.get_attribute("target"), "_blank")
                self.assertEqual(link.get_attribute("rel"), "noopener noreferrer")
        self.assertEqual(content.get_by_role("link").count(), len(expected))
        self.assertIn("[Script](javascript:alert(1))", content.inner_text())
        self.assertIn("[Inline](E3.md)", content.locator("code").all_text_contents())
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
                self.page.locator("#toolDialogScroll").evaluate("el => {el.scrollTop=0}")
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

    def test_commonmark_links_match_independent_oracles_without_html_execution(self):
        cases = json.loads(
            (
                Path(__file__).parent / "fixtures" / "manager_instruction_commonmark_cases.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(len(cases), 26)
        module_path = "docs/agent/modules/E3.md"
        for name, source, label in (
            (
                "closed-frontmatter",
                '---\nnav: "[Hidden](E3.md)"\n---\n\n[Visible](E3.md)',
                "Visible",
            ),
            ("cr-frontmatter", '---\rnav: "[Hidden](E3.md)"\r---\r\r[Visible](E3.md)', "Visible"),
            ("unclosed-frontmatter", '---\nnav: "[Visible](E3.md)"\n', "Visible"),
            ("table-link", "| Docs |\n| --- |\n| [Visible](E3.md) |", "Visible"),
            ("code-emphasis-label", "[Read `code` &amp; **bold**](E3.md)", "Read code & bold"),
            (
                "literal-script-block",
                "<script>\nwindow.instructionInjected=true;\n[Hidden](E3.md)\n</script>\n\n[Visible](E3.md)",
                "Visible",
            ),
            (
                "literal-html-events",
                '<span onclick="window.instructionInjected=true" title="[Hidden](E3.md)">text</span> [Visible](E3.md)',
                "Visible",
            ),
            (
                "literal-image-data",
                "![sample [Hidden](E3.md)](data:image/png;base64,AA==) [Visible](E3.md)",
                "Visible",
            ),
        ):
            cases.append(
                {"name": name, "source": source, "links": [{"label": label, "path": module_path}]}
            )
        # CommonMark rejects SVG data image destinations, so the inner Markdown
        # falls back to ordinary visible text/link tokens. Neither data URL nor
        # an actual image may reach the DOM.
        cases.append(
            {
                "name": "invalid-image-data-fallback",
                "source": "![sample [Hidden](E3.md)](data:image/svg+xml;base64,PHN2Zz4=) [Visible](E3.md)",
                "links": [
                    {"label": "Hidden", "path": module_path},
                    {"label": "Visible", "path": module_path},
                ],
            }
        )
        bundle = self.automotive_fixture()
        base = (
            "https://github.com/UgaChavis/AutostopManager/blob/" + bundle["source_revision"] + "/"
        )
        module = next(item for item in bundle["modules"] if item["element_id"] == "E2")
        self.page.route(
            self.runtime.base_url + "/api/manager_structure/tool_catalog",
            lambda route: route.fulfill(json={"ok": True, "data": bundle}),
        )
        before = self.read()
        results = []
        for case in cases:
            with self.subTest(markdown=case["name"]):
                module["instruction_text"] = case["source"]
                self.page.reload()
                self.page.locator('.node[data-id="E2"]').click()
                self.page.locator(".tool-card").first.wait_for()
                content = self.page.locator("#toolDialogInstruction")
                actual = content.evaluate(
                    'el => [...el.querySelectorAll("a")].map(a => ({label:a.textContent,href:a.getAttribute("href")}))'
                )
                expected = [
                    {"label": link["label"], "href": base + link["path"]} for link in case["links"]
                ]
                results.append(
                    {
                        "name": case["name"],
                        "source": case["source"],
                        "expected": expected,
                        "actual": actual,
                    }
                )
                self.assertEqual(actual, expected)
                self.assertEqual(content.locator("img,script,svg,iframe,object,embed").count(), 0)
                self.assertFalse(self.page.evaluate("Boolean(window.instructionInjected)"))
                for anchor in content.locator("a").all():
                    self.assertEqual(anchor.get_attribute("target"), "_blank")
                    self.assertEqual(anchor.get_attribute("rel"), "noopener noreferrer")
        self.assertEqual(self.read(), before)
        screenshots = os.environ.get("AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR")
        if screenshots:
            output = Path(screenshots)
            output.mkdir(parents=True, exist_ok=True)
            (output / "instruction-commonmark-dom.json").write_text(
                json.dumps(
                    {
                        "revision": bundle["source_revision"],
                        "cases": results,
                        "graph_unchanged": True,
                        "page_errors": self.errors,
                        "console_issues": self.console_issues,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            module["instruction_text"] = (
                "# CommonMark documentation\n\n"
                "[Full][nav] · [Read [label]](E3.md) · [Read `code` &amp; **bold**](E3.md)\n\n"
                "[nav]:\n  E3.md\n\n"
                "- List\n\n    [Nested](E3.md)\n\n"
                "| Docs |\n| --- |\n| [Visible](E3.md) |\n\n"
                '<span title="[Ghost](E3.md)">Literal HTML</span> [Visible](E3.md)\n\n'
                "`[Literal](E3.md)`\n"
            )
            self.page.reload()
            self.page.locator('.node[data-id="E2"]').click()
            self.page.locator(".tool-card").first.wait_for()
            for label, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                self.page.set_viewport_size({"width": width, "height": height})
                self.assertFalse(
                    self.page.locator("#toolDialog").evaluate(
                        "el => el.scrollWidth > el.clientWidth"
                    )
                )
                self.page.screenshot(path=str(output / f"instruction-commonmark-{label}.png"))
        self.assertEqual(self.errors, [])
        self.assertEqual(self.console_issues, [])

    def test_saved_module_links_keep_viewer_access_and_graph_unchanged(self):
        from sync_manager_structure_instructions import A5_POINTER

        bundle = self.automotive_fixture()
        for ident, y, instruction in (
            ("A2", 30, "[Navigation](docs/agent/modules/A1.md)"),
            ("A5", 280, A5_POINTER),
            (
                "X99",
                500,
                "[Unbased](E3.md) [External](https://example.com/read) [HTTP](http://example.com/read)",
            ),
        ):
            response = self.context.request.post(
                self.runtime.base_url + "/api/manager_structure/apply",
                headers={"X-Operator-Session": self.admin},
                data={
                    "operation": "upsert_element",
                    "element": {
                        "id": ident,
                        "title": "Инструкция агента",
                        "kind": "module",
                        "x": 430,
                        "y": y,
                        "width": 320,
                        "height": 80 if ident == "X99" else 200,
                        "instruction": instruction,
                    },
                    "expected_version": self.read()["version"],
                    "idempotency_key": "instruction-viewer-fixture-" + ident,
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
        self.page.keyboard.press("Escape")
        self.page.locator('.node[data-id="A5"]').click()
        self.page.locator("#toolDialog").wait_for(state="visible")
        self.page.locator("#toolDialogInstruction").get_by_role(
            "link", name="A5", exact=True
        ).wait_for()
        self.assertEqual(
            self.page.locator("#toolDialogMeta a").get_attribute("href"),
            base + "docs/agent/modules/A5.md",
        )
        links = self.page.locator("#toolDialogInstruction").get_by_role("link")
        self.assertEqual(links.count(), 3)
        for name in ("A5", "A3", "D1"):
            self.assertEqual(
                self.page.locator("#toolDialogInstruction")
                .get_by_role("link", name=name, exact=True)
                .get_attribute("href"),
                base + "docs/agent/modules/" + name + ".md",
            )
        self.assertFalse(self.page.locator("#toolStatusHelp").is_visible())
        self.assertEqual(self.page.locator(".tool-card").count(), 0)
        self.assertFalse(self.page.locator("#actions").is_visible())
        self.assertFalse(before["can_edit"])
        self.assertEqual(self.read(viewer), before)
        screenshots = os.environ.get("AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR")
        if screenshots:
            output = Path(screenshots)
            output.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(output / "instruction-links-a5-viewer.png"))
        self.page.keyboard.press("Escape")
        self.page.locator('.node[data-id="X99"]').click()
        self.page.locator("#toolDialog").wait_for(state="visible")
        content = self.page.locator("#toolDialogInstruction")
        self.assertIn("Unbased", content.inner_text())
        self.assertEqual(content.get_by_role("link", name="Unbased", exact=True).count(), 0)
        self.assertEqual(self.page.locator("#toolDialogMeta a").count(), 0)
        self.assertEqual(content.get_by_role("link").count(), 2)
        for name, href in (
            ("External", "https://example.com/read"),
            ("HTTP", "http://example.com/read"),
        ):
            self.assertEqual(
                content.get_by_role("link", name=name, exact=True).get_attribute("href"), href
            )
        self.assertFalse(self.page.locator("#actions").is_visible())
        self.assertEqual(self.read(viewer), before)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.console_issues, [])
