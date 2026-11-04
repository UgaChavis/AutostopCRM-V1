"""Audit and journal must not infer ownership from ambiguous transaction aliases."""

# The fixture import establishes the application source path.
# ruff: noqa: I001
from copy import deepcopy
from dataclasses import replace

from tests.services_case import CardServiceCase
from tests import test_finance_audit_payment_fixes as payment_fixtures

from minimal_kanban.models import normalize_money_minor, short_entity_id
from minimal_kanban.services.finance_audit_payment_fixes import TransactionReferences


class FinanceAuditReferenceAliasesTests(CardServiceCase):
    _case = payment_fixtures.FinanceAuditPaymentFixTests._case

    def test_missing_order_number_never_offers_to_erase_the_movement_note(self):
        for short in (False, True):
            with self.subTest(short=short), self._case("legacy") as (root, service, store, tid):
                bundle = deepcopy(store.read_bundle())
                bundle["cards"][0].repair_order.number = ""
                if short:
                    bundle["cards"][0].repair_order.payments[
                        0
                    ].cash_transaction_id = short_entity_id(tid, prefix="CT")
                store.write_bundle(**bundle)
                audit = service.get_finance_audit()
                issue = next(
                    item
                    for item in audit["issues"]
                    if item["code"] == "stale_default_repair_order_note"
                )
                self.assertFalse(issue["safe_fix_available"])
                before = (root / "state.json").read_bytes()
                result = service.apply_finance_audit_safe_fixes(
                    {"dry_run": False, "issue_ids": [issue["id"]]}
                )
                self.assertFalse(result["meta"]["changed"])
                self.assertEqual((root / "state.json").read_bytes(), before)
                self.assertEqual(
                    next(
                        item for item in store.read_bundle()["cash_transactions"] if item.id == tid
                    ).note,
                    "Заказ-наряд №214",
                )

    def test_reference_index_preserves_exact_priority_and_case_collision(self):
        with self._case("legacy") as (_, _, store, _):
            original = store.read_bundle()["cash_transactions"][0]
            long_id = replace(original, id="12345678-abcd-0000-0000-000000000001")
            exact_id = replace(original, id="CT-12345678")
            index = TransactionReferences([long_id, exact_id])
            self.assertEqual(index.matches("CT-12345678"), [exact_id])
            self.assertEqual(
                {item.id for item in index.matches("ct-12345678")}, {long_id.id, exact_id.id}
            )
            self.assertEqual(index.matches(long_id.id.upper()), [])

    def test_unique_short_link_is_visible_and_can_be_fixed_in_isolation(self):
        with self._case("legacy") as (_, service, store, transaction_id):
            bundle = deepcopy(store.read_bundle())
            short = short_entity_id(transaction_id, prefix="CT")
            bundle["cards"][0].repair_order.payments[0].cash_transaction_id = short.lower()
            store.write_bundle(**bundle)
            audit = service.get_finance_audit()
            codes = {issue["code"] for issue in audit["issues"]}
            self.assertNotIn("payment_missing_cash_transaction", codes)
            context = service._repair_order_transaction_context(
                bundle["cards"], bundle["cash_transactions"], bundle["cashboxes"]
            )
            self.assertEqual(context[transaction_id]["repair_order_number"], "214")
            cashbox_id = bundle["cash_transactions"][0].cashbox_id
            entries = service.get_cashbox({"cashbox_id": cashbox_id})["transactions"]
            item = next(entry for entry in entries if entry["id"] == transaction_id)
            self.assertEqual(item["repair_order_number"], "214")
            self.assertEqual(item["link_status"], "linked_legacy")
            entries = service.get_cash_journal({"months": 12})["entries"]
            item = next(entry for entry in entries if entry["id"] == transaction_id)
            self.assertEqual(item["repair_order_number"], "214")
            result = service.apply_finance_audit_safe_fixes({"dry_run": False})
            self.assertTrue(any(fix["kind"] == "set_transaction_kind" for fix in result["applied"]))

    def test_duplicate_exact_or_short_ownership_never_offers_payment_fixes(self):
        for duplicate_short in (False, True):
            with (
                self.subTest(short=duplicate_short),
                self._case("legacy") as (_, service, store, transaction_id),
            ):
                bundle = deepcopy(store.read_bundle())
                card = deepcopy(bundle["cards"][0])
                card.id = "other-card"
                card.repair_order.number = "999"
                card.repair_order.payments[0].id = "other-payment"
                card.repair_order.payments[0].cash_transaction_id = (
                    short_entity_id(transaction_id, prefix="CT")
                    if duplicate_short
                    else transaction_id
                )
                bundle["cards"].append(card)
                bundle["cash_transactions"][0].note = "Заказ-наряд №1"
                store.write_bundle(**bundle)
                audit = service.get_finance_audit()
                self.assertTrue(
                    any(
                        issue["code"] == "duplicate_repair_order_payment_cash_link"
                        for issue in audit["issues"]
                    )
                )
                self.assertFalse(
                    any(
                        issue["safe_fix_available"]
                        for issue in audit["issues"]
                        if issue["cash_transaction_id"] == transaction_id
                    )
                )
                self.assertNotIn(
                    transaction_id,
                    service._repair_order_transaction_context(
                        bundle["cards"], bundle["cash_transactions"], bundle["cashboxes"]
                    ),
                )
                self.assertFalse(
                    service.apply_finance_audit_safe_fixes({"dry_run": False})["meta"]["changed"]
                )

    def test_short_cancellation_backreference_blocks_all_payment_fixes(self):
        with self._case("cancellation_backref") as (_, service, store, transaction_id):
            bundle = deepcopy(store.read_bundle())
            peer = next(
                item for item in bundle["cash_transactions"] if item.id == "cancellation-peer"
            )
            peer.related_transaction_id = short_entity_id(transaction_id, prefix="CT").lower()
            bundle["cash_transactions"][0].note = "Заказ-наряд №1"
            store.write_bundle(**bundle)
            audit = service.get_finance_audit()
            self.assertFalse(
                any(
                    issue["safe_fix_available"]
                    for issue in audit["issues"]
                    if issue["cash_transaction_id"] == transaction_id
                )
            )
            self.assertFalse(
                service.apply_finance_audit_safe_fixes({"dry_run": False})["meta"]["changed"]
            )

    def test_ambiguous_short_reference_blocks_both_candidates(self):
        with self._case("legacy") as (_, service, store, transaction_id):
            bundle = deepcopy(store.read_bundle())
            transaction = bundle["cash_transactions"][0]
            other_id = transaction_id.split("-")[0] + "-ffff-0000-0000-000000000002"
            bundle["cash_transactions"].append(replace(transaction, id=other_id))
            bundle["cards"][0].repair_order.payments[0].cash_transaction_id = short_entity_id(
                transaction_id, prefix="CT"
            )
            store.write_bundle(**bundle)
            audit = service.get_finance_audit()
            self.assertTrue(
                any(
                    issue["code"] == "payment_cash_transaction_ambiguous"
                    for issue in audit["issues"]
                )
            )
            self.assertFalse(
                any(
                    issue["safe_fix_available"]
                    for issue in audit["issues"]
                    if issue["cash_transaction_id"] in {transaction_id, other_id}
                )
            )
            self.assertEqual(
                {},
                service._repair_order_transaction_context(
                    bundle["cards"], bundle["cash_transactions"], bundle["cashboxes"]
                ),
            )

    def test_untrusted_amount_cashbox_and_payroll_have_no_safe_fix_or_context(self):
        variants = (
            "fractional",
            "overflow",
            "empty_cashbox",
            "salary",
            "avans",
            "cashbox_normalization",
            "employee_id",
            "employee_name",
        )
        for variant in variants:
            with (
                self.subTest(variant=variant),
                self._case("legacy") as (_, service, store, transaction_id),
            ):
                bundle = deepcopy(store.read_bundle())
                payment = bundle["cards"][0].repair_order.payments[0]
                transaction = bundle["cash_transactions"][0]
                transaction.note = "Заказ-наряд №1"
                if variant in {"fractional", "overflow"}:
                    payment.amount = "1.001" if variant == "fractional" else "1000000000000.01"
                    transaction.amount_minor = normalize_money_minor(payment.amount, default=0)
                elif variant == "empty_cashbox":
                    payment.cashbox_id = ""
                elif variant in {"salary", "avans", "cashbox_normalization"}:
                    transaction.transaction_kind = variant
                else:
                    setattr(transaction, variant, "SYNTHETIC")
                store.write_bundle(**bundle)
                audit = service.get_finance_audit()
                if variant in {"fractional", "overflow", "empty_cashbox"}:
                    mismatch = next(
                        issue
                        for issue in audit["issues"]
                        if issue["code"] == "linked_payment_cash_transaction_mismatch"
                    )
                    expected_reason = "cashbox" if variant == "empty_cashbox" else "amount"
                    self.assertIn(expected_reason, mismatch["data"]["mismatch_reasons"])
                    if variant in {"fractional", "overflow"}:
                        self.assertIsNone(mismatch["data"]["expected_amount_minor"])
                payment_issues = [
                    issue
                    for issue in audit["issues"]
                    if issue["cash_transaction_id"] == transaction_id
                    and issue["code"]
                    in {
                        "legacy_order_note_without_kind",
                        "linked_payment_transaction_missing_kind",
                        "stale_default_repair_order_note",
                    }
                ]
                self.assertTrue(payment_issues)
                self.assertFalse(any(issue["safe_fix_available"] for issue in payment_issues))
                self.assertNotIn(
                    transaction_id,
                    service._repair_order_transaction_context(
                        bundle["cards"], bundle["cash_transactions"], bundle["cashboxes"]
                    ),
                )
                result = service.apply_finance_audit_safe_fixes(
                    {"dry_run": False, "issue_ids": [issue["id"] for issue in payment_issues]}
                )
                self.assertFalse(result["meta"]["changed"])

    def test_incoming_peer_alias_blocks_target_but_unrelated_peer_does_not(self):
        for related in (True, False):
            with (
                self.subTest(related=related),
                self._case("legacy") as (_, service, store, transaction_id),
            ):
                bundle = deepcopy(store.read_bundle())
                transaction = bundle["cash_transactions"][0]
                peer = replace(
                    transaction,
                    id="other-peer",
                    related_transaction_id=(
                        short_entity_id(transaction_id, prefix="CT") if related else "missing-peer"
                    ),
                )
                bundle["cash_transactions"].append(peer)
                store.write_bundle(**bundle)
                audit = service.get_finance_audit()
                fixes = [
                    issue
                    for issue in audit["issues"]
                    if issue["cash_transaction_id"] == transaction_id
                    and issue["safe_fix_available"]
                ]
                self.assertEqual(bool(fixes), not related)
                context = service._repair_order_transaction_context(
                    bundle["cards"], bundle["cash_transactions"], bundle["cashboxes"]
                )
                self.assertEqual(transaction_id in context, not related)
