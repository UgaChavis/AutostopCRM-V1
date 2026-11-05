from __future__ import annotations

from datetime import datetime

from ..models import CashBox, CashTransaction, normalize_actor_name, normalize_text
from .card_service_cashbox_cancellation import CardServiceCashboxCancellationMixin
from .journal_labels import day_label, month_label, week_label


class CardServiceCashJournalMixin(CardServiceCashboxCancellationMixin):
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
            if item.get("direction") == "income"
            and not (item.get("transfer_group_id") or item.get("source_label") == "перемещение")
        )
        external_expense_minor = sum(
            self._cash_journal_minor_value(item.get("amount_minor"))
            for item in entries
            if item.get("direction") == "expense"
            and not (item.get("transfer_group_id") or item.get("source_label") == "перемещение")
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

    _cash_journal_day_label = staticmethod(day_label)

    _cash_journal_week_label = staticmethod(week_label)

    _cash_journal_month_label = staticmethod(month_label)
