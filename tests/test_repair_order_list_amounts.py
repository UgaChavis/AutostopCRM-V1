from __future__ import annotations

# Fixture import selects this checkout before application imports.
# ruff: noqa: I001
from itertools import product

from tests.services_case import CardServiceCase

from minimal_kanban.models import Card
from minimal_kanban.repair_order import RepairOrder


class RepairOrderListAmountsTests(CardServiceCase):
    def test_list_amounts_match_order_calculations(self):
        payments = (
            [],
            [{"id": "cash", "amount": "1.005", "payment_method": "cash"}],
            [
                {"id": "card", "amount": "10.01", "payment_method": "card"},
                {"id": "bank", "amount": "8.335", "payment_method": "cashless"},
            ],
            [{"id": "overpaid", "amount": "1000", "payment_method": "cashless"}],
        )
        for price, order_payments in product(("0", "0.01", "13.37"), payments):
            with self.subTest(price=price, payments=order_payments):
                order = RepairOrder.from_dict(
                    {
                        "number": "1",
                        "prepayment": "1.005",
                        "works": [{"name": "synthetic", "quantity": "2.1", "price": price}],
                        "materials": [{"name": "synthetic", "quantity": "1", "price": "3.57"}],
                        "payments": order_payments,
                    }
                )
                card = Card(
                    id="synthetic",
                    title="synthetic",
                    description="",
                    column="inbox",
                    archived=False,
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                    deadline_timestamp="2026-01-02T00:00:00+00:00",
                    repair_order=order,
                )
                item = self._build_service()._serialize_repair_order_list_item(
                    card, materialize=False
                )
                expected = {
                    "prepayment": order.prepayment_amount() if order.payments else order.prepayment,
                    "prepayment_display": order.prepayment_amount(),
                    "paid_total": order.prepayment_amount(),
                    "paid_total_display": order.prepayment_amount(),
                    "is_paid": order.is_paid(),
                    "payment_status": order.payment_status(),
                    "payment_status_label": order.payment_status_label(),
                    "subtotal_total": order.subtotal_amount(),
                    "taxes_total": order.taxes_amount(),
                    "works_total": order.works_total_amount(),
                    "grand_total": order.grand_total_amount(),
                    "due_total": order.due_total_amount(),
                }
                self.assertEqual({key: item[key] for key in expected}, expected)
