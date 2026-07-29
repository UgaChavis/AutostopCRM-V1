from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .. import models as model_helpers
from ..models import (
    Card,
    CashBox,
    CashTransaction,
    business_timezone,
    format_money_minor,
    normalize_actor_name,
    normalize_cash_direction,
    normalize_money_minor,
    normalize_text,
    parse_business_datetime,
    parse_datetime,
    short_entity_id,
)
from ..repair_order import (
    REPAIR_ORDER_STATUS_CLOSED,
    REPAIR_ORDER_STATUS_OPEN,
    RepairOrder,
    RepairOrderPayment,
)
from .card_service_cashbox_cancellation import (
    _CASH_TRANSACTION_KIND_CANCELLATION,
    _CASH_TRANSACTION_KIND_CANCELLED,
    CardServiceCashboxCancellationMixin,
)
from .finance_read_core import CASHBOX_NOTIFICATION_SEEN_SETTING_KEY
from .payroll_constants import EMPLOYEE_SHIFT_ACCRUAL_NOTE

EMPLOYEES_SETTING_KEY = "employees"
EMPLOYEE_SHIFT_ACCRUALS_SETTING_KEY = "employee_shift_accruals"
_CASH_EXPENSE_NOTE_MIN_CHARS = 10
_CASHBOX_NOTIFICATION_UNREAD_LIMIT = 500
_GATEWAY_ATTESTATION_RUN_RE = re.compile(r"^AST-GWAT-\d{8}T\d{6}Z$")
_MAX_REGULAR_CASHBOXES = 6
_MAX_GATEWAY_ATTESTATION_CASHBOXES = 2


