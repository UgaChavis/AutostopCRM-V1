from __future__ import annotations

import math
from copy import copy
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple

from ..models import Card, CashBox, CashTransaction, business_timezone, normalize_actor_name
from ..operator_permissions import (
    EMPLOYEES_CASHBOXES_ACCESS_PERMISSION,
    operator_has_permission,
)
from ..storage import json_store
from ..storage.cashbox_receipts import (
    CASHBOX_NOTIFICATION_SEEN_SETTING_KEY as CASHBOX_NOTIFICATION_SEEN_SETTING_KEY,
)
from ..storage.cashbox_receipts import (
    CashboxReceiptStoreError,
    newer_cashbox_receipt,
)

if TYPE_CHECKING:
    from ..storage.json_store import CashboxReceiptVersion


class _CashboxDetails(NamedTuple):
    version: CashboxReceiptVersion | None
    cashboxes: list[CashBox]
    transactions: list[CashTransaction]
    order_context: dict[str, dict[str, object]]


class FinanceReadCore:
    """Read-only finance orchestration behind CardService public facades."""

    def __init__(self, service: Any) -> None:
        self._service = service
        self._cashbox_details: _CashboxDetails | None = None

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

    def _current_cashbox_details(self) -> _CashboxDetails | None:
        store = self._service._store
        cached = self._cashbox_details
        if (
            cached is not None
            and cached.version is not None
            and cached.version.content_digest is None
            and store._cashbox_receipt_state_matches(cached.version)
        ):
            return cached
        return None

    def _read_cold_cashbox_details(self) -> _CashboxDetails | None:
        service = self._service
        store = service._store
        with store._cashbox_receipt_snapshot_lock:
            if cached := self._current_cashbox_details():
                return cached
            try:
                version, state = store._read_cashbox_source(None, full_state=True)
                receipt = store._cashbox_receipt_snapshot
                if receipt is not None and receipt.version == version and receipt.dates_valid:
                    cashboxes = list(receipt.cashboxes)
                    transactions = list(receipt.transactions)
                else:
                    cashboxes, _ = store._normalize_cashboxes(state)
                    transactions, _ = store._normalize_cash_transactions(state)
                # Preserve the existing migration path for legacy ledgers.
                if [box.to_storage_dict() for box in cashboxes] != state.get("cashboxes", []):
                    return None
                if [item.to_storage_dict() for item in transactions] != state.get(
                    "cash_transactions", []
                ):
                    return None
                # A legacy prepayment can synthesize a payment, but has no
                # transaction reference. Only explicit payments can link a row.
                cards = []
                for raw_card in state.get("cards", []):
                    order = raw_card.get("repair_order")
                    if isinstance(order, dict) and order.get(
                        "payments", order.get("payment_history")
                    ):
                        card = Card.from_dict(raw_card)
                        if card.id != raw_card.get("id"):
                            return None
                        cards.append(card)
            except (ValueError, TypeError, OverflowError, json_store.StateFileCorruptedError):
                return None
            details = _CashboxDetails(
                version,
                service._ordered_cashboxes(cashboxes),
                transactions,
                service._repair_order_transaction_context(cards, transactions, cashboxes),
            )
            self._cashbox_details = details
            return details

    def _read_cashbox_details(self) -> _CashboxDetails:
        service = self._service
        store = service._store
        if cached := self._current_cashbox_details():
            return cached
        source = store._cashbox_receipt_source
        if not json_store._SNAPSHOT_REQUIRES_FILE_LOCK and (
            source is None or not store._cashbox_receipt_state_matches(source[0])
        ):
            if details := self._read_cold_cashbox_details():
                return details
        with service._lock:
            if cached := self._current_cashbox_details():
                return cached
            # Read the bundle and its verified ledger provenance together.
            with store._lock:
                bundle = store.read_bundle()
                source = store._cashbox_receipt_source
            details = _CashboxDetails(
                source[0] if source is not None else None,
                service._ordered_cashboxes([copy(box) for box in bundle["cashboxes"]]),
                [copy(transaction) for transaction in bundle["cash_transactions"]],
                service._repair_order_transaction_context(
                    bundle["cards"], bundle["cash_transactions"], bundle["cashboxes"]
                ),
            )
            if details.version is not None and details.version.content_digest is None:
                self._cashbox_details = details
            return details

    def get_cashbox(self, payload: dict | None = None) -> dict:
        service = self._service
        payload = payload or {}
        transaction_limit = service._validated_limit(
            payload.get("transaction_limit"), default=300, maximum=5000
        )
        transaction_offset = _validated_offset(payload.get("transaction_offset"))
        compact = service._validated_optional_bool(payload, "compact", default=False)
        details = self._read_cashbox_details()
        cashbox = service._find_cashbox(details.cashboxes, payload.get("cashbox_id"))
        transactions = service._cashbox_transactions(details.transactions, cashbox.id)
        returned_transactions = transactions[
            transaction_offset : transaction_offset + transaction_limit
        ]
        serialize_transaction = (
            service._serialize_cash_transaction_compact
            if compact
            else service._serialize_cash_transaction
        )
        return {
            "cashbox": service._serialize_cashbox(cashbox, transactions),
            "transactions": [
                serialize_transaction(item, repair_order_context=details.order_context.get(item.id))
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
