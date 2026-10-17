from __future__ import annotations

from collections.abc import Callable

from ..models import CashTransaction, normalize_text
from .card_service_cashbox_cancellation import (
    _CASH_TRANSACTION_KIND_CANCELLATION,
    _CASH_TRANSACTION_KIND_CANCELLED,
)


def payment_kind_safe_fixes(
    transactions: list[CashTransaction],
    is_transfer: Callable[[CashTransaction], bool],
) -> dict[str, dict[str, str]]:
    """Offer legacy payment tags without overwriting reversal or transfer identity."""
    blocked_ids = {
        transaction.id
        for transaction in transactions
        if normalize_text(transaction.transaction_kind, default="", limit=32).casefold()
        in {_CASH_TRANSACTION_KIND_CANCELLED, _CASH_TRANSACTION_KIND_CANCELLATION}
        or transaction.transfer_group_id
        or transaction.related_transaction_id
        or is_transfer(transaction)
    }
    # Preserve cancellation backreferences, including legacy case variants.
    # Build the set once instead of scanning the ledger for every proposal.
    blocked_ids.update(
        transaction.related_transaction_id
        for transaction in transactions
        if normalize_text(transaction.transaction_kind, default="", limit=32).casefold()
        == _CASH_TRANSACTION_KIND_CANCELLATION
    )
    return {
        transaction.id: {
            "kind": "set_transaction_kind",
            "cash_transaction_id": transaction.id,
            "value": "repair_order_payment",
        }
        for transaction in transactions
        if transaction.id not in blocked_ids
        and transaction.transaction_kind != "repair_order_payment"
    }
