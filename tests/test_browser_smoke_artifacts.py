from __future__ import annotations

import ast
import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from scripts import browser_smoke

SCREENSHOT_ENV = "AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR"


def artifact_statements(function_name: str, count: int) -> list[ast.stmt]:
    """Execute the real artifact block without unrelated UI workflow setup."""
    tree = ast.parse(Path(browser_smoke.__file__).read_text(encoding="utf-8"))
    function = next(node for node in tree.body if getattr(node, "name", None) == function_name)
    for parent in ast.walk(function):
        for _field, value in ast.iter_fields(parent):
            if not isinstance(value, list):
                continue
            for index, statement in enumerate(value):
                if isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "artifact_dir"
                    for target in statement.targets
                ):
                    return value[index : index + count]
    raise AssertionError(f"Missing artifact block in {function_name}")


class BrowserSmokeArtifactTests(unittest.TestCase):
    def test_absent_empty_and_whitespace_env_preserve_optional_default(self) -> None:
        default = Path("relative-default")
        for value in (None, "", " \t\r\n "):
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                if value is not None:
                    os.environ[SCREENSHOT_ENV] = value
                self.assertIsNone(browser_smoke._screenshot_directory())
                self.assertIsNone(browser_smoke._screenshot_directory(None))
                self.assertIs(browser_smoke._screenshot_directory(default), default)

    def test_env_path_is_trimmed_expanded_and_resolved_without_creating_it(self) -> None:
        for value in (" output/modernization/owned-artifacts ", " ~/owned-artifacts "):
            with self.subTest(value=value), patch.dict(os.environ, {SCREENSHOT_ENV: value}):
                with patch.object(Path, "mkdir") as mkdir:
                    expected = Path(value.strip()).expanduser().resolve()
                    self.assertEqual(browser_smoke._screenshot_directory(), expected)
                    self.assertEqual(
                        browser_smoke._screenshot_directory(Path("ignored-default")), expected
                    )
                mkdir.assert_not_called()

    def exercise_artifact_block(self, function_name: str, count: int, environment: str | None):
        page = SimpleNamespace(screenshot=AsyncMock(), locator=Mock())
        panel = SimpleNamespace(screenshot=AsyncMock())
        page.locator.return_value = panel
        runtime = SimpleNamespace(temp_dir=SimpleNamespace(name="owned-runtime"))
        wrapper = ast.parse("async def exercise():\n    pass\n")
        wrapper.body[0].body = artifact_statements(function_name, count)
        namespace = {
            **vars(browser_smoke),
            "page": page,
            "dashboard_page": page,
            "runtime": runtime,
        }
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(Path, "mkdir", autospec=True) as mkdir,
        ):
            if environment is not None:
                os.environ[SCREENSHOT_ENV] = environment
            exec(
                compile(ast.fix_missing_locations(wrapper), browser_smoke.__file__, "exec"),
                namespace,
            )
            asyncio.run(namespace["exercise"]())
        return page, panel, mkdir

    def test_timer_artifact_block_remains_opt_in(self) -> None:
        for value in (None, "", " \t ", " output/modernization/owned-artifacts "):
            with self.subTest(value=value):
                page, panel, mkdir = self.exercise_artifact_block(
                    "_exercise_card_modal_roundtrip", 2, value
                )
                page.screenshot.assert_not_awaited()
                if value and value.strip():
                    folder = Path(value.strip()).resolve()
                    mkdir.assert_called_once_with(folder, parents=True, exist_ok=True)
                    page.locator.assert_called_once_with(".signal-panel")
                    panel.screenshot.assert_awaited_once_with(
                        path=str(folder / "card-timer-running.png")
                    )
                else:
                    mkdir.assert_not_called()
                    page.locator.assert_not_called()
                    panel.screenshot.assert_not_awaited()

    def test_dashboard_artifact_block_respects_override_and_original_default(self) -> None:
        for value in (None, "", " \t ", " output/modernization/owned-artifacts "):
            with self.subTest(value=value):
                page, panel, mkdir = self.exercise_artifact_block(
                    "_exercise_display_dashboard", 3, value
                )
                folder = (
                    Path(value.strip()).resolve()
                    if value and value.strip()
                    else browser_smoke.ROOT / "output" / "playwright"
                )
                mkdir.assert_called_once_with(folder, parents=True, exist_ok=True)
                page.screenshot.assert_awaited_once_with(
                    path=str(folder / "tv-dashboard-1920x1080.png"), full_page=True
                )
                panel.screenshot.assert_not_awaited()

    def test_completion_artifact_block_respects_override_and_runtime_default(self) -> None:
        for value in (None, "", " \t ", " output/modernization/owned-artifacts "):
            with self.subTest(value=value):
                with patch.object(
                    browser_smoke,
                    "_screenshot_directory",
                    wraps=browser_smoke._screenshot_directory,
                ) as resolver:
                    page, panel, mkdir = self.exercise_artifact_block(
                        "_exercise_completion_act_editor", 2, value
                    )
                resolver.assert_called_once_with(Path("owned-runtime") / "playwright")
                folder = (
                    Path(value.strip()).resolve()
                    if value and value.strip()
                    else Path("owned-runtime") / "playwright"
                )
                mkdir.assert_called_once_with(folder, parents=True, exist_ok=True)
                page.screenshot.assert_not_awaited()
                panel.screenshot.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
