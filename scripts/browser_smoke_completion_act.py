from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from browser_smoke_runtime import TempRuntime
from browser_smoke_support import (
    _api_data,
    _close_card_modal_if_open,
    _is_modal_open,
    _wait_modal_closed,
    _wait_modal_open,
)


def _pdf_file_is_parseable(path: Path) -> bool:
    try:
        from PySide6.QtPdf import QPdfDocument
    except Exception:
        return False
    document = QPdfDocument()
    try:
        error = document.load(str(path))
        return bool(
            error == QPdfDocument.Error.None_
            and document.status() == QPdfDocument.Status.Ready
            and document.pageCount() > 0
        )
    finally:
        document.close()


def _pdfinfo_page_count(path: Path) -> int:
    try:
        result = subprocess.run(
            ["pdfinfo", str(path)],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if result.returncode != 0:
        return 0
    output = result.stdout.decode("utf-8", errors="replace")
    match = re.search(r"^Pages:\s*(\d+)\s*$", output, flags=re.MULTILINE)
    return int(match.group(1)) if match else 0


def _pdf_page_texts(path: Path, page_count: int) -> list[str]:
    pages: list[str] = []
    for page_number in range(1, page_count + 1):
        try:
            result = subprocess.run(
                [
                    "pdftotext",
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-layout",
                    str(path),
                    "-",
                ],
                check=False,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        pages.append(result.stdout.decode("utf-8", errors="replace").strip("\f\r\n "))
    return pages


def _completion_act_footer_sequence(
    page_texts: list[str],
    *,
    platform_name: str | None = None,
) -> list[tuple[int, int]]:
    effective_platform = os.name if platform_name is None else platform_name
    footer_sequence: list[tuple[int, int]] = []
    for page_text in page_texts:
        compact = re.sub(r"\s+", " ", page_text)
        matches = re.findall(r"страница\s+(\d+)\s+из\s+(\d+)", compact, flags=re.IGNORECASE)
        if len(matches) == 1:
            footer_sequence.append(tuple(int(value) for value in matches[0]))
            continue
        if matches or effective_platform != "nt":
            return []

        non_empty_lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        if not non_empty_lines:
            return []
        fallback = re.search(r"(\d+)\s+(\d+)\s*$", non_empty_lines[-1])
        if fallback is None:
            return []
        footer_sequence.append(tuple(int(value) for value in fallback.groups()))
    return footer_sequence


def _completion_act_pdf_contract(
    path: Path,
    *,
    expected_page_count: int,
    expected_item_names: list[str],
) -> bool:
    page_count = _pdfinfo_page_count(path)
    if page_count != expected_page_count:
        return False
    page_texts = _pdf_page_texts(path, page_count)
    if len(page_texts) != page_count or any(not text.strip() for text in page_texts):
        return False
    footer_sequence = _completion_act_footer_sequence(page_texts)
    if footer_sequence != [(page_number, page_count) for page_number in range(1, page_count + 1)]:
        return False
    combined_text = "\n".join(page_texts)
    return all(combined_text.count(name) == 1 for name in expected_item_names)


async def _completion_act_browser_print_html(page: Any, pages: list[dict[str, Any]]) -> str:
    return str(
        await page.evaluate(
            """(pages) => {
              const parser = new DOMParser();
              const parsedPages = (Array.isArray(pages) ? pages : []).map((page) => {
                const documentNode = parser.parseFromString(
                  String(page?.html || ''),
                  'text/html'
                );
                const shell = documentNode.querySelector('.document-shell');
                const bodyFragment = shell?.outerHTML || documentNode.body?.innerHTML || '';
                const headFragments = Array.from(
                  documentNode.head?.querySelectorAll('style, link[rel="stylesheet"]') || []
                ).map((node) => node.outerHTML).filter(Boolean);
                return { bodyFragment, headFragments };
              }).filter((page) => page.bodyFragment.trim());
              const headFragments = Array.from(
                new Set(parsedPages.flatMap((page) => page.headFragments))
              ).join('');
              const body = parsedPages.length
                ? parsedPages.map((page) => page.bodyFragment).join('')
                : '<main>Нет данных акта для печати.</main>';
              return '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
                + '<title>Акт выполненных работ</title>'
                + headFragments
                + '<style>.document-shell + .document-shell{break-before:page;page-break-before:always}</style>'
                + '</head><body>' + body + '</body></html>';
            }""",
            pages,
        )
    )


def _completion_act_pdf_fixture(
    base_form: dict[str, Any], *, label: str, maximal_final_block: bool, row_count: int = 26
) -> tuple[dict[str, Any], list[str]]:
    form = json.loads(json.dumps(base_form))
    item_names = [f"Smoke PDF {label} row {index:03d}" for index in range(1, row_count + 1)]
    form["document_number"] = f"SMOKE-PDF-{label.upper()}"
    form["items"] = [
        {
            "id": f"smoke-pdf-{label}-{index:02d}",
            "section": "works",
            "name": name,
            "unit": "ч",
            "quantity": "1",
            "price": str(100 + index),
        }
        for index, name in enumerate(item_names, start=1)
    ]
    if maximal_final_block:
        form["basis"] = ("Договор на выполнение работ и техническое обслуживание; " * 12)[:500]
        form["acceptance_text"] = (
            "Работы выполнены полностью и в срок, качество проверено заказчиком; " * 20
        )[:1000]
        for party_name in ("performer", "customer"):
            party = form.setdefault(party_name, {})
            party["legal_name"] = ("Организация с длинным полным наименованием " * 8)[:240]
            party["address"] = (
                "Красноярский край, город Красноярск, улица Длинная, дом 123, офис 456; " * 6
            )[:320]
            party["bank_name"] = ("Банк с длинным официальным наименованием " * 8)[:240]
            party["signer_position"] = ("Старший руководитель подразделения " * 5)[:120]
            party["signer_name"] = ("Иванов Иван Иванович " * 8)[:160]
    return form, item_names


async def _exercise_completion_act_physical_pdf_regression(
    page: Any, runtime: TempRuntime, artifact_dir: Path
) -> bool:
    current = runtime.service.get_completion_act_form({"card_id": runtime.card_id})
    base_form = current.get("form", {})
    if not isinstance(base_form, dict):
        return False
    cases = (("short", False, 3), ("long", False, 149), ("max-300", True, 300))
    for label, maximal_final_block, row_count in cases:
        form, item_names = _completion_act_pdf_fixture(
            base_form,
            label=label,
            maximal_final_block=maximal_final_block,
            row_count=row_count,
        )
        request_payload = {
            "card_id": runtime.card_id,
            "selected_document_ids": ["completion_act"],
            "active_document_id": "completion_act",
            "document_overrides": {"completion_act": form},
        }
        preview_response = await page.request.post(
            f"{runtime.base_url}/api/preview_repair_order_print_documents",
            data=request_payload,
            headers=runtime.auth_headers,
        )
        if not preview_response.ok:
            return False
        preview = _api_data(await preview_response.json())
        documents = preview.get("documents") if isinstance(preview, dict) else []
        document = documents[0] if isinstance(documents, list) and documents else {}
        pages = document.get("pages") if isinstance(document, dict) else []
        logical_page_count = int(document.get("page_count") or 0)
        if not isinstance(pages, list) or len(pages) != logical_page_count:
            return False
        if label == "long" and logical_page_count >= 7:
            return False
        if logical_page_count > 40:
            return False

        printable_html = await _completion_act_browser_print_html(page, pages)
        if printable_html.lower().count("<!doctype html>") != 1:
            return False
        chromium_path = artifact_dir / f"completion-act-{label}-chromium.pdf"
        pdf_page = await page.context.new_page()
        try:
            await pdf_page.emulate_media(media="print")
            await pdf_page.set_content(printable_html, wait_until="load")
            await pdf_page.evaluate("() => document.fonts?.ready")
            await pdf_page.pdf(
                path=str(chromium_path),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
            )
        finally:
            await pdf_page.close()

        export_response = await page.request.post(
            f"{runtime.base_url}/api/export_repair_order_print_pdf",
            data=request_payload,
            headers=runtime.auth_headers,
        )
        if not export_response.ok:
            return False
        exported = _api_data(await export_response.json())
        try:
            qt_pdf_bytes = base64.b64decode(exported.get("content_base64") or "", validate=True)
        except (TypeError, ValueError):
            return False
        qt_path = artifact_dir / f"completion-act-{label}-qt.pdf"
        qt_path.write_bytes(qt_pdf_bytes)

        if not (
            chromium_path.read_bytes().startswith(b"%PDF")
            and qt_pdf_bytes.startswith(b"%PDF")
            and _completion_act_pdf_contract(
                chromium_path,
                expected_page_count=logical_page_count,
                expected_item_names=item_names,
            )
            and _completion_act_pdf_contract(
                qt_path,
                expected_page_count=logical_page_count,
                expected_item_names=item_names,
            )
        ):
            return False
    return True


async def _arm_browser_print_capture(page: Any) -> None:
    await page.evaluate(
        """() => {
          window.__AUTOSTOP_SMOKE_MAIN_PRINT_HTML__ = '';
          window.__AUTOSTOP_SMOKE_MAIN_PRINT_OBSERVER__?.disconnect?.();
          const observer = new MutationObserver((records) => {
            records.flatMap((record) => Array.from(record.addedNodes || [])).forEach((node) => {
              if (!(node instanceof HTMLIFrameElement) || node.style.right !== '-12000px') return;
              const installPrintCapture = () => {
                if (!node.isConnected || !node.contentWindow || !node.contentDocument) return;
                node.contentWindow.print = () => {
                  const root = node.contentDocument?.documentElement;
                  window.__AUTOSTOP_SMOKE_MAIN_PRINT_HTML__ = root
                    ? '<!doctype html>' + root.outerHTML
                    : '';
                  observer.disconnect();
                };
              };
              installPrintCapture();
              const timer = window.setInterval(installPrintCapture, 10);
              window.setTimeout(() => window.clearInterval(timer), 1200);
            });
          });
          observer.observe(document.body, { childList: true });
          window.__AUTOSTOP_SMOKE_MAIN_PRINT_OBSERVER__ = observer;
        }"""
    )


async def _capture_browser_print_html(page: Any, button_selector: str) -> str:
    await _arm_browser_print_capture(page)
    await page.click(button_selector)
    await page.wait_for_function(
        "() => Boolean(window.__AUTOSTOP_SMOKE_MAIN_PRINT_HTML__)", timeout=20_000
    )
    await page.wait_for_function(
        "(selector) => !document.querySelector(selector)?.disabled",
        arg=button_selector,
    )
    return str(await page.evaluate("() => window.__AUTOSTOP_SMOKE_MAIN_PRINT_HTML__"))


async def _exercise_completion_act_editor_print_export(
    page: Any, artifact_dir: Path
) -> tuple[bool, bool]:
    async def delay_editor_preview(route: Any) -> None:
        await asyncio.sleep(0.45)
        await route.continue_()

    await page.click('[data-completion-act-section="document"]')
    editor_print_marker = "SMOKE-EDITOR-PRINT-FRESH"
    await page.route("**/api/preview_repair_order_print_documents", delay_editor_preview)
    try:
        await page.fill("#completionActDocumentNumber", editor_print_marker)
        editor_print_html = await _capture_browser_print_html(page, "#completionActPrintButton")
    finally:
        await page.unroute("**/api/preview_repair_order_print_documents", delay_editor_preview)
    editor_print_logical_pages = editor_print_html.count('class="document-shell"')
    editor_print_path = artifact_dir / "completion-act-editor-immediate-print.pdf"
    editor_print_page = await page.context.new_page()
    try:
        await editor_print_page.emulate_media(media="print")
        await editor_print_page.set_content(editor_print_html, wait_until="load")
        await editor_print_page.evaluate("() => document.fonts?.ready")
        await editor_print_page.pdf(
            path=str(editor_print_path),
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
        )
    finally:
        await editor_print_page.close()
    editor_print_page_count = _pdfinfo_page_count(editor_print_path)
    editor_print_text = "\n".join(_pdf_page_texts(editor_print_path, editor_print_page_count))
    editor_print_race_ok = bool(
        editor_print_html.lower().count("<!doctype html>") == 1
        and editor_print_logical_pages > 0
        and editor_print_marker in editor_print_html
        and "SMOKE-ACT-1" not in editor_print_html
        and editor_print_marker in editor_print_text
        and _completion_act_pdf_contract(
            editor_print_path,
            expected_page_count=editor_print_logical_pages,
            expected_item_names=["Smoke completion work"],
        )
    )

    editor_export_marker = "SMOKE-EDITOR-EXPORT-FRESH"
    await page.route("**/api/preview_repair_order_print_documents", delay_editor_preview)
    try:
        await page.fill("#completionActDocumentNumber", editor_export_marker)
        async with page.expect_download() as editor_export_info:
            await page.click("#completionActExportButton")
        editor_export = await editor_export_info.value
    finally:
        await page.unroute("**/api/preview_repair_order_print_documents", delay_editor_preview)
    editor_export_path = artifact_dir / "completion-act-editor-immediate-export.pdf"
    await editor_export.save_as(str(editor_export_path))
    editor_export_page_count = _pdfinfo_page_count(editor_export_path)
    editor_export_text = "\n".join(_pdf_page_texts(editor_export_path, editor_export_page_count))
    editor_export_race_ok = bool(
        editor_export.suggested_filename.lower().endswith(".pdf")
        and editor_export_path.read_bytes().startswith(b"%PDF")
        and _pdf_file_is_parseable(editor_export_path)
        and editor_export_page_count == editor_print_logical_pages
        and editor_export_marker in editor_export_text
        and editor_print_marker not in editor_export_text
        and _completion_act_pdf_contract(
            editor_export_path,
            expected_page_count=editor_print_logical_pages,
            expected_item_names=["Smoke completion work"],
        )
    )
    return editor_print_race_ok, editor_export_race_ok


async def _exercise_completion_act_main_print_regression(
    page: Any, runtime: TempRuntime, artifact_dir: Path
) -> bool:
    current = runtime.service.get_completion_act_form({"card_id": runtime.card_id})
    base_form = current.get("form", {})
    if not isinstance(base_form, dict):
        return False
    form, item_names = _completion_act_pdf_fixture(
        base_form, label="main-button", maximal_final_block=False
    )
    saved = runtime.service.save_completion_act_form(
        {
            "card_id": runtime.card_id,
            "form": form,
            "expected_version": int((current.get("draft") or {}).get("version") or 0),
            "expected_source_fingerprint": str(
                (current.get("draft") or {}).get("current_source_fingerprint") or ""
            ),
            "idempotency_key": "browser-smoke-main-print-save",
            "actor_name": "SMOKE",
            "source": "browser_smoke",
        }
    )
    draft_version = int((saved.get("draft") or {}).get("version") or 0)
    try:
        async with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.split("?", 1)[0].endswith(
                    "/api/preview_repair_order_print_documents"
                )
            )
        ) as preview_response_info:
            await page.click('[data-print-document="completion_act"]')
        preview_response = await preview_response_info.value
        preview = _api_data(await preview_response.json())
        documents = preview.get("documents") if isinstance(preview, dict) else []
        document = documents[0] if isinstance(documents, list) and documents else {}
        logical_page_count = int(document.get("page_count") or 0)
        if logical_page_count != 2:
            return False

        gear = page.locator("[data-completion-act-editor-open]")
        async with (
            page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.split("?", 1)[0].endswith("/api/get_completion_act_form")
                )
            ),
            page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.split("?", 1)[0].endswith(
                        "/api/preview_repair_order_print_documents"
                    )
                )
            ),
        ):
            await gear.click()
        await _wait_modal_open(page, "#completionActEditorModal")
        discarded_marker = "SMOKE-UNSAVED-DISCARD"
        async with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.split("?", 1)[0].endswith(
                    "/api/preview_repair_order_print_documents"
                )
            )
        ):
            await page.fill("#completionActDocumentNumber", discarded_marker)
        await page.wait_for_function(
            """(marker) => {
              const frame = document.querySelector('#completionActPreviewFrame');
              return (frame?.contentDocument?.body?.innerText || '').includes(marker);
            }""",
            arg=discarded_marker,
        )

        async def delay_preview_response(route: Any) -> None:
            await asyncio.sleep(0.45)
            await route.continue_()

        await page.route("**/api/preview_repair_order_print_documents", delay_preview_response)
        try:
            page.once("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
            await page.click("#completionActEditorCloseX")
            await _wait_modal_closed(page, "#completionActEditorModal")
            printable_html = await _capture_browser_print_html(page, "#repairOrderPrintRunButton")
        finally:
            await page.unroute(
                "**/api/preview_repair_order_print_documents", delay_preview_response
            )
        if (
            printable_html.lower().count("<!doctype html>") != 1
            or printable_html.count('class="document-shell"') != logical_page_count
            or discarded_marker in printable_html
            or form["document_number"] not in printable_html
        ):
            return False
        chromium_path = artifact_dir / "completion-act-main-button-chromium.pdf"
        pdf_page = await page.context.new_page()
        try:
            await pdf_page.emulate_media(media="print")
            await pdf_page.set_content(printable_html, wait_until="load")
            await pdf_page.evaluate("() => document.fonts?.ready")
            await pdf_page.pdf(
                path=str(chromium_path),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
            )
        finally:
            await pdf_page.close()
        return _completion_act_pdf_contract(
            chromium_path,
            expected_page_count=logical_page_count,
            expected_item_names=item_names,
        )
    finally:
        if draft_version:
            runtime.service.reset_completion_act_form(
                {
                    "card_id": runtime.card_id,
                    "expected_version": draft_version,
                    "idempotency_key": "browser-smoke-main-print-reset",
                    "actor_name": "SMOKE",
                    "source": "browser_smoke",
                }
            )


async def _exercise_completion_act_max_items_ui(page: Any, runtime: TempRuntime, gear: Any) -> bool:
    current = runtime.service.get_completion_act_form({"card_id": runtime.card_id})
    base_form = current.get("form", {})
    if not isinstance(base_form, dict):
        return False
    form = json.loads(json.dumps(base_form))
    form["items"] = [
        {
            "id": f"smoke-ui-max-{index:03d}",
            "section": "works" if index <= 150 else "materials",
            "name": f"Smoke UI maximum row {index:03d}",
            "unit": "ч" if index <= 150 else "шт",
            "quantity": "1",
            "price": "1",
        }
        for index in range(1, 301)
    ]
    saved = runtime.service.save_completion_act_form(
        {
            "card_id": runtime.card_id,
            "form": form,
            "expected_version": int((current.get("draft") or {}).get("version") or 0),
            "expected_source_fingerprint": str(
                (current.get("draft") or {}).get("current_source_fingerprint") or ""
            ),
            "idempotency_key": "browser-smoke-ui-max-save",
            "actor_name": "SMOKE",
            "source": "browser_smoke",
        }
    )
    draft_version = int((saved.get("draft") or {}).get("version") or 0)
    try:
        async with (
            page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.split("?", 1)[0].endswith("/api/get_completion_act_form")
                )
            ),
            page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.split("?", 1)[0].endswith(
                        "/api/preview_repair_order_print_documents"
                    )
                )
            ),
        ):
            await gear.click()
        await _wait_modal_open(page, "#completionActEditorModal")
        await page.wait_for_function(
            """() => (
              document.querySelectorAll('#completionActItemRows [data-completion-act-item]').length === 300 &&
              document.querySelector('#completionActAddItemButton')?.disabled === true
            )"""
        )
        names = page.locator('#completionActItemRows [data-completion-act-item-field="name"]')
        rows_preserved = bool(
            await names.count() == 300
            and await names.first.input_value() == "Smoke UI maximum row 001"
            and await names.last.input_value() == "Smoke UI maximum row 300"
        )
        await page.click("#completionActEditorCloseX")
        await _wait_modal_closed(page, "#completionActEditorModal")
        return rows_preserved
    finally:
        if await _is_modal_open(page, "#completionActEditorModal"):
            await page.click("#completionActEditorCloseX")
            await _wait_modal_closed(page, "#completionActEditorModal")
        if draft_version:
            runtime.service.reset_completion_act_form(
                {
                    "card_id": runtime.card_id,
                    "expected_version": draft_version,
                    "idempotency_key": "browser-smoke-ui-max-reset",
                    "actor_name": "SMOKE",
                    "source": "browser_smoke",
                }
            )


