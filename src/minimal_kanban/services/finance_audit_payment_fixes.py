from __future__ import annotations

from collections.abc import Callable

from ..models import CashTransaction, normalize_text, short_entity_id
from .card_service_cashbox_cancellation import (
    _CASH_TRANSACTION_KIND_CANCELLATION,
    _CASH_TRANSACTION_KIND_CANCELLED,
)


class TransactionReferences:
    """Resolve stored references with the same precedence as the transaction resolver."""

    def __init__(self, transactions: list[CashTransaction]):
        self.transactions = transactions
        self.exact: dict[str, list[CashTransaction]] = {}
        for transaction in transactions:
            self.exact.setdefault(transaction.id, []).append(transaction)
        self.folded: dict[str, list[CashTransaction]] | None = None
        self.short: dict[str, list[CashTransaction]] | None = None

    def matches(self, value: str) -> list[CashTransaction]:
        requested = normalize_text(value, default="")
        if not requested or len(requested) > 128:
            return []
        exact = self.exact.get(requested, [])
        if exact:
            return exact
        if self.short is None:
            self.short = {}
            self.folded = {}
            for transaction in self.transactions:
                self.folded.setdefault(transaction.id.upper(), []).append(transaction)
                key = short_entity_id(transaction.id, prefix="CT").upper()
                self.short.setdefault(key, []).append(transaction)
        key = requested.upper()
        short = self.short.get(key, [])
        if not short:
            return []
        result = list(short)
        seen = {id(item) for item in result}
        result.extend(item for item in self.folded.get(key, []) if id(item) not in seen)
        return result


def payment_protected_transaction_ids(
    transactions: list[CashTransaction],
    is_transfer: Callable[[CashTransaction], bool],
    references: TransactionReferences,
    target_ids: set[str],
) -> set[str]:
    blocked_ids = {
        transaction.id
        for transaction in transactions
        if transaction.id in target_ids
        and (
            normalize_text(transaction.transaction_kind, default="", limit=32).casefold()
            in {
                _CASH_TRANSACTION_KIND_CANCELLED,
                _CASH_TRANSACTION_KIND_CANCELLATION,
                "cashbox_normalization",
                "salary_payout",
                "payout",
                "salary",
                "salary_payment",
                "salary_advance",
                "advance",
                "avans",
            }
            or transaction.employee_id
            or transaction.employee_name
            or transaction.transfer_group_id
            or transaction.related_transaction_id
            or is_transfer(transaction)
        )
    }
    for transaction in transactions:
        blocked_ids.update(
            item.id
            for item in references.matches(transaction.related_transaction_id)
            if item.id in target_ids
        )
    return blocked_ids


def payment_kind_safe_fixes(
    transactions: list[CashTransaction], blocked_ids: set[str]
) -> dict[str, dict[str, str]]:
    """Offer legacy payment tags without overwriting reversal or transfer identity."""
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
