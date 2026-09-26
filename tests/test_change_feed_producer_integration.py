from __future__ import annotations

# ruff: noqa: I001
import unittest

if __package__:
    from tests.source_path_support import ensure_repository_root_path
else:
    from source_path_support import ensure_repository_root_path

ensure_repository_root_path()

from tests.services_case import CardServiceCase

from minimal_kanban.services.change_feed_service import ChangeFeedService  # noqa: E402


class ChangeFeedProducerIntegrationTests(CardServiceCase):
    def _feed(self, consumer_id: str) -> ChangeFeedService:
        feed = ChangeFeedService(self.store.change_feed_store)
        feed.register({"consumer_id": consumer_id, "start_at": "latest"})
        return feed

    def test_inventory_write_off_and_return_collapse_into_two_digest_groups(self) -> None:
        card = self.service.create_card(
            {"vehicle": "Lexus", "title": "Возврат материала", "deadline": {"hours": 1}}
        )["card"]
        item = self.service.save_inventory_item(
            {
                "name": "Антифриз",
                "catalog_number": "AF-G12",
                "unit": "л",
                "quantity": "10",
                "cost_price": "250",
                "sale_price": "420",
            }
        )["item"]
        feed = self._feed("inventory-return-test")
        written_off = self.service.write_off_inventory_item(
            {
                "item_id": item["id"],
                "card_id": card["id"],
                "quantity": "1.5",
                "actor_name": "ADMIN",
            }
        )
        self.service.return_inventory_movement(
            {
                "movement_id": written_off["movement"]["id"],
                "card_id": card["id"],
                "actor_name": "ADMIN",
            }
        )

        page = feed.read({"consumer_id": "inventory-return-test"})
        digest = feed.summarize({"consumer_id": "inventory-return-test", "ack": page["ack"]})

        self.assertEqual(2, digest["category_counts"]["inventory"])
        self.assertGreaterEqual(digest["raw_event_count"], 4)
        self.assertLess(digest["total_events"], digest["raw_event_count"])

    def test_cash_cancellation_and_repair_update_share_one_correlation(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Безналичный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Оплата", "deadline": {"hours": 2}}
        )["card"]
        order = self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "2000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:00",
                            "payment_method": "cashless",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )["card"]["repair_order"]
        self.service.cancel_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "transaction_id": order["payments"][0]["cash_transaction_id"],
                "reason": "Тест корреляции",
                "actor_name": "ADMIN",
            }
        )

        cancellation = next(
            event
            for event in reversed(self.store.read_events())
            if event.action == "cash_transaction_cancelled"
        )
        correlation_id = cancellation.details.get("correlation_id")
        correlated_actions = {
            event.action
            for event in self.store.read_events()
            if event.details.get("correlation_id") == correlation_id
        }

        self.assertTrue(correlation_id)
        self.assertTrue(
            {"cash_transaction_cancelled", "repair_order_updated"} <= correlated_actions
        )

    def test_repair_payments_emit_one_correlated_finance_digest_group(self) -> None:
        supplier = self.service.create_cashbox(
            {"name": "Алексей Снабженец", "actor_name": "ADMIN"}
        )["cashbox"]
        self.service.create_cashbox({"name": "Касса наличных оплат", "actor_name": "ADMIN"})
        self.service.create_cashbox({"name": "Безналичная касса", "actor_name": "ADMIN"})
        self.service.create_cashbox({"name": "На карту", "actor_name": "ADMIN"})
        card = self.service.create_card(
            {"vehicle": "TOYOTA CAMRY", "title": "Оплата", "deadline": {"hours": 2}}
        )["card"]
        feed = self._feed("repair-payment-test")

        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [{"name": "Работы", "quantity": "1", "price": "6000"}],
                    "payments": [
                        {
                            "amount": amount,
                            "paid_at": f"06.04.2026 10:{index}0",
                            "payment_method": method,
                            "cashbox_id": supplier["id"],
                        }
                        for index, (amount, method) in enumerate(
                            (("1000", "cash"), ("2000", "cashless"), ("3000", "card"))
                        )
                    ],
                },
            }
        )

        created = [
            event
            for event in self.store.read_events()
            if event.action == "cash_transaction_created" and event.card_id == card["id"]
        ]
        page = feed.read({"consumer_id": "repair-payment-test"})
        digest = feed.summarize({"consumer_id": "repair-payment-test", "ack": page["ack"]})

        self.assertEqual(3, len(created))
        self.assertTrue(all(event.details.get("transaction_type") == "income" for event in created))
        self.assertEqual(1, len({event.details.get("correlation_id") for event in created}))
        self.assertEqual(1, digest["category_counts"]["finance"])
        self.assertEqual(600000, digest["financial_totals"]["income_minor"])
        self.assertGreater(digest["raw_event_count"], digest["total_events"])


if __name__ == "__main__":
    unittest.main()
