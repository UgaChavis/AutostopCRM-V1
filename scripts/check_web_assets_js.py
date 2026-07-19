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
        if attr_map.get("src"):
            return
        self._in_inline_script = True
        self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._in_inline_script:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._in_inline_script:
            return
        self.scripts.append("".join(self._chunks))
        self._chunks = []
        self._in_inline_script = False


def _browser_html_documents() -> dict[str, str]:
    sys.path.insert(0, str(SRC))
    from minimal_kanban.web_assets import BOARD_WEB_APP_HTML, DISPLAY_DASHBOARD_HTML

    return {
        "board": BOARD_WEB_APP_HTML,
        "display_dashboard": DISPLAY_DASHBOARD_HTML,
    }


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

    scripts = [
        (document_name, script)
        for document_name, html in _browser_html_documents().items()
        for script in extract_inline_scripts(html)
    ]
    if not scripts:
        print("No inline scripts found in browser HTML documents.", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="autostop-web-assets-js-") as temp_dir:
        temp_path = Path(temp_dir)
        for index, (document_name, script) in enumerate(scripts, start=1):
            script_path = temp_path / f"{document_name}_inline_script_{index}.js"
            script_path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(script_path)],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                check=False,
                timeout=NODE_CHECK_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                if result.stdout:
                    print(result.stdout, end="")
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr)
                return result.returncode

    print(f"Generated browser JavaScript syntax check passed: {len(scripts)} inline script(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
