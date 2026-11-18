from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from ..models import CashTransaction, business_timezone, normalize_actor_name
from ..operator_permissions import (
    EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
    operator_has_permission,
)
from ..storage.cashbox_receipts import (
    CASHBOX_NOTIFICATION_SEEN_SETTING_KEY as CASHBOX_NOTIFICATION_SEEN_SETTING_KEY,
)
from ..storage.cashbox_receipts import (
    CashboxReceiptStoreError,
    newer_cashbox_receipt,
)


class FinanceReadCore:
    """Read-only finance orchestration behind CardService public facades."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def list_cashboxes(self, payload: dict | None = None) -> dict:
        service = self._service
        payload = payload or {}
        limit = service._validated_limit(payload.get("limit"), default=200, maximum=1000)
        operator_session = payload.get("_operator_session")
        references_only = isinstance(operator_session, dict) and not operator_has_permission(
            operator_session, EMPLOYEES_CASHBOXES_ACCESS_PERMISSION
        )
        actor_key = normalize_actor_name(payload.get("actor_name"), default="").casefold()
        if references_only:
            cashboxes = service._ordered_cashboxes(service._store.read_cashbox_references())
            serialized = [cashbox.to_dict() for cashbox in cashboxes[:limit]]
            return {
                "cashboxes": serialized,
                "notification": None,
                "meta": {
                    "total": len(cashboxes),
                    "limit": limit,
                    "returned": len(serialized),
                    "has_more": len(cashboxes) > len(serialized),
                    "references_only": True,
                },
            }
        snapshot, notification = self._cashbox_list_view(actor_key)
        cashboxes = service._ordered_cashboxes(snapshot.cashboxes)
        transactions = snapshot.transactions
        serialized = service._serialize_cashboxes(cashboxes[:limit], transactions)
        active_revision = {}
        if active_id := payload.get("cashbox_id"):
            active_revision["active_cashbox_updated_at"] = next(
                (box.updated_at for box in cashboxes if box.id == active_id), None
            )
        return {
            "cashboxes": serialized,
            "notification": notification,
            "meta": {
                "total": len(cashboxes),
                "transactions_total": len(transactions),
                "limit": limit,
                "returned": len(serialized),
                "has_more": len(cashboxes) > len(serialized),
                **active_revision,
                **({"notification_unavailable": True} if notification is None else {}),
            },
        }

    def _cashbox_list_view(self, actor_key: str):
        service = self._service
        store = service._store
        # The actor's monotonic position must span the ledger read. Never wait
        # for the actor's filesystem lock under the shared service or state lock.
        snapshot = None
        try:
            before = store.cashbox_receipts.read(actor_key) if actor_key else None
            for _ in range(2):
                snapshot = store.read_cashbox_receipt_state()
                if not snapshot.dates_valid:
                    break
                after = store.cashbox_receipts.read(actor_key) if actor_key else None
                if before == after:
                    receipt = newer_cashbox_receipt(
                        service._registered_cashbox_receipt(
                            snapshot.transactions, snapshot.legacy_receipts.get(actor_key)
                        ),
                        service._registered_cashbox_receipt(snapshot.transactions, after),
                    )
                    return snapshot, service._cashbox_notification_summary(
                        snapshot.transactions, actor_key, receipt
                    )
                before = after
        except CashboxReceiptStoreError:
            service._logger.warning("cashbox_notification_receipt_unavailable")
        if snapshot is None:
            snapshot = store.read_cashbox_receipt_state()
        return snapshot, None

    def get_cashbox(self, payload: dict | None = None) -> dict:
        service = self._service
        with service._lock:
            payload = payload or {}
            transaction_limit = service._validated_limit(
                payload.get("transaction_limit"), default=300, maximum=5000
            )
            transaction_offset = _validated_offset(payload.get("transaction_offset"))
            compact = service._validated_optional_bool(payload, "compact", default=False)
            bundle = service._store.read_bundle()
            cashboxes = service._ordered_cashboxes(bundle["cashboxes"])
            cashbox = service._find_cashbox(cashboxes, payload.get("cashbox_id"))
            transactions = service._cashbox_transactions(bundle["cash_transactions"], cashbox.id)
            returned_transactions = transactions[
                transaction_offset : transaction_offset + transaction_limit
            ]
            repair_order_transaction_context = service._repair_order_transaction_context(
                bundle["cards"], bundle["cash_transactions"], bundle["cashboxes"]
            )
            serialize_transaction = (
                service._serialize_cash_transaction_compact
                if compact
                else service._serialize_cash_transaction
            )
            return {
                "cashbox": service._serialize_cashbox(cashbox, transactions),
                "transactions": [
                    serialize_transaction(
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
                    "compact": compact,
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
                bundle["cards"], bundle["cash_transactions"], bundle["cashboxes"]
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
    if isinstance(value, bool):
        return default
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return default
    if not math.isfinite(numeric) or not numeric.is_integer():
        return default
    if numeric <= 0:
        return 0
    if numeric > maximum:
        return maximum
    return int(numeric)
