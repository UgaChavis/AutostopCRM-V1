"""Verify the offline CommonMark package included in the structure screen."""

from __future__ import annotations

import hashlib
import json
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
    def test_pinned_vendor_checksum_and_complete_notices(self):
        source = resources.files("minimal_kanban.web_app_assets").joinpath("source")
        manifest = json.loads(source.joinpath("markdown_it.provenance.json").read_text())
        vendor = source.joinpath(manifest["file"]).read_bytes()
        # Independently verified official markdown-it 15.0.2 UMD distribution.
        self.assertEqual(
            manifest["sha256"],
            "635972b985228e8af9f0143647c68616b7a3bb09f6946e7e4a52e43dcf5e7be5",
        )
        self.assertEqual(hashlib.sha256(vendor).hexdigest(), manifest["sha256"])
        self.assertEqual(len(vendor), manifest["bytes"])
        self.assertEqual(manifest["version"], "15.0.2")
        notices = source.joinpath(manifest["license_file"]).read_text()
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
            self.assertIn(source.joinpath(name).read_text(), MANAGER_STRUCTURE_HTML)
        self.assertNotIn("// @include", MANAGER_STRUCTURE_HTML)
        scripts = _Scripts()
        scripts.feed(MANAGER_STRUCTURE_HTML)
        self.assertEqual(scripts.external, [])
