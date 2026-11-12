"""Verify the offline CommonMark package included in the structure screen."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from importlib import resources

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.web_assets import MANAGER_STRUCTURE_HTML  # noqa: E402


class _Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.external: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.external.extend(value or "" for name, value in attrs if name == "src")


class ManagerDocumentationAssetsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_website_module_links_use_canonical_pinned_source_and_keep_custom_guard(self):
        source = resources.files("minimal_kanban.web_app_assets").joinpath("source")
        dialog = source.joinpath("manager_tool_dialog.js").read_text(encoding="utf-8")
        declarations = "\n".join(
            line
            for line in dialog.splitlines()
            if line.startswith(("function moduleDocumentRef(", "function textElement("))
        )
        script = (
            "const assert=require('node:assert/strict');\n"
            "const window={markdownit:require("
            + json.dumps(str(source.joinpath("markdown_it.js")))
            + ")};\n"
            + """
function element(tag){return {tagName:tag.toUpperCase(),children:[],textContent:'',
  append(...nodes){this.children.push(...nodes)},
  replaceChildren(...nodes){this.children=nodes}}}
const document={createElement:element,createTextNode:text=>({textContent:text})};
function hrefs(node){return [...(node.href?[node.href]:[]),
  ...(node.children||[]).flatMap(hrefs)]}
"""
            + declarations
            + "\n"
            + source.joinpath("manager_documentation.js").read_text(encoding="utf-8")
            + """
const revision='a'.repeat(40),base=`https://github.com/UgaChavis/AutostopManager/blob/${revision}/`;
const reference=moduleDocumentRef('K1');
assert.equal(reference,'docs/agent/modules/K1.md');
assert.equal(documentLink('K1',reference,revision).href,base+reference);
const target=element('div');
instructionLinks(target,'[M2](M2.md) [Root](../../../AGENTS.md) '+
  '[Website runtime](../references/website-runtime.md) [Site](https://autostop24.ru/)',
  reference,revision);
assert.deepEqual(hrefs(target),[base+'docs/agent/modules/M2.md',base+'AGENTS.md',
  base+'docs/agent/references/website-runtime.md','https://autostop24.ru/']);
assert.equal(moduleDocumentRef('K10'),'');
assert.equal(moduleDocumentRef('CUSTOM1'),'');
const custom=element('div');
instructionLinks(custom,'[Sibling](M2.md) [Public](https://autostop24.ru/) '+
  '[Local](file:///etc/passwd)',moduleDocumentRef('CUSTOM1'),revision);
assert.deepEqual(hrefs(custom),['https://autostop24.ru/']);
"""
        )
        result = subprocess.run(
            [shutil.which("node"), "-e", script], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pinned_vendor_checksum_and_complete_notices(self):
        source = resources.files("minimal_kanban.web_app_assets").joinpath("source")
        manifest = json.loads(
            source.joinpath("markdown_it.provenance.json").read_text(encoding="utf-8")
        )
        vendor = source.joinpath(manifest["file"]).read_bytes()
        # Independently verified official markdown-it 15.0.2 UMD distribution.
        self.assertEqual(
            manifest["sha256"],
            "635972b985228e8af9f0143647c68616b7a3bb09f6946e7e4a52e43dcf5e7be5",
        )
        self.assertEqual(hashlib.sha256(vendor).hexdigest(), manifest["sha256"])
        self.assertEqual(len(vendor), manifest["bytes"])
        self.assertEqual(manifest["version"], "15.0.2")
        notices = source.joinpath(manifest["license_file"]).read_text(encoding="utf-8")
        self.assertIn("Copyright (c) 2014 Vitaly Puzrin, Alex Kocharin", notices)
        self.assertEqual(
            {item["name"]: item["license"] for item in manifest["bundled_components"]}["entities"],
            "BSD-2-Clause",
        )
        self.assertIn("Joyent", notices)
        for component in manifest["bundled_components"]:
            self.assertIn(
                "Bundled component: " + component["name"] + " " + component["version"], notices
            )
            self.assertTrue(component["compiled_sources_verified"])

    def test_packaged_screen_expands_parser_and_renderer_without_network(self):
        source = resources.files("minimal_kanban.web_app_assets").joinpath("source")
        for name in ("markdown_it.js", "manager_documentation.js"):
            self.assertIn(source.joinpath(name).read_text(encoding="utf-8"), MANAGER_STRUCTURE_HTML)
        self.assertNotIn("// @include", MANAGER_STRUCTURE_HTML)
        scripts = _Scripts()
        scripts.feed(MANAGER_STRUCTURE_HTML)
        self.assertEqual(scripts.external, [])
