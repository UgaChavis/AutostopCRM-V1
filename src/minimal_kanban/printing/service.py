from __future__ import annotations

import html
import json
import re
import uuid
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..models import Card, parse_datetime, utc_now_iso
from ..repair_order import RepairOrder, RepairOrderRow
from .defaults import BUILTIN_PRINT_DOCUMENTS, PRINT_BASE_STYLES, builtin_template_records
from .models import (
    SUPPORTED_PRINT_DOCUMENT_TYPES,
    PrintDocumentDefinition,
    PrintModuleSettings,
    PrintTemplateRecord,
)
from .pdf import PdfRenderError, render_html_to_pdf_bytes
from .printers import PrinterBackendError, list_printers, print_html
from .template_engine import TemplateRenderError, render_template


_SETTINGS_FILE_NAME = "settings.json"
_TEMPLATES_FILE_NAME = "templates.json"
_PAGE_BREAK_MARKER = "<!-- AUTOSTOPCRM_PAGE_BREAK -->"
_SENTENCE_SPLIT_RE = re.compile(r"[\n\r]+|(?<=[.!?])\s+")
_MONEY_QUANT = Decimal("0.01")


class PrintModuleError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def _normalize_text(value: Any, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _normalize_multiline(value: Any, *, limit: int = 120_000) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:limit]


def _normalize_document_type(value: Any) -> str:
    document_type = _normalize_text(value, limit=64)
    if document_type not in SUPPORTED_PRINT_DOCUMENT_TYPES:
        raise PrintModuleError(
            "validation_error",
            "Указан неподдерживаемый тип печатного документа.",
            details={"document_type": document_type},
        )
    return document_type


def _parse_decimal(value: Any) -> Decimal | None:
    raw = str(value or "").strip().replace(" ", "").replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _money_display(value: Any) -> str:
    parsed = _parse_decimal(value)
    if parsed is None:
        return "—"
    quantized = parsed.quantize(_MONEY_QUANT)
    text = format(quantized, "f")
    whole, dot, fraction = text.partition(".")
    grouped_whole = f"{int(whole):,}".replace(",", " ")
    return f"{grouped_whole},{fraction[:2]}" if dot else grouped_whole


def _line_breaks_html(value: Any, *, fallback: str = "—") -> str:
    text = _normalize_multiline(value, limit=20_000)
    if not text:
        return html.escape(fallback)
    return "<br>".join(html.escape(line) for line in text.split("\n"))


def _display(value: Any, *, fallback: str = "—", limit: int = 4000) -> str:
    text = _normalize_text(value, limit=limit)
    return text or fallback


def _date_display(value: Any, *, fallback: str = "—") -> str:
    text = _normalize_text(value, limit=64)
    if not text:
        return fallback
    parsed = parse_datetime(text)
    if parsed is None:
        return text
    return parsed.strftime("%d.%m.%Y %H:%M")


