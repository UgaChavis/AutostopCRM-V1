from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _reject_bool_int(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid integer parameters")
    return value


McpInt = Annotated[int | float | str, BeforeValidator(_reject_bool_int)]


class DeadlinePayload(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"days": 1, "hours": 0, "minutes": 0, "seconds": 0},
                {"total_seconds": 5400},
            ]
        }
    )

    days: McpInt = Field(default=0, ge=0, le=365, description="Whole days in the deadline delta.")
    hours: McpInt = Field(default=0, ge=0, le=23, description="Hours in the deadline delta.")
    minutes: McpInt = Field(default=0, ge=0, le=59, description="Minutes in the deadline delta.")
    seconds: McpInt = Field(default=0, ge=0, le=59, description="Seconds in the deadline delta.")
    total_seconds: McpInt = Field(
        default=0,
        ge=0,
        le=31_536_000,
        description="Optional shorthand for the full deadline in seconds. Can be combined with days, hours, minutes, and seconds.",
    )


class StickyDeadlinePayload(DeadlinePayload):
    total_seconds: McpInt = Field(default=0, ge=0, le=31_536_000)


class TagPayload(BaseModel):
    label: str = Field(min_length=1, max_length=24)
    color: Literal["green", "yellow", "red"] = "green"


class RepairOrderRowPayload(BaseModel):
    id: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=240)
    catalog_number: str = Field(default="", max_length=160)
    catalogNumber: str = Field(default="", max_length=160)
    quantity: str = Field(default="", max_length=40)
    cost_price: str = Field(default="", max_length=40)
    costPrice: str = Field(default="", max_length=40)
    price: str = Field(default="", max_length=40)
    total: str = Field(default="", max_length=40)
    executor_id: str = Field(default="", max_length=64)
    executor_name: str = Field(default="", max_length=160)
    work_executor_id_snapshot: str = Field(default="", max_length=64)
    work_executor_name_snapshot: str = Field(default="", max_length=160)
    material_executor_id_snapshot: str = Field(default="", max_length=64)
    material_executor_name_snapshot: str = Field(default="", max_length=160)
    material_quantity_snapshot: str = Field(default="", max_length=40)


class RepairOrderPaymentPayload(BaseModel):
    id: str | None = Field(default=None, max_length=80)
    amount: str = Field(default="", max_length=40)
    paid_at: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=240)
    payment_method: Literal["cash", "cashless", "card"] | None = None
    actor_name: str | None = Field(default=None, max_length=160)
    cashbox_id: str | None = Field(default=None, max_length=80)
    cashbox_name: str | None = Field(default=None, max_length=160)
    cash_transaction_id: str | None = Field(default=None, max_length=80)


class RepairOrderPatchPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "client": "Иван Иванов",
                    "comment": "Согласовать дальнейшую диагностику",
                    "note": "Комментарий мастера",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "2000"}],
                },
                {
                    "clientInformation": "Информация для клиента",
                    "master_comment": "Внутренняя заметка мастера",
                    "advancePayment": "500",
                },
            ]
        },
    )

    number: str | None = Field(default=None, max_length=40)
    date: str | None = Field(default=None, max_length=32)
    status: Literal["open", "ready", "closed"] | None = None
    opened_at: str | None = Field(default=None, max_length=32)
    openedAt: str | None = Field(default=None, max_length=32)
    closed_at: str | None = Field(default=None, max_length=32)
    closedAt: str | None = Field(default=None, max_length=32)
    client: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=160)
    vehicle: str | None = Field(default=None, max_length=160)
    license_plate: str | None = Field(default=None, max_length=160)
    licensePlate: str | None = Field(default=None, max_length=160)
    vin: str | None = Field(default=None, max_length=160)
    mileage: str | None = Field(default=None, max_length=160)
    odometer: str | None = Field(default=None, max_length=160)
    payment_method: Literal["cash", "cashless", "card"] | None = None
    paymentMethod: Literal["cash", "cashless", "card"] | None = None
    prepayment: str | None = Field(default=None, max_length=40)
    advance_payment: str | None = Field(default=None, max_length=40)
    advancePayment: str | None = Field(default=None, max_length=40)
    payments: list[RepairOrderPaymentPayload] | None = None
    payment_history: list[RepairOrderPaymentPayload] | None = None
    reason: str | None = Field(default=None, max_length=4000)
    comment: str | None = Field(default=None, max_length=4000)
    client_information: str | None = Field(default=None, max_length=4000)
    clientInformation: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=4000)
    master_comment: str | None = Field(default=None, max_length=4000)
    masterComment: str | None = Field(default=None, max_length=4000)
    internal_comment: str | None = Field(default=None, max_length=4000)
    internalComment: str | None = Field(default=None, max_length=4000)
    tags: list[TagPayload] | None = None
    works: list[RepairOrderRowPayload] | None = None
    materials: list[RepairOrderRowPayload] | None = None