async def _exercise_completion_act_cross_card_race(page: Any, runtime: TempRuntime) -> bool:
    card_a_id = runtime.card_id
    card_b_id = runtime.extra_column_card_id
    card_a_marker = "SMOKE-CARD-A-ACT"
    card_b_marker = "SMOKE-CARD-B-ACT"
    card_b_edit_marker = "SMOKE-CARD-B-EDIT"
    card_a_customer = "Smoke requisites card A only"
    card_b_customer = "Smoke requisites card B only"
    card_a_before = runtime.service.get_completion_act_form({"card_id": card_a_id})
    card_b_before = runtime.service.get_completion_act_form({"card_id": card_b_id})
    if card_a_before.get("draft", {}).get("exists") or card_b_before.get("draft", {}).get("exists"):
        return False

    def marked_form(source: dict[str, Any], *, marker: str, customer_marker: str) -> dict[str, Any]:
        form = json.loads(json.dumps(source.get("form") or {}))
        form["document_number"] = marker
        customer = form.setdefault("customer", {})
        customer["legal_name"] = customer_marker
        customer["address"] = f"{customer_marker}, address"
        customer["inn"] = "2400000000" if marker == card_a_marker else "2400000001"
        form["items"] = [
            {
                "id": f"{marker.lower()}-row",
                "section": "works",
                "name": f"{marker} work",
                "unit": "ч",
                "quantity": "1",
                "price": "100",
            }
        ]
        return form

    saved_a = runtime.service.save_completion_act_form(
        {
            "card_id": card_a_id,
            "form": marked_form(
                card_a_before, marker=card_a_marker, customer_marker=card_a_customer
            ),
            "expected_version": int((card_a_before.get("draft") or {}).get("version") or 0),
            "expected_source_fingerprint": str(
                (card_a_before.get("draft") or {}).get("current_source_fingerprint") or ""
            ),
            "idempotency_key": "browser-smoke-cross-card-a-save",
            "actor_name": "SMOKE",
            "source": "browser_smoke",
        }
    )
    saved_b = runtime.service.save_completion_act_form(
        {
            "card_id": card_b_id,
            "form": marked_form(
                card_b_before, marker=card_b_marker, customer_marker=card_b_customer
            ),
            "expected_version": int((card_b_before.get("draft") or {}).get("version") or 0),
            "expected_source_fingerprint": str(
                (card_b_before.get("draft") or {}).get("current_source_fingerprint") or ""
            ),
            "idempotency_key": "browser-smoke-cross-card-b-save",
            "actor_name": "SMOKE",
            "source": "browser_smoke",
        }
    )

    def completion_request_card_id(request: Any) -> str:
        try:
            payload = request.post_data_json
        except (TypeError, ValueError):
            return ""
        return str(payload.get("card_id") or "") if isinstance(payload, dict) else ""

    def completion_get_response_for(response: Any, card_id: str) -> bool:
        return bool(
            response.request.method == "POST"
            and response.url.split("?", 1)[0].endswith("/api/get_completion_act_form")
            and completion_request_card_id(response.request) == card_id
        )

    async def delay_card_a_completion_get(route: Any) -> None:
        if completion_request_card_id(route.request) == card_a_id:
            await asyncio.sleep(2.5)
        await route.continue_()

    async def close_repair_order_and_card() -> None:
        if await _is_modal_open(page, "#repairOrderModal"):
            await page.click('[data-close="repair-order"]')
            await _wait_modal_closed(page, "#repairOrderModal")
        await _close_card_modal_if_open(page)

    async def open_card_repair_order(card_id: str) -> None:
        selector = f'.card[data-card-id="{card_id}"]:not([data-virtual-card="true"])'
        await page.wait_for_selector(selector)
        await page.click(selector)
        await _wait_modal_open(page, "#cardModal")
        await page.wait_for_function(
            "() => !document.querySelector('#repairOrderButton')?.disabled"
        )
        await page.click("#repairOrderButton")
        await _wait_modal_open(page, "#repairOrderModal")

    await page.route("**/api/get_completion_act_form", delay_card_a_completion_get)
    cross_card_ok = False
    try:
        await page.click("#repairOrderPrintButton")
        await _wait_modal_open(page, "#repairOrderPrintModal")
        gear = page.locator("[data-completion-act-editor-open]")
        async with page.expect_response(
            lambda response: completion_get_response_for(response, card_a_id)
        ) as late_card_a_response_info:
            await gear.click()
            await _wait_modal_open(page, "#completionActEditorModal")
            await page.click("#completionActEditorCloseX")
            await _wait_modal_closed(page, "#completionActEditorModal")
            await page.click("#repairOrderPrintCloseX")
            await _wait_modal_closed(page, "#repairOrderPrintModal")
            await close_repair_order_and_card()

            await open_card_repair_order(card_b_id)
            await page.click("#repairOrderPrintButton")
            await _wait_modal_open(page, "#repairOrderPrintModal")
            async with page.expect_response(
                lambda response: completion_get_response_for(response, card_b_id)
            ):
                await page.click("[data-completion-act-editor-open]")
            await _wait_modal_open(page, "#completionActEditorModal")
            await page.wait_for_function(
                "(marker) => document.querySelector('#completionActDocumentNumber')?.value === marker",
                arg=card_b_marker,
            )
        late_card_a_response = await late_card_a_response_info.value
        await late_card_a_response.body()
        await page.wait_for_timeout(100)

        card_b_form_survived = bool(
            await page.input_value("#completionActDocumentNumber") == card_b_marker
            and await page.input_value("#completionActCustomerLegalName") == card_b_customer
            and card_a_customer not in await page.input_value("#completionActCustomerLegalName")
            and card_a_marker not in await page.input_value("#completionActDocumentNumber")
        )
        await page.fill("#completionActDocumentNumber", card_b_edit_marker)
        async with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.split("?", 1)[0].endswith("/api/save_completion_act_form")
            )
        ) as card_b_save_response_info:
            await page.click("#completionActSaveButton")
        card_b_save_response = await card_b_save_response_info.value
        card_b_save_card_id = completion_request_card_id(card_b_save_response.request)
        await page.wait_for_function(
            "() => !document.querySelector('#completionActSaveButton')?.disabled"
        )
        card_a_after = runtime.service.get_completion_act_form({"card_id": card_a_id})
        card_b_after = runtime.service.get_completion_act_form({"card_id": card_b_id})
        card_b_print_html = await _capture_browser_print_html(page, "#completionActPrintButton")
        cross_card_ok = bool(
            card_b_form_survived
            and card_b_save_card_id == card_b_id
            and str((card_a_after.get("form") or {}).get("document_number") or "") == card_a_marker
            and str((card_b_after.get("form") or {}).get("document_number") or "")
            == card_b_edit_marker
            and card_b_edit_marker in card_b_print_html
            and card_b_customer in card_b_print_html
            and card_a_marker not in card_b_print_html
            and card_a_customer not in card_b_print_html
        )

        await page.click("#completionActEditorCloseX")
        await _wait_modal_closed(page, "#completionActEditorModal")
        await page.click("#repairOrderPrintCloseX")
        await _wait_modal_closed(page, "#repairOrderPrintModal")
        await close_repair_order_and_card()
        await open_card_repair_order(card_a_id)
    finally:
        await page.unroute("**/api/get_completion_act_form", delay_card_a_completion_get)
        for card_id, key in (
            (card_a_id, "browser-smoke-cross-card-a-reset"),
            (card_b_id, "browser-smoke-cross-card-b-reset"),
        ):
            current = runtime.service.get_completion_act_form({"card_id": card_id})
            version = int((current.get("draft") or {}).get("version") or 0)
            if version:
                runtime.service.reset_completion_act_form(
                    {
                        "card_id": card_id,
                        "expected_version": version,
                        "idempotency_key": key,
                        "actor_name": "SMOKE",
                        "source": "browser_smoke",
                    }
                )
    return bool(
        cross_card_ok
        and int((saved_a.get("draft") or {}).get("version") or 0) > 0
        and int((saved_b.get("draft") or {}).get("version") or 0) > 0
    )
