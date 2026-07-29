from __future__ import annotations

import re
from typing import Any

from .. import models as model_helpers
from ..models import (
    CashBox,
    CashTransaction,
    business_timezone,
    format_money_minor,
    normalize_text,
)
from ..repair_order import REPAIR_ORDER_STATUS_CLOSED, REPAIR_ORDER_STATUS_OPEN, RepairOrder

_CASH_CANCEL_REASON_MIN_CHARS = 10
_CASH_TRANSACTION_KIND_CANCELLED = "cashbox_cancelled"
_CASH_TRANSACTION_KIND_CANCELLATION = "cashbox_cancellation"
_GATEWAY_ATTESTATION_RUN_RE = re.compile(r"^AST-GWAT-\d{8}T\d{6}Z$")


class CardServiceCashboxCancellationMixin:
    def cancel_cash_transaction(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            cashboxes = bundle["cashboxes"]
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            settings = bundle["settings"]
            actor_name, source = self._audit_identity(payload, default_source="ui")
            transaction = self._find_cash_transaction(transactions, payload.get("transaction_id"))
            if transaction is None:
                self._fail(
                    "not_found",
                    "Кассовое движение не найдено.",
                    status_code=404,
                    details={"transaction_id": payload.get("transaction_id")},
                )
            requested_cashbox_id = normalize_text(payload.get("cashbox_id"), default="", limit=128)
            if requested_cashbox_id and requested_cashbox_id != transaction.cashbox_id:
                self._fail(
                    "validation_error",
                    "Касса не совпадает с выбранным движением.",
                    details={
                        "field": "cashbox_id",
                        "cashbox_id": requested_cashbox_id,
                        "transaction_cashbox_id": transaction.cashbox_id,
                    },
                )
            cashbox = self._find_cashbox(cashboxes, transaction.cashbox_id)
            expected_cashbox_updated_at = normalize_text(
                payload.get("expected_cashbox_updated_at"),
                default="",
                limit=80,
            )
            if expected_cashbox_updated_at and cashbox.updated_at != expected_cashbox_updated_at:
                self._fail(
                    "cashbox_update_conflict",
                    "Касса уже изменилась. Обновите данные и повторите действие.",
                    status_code=409,
                    details={"cashbox_id": cashbox.id},
                )
            reason = self._validated_cash_cancellation_reason(payload)
            attestation_run_id = normalize_text(
                payload.get("attestation_run_id"),
                default="",
                limit=64,
            )
            if attestation_run_id and not (
                _GATEWAY_ATTESTATION_RUN_RE.fullmatch(attestation_run_id)
                and cashbox.name.startswith(f"{attestation_run_id}-")
                and attestation_run_id in transaction.note
                and reason.startswith(attestation_run_id)
                and transaction.amount_minor == 100
                and str(payload.get("source") or "").strip().casefold() == "mcp_agent_gateway_v2"
                and actor_name
            ):
                self._fail(
                    "cash_cancellation_attestation_scope_invalid",
                    "Синтетическая отмена не соответствует контуру аттестации.",
                    status_code=403,
                )
            transaction_kind = normalize_text(transaction.transaction_kind, default="", limit=32)
            if transaction_kind in {
                _CASH_TRANSACTION_KIND_CANCELLED,
                _CASH_TRANSACTION_KIND_CANCELLATION,
            }:
                self._fail(
                    "validation_error",
                    "Эта операция уже является отменой или уже отменена.",
                    details={"transaction_id": transaction.id},
                )
            existing_cancellation = next(
                (
                    item
                    for item in transactions
                    if item.related_transaction_id == transaction.id
                    and item.transaction_kind == _CASH_TRANSACTION_KIND_CANCELLATION
                ),
                None,
            )
            if existing_cancellation is not None:
                self._fail(
                    "validation_error",
                    "Эта операция уже отменена.",
                    details={
                        "transaction_id": transaction.id,
                        "cancellation_transaction_id": existing_cancellation.id,
                    },
                )
            if transaction.transfer_group_id or transaction.related_transaction_id:
                return self._cancel_selected_cashbox_transfer_transaction(
                    bundle=bundle,
                    cashbox=cashbox,
                    transaction=transaction,
                    reason=reason,
                    actor_name=actor_name,
                    source=source,
                    expected_related_cashbox_updated_at=normalize_text(
                        payload.get("expected_related_cashbox_updated_at"),
                        default="",
                        limit=80,
                    ),
                    attestation_run_id=attestation_run_id,
                )
            linked_card, linked_payment = self._find_repair_order_payment_by_cash_transaction(
                cards, transaction.id
            )
            cancellation = self._append_cash_transaction(
                transactions=transactions,
                cashbox=cashbox,
                direction="expense" if transaction.direction == "income" else "income",
                amount_minor=transaction.amount_minor,
                note=self._cash_cancellation_note(reason),
                actor_name=actor_name,
                source=source,
                transaction_kind=_CASH_TRANSACTION_KIND_CANCELLATION,
                related_transaction_id=transaction.id,
            )
            transaction.transaction_kind = _CASH_TRANSACTION_KIND_CANCELLED
            response_meta: dict[str, object] = {
                "cancelled": True,
                "transaction_id": transaction.id,
                "cancellation_transaction_id": cancellation.id,
                "cashbox_id": cashbox.id,
                "reason": reason,
                "repair_order_card_id": linked_card.id if linked_card is not None else None,
            }
            if linked_card is not None and linked_payment is not None:
                next_order_payload = linked_card.repair_order.to_storage_dict()
                next_order_payload["payments"] = [
                    payment.to_storage_dict()
                    for payment in linked_card.repair_order.payments
                    if payment.id != linked_payment.id
                ]
                if not next_order_payload["payments"]:
                    next_order_payload["prepayment"] = ""
                candidate_order = RepairOrder.from_dict(next_order_payload)
                if (
                    linked_card.repair_order.status == REPAIR_ORDER_STATUS_CLOSED
                    and not candidate_order.is_paid()
                ):
                    next_order_payload["status"] = REPAIR_ORDER_STATUS_OPEN
                    next_order_payload["closed_at"] = ""
                self._update_repair_order(
                    linked_card,
                    cards,
                    next_order_payload,
                    events,
                    actor_name,
                    source,
                    settings=settings,
                )
                self._touch_card(linked_card, actor_name)
                self._refresh_card_ai_fingerprint_if_agent_changed(linked_card, actor_name, source)
                if self._card_has_repair_order(linked_card):
                    self._ensure_repair_order_text_file(linked_card, force=True)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="cash_transaction_cancelled",
                message=f"{actor_name} отменил движение по кассе",
                card_id=linked_card.id if linked_card is not None else None,
                details={
                    "cash_transaction_id": transaction.id,
                    "cancellation_transaction_id": cancellation.id,
                    "cashbox_id": cashbox.id,
                    "cashbox_name": cashbox.name,
                    "direction": transaction.direction,
                    "amount_minor": transaction.amount_minor,
                    "amount_display": format_money_minor(transaction.amount_minor),
                    "reason": reason,
                    "repair_order_payment_id": linked_payment.id
                    if linked_payment is not None
                    else None,
                },
            )
            self._refresh_cashbox_updated_at(cashbox, transactions)
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=cards,
                cashboxes=cashboxes,
                cash_transactions=transactions,
                events=events,
                settings=settings,
            )
            return {
                "cashbox": self._serialize_cashbox(cashbox, transactions),
                "cancelled_transaction": self._serialize_cash_transaction(transaction),
                "cancellation_transaction": self._serialize_cash_transaction(cancellation),
                "meta": response_meta,
            }

    def _cancel_selected_cashbox_transfer_transaction(
        self,
        *,
        bundle: dict[str, Any],
        cashbox: CashBox,
        transaction: CashTransaction,
        reason: str,
        actor_name: str,
        source: str,
        expected_related_cashbox_updated_at: str,
        attestation_run_id: str,
    ) -> dict:
        transactions = bundle["cash_transactions"]
        cashboxes = bundle["cashboxes"]
        events = bundle["events"]
        related_transaction = self._find_cash_transaction(
            transactions, transaction.related_transaction_id
        )
        if related_transaction is None and transaction.transfer_group_id:
            group_peers = [
                candidate
                for candidate in transactions
                if candidate.id != transaction.id
                and candidate.transfer_group_id == transaction.transfer_group_id
                and normalize_text(candidate.transaction_kind, default="", limit=32).casefold()
                not in {
                    _CASH_TRANSACTION_KIND_CANCELLED,
                    _CASH_TRANSACTION_KIND_CANCELLATION,
                }
            ]
            related_transaction = group_peers[0] if len(group_peers) == 1 else None
        if (
            related_transaction is None
            or not transaction.transfer_group_id
            or transaction.transfer_group_id != related_transaction.transfer_group_id
        ):
            self._fail(
                "cashbox_transfer_pair_required",
                "Перемещение можно отменить только целиком. Для legacy-перемещения без связки нужна ручная сверка.",
                status_code=409,
                details={
                    "transaction_id": transaction.id,
                    "cashbox_id": cashbox.id,
                    "related_transaction_id": transaction.related_transaction_id,
                    "transfer_group_id": transaction.transfer_group_id,
                },
            )
        related_kind = normalize_text(
            related_transaction.transaction_kind,
            default="",
            limit=32,
        ).casefold()
        if related_kind in {
            _CASH_TRANSACTION_KIND_CANCELLED,
            _CASH_TRANSACTION_KIND_CANCELLATION,
        }:
            self._fail(
                "validation_error",
                "Связанная операция уже является отменой или уже отменена.",
                details={
                    "transaction_id": transaction.id,
                    "related_transaction_id": related_transaction.id,
                },
            )
        related_existing_cancellation = next(
            (
                item
                for item in transactions
                if item.related_transaction_id == related_transaction.id
                and item.transaction_kind == _CASH_TRANSACTION_KIND_CANCELLATION
            ),
            None,
        )
        if related_existing_cancellation is not None:
            self._fail(
                "validation_error",
                "Связанная операция уже отменена.",
                details={
                    "transaction_id": transaction.id,
                    "related_transaction_id": related_transaction.id,
                    "related_cancellation_transaction_id": related_existing_cancellation.id,
                },
            )
        if (
            related_transaction.direction == transaction.direction
            or related_transaction.amount_minor != transaction.amount_minor
        ):
            self._fail(
                "cashbox_transfer_pair_mismatch",
                "Парные операции перемещения не совпадают. Нужна ручная сверка.",
                status_code=409,
                details={
                    "transaction_id": transaction.id,
                    "related_transaction_id": related_transaction.id,
                    "direction": transaction.direction,
                    "related_direction": related_transaction.direction,
                    "amount_minor": transaction.amount_minor,
                    "related_amount_minor": related_transaction.amount_minor,
                },
            )
        related_cashbox = self._find_cashbox(cashboxes, related_transaction.cashbox_id)
        if attestation_run_id and not (
            related_cashbox.name.startswith(f"{attestation_run_id}-")
            and attestation_run_id in related_transaction.note
            and related_transaction.amount_minor == 100
        ):
            self._fail(
                "cash_cancellation_attestation_scope_invalid",
                "Синтетическая отмена не соответствует контуру аттестации.",
                status_code=403,
            )
        if not expected_related_cashbox_updated_at:
            self._fail(
                "cashbox_transfer_related_revision_required",
                "Для отмены перемещения нужно перечитать связанную кассу.",
                status_code=409,
                details={"related_cashbox_id": related_cashbox.id},
            )
        if related_cashbox.updated_at != expected_related_cashbox_updated_at:
            self._fail(
                "cashbox_update_conflict",
                "Связанная касса уже изменилась. Обновите данные и повторите действие.",
                status_code=409,
                details={"cashbox_id": related_cashbox.id},
            )
        cancellation_note = self._cash_cancellation_note(reason)
        cancellation_created_at = (
            model_helpers.utc_now().astimezone(business_timezone()).isoformat()
        )
        cancellation = self._append_cash_transaction(
            transactions=transactions,
            cashbox=cashbox,
            direction="expense" if transaction.direction == "income" else "income",
            amount_minor=transaction.amount_minor,
            note=cancellation_note,
            actor_name=actor_name,
            source=source,
            created_at=cancellation_created_at,
            transaction_kind=_CASH_TRANSACTION_KIND_CANCELLATION,
            transfer_group_id=transaction.transfer_group_id,
            related_transaction_id=transaction.id,
        )
        related_cancellation = self._append_cash_transaction(
            transactions=transactions,
            cashbox=related_cashbox,
            direction="expense" if related_transaction.direction == "income" else "income",
            amount_minor=related_transaction.amount_minor,
            note=cancellation_note,
            actor_name=actor_name,
            source=source,
            created_at=cancellation_created_at,
            transaction_kind=_CASH_TRANSACTION_KIND_CANCELLATION,
            transfer_group_id=transaction.transfer_group_id,
            related_transaction_id=related_transaction.id,
        )
        transaction.transaction_kind = _CASH_TRANSACTION_KIND_CANCELLED
        related_transaction.transaction_kind = _CASH_TRANSACTION_KIND_CANCELLED
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="cashbox_transfer_transaction_cancelled",
            message=f"{actor_name} отменил перемещение между кассами",
            card_id=None,
            details={
                "cash_transaction_id": transaction.id,
                "cancellation_transaction_id": cancellation.id,
                "related_cash_transaction_id": related_transaction.id,
                "related_cancellation_transaction_id": related_cancellation.id,
                "cashbox_id": cashbox.id,
                "cashbox_name": cashbox.name,
                "related_cashbox_id": related_cashbox.id,
                "related_cashbox_name": related_cashbox.name,
                "transfer_group_id": transaction.transfer_group_id,
                "amount_minor": transaction.amount_minor,
                "amount_display": format_money_minor(transaction.amount_minor),
                "reason": reason,
            },
        )
        self._refresh_cashbox_updated_at(cashbox, transactions)
        self._refresh_cashbox_updated_at(related_cashbox, transactions)
        self._save_bundle(
            bundle,
            columns=bundle["columns"],
            cards=bundle["cards"],
            cashboxes=cashboxes,
            cash_transactions=transactions,
            events=events,
        )
        response_meta = {
            "cancelled": True,
            "cancelled_pair": True,
            "transaction_id": transaction.id,
            "related_transaction_id": related_transaction.id,
            "cancellation_transaction_id": cancellation.id,
            "related_cancellation_transaction_id": related_cancellation.id,
            "cashbox_id": cashbox.id,
            "related_cashbox_id": related_cashbox.id,
            "transfer_group_id": transaction.transfer_group_id,
            "reason": reason,
        }
        return {
            "cashbox": self._serialize_cashbox(cashbox, transactions),
            "related_cashbox": self._serialize_cashbox(related_cashbox, transactions),
            "cancelled_transaction": self._serialize_cash_transaction(transaction),
            "related_cancelled_transaction": self._serialize_cash_transaction(related_transaction),
            "cancellation_transaction": self._serialize_cash_transaction(cancellation),
            "related_cancellation_transaction": self._serialize_cash_transaction(
                related_cancellation
            ),
            "meta": response_meta,
        }

    def _validated_cash_cancellation_reason(self, payload: dict[str, Any]) -> str:
        reason = normalize_text(
            payload.get("reason") or payload.get("cancel_reason") or payload.get("note"),
            default="",
            limit=240,
        )
        if len(reason) < _CASH_CANCEL_REASON_MIN_CHARS:
            self._fail(
                "validation_error",
                "Для отмены платежа нужно указать причину не короче 10 символов.",
                details={"field": "reason", "min_length": _CASH_CANCEL_REASON_MIN_CHARS},
            )
        return reason

    def _cash_cancellation_note(self, reason: str) -> str:
        return self._validated_cash_transaction_note(f"Отмена платежа: {reason}")
