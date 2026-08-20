from __future__ import annotations

import hashlib
from importlib import resources

from ..printing.web_module import (
    PRINTING_WEB_MODULE_HTML,
    PRINTING_WEB_MODULE_SCRIPT,
    PRINTING_WEB_MODULE_STYLE,
)


def _read_source_chunk(name: str) -> str:
    return resources.files(__package__).joinpath("source", name).read_text(encoding="utf-8")


BOARD_WEB_APP_CONTRACT_TEXT = "".join(
    [
        _read_source_chunk("document_head_start.html"),
        _read_source_chunk("base_styles.css"),
        PRINTING_WEB_MODULE_STYLE,
        _read_source_chunk("post_printing_styles_and_body.html"),
        PRINTING_WEB_MODULE_HTML,
        _read_source_chunk("app_main_before_printing.js"),
        _read_source_chunk("cashbox_transactions.js"),
        _read_source_chunk("cashbox_transfer.js"),
        _read_source_chunk("cash_journal.js"),
        PRINTING_WEB_MODULE_SCRIPT,
        _read_source_chunk("app_main_after_printing.js"),
    ]
)


def _split_board_document(document: str) -> tuple[str, str, str, str, str]:
    head, style_marker, after_style = document.partition("  <style>\n")
    styles, style_end_marker, after_styles = after_style.partition("  </style>")
    markup, script_marker, after_script = after_styles.partition("  <script>\n")
    script, script_end_marker, tail = after_script.rpartition("  </script>")
    if not all((style_marker, style_end_marker, script_marker, script_end_marker)):
        raise RuntimeError("Board web document markers are incomplete.")
    return head, styles, markup, script, tail


def _fingerprinted_path(kind: str, content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"/assets/board.{digest}.{kind}"


(
    _BOARD_HEAD,
    BOARD_WEB_APP_CSS,
    _BOARD_MARKUP,
    BOARD_WEB_APP_JS,
    _BOARD_TAIL,
) = _split_board_document(BOARD_WEB_APP_CONTRACT_TEXT)

BOARD_WEB_APP_CSS_PATH = _fingerprinted_path("css", BOARD_WEB_APP_CSS)
BOARD_WEB_APP_JS_PATH = _fingerprinted_path("js", BOARD_WEB_APP_JS)

BOARD_WEB_APP_HTML = "".join(
    [
        _BOARD_HEAD,
        f'  <link rel="stylesheet" href="{BOARD_WEB_APP_CSS_PATH}">\n',
        f'  <link rel="preload" as="script" href="{BOARD_WEB_APP_JS_PATH}">\n',
        _BOARD_MARKUP,
        f'  <script defer src="{BOARD_WEB_APP_JS_PATH}"></script>',
        _BOARD_TAIL,
    ]
)

DISPLAY_DASHBOARD_HTML = _read_source_chunk("display_dashboard.html")
