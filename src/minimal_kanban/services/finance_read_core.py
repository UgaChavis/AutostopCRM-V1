from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..models import CashTransaction, business_timezone


class FinanceReadCore:
    """Read-only finance orchestration behind CardService public facades."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def list_cashboxes(self, payload: dict | None = None) -> dict:
        service = self._service
        with service._lock:
            payload = payload or {}
            limit = service._validated_limit(payload.get("limit"), default=200, maximum=1000)
            bundle = service._store.read_bundle()
            cashboxes = service._ordered_cashboxes(bundle["cashboxes"])
            transactions = bundle["cash_transactions"]
            serialized_cashboxes = [
                service._serialize_cashbox(cashbox, transactions) for cashbox in cashboxes[:limit]
            ]
            return {
                "cashboxes": serialized_cashboxes,
                "meta": {
                    "total": len(cashboxes),
                    "transactions_total": len(transactions),
                    "limit": limit,
                    "returned": len(serialized_cashboxes),
                    "has_more": len(cashboxes) > len(serialized_cashboxes),
                },
            }

    def get_cashbox(self, payload: dict | None = None) -> dict:
        service = self._service
        with service._lock:
            payload = payload or {}
            transaction_limit = service._validated_limit(
                payload.get("transaction_limit"), default=300, maximum=5000
            )
            bundle = service._store.read_bundle()
            cashboxes = service._ordered_cashboxes(bundle["cashboxes"])
            cashbox = service._find_cashbox(cashboxes, payload.get("cashbox_id"))
            transactions = service._cashbox_transactions(bundle["cash_transactions"], cashbox.id)
            repair_order_transaction_context = service._repair_order_transaction_context(
                bundle["cards"]
            )
            return {
                "cashbox": service._serialize_cashbox(cashbox, bundle["cash_transactions"]),
                "transactions": [
                    service._serialize_cash_transaction(
                        item,
                        repair_order_context=repair_order_transaction_context.get(item.id),
                    )
                    for item in transactions[:transaction_limit]
                ],
                "meta": {
                    "transactions_total": len(transactions),
                    "transaction_limit": transaction_limit,
                },
            }

    def get_cash_journal(self, payload: dict | None = None) -> dict:
        service = self._service
        with service._lock:
            payload = payload or {}
            months = service._validated_limit(payload.get("months"), default=3, maximum=12)
            limit = service._validated_limit(payload.get("limit"), default=5000, maximum=10000)
            bundle = service._store.read_bundle()
            period_start = datetime.now(tz=business_timezone()) - timedelta(days=30 * months)
            recent_transactions: list[CashTransaction] = []
            for item in bundle["cash_transactions"]:
                created_at = service._cash_transaction_business_datetime(item.created_at)
                if created_at is None or created_at < period_start:
                    continue
                recent_transactions.append(item)
            recent_transactions.sort(
                key=lambda item: (
                    service._cash_transaction_business_sortable_datetime(item.created_at),
                    item.id,
                ),
                reverse=True,
            )
            returned_transactions = recent_transactions[:limit]
            cashboxes = service._ordered_cashboxes(bundle["cashboxes"])
            cashboxes_by_id = {cashbox.id: cashbox for cashbox in cashboxes}
            repair_order_transaction_context = service._repair_order_transaction_context(
                bundle["cards"]
            )
            journal = service._build_cash_journal(
                returned_transactions,
                cashboxes_by_id,
                months=months,
                limit=limit,
                total=len(recent_transactions),
                period_start=period_start,
                all_transactions=bundle["cash_transactions"],
                cashboxes=cashboxes,
                repair_order_transaction_context=repair_order_transaction_context,
            )
            return {
                "entries": journal["entries"],
                "days": journal["days"],
                "weeks": journal["weeks"],
                "months": journal["months"],
                "totals": journal["totals"],
                "markdown": journal["markdown"],
                "text": journal["markdown"],
                "meta": journal["meta"],
            }

    def get_finance_audit(self, payload: dict | None = None) -> dict:
        service = self._service
        with service._lock:
            _ = payload or {}
            bundle = service._store.read_bundle()
            return service._build_finance_audit(bundle)
