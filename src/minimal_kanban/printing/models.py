from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any


SUPPORTED_PRINT_DOCUMENT_TYPES = (
    "repair_order",
    "invoice",
    "invoice_factura",
    "inspection_sheet",
    "completion_act",
)


def _clean_text(value: Any, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _clean_multiline(value: Any, *, limit: int = 120_000) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:limit]


def _clean_table_rows(value: Any, *, limit_rows: int = 80) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            name = _clean_text(item.get("name", ""), limit=240)
            quantity = _clean_text(item.get("quantity", ""), limit=40)
        else:
            name = _clean_text(item, limit=240)
            quantity = ""
        if not name and not quantity:
            continue
        rows.append({"name": name, "quantity": quantity})
        if len(rows) >= limit_rows:
            break
    return rows


@dataclass(slots=True)
class PrintServiceProfile:
    company_name: str = "AutoStop CRM"
    legal_name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    inn: str = ""
    kpp: str = ""
    ogrn: str = ""
    bank_name: str = ""
    bik: str = ""
    settlement_account: str = ""
    correspondent_account: str = ""
    tax_label: str = "Без НДС"

    def __post_init__(self) -> None:
        self.company_name = _clean_text(self.company_name, limit=120) or "AutoStop CRM"
        self.legal_name = _clean_text(self.legal_name, limit=160)
        self.address = _clean_text(self.address, limit=240)
        self.phone = _clean_text(self.phone, limit=80)
        self.email = _clean_text(self.email, limit=120)
        self.inn = _clean_text(self.inn, limit=32)
        self.kpp = _clean_text(self.kpp, limit=32)
        self.ogrn = _clean_text(self.ogrn, limit=32)
        self.bank_name = _clean_text(self.bank_name, limit=160)
        self.bik = _clean_text(self.bik, limit=32)
        self.settlement_account = _clean_text(self.settlement_account, limit=64)
        self.correspondent_account = _clean_text(self.correspondent_account, limit=64)
        self.tax_label = _clean_text(self.tax_label, limit=48) or "Без НДС"

    def to_dict(self) -> dict[str, str]:
        return {
            "company_name": self.company_name,
            "legal_name": self.legal_name,
            "address": self.address,
            "phone": self.phone,
            "email": self.email,
            "inn": self.inn,
            "kpp": self.kpp,
            "ogrn": self.ogrn,
            "bank_name": self.bank_name,
            "bik": self.bik,
            "settlement_account": self.settlement_account,
            "correspondent_account": self.correspondent_account,
            "tax_label": self.tax_label,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "PrintServiceProfile":
        if not isinstance(payload, dict):
            return cls()
        values = {item.name: payload.get(item.name, "") for item in fields(cls)}
        return cls(**values)


@dataclass(slots=True)
class PrintModuleSettings:
    default_printer: str = ""
    copies: int = 1
    paper_size: str = "A4"
    orientation: str = "portrait"
    default_template_ids: dict[str, str] = field(default_factory=dict)
    service_profile: PrintServiceProfile = field(default_factory=PrintServiceProfile)

    def __post_init__(self) -> None:
        self.default_printer = _clean_text(self.default_printer, limit=120)
        try:
            self.copies = max(1, min(20, int(self.copies)))
        except (TypeError, ValueError):
            self.copies = 1
        self.paper_size = _clean_text(self.paper_size, limit=20).upper() or "A4"
        orientation = _clean_text(self.orientation, limit=20).lower()
        self.orientation = "landscape" if orientation == "landscape" else "portrait"
        normalized_defaults: dict[str, str] = {}
        if isinstance(self.default_template_ids, dict):
            for key, value in self.default_template_ids.items():
                document_type = _clean_text(key, limit=64)
                template_id = _clean_text(value, limit=128)
                if document_type and template_id:
                    normalized_defaults[document_type] = template_id
        self.default_template_ids = normalized_defaults
        if not isinstance(self.service_profile, PrintServiceProfile):
            self.service_profile = PrintServiceProfile.from_dict(self.service_profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_printer": self.default_printer,
            "copies": self.copies,
            "paper_size": self.paper_size,
            "orientation": self.orientation,
            "default_template_ids": dict(self.default_template_ids),
            "service_profile": self.service_profile.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "PrintModuleSettings":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            default_printer=payload.get("default_printer", ""),
            copies=payload.get("copies", 1),
            paper_size=payload.get("paper_size", "A4"),
            orientation=payload.get("orientation", "portrait"),
            default_template_ids=payload.get("default_template_ids", {}),
            service_profile=PrintServiceProfile.from_dict(payload.get("service_profile", {})),
        )


@dataclass(slots=True)
class PrintTemplateRecord:
    id: str
    document_type: str
    name: str
    content: str
    created_at: str
    updated_at: str
    source: str = "custom"

    def __post_init__(self) -> None:
        self.id = _clean_text(self.id, limit=128)
        self.document_type = _clean_text(self.document_type, limit=64)
        self.name = _clean_text(self.name, limit=120)
        self.content = _clean_multiline(self.content, limit=200_000)
        self.created_at = _clean_text(self.created_at, limit=48)
        self.updated_at = _clean_text(self.updated_at, limit=48)
        self.source = _clean_text(self.source, limit=32).lower() or "custom"

    @property
    def is_builtin(self) -> bool:
        return self.source == "builtin"

    def to_dict(self, *, is_default: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "document_type": self.document_type,
            "name": self.name,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source,
            "is_builtin": self.is_builtin,
            "is_default": bool(is_default),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "PrintTemplateRecord | None":
        if not isinstance(payload, dict):
            return None
        record = cls(
            id=payload.get("id", ""),
            document_type=payload.get("document_type", ""),
            name=payload.get("name", ""),
            content=payload.get("content", ""),
            created_at=payload.get("created_at", ""),
            updated_at=payload.get("updated_at", ""),
            source=payload.get("source", "custom"),
        )
        if not record.id or not record.document_type or not record.name or not record.content:
            return None
        return record


@dataclass(slots=True)
class InspectionSheetFormData:
    client: str = ""
    vehicle: str = ""
    vin_or_plate: str = ""
    complaint_summary: str = ""
    findings: str = ""
    recommendations: str = ""
    planned_works: str = ""
    planned_materials: str = ""
    planned_work_rows: list[dict[str, str]] = field(default_factory=list)
    planned_material_rows: list[dict[str, str]] = field(default_factory=list)
    master_comment: str = ""
    updated_at: str = ""
    filled_by: str = ""
    source: str = "manual"

    def __post_init__(self) -> None:
        self.client = _clean_text(self.client, limit=200)
        self.vehicle = _clean_text(self.vehicle, limit=200)
        self.vin_or_plate = _clean_text(self.vin_or_plate, limit=200)
        self.complaint_summary = _clean_multiline(self.complaint_summary, limit=16_000)
        self.findings = _clean_multiline(self.findings, limit=24_000)
        self.recommendations = _clean_multiline(self.recommendations, limit=24_000)
        self.planned_works = _clean_multiline(self.planned_works, limit=24_000)
        self.planned_materials = _clean_multiline(self.planned_materials, limit=24_000)
        self.planned_work_rows = _clean_table_rows(self.planned_work_rows)
        self.planned_material_rows = _clean_table_rows(self.planned_material_rows)
        self.master_comment = _clean_multiline(self.master_comment, limit=16_000)
        self.updated_at = _clean_text(self.updated_at, limit=48)
        self.filled_by = _clean_text(self.filled_by, limit=120)
        self.source = _clean_text(self.source, limit=24).lower() or "manual"

    def to_dict(self) -> dict[str, str]:
        return {
            "client": self.client,
            "vehicle": self.vehicle,
            "vin_or_plate": self.vin_or_plate,
            "complaint_summary": self.complaint_summary,
            "findings": self.findings,
            "recommendations": self.recommendations,
            "planned_works": self.planned_works,
            "planned_materials": self.planned_materials,
            "planned_work_rows": [dict(item) for item in self.planned_work_rows],
            "planned_material_rows": [dict(item) for item in self.planned_material_rows],
            "master_comment": self.master_comment,
            "updated_at": self.updated_at,
            "filled_by": self.filled_by,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "InspectionSheetFormData":
        if not isinstance(payload, dict):
            return cls()
        values = {item.name: payload.get(item.name, "") for item in fields(cls)}
        return cls(**values)


@dataclass(frozen=True, slots=True)
class PrintDocumentDefinition:
    id: str
    label: str
    description: str
    default_template_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "default_template_id": self.default_template_id,
        }