class ClientVehiclePayload(BaseModel):
    id: str | None = Field(default=None, max_length=128)
    vehicle: str | None = Field(default=None, max_length=160)
    brand: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    vin: str | None = Field(default=None, max_length=160)
    license_plate: str | None = Field(default=None, max_length=160)
    year: str | None = Field(default=None, max_length=16)
    mileage: str | None = Field(default=None, max_length=160)
    body_number: str | None = Field(default=None, max_length=160)
    chassis_number: str | None = Field(default=None, max_length=160)
    engine_code: str | None = Field(default=None, max_length=160)
    engine_model: str | None = Field(default=None, max_length=160)
    gearbox_type: str | None = Field(default=None, max_length=160)
    gearbox_model: str | None = Field(default=None, max_length=160)
    drivetrain: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=2000)


class ClientProfilePayload(BaseModel):
    client_type: Literal["person", "ip", "ooo", "company"] = "person"
    last_name: str | None = Field(default=None, max_length=120)
    first_name: str | None = Field(default=None, max_length=120)
    middle_name: str | None = Field(default=None, max_length=120)
    display_name: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=80)
    phones: list[str] | None = Field(default=None, max_length=3)
    email: str | None = Field(default=None, max_length=160)
    emails: list[str] | None = Field(default=None, max_length=3)
    comment: str | None = Field(default=None, max_length=2000)
    legal_name: str | None = Field(default=None, max_length=160)
    short_name: str | None = Field(default=None, max_length=160)
    inn: str | None = Field(default=None, max_length=160)
    kpp: str | None = Field(default=None, max_length=160)
    ogrn: str | None = Field(default=None, max_length=160)
    checking_account: str | None = Field(default=None, max_length=160)
    bank_name: str | None = Field(default=None, max_length=160)
    bik: str | None = Field(default=None, max_length=160)
    correspondent_account: str | None = Field(default=None, max_length=160)
    legal_address: str | None = Field(default=None, max_length=160)
    actual_address: str | None = Field(default=None, max_length=160)
    contact_person: str | None = Field(default=None, max_length=160)
    contact_position: str | None = Field(default=None, max_length=160)
    vehicles: list[ClientVehiclePayload] | None = None


class ClientPatchPayload(BaseModel):
    client_type: Literal["person", "ip", "ooo", "company"] | None = None
    last_name: str | None = Field(default=None, max_length=120)
    first_name: str | None = Field(default=None, max_length=120)
    middle_name: str | None = Field(default=None, max_length=120)
    display_name: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=80)
    phones: list[str] | None = Field(default=None, max_length=3)
    email: str | None = Field(default=None, max_length=160)
    emails: list[str] | None = Field(default=None, max_length=3)
    comment: str | None = Field(default=None, max_length=2000)
    legal_name: str | None = Field(default=None, max_length=160)
    short_name: str | None = Field(default=None, max_length=160)
    inn: str | None = Field(default=None, max_length=160)
    kpp: str | None = Field(default=None, max_length=160)
    ogrn: str | None = Field(default=None, max_length=160)
    checking_account: str | None = Field(default=None, max_length=160)
    bank_name: str | None = Field(default=None, max_length=160)
    bik: str | None = Field(default=None, max_length=160)
    correspondent_account: str | None = Field(default=None, max_length=160)
    legal_address: str | None = Field(default=None, max_length=160)
    actual_address: str | None = Field(default=None, max_length=160)
    contact_person: str | None = Field(default=None, max_length=160)
    contact_position: str | None = Field(default=None, max_length=160)
    vehicles: list[ClientVehiclePayload] | None = None


def _deadline_part_value(value: Any, *, maximum: int) -> int:
    if isinstance(value, bool):
        return 0
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return 0
    if not math.isfinite(numeric) or not numeric.is_integer():
        return 0
    if numeric <= 0:
        return 0
    if numeric > maximum:
        return maximum
    return int(numeric)


def _resolved_create_card_deadline(deadline: DeadlinePayload | None) -> dict[str, int] | None:
    if deadline is None:
        return None
    payload = deadline.model_dump()
    resolved = {
        "days": _deadline_part_value(payload.get("days"), maximum=365),
        "hours": _deadline_part_value(payload.get("hours"), maximum=23),
        "minutes": _deadline_part_value(payload.get("minutes"), maximum=59),
        "seconds": _deadline_part_value(payload.get("seconds"), maximum=59),
        "total_seconds": _deadline_part_value(payload.get("total_seconds"), maximum=31_536_000),
    }
    if resolved["total_seconds"] > 0:
        return resolved
    if not any(resolved.get(part, 0) > 0 for part in ("days", "hours", "minutes", "seconds")):
        return {"days": 1, "hours": 0, "minutes": 0, "seconds": 0}
    return resolved


class JsonEnvelope(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


class ConnectorIdentityPayload(BaseModel):
    connector_name: str
    product_name: str
    board_name: str
    board_scope: str
    board_key: str
    scope_rule: str
    resource_url: str
    server_base_url: str
    streamable_http_path: str
    local_bind: str
    board_api_base_url: str
    auth_mode: str
    host: str
    port: int


class ConnectorIdentityToolData(BaseModel):
    identity: ConnectorIdentityPayload
    text: str


class ConnectorIdentityEnvelope(BaseModel):
    ok: bool
    data: ConnectorIdentityToolData
    error: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
