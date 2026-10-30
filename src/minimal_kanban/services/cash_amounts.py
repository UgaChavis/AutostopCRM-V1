"""Check new cash amounts before permissive storage normalization clamps them."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, DecimalException, localcontext

from .. import models as model_helpers
from ..models import MONEY_MINOR_ABS_MAX, CashBox, _parse_money_minor_value, parse_datetime
from .errors import ServiceError


def advance_cashbox_revision(cashbox: CashBox) -> None:
    """Advance the change marker independently of transaction business dates."""
    now = model_helpers.utc_now()
    previous = parse_datetime(cashbox.updated_at)
    if previous is not None and now <= previous:
        now = previous + timedelta(microseconds=1)
    cashbox.updated_at = now.isoformat()


def validated_cash_amount_minor(payload: dict) -> int:
    raw = payload.get("amount_minor")
    if raw in (None, ""):
        raw = payload.get("amount")
    if raw in (None, ""):
        raise ServiceError(
            "validation_error", "Нужно передать сумму операции.", details={"field": "amount"}
        )
    # Preserve the established int/minor and string/float/ruble input contract,
    # including rounding. The storage normalizer must not silently cap a new write.
    minor = _parse_money_minor_value(raw)
    if minor < 1 or minor > MONEY_MINOR_ABS_MAX:
        raise ServiceError(
            "validation_error",
            "Сумма операции должна быть больше нуля и не превышать допустимый лимит кассы.",
            details={"field": "amount", "maximum_minor": MONEY_MINOR_ABS_MAX},
        )
    return minor


def cash_payment_minor(amount: str) -> int:
    """New ledger entries must reproduce an order payment without rounding/clamping."""
    try:
        value = Decimal("".join(str(amount).split()).replace(",", "."))
        with localcontext() as context:
            context.prec = 64
            minor = value * 100
            if (
                not minor.is_finite()
                or minor <= 0
                or minor > MONEY_MINOR_ABS_MAX
                or minor != minor.to_integral_value()
            ):
                raise _invalid_cash_payment()
            return int(minor)
    except DecimalException:
        raise _invalid_cash_payment() from None


def _invalid_cash_payment() -> ServiceError:
    return ServiceError(
        "validation_error",
        "Для кассовой оплаты укажите положительную сумму в рублях с точностью до копеек "
        "в пределах допустимого лимита кассы.",
        details={"field": "repair_order.payments.amount", "maximum_minor": MONEY_MINOR_ABS_MAX},
    )
