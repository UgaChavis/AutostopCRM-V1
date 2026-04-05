from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


REPAIR_ORDER_NUMBER_LIMIT = 40
REPAIR_ORDER_DATE_LIMIT = 32
REPAIR_ORDER_FIELD_LIMIT = 160
REPAIR_ORDER_COMMENT_LIMIT = 4000
REPAIR_ORDER_ROW_NAME_LIMIT = 240
REPAIR_ORDER_ROW_VALUE_LIMIT = 40
REPAIR_ORDER_ROWS_LIMIT = 100
REPAIR_ORDER_TAG_LIMIT = 5
REPAIR_ORDER_TAG_LABEL_LIMIT = 24
REPAIR_ORDER_TAG_COLOR_LIMIT = 16
REPAIR_ORDER_STATUS_OPEN = "open"
REPAIR_ORDER_STATUS_CLOSED = "closed"
REPAIR_ORDER_STATUS_LIMIT = 16
REPAIR_ORDER_DEFAULT_TAG_COLOR = "green"
REPAIR_ORDER_ALLOWED_STATUSES = {
    REPAIR_ORDER_STATUS_OPEN,
    REPAIR_ORDER_STATUS_CLOSED,
}
REPAIR_ORDER_ALLOWED_TAG_COLORS = {
    "green",
    "yellow",
    "red",
}


