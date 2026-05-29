from __future__ import annotations

from importlib import resources

from ..printing.web_module import (
    PRINTING_WEB_MODULE_HTML,
    PRINTING_WEB_MODULE_SCRIPT,
    PRINTING_WEB_MODULE_STYLE,
)


def _read_source_chunk(name: str) -> str:
    return resources.files(__package__).joinpath("source", name).read_text(encoding="utf-8")


BOARD_WEB_APP_HTML = "".join(
    [
        _read_source_chunk("document_head_start.html"),
        _read_source_chunk("base_styles.css"),
        PRINTING_WEB_MODULE_STYLE,
        _read_source_chunk("post_printing_styles_and_body.html"),
        PRINTING_WEB_MODULE_HTML,
        _read_source_chunk("app_main_before_printing.js"),
        PRINTING_WEB_MODULE_SCRIPT,
        _read_source_chunk("app_main_after_printing.js"),
    ]
)
