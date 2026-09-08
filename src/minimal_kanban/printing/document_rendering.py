"""HTML shells and preview projections, independent of persistence/render backends."""

from __future__ import annotations

import html
import re
from typing import Any

from .defaults import PRINT_BASE_STYLES
from .models import PrintDocumentDefinition, PrintModuleSettings

PAGE_BREAK_MARKER = "<!-- AUTOSTOPCRM_PAGE_BREAK -->"


def preview_document_payload(
    rendered: dict[str, Any], *, settings: PrintModuleSettings
) -> dict[str, Any]:
    document = rendered["document"]
    preview_pages = _preview_pages(rendered["document_html"], document=document)
    return {
        "id": document.id,
        "label": document.label,
        "template": rendered["template"].to_dict(
            is_default=(settings.default_template_ids.get(document.id) == rendered["template"].id)
        ),
        "warnings": rendered["warnings"],
        "missing_fields": rendered["missing_fields"],
        "computed_totals": rendered["computed_totals"],
        "computed_items": rendered["computed_items"],
        "page_count": len(preview_pages),
        "pages": [
            {"number": index + 1, "html": page_html}
            for index, page_html in enumerate(preview_pages)
        ],
    }


def combined_document_html(payloads: list[dict[str, Any]]) -> str:
    bodies: list[str] = []
    for payload in payloads:
        body = _extract_document_shell_content(payload["document_html"]).replace(
            PAGE_BREAK_MARKER, ""
        )
        bodies.append(body)
    return wrap_document_html("\n".join(bodies), title="Печать документов AutoStop CRM")


def wrap_document_html(body_html: str, *, title: str) -> str:
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        f"<style>{PRINT_BASE_STYLES}</style>"
        '</head><body><div class="document-shell">'
        f"{body_html}"
        "</div></body></html>"
    )


def _extract_body(document_html: str) -> str:
    match = re.search(r"<body[^>]*>(.*)</body>", document_html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return document_html
    return match.group(1)


def _extract_document_shell_content(document_html: str) -> str:
    body = _extract_body(document_html)
    match = re.search(
        r'^\s*<div class="document-shell">(.*)</div>\s*$',
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else body


def _preview_pages(document_html: str, *, document: PrintDocumentDefinition) -> list[str]:
    body_html = _extract_document_shell_content(document_html)
    chunks = [chunk.strip() for chunk in body_html.split(PAGE_BREAK_MARKER) if chunk.strip()]
    if not chunks:
        chunks = [body_html]
    return [wrap_document_html(chunk, title=document.label) for chunk in chunks]
