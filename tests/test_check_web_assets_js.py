from __future__ import annotations

import io
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

if __package__:
    from tests.source_path_support import ensure_repository_root_path
else:
    from source_path_support import ensure_repository_root_path

ensure_repository_root_path()

from scripts.check_web_assets_js import (  # noqa: E402
    ROOT,
    SRC,
    _browser_javascript_sources,
    extract_inline_scripts,
    main,
)


class InlineScriptExtractorTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("node"), "Node.js is required for generated browser JS syntax check"
    )
    def test_generated_inline_javascript_is_syntax_valid(self) -> None:
        script = ROOT / "scripts" / "check_web_assets_js.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_node_timeout_returns_actionable_error(self) -> None:
        stderr = io.StringIO()
        with (
            patch("scripts.check_web_assets_js.shutil.which", return_value="node"),
            patch(
                "scripts.check_web_assets_js._browser_javascript_sources",
                return_value=[("board_external", "const value = 1;")],
            ),
            patch(
                "scripts.check_web_assets_js.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="node --check", timeout=30),
            ),
            redirect_stderr(stderr),
        ):
            self.assertEqual(main(), 1)

        self.assertIn("timed out after 30s", stderr.getvalue())
        self.assertIn("board_external", stderr.getvalue())

    def test_node_launch_error_returns_actionable_error(self) -> None:
        stderr = io.StringIO()
        with (
            patch("scripts.check_web_assets_js.shutil.which", return_value="node"),
            patch(
                "scripts.check_web_assets_js._browser_javascript_sources",
                return_value=[("board_external", "const value = 1;")],
            ),
            patch(
                "scripts.check_web_assets_js.subprocess.run",
                side_effect=OSError("node executable disappeared"),
            ),
            redirect_stderr(stderr),
        ):
            self.assertEqual(main(), 1)

        self.assertIn("Could not start Node.js syntax check", stderr.getvalue())
        self.assertIn("board_external", stderr.getvalue())
        self.assertIn("node executable disappeared", stderr.getvalue())

    def test_node_failure_without_output_names_script(self) -> None:
        stderr = io.StringIO()
        failed_result = subprocess.CompletedProcess(
            args=["node", "--check"],
            returncode=2,
            stdout="",
            stderr="",
        )
        with (
            patch("scripts.check_web_assets_js.shutil.which", return_value="node"),
            patch(
                "scripts.check_web_assets_js._browser_javascript_sources",
                return_value=[("board_external", "const value = 1;")],
            ),
            patch(
                "scripts.check_web_assets_js.subprocess.run",
                return_value=failed_result,
            ),
            redirect_stderr(stderr),
        ):
            self.assertEqual(main(), 2)

        self.assertIn("failed for board_external", stderr.getvalue())
        self.assertIn("exit code 2", stderr.getvalue())

    def test_script_with_empty_src_is_not_treated_as_inline(self) -> None:
        self.assertEqual(
            extract_inline_scripts('<script src="">const value = 1</script>'),
            [],
        )

    def test_required_pages_without_inline_javascript_fail(self) -> None:
        _browser_javascript_sources()
        web_assets = sys.modules["minimal_kanban.web_assets"]
        for attribute, document_name in (
            ("DISPLAY_DASHBOARD_HTML", "display_dashboard"),
            ("MODULE_MAP_HTML", "module_map"),
        ):
            for html in ("", "<script> \n </script>", '<script src="app.js"></script>'):
                with self.subTest(document=document_name, html=html):
                    with patch.object(web_assets, attribute, html):
                        with self.assertRaisesRegex(ValueError, document_name):
                            _browser_javascript_sources()

    def test_empty_board_javascript_fails_source_check(self) -> None:
        _browser_javascript_sources()
        web_assets = sys.modules["minimal_kanban.web_assets"]
        for source in ("", " \n "):
            with self.subTest(source=source):
                with patch.object(web_assets, "BOARD_WEB_APP_JS", source):
                    with self.assertRaisesRegex(ValueError, "board_external"):
                        _browser_javascript_sources()

    def test_missing_inline_javascript_exits_before_node_check(self) -> None:
        stderr = io.StringIO()
        with (
            patch("scripts.check_web_assets_js.shutil.which", return_value="node"),
            patch(
                "scripts.check_web_assets_js._browser_javascript_sources",
                side_effect=ValueError("No inline JavaScript found in module_map HTML."),
            ),
            patch("scripts.check_web_assets_js.subprocess.run") as node_check,
            redirect_stderr(stderr),
        ):
            self.assertEqual(main(), 1)
        self.assertIn("module_map", stderr.getvalue())
        node_check.assert_not_called()

    def test_multiple_inline_scripts_keep_document_order(self) -> None:
        _browser_javascript_sources()
        web_assets = sys.modules["minimal_kanban.web_assets"]
        with patch.object(
            web_assets,
            "DISPLAY_DASHBOARD_HTML",
            "<script>const first = 1;</script><script>const second = 2;</script>",
        ):
            scripts = _browser_javascript_sources()
        self.assertEqual(
            [script for name, script in scripts if name == "display_dashboard"],
            ["const first = 1;", "const second = 2;"],
        )

    def test_unclosed_inline_script_is_retained_at_eof(self) -> None:
        self.assertEqual(
            extract_inline_scripts("<script>const value = 1"),
            ["const value = 1"],
        )

    def test_browser_sources_do_not_duplicate_source_path(self) -> None:
        source_path = str(SRC)
        search_path = [source_path, *(entry for entry in sys.path if entry != source_path)]

        with patch.object(sys, "path", search_path):
            _browser_javascript_sources()
            self.assertEqual(sys.path.count(source_path), 1)

    def test_browser_sources_restores_source_path_added_for_import(self) -> None:
        source_path = str(SRC)
        search_path = [entry for entry in sys.path if entry != source_path]

        with patch.object(sys, "path", search_path):
            _browser_javascript_sources()
            self.assertNotIn(source_path, sys.path)


if __name__ == "__main__":
    unittest.main()