def _normalize_single_line(value, *, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    return text[:limit]


def _normalize_multiline(value, *, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _normalize_tag_label(value) -> str:
    normalized = _normalize_single_line(value, limit=REPAIR_ORDER_TAG_LABEL_LIMIT).replace(",", " ")
    return " ".join(normalized.split()).upper()


def _normalize_tag_color(value) -> str:
    normalized = _normalize_single_line(value, limit=REPAIR_ORDER_TAG_COLOR_LIMIT).lower()
    if normalized not in REPAIR_ORDER_ALLOWED_TAG_COLORS:
        return REPAIR_ORDER_DEFAULT_TAG_COLOR
    return normalized


def normalize_repair_order_status(value, *, default: str = REPAIR_ORDER_STATUS_OPEN) -> str:
    raw = _normalize_single_line(value, limit=REPAIR_ORDER_STATUS_LIMIT).lower()
    if raw in {"", "opened", "active", "открыт", "открыта"}:
        return default
    if raw in {"closed", "archived", "archive", "закрыт", "закрыта"}:
        return REPAIR_ORDER_STATUS_CLOSED
    if raw in REPAIR_ORDER_ALLOWED_STATUSES:
        return raw
    return default


def _parse_decimal(value) -> Decimal | None:
    raw = str(value or "").strip().replace(" ", "").replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _format_decimal(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


@dataclass(slots=True)
class RepairOrderRow:
    name: str = ""
    quantity: str = ""
    price: str = ""
    total: str = ""

    def __post_init__(self) -> None:
        self.name = _normalize_single_line(self.name, limit=REPAIR_ORDER_ROW_NAME_LIMIT)
        self.quantity = _normalize_single_line(self.quantity, limit=REPAIR_ORDER_ROW_VALUE_LIMIT)
        self.price = _normalize_single_line(self.price, limit=REPAIR_ORDER_ROW_VALUE_LIMIT)
        normalized_total = _normalize_single_line(self.total, limit=REPAIR_ORDER_ROW_VALUE_LIMIT)
        computed_total = self.computed_total()
        self.total = computed_total if computed_total else normalized_total

    def is_empty(self) -> bool:
        return not any([self.name, self.quantity, self.price, self.total])

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "quantity": self.quantity,
            "price": self.price,
            "total": self.total,
        }

    def total_value(self) -> Decimal:
        computed_total = self.computed_total()
        if computed_total:
            parsed = _parse_decimal(computed_total)
            if parsed is not None:
                return parsed
        parsed_total = _parse_decimal(self.total)
        return parsed_total if parsed_total is not None else Decimal("0")

    def computed_total(self) -> str:
        quantity = _parse_decimal(self.quantity)
        price = _parse_decimal(self.price)
        if quantity is None or price is None:
            return ""
        return _format_decimal(quantity * price)

    @classmethod
    def from_dict(cls, payload: Any) -> "RepairOrderRow":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            name=payload.get("name", ""),
            quantity=payload.get("quantity", payload.get("qty", "")),
            price=payload.get("price", ""),
            total=payload.get("total", payload.get("sum", "")),
        )


@dataclass(slots=True)
class RepairOrderTag:
    label: str = ""
    color: str = REPAIR_ORDER_DEFAULT_TAG_COLOR

    def __post_init__(self) -> None:
        self.label = _normalize_tag_label(self.label)
        self.color = _normalize_tag_color(self.color)

    def is_empty(self) -> bool:
        return not self.label

    def to_dict(self) -> dict[str, str]:
        return {
            "label": self.label,
            "color": self.color,
        }

    @classmethod
    def from_value(cls, payload: Any) -> "RepairOrderTag | None":
        if isinstance(payload, cls):
            return cls(label=payload.label, color=payload.color)
        if isinstance(payload, dict):
            label = _normalize_tag_label(payload.get("label") or payload.get("name"))
            if not label:
                return None
            return cls(label=label, color=payload.get("color"))
        label = _normalize_tag_label(payload)
        if not label:
            return None
        return cls(label=label)


def normalize_repair_order_rows(value: Any) -> list[RepairOrderRow]:
    if not isinstance(value, list):
        return []
    rows: list[RepairOrderRow] = []
    for item in value:
        row = RepairOrderRow.from_dict(item)
        if row.is_empty():
            continue
        rows.append(row)
        if len(rows) >= REPAIR_ORDER_ROWS_LIMIT:
            break
    return rows


def normalize_repair_order_tags(value: Any) -> list[RepairOrderTag]:
    if not isinstance(value, list):
        return []
    tags_by_label: dict[str, RepairOrderTag] = {}
    for item in value:
        tag = RepairOrderTag.from_value(item)
        if tag is None or tag.is_empty():
            continue
        tags_by_label[tag.label] = tag
        if len(tags_by_label) >= REPAIR_ORDER_TAG_LIMIT:
            break
    return list(tags_by_label.values())


@dataclass(slots=True)
class RepairOrder:
    number: str = ""
    date: str = ""
    status: str = REPAIR_ORDER_STATUS_OPEN
    opened_at: str = ""
    closed_at: str = ""
    client: str = ""
    phone: str = ""
    vehicle: str = ""
    license_plate: str = ""
    vin: str = ""
    mileage: str = ""
    reason: str = ""
    comment: str = ""
    note: str = ""
    tags: list[RepairOrderTag] = field(default_factory=list)
    works: list[RepairOrderRow] = field(default_factory=list)
    materials: list[RepairOrderRow] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.number = _normalize_single_line(self.number, limit=REPAIR_ORDER_NUMBER_LIMIT)
        self.date = _normalize_single_line(self.date, limit=REPAIR_ORDER_DATE_LIMIT)
        self.status = normalize_repair_order_status(self.status)
        self.opened_at = _normalize_single_line(self.opened_at, limit=REPAIR_ORDER_DATE_LIMIT)
        self.closed_at = _normalize_single_line(self.closed_at, limit=REPAIR_ORDER_DATE_LIMIT)
        self.client = _normalize_single_line(self.client, limit=REPAIR_ORDER_FIELD_LIMIT)
        self.phone = _normalize_single_line(self.phone, limit=REPAIR_ORDER_FIELD_LIMIT)
        self.vehicle = _normalize_single_line(self.vehicle, limit=REPAIR_ORDER_FIELD_LIMIT)
        self.license_plate = _normalize_single_line(self.license_plate, limit=REPAIR_ORDER_FIELD_LIMIT)
        self.vin = _normalize_single_line(self.vin, limit=REPAIR_ORDER_FIELD_LIMIT)
        self.mileage = _normalize_single_line(self.mileage, limit=REPAIR_ORDER_FIELD_LIMIT)
        self.reason = _normalize_multiline(self.reason, limit=REPAIR_ORDER_COMMENT_LIMIT)
        self.comment = _normalize_multiline(self.comment, limit=REPAIR_ORDER_COMMENT_LIMIT)
        self.note = _normalize_multiline(self.note, limit=REPAIR_ORDER_COMMENT_LIMIT)
        self.tags = normalize_repair_order_tags(self.tags)
        self.works = normalize_repair_order_rows(self.works)
        self.materials = normalize_repair_order_rows(self.materials)

    def is_empty(self) -> bool:
        return not any(
            [
                self.number,
                self.date,
                self.opened_at,
                self.closed_at,
                self.client,
                self.phone,
                self.vehicle,
                self.license_plate,
                self.vin,
                self.mileage,
                self.reason,
                self.comment,
                self.note,
                self.tags,
                self.works,
                self.materials,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "date": self.date,
            "status": self.status,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "client": self.client,
            "phone": self.phone,
            "vehicle": self.vehicle,
            "license_plate": self.license_plate,
            "vin": self.vin,
            "mileage": self.mileage,
            "reason": self.reason,
            "comment": self.comment,
            "client_information": self.comment,
            "note": self.note,
            "tags": [tag.to_dict() for tag in self.tags],
            "works": [row.to_dict() for row in self.works],
            "materials": [row.to_dict() for row in self.materials],
            "works_total": self.works_total_amount(),
            "materials_total": self.materials_total_amount(),
            "grand_total": self.grand_total_amount(),
            "has_any_data": not self.is_empty(),
        }

    def to_storage_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "date": self.date,
            "status": self.status,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "client": self.client,
            "phone": self.phone,
            "vehicle": self.vehicle,
            "license_plate": self.license_plate,
            "vin": self.vin,
            "mileage": self.mileage,
            "reason": self.reason,
            "comment": self.comment,
            "note": self.note,
            "tags": [tag.to_dict() for tag in self.tags],
            "works": [row.to_dict() for row in self.works],
            "materials": [row.to_dict() for row in self.materials],
        }

    def works_total_amount(self) -> str:
        return _format_decimal(sum((row.total_value() for row in self.works), Decimal("0")))

    def materials_total_amount(self) -> str:
        return _format_decimal(sum((row.total_value() for row in self.materials), Decimal("0")))

    def grand_total_amount(self) -> str:
        works_total = _parse_decimal(self.works_total_amount()) or Decimal("0")
        materials_total = _parse_decimal(self.materials_total_amount()) or Decimal("0")
        return _format_decimal(works_total + materials_total)

    @classmethod
    def from_dict(cls, payload: Any) -> "RepairOrder":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            number=payload.get("number", ""),
            date=payload.get("date", ""),
            status=payload.get("status", REPAIR_ORDER_STATUS_OPEN),
            opened_at=payload.get("opened_at", payload.get("openedAt", "")),
            closed_at=payload.get("closed_at", payload.get("closedAt", "")),
            client=payload.get("client", ""),
            phone=payload.get("phone", ""),
            vehicle=payload.get("vehicle", ""),
            license_plate=payload.get("license_plate", payload.get("licensePlate", "")),
            vin=payload.get("vin", ""),
            mileage=payload.get("mileage", payload.get("odometer", "")),
            reason=payload.get("reason", payload.get("problem", "")),
            comment=payload.get("client_information", payload.get("clientInformation", payload.get("comment", ""))),
            note=payload.get("note", payload.get("master_comment", payload.get("masterComment", payload.get("internal_comment", payload.get("internalComment", ""))))),
            tags=payload.get("tags", []),
            works=payload.get("works", []),
            materials=payload.get("materials", []),
        )
