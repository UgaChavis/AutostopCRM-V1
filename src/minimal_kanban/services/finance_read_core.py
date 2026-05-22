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
            transaction_offset = _validated_offset(payload.get("transaction_offset"))
            bundle = service._store.read_bundle()
            cashboxes = service._ordered_cashboxes(bundle["cashboxes"])
            cashbox = service._find_cashbox(cashboxes, payload.get("cashbox_id"))
            transactions = service._cashbox_transactions(bundle["cash_transactions"], cashbox.id)
            returned_transactions = transactions[
                transaction_offset : transaction_offset + transaction_limit
            ]
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
                    for item in returned_transactions
                ],
                "meta": {
                    "transactions_total": len(transactions),
                    "transaction_limit": transaction_limit,
                    "transaction_offset": transaction_offset,
                    "transactions_returned": len(returned_transactions),
                    "has_more": transaction_offset + len(returned_transactions) < len(transactions),
                },
            }

    def get_cash_journal(self, payload: dict | None = None) -> dict:
        service = self._service
        with service._lock:
            payload = payload or {}
            months = service._validated_limit(payload.get("months"), default=3, maximum=12)
            limit = service._validated_limit(payload.get("limit"), default=5000, maximum=10000)
            include_markdown = service._validated_optional_bool(
                payload, "include_markdown", default=True
            )
            compact_groups = service._validated_optional_bool(
                payload, "compact_groups", default=False
            )
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
                include_markdown=include_markdown,
                compact_groups=compact_groups,
            )
            result = {
                "entries": journal["entries"],
                "days": journal["days"],
                "weeks": journal["weeks"],
                "months": journal["months"],
                "totals": journal["totals"],
                "meta": journal["meta"],
            }
            if include_markdown:
                result["markdown"] = journal["markdown"]
                result["text"] = journal["markdown"]
            return result

    def get_finance_audit(self, payload: dict | None = None) -> dict:
        service = self._service
        with service._lock:
            _ = payload or {}
            bundle = service._store.read_bundle()
            return service._build_finance_audit(bundle)


def _validated_offset(value: Any, *, default: int = 0, maximum: int = 1_000_000) -> int:
    if value in (None, ""):
        return default
    try:
        offset = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(maximum, offset))