def _safe_json_read(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return deepcopy(default)


def _safe_json_write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _repair_row_dict(row: RepairOrderRow, *, section: str, index: int) -> dict[str, Any]:
    return {
        "index": index + 1,
        "section": section,
        "section_label": "Работы" if section == "works" else "Материалы",
        "name": row.name or "—",
        "quantity": row.quantity,
        "price": row.price,
        "total": row.total or row.computed_total(),
        "quantity_display": row.quantity or "—",
        "price_display": _money_display(row.price),
        "total_display": _money_display(row.total or row.computed_total()),
    }


class PrintModuleService:
    """Storage-backed printing module for repair-order documents."""

    def __init__(self, base_dir: Path) -> None:
        self._root_dir = Path(base_dir) / "printing"
        self._root_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path = self._root_dir / _SETTINGS_FILE_NAME
        self._templates_path = self._root_dir / _TEMPLATES_FILE_NAME
        self._builtin_documents = {item.id: item for item in BUILTIN_PRINT_DOCUMENTS}
        self._builtin_templates = {item.id: item for item in builtin_template_records()}

    def workspace(self, card: Card, *, repair_order: RepairOrder | None = None) -> dict[str, Any]:
        settings = self._read_settings()
        template_map = self._templates_by_document_type(settings=settings)
        printers = list_printers(default_name=settings.default_printer)
        return {
            "card_id": card.id,
            "heading": card.heading(),
            "documents": [
                self._document_workspace_payload(document, settings=settings, template_map=template_map)
                for document in BUILTIN_PRINT_DOCUMENTS
            ],
            "templates": {
                document_type: [record.to_dict(is_default=(settings.default_template_ids.get(document_type) == record.id)) for record in records]
                for document_type, records in template_map.items()
            },
            "printers": printers,
            "settings": settings.to_dict(),
            "meta": {
                "default_document_id": "repair_order",
                "supported_document_types": list(SUPPORTED_PRINT_DOCUMENT_TYPES),
                "has_printers": bool(printers),
                "has_repair_order_data": not (repair_order or card.repair_order).is_empty(),
            },
        }

    def preview_documents(
        self,
        card: Card,
        *,
        repair_order: RepairOrder | None = None,
        selected_document_ids: list[str] | None = None,
        active_document_id: str | None = None,
        selected_template_ids: dict[str, str] | None = None,
        template_overrides: dict[str, str] | None = None,
        print_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        order = repair_order or card.repair_order
        settings = self._merged_settings(print_settings)
        selected_ids = self._normalized_document_ids(selected_document_ids)
        resolved_active = self._resolved_active_document_id(active_document_id, selected_ids)
        documents_payload: list[dict[str, Any]] = []
        for document_id in selected_ids:
            document = self._document_definition(document_id)
            template = self._resolve_template(
                document_type=document_id,
                template_id=(selected_template_ids or {}).get(document_id, ""),
                settings=settings,
            )
            documents_payload.append(
                self._preview_document_payload(
                    card,
                    order,
                    document,
                    template,
                    settings=settings,
                    template_overrides=template_overrides,
                )
            )
        return {
            "card_id": card.id,
            "heading": card.heading(),
            "documents": documents_payload,
            "active_document_id": resolved_active,
            "meta": {
                "selected_count": len(selected_ids),
                "page_count": sum(document["page_count"] for document in documents_payload),
            },
        }

    def export_documents_pdf(
        self,
        card: Card,
        *,
        repair_order: RepairOrder | None = None,
        selected_document_ids: list[str] | None = None,
        selected_template_ids: dict[str, str] | None = None,
        template_overrides: dict[str, str] | None = None,
        print_settings: dict[str, Any] | None = None,
    ) -> tuple[bytes, str, dict[str, Any]]:
        order = repair_order or card.repair_order
        settings = self._merged_settings(print_settings)
        selected_ids = self._normalized_document_ids(selected_document_ids)
        document_payloads = [
            self._rendered_document_payload(
                card,
                order,
                self._document_definition(document_id),
                self._resolve_template(
                    document_type=document_id,
                    template_id=(selected_template_ids or {}).get(document_id, ""),
                    settings=settings,
                ),
                settings=settings,
                template_overrides=template_overrides,
            )
            for document_id in selected_ids
        ]
        combined_html = self._combined_document_html(document_payloads)
        try:
            pdf_bytes = render_html_to_pdf_bytes(
                combined_html,
                paper_size=settings.paper_size,
                orientation=settings.orientation,
                title=f"AutoStop CRM {card.heading()}",
            )
        except PdfRenderError as exc:
            raise PrintModuleError("pdf_error", str(exc), status_code=500) from exc
        file_name = self._build_export_file_name(card, selected_ids)
        return pdf_bytes, file_name, {
            "documents": [
                {
                    "id": payload["document"].id,
                    "label": payload["document"].label,
                    "template_id": payload["template"].id,
                    "template_name": payload["template"].name,
                }
                for payload in document_payloads
            ],
            "paper_size": settings.paper_size,
            "orientation": settings.orientation,
        }

    def print_documents(
        self,
        card: Card,
        *,
        repair_order: RepairOrder | None = None,
        selected_document_ids: list[str] | None = None,
        selected_template_ids: dict[str, str] | None = None,
        template_overrides: dict[str, str] | None = None,
        print_settings: dict[str, Any] | None = None,
        printer_name: str = "",
    ) -> dict[str, Any]:
        order = repair_order or card.repair_order
        settings = self._merged_settings(print_settings)
        requested_printer = _normalize_text(printer_name or settings.default_printer, limit=120)
        if not requested_printer:
            raise PrintModuleError("validation_error", "Не выбран принтер. Сначала выберите принтер или экспортируйте PDF.")
        selected_ids = self._normalized_document_ids(selected_document_ids)
        document_payloads = [
            self._rendered_document_payload(
                card,
                order,
                self._document_definition(document_id),
                self._resolve_template(
                    document_type=document_id,
                    template_id=(selected_template_ids or {}).get(document_id, ""),
                    settings=settings,
                ),
                settings=settings,
                template_overrides=template_overrides,
            )
            for document_id in selected_ids
        ]
        combined_html = self._combined_document_html(document_payloads)
        try:
            print_html(
                combined_html,
                printer_name=requested_printer,
                copies=settings.copies,
                paper_size=settings.paper_size,
                orientation=settings.orientation,
                title=f"AutoStop CRM {card.heading()}",
            )
        except PrinterBackendError as exc:
            raise PrintModuleError("printer_unavailable", str(exc), status_code=503) from exc
        return {
            "printer_name": requested_printer,
            "copies": settings.copies,
            "documents": [payload["document"].to_dict() for payload in document_payloads],
        }

    def save_template(
        self,
        *,
        document_type: str,
        name: str,
        content: str,
        template_id: str = "",
    ) -> dict[str, Any]:
        normalized_document_type = _normalize_document_type(document_type)
        normalized_name = _normalize_text(name, limit=120)
        normalized_content = _normalize_multiline(content, limit=200_000)
        if not normalized_name:
            raise PrintModuleError("validation_error", "Укажите название шаблона.")
        if not normalized_content:
            raise PrintModuleError("validation_error", "Шаблон не может быть пустым.")
        templates = self._read_custom_templates()
        existing = next((item for item in templates if item.id == template_id), None)
        now = utc_now_iso()
        if existing is not None:
            existing.name = normalized_name
            existing.content = normalized_content
            existing.updated_at = now
            record = existing
        else:
            record = PrintTemplateRecord(
                id=f"custom:{normalized_document_type}:{uuid.uuid4().hex}",
                document_type=normalized_document_type,
                name=normalized_name,
                content=normalized_content,
                created_at=now,
                updated_at=now,
                source="custom",
            )
            templates.append(record)
        self._write_custom_templates(templates)
        settings = self._read_settings()
        return {
            "template": record.to_dict(is_default=(settings.default_template_ids.get(normalized_document_type) == record.id)),
            "templates": self._template_payloads_for_document_type(normalized_document_type, settings=settings),
        }

    def duplicate_template(self, *, template_id: str, name: str = "") -> dict[str, Any]:
        source = self._find_template(template_id)
        now = utc_now_iso()
        duplicate = PrintTemplateRecord(
            id=f"custom:{source.document_type}:{uuid.uuid4().hex}",
            document_type=source.document_type,
            name=_normalize_text(name, limit=120) or f"{source.name} (копия)",
            content=source.content,
            created_at=now,
            updated_at=now,
            source="custom",
        )
        templates = self._read_custom_templates()
        templates.append(duplicate)
        self._write_custom_templates(templates)
        settings = self._read_settings()
        return {
            "template": duplicate.to_dict(is_default=False),
            "templates": self._template_payloads_for_document_type(source.document_type, settings=settings),
        }

    def delete_template(self, *, template_id: str) -> dict[str, Any]:
        record = self._find_template(template_id)
        if record.is_builtin:
            raise PrintModuleError("forbidden", "Встроенный шаблон нельзя удалить.", status_code=403)
        templates = [item for item in self._read_custom_templates() if item.id != template_id]
        self._write_custom_templates(templates)
        settings = self._read_settings()
        if settings.default_template_ids.get(record.document_type) == template_id:
            settings.default_template_ids.pop(record.document_type, None)
            self._write_settings(settings)
        return {
            "deleted": True,
            "document_type": record.document_type,
            "templates": self._template_payloads_for_document_type(record.document_type, settings=self._read_settings()),
        }

    def set_default_template(self, *, document_type: str, template_id: str) -> dict[str, Any]:
        normalized_document_type = _normalize_document_type(document_type)
        template = self._find_template(template_id)
        if template.document_type != normalized_document_type:
            raise PrintModuleError("validation_error", "Шаблон не соответствует выбранному типу документа.")
        settings = self._read_settings()
        settings.default_template_ids[normalized_document_type] = template.id
        self._write_settings(settings)
        return {
            "document_type": normalized_document_type,
            "template_id": template.id,
            "templates": self._template_payloads_for_document_type(normalized_document_type, settings=settings),
        }

    def save_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = self._merged_settings(payload or {})
        self._write_settings(settings)
        return {
            "settings": settings.to_dict(),
            "printers": list_printers(default_name=settings.default_printer),
        }

    def _document_workspace_payload(
        self,
        document: PrintDocumentDefinition,
        *,
        settings: PrintModuleSettings,
        template_map: dict[str, list[PrintTemplateRecord]],
    ) -> dict[str, Any]:
        selected_template = self._resolve_template(document_type=document.id, settings=settings)
        return {
            **document.to_dict(),
            "selected_template_id": selected_template.id,
            "selected_template_name": selected_template.name,
            "template_count": len(template_map.get(document.id, [])),
            "is_default_selected": document.id == "repair_order",
        }

    def _preview_document_payload(
        self,
        card: Card,
        order: RepairOrder,
        document: PrintDocumentDefinition,
        template: PrintTemplateRecord,
        *,
        settings: PrintModuleSettings,
        template_overrides: dict[str, str] | None,
    ) -> dict[str, Any]:
        rendered = self._rendered_document_payload(
            card,
            order,
            document,
            template,
            settings=settings,
            template_overrides=template_overrides,
        )
        preview_pages = self._preview_pages(rendered["document_html"], document=document)
        return {
            "id": document.id,
            "label": document.label,
            "template": rendered["template"].to_dict(
                is_default=(settings.default_template_ids.get(document.id) == rendered["template"].id)
            ),
            "warnings": rendered["warnings"],
            "missing_fields": rendered["missing_fields"],
            "page_count": len(preview_pages),
            "pages": [{"number": index + 1, "html": page_html} for index, page_html in enumerate(preview_pages)],
        }

    def _rendered_document_payload(
        self,
        card: Card,
        order: RepairOrder,
        document: PrintDocumentDefinition,
        template: PrintTemplateRecord,
        *,
        settings: PrintModuleSettings,
        template_overrides: dict[str, str] | None,
    ) -> dict[str, Any]:
        effective_template = template
        if template_overrides and document.id in template_overrides:
            effective_template = PrintTemplateRecord(
                id="preview:override",
                document_type=document.id,
                name="Предпросмотр шаблона",
                content=_normalize_multiline(template_overrides.get(document.id, ""), limit=200_000) or template.content,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                source="custom",
            )
        context = self._build_document_context(card, order, document=document, settings=settings)
        try:
            fragment = render_template(effective_template.content, context)
        except TemplateRenderError as exc:
            raise PrintModuleError("template_error", f"Шаблон поврежден: {exc}") from exc
        return {
            "document": document,
            "template": effective_template,
            "document_html": self._wrap_document_html(fragment, title=document.label),
            "warnings": context["meta"]["warnings"],
            "missing_fields": context["meta"]["missing_fields"],
        }

    def _combined_document_html(self, payloads: list[dict[str, Any]]) -> str:
        bodies: list[str] = []
        for index, payload in enumerate(payloads):
            bodies.append(self._extract_body(payload["document_html"]))
            if index != len(payloads) - 1:
                bodies.append('<div class="doc-page-break"></div>')
        return self._wrap_document_html("\n".join(bodies), title="Печать документов AutoStop CRM")

    def _wrap_document_html(self, body_html: str, *, title: str) -> str:
        return (
            "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{html.escape(title)}</title>"
            f"<style>{PRINT_BASE_STYLES}</style>"
            "</head><body><div class=\"document-shell\">"
            f"{body_html}"
            "</div></body></html>"
        )

    def _extract_body(self, document_html: str) -> str:
        match = re.search(r"<body[^>]*>(.*)</body>", document_html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return document_html
        return match.group(1)

    def _preview_pages(self, document_html: str, *, document: PrintDocumentDefinition) -> list[str]:
        body_html = self._extract_body(document_html)
        chunks = [chunk.strip() for chunk in body_html.split(_PAGE_BREAK_MARKER) if chunk.strip()]
        if not chunks:
            chunks = [body_html]
        return [self._wrap_document_html(chunk, title=document.label) for chunk in chunks]

    def _document_definition(self, document_id: str) -> PrintDocumentDefinition:
        normalized = _normalize_document_type(document_id)
        return self._builtin_documents[normalized]

    def _normalized_document_ids(self, value: list[str] | None) -> list[str]:
        normalized: list[str] = []
        for raw in value or ["repair_order"]:
            candidate = _normalize_text(raw, limit=64)
            if candidate in self._builtin_documents and candidate not in normalized:
                normalized.append(candidate)
        return normalized or ["repair_order"]

    def _resolved_active_document_id(self, active_document_id: str | None, selected_ids: list[str]) -> str:
        candidate = _normalize_text(active_document_id, limit=64)
        if candidate in selected_ids:
            return candidate
        return selected_ids[0]

    def _read_settings(self) -> PrintModuleSettings:
        raw = _safe_json_read(self._settings_path, default={})
        return PrintModuleSettings.from_dict(raw)

    def _write_settings(self, settings: PrintModuleSettings) -> None:
        _safe_json_write(self._settings_path, settings.to_dict())

    def _read_custom_templates(self) -> list[PrintTemplateRecord]:
        raw = _safe_json_read(self._templates_path, default=[])
        if not isinstance(raw, list):
            return []
        templates: list[PrintTemplateRecord] = []
        for item in raw:
            record = PrintTemplateRecord.from_dict(item)
            if record is not None and not record.is_builtin and record.document_type in SUPPORTED_PRINT_DOCUMENT_TYPES:
                templates.append(record)
        return templates

    def _write_custom_templates(self, records: list[PrintTemplateRecord]) -> None:
        payload = [record.to_dict() for record in records if not record.is_builtin]
        _safe_json_write(self._templates_path, payload)

    def _templates_by_document_type(self, *, settings: PrintModuleSettings) -> dict[str, list[PrintTemplateRecord]]:
        combined = list(self._builtin_templates.values()) + self._read_custom_templates()
        grouped: dict[str, list[PrintTemplateRecord]] = {document_type: [] for document_type in SUPPORTED_PRINT_DOCUMENT_TYPES}
        for record in combined:
            grouped.setdefault(record.document_type, []).append(record)
        for document_type, records in grouped.items():
            default_id = settings.default_template_ids.get(document_type, "")
            grouped[document_type] = sorted(
                records,
                key=lambda item: (
                    0 if item.id == default_id else 1,
                    0 if item.is_builtin else 1,
                    item.name.lower(),
                ),
            )
        return grouped

    def _resolve_template(
        self,
        *,
        document_type: str,
        template_id: str = "",
        settings: PrintModuleSettings,
    ) -> PrintTemplateRecord:
        grouped = self._templates_by_document_type(settings=settings)
        requested_id = _normalize_text(template_id, limit=128)
        if requested_id:
            for record in grouped.get(document_type, []):
                if record.id == requested_id:
                    return record
        default_id = settings.default_template_ids.get(document_type, "")
        if default_id:
            for record in grouped.get(document_type, []):
                if record.id == default_id:
                    return record
        for record in grouped.get(document_type, []):
            if record.id == self._builtin_documents[document_type].default_template_id:
                return record
        if grouped.get(document_type):
            return grouped[document_type][0]
        raise PrintModuleError(
            "not_found",
            "Шаблоны для документа не найдены.",
            status_code=404,
            details={"document_type": document_type},
        )

    def _find_template(self, template_id: str) -> PrintTemplateRecord:
        normalized = _normalize_text(template_id, limit=128)
        if normalized in self._builtin_templates:
            return self._builtin_templates[normalized]
        for record in self._read_custom_templates():
            if record.id == normalized:
                return record
        raise PrintModuleError("not_found", "Шаблон не найден.", status_code=404, details={"template_id": normalized})

    def _template_payloads_for_document_type(self, document_type: str, *, settings: PrintModuleSettings) -> list[dict[str, Any]]:
        return [
            record.to_dict(is_default=(settings.default_template_ids.get(document_type) == record.id))
            for record in self._templates_by_document_type(settings=settings).get(document_type, [])
        ]

    def _merged_settings(self, payload: dict[str, Any] | None) -> PrintModuleSettings:
        current = self._read_settings()
        if not isinstance(payload, dict) or not payload:
            return current
        merged = current.to_dict()
        service_profile = dict(current.service_profile.to_dict())
        if isinstance(payload.get("service_profile"), dict):
            for key, value in payload["service_profile"].items():
                service_profile[str(key)] = value
        merged.update({key: value for key, value in payload.items() if key != "service_profile"})
        merged["service_profile"] = service_profile
        return PrintModuleSettings.from_dict(merged)

    def _build_document_context(
        self,
        card: Card,
        order: RepairOrder,
        *,
        document: PrintDocumentDefinition,
        settings: PrintModuleSettings,
    ) -> dict[str, Any]:
        works = [_repair_row_dict(row, section="works", index=index) for index, row in enumerate(order.works)]
        materials = [_repair_row_dict(row, section="materials", index=index) for index, row in enumerate(order.materials)]
        line_items = works + materials
        findings = self._bullet_points(order.note, fallback_source=order.comment)
        recommendations = self._bullet_points(order.comment)
        issue_points = self._bullet_points(order.reason)
        missing_fields = self._missing_fields(card, order, works=works, materials=materials)
        warnings: list[str] = []
        if missing_fields:
            warnings.append("Часть полей не заполнена, проверьте документ перед печатью.")
        if not line_items:
            warnings.append("В документе нет работ и материалов.")
        return {
            "service": {
                **settings.service_profile.to_dict(),
                "company_name": _display(settings.service_profile.company_name),
                "legal_name": _display(settings.service_profile.legal_name, fallback=settings.service_profile.company_name or "—"),
                "address": _display(settings.service_profile.address),
                "phone": _display(settings.service_profile.phone),
                "email": _display(settings.service_profile.email),
                "inn": _display(settings.service_profile.inn),
                "kpp": _display(settings.service_profile.kpp),
                "ogrn": _display(settings.service_profile.ogrn),
                "bank_name": _display(settings.service_profile.bank_name),
                "bik": _display(settings.service_profile.bik),
                "settlement_account": _display(settings.service_profile.settlement_account),
                "correspondent_account": _display(settings.service_profile.correspondent_account),
                "tax_label": _display(settings.service_profile.tax_label),
            },
            "document": document.to_dict(),
            "card": {
                "id": card.id,
                "heading": card.heading(),
                "title": _display(card.title),
                "description": _display(card.description),
            },
            "repair_order": {
                **order.to_dict(),
                "number_display": _display(order.number),
                "date_display": _date_display(order.date),
                "opened_at_display": _date_display(order.opened_at),
                "closed_at_display": _date_display(order.closed_at),
                "status_label": "Закрыт" if str(order.status).strip().lower() == "closed" else "Открыт",
                "reason_display": _display(order.reason),
                "reason_html": _line_breaks_html(order.reason),
                "client_information_html": _line_breaks_html(order.comment),
                "note_display": _display(order.note),
            },
            "client": {
                "name": order.client,
                "phone": order.phone,
                "name_display": _display(order.client),
                "phone_display": _display(order.phone),
            },
            "vehicle": {
                "display_name": _display(order.vehicle or card.vehicle_display()),
                "license_plate": order.license_plate,
                "license_plate_display": _display(order.license_plate),
                "vin": order.vin,
                "vin_display": _display(order.vin),
                "mileage": order.mileage,
                "mileage_display": _display(order.mileage),
            },
            "dates": {
                "document_date_display": _date_display(order.date or order.opened_at),
                "opened_at_display": _date_display(order.opened_at),
                "closed_at_display": _date_display(order.closed_at),
                "generated_at_display": _date_display(utc_now_iso()),
            },
            "works": works,
            "materials": materials,
            "line_items": line_items,
            "issue_points": issue_points,
            "findings": findings,
            "recommendations": recommendations,
            "totals": {
                "works": order.works_total_amount(),
                "materials": order.materials_total_amount(),
                "grand": order.grand_total_amount(),
                "works_display": _money_display(order.works_total_amount()),
                "materials_display": _money_display(order.materials_total_amount()),
                "grand_display": _money_display(order.grand_total_amount()),
            },
            "meta": {
                "warnings": warnings,
                "missing_fields": missing_fields,
                "works_count": len(works),
                "materials_count": len(materials),
            },
        }

    def _missing_fields(
        self,
        card: Card,
        order: RepairOrder,
        *,
        works: list[dict[str, Any]],
        materials: list[dict[str, Any]],
    ) -> list[str]:
        missing: list[str] = []
        if not _normalize_text(order.client):
            missing.append("client")
        if not _normalize_text(order.phone):
            missing.append("phone")
        if not _normalize_text(order.vehicle or card.vehicle_display()):
            missing.append("vehicle")
        if not _normalize_text(order.vin):
            missing.append("vin")
        if not works:
            missing.append("works")
        if not materials:
            missing.append("materials")
        return missing

    def _bullet_points(self, value: Any, fallback_source: Any = "") -> list[dict[str, str]]:
        text = _normalize_multiline(value, limit=6000) or _normalize_multiline(fallback_source, limit=6000)
        if not text:
            return []
        points: list[dict[str, str]] = []
        seen: set[str] = set()
        for chunk in _SENTENCE_SPLIT_RE.split(text):
            cleaned = _normalize_text(chunk, limit=320)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            points.append({"text": cleaned})
            if len(points) >= 8:
                break
        return points

    def _build_export_file_name(self, card: Card, selected_document_ids: list[str]) -> str:
        doc_part = "-".join(selected_document_ids[:3]) if selected_document_ids else "print"
        number = _normalize_text(card.repair_order.number, limit=32) or "draft"
        return f"autostopcrm-{doc_part}-{number}.pdf"
