"""Cached diagram pages shared by GET and HEAD."""

from __future__ import annotations

import gzip
from functools import cache

from ..web_assets import (
    DISPLAY_DASHBOARD_HTML,
    MANAGER_STRUCTURE_HTML,
    MODULE_MAP_HTML,
    TELEGRAM_AGENT_BEHAVIOR_HTML,
)


@cache
def display_dashboard_html_bytes() -> bytes:
    return DISPLAY_DASHBOARD_HTML.encode("utf-8")


@cache
def display_dashboard_html_gzip_bytes() -> bytes:
    return gzip.compress(display_dashboard_html_bytes())


@cache
def infrastructure_html_bytes(page: bool | str) -> bytes:
    html = (
        MANAGER_STRUCTURE_HTML
        if page == "manager"
        else TELEGRAM_AGENT_BEHAVIOR_HTML
        if page
        else MODULE_MAP_HTML
    )
    return html.encode("utf-8")


@cache
def infrastructure_html_gzip_bytes(page: bool | str) -> bytes:
    return gzip.compress(infrastructure_html_bytes(page))


def module_map_html_gzip_bytes() -> bytes:
    return infrastructure_html_gzip_bytes(False)


def infrastructure_response(route: str, gzip_ok: bool) -> tuple[bytes, dict[str, str]]:
    page = (
        "manager"
        if route.startswith("/manager-structure")
        else route.startswith("/telegram-agent-behavior")
    )
    body = infrastructure_html_gzip_bytes(page) if gzip_ok else infrastructure_html_bytes(page)
    headers = {"Vary": "Accept-Encoding"}
    if gzip_ok:
        headers["Content-Encoding"] = "gzip"
    return body, headers
