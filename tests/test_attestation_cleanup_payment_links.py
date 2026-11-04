"""Synthetic cleanup must not orphan a payment linked by a legacy ID."""

# The fixture import selects this checkout before application imports.
# ruff: noqa: I001
import os
import tempfile
from dataclasses import replace
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.repair_order import RepairOrder, RepairOrderPayment, RepairOrderRow
from minimal_kanban.services.errors import ServiceError


RUN = "AST-GWAT-20260728T165722Z"


class AttestationCleanupPaymentLinksTests(CardServiceCase):
    def setUp(self):
        app_data = tempfile.TemporaryDirectory(prefix="synthetic-cleanup-app-")
        self.addCleanup(app_data.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": app_data.name})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        self.addCleanup(self.store.close)

    def payment(self, card_id, box, transaction_id):
        bundle = self.store.read_bundle()
        card = next(card for card in bundle["cards"] if card.id == card_id)
        card.repair_order.payments = [
            RepairOrderPayment.from_dict(
                {
                    "id": "payment",
                    "amount": "1",
                    "cashbox_id": box,
                    "cash_transaction_id": transaction_id,
                    "note": f"{RUN} synthetic payment",
                }
            )
        ]
        self.store.write_bundle(**bundle)

    def transaction(self, box, direction="income"):
        return self.service.create_cash_transaction(
            {
                "cashbox_id": box,
                "direction": direction,
                "amount_minor": 100,
                "note": f"{RUN} synthetic payment",
            }
        )["transaction"]["id"]

    def box(self):
        return self.service.create_cashbox({"name": f"{RUN}-synthetic"})["cashbox"]["id"]

    def delete_box(self, box):
        snapshot = self.service.get_cashbox({"cashbox_id": box})
        return self.service.delete_cashbox(
            {
                "cashbox_id": box,
                "expected_cashbox_updated_at": snapshot["cashbox"]["updated_at"],
                "expected_transaction_ids": [item["id"] for item in snapshot["transactions"]],
                "attestation_run_id": RUN,
                "actor_name": "CODEX",
                "source": "mcp_agent_gateway_v2",
            }
        )

    def assert_rejected_without_save(self, action, *, status=409):
        self.store.read_bundle()
        before = self.state_file.read_bytes()
        with patch.object(
            self.service, "_save_bundle", side_effect=AssertionError("unexpected save")
        ):
            with self.assertRaises(ServiceError) as failure:
                action()
        self.assertEqual(failure.exception.status_code, status)
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_cashbox_cleanup_rejects_short_and_multiple_owners(self):
        box = self.box()
        incoming = self.transaction(box)
        self.transaction(box, "expense")
        owner = self.service.create_card({"title": "Synthetic owner"})["card"]["id"]
        peer = self.service.create_card({"title": "Synthetic peer"})["card"]["id"]
        self.payment(owner, box, "CT-" + incoming[:8])
        self.assert_rejected_without_save(lambda: self.delete_box(box))
        self.payment(peer, box, incoming)
        self.assert_rejected_without_save(lambda: self.delete_box(box))

    def test_cashbox_cleanup_ignores_unrelated_ambiguous_alias(self):
        box = self.box()
        self.transaction(box)
        self.transaction(box, "expense")
        other_box = self.service.create_cashbox({"name": "Synthetic other"})["cashbox"]["id"]
        tx1, tx2 = self.transaction(other_box), self.transaction(other_box)
        bundle = self.store.read_bundle()
        bundle["cash_transactions"] = [
            replace(item, id={tx1: "eeeeeeee-1", tx2: "eeeeeeee-2"}.get(item.id, item.id))
            for item in bundle["cash_transactions"]
        ]
        self.store.write_bundle(**bundle)
        peer = self.service.create_card({"title": "Synthetic peer"})["card"]["id"]
        self.payment(peer, other_box, "CT-eeeeeeee")
        self.assertTrue(self.delete_box(box)["meta"]["deleted"])

    def test_empty_synthetic_cashbox_can_be_cleaned_up(self):
        box = self.box()
        result = self.delete_box(box)
        self.assertTrue(result["meta"]["deleted"])
        self.assertEqual(result["meta"]["removed_transactions"], 0)

    def test_cashbox_cleanup_rejects_ambiguous_target_alias(self):
        box = self.box()
        incoming, outgoing = self.transaction(box), self.transaction(box, "expense")
        bundle = self.store.read_bundle()
        bundle["cash_transactions"] = [
            replace(item, id=incoming[:8] + "-0000-0000-0000-000000000002")
            if item.id == outgoing
            else item
            for item in bundle["cash_transactions"]
        ]
        self.store.write_bundle(**bundle)
        owner = self.service.create_card({"title": "Synthetic owner"})["card"]["id"]
        self.payment(owner, box, "CT-" + incoming[:8])
        self.assert_rejected_without_save(lambda: self.delete_box(box))

    def test_cashbox_cleanup_rejects_peer_transaction_in_another_cashbox(self):
        box = self.box()
        self.transaction(box)
        source = self.transaction(box, "expense")
        other_box = self.service.create_cashbox({"name": f"{RUN}-other"})["cashbox"]["id"]
        peer = self.transaction(other_box)
        bundle = self.store.read_bundle()
        for transaction in bundle["cash_transactions"]:
            if transaction.id == source:
                transaction.related_transaction_id = peer
                transaction.transfer_group_id = "synthetic-transfer"
            elif transaction.id == peer:
                transaction.related_transaction_id = source
                transaction.transfer_group_id = "synthetic-transfer"
        self.store.write_bundle(**bundle)
        self.assert_rejected_without_save(lambda: self.delete_box(box))
        bundle = self.store.read_bundle()
        for transaction in bundle["cash_transactions"]:
            if transaction.id == source:
                transaction.related_transaction_id = ""
                transaction.transfer_group_id = ""
            elif transaction.id == peer:
                transaction.related_transaction_id = "ct-" + source[:8]
                transaction.transfer_group_id = ""
        self.store.write_bundle(**bundle)
        self.assert_rejected_without_save(lambda: self.delete_box(box))

    def test_gateway_transfer_compensation_cannot_be_cleaned_one_cashbox_at_a_time(self):
        primary = self.box()
        secondary = self.service.create_cashbox({"name": f"{RUN}-secondary"})["cashbox"]["id"]
        transfer = self.service.create_cashbox_transfer(
            {
                "from_cashbox_id": primary,
                "to_cashbox_id": secondary,
                "amount_minor": 100,
                "note": f"{RUN} transfer",
                "actor_name": "CODEX",
                "source": "mcp_agent_gateway_v2",
            }
        )
        primary_box = self.service.get_cashbox({"cashbox_id": primary})["cashbox"]
        secondary_box = self.service.get_cashbox({"cashbox_id": secondary})["cashbox"]
        self.service.cancel_cash_transaction(
            {
                "cashbox_id": secondary,
                "transaction_id": transfer["target_transaction"]["id"],
                "reason": f"{RUN} transfer compensation",
                "attestation_run_id": RUN,
                "expected_cashbox_updated_at": secondary_box["updated_at"],
                "expected_related_cashbox_updated_at": primary_box["updated_at"],
                "actor_name": "CODEX",
                "source": "mcp_agent_gateway_v2",
            }
        )
        for box in (primary, secondary):
            with self.subTest(box=box):
                self.assertEqual(
                    self.service.get_cashbox({"cashbox_id": box})["cashbox"]["statistics"][
                        "balance_minor"
                    ],
                    0,
                )
                self.assert_rejected_without_save(lambda: self.delete_box(box))

    def test_cashbox_cleanup_rejects_salary_movements(self):
        box = self.box()
        self.transaction(box)
        salary_id = self.transaction(box, "expense")
        for case in (
            "salary_payout",
            "salary_advance",
            "salary_payment",
            "employee_id",
            "employee_name",
        ):
            with self.subTest(case=case):
                bundle = self.store.read_bundle()
                salary = next(item for item in bundle["cash_transactions"] if item.id == salary_id)
                salary.transaction_kind = case if case.startswith("salary_") else ""
                salary.employee_id = "synthetic-employee" if case == "employee_id" else ""
                salary.employee_name = "Synthetic Employee" if case == "employee_name" else ""
                self.store.write_bundle(**bundle)
                self.assert_rejected_without_save(lambda: self.delete_box(box))

    def test_payment_cleanup_rejects_a_second_short_owner(self):
        box = self.box()
        transaction_id = self.transaction(box)
        owner = self.service.create_card({"title": f"{RUN}-owner"})["card"]["id"]
        peer = self.service.create_card({"title": "Synthetic peer"})["card"]["id"]
        self.payment(owner, box, transaction_id)
        self.payment(peer, box, "CT-" + transaction_id[:8])
        bundle = self.store.read_bundle()
        next(
            item for item in bundle["cash_transactions"] if item.id == transaction_id
        ).transaction_kind = "repair_order_payment"
        self.store.write_bundle(**bundle)
        card = self.service.get_card({"card_id": owner})["card"]
        cashbox = self.service.get_cashbox({"cashbox_id": box})["cashbox"]
        payload = {
            "card_id": owner,
            "payment_id": "payment",
            "expected_updated_at": card["updated_at"],
            "expected_cashbox_updated_at": cashbox["updated_at"],
            "expected_transaction_ids": [transaction_id],
            "attestation_run_id": RUN,
            "actor_name": "CODEX",
            "source": "mcp_agent_gateway_v2",
        }
        self.assert_rejected_without_save(
            lambda: self.service.delete_gateway_attestation_payment_fixture(payload)
        )

    def test_payment_cleanup_rejects_another_payment_on_the_same_card(self):
        box = self.box()
        transaction_id = self.transaction(box)
        other_box = self.service.create_cashbox({"name": "Synthetic other"})["cashbox"]["id"]
        other_transaction = self.transaction(other_box)
        owner = self.service.create_card({"title": f"{RUN}-owner"})["card"]["id"]
        self.payment(owner, box, transaction_id)
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == owner)
        card.repair_order.payments.append(
            RepairOrderPayment.from_dict(
                {
                    "id": "other",
                    "amount": "1",
                    "cashbox_id": other_box,
                    "cash_transaction_id": other_transaction,
                }
            )
        )
        next(
            item for item in bundle["cash_transactions"] if item.id == transaction_id
        ).transaction_kind = "repair_order_payment"
        self.store.write_bundle(**bundle)
        card = self.service.get_card({"card_id": owner})["card"]
        cashbox = self.service.get_cashbox({"cashbox_id": box})["cashbox"]
        payload = {
            "card_id": owner,
            "payment_id": "payment",
            "expected_updated_at": card["updated_at"],
            "expected_cashbox_updated_at": cashbox["updated_at"],
            "expected_transaction_ids": [transaction_id],
            "attestation_run_id": RUN,
            "actor_name": "CODEX",
            "source": "mcp_agent_gateway_v2",
        }
        self.assert_rejected_without_save(
            lambda: self.service.delete_gateway_attestation_payment_fixture(payload)
        )

    def test_payment_cleanup_rejects_an_external_reverse_link(self):
        box = self.box()
        transaction_id = self.transaction(box)
        other_box = self.service.create_cashbox({"name": "Synthetic other"})["cashbox"]["id"]
        peer_id = self.transaction(other_box)
        owner = self.service.create_card({"title": f"{RUN}-owner"})["card"]["id"]
        self.payment(owner, box, transaction_id)
        bundle = self.store.read_bundle()
        next(
            item for item in bundle["cash_transactions"] if item.id == transaction_id
        ).transaction_kind = "repair_order_payment"
        next(
            item for item in bundle["cash_transactions"] if item.id == peer_id
        ).related_transaction_id = "ct-" + transaction_id[:8]
        self.store.write_bundle(**bundle)
        card = self.service.get_card({"card_id": owner})["card"]
        cashbox = self.service.get_cashbox({"cashbox_id": box})["cashbox"]
        payload = {
            "card_id": owner,
            "payment_id": "payment",
            "expected_updated_at": card["updated_at"],
            "expected_cashbox_updated_at": cashbox["updated_at"],
            "expected_transaction_ids": [transaction_id],
            "attestation_run_id": RUN,
            "actor_name": "CODEX",
            "source": "mcp_agent_gateway_v2",
        }
        self.assert_rejected_without_save(
            lambda: self.service.delete_gateway_attestation_payment_fixture(payload)
        )

    def test_payment_cleanup_rejects_salary_movements(self):
        box = self.box()
        transaction_id = self.transaction(box)
        self.transaction(box)
        salary_id = self.transaction(box, "expense")
        owner = self.service.create_card({"title": f"{RUN}-owner"})["card"]["id"]
        self.payment(owner, box, transaction_id)
        bundle = self.store.read_bundle()
        next(
            item for item in bundle["cash_transactions"] if item.id == transaction_id
        ).transaction_kind = "repair_order_payment"
        self.store.write_bundle(**bundle)
        for case in (
            "salary_payout",
            "salary_advance",
            "salary_payment",
            "employee_id",
            "employee_name",
        ):
            with self.subTest(case=case):
                bundle = self.store.read_bundle()
                salary = next(item for item in bundle["cash_transactions"] if item.id == salary_id)
                salary.transaction_kind = case if case.startswith("salary_") else ""
                salary.employee_id = "synthetic-employee" if case == "employee_id" else ""
                salary.employee_name = "Synthetic Employee" if case == "employee_name" else ""
                self.store.write_bundle(**bundle)
                card = self.service.get_card({"card_id": owner})["card"]
                cashbox = self.service.get_cashbox({"cashbox_id": box})
                payload = {
                    "card_id": owner,
                    "payment_id": "payment",
                    "expected_updated_at": card["updated_at"],
                    "expected_cashbox_updated_at": cashbox["cashbox"]["updated_at"],
                    "expected_transaction_ids": [item["id"] for item in cashbox["transactions"]],
                    "attestation_run_id": RUN,
                    "actor_name": "CODEX",
                    "source": "mcp_agent_gateway_v2",
                }
                self.assert_rejected_without_save(
                    lambda: self.service.delete_gateway_attestation_payment_fixture(payload)
                )

    def test_payment_cleanup_rejects_nonfixture_order_data(self):
        box = self.box()
        transaction_id = self.transaction(box)
        owner = self.service.create_card({"title": f"{RUN}-owner"})["card"]["id"]
        self.payment(owner, box, transaction_id)
        bundle = self.store.read_bundle()
        next(
            item for item in bundle["cash_transactions"] if item.id == transaction_id
        ).transaction_kind = "repair_order_payment"
        self.store.write_bundle(**bundle)
        for case in (
            "fractional",
            "invalid",
            "extra_work",
            "work_payroll",
            "material",
            "note",
            "payroll",
            "legacy",
            "note_suffix",
            "transaction_note",
            "transaction_expense",
        ):
            with self.subTest(case=case):
                bundle = self.store.read_bundle()
                card = next(item for item in bundle["cards"] if item.id == owner)
                card.repair_order = RepairOrder.from_dict(
                    {
                        "payments": [
                            {
                                "id": "payment",
                                "amount": "1",
                                "cashbox_id": box,
                                "cash_transaction_id": transaction_id,
                                "note": f"{RUN} synthetic payment",
                            }
                        ],
                        "works": [{"name": f"{RUN} synthetic work", "quantity": "1", "price": "1"}],
                    }
                )
                order = card.repair_order
                transaction = next(
                    item for item in bundle["cash_transactions"] if item.id == transaction_id
                )
                transaction.note = f"{RUN} synthetic payment"
                transaction.direction = "income"
                if case == "fractional":
                    order.payments[0].amount = "1.001"
                elif case == "invalid":
                    order.payments[0].amount = "invalid"
                elif case == "extra_work":
                    order.works.append(
                        RepairOrderRow.from_dict({"name": "OTHER WORK", "price": "1"})
                    )
                elif case == "work_payroll":
                    order.works[0].salary_amount = "50"
                elif case == "material":
                    order.materials.append(
                        RepairOrderRow.from_dict({"name": "OTHER MATERIAL", "price": "1"})
                    )
                elif case == "note":
                    order.note = "OTHER NOTE"
                elif case == "payroll":
                    order.payroll_postings = [{"id": "other-payroll"}]
                elif case == "legacy":
                    order.client = "LEGACY CLIENT"
                elif case == "note_suffix":
                    order.payments[0].note += " OTHER DATA"
                elif case == "transaction_note":
                    transaction.note += " OTHER DATA"
                elif case == "transaction_expense":
                    transaction.direction = "expense"
                self.store.write_bundle(**bundle)
                snapshot = self.service.get_cashbox({"cashbox_id": box})["cashbox"]
                payload = {
                    "card_id": owner,
                    "payment_id": "payment",
                    "expected_updated_at": card.updated_at,
                    "expected_cashbox_updated_at": snapshot["updated_at"],
                    "expected_transaction_ids": [transaction_id],
                    "attestation_run_id": RUN,
                    "actor_name": "CODEX",
                    "source": "mcp_agent_gateway_v2",
                }
                self.assert_rejected_without_save(
                    lambda: self.service.delete_gateway_attestation_payment_fixture(payload),
                    status=403,
                )

    def test_payment_cleanup_rejects_linked_external_salary_accrual(self):
        box = self.box()
        transaction_id = self.transaction(box)
        owner = self.service.create_card({"title": f"{RUN}-owner"})["card"]["id"]
        self.payment(owner, box, transaction_id)
        bundle = self.store.read_bundle()
        next(
            item for item in bundle["cash_transactions"] if item.id == transaction_id
        ).transaction_kind = "repair_order_payment"
        self.store.write_bundle(**bundle)
        for card_reference in (owner, "c-" + owner[:8], "x" * 129, None):
            with self.subTest(card_reference=card_reference):
                bundle = self.store.read_bundle()
                bundle["settings"]["employee_repair_order_accruals"] = [
                    {
                        "id": "synthetic-accrual",
                        "employee_id": "synthetic-employee",
                        "card_id": card_reference,
                        "kind": "accrual",
                        "amount_minor": 100,
                        "created_at": "2026-01-01T12:00:00+00:00",
                    }
                ]
                if card_reference is None:
                    bundle["settings"]["employee_repair_order_accruals"] = ["malformed-accrual"]
                self.store.write_bundle(**bundle)
                card = self.service.get_card({"card_id": owner})["card"]
                cashbox = self.service.get_cashbox({"cashbox_id": box})["cashbox"]
                payload = {
                    "card_id": owner,
                    "payment_id": "payment",
                    "expected_updated_at": card["updated_at"],
                    "expected_cashbox_updated_at": cashbox["updated_at"],
                    "expected_transaction_ids": [transaction_id],
                    "attestation_run_id": RUN,
                    "actor_name": "CODEX",
                    "source": "mcp_agent_gateway_v2",
                }
                self.assert_rejected_without_save(
                    lambda: self.service.delete_gateway_attestation_payment_fixture(payload)
                )

    def test_payment_cleanup_allows_gateway_note_and_unrelated_salary_accrual(self):
        box = self.box()
        transaction_id = self.transaction(box)
        owner = self.service.create_card({"title": f"{RUN}-owner"})["card"]["id"]
        self.payment(owner, box, transaction_id)
        bundle = self.store.read_bundle()
        transaction = next(
            item for item in bundle["cash_transactions"] if item.id == transaction_id
        )
        transaction.transaction_kind = "repair_order_payment"
        transaction.note = f"{RUN} synthetic repair order payment"
        card = next(item for item in bundle["cards"] if item.id == owner)
        card.repair_order.payments[0].note = transaction.note
        unrelated = [{"id": "synthetic-accrual", "card_id": "unrelated-card"}]
        bundle["settings"]["employee_repair_order_accruals"] = unrelated
        self.store.write_bundle(**bundle)
        card = self.service.get_card({"card_id": owner})["card"]
        cashbox = self.service.get_cashbox({"cashbox_id": box})["cashbox"]
        result = self.service.delete_gateway_attestation_payment_fixture(
            {
                "card_id": owner,
                "payment_id": "payment",
                "expected_updated_at": card["updated_at"],
                "expected_cashbox_updated_at": cashbox["updated_at"],
                "expected_transaction_ids": [transaction_id],
                "attestation_run_id": RUN,
                "actor_name": "CODEX",
                "source": "mcp_agent_gateway_v2",
            }
        )
        self.assertTrue(result["meta"]["deleted"])
        self.assertEqual(
            self.store.read_bundle()["settings"]["employee_repair_order_accruals"], unrelated
        )
