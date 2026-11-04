"""Validate cash references before adopting or deleting a payment's ledger row."""

from ..models import Card, CashTransaction, normalize_money_minor, normalize_text
from ..repair_order import RepairOrderPayment
from .cash_amounts import cash_payment_minor
from .errors import ServiceError


class CardServicePaymentLinksMixin:
    def _ensure_transfer_has_no_payment_links(
        self, cards: list[Card], transactions: list[CashTransaction], *transfer: CashTransaction
    ) -> None:
        for transaction in transfer:
            linked_card, _ = self._find_repair_order_payment_by_cash_transaction(
                cards, transaction.id, transactions
            )
            if linked_card is not None:
                self._fail(
                    "repair_order_payment_cash_link_invalid",
                    "Перемещение связано с оплатой заказ-наряда. Требуется сверка.",
                    status_code=409,
                    details={"transaction_id": transaction.id},
                )

    def _ensure_repair_order_payment_cash_link(
        self,
        cards: list[Card],
        transactions: list[CashTransaction],
        payment: RepairOrderPayment,
        transaction: CashTransaction,
        *,
        owner: tuple[str, str] | None = None,
        duplicate: bool = False,
        reference_transactions: list[CashTransaction] | None = None,
        exact_amount: bool = False,
    ) -> None:
        linked_card, linked_payment = self._find_repair_order_payment_by_cash_transaction(
            cards,
            transaction.id,
            reference_transactions if reference_transactions is not None else transactions,
        )
        if duplicate or (linked_card is not None and (linked_card.id, linked_payment.id) != owner):
            self._fail(
                "repair_order_payment_cash_link_ambiguous",
                "Кассовое движение связано с несколькими оплатами. Требуется сверка.",
                status_code=409,
                details={"transaction_id": transaction.id},
            )
        self._ensure_cash_transaction_cancellable(transaction, transactions)
        try:
            amount_minor = (
                cash_payment_minor(payment.amount)
                if exact_amount
                else normalize_money_minor(payment.amount, default=0)
            )
        except ServiceError:
            amount_minor = None
        if (
            transaction.direction != "income"
            or transaction.amount_minor != amount_minor
            or transaction.cashbox_id != payment.cashbox_id
            or transaction.transfer_group_id
            or transaction.related_transaction_id
            or self._is_cashbox_transfer_transaction(transaction)
            or normalize_text(transaction.transaction_kind, default="", limit=32).casefold()
            in {"cashbox_normalization", "salary_payout", "salary_advance"}
        ):
            self._fail(
                "repair_order_payment_cash_link_invalid",
                "Кассовое движение не соответствует оплате заказ-наряда.",
                status_code=409,
                details={"transaction_id": transaction.id},
            )
