"""Extract shipped browser functions, including their source-module dependencies."""

from __future__ import annotations

import re

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.web_app_assets.module_assets import read_board_source  # noqa: E402

_PERSIST_HELPERS = (
    "cardPersistPayloadKey",
    "cardPersistPayloadMatches",
    "cardPersistErrorIsDefinite",
    "cardPersistWrite",
    "adoptPersistedCard",
    "reconcileCardPersist",
)


def functions(file_name: str, *names: str) -> str:
    source = read_board_source(file_name)
    dependencies = list(names)
    if "persistCardPayload" in names:
        dependencies.extend(_PERSIST_HELPERS)
    if {"persistCardPayload", "saveCard", "ensureRepairOrderCard"}.intersection(names):
        dependencies.append("captureCardSaveWorkspace")
        if file_name != "app_main_before_printing.js":
            source += read_board_source("card_save_controls.js")
    chunks: list[str] = []
    for name in dict.fromkeys(dependencies):
        match = re.search(rf"^    (?:async )?function {name}\(.*?^    }}", source, re.M | re.S)
        if match is None:
            raise AssertionError(f"Missing assembled browser function {file_name}:{name}")
        chunks.append(match.group())
    return "\n".join(chunks)
