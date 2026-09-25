from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
NODE_CHECK_TIMEOUT_SECONDS = 30


class InlineScriptExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_inline_script = False
        self._chunks: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attr_map = {name.lower(): value for name, value in attrs}
        script_type = str(attr_map.get("type") or "").strip().lower()
        if "src" in attr_map or script_type not in {
            "",
            "application/javascript",
            "text/javascript",
            "module",
        }:
            return
        self._in_inline_script = True
        self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._in_inline_script:
            self._chunks.append(data)

    def _finish_inline_script(self) -> None:
        self.scripts.append("".join(self._chunks))
        self._chunks = []
        self._in_inline_script = False

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._in_inline_script:
            return
        self._finish_inline_script()

    def close(self) -> None:
        trailing_data = self.rawdata if self._in_inline_script else ""
        super().close()
        if self._in_inline_script:
            if trailing_data:
                self._chunks.append(trailing_data)
            self._finish_inline_script()


def _browser_javascript_sources() -> list[tuple[str, str]]:
    source_path = str(SRC)
    added_source_path = source_path not in sys.path
    if added_source_path:
        sys.path.insert(0, source_path)
    try:
        from minimal_kanban.web_assets import (
            BOARD_WEB_APP_JS,
            BOARD_WEB_APP_MODULES,
            DISPLAY_DASHBOARD_HTML,
            MODULE_MAP_HTML,
        )
    finally:
        if added_source_path:
            sys.path.remove(source_path)

    return (
        [("board_external", BOARD_WEB_APP_JS)]
        + [
            (f"board_module_{index}", source)
            for index, source in enumerate(BOARD_WEB_APP_MODULES.values())
        ]
        + [
            ("display_dashboard", script)
            for script in extract_inline_scripts(DISPLAY_DASHBOARD_HTML)
        ]
        + [("module_map", script) for script in extract_inline_scripts(MODULE_MAP_HTML)]
    )


def extract_inline_scripts(html: str) -> list[str]:
    parser = InlineScriptExtractor()
    parser.feed(html)
    parser.close()
    return [script for script in parser.scripts if script.strip()]


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("Node.js is required to validate generated browser JavaScript.", file=sys.stderr)
        return 1

    scripts = _browser_javascript_sources()
    if not scripts:
        print("No inline scripts found in browser HTML documents.", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="autostop-web-assets-js-") as temp_dir:
        temp_path = Path(temp_dir)
        for index, (document_name, script) in enumerate(scripts, start=1):
            script_path = temp_path / f"{document_name}_inline_script_{index}.js"
            script_path.write_text(script, encoding="utf-8")
            try:
                result = subprocess.run(
                    [node, "--check", str(script_path)],
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=NODE_CHECK_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                print(
                    f"Node.js syntax check timed out after {NODE_CHECK_TIMEOUT_SECONDS}s "
                    f"for {document_name}.",
                    file=sys.stderr,
                )
                return 1
            except OSError as exc:
                print(
                    f"Could not start Node.js syntax check for {document_name}: {exc}",
                    file=sys.stderr,
                )
                return 1
            if result.returncode != 0:
                if result.stdout:
                    print(result.stdout, end="")
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr)
                if not result.stdout and not result.stderr:
                    print(
                        f"Node.js syntax check failed for {document_name} "
                        f"(exit code {result.returncode}).",
                        file=sys.stderr,
                    )
                return result.returncode

    print(f"Generated browser JavaScript syntax check passed: {len(scripts)} script(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