class CardServiceFinanceMixin(CardServiceCashboxCancellationMixin):
    def list_cashboxes(self, payload: dict | None = None) -> dict:
        return self._finance_read_core.list_cashboxes(payload)

    def get_cashbox(self, payload: dict | None = None) -> dict:
        return self._finance_read_core.get_cashbox(payload)

    def get_cash_journal(self, payload: dict | None = None) -> dict:
        return self._finance_read_core.get_cash_journal(payload)

    def get_finance_audit(self, payload: dict | None = None) -> dict:
        return self._finance_read_core.get_finance_audit(payload)

    def mark_cashbox_notifications_seen(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            actor_name = normalize_actor_name(payload.get("actor_name"), default="")
            if not actor_name:
                self._fail(
                    "validation_error",
                    "Нужно определить пользователя, который просмотрел кассы.",
                    details={"field": "actor_name"},
                )
            bundle = self._store.read_bundle()
            transactions = bundle["cash_transactions"]
            requested_transaction_id = normalize_text(
                payload.get("through_transaction_id"), default="", limit=128
            )
            through_transaction = (
                self._find_cash_transaction(transactions, requested_transaction_id)
                if requested_transaction_id
                else self._cashbox_notification_latest_transaction(transactions)
            )
            if requested_transaction_id and through_transaction is None:
                self._fail(
                    "not_found",
                    "Движение кассы для отметки прочтения не найдено.",
                    status_code=404,
                    details={"through_transaction_id": requested_transaction_id},
                )
            settings = dict(bundle["settings"])
            seen_by_users = self._cashbox_notification_seen_by_users(settings)
            actor_key = actor_name.casefold()
            current_receipt = seen_by_users.get(actor_key)
            next_receipt = self._cashbox_notification_receipt(through_transaction)
            changed = current_receipt is None or self._cashbox_notification_receipt_key(
                next_receipt
            ) > self._cashbox_notification_receipt_key(current_receipt)
            if changed:
                seen_by_users[actor_key] = next_receipt
                settings[CASHBOX_NOTIFICATION_SEEN_SETTING_KEY] = seen_by_users
                bundle["settings"] = settings
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    cashboxes=bundle["cashboxes"],
                    cash_transactions=transactions,
                    events=bundle["events"],
                    settings=settings,
                )
            return {
                "notification": self._cashbox_notification_summary(bundle, payload),
                "meta": {
                    "changed": changed,
                    "through_transaction_id": str(next_receipt.get("transaction_id") or ""),
                },
            }

    def apply_finance_audit_safe_fixes(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            dry_run = self._validated_optional_bool(payload, "dry_run", default=True)
            requested_issue_ids = payload.get("issue_ids")
            selected_issue_ids: set[str] | None = None
            if requested_issue_ids is not None:
                if not isinstance(requested_issue_ids, list):
                    self._fail(
                        "validation_error",
                        "Поле issue_ids должно быть массивом.",
                        details={"field": "issue_ids"},
                    )
                selected_issue_ids = {
                    normalize_text(item, default="", limit=240)
                    for item in requested_issue_ids
                    if normalize_text(item, default="", limit=240)
                }
            actor_name, source = self._audit_identity(payload, default_source="api")
            bundle = self._store.read_bundle()
            audit = self._build_finance_audit(bundle)
            expected_issue_ids = payload.get("expected_issue_ids")
            current_issue_ids = [str(issue.get("id") or "") for issue in audit["issues"]]
            if expected_issue_ids is not None:
                if (
                    not isinstance(expected_issue_ids, list)
                    or any(
                        not isinstance(item, str) or not item.strip() for item in expected_issue_ids
                    )
                    or len(set(expected_issue_ids)) != len(expected_issue_ids)
                ):
                    self._fail(
                        "validation_error",
                        "Поле expected_issue_ids должно содержать упорядоченные ID проблем.",
                        details={"field": "expected_issue_ids"},
                    )
                if expected_issue_ids != current_issue_ids:
                    self._fail(
                        "finance_audit_snapshot_conflict",
                        "Финансовая сверка уже изменилась. Обновите её и повторите действие.",
                        status_code=409,
                        details={
                            "expected_count": len(expected_issue_ids),
                            "current_count": len(current_issue_ids),
                        },
                    )
            safe_issues = [
                issue
                for issue in audit["issues"]
                if issue.get("safe_fix_available")
                and (selected_issue_ids is None or str(issue.get("id") or "") in selected_issue_ids)
            ]
            planned_fixes = [issue["safe_fix"] for issue in safe_issues if issue.get("safe_fix")]
            attestation_run_id = normalize_text(
                payload.get("attestation_run_id"),
                default="",
                limit=64,
            )
            if attestation_run_id:
                selected_issue = safe_issues[0] if len(safe_issues) == 1 else None
                selected_fix = (
                    selected_issue.get("safe_fix")
                    if isinstance(selected_issue, dict)
                    and isinstance(selected_issue.get("safe_fix"), dict)
                    else {}
                )
                transaction_id = str((selected_issue or {}).get("cash_transaction_id") or "")
                transaction = next(
                    (item for item in bundle["cash_transactions"] if item.id == transaction_id),
                    None,
                )
                cashbox = next(
                    (
                        item
                        for item in bundle["cashboxes"]
                        if transaction is not None and item.id == transaction.cashbox_id
                    ),
                    None,
                )
                employee_name = str(selected_fix.get("employee_name") or "")
                if not (
                    _GATEWAY_ATTESTATION_RUN_RE.fullmatch(attestation_run_id)
                    and isinstance(requested_issue_ids, list)
                    and len(requested_issue_ids) == 1
                    and selected_issue is not None
                    and selected_issue.get("code") == "salary_transaction_missing_employee"
                    and selected_fix.get("kind") == "restore_missing_employee"
                    and employee_name.startswith(f"{attestation_run_id}-")
                    and transaction is not None
                    and cashbox is not None
                    and cashbox.name.startswith(f"{attestation_run_id}-")
                    and transaction.note.startswith(attestation_run_id)
                    and transaction.amount_minor == 100
                    and transaction.transaction_kind == "salary_payout"
                    and str(payload.get("source") or "").strip().casefold()
                    == "mcp_agent_gateway_v2"
                    and actor_name
                ):
                    self._fail(
                        "finance_audit_attestation_scope_invalid",
                        "Безопасная правка не соответствует синтетическому контуру аттестации.",
                        status_code=403,
                    )
            if dry_run:
                return {
                    "issues": audit["issues"],
                    "summary": audit["summary"],
                    "safe_fixes": planned_fixes,
                    "meta": {
                        "dry_run": True,
                        "changed": False,
                        "planned": len(planned_fixes),
                    },
                }

            transactions_by_id = {
                transaction.id: transaction for transaction in bundle["cash_transactions"]
            }
            payment_links = self._finance_payment_links(bundle["cards"])
            applied: list[dict[str, object]] = []
            for fix in planned_fixes:
                if not isinstance(fix, dict):
                    continue
                kind = normalize_text(fix.get("kind"), default="", limit=64)
                if kind == "restore_missing_employee":
                    employee_id = normalize_text(fix.get("employee_id"), default="", limit=64)
                    employee_name = normalize_text(fix.get("employee_name"), default="", limit=80)
                    if not employee_id or not employee_name:
                        continue
                    existing_employees = self._employees_from_settings(bundle["settings"])
                    if any(employee["id"] == employee_id for employee in existing_employees):
                        continue
                    restored_employee = self._normalized_employee_record(
                        {
                            "id": employee_id,
                            "name": employee_name,
                            "is_active": False,
                            "note": "Восстановлен автоматически из зарплатного движения.",
                        }
                    )
                    if restored_employee is None:
                        continue
                    next_employees = [
                        employee
                        for employee in existing_employees
                        if employee["id"] != restored_employee["id"]
                    ]
                    next_employees.append(restored_employee)
                    next_employees.sort(
                        key=lambda item: (
                            not item["is_active"],
                            item["name"].casefold(),
                            item["id"],
                        )
                    )
                    bundle["settings"][EMPLOYEES_SETTING_KEY] = next_employees
                    applied.append(
                        {
                            "kind": kind,
                            "employee_id": employee_id,
                            "employee_name": employee_name,
                            "is_active": False,
                        }
                    )
                    continue
                transaction_id = normalize_text(
                    fix.get("cash_transaction_id"), default="", limit=128
                )
                transaction = transactions_by_id.get(transaction_id)
                if transaction is None or transaction_id not in payment_links:
                    continue
                if kind == "set_transaction_kind":
                    value = normalize_text(fix.get("value"), default="", limit=32)
                    if value != "repair_order_payment":
                        continue
                    if transaction.transaction_kind == value:
                        continue
                    before = transaction.transaction_kind
                    transaction.transaction_kind = value
                    applied.append(
                        {
                            "kind": kind,
                            "cash_transaction_id": transaction.id,
                            "before": before,
                            "after": value,
                        }
                    )
                elif kind == "refresh_default_note":
                    value = normalize_text(fix.get("value"), default="", limit=240)
                    if not value or not self._is_default_repair_order_cash_transaction_note(
                        transaction.note
                    ):
                        continue
                    if transaction.note == value:
                        continue
                    before = transaction.note
                    transaction.note = value
                    applied.append(
                        {
                            "kind": kind,
                            "cash_transaction_id": transaction.id,
                            "before": before,
                            "after": value,
                        }
                    )
            if applied:
                self._append_event(
                    bundle["events"],
                    actor_name=actor_name,
                    source=source,
                    action="finance_audit_safe_fix_applied",
                    message=f"{actor_name} применил безопасные правки финансовой сверки",
                    card_id=None,
                    details={"applied": applied, "count": len(applied)},
                )
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    cashboxes=bundle["cashboxes"],
                    cash_transactions=bundle["cash_transactions"],
                    events=bundle["events"],
                    settings=bundle["settings"],
                )
            next_audit = self._build_finance_audit(bundle)
            return {
                "issues": next_audit["issues"],
                "summary": next_audit["summary"],
                "safe_fixes": planned_fixes,
                "applied": applied,
                "meta": {
                    "dry_run": False,
                    "changed": bool(applied),
                    "planned": len(planned_fixes),
                    "applied": len(applied),
                },
            }

    def create_cashbox(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = self._ordered_cashboxes(bundle["cashboxes"])
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            expected_cashbox_ids = payload.get("expected_cashbox_ids")
            if expected_cashbox_ids is not None:
                if not isinstance(expected_cashbox_ids, list) or any(
                    not isinstance(item, str) or not item.strip() for item in expected_cashbox_ids
                ):
                    self._fail(
                        "validation_error",
                        "Поле expected_cashbox_ids должно содержать ID касс.",
                        details={"field": "expected_cashbox_ids"},
                    )
                current_cashbox_ids = [item.id for item in cashboxes]
                if expected_cashbox_ids != current_cashbox_ids:
                    self._fail(
                        "cashbox_snapshot_conflict",
                        "Список касс уже изменился. Обновите его и повторите создание.",
                        status_code=409,
                        details={
                            "expected_count": len(expected_cashbox_ids),
                            "current_count": len(current_cashbox_ids),
                        },
                    )
            requested_name = normalize_text(payload.get("name"), default="", limit=80)
            attestation_run_id = normalize_text(
                payload.get("attestation_run_id"),
                default="",
                limit=64,
            )
            attestation_mode = bool(
                _GATEWAY_ATTESTATION_RUN_RE.fullmatch(attestation_run_id)
                and requested_name.startswith(f"{attestation_run_id}-")
                and source == "mcp"
                and actor_name
            )
            existing_attestation_cashboxes = [
                item for item in cashboxes if item.name.startswith(f"{attestation_run_id}-")
            ]
            attestation_capacity_available = bool(
                attestation_mode
                and len(cashboxes) < _MAX_REGULAR_CASHBOXES + _MAX_GATEWAY_ATTESTATION_CASHBOXES
                and len(existing_attestation_cashboxes) < _MAX_GATEWAY_ATTESTATION_CASHBOXES
            )
            if len(cashboxes) >= _MAX_REGULAR_CASHBOXES and not attestation_capacity_available:
                raise ValueError("Нельзя создать больше 6 касс.")
            now_iso = model_helpers.utc_now_iso()
            cashbox = CashBox(
                id=str(uuid.uuid4()),
                name=self._validated_cashbox_name(requested_name, cashboxes),
                order=len(cashboxes),
                created_at=now_iso,
                updated_at=now_iso,
            )
            cashboxes.append(cashbox)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="cashbox_created",
                message=f"{actor_name} создал кассу",
                card_id=None,
                details={"cashbox_id": cashbox.id, "cashbox_name": cashbox.name},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=cashboxes,
                cash_transactions=transactions,
                events=events,
            )
            return {"cashbox": self._serialize_cashbox(cashbox, transactions)}

    def reorder_cashboxes(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = self._ordered_cashboxes(bundle["cashboxes"])
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            expected_cashbox_ids = payload.get("expected_cashbox_ids")
            if expected_cashbox_ids is not None:
                if (
                    not isinstance(expected_cashbox_ids, list)
                    or any(
                        not isinstance(item, str) or not item.strip()
                        for item in expected_cashbox_ids
                    )
                    or len(set(expected_cashbox_ids)) != len(expected_cashbox_ids)
                ):
                    self._fail(
                        "validation_error",
                        "Поле expected_cashbox_ids должно содержать упорядоченные ID касс.",
                        details={"field": "expected_cashbox_ids"},
                    )
                current_cashbox_ids = [item.id for item in cashboxes]
                if expected_cashbox_ids != current_cashbox_ids:
                    self._fail(
                        "cashbox_order_conflict",
                        "Порядок касс уже изменился. Обновите список и повторите действие.",
                        status_code=409,
                        details={
                            "expected_count": len(expected_cashbox_ids),
                            "current_count": len(current_cashbox_ids),
                        },
                    )
            cashbox = self._find_cashbox(cashboxes, payload.get("cashbox_id"))
            before_cashbox_id = (
                payload.get("before_cashbox_id")
                or payload.get("before_id")
                or payload.get("target_cashbox_id")
            )
            if before_cashbox_id and str(before_cashbox_id).strip() == cashbox.id:
                return {
                    "cashboxes": [
                        self._serialize_cashbox(item, transactions) for item in cashboxes
                    ],
                    "cashbox": self._serialize_cashbox(cashbox, transactions),
                    "meta": {
                        "changed": False,
                        "total": len(cashboxes),
                    },
                }
            reordered_cashboxes, changed = self._reposition_cashbox(
                cashboxes,
                cashbox,
                before_cashbox_id=before_cashbox_id,
            )
            if changed:
                before_cashbox = None
                if before_cashbox_id:
                    before_cashbox = self._find_cashbox(reordered_cashboxes, before_cashbox_id)
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="cashbox_reordered",
                    message=f"{actor_name} изменил порядок касс",
                    card_id=None,
                    details={
                        "cashbox_id": cashbox.id,
                        "cashbox_name": cashbox.name,
                        "before_cashbox_id": before_cashbox.id if before_cashbox else None,
                        "before_cashbox_name": before_cashbox.name if before_cashbox else None,
                    },
                )
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    cashboxes=reordered_cashboxes,
                    cash_transactions=transactions,
                    events=events,
                )
            return {
                "cashboxes": [
                    self._serialize_cashbox(item, transactions) for item in reordered_cashboxes
                ],
                "cashbox": self._serialize_cashbox(cashbox, transactions),
                "meta": {
                    "changed": changed,
                    "total": len(reordered_cashboxes),
                },
            }

    def create_cashbox_transfer(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = self._ordered_cashboxes(bundle["cashboxes"])
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            source_cashbox = self._find_cashbox(
                cashboxes, payload.get("from_cashbox_id") or payload.get("cashbox_id")
            )
            target_cashbox = self._find_cashbox(
                cashboxes, payload.get("to_cashbox_id") or payload.get("target_cashbox_id")
            )
            expected_from_updated_at = normalize_text(
                payload.get("expected_from_updated_at"),
                default="",
                limit=80,
            )
            expected_to_updated_at = normalize_text(
                payload.get("expected_to_updated_at"),
                default="",
                limit=80,
            )
            revision_conflicts = [
                cashbox.id
                for cashbox, expected in (
                    (source_cashbox, expected_from_updated_at),
                    (target_cashbox, expected_to_updated_at),
                )
                if expected and expected != cashbox.updated_at
            ]
            if revision_conflicts:
                self._fail(
                    "cashbox_update_conflict",
                    "Одна из касс уже изменилась. Обновите обе кассы и повторите перевод.",
                    status_code=409,
                    details={"cashbox_ids": revision_conflicts},
                )
            if source_cashbox.id == target_cashbox.id:
                self._fail(
                    "validation_error",
                    "Нельзя переместить деньги в ту же кассу.",
                    details={"field": "to_cashbox_id"},
                )
            amount_minor = self._validated_cash_amount_minor(payload)
            base_note = self._validated_cash_transaction_note(payload.get("note"))
            transfer_out_note = f"Перемещение в {target_cashbox.name}"
            transfer_in_note = f"Перемещение из {source_cashbox.name}"
            if base_note:
                transfer_out_note = f"{transfer_out_note}: {base_note}"
                transfer_in_note = f"{transfer_in_note}: {base_note}"
            transfer_created_at = (
                model_helpers.utc_now().astimezone(business_timezone()).isoformat()
            )
            transfer_group_id = str(uuid.uuid4())
            source_transaction = self._append_cash_transaction(
                transactions=transactions,
                cashbox=source_cashbox,
                direction="expense",
                amount_minor=amount_minor,
                note=transfer_out_note,
                actor_name=actor_name,
                source=source,
                created_at=transfer_created_at,
                transfer_group_id=transfer_group_id,
            )
            target_transaction = self._append_cash_transaction(
                transactions=transactions,
                cashbox=target_cashbox,
                direction="income",
                amount_minor=amount_minor,
                note=transfer_in_note,
                actor_name=actor_name,
                source=source,
                created_at=transfer_created_at,
                transfer_group_id=transfer_group_id,
            )
            source_transaction.related_transaction_id = target_transaction.id
            target_transaction.related_transaction_id = source_transaction.id
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="cashbox_transfer_created",
                message=f"{actor_name} переместил деньги между кассами",
                card_id=None,
                details={
                    "from_cashbox_id": source_cashbox.id,
                    "from_cashbox_name": source_cashbox.name,
                    "to_cashbox_id": target_cashbox.id,
                    "to_cashbox_name": target_cashbox.name,
                    "amount_minor": amount_minor,
                    "amount_display": format_money_minor(amount_minor),
                    "note": base_note,
                    "source_transaction_id": source_transaction.id,
                    "target_transaction_id": target_transaction.id,
                    "transfer_group_id": transfer_group_id,
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=cashboxes,
                cash_transactions=transactions,
                events=events,
            )
            return {
                "from_cashbox": self._serialize_cashbox(source_cashbox, transactions),
                "to_cashbox": self._serialize_cashbox(target_cashbox, transactions),
                "source_transaction": self._serialize_cash_transaction(source_transaction),
                "target_transaction": self._serialize_cash_transaction(target_transaction),
            }

    def delete_cashbox(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = self._ordered_cashboxes(bundle["cashboxes"])
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            cashbox = self._find_cashbox(cashboxes, payload.get("cashbox_id"))
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
            related_transactions = self._cashbox_transactions(transactions, cashbox.id)
            expected_transaction_ids = payload.get("expected_transaction_ids")
            if expected_transaction_ids is not None:
                if (
                    not isinstance(expected_transaction_ids, list)
                    or any(
                        not isinstance(item, str) or not item.strip()
                        for item in expected_transaction_ids
                    )
                    or len(set(expected_transaction_ids)) != len(expected_transaction_ids)
                ):
                    self._fail(
                        "validation_error",
                        "Поле expected_transaction_ids должно содержать ID движений кассы.",
                        details={"field": "expected_transaction_ids"},
                    )
                current_transaction_ids = [transaction.id for transaction in related_transactions]
                if expected_transaction_ids != current_transaction_ids:
                    self._fail(
                        "cashbox_transaction_snapshot_conflict",
                        "Журнал кассы уже изменился. Обновите данные и повторите действие.",
                        status_code=409,
                        details={"cashbox_id": cashbox.id},
                    )
            statistics = self._cashbox_statistics(cashbox, transactions)
            attestation_run_id = normalize_text(
                payload.get("attestation_run_id"),
                default="",
                limit=64,
            )
            attestation_cleanup = bool(attestation_run_id)
            if attestation_cleanup and statistics["balance_minor"] != 0:
                self._fail(
                    "cashbox_attestation_balance_not_zero",
                    "Синтетическую кассу можно удалить только с нулевым остатком.",
                    status_code=409,
                    details={"cashbox_id": cashbox.id},
                )
            payment_links = self._finance_payment_links(bundle["cards"])
            related_ids = {transaction.id for transaction in related_transactions}
            linked_payment_ids = related_ids.intersection(payment_links)
            peer_transactions = [
                transaction
                for transaction in transactions
                if transaction.id not in related_ids
                and (
                    transaction.related_transaction_id in related_ids
                    or any(
                        peer_id and transaction.id == peer_id
                        for peer_id in (
                            candidate.related_transaction_id for candidate in related_transactions
                        )
                    )
                )
            ]
            peer_cashboxes = {item.id: item for item in cashboxes if item.id != cashbox.id}
            if attestation_cleanup and not (
                _GATEWAY_ATTESTATION_RUN_RE.fullmatch(attestation_run_id)
                and expected_cashbox_updated_at
                and isinstance(expected_transaction_ids, list)
                and cashbox.name.startswith(f"{attestation_run_id}-")
                and not linked_payment_ids
                and all(
                    transaction.amount_minor == 100 and attestation_run_id in transaction.note
                    for transaction in related_transactions
                )
                and all(
                    peer.amount_minor == 100
                    and attestation_run_id in peer.note
                    and peer.cashbox_id in peer_cashboxes
                    and peer_cashboxes[peer.cashbox_id].name.startswith(f"{attestation_run_id}-")
                    for peer in peer_transactions
                )
                and str(payload.get("source") or "").strip().casefold() == "mcp_agent_gateway_v2"
                and actor_name
            ):
                self._fail(
                    "cashbox_delete_attestation_scope_invalid",
                    "Удаление кассы не соответствует синтетическому контуру аттестации.",
                    status_code=403,
                )
            if related_transactions and not attestation_cleanup:
                raise ValueError("Нельзя удалить кассу, пока в ней есть движения.")
            remaining_cashboxes = self._ordered_cashboxes(
                [item for item in cashboxes if item.id != cashbox.id]
            )
            self._renumber_cashboxes(remaining_cashboxes)
            remaining_transactions = [
                item for item in transactions if item.cashbox_id != cashbox.id
            ]
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="cashbox_deleted",
                message=f"{actor_name} удалил кассу",
                card_id=None,
                details={
                    "cashbox_id": cashbox.id,
                    "cashbox_name": cashbox.name,
                    "transactions_total": len(related_transactions),
                    "balance_minor": statistics["balance_minor"],
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=remaining_cashboxes,
                cash_transactions=remaining_transactions,
                events=events,
            )
            return {
                "cashbox": self._serialize_cashbox(cashbox, transactions),
                "meta": {
                    "deleted": True,
                    "removed_transactions": len(related_transactions),
                    "attestation_cleanup": attestation_cleanup,
                },
            }

    def delete_gateway_attestation_payment_fixture(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            cashboxes = bundle["cashboxes"]
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            card = self._find_card(cards, payload.get("card_id"))
            expected_updated_at = normalize_text(
                payload.get("expected_updated_at"), default="", limit=80
            )
            if not expected_updated_at or card.updated_at != expected_updated_at:
                self._fail(
                    "card_update_conflict",
                    "Карточка уже изменилась. Обновите данные и повторите действие.",
                    status_code=409,
                    details={"card_id": card.id},
                )
            payment_id = normalize_text(payload.get("payment_id"), default="", limit=128)
            payment = next(
                (item for item in card.repair_order.payments if item.id == payment_id),
                None,
            )
            if payment is None:
                self._fail(
                    "not_found",
                    "Синтетическая оплата не найдена.",
                    status_code=404,
                    details={"card_id": card.id, "payment_id": payment_id},
                )
            cashbox = self._find_cashbox(cashboxes, payment.cashbox_id)
            expected_cashbox_updated_at = normalize_text(
                payload.get("expected_cashbox_updated_at"),
                default="",
                limit=80,
            )
            if not expected_cashbox_updated_at or cashbox.updated_at != expected_cashbox_updated_at:
                self._fail(
                    "cashbox_update_conflict",
                    "Касса уже изменилась. Обновите данные и повторите действие.",
                    status_code=409,
                    details={"cashbox_id": cashbox.id},
                )
            expected_transaction_ids = payload.get("expected_transaction_ids")
            if (
                not isinstance(expected_transaction_ids, list)
                or not expected_transaction_ids
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in expected_transaction_ids
                )
                or len(set(expected_transaction_ids)) != len(expected_transaction_ids)
            ):
                self._fail(
                    "validation_error",
                    "Нужен точный снимок синтетических движений.",
                    details={"field": "expected_transaction_ids"},
                )
            attestation_run_id = normalize_text(
                payload.get("attestation_run_id"), default="", limit=64
            )
            scoped_transactions = [
                item
                for item in transactions
                if item.cashbox_id == cashbox.id and attestation_run_id in item.note
            ]
            scoped_ids = {item.id for item in scoped_transactions}
            if scoped_ids != set(expected_transaction_ids):
                self._fail(
                    "cashbox_transaction_snapshot_conflict",
                    "Синтетический журнал уже изменился. Перечитайте кассу.",
                    status_code=409,
                    details={"cashbox_id": cashbox.id},
                )
            payment_links = self._finance_payment_links(cards)
            linked_scoped = {
                transaction_id: link
                for transaction_id, link in payment_links.items()
                if transaction_id in scoped_ids
            }
            before_balance = int(self._cashbox_statistics(cashbox, transactions)["balance_minor"])
            removed_effect_minor = sum(
                item.amount_minor if item.direction == "income" else -item.amount_minor
                for item in scoped_transactions
            )
            remaining_transactions = [item for item in transactions if item.id not in scoped_ids]
            after_balance = int(
                self._cashbox_statistics(cashbox, remaining_transactions)["balance_minor"]
            )
            current_transaction = self._find_cash_transaction(
                transactions, payment.cash_transaction_id
            )
            if not (
                _GATEWAY_ATTESTATION_RUN_RE.fullmatch(attestation_run_id)
                and card.title.startswith(attestation_run_id)
                and payment.note.startswith(attestation_run_id)
                and normalize_money_minor(payment.amount) == 100
                and current_transaction is not None
                and current_transaction.id in scoped_ids
                and current_transaction.transaction_kind == "repair_order_payment"
                and all(
                    item.amount_minor == 100
                    and attestation_run_id in item.note
                    and (
                        not item.related_transaction_id or item.related_transaction_id in scoped_ids
                    )
                    for item in scoped_transactions
                )
                and set(linked_scoped) == {current_transaction.id}
                and linked_scoped[current_transaction.id][0].id == card.id
                and linked_scoped[current_transaction.id][1].id == payment.id
                and removed_effect_minor == 100
                and before_balance - after_balance == removed_effect_minor
                and str(payload.get("source") or "").strip().casefold() == "mcp_agent_gateway_v2"
                and actor_name
            ):
                self._fail(
                    "gateway_attestation_payment_cleanup_scope_invalid",
                    "Очистка оплаты не соответствует синтетическому контуру.",
                    status_code=403,
                )
            card.repair_order = RepairOrder()
            self._touch_card(card, actor_name)
            if self._card_has_repair_order(card):
                self._ensure_repair_order_text_file(card, force=True)
            self._refresh_cashbox_updated_at(cashbox, remaining_transactions)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="gateway_attestation_payment_fixture_deleted",
                message=f"{actor_name} удалил синтетический финансовый контур",
                card_id=card.id,
                details={
                    "attestation_run_id": attestation_run_id,
                    "payment_id": payment.id,
                    "cashbox_id": cashbox.id,
                    "removed_transaction_ids": sorted(scoped_ids),
                    "removed_effect_minor": removed_effect_minor,
                    "balance_minor_before": before_balance,
                    "balance_minor_after": after_balance,
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=cards,
                cashboxes=cashboxes,
                cash_transactions=remaining_transactions,
                events=events,
            )
            return {
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(bundle["columns"]),
                    include_removed_attachments=True,
                ),
                "cashbox": self._serialize_cashbox(cashbox, remaining_transactions),
                "meta": {
                    "deleted": True,
                    "payment_id": payment.id,
                    "cashbox_id": cashbox.id,
                    "removed_transaction_ids": sorted(scoped_ids),
                    "removed_effect_minor": removed_effect_minor,
                    "balance_minor_before": before_balance,
                    "balance_minor_after": after_balance,
                },
            }

    def create_cash_transaction(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = bundle["cashboxes"]
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            cashbox = self._find_cashbox(cashboxes, payload.get("cashbox_id"))
            expected_updated_at = normalize_text(
                payload.get("expected_updated_at"),
                default="",
                limit=80,
            )
            if expected_updated_at and expected_updated_at != cashbox.updated_at:
                self._fail(
                    "cashbox_update_conflict",
                    "Касса уже изменилась. Обновите её и повторите операцию.",
                    status_code=409,
                    details={
                        "cashbox_id": cashbox.id,
                        "expected_updated_at": expected_updated_at,
                        "current_updated_at": cashbox.updated_at,
                    },
                )
            note = self._validated_cash_transaction_note(payload.get("note"))
            direction = normalize_cash_direction(payload.get("direction"), default="income")
            transaction_kind = normalize_text(payload.get("transaction_kind"), default="", limit=32)
            if direction == "expense" and len(note) < _CASH_EXPENSE_NOTE_MIN_CHARS:
                self._fail(
                    "validation_error",
                    "Для списания нужно указать комментарий не короче 10 символов.",
                    details={"field": "note", "min_length": _CASH_EXPENSE_NOTE_MIN_CHARS},
                )
            if (
                self._is_default_repair_order_cash_transaction_note(note)
                and transaction_kind != "repair_order_payment"
            ):
                self._fail(
                    "manual_repair_order_cash_note_blocked",
                    "Поступления вида «Заказ-наряд №...» нужно создавать через оплату заказ-наряда.",
                    details={"field": "note"},
                )
            transaction = self._append_cash_transaction(
                transactions=transactions,
                cashbox=cashbox,
                direction=direction,
                amount_minor=self._validated_cash_amount_minor(payload),
                note=note,
                actor_name=actor_name,
                source=source,
                employee_id=normalize_text(payload.get("employee_id"), default="", limit=64),
                employee_name=normalize_text(payload.get("employee_name"), default="", limit=80),
                transaction_kind=transaction_kind,
            )
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="cash_transaction_created",
                message=f"{actor_name} добавил движение по кассе",
                card_id=None,
                details={
                    "cashbox_id": cashbox.id,
                    "cashbox_name": cashbox.name,
                    "direction": transaction.direction,
                    "amount_minor": transaction.amount_minor,
                    "amount_display": format_money_minor(transaction.amount_minor),
                    "note": transaction.note,
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=cashboxes,
                cash_transactions=transactions,
                events=events,
            )
            return {
                "cashbox": self._serialize_cashbox(cashbox, transactions),
                "transaction": self._serialize_cash_transaction(transaction),
            }

    def create_employee_salary_transaction(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cashboxes = bundle["cashboxes"]
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="ui")
            settings = bundle["settings"]
            employees = self._employees_from_settings(settings)
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "Нужно передать employee_id.",
                    details={"field": "employee_id"},
                )
            employee = next((item for item in employees if item["id"] == employee_id), None)
            if employee is None:
                self._fail(
                    "not_found",
                    "Сотрудник не найден.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            expected_employee_updated_at = normalize_text(
                payload.get("expected_employee_updated_at"),
                default="",
                limit=80,
            )
            if expected_employee_updated_at and str(employee.get("updated_at") or "") != (
                expected_employee_updated_at
            ):
                self._fail(
                    "employee_update_conflict",
                    "Сотрудник уже изменился. Обновите данные и повторите действие.",
                    status_code=409,
                    details={"employee_id": employee_id},
                )
            kind = self._normalize_salary_transaction_kind(
                payload.get("transaction_kind") or payload.get("kind")
            )
            amount_minor = self._validated_cash_amount_minor(payload)
            requested_cashbox_id = normalize_text(
                payload.get("cashbox_id") or payload.get("cashboxId"),
                default="",
                limit=128,
            )
            cashbox = (
                self._find_cashbox(cashboxes, requested_cashbox_id)
                if requested_cashbox_id
                else self._salary_cashbox(cashboxes)
            )
            if cashbox is None:
                self._fail(
                    "validation_error",
                    "Для выплат зарплаты нужно выбрать кассу.",
                    details={"field": "cashbox_id"},
                )
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
            note_prefix = "Выплата зарплаты" if kind == "salary_payout" else "Аванс"
            note = self._validated_cash_transaction_note(
                payload.get("note") or f"{note_prefix}: {employee['name']}",
            )
            attestation_run_id = normalize_text(
                payload.get("attestation_run_id"),
                default="",
                limit=64,
            )
            if attestation_run_id and not (
                _GATEWAY_ATTESTATION_RUN_RE.fullmatch(attestation_run_id)
                and str(employee.get("name") or "").startswith(f"{attestation_run_id}-")
                and cashbox.name.startswith(f"{attestation_run_id}-")
                and note.startswith(attestation_run_id)
                and amount_minor == 100
                and str(payload.get("source") or "").strip().casefold() == "mcp_agent_gateway_v2"
                and actor_name
            ):
                self._fail(
                    "salary_attestation_scope_invalid",
                    "Синтетическая выплата не соответствует контуру аттестации.",
                    status_code=403,
                )
            transaction = self._append_cash_transaction(
                transactions=transactions,
                cashbox=cashbox,
                direction="expense",
                amount_minor=amount_minor,
                note=note,
                actor_name=actor_name,
                source=source,
                employee_id=employee["id"],
                employee_name=employee["name"],
                transaction_kind=kind,
            )
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="employee_salary_transaction_created",
                message=f"{actor_name} провёл {'выплату зарплаты' if kind == 'salary_payout' else 'аванс'} сотруднику",
                card_id=None,
                details={
                    "employee_id": employee["id"],
                    "employee_name": employee["name"],
                    "transaction_kind": kind,
                    "cashbox_id": cashbox.id,
                    "cashbox_name": cashbox.name,
                    "amount_minor": transaction.amount_minor,
                    "amount_display": format_money_minor(transaction.amount_minor),
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=cashboxes,
                cash_transactions=transactions,
                events=events,
            )
            return {
                "cashbox": self._serialize_cashbox(cashbox, transactions),
                "transaction": self._serialize_cash_transaction(transaction),
                "employee": employee,
            }

    def create_employee_shift_accrual(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            settings = dict(bundle["settings"])
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="ui")
            employees = self._employees_from_settings(settings)
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "Нужно передать employee_id.",
                    details={"field": "employee_id"},
                )
            employee = next((item for item in employees if item["id"] == employee_id), None)
            if employee is None:
                self._fail(
                    "not_found",
                    "Сотрудник не найден.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            expected_employee_updated_at = normalize_text(
                payload.get("expected_employee_updated_at"),
                default="",
                limit=80,
            )
            if expected_employee_updated_at and str(employee.get("updated_at") or "") != (
                expected_employee_updated_at
            ):
                self._fail(
                    "employee_update_conflict",
                    "Сотрудник уже изменился. Обновите данные и повторите действие.",
                    status_code=409,
                    details={"employee_id": employee_id},
                )
            if not employee.get("is_active", True):
                self._fail(
                    "validation_error",
                    "Начисление можно добавить только активному сотруднику.",
                    details={"employee_id": employee_id},
                )
            amount_minor = self._validated_cash_amount_minor(payload)
            amount = Decimal(amount_minor) / Decimal("100")
            note = self._validated_cash_transaction_note(
                payload.get("note") or EMPLOYEE_SHIFT_ACCRUAL_NOTE
            )
            attestation_run_id = normalize_text(
                payload.get("attestation_run_id"),
                default="",
                limit=64,
            )
            if attestation_run_id and not (
                _GATEWAY_ATTESTATION_RUN_RE.fullmatch(attestation_run_id)
                and str(employee.get("name") or "").startswith(f"{attestation_run_id}-")
                and note.startswith(attestation_run_id)
                and amount_minor == 100
                and str(payload.get("source") or "").strip().casefold() == "mcp_agent_gateway_v2"
                and actor_name
            ):
                self._fail(
                    "shift_accrual_attestation_scope_invalid",
                    "Синтетическое начисление не соответствует контуру аттестации.",
                    status_code=403,
                )
            created_at = (
                parse_business_datetime(payload.get("created_at")) or model_helpers.utc_now()
            )
            accrual = {
                "id": str(uuid.uuid4()),
                "employee_id": employee["id"],
                "employee_name": employee["name"],
                "amount": self._format_payroll_decimal(amount),
                "amount_minor": amount_minor,
                "note": note,
                "created_at": created_at.isoformat(),
                "updated_at": model_helpers.utc_now_iso(),
                "actor_name": actor_name,
                "source": source,
            }
            employees_by_id = {item["id"]: item for item in employees}
            shift_accruals = self._employee_shift_accruals_from_settings(
                settings, employees_by_id=employees_by_id
            )
            shift_accruals.append(accrual)
            shift_accruals.sort(
                key=lambda item: (
                    self._repair_order_sortable_datetime(item.get("created_at")),
                    item.get("id") or "",
                )
            )
            settings[EMPLOYEE_SHIFT_ACCRUALS_SETTING_KEY] = [
                self._employee_shift_accrual_storage_payload(item) for item in shift_accruals
            ]
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="employee_shift_accrual_created",
                message=f"{actor_name} добавил начисление за смены сотруднику",
                card_id=None,
                details={
                    "employee_id": employee["id"],
                    "employee_name": employee["name"],
                    "accrual_id": accrual["id"],
                    "amount_minor": amount_minor,
                    "amount_display": format_money_minor(amount_minor),
                    "note": note,
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                events=events,
                settings=settings,
            )
            return {
                "accrual": self._serialize_employee_shift_accrual(accrual),
                "employee": employee,
            }

    def cancel_last_cash_transaction(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            cashboxes = bundle["cashboxes"]
            transactions = bundle["cash_transactions"]
            events = bundle["events"]
            settings = bundle["settings"]
            actor_name, source = self._audit_identity(payload, default_source="ui")
            cashbox = self._find_cashbox(cashboxes, payload.get("cashbox_id"))
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
            related_transactions = self._cashbox_transactions(transactions, cashbox.id)
            if not related_transactions:
                self._fail(
                    "validation_error",
                    "В кассе нет движений для отмены.",
                    details={"field": "cashbox_id"},
                )
            latest_transaction = related_transactions[0]
            requested_transaction_id = normalize_text(
                payload.get("transaction_id"),
                default="",
                limit=128,
            )
            requested_transaction = (
                self._find_cash_transaction(transactions, requested_transaction_id)
                if requested_transaction_id
                else latest_transaction
            )
            if requested_transaction is None:
                self._fail(
                    "not_found",
                    "Кассовое движение не найдено.",
                    status_code=404,
                    details={
                        "cashbox_id": cashbox.id,
                        "transaction_id": requested_transaction_id,
                    },
                )
            if requested_transaction.id != latest_transaction.id:
                self._fail(
                    "validation_error",
                    "Можно отменить только последнее движение по выбранной кассе.",
                    details={
                        "field": "transaction_id",
                        "cashbox_id": cashbox.id,
                        "latest_transaction_id": latest_transaction.id,
                    },
                )
            attestation_run_id = normalize_text(
                payload.get("attestation_run_id"),
                default="",
                limit=64,
            )
            if attestation_run_id and not (
                _GATEWAY_ATTESTATION_RUN_RE.fullmatch(attestation_run_id)
                and cashbox.name.startswith(f"{attestation_run_id}-")
                and requested_transaction.note.startswith(attestation_run_id)
                and requested_transaction.amount_minor == 100
                and not requested_transaction.transaction_kind
                and requested_transaction.source in {"api", "mcp"}
                and requested_transaction.actor_name == actor_name
                and str(payload.get("source") or "").strip().casefold() == "mcp_agent_gateway_v2"
                and actor_name
            ):
                self._fail(
                    "cancel_last_cash_transaction_attestation_scope_invalid",
                    "Синтетическая отмена последнего движения не соответствует контуру аттестации.",
                    status_code=403,
                )
            if self._is_cashbox_transfer_transaction(latest_transaction):
                return self._cancel_cashbox_transfer_pair(
                    bundle=bundle,
                    cashbox=cashbox,
                    latest_transaction=latest_transaction,
                    actor_name=actor_name,
                    source=source,
                )

            linked_card, linked_payment = self._find_repair_order_payment_by_cash_transaction(
                cards, latest_transaction.id
            )
            response_meta: dict[str, object] = {
                "cancelled": True,
                "transaction_id": latest_transaction.id,
                "cashbox_id": cashbox.id,
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
                changed = self._update_repair_order(
                    linked_card,
                    cards,
                    next_order_payload,
                    events,
                    actor_name,
                    source,
                    cashboxes=cashboxes,
                    cash_transactions=transactions,
                    settings=settings,
                )
                if not changed:
                    self._fail(
                        "validation_error",
                        "Не удалось отменить последнее движение по кассе.",
                        details={"transaction_id": latest_transaction.id, "cashbox_id": cashbox.id},
                    )
                self._touch_card(linked_card, actor_name)
                self._refresh_card_ai_fingerprint_if_agent_changed(linked_card, actor_name, source)
                if self._card_has_repair_order(linked_card):
                    self._ensure_repair_order_text_file(linked_card, force=True)
            else:
                transactions[:] = [
                    item for item in transactions if item.id != latest_transaction.id
                ]
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="cash_transaction_deleted",
                    message=f"{actor_name} отменил последнее движение по кассе",
                    card_id=None,
                    details={
                        "cash_transaction_id": latest_transaction.id,
                        "cashbox_id": latest_transaction.cashbox_id,
                        "cashbox_name": cashbox.name,
                        "direction": latest_transaction.direction,
                        "amount_minor": latest_transaction.amount_minor,
                        "amount_display": format_money_minor(latest_transaction.amount_minor),
                        "note": latest_transaction.note,
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
            )
            return {
                "cashbox": self._serialize_cashbox(cashbox, transactions),
                "cancelled_transaction": self._serialize_cash_transaction(latest_transaction),
                "meta": response_meta,
            }

    def _cancel_cashbox_transfer_pair(
        self,
        *,
        bundle: dict[str, Any],
        cashbox: CashBox,
        latest_transaction: CashTransaction,
        actor_name: str,
        source: str,
    ) -> dict:
        transactions = bundle["cash_transactions"]
        cashboxes = bundle["cashboxes"]
        events = bundle["events"]
        related_transaction = self._find_cash_transaction(
            transactions, latest_transaction.related_transaction_id
        )
        if (
            related_transaction is None
            or not latest_transaction.transfer_group_id
            or latest_transaction.transfer_group_id != related_transaction.transfer_group_id
        ):
            self._fail(
                "cashbox_transfer_pair_required",
                "Перемещение можно отменить только целиком. Для legacy-перемещения без связки нужна ручная сверка.",
                status_code=409,
                details={
                    "transaction_id": latest_transaction.id,
                    "cashbox_id": cashbox.id,
                    "related_transaction_id": latest_transaction.related_transaction_id,
                },
            )
        related_cashbox = self._find_cashbox(cashboxes, related_transaction.cashbox_id)
        related_latest = next(
            iter(self._cashbox_transactions(transactions, related_cashbox.id)),
            None,
        )
        if related_latest is None or related_latest.id != related_transaction.id:
            self._fail(
                "cashbox_transfer_pair_not_latest",
                "Нельзя отменить перемещение: связанное движение уже не последнее в своей кассе.",
                status_code=409,
                details={
                    "transaction_id": latest_transaction.id,
                    "related_transaction_id": related_transaction.id,
                    "related_cashbox_id": related_cashbox.id,
                },
            )
        removed_ids = {latest_transaction.id, related_transaction.id}
        transactions[:] = [item for item in transactions if item.id not in removed_ids]
        self._append_event(
            events,
            actor_name=actor_name,
            source=source,
            action="cashbox_transfer_cancelled",
            message=f"{actor_name} отменил перемещение между кассами",
            card_id=None,
            details={
                "transfer_group_id": latest_transaction.transfer_group_id,
                "source_transaction_id": latest_transaction.id
                if latest_transaction.direction == "expense"
                else related_transaction.id,
                "target_transaction_id": latest_transaction.id
                if latest_transaction.direction == "income"
                else related_transaction.id,
                "amount_minor": latest_transaction.amount_minor,
                "amount_display": format_money_minor(latest_transaction.amount_minor),
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
        return {
            "cashbox": self._serialize_cashbox(cashbox, transactions),
            "related_cashbox": self._serialize_cashbox(related_cashbox, transactions),
            "cancelled_transaction": self._serialize_cash_transaction(latest_transaction),
            "related_cancelled_transaction": self._serialize_cash_transaction(related_transaction),
            "meta": {
                "cancelled": True,
                "cancelled_pair": True,
                "transaction_id": latest_transaction.id,
                "related_transaction_id": related_transaction.id,
                "transfer_group_id": latest_transaction.transfer_group_id,
                "cashbox_id": cashbox.id,
            },
        }

    def _append_cash_transaction(
        self,
        *,
        transactions: list[CashTransaction],
        cashbox: CashBox,
        direction: str,
        amount_minor: int,
        note: str,
        actor_name: str,
        source: str,
        created_at: str | None = None,
        employee_id: str = "",
        employee_name: str = "",
        transaction_kind: str = "",
        transfer_group_id: str = "",
        related_transaction_id: str = "",
    ) -> CashTransaction:
        parsed_created_at = parse_datetime(created_at) if created_at else None
        transaction = CashTransaction(
            id=str(uuid.uuid4()),
            cashbox_id=cashbox.id,
            direction=direction,
            amount_minor=amount_minor,
            note=note,
            created_at=(parsed_created_at or model_helpers.utc_now()).isoformat(),
            actor_name=actor_name,
            source=source,
            employee_id=normalize_text(employee_id, default="", limit=64),
            employee_name=normalize_text(employee_name, default="", limit=80),
            transaction_kind=normalize_text(transaction_kind, default="", limit=32),
            transfer_group_id=normalize_text(transfer_group_id, default="", limit=128),
            related_transaction_id=normalize_text(related_transaction_id, default="", limit=128),
        )
        transactions.append(transaction)
        transactions.sort(
            key=lambda item: (
                self._cash_transaction_sortable_datetime(item.created_at),
                item.id,
            )
        )
        cashbox.updated_at = transaction.created_at
        return transaction

    def _serialize_cash_transaction(
        self,
        transaction: CashTransaction,
        *,
        repair_order_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        serialized = transaction.to_dict()
        serialized.update(self._cash_transaction_business_time_payload(transaction))
        source_label = (
            "заказ-наряд"
            if repair_order_context
            else self._cash_transaction_source_label(transaction)
        )
        serialized["source_label"] = source_label
        serialized["link_status"] = self._cash_transaction_link_status(
            transaction,
            repair_order_context=repair_order_context,
        )
        serialized.setdefault("repair_order_number", "")
        serialized.setdefault("repair_order_card_id", "")
        serialized.setdefault("repair_order_payment_id", "")
        serialized.setdefault("repair_order_vehicle", "")
        serialized["direction_label"] = (
            "Поступление" if transaction.direction == "income" else "Списание"
        )
        if repair_order_context:
            serialized.update(repair_order_context)
            if self._is_default_repair_order_cash_transaction_note(transaction.note):
                serialized["stored_note"] = transaction.note
                serialized["note"] = self._repair_order_cash_transaction_note(
                    repair_order_context.get("repair_order_number")
                )
        return serialized

    def _serialize_cash_transaction_compact(
        self,
        transaction: CashTransaction,
        *,
        repair_order_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        serialized = self._serialize_cash_transaction(
            transaction,
            repair_order_context=repair_order_context,
        )
        compact_keys = (
            "id",
            "cashbox_id",
            "direction",
            "amount_minor",
            "amount_display",
            "note",
            "created_at",
            "actor_name",
            "source",
            "transaction_kind",
            "transfer_group_id",
            "related_transaction_id",
            "business_datetime_display",
            "source_label",
            "link_status",
            "stored_note",
            "repair_order_number",
            "repair_order_card_id",
            "repair_order_payment_id",
            "repair_order_vehicle",
        )
        return {
            key: value for key in compact_keys if (value := serialized.get(key)) not in ("", None)
        }

    def _cash_transaction_business_datetime(self, value: str | None) -> datetime | None:
        parsed = parse_datetime(value)
        if parsed is None:
            return None
        return parsed.astimezone(business_timezone())

    def _cash_transaction_business_time_payload(
        self, transaction: CashTransaction
    ) -> dict[str, str]:
        business_datetime = self._cash_transaction_business_datetime(transaction.created_at)
        parsed = parse_datetime(transaction.created_at)
        utc_datetime = parsed.astimezone(UTC) if parsed is not None else None
        if business_datetime is None:
            return {
                "business_datetime": "",
                "business_date": "",
                "business_time": "",
                "business_datetime_display": "",
                "created_at_utc": utc_datetime.isoformat() if utc_datetime is not None else "",
                "created_at_original": transaction.created_at,
            }
        return {
            "business_datetime": business_datetime.isoformat(),
            "business_date": business_datetime.date().isoformat(),
            "business_time": business_datetime.strftime("%H:%M:%S"),
            "business_datetime_display": business_datetime.strftime("%d.%m.%Y %H:%M"),
            "created_at_utc": utc_datetime.isoformat() if utc_datetime is not None else "",
            "created_at_original": transaction.created_at,
        }

    def _cash_transaction_link_status(
        self,
        transaction: CashTransaction,
        *,
        repair_order_context: dict[str, object] | None = None,
    ) -> str:
        kind = normalize_text(transaction.transaction_kind, default="", limit=32).casefold()
        if kind == _CASH_TRANSACTION_KIND_CANCELLATION:
            return "cancellation"
        if kind == _CASH_TRANSACTION_KIND_CANCELLED:
            return "cancelled"
        if repair_order_context:
            return "linked" if kind == "repair_order_payment" else "linked_legacy"
        if kind == "repair_order_payment":
            return "payment_without_order"
        if self._is_default_repair_order_cash_transaction_note(transaction.note):
            return "legacy_without_payment"
        if transaction.transfer_group_id or transaction.related_transaction_id:
            return "linked_transfer"
        return "manual"

    def _repair_order_transaction_context(self, cards: list[Card]) -> dict[str, dict[str, object]]:
        contexts: dict[str, dict[str, object]] = {}
        for card in cards:
            order = card.repair_order
            for payment in order.payments:
                transaction_id = normalize_text(
                    payment.cash_transaction_id,
                    default="",
                    limit=128,
                )
                if not transaction_id:
                    continue
                contexts[transaction_id] = {
                    "source_label": "заказ-наряд",
                    "repair_order_number": order.number,
                    "repair_order_card_id": card.id,
                    "repair_order_vehicle": order.vehicle or card.vehicle,
                    "repair_order_payment_id": payment.id,
                }
        return contexts

    def _finance_payment_links(
        self, cards: list[Card]
    ) -> dict[str, tuple[Card, RepairOrderPayment]]:
        links: dict[str, tuple[Card, RepairOrderPayment]] = {}
        for card in cards:
            for payment in card.repair_order.payments:
                transaction_id = normalize_text(
                    payment.cash_transaction_id,
                    default="",
                    limit=128,
                )
                if transaction_id:
                    links[transaction_id] = (card, payment)
        return links

    def _finance_audit_issue(
        self,
        *,
        code: str,
        severity: str,
        message: str,
        card: Card | None = None,
        payment: RepairOrderPayment | None = None,
        transaction: CashTransaction | None = None,
        safe_fix: dict[str, object] | None = None,
        data: dict[str, object] | None = None,
    ) -> dict[str, object]:
        parts = [
            code,
            card.id if card is not None else "",
            payment.id if payment is not None else "",
            transaction.id if transaction is not None else "",
        ]
        issue_id = ":".join(part for part in parts if part)
        order = card.repair_order if card is not None else None
        return {
            "id": issue_id,
            "code": code,
            "severity": severity,
            "message": message,
            "card_id": card.id if card is not None else "",
            "repair_order_number": order.number if order is not None else "",
            "repair_order_vehicle": (order.vehicle or card.vehicle)
            if order is not None and card
            else "",
            "repair_order_payment_id": payment.id if payment is not None else "",
            "cash_transaction_id": transaction.id if transaction is not None else "",
            "cashbox_id": transaction.cashbox_id
            if transaction is not None
            else (payment.cashbox_id if payment is not None else ""),
            "amount_minor": transaction.amount_minor
            if transaction is not None
            else (normalize_money_minor(payment.amount) if payment is not None else 0),
            "safe_fix_available": safe_fix is not None,
            "safe_fix": safe_fix or {},
            "data": data or {},
        }

    def _build_finance_audit(self, bundle: dict[str, Any]) -> dict:
        cards = bundle["cards"]
        transactions = bundle["cash_transactions"]
        transactions_by_id = {transaction.id: transaction for transaction in transactions}
        cashboxes_by_id = {cashbox.id: cashbox for cashbox in bundle["cashboxes"]}
        payment_links = self._finance_payment_links(cards)
        payment_refs_by_transaction_id: dict[str, list[tuple[Card, RepairOrderPayment]]] = {}
        for payment_card in cards:
            for payment in payment_card.repair_order.payments:
                transaction_id = normalize_text(
                    payment.cash_transaction_id,
                    default="",
                    limit=128,
                )
                if transaction_id:
                    payment_refs_by_transaction_id.setdefault(transaction_id, []).append(
                        (payment_card, payment)
                    )
        employees = self._employees_from_settings(bundle.get("settings", {}))
        employee_ids = {employee["id"] for employee in employees}
        issues: list[dict[str, object]] = []
        checked_transfer_keys: set[str] = set()

        for card in cards:
            order = card.repair_order
            if order.is_empty():
                continue
            for payment in order.payments:
                transaction_id = normalize_text(payment.cash_transaction_id, default="", limit=128)
                if not transaction_id:
                    issues.append(
                        self._finance_audit_issue(
                            code="payment_without_cash_transaction_id",
                            severity="warning",
                            message="Оплата заказ-наряда не связана с движением кассы.",
                            card=card,
                            payment=payment,
                        )
                    )
                    continue
                transaction = transactions_by_id.get(transaction_id)
                if transaction is None:
                    issues.append(
                        self._finance_audit_issue(
                            code="payment_missing_cash_transaction",
                            severity="error",
                            message="Оплата заказ-наряда ссылается на отсутствующее движение кассы.",
                            card=card,
                            payment=payment,
                        )
                    )
                    continue
                duplicate_payment_refs = payment_refs_by_transaction_id.get(transaction_id, [])
                if len(duplicate_payment_refs) > 1:
                    issues.append(
                        self._finance_audit_issue(
                            code="duplicate_repair_order_payment_cash_link",
                            severity="error",
                            message="Несколько оплат заказ-нарядов ссылаются на одно движение кассы.",
                            card=card,
                            payment=payment,
                            transaction=transaction,
                            data={
                                "linked_payments": [
                                    {
                                        "card_id": linked_card.id,
                                        "repair_order_number": linked_card.repair_order.number,
                                        "repair_order_payment_id": linked_payment.id,
                                    }
                                    for linked_card, linked_payment in duplicate_payment_refs
                                ],
                            },
                        )
                    )
                expected_amount_minor = normalize_money_minor(payment.amount, default=0)
                expected_cashbox_id = normalize_text(payment.cashbox_id, default="", limit=128)
                mismatch_reasons: list[str] = []
                if transaction.direction != "income":
                    mismatch_reasons.append("direction")
                if transaction.amount_minor != expected_amount_minor:
                    mismatch_reasons.append("amount")
                if expected_cashbox_id and transaction.cashbox_id != expected_cashbox_id:
                    mismatch_reasons.append("cashbox")
                if mismatch_reasons:
                    issues.append(
                        self._finance_audit_issue(
                            code="linked_payment_cash_transaction_mismatch",
                            severity="error",
                            message="Связанное движение кассы не совпадает с оплатой заказ-наряда.",
                            card=card,
                            payment=payment,
                            transaction=transaction,
                            data={
                                "mismatch_reasons": mismatch_reasons,
                                "expected_direction": "income",
                                "direction": transaction.direction,
                                "expected_amount_minor": expected_amount_minor,
                                "amount_minor": transaction.amount_minor,
                                "expected_cashbox_id": expected_cashbox_id,
                                "cashbox_id": transaction.cashbox_id,
                            },
                        )
                    )
                if transaction.transaction_kind != "repair_order_payment":
                    issues.append(
                        self._finance_audit_issue(
                            code="linked_payment_transaction_missing_kind",
                            severity="warning",
                            message="Связанное движение оплаты заказ-наряда не помечено как repair_order_payment.",
                            card=card,
                            payment=payment,
                            transaction=transaction,
                            safe_fix={
                                "kind": "set_transaction_kind",
                                "cash_transaction_id": transaction.id,
                                "value": "repair_order_payment",
                            },
                        )
                    )
                expected_note = self._repair_order_cash_transaction_note(order.number)
                if (
                    self._is_default_repair_order_cash_transaction_note(transaction.note)
                    and transaction.note != expected_note
                ):
                    issues.append(
                        self._finance_audit_issue(
                            code="stale_default_repair_order_note",
                            severity="warning",
                            message="Default-note движения кассы не совпадает с текущим номером связанного заказ-наряда.",
                            card=card,
                            payment=payment,
                            transaction=transaction,
                            safe_fix={
                                "kind": "refresh_default_note",
                                "cash_transaction_id": transaction.id,
                                "value": expected_note,
                            },
                            data={"stored_note": transaction.note, "expected_note": expected_note},
                        )
                    )

            payment_summary = order.payment_summary_amounts()
            if order.status == REPAIR_ORDER_STATUS_CLOSED and not order.is_paid():
                issues.append(
                    self._finance_audit_issue(
                        code="closed_underpaid",
                        severity="error",
                        message="Закрытый заказ-наряд имеет недоплату.",
                        card=card,
                        data={
                            "due_total": payment_summary["due_total"],
                            "paid_total": payment_summary["total_paid"],
                            "grand_total": payment_summary["grand_total"],
                        },
                    )
                )
            if (
                order.status != REPAIR_ORDER_STATUS_OPEN
                and order.prepayment_value() > Decimal("0")
                and order.subtotal_value() == Decimal("0")
            ):
                issues.append(
                    self._finance_audit_issue(
                        code="paid_zero_total",
                        severity="warning",
                        message="В заказ-наряде есть оплаты, но нет суммы работ или материалов.",
                        card=card,
                        data={"paid_total": order.prepayment_amount()},
                    )
                )
            if order.status == REPAIR_ORDER_STATUS_OPEN and order.prepayment_value() > Decimal("0"):
                issues.append(
                    self._finance_audit_issue(
                        code="open_with_payments",
                        severity="info",
                        message="Открытый заказ-наряд уже имеет оплаты.",
                        card=card,
                        data={"paid_total": order.prepayment_amount()},
                    )
                )

        for transaction in transactions:
            kind = normalize_text(transaction.transaction_kind, default="", limit=32)
            kind_casefold = kind.casefold()
            if transaction.cashbox_id not in cashboxes_by_id:
                issues.append(
                    self._finance_audit_issue(
                        code="cash_transaction_missing_cashbox",
                        severity="error",
                        message="Кассовое движение ссылается на отсутствующую кассу.",
                        transaction=transaction,
                        data={"cashbox_id": transaction.cashbox_id},
                    )
                )
            if kind_casefold in {
                _CASH_TRANSACTION_KIND_CANCELLED,
                _CASH_TRANSACTION_KIND_CANCELLATION,
            }:
                continue
            if kind_casefold in {"salary_payout", "salary_advance"}:
                if transaction.direction != "expense":
                    issues.append(
                        self._finance_audit_issue(
                            code="salary_transaction_wrong_direction",
                            severity="error",
                            message="Зарплатное движение кассы должно быть расходом.",
                            transaction=transaction,
                            data={
                                "direction": transaction.direction,
                                "expected_direction": "expense",
                                "transaction_kind": kind,
                            },
                        )
                    )
                employee_id = normalize_text(transaction.employee_id, default="", limit=64)
                if not employee_id or employee_id not in employee_ids:
                    employee_name = normalize_text(
                        transaction.employee_name,
                        default="",
                        limit=80,
                    )
                    safe_fix = None
                    if employee_id and employee_name:
                        safe_fix = {
                            "kind": "restore_missing_employee",
                            "employee_id": employee_id,
                            "employee_name": employee_name,
                        }
                    issues.append(
                        self._finance_audit_issue(
                            code="salary_transaction_missing_employee",
                            severity="error",
                            message="Зарплатное движение кассы ссылается на отсутствующего сотрудника.",
                            transaction=transaction,
                            safe_fix=safe_fix,
                            data={
                                "employee_id": employee_id,
                                "employee_name": employee_name,
                                "transaction_kind": kind,
                            },
                        )
                    )
            if transaction.transfer_group_id or transaction.related_transaction_id:
                transfer_key = transaction.transfer_group_id or "|".join(
                    sorted(
                        [
                            transaction.id,
                            normalize_text(
                                transaction.related_transaction_id,
                                default="",
                                limit=128,
                            ),
                        ]
                    )
                )
                if transfer_key not in checked_transfer_keys:
                    checked_transfer_keys.add(transfer_key)
                    related_transaction = (
                        transactions_by_id.get(transaction.related_transaction_id)
                        if transaction.related_transaction_id
                        else None
                    )
                    if related_transaction is None and transaction.transfer_group_id:
                        group_peers = [
                            candidate
                            for candidate in transactions
                            if candidate.id != transaction.id
                            and candidate.transfer_group_id == transaction.transfer_group_id
                        ]
                        related_transaction = group_peers[0] if len(group_peers) == 1 else None
                    if related_transaction is None:
                        issues.append(
                            self._finance_audit_issue(
                                code="transfer_pair_missing",
                                severity="error",
                                message="Внутреннее перемещение кассы не имеет найденной парной операции.",
                                transaction=transaction,
                                data={
                                    "transfer_group_id": transaction.transfer_group_id,
                                    "related_transaction_id": transaction.related_transaction_id,
                                },
                            )
                        )
                    else:
                        mismatch_reasons: list[str] = []
                        if related_transaction.direction == transaction.direction:
                            mismatch_reasons.append("same_direction")
                        if related_transaction.amount_minor != transaction.amount_minor:
                            mismatch_reasons.append("amount")
                        if (
                            transaction.transfer_group_id
                            and related_transaction.transfer_group_id
                            and related_transaction.transfer_group_id
                            != transaction.transfer_group_id
                        ):
                            mismatch_reasons.append("transfer_group")
                        if (
                            related_transaction.related_transaction_id
                            and related_transaction.related_transaction_id != transaction.id
                        ):
                            mismatch_reasons.append("related_transaction")
                        if mismatch_reasons:
                            issues.append(
                                self._finance_audit_issue(
                                    code=(
                                        "transfer_pair_amount_mismatch"
                                        if "amount" in mismatch_reasons
                                        else "transfer_pair_mismatch"
                                    ),
                                    severity="error",
                                    message="Парные операции внутреннего перемещения кассы не совпадают.",
                                    transaction=transaction,
                                    data={
                                        "peer_cash_transaction_id": related_transaction.id,
                                        "transfer_group_id": transaction.transfer_group_id,
                                        "mismatch_reasons": mismatch_reasons,
                                        "amount_minor": transaction.amount_minor,
                                        "peer_amount_minor": related_transaction.amount_minor,
                                    },
                                )
                            )
            has_default_order_note = self._is_default_repair_order_cash_transaction_note(
                transaction.note
            )
            linked = payment_links.get(transaction.id)
            if kind == "repair_order_payment" and linked is None:
                issues.append(
                    self._finance_audit_issue(
                        code="repair_payment_transaction_without_payment",
                        severity="error",
                        message="Кассовое движение помечено оплатой заказ-наряда, но связанная оплата не найдена.",
                        transaction=transaction,
                    )
                )
            if has_default_order_note and kind != "repair_order_payment":
                card = linked[0] if linked is not None else None
                payment = linked[1] if linked is not None else None
                safe_fix = None
                if linked is not None:
                    safe_fix = {
                        "kind": "set_transaction_kind",
                        "cash_transaction_id": transaction.id,
                        "value": "repair_order_payment",
                    }
                issues.append(
                    self._finance_audit_issue(
                        code="legacy_order_note_without_kind",
                        severity="warning",
                        message="Движение с note вида «Заказ-наряд №...» не имеет transaction_kind.",
                        card=card,
                        payment=payment,
                        transaction=transaction,
                        safe_fix=safe_fix,
                    )
                )
            raw_created_at = normalize_text(transaction.created_at, default="", limit=80)
            parsed_created_at = parse_datetime(raw_created_at)
            if parsed_created_at is None or re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?",
                raw_created_at,
            ):
                issues.append(
                    self._finance_audit_issue(
                        code="suspicious_timezone_display",
                        severity="info",
                        message="У движения кассы нет явной timezone или дата не распознана.",
                        transaction=transaction,
                        data={"created_at": raw_created_at},
                    )
                )

        issues.sort(
            key=lambda item: (
                {"error": 0, "warning": 1, "info": 2}.get(str(item.get("severity")), 3),
                str(item.get("code") or ""),
                str(item.get("id") or ""),
            )
        )
        counts_by_code: dict[str, int] = {}
        for issue in issues:
            code = str(issue.get("code") or "")
            counts_by_code[code] = counts_by_code.get(code, 0) + 1
        safe_fix_count = sum(1 for issue in issues if issue.get("safe_fix_available"))
        return {
            "issues": issues,
            "summary": {
                "issues_total": len(issues),
                "safe_fix_count": safe_fix_count,
                "counts_by_code": counts_by_code,
                "payments_total": sum(len(card.repair_order.payments) for card in cards),
                "cash_transactions_total": len(transactions),
                "business_timezone": str(business_timezone()),
            },
            "meta": {
                "schema_version": "finance_audit.v1",
                "generated_at": model_helpers.utc_now_iso(),
                "read_only": True,
            },
        }

    def _repair_order_cash_transaction_note(self, repair_order_number: object) -> str:
        number = normalize_text(repair_order_number, default="", limit=40)
        return f"Заказ-наряд №{number}" if number else "Заказ-наряд"

    def _is_default_repair_order_cash_transaction_note(self, note: str | None) -> bool:
        normalized = normalize_text(note, default="", limit=240).casefold()
        return bool(re.fullmatch(r"заказ-наряд\s*№\s*\S+", normalized))

    def _cash_transaction_sortable_datetime(self, value: str | None) -> datetime:
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed.astimezone(UTC)
        return datetime.min.replace(tzinfo=UTC)

    def _cash_transaction_business_sortable_datetime(self, value: str | None) -> datetime:
        parsed = self._cash_transaction_business_datetime(value)
        if parsed is not None:
            return parsed
        return datetime.min.replace(tzinfo=business_timezone())

    def _cashbox_notification_transaction_key(
        self, transaction: CashTransaction
    ) -> tuple[datetime, str]:
        return (
            self._cash_transaction_business_sortable_datetime(transaction.created_at),
            transaction.id,
        )

    def _cashbox_notification_receipt_key(self, receipt: object) -> tuple[datetime, str]:
        if not isinstance(receipt, dict):
            return datetime.min.replace(tzinfo=business_timezone()), ""
        return (
            self._cash_transaction_business_sortable_datetime(
                normalize_text(receipt.get("created_at"), default="", limit=80)
            ),
            normalize_text(receipt.get("transaction_id"), default="", limit=128),
        )

    def _cashbox_notification_receipt(self, transaction: CashTransaction | None) -> dict[str, str]:
        if transaction is None:
            return {"transaction_id": "", "created_at": ""}
        return {
            "transaction_id": transaction.id,
            "created_at": transaction.created_at,
        }

    def _cashbox_notification_seen_by_users(
        self, settings: dict[str, Any]
    ) -> dict[str, dict[str, str]]:
        raw_seen = settings.get(CASHBOX_NOTIFICATION_SEEN_SETTING_KEY)
        if not isinstance(raw_seen, dict):
            return {}
        normalized: dict[str, dict[str, str]] = {}
        for raw_actor, raw_receipt in raw_seen.items():
            actor_key = normalize_actor_name(raw_actor, default="").casefold()
            if not actor_key or not isinstance(raw_receipt, dict):
                continue
            normalized[actor_key] = {
                "transaction_id": normalize_text(
                    raw_receipt.get("transaction_id"), default="", limit=128
                ),
                "created_at": normalize_text(raw_receipt.get("created_at"), default="", limit=80),
            }
        return normalized

    def _cashbox_notification_latest_transaction(
        self, transactions: list[CashTransaction]
    ) -> CashTransaction | None:
        if not transactions:
            return None
        return max(transactions, key=self._cashbox_notification_transaction_key)

    def _cashbox_notification_tone(self, transaction: CashTransaction) -> str:
        if transaction.transfer_group_id:
            return "transfer"
        return "expense" if transaction.direction == "expense" else "income"

    def _cashbox_notification_summary(
        self, bundle: dict[str, Any], payload: dict | None = None
    ) -> dict[str, Any]:
        payload = payload or {}
        actor_name = normalize_actor_name(payload.get("actor_name"), default="")
        transactions = list(bundle.get("cash_transactions") or [])
        latest_transaction = self._cashbox_notification_latest_transaction(transactions)
        through_transaction_id = latest_transaction.id if latest_transaction else ""
        seen_by_users = self._cashbox_notification_seen_by_users(bundle.get("settings") or {})
        actor_key = actor_name.casefold()
        receipt = seen_by_users.get(actor_key) if actor_key else None
        if receipt is None:
            return {
                "initialized": False,
                "has_unread": False,
                "tone": "",
                "unread_count": 0,
                "unread_transactions": [],
                "through_transaction_id": through_transaction_id,
            }

        receipt_key = self._cashbox_notification_receipt_key(receipt)
        unseen = [
            transaction
            for transaction in transactions
            if self._cashbox_notification_transaction_key(transaction) > receipt_key
            and normalize_actor_name(transaction.actor_name, default="").casefold() != actor_key
        ]
        unseen.sort(key=self._cashbox_notification_transaction_key, reverse=True)
        event_keys = {
            f"transfer:{transaction.transfer_group_id}"
            if transaction.transfer_group_id
            else f"transaction:{transaction.id}"
            for transaction in unseen
        }
        visible_unseen = unseen[:_CASHBOX_NOTIFICATION_UNREAD_LIMIT]
        unread_transactions = [
            {
                "transaction_id": transaction.id,
                "cashbox_id": transaction.cashbox_id,
                "tone": self._cashbox_notification_tone(transaction),
                "created_at": transaction.created_at,
                "transfer_group_id": transaction.transfer_group_id,
            }
            for transaction in visible_unseen
        ]
        latest_unseen = unseen[0] if unseen else None
        return {
            "initialized": True,
            "has_unread": bool(unseen),
            "tone": self._cashbox_notification_tone(latest_unseen) if latest_unseen else "",
            "unread_count": len(event_keys),
            "unread_transactions": unread_transactions,
            "unread_transactions_truncated": len(unseen) > len(visible_unseen),
            "through_transaction_id": through_transaction_id,
        }

    def _find_cash_transaction(
        self,
        transactions: list[CashTransaction],
        transaction_id: str | None,
    ) -> CashTransaction | None:
        requested_id = normalize_text(transaction_id, default="", limit=128)
        if not requested_id:
            return None
        requested_short_id = requested_id.upper()
        for transaction in transactions:
            if (
                transaction.id == requested_id
                or short_entity_id(transaction.id, prefix="CT").upper() == requested_short_id
            ):
                return transaction
        return None

    def _cashbox_transactions(
        self,
        transactions: list[CashTransaction],
        cashbox_id: str,
    ) -> list[CashTransaction]:
        matched = [item for item in transactions if item.cashbox_id == cashbox_id]
        matched.sort(
            key=lambda item: (
                self._cash_transaction_business_sortable_datetime(item.created_at),
                item.id,
            ),
            reverse=True,
        )
        return matched

    def _cashbox_rounded_money_text(self, amount_minor: object, *, signed: bool = False) -> str:
        value = self._cash_journal_minor_value(amount_minor)
        rounded_rubles = (abs(value) + 50) // 100
        formatted = f"{rounded_rubles:,}".replace(",", " ") + " ₽"
        if rounded_rubles == 0:
            return formatted
        if signed and value > 0:
            return f"+{formatted}"
        if value < 0:
            return f"-{formatted}"
        return formatted

    def _cash_journal_money_text(self, amount_minor: int, *, signed: bool = False) -> str:
        return self._cashbox_rounded_money_text(amount_minor, signed=signed)

    def _cash_journal_minor_value(self, value: object) -> int:
        return model_helpers.normalize_int(
            value,
            default=0,
            minimum=-model_helpers.MONEY_MINOR_ABS_MAX,
            maximum=model_helpers.MONEY_MINOR_ABS_MAX,
        )

    def _cash_transaction_source_label(self, transaction: CashTransaction) -> str:
        transaction_kind = self._cash_transaction_kind_label(transaction.transaction_kind)
        if transaction_kind:
            return transaction_kind
        if transaction.transfer_group_id or transaction.related_transaction_id:
            return "перемещение"
        note = normalize_text(transaction.note, default="", limit=240)
        if note.casefold().startswith("перемещение"):
            return "перемещение"
        if "заказ-наряд" in note.casefold():
            return "заказ-наряд"
        source = normalize_text(transaction.source, default="", limit=64).casefold()
        if source == "ui":
            return "ручное"
        if source == "mcp":
            return "mcp"
        return source or "система"

    def _cash_transaction_kind_label(self, transaction_kind: str) -> str:
        normalized = normalize_text(transaction_kind, default="", limit=32).casefold()
        if normalized == "repair_order_payment":
            return "заказ-наряд"
        if normalized == "salary_payout":
            return "зарплата"
        if normalized == "salary_advance":
            return "аванс"
        if normalized == "cashbox_normalization":
            return "нормализация"
        if normalized == _CASH_TRANSACTION_KIND_CANCELLATION:
            return "отмена"
        if normalized == _CASH_TRANSACTION_KIND_CANCELLED:
            return "отменено"
        return ""

    def _normalize_salary_transaction_kind(self, value: Any) -> str:
        normalized = normalize_text(value, default="", limit=32).casefold()
        if normalized in {"salary_payout", "payout", "salary", "salary_payment"}:
            return "salary_payout"
        if normalized in {"salary_advance", "advance", "avans"}:
            return "salary_advance"
        self._fail(
            "validation_error",
            "Неверный тип операции по зарплате.",
            details={"field": "transaction_kind"},
        )

    def _salary_cashbox(self, cashboxes: list[CashBox]) -> CashBox | None:
        if not cashboxes:
            return None
        exact = [item for item in cashboxes if item.name.casefold() == "наличный"]
        if exact:
            return exact[0]
        loose = [
            item
            for item in cashboxes
            if "налич" in item.name.casefold() or "cash" in item.name.casefold()
        ]
        if loose:
            return loose[0]
        return None

    def _build_cash_journal(
        self,
        transactions: list[CashTransaction],
        cashboxes_by_id: dict[str, CashBox],
        *,
        months: int,
        limit: int,
        total: int,
        period_start: datetime,
        all_transactions: list[CashTransaction] | None = None,
        cashboxes: list[CashBox] | None = None,
        repair_order_transaction_context: dict[str, dict[str, object]] | None = None,
        include_markdown: bool = True,
        compact_groups: bool = False,
    ) -> dict[str, object]:
        entries: list[dict[str, object]] = []
        for item in transactions:
            created_at = self._cash_transaction_business_datetime(item.created_at)
            cashbox = cashboxes_by_id.get(item.cashbox_id)
            base = self._serialize_cash_transaction(
                item,
                repair_order_context=(repair_order_transaction_context or {}).get(item.id),
            )
            direction_sign = 1 if item.direction == "income" else -1
            signed_amount_minor = self._cash_journal_minor_value(item.amount_minor) * direction_sign
            if created_at is not None:
                iso_year, iso_week, _ = created_at.isocalendar()
                date_label = created_at.date().isoformat()
                time_label = created_at.strftime("%H:%M:%S")
                short_time_label = created_at.strftime("%H:%M")
                month_key = created_at.strftime("%Y-%m")
                week_key = f"{iso_year}-W{iso_week:02d}"
            else:
                date_label = "unknown"
                time_label = ""
                short_time_label = ""
                month_key = "unknown"
                week_key = "unknown"
            base.update(
                {
                    "schema_version": "cash_journal.entry.v2",
                    "cashbox_name": cashbox.name if cashbox else "Неизвестная касса",
                    "date": str(base.get("business_date") or date_label),
                    "time": str(base.get("business_time") or time_label),
                    "time_short": str(base.get("business_time") or time_label)[:5]
                    if str(base.get("business_time") or time_label)
                    else short_time_label,
                    "month_key": month_key,
                    "week_key": week_key,
                    "direction_label": "Поступление" if item.direction == "income" else "Списание",
                    "direction_sign": direction_sign,
                    "signed_amount_minor": signed_amount_minor,
                    "signed_amount_display": self._cash_journal_money_text(
                        signed_amount_minor, signed=True
                    ),
                    "amount_display": self._cash_journal_money_text(item.amount_minor),
                    "actor_label": normalize_actor_name(item.actor_name),
                    "source_label": str(
                        base.get("source_label") or self._cash_transaction_source_label(item)
                    ),
                    "note": normalize_text(base.get("note"), default="Без комментария", limit=240),
                }
            )
            entries.append(base)

        days = self._cash_journal_group_entries(entries, key="date", kind="day")
        days = self._cash_journal_with_opening_balances(
            days,
            all_transactions=all_transactions or transactions,
            cashboxes_by_id=cashboxes_by_id,
            cashboxes=cashboxes,
        )
        weeks = self._cash_journal_group_entries(entries, key="week_key", kind="week")
        months_grouped = self._cash_journal_group_entries(entries, key="month_key", kind="month")
        totals = self._cash_journal_totals(entries)
        meta = {
            "schema_version": "cash_journal.v2",
            "months": months,
            "limit": limit,
            "total": total,
            "returned": len(transactions),
            "period_start": period_start.isoformat(),
            "format": "markdown+json" if include_markdown else "json",
            "include_markdown": include_markdown,
            "compact_groups": compact_groups,
        }
        if include_markdown:
            meta["text_alias"] = "markdown"
        journal: dict[str, object] = {
            "entries": entries,
            "days": days,
            "weeks": weeks,
            "months": months_grouped,
            "totals": totals,
            "meta": meta,
        }
        if include_markdown:
            journal["markdown"] = self._cash_journal_markdown(
                entries=entries,
                days=days,
                weeks=weeks,
                months=months_grouped,
                totals=totals,
                meta=meta,
            )
        if compact_groups:
            journal["days"] = self._compact_cash_journal_groups(days)
            journal["weeks"] = self._compact_cash_journal_groups(weeks)
            journal["months"] = self._compact_cash_journal_groups(months_grouped)
        return journal

    def _compact_cash_journal_groups(
        self, groups: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        return [
            {key: value for key, value in group.items() if key != "entries"} for group in groups
        ]

    def _cash_journal_totals(self, entries: list[dict[str, object]]) -> dict[str, object]:
        income_minor = sum(
            self._cash_journal_minor_value(item.get("amount_minor"))
            for item in entries
            if item.get("direction") == "income"
        )
        expense_minor = sum(
            self._cash_journal_minor_value(item.get("amount_minor"))
            for item in entries
            if item.get("direction") == "expense"
        )
        external_income_minor = sum(
            self._cash_journal_minor_value(item.get("amount_minor"))
            for item in entries
            if item.get("direction") == "income" and item.get("source_label") != "перемещение"
        )
        external_expense_minor = sum(
            self._cash_journal_minor_value(item.get("amount_minor"))
            for item in entries
            if item.get("direction") == "expense" and item.get("source_label") != "перемещение"
        )
        transfer_income_minor = income_minor - external_income_minor
        transfer_expense_minor = expense_minor - external_expense_minor
        balance_minor = income_minor - expense_minor
        return {
            "count": len(entries),
            "income_minor": income_minor,
            "expense_minor": expense_minor,
            "balance_minor": balance_minor,
            "external_income_minor": external_income_minor,
            "external_expense_minor": external_expense_minor,
            "transfer_income_minor": transfer_income_minor,
            "transfer_expense_minor": transfer_expense_minor,
            "income_display": self._cash_journal_money_text(income_minor),
            "expense_display": self._cash_journal_money_text(expense_minor),
            "balance_display": self._cash_journal_money_text(balance_minor, signed=True),
            "external_income_display": self._cash_journal_money_text(external_income_minor),
            "external_expense_display": self._cash_journal_money_text(external_expense_minor),
            "transfer_income_display": self._cash_journal_money_text(transfer_income_minor),
            "transfer_expense_display": self._cash_journal_money_text(transfer_expense_minor),
        }

    def _cash_journal_group_entries(
        self, entries: list[dict[str, object]], *, key: str, kind: str
    ) -> list[dict[str, object]]:
        grouped: dict[str, list[dict[str, object]]] = {}
        for item in entries:
            grouped.setdefault(str(item.get(key) or "unknown"), []).append(item)
        result: list[dict[str, object]] = []
        for group_key in sorted(grouped.keys(), reverse=True):
            group_entries = grouped[group_key]
            totals = self._cash_journal_totals(group_entries)
            payload: dict[str, object] = {
                "key": group_key,
                "entries": group_entries,
                **totals,
            }
            if kind == "day":
                payload["date"] = group_key
                payload["label"] = self._cash_journal_day_label(group_key)
            elif kind == "week":
                payload["week_key"] = group_key
                payload["label"] = self._cash_journal_week_label(group_key)
            else:
                payload["month_key"] = group_key
                payload["label"] = self._cash_journal_month_label(group_key)
            result.append(payload)
        return result

    def _cash_journal_with_opening_balances(
        self,
        days: list[dict[str, object]],
        *,
        all_transactions: list[CashTransaction],
        cashboxes_by_id: dict[str, CashBox],
        cashboxes: list[CashBox] | None = None,
    ) -> list[dict[str, object]]:
        ordered_cashboxes = self._ordered_cashboxes(
            list(cashboxes) if cashboxes is not None else list(cashboxes_by_id.values())
        )
        known_ids = {cashbox.id for cashbox in ordered_cashboxes}
        cashbox_labels = [(cashbox.id, cashbox.name) for cashbox in ordered_cashboxes]
        unknown_ids = sorted(
            {
                transaction.cashbox_id
                for transaction in all_transactions
                if transaction.cashbox_id and transaction.cashbox_id not in known_ids
            }
        )
        cashbox_labels.extend((cashbox_id, "Неизвестная касса") for cashbox_id in unknown_ids)
        balances_by_date: dict[str, dict[str, int]] = {}
        for transaction in all_transactions:
            transaction_date_key = self._cash_journal_transaction_date_key(transaction)
            if transaction_date_key == "unknown":
                continue
            direction_sign = 1 if transaction.direction == "income" else -1
            cashbox_id = transaction.cashbox_id
            if not cashbox_id:
                continue
            balances_by_date.setdefault(transaction_date_key, {}).setdefault(cashbox_id, 0)
            balances_by_date[transaction_date_key][cashbox_id] += (
                self._cash_journal_minor_value(transaction.amount_minor) * direction_sign
            )
        day_keys = sorted(
            {
                str(day.get("date") or day.get("key") or "")
                for day in days
                if str(day.get("date") or day.get("key") or "") not in {"", "unknown"}
            }
        )
        running_balances = {cashbox_id: 0 for cashbox_id, _ in cashbox_labels}
        opening_balances_by_day: dict[str, dict[str, int]] = {}
        transaction_dates = sorted(balances_by_date)
        transaction_date_index = 0
        for day_key in day_keys:
            while (
                transaction_date_index < len(transaction_dates)
                and transaction_dates[transaction_date_index] < day_key
            ):
                date_deltas = balances_by_date[transaction_dates[transaction_date_index]]
                for cashbox_id, delta_minor in date_deltas.items():
                    running_balances.setdefault(cashbox_id, 0)
                    running_balances[cashbox_id] += self._cash_journal_minor_value(delta_minor)
                transaction_date_index += 1
            opening_balances_by_day[day_key] = dict(running_balances)
        for day in days:
            date_key = str(day.get("date") or day.get("key") or "")
            opening_balances = self._cash_journal_balance_rows(
                opening_balances_by_day.get(date_key, {}),
                cashbox_labels,
            )
            opening_total_minor = sum(
                self._cash_journal_minor_value(item.get("balance_minor"))
                for item in opening_balances
            )
            day["opening_balances"] = opening_balances
            day["opening_total_minor"] = opening_total_minor
            day["opening_total_display"] = self._cashbox_rounded_money_text(opening_total_minor)
            day["opening_total_sign"] = "negative" if opening_total_minor < 0 else "positive"
        return days

    def _cash_journal_balance_rows(
        self,
        balances_by_id: dict[str, int],
        cashbox_labels: list[tuple[str, str]],
    ) -> list[dict[str, object]]:
        return [
            {
                "cashbox_id": cashbox_id,
                "cashbox_name": cashbox_name,
                "balance_minor": self._cash_journal_minor_value(balances_by_id.get(cashbox_id)),
                "balance_display": self._cashbox_rounded_money_text(balances_by_id.get(cashbox_id)),
                "balance_sign": (
                    "negative"
                    if self._cash_journal_minor_value(balances_by_id.get(cashbox_id)) < 0
                    else "positive"
                ),
            }
            for cashbox_id, cashbox_name in cashbox_labels
        ]

    def _cash_journal_transaction_date_key(self, transaction: CashTransaction) -> str:
        created_at = self._cash_transaction_business_datetime(transaction.created_at)
        if created_at is None:
            return "unknown"
        return created_at.date().isoformat()

    def _cash_journal_day_label(self, date_key: str) -> str:
        try:
            value = datetime.strptime(date_key, "%Y-%m-%d")
        except ValueError:
            return date_key
        weekdays = [
            "понедельник",
            "вторник",
            "среда",
            "четверг",
            "пятница",
            "суббота",
            "воскресенье",
        ]
        return f"{value.strftime('%d.%m.%Y')}, {weekdays[value.weekday()]}"

    def _cash_journal_week_label(self, week_key: str) -> str:
        try:
            year_text, week_text = week_key.split("-W", 1)
            if (
                not year_text.isdecimal()
                or not week_text.isdecimal()
                or len(year_text) != 4
                or len(week_text) > 2
            ):
                return week_key
            start = datetime.fromisocalendar(int(year_text), int(week_text), 1)
            end = datetime.fromisocalendar(int(year_text), int(week_text), 7)
        except (ValueError, TypeError):
            return week_key
        return f"{week_text} неделя: {start.strftime('%d.%m')} - {end.strftime('%d.%m.%Y')}"

    def _cash_journal_month_label(self, month_key: str) -> str:
        try:
            value = datetime.strptime(month_key, "%Y-%m")
        except ValueError:
            return month_key
        month_names = [
            "Январь",
            "Февраль",
            "Март",
            "Апрель",
            "Май",
            "Июнь",
            "Июль",
            "Август",
            "Сентябрь",
            "Октябрь",
            "Ноябрь",
            "Декабрь",
        ]
        return f"{month_names[value.month - 1]} {value.year}"

    def _cash_journal_markdown(
        self,
        *,
        entries: list[dict[str, object]],
        days: list[dict[str, object]],
        weeks: list[dict[str, object]],
        months: list[dict[str, object]],
        totals: dict[str, object],
        meta: dict[str, object],
    ) -> str:
        lines = [
            "# Кассовый журнал",
            "",
            "## Итоги периода",
            f"- Период: последние {meta['months']} мес.",
            f"- Показано операций: {totals['count']} из {meta['total']}",
            f"- Реальные поступления: {totals['external_income_display']}",
            f"- Реальные списания: {totals['external_expense_display']}",
            f"- Итог периода: {totals['balance_display']}",
            f"- Внутренние перемещения: пришло {totals['transfer_income_display']} | ушло {totals['transfer_expense_display']}",
            "",
        ]
        if int(meta["total"]) > int(totals["count"]):
            lines.extend(
                [
                    "Выгрузка ограничена лимитом. Для полного журнала увеличьте лимит выгрузки.",
                    "",
                ]
            )
        if not entries:
            lines.extend(
                [
                    "## Операции",
                    "За выбранный период движений нет.",
                ]
            )
            return "\n".join(lines).strip()

        lines.extend(["## По месяцам"])
        for item in months:
            lines.append(
                f"- {item['label']}: приход {item['external_income_display']} | "
                + f"расход {item['external_expense_display']} | итог {item['balance_display']} | "
                + f"перемещения {item['transfer_income_display']}/{item['transfer_expense_display']} | "
                + f"{item['count']} оп."
            )
        lines.extend(["", "## По неделям"])
        for item in weeks:
            lines.append(
                f"- {item['label']}: приход {item['external_income_display']} | "
                + f"расход {item['external_expense_display']} | итог {item['balance_display']} | "
                + f"перемещения {item['transfer_income_display']}/{item['transfer_expense_display']} | "
                + f"{item['count']} оп."
            )
        lines.extend(["", "## Операции по дням"])
        for day in days:
            display_rows = self._cash_journal_display_rows(day["entries"])
            lines.extend(
                [
                    "",
                    f"### {day['label']}",
                    "Остаток на начало дня:",
                ]
            )
            for balance in day.get("opening_balances") or []:
                lines.append(f"- {balance['cashbox_name']}: {balance['balance_display']}")
            lines.extend(
                [
                    f"Итого: приход {day['external_income_display']} | расход {day['external_expense_display']} | "
                    + f"итог {day['balance_display']} | перемещения {day['transfer_income_display']}/{day['transfer_expense_display']} | "
                    + f"{day['count']} оп.",
                    "",
                ]
            )
            for row in display_rows:
                lines.append(str(row["line"]))
                detail = str(row.get("detail") or "")
                if detail:
                    lines.append(detail)
        return "\n".join(lines).strip()

    def _cash_journal_display_rows(self, entries: list[dict[str, object]]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        used_ids: set[str] = set()
        for item in entries:
            item_id = str(item.get("id") or "")
            if item_id in used_ids:
                continue
            transfer_pair = self._cash_journal_transfer_pair(item, entries, used_ids)
            if transfer_pair is not None:
                source, target = transfer_pair
                used_ids.update({str(source.get("id") or ""), str(target.get("id") or "")})
                rows.append(self._cash_journal_transfer_row(source, target))
                continue
            used_ids.add(item_id)
            rows.append(self._cash_journal_operation_row(item))
        return rows

    def _cash_journal_transfer_pair(
        self,
        item: dict[str, object],
        entries: list[dict[str, object]],
        used_ids: set[str],
    ) -> tuple[dict[str, object], dict[str, object]] | None:
        if item.get("source_label") != "перемещение":
            return None
        related_id = str(item.get("related_transaction_id") or "")
        transfer_group_id = str(item.get("transfer_group_id") or "")
        for candidate in entries:
            candidate_id = str(candidate.get("id") or "")
            if candidate_id in used_ids or candidate_id == str(item.get("id") or ""):
                continue
            if candidate.get("source_label") != "перемещение":
                continue
            same_related = related_id and candidate_id == related_id
            same_group = (
                transfer_group_id
                and str(candidate.get("transfer_group_id") or "") == transfer_group_id
            )
            if not same_related and not same_group:
                continue
            if candidate.get("direction") == item.get("direction"):
                continue
            source = item if item.get("direction") == "expense" else candidate
            target = item if item.get("direction") == "income" else candidate
            return source, target
        return None

    def _cash_journal_transfer_row(
        self, source: dict[str, object], target: dict[str, object]
    ) -> dict[str, str]:
        amount = self._cash_journal_money_text(source.get("amount_minor"))
        line = (
            f"- {source.get('time_short') or '--:--'} | перемещение {amount} | "
            + f"{source.get('cashbox_name')} → {target.get('cashbox_name')}"
        )
        return {
            "line": line,
            "detail": self._cash_journal_detail_line(
                source, prefix="  - Перемещение", include_source=False
            ),
        }

    def _cash_journal_operation_row(self, item: dict[str, object]) -> dict[str, str]:
        action = "приход" if item.get("direction") == "income" else "расход"
        line = (
            f"- {item.get('time_short') or '--:--'} | "
            + f"{item['signed_amount_display']} | {item['cashbox_name']} | "
            + f"{action}: {item['note']}"
        )
        return {"line": line, "detail": self._cash_journal_detail_line(item)}

    def _cash_journal_detail_line(
        self,
        item: dict[str, object],
        *,
        prefix: str = "  - Детали",
        include_source: bool = True,
    ) -> str:
        actor = str(item.get("actor_label") or "").strip()
        source = str(item.get("source_label") or "").strip()
        parts = []
        if actor:
            parts.append(f"оператор {actor}")
        if include_source and source and source not in {"ручное", "api", "система"}:
            parts.append(source)
        if not parts:
            return ""
        return f"{prefix}: " + ", ".join(parts)

    def _cashbox_statistics(
        self,
        cashbox: CashBox,
        transactions: list[CashTransaction],
    ) -> dict[str, object]:
        related = self._cashbox_transactions(transactions, cashbox.id)
        income_minor = sum(item.amount_minor for item in related if item.direction == "income")
        expense_minor = sum(item.amount_minor for item in related if item.direction == "expense")
        balance_minor = income_minor - expense_minor
        return {
            "transactions_total": len(related),
            "income_total_minor": income_minor,
            "income_total_display": self._cashbox_rounded_money_text(income_minor),
            "expense_total_minor": expense_minor,
            "expense_total_display": self._cashbox_rounded_money_text(expense_minor),
            "balance_minor": balance_minor,
            "balance_display": self._cashbox_rounded_money_text(balance_minor),
            "balance_sign": "negative" if balance_minor < 0 else "positive",
            "last_transaction_at": related[0].created_at if related else None,
        }

    def _serialize_cashbox(
        self,
        cashbox: CashBox,
        transactions: list[CashTransaction],
    ) -> dict[str, object]:
        payload = cashbox.to_dict()
        payload["statistics"] = self._cashbox_statistics(cashbox, transactions)
        return payload

    def _ordered_cashboxes(
        self,
        cashboxes: list[CashBox],
        *,
        exclude_cashbox_id: str | None = None,
    ) -> list[CashBox]:
        ordered = [
            cashbox
            for cashbox in cashboxes
            if exclude_cashbox_id is None or cashbox.id != exclude_cashbox_id
        ]
        ordered.sort(
            key=lambda item: (
                item.order,
                item.created_at,
                item.updated_at,
                item.name.casefold(),
                item.id,
            )
        )
        return ordered

    def _renumber_cashboxes(self, cashboxes: list[CashBox]) -> bool:
        changed = False
        for index, cashbox in enumerate(cashboxes):
            if cashbox.order != index:
                cashbox.order = index
                changed = True
        return changed

    def _reposition_cashbox(
        self,
        cashboxes: list[CashBox],
        cashbox: CashBox,
        *,
        before_cashbox_id: str | None = None,
    ) -> tuple[list[CashBox], bool]:
        original_ids = [item.id for item in self._ordered_cashboxes(cashboxes)]
        ordered = self._ordered_cashboxes(cashboxes, exclude_cashbox_id=cashbox.id)
        insert_index = len(ordered)
        if before_cashbox_id:
            before_cashbox = self._find_cashbox(ordered, before_cashbox_id)
            insert_index = next(
                (index for index, item in enumerate(ordered) if item.id == before_cashbox.id),
                len(ordered),
            )
        ordered.insert(insert_index, cashbox)
        changed = [item.id for item in ordered] != original_ids
        if self._renumber_cashboxes(ordered):
            changed = True
        return ordered, changed
