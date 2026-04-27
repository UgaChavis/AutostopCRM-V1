from __future__ import annotations
# ruff: noqa: I001,E402,F401,UP017,F841,UP012

import base64
import json
from datetime import datetime, timedelta, timezone
import logging
from datetime import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from attachment_samples import (
    GIF_1X1_BYTES,
    JPEG_1X1_BYTES,
    PNG_1X1_BYTES,
    minimal_docx_bytes,
    minimal_pdf_bytes,
    minimal_text_bytes,
    minimal_xlsx_bytes,
)
from minimal_kanban.models import CARD_DESCRIPTION_LIMIT, AuditEvent, Card, utc_now
from minimal_kanban.agent.config import get_agent_name
from minimal_kanban.repair_order import RepairOrder
from minimal_kanban.services.card_service import CardService, ServiceError
from minimal_kanban.storage.financial_history_cleanup import sanitize_financial_history_state
from minimal_kanban.storage.json_store import JsonStore
from minimal_kanban.vehicle_profile import VehicleProfile


class _FakeAgentControl:
    def __init__(self) -> None:
        self.created_payloads: list[dict[str, object]] = []
        self.autofill_calls: list[dict[str, object]] = []
        self.board_control_calls: list[dict[str, object]] = []
        self.active_card_tasks: set[tuple[str, str | None]] = set()
        self.latest_task_by_card: dict[tuple[str, str | None], dict[str, object]] = {}

    def handle_card_created(self, payload: dict | None = None) -> dict:
        payload = dict(payload or {})
        self.created_payloads.append(payload)
        return {"launched": [], "meta": {"matched": 0}}

    def agent_status(self, payload: dict | None = None) -> dict:
        _ = payload
        return {
            "agent": {
                "enabled": True,
                "available": True,
                "ready": True,
                "availability_reason": "worker_running",
                "configured": True,
                "model": "gpt-test",
                "board_api_url": "http://127.0.0.1:41731",
            },
            "ai_remodel": {},
            "board_control": {},
            "worker": {
                "embedded": True,
                "running": True,
                "heartbeat_fresh": True,
            },
            "scheduler": {
                "last_run_at": "",
                "last_success_at": "",
                "last_error": "",
            },
            "status": {
                "running": True,
                "current_task_id": None,
                "current_run_id": None,
                "last_heartbeat": utc_now().isoformat(),
                "last_run_started_at": "",
                "last_run_finished_at": "",
                "last_error": "",
                "last_scheduler_run_at": "",
                "last_scheduler_success_at": "",
                "last_scheduler_error": "",
                "board_control": {},
            },
            "queue": {"pending_total": 0, "running_total": 0},
            "scheduled": {"total": 0, "active_total": 0, "paused_total": 0},
            "recent_runs": [],
        }

    def agent_tasks(self, payload: dict | None = None) -> dict:
        _ = payload
        return {"tasks": [], "meta": {"limit": 50, "statuses": []}}

    def agent_actions(self, payload: dict | None = None) -> dict:
        _ = payload
        return {"actions": [], "meta": {"limit": 100, "run_id": None, "task_id": None}}

    def agent_scheduled_tasks(self, payload: dict | None = None) -> dict:
        _ = payload
        return {"tasks": [], "meta": {"total": 0}}

    def save_agent_scheduled_task(self, payload: dict | None = None) -> dict:
        _ = payload
        return {"task": {"id": "schedule-1"}}

    def delete_agent_scheduled_task(self, payload: dict | None = None) -> dict:
        _ = payload
        return {"deleted": True, "task_id": "schedule-1"}

    def pause_agent_scheduled_task(self, payload: dict | None = None) -> dict:
        _ = payload
        return {"task": {"id": "schedule-1", "active": False}}

    def resume_agent_scheduled_task(self, payload: dict | None = None) -> dict:
        _ = payload
        return {"task": {"id": "schedule-1", "active": True}}

    def run_agent_scheduled_task(self, payload: dict | None = None) -> dict:
        _ = payload
        return {
            "task": {"id": "schedule-1"},
            "scheduled_task": {"id": "schedule-1"},
            "meta": {"already_running": False},
        }

    def agent_enqueue_task(self, payload: dict | None = None) -> dict:
        payload = dict(payload or {})
        self.autofill_calls.append({"payload": payload, "source": "ui", "trigger": "manual"})
        return {
            "task": {
                "id": f"task-{len(self.autofill_calls)}",
                "created_at": utc_now().isoformat(),
                "status": "pending",
                "metadata": payload.get("metadata", {}),
            }
        }

    def enqueue_card_autofill_task(
        self,
        payload: dict | None = None,
        *,
        source: str = "ui_card_autofill",
        trigger: str = "manual",
        purpose: str = "card_autofill",
        mode: str | None = None,
    ) -> dict | None:
        payload = dict(payload or {})
        self.autofill_calls.append(
            {
                "payload": payload,
                "source": source,
                "trigger": trigger,
                "purpose": purpose,
                "mode": mode,
            }
        )
        return {
            "id": f"task-{len(self.autofill_calls)}",
            "created_at": utc_now().isoformat(),
        }

    def enqueue_board_control_task(
        self,
        payload: dict | None = None,
        *,
        source: str = "agent_board_control",
        trigger: str = "scheduled_board_control",
    ) -> dict | None:
        payload = dict(payload or {})
        self.board_control_calls.append({"payload": payload, "source": source, "trigger": trigger})
        return {
            "id": f"board-task-{len(self.board_control_calls)}",
            "created_at": utc_now().isoformat(),
        }

    def has_active_task_for_card(self, card_id: str, *, purpose: str | None = None) -> bool:
        return (card_id, purpose) in self.active_card_tasks

    def latest_task_for_card(self, card_id: str, *, purpose: str | None = None) -> dict | None:
        return self.latest_task_by_card.get((card_id, purpose))


class CardServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.service.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.service = CardService(self.store, self.logger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build_service(self) -> CardService:
        return CardService(
            self.store,
            self.logger,
            attachments_dir=Path(self.temp_dir.name) / "attachments",
            repair_orders_dir=Path(self.temp_dir.name) / "repair-orders",
        )

    def _patch_time(self, moment: datetime):
        return (
            patch("minimal_kanban.services.card_service.utc_now", return_value=moment),
            patch(
                "minimal_kanban.services.card_service.utc_now_iso", return_value=moment.isoformat()
            ),
            patch("minimal_kanban.models.utc_now", return_value=moment),
        )

    def test_card_lifecycle_with_deadline(self) -> None:
        base = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            created = self.service.create_card(
                {
                    "vehicle": "KIA RIO",
                    "title": "Задача",
                    "description": "Текст",
                    "deadline": {"days": 1, "hours": 4},
                }
            )
        card_id = created["card"]["id"]
        self.assertEqual(created["card"]["vehicle"], "KIA RIO")
        self.assertEqual(created["card"]["status"], "ok")
        self.assertEqual(created["card"]["indicator"], "green")

        moved = self.service.move_card({"card_id": card_id, "column": "in_progress"})
        self.assertEqual(moved["card"]["column"], "in_progress")

        update_time = base + timedelta(hours=1)
        patches = self._patch_time(update_time)
        with patches[0], patches[1], patches[2]:
            updated = self.service.update_card(
                {
                    "card_id": card_id,
                    "vehicle": "KIA RIO X",
                    "title": "Задача 2",
                    "description": "Новый текст",
                    "deadline": {"days": 0, "hours": 3},
                }
            )
        self.assertEqual(updated["card"]["vehicle"], "KIA RIO X")
        self.assertEqual(updated["card"]["title"], "Задача 2")
        self.assertEqual(updated["card"]["description"], "Новый текст")
        self.assertEqual(updated["card"]["status"], "ok")
        self.assertTrue(updated["meta"]["changed"])
        self.assertEqual(
            set(updated["meta"]["changed_fields"]), {"vehicle", "title", "description", "deadline"}
        )

        archived = self.service.archive_card({"card_id": card_id})
        self.assertTrue(archived["card"]["archived"])

    def test_clients_can_be_created_searched_and_linked_to_card(self) -> None:
        client = self.service.create_client(
            {
                "client_type": "person",
                "last_name": "Иванов",
                "first_name": "Иван",
                "middle_name": "Иванович",
                "phone": "+7 913 000-11-22",
            }
        )["client"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota Camry",
                "title": "Диагностика",
                "description": "Первичный осмотр",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "customer_name": "Иванов",
                    "customer_phone": "+7 913 000-11-22",
                    "vin": "JTDBE32K620123456",
                    "registration_plate": "А123ВС124",
                },
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "",
                    "phone": "",
                    "vehicle": "Toyota Camry",
                    "vin": "JTDBE32K620123456",
                    "license_plate": "А123ВС124",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                },
            }
        )

        search = self.service.search_clients({"query": "Иванов", "limit": 5})
        self.assertEqual(search["clients"][0]["id"], client["id"])

        linked = self.service.link_card_to_client(
            {"card_id": card_id, "client_id": client["id"], "sync_fields": True}
        )
        self.assertEqual(linked["card"]["client_id"], client["id"])
        self.assertEqual(linked["card"]["repair_order"]["client"], "Иванов Иван Иванович")
        self.assertEqual(linked["card"]["repair_order"]["phone"], "+7 913 000-11-22")

        profile = self.service.get_client({"client_id": client["id"]})
        self.assertEqual(profile["client"]["stats"]["repair_orders_total"], 1)
        self.assertEqual(profile["vehicles"][0]["vin"], "JTDBE32K620123456")
        self.assertEqual(profile["repair_orders"][0]["card_id"], card_id)

    def test_client_api_payload_accepts_nested_client_and_patch(self) -> None:
        created = self.service.create_client(
            {
                "client": {
                    "client_type": "ooo",
                    "legal_name": "ООО Ромашка",
                    "short_name": "Ромашка",
                    "inn": "5400000000",
                    "phone": "+7 913 222-33-44",
                }
            }
        )["client"]

        self.assertEqual(created["client_type"], "ooo")
        self.assertEqual(created["legal_name"], "ООО Ромашка")
        self.assertEqual(created["inn"], "5400000000")

        updated = self.service.update_client(
            {
                "client_id": created["id"],
                "patch": {
                    "contact_person": "Иванов Иван",
                    "comment": "Проверка nested patch",
                },
            }
        )["client"]

        self.assertEqual(updated["contact_person"], "Иванов Иван")
        self.assertEqual(updated["comment"], "Проверка nested patch")

    def test_client_matching_treats_plus_seven_and_eight_phone_as_same(self) -> None:
        client = self.service.create_client(
            {
                "last_name": "Сидоров",
                "first_name": "Семен",
                "phone": "+7 (913) 333-44-55",
            }
        )["client"]
        created = self.service.create_card(
            {
                "vehicle": "Nissan X-Trail",
                "title": "Осмотр",
                "description": "Разовая запись",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "customer_name": "Сидоров Семен",
                    "customer_phone": "8 913 333-44-55",
                    "vin": "JN1TANT32U0012345",
                },
            }
        )
        card_id = created["card"]["id"]

        suggestion = self.service.suggest_clients_for_card({"card_id": card_id, "limit": 5})
        self.assertEqual(suggestion["clients"][0]["id"], client["id"])

        stats = self.service.get_client_stats({"client_id": client["id"]})
        self.assertEqual(stats["stats"]["cards_total"], 1)

    def test_client_search_uses_related_vehicle_plate_vin_and_phone_formats(self) -> None:
        client = self.service.create_client(
            {
                "last_name": "Петров",
                "first_name": "Петр",
                "phone": "+7 (913) 555-66-77",
            }
        )["client"]
        self.service.create_card(
            {
                "vehicle": "Toyota Camry",
                "title": "Плановое ТО",
                "description": "Тест поиска клиента по автомобилю",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "customer_name": "Петров Петр",
                    "customer_phone": "8 913 555 66 77",
                    "vin": "JTDBE32K620654321",
                    "registration_plate": "А555ВС124",
                },
            }
        )

        for query in ("А555ВС124", "а555вс124", "Camry", "JTDBE32K620654321", "89135556677"):
            with self.subTest(query=query):
                search = self.service.search_clients({"query": query, "limit": 5})
                self.assertTrue(search["clients"])
                self.assertEqual(search["clients"][0]["id"], client["id"])

    def test_client_search_matches_common_russian_phone_variants(self) -> None:
        client = self.service.create_client(
            {
                "last_name": "Смирнов",
                "first_name": "Илья",
                "phone": "+7 (901) 222-33-44",
            }
        )["client"]
        for query in (
            "+7 901 222 33 44",
            "8 901 222 33 44",
            "89012223344",
            "79012223344",
            "+7(901)222-33-44",
        ):
            with self.subTest(query=query):
                search = self.service.search_clients({"query": query, "limit": 5})
                self.assertTrue(search["clients"])
                self.assertEqual(search["clients"][0]["id"], client["id"])

    def test_delete_client_rejects_linked_cards_unless_explicitly_allowed(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Тестовый клиент на удаление",
                "phone": "+7 913 444-55-66",
            }
        )["client"]
        created = self.service.create_card(
            {
                "vehicle": "Honda Fit",
                "title": "Удаление клиента",
                "description": "Проверка безопасного удаления",
                "deadline": {"hours": 1},
            }
        )
        card_id = created["card"]["id"]
        self.service.link_card_to_client({"card_id": card_id, "client_id": client["id"]})

        with self.assertRaisesRegex(ServiceError, "Нельзя удалить клиента"):
            self.service.delete_client({"client_id": client["id"]})

        deleted = self.service.delete_client({"client_id": client["id"], "allow_linked": True})
        self.assertTrue(deleted["meta"]["deleted"])
        self.assertEqual(deleted["meta"]["unlinked_cards"], 1)

        card = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(card["client_id"], "")
        search = self.service.search_clients({"query": "Тестовый клиент на удаление"})
        self.assertEqual(search["clients"], [])

    def test_delete_unlinked_client_removes_profile(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Временный клиент MCP",
                "phone": "+7 913 777-88-99",
            }
        )["client"]

        deleted = self.service.delete_client({"client_id": client["id"]})

        self.assertTrue(deleted["meta"]["deleted"])
        self.assertEqual(deleted["meta"]["unlinked_cards"], 0)
        search = self.service.search_clients({"query": "Временный клиент MCP"})
        self.assertEqual(search["clients"], [])

    def test_archive_card_rejects_open_repair_order(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "Открытый заказ-наряд",
                "description": "Проверить подвеску",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "18",
                    "status": "open",
                    "client": "Иван Иванов",
                    "vehicle": "KIA RIO",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "2000"}],
                },
            }
        )

        with self.assertRaises(ServiceError) as raised:
            self.service.archive_card({"card_id": card_id})

        self.assertEqual(raised.exception.code, "repair_order_open_archive_blocked")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("открыт заказ-наряд", raised.exception.message)

    def test_archive_card_allows_closed_repair_order(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        created = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "Закрытый заказ-наряд",
                "description": "Выдать автомобиль",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "19",
                    "status": "closed",
                    "client": "Иван Иванов",
                    "vehicle": "KIA RIO",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "2000"}],
                    "payments": [
                        {
                            "amount": "2000",
                            "paid_at": "06.04.2026 10:00",
                            "payment_method": "cash",
                            "cashbox_id": cashbox["id"],
                        }
                    ],
                },
            }
        )

        archived = self.service.archive_card({"card_id": card_id})
        self.assertTrue(archived["card"]["archived"])

    def test_close_repair_order_requires_full_payment(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota Corolla",
                "title": "Закрытие без оплаты",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                },
            }
        )

        with self.assertRaises(ServiceError) as raised:
            self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})

        self.assertEqual(raised.exception.code, "repair_order_payment_required")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("выполнить оплату", raised.exception.message.lower())

    def test_update_repair_order_rejects_unpaid_closed_status(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota Corolla",
                "title": "Обход закрытия",
                "deadline": {"hours": 2},
            }
        )

        with self.assertRaises(ServiceError) as raised:
            self.service.update_repair_order(
                {
                    "card_id": created["card"]["id"],
                    "repair_order": {
                        "status": "closed",
                        "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                    },
                }
            )

        self.assertEqual(raised.exception.code, "repair_order_payment_required")
        self.assertEqual(raised.exception.status_code, 409)

    def test_create_card_does_not_materialize_repair_order_before_first_open(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota Corolla",
                "title": "Ленивая карточка",
                "description": "Пока без заказ-наряда",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        listed_before = self.service.list_repair_orders()
        self.assertEqual(listed_before["meta"]["total"], 0)

        fetched = self.service.get_repair_order(
            {"card_id": card_id, "actor_name": "UI", "source": "ui"}
        )
        self.assertTrue(fetched["meta"]["has_any_data"])
        self.assertTrue(fetched["meta"]["created"])
        self.assertEqual(fetched["repair_order"]["reason"], "Ленивая карточка")
        self.assertEqual(fetched["repair_order"]["comment"], "Пока без заказ-наряда")
        self.assertEqual(fetched["card"]["repair_order"]["number"], "1")

        listed_after = self.service.list_repair_orders()
        self.assertEqual(listed_after["meta"]["total"], 1)
        self.assertEqual(listed_after["repair_orders"][0]["card_id"], card_id)

    def test_set_card_ai_autofill_returns_retired_cleanup_state_and_clears_legacy_fields(
        self,
    ) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota Corolla",
                "title": "Legacy AI",
                "description": "Старый режим.",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == card_id)
        card.ai_autofill_active = True
        card.ai_autofill_until = "2026-04-12T10:00:00+00:00"
        card.ai_next_run_at = "2026-04-12T09:00:00+00:00"
        card.ai_autofill_prompt = "Старый prompt"
        card.last_card_fingerprint = "legacy-fingerprint"
        card.ai_run_count = 3
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        result = self.service.set_card_ai_autofill(
            {"card_id": card_id, "enabled": True, "actor_name": "AI"}
        )

        self.assertFalse(result["meta"]["enabled"])
        self.assertFalse(result["meta"]["launched"])
        self.assertTrue(result["meta"]["retired"])
        self.assertTrue(result["meta"]["cleanup_available"])
        self.assertEqual(result["meta"]["reason"], "legacy_agent_runtime_disabled")
        self.assertFalse(result["card"]["ai_autofill_active"])
        self.assertEqual(result["card"]["ai_autofill_until"], "")
        self.assertEqual(result["card"]["ai_next_run_at"], "")
        self.assertEqual(result["card"]["ai_autofill_prompt"], "")
        self.assertEqual(result["card"]["last_card_fingerprint"], "")
        self.assertEqual(result["card"]["ai_run_count"], 0)
        self.assertIn(
            "Старое автосопровождение отключено. Доступна только локальная уборка карточки.",
            [entry["message"] for entry in result["card"]["ai_autofill_log"]],
        )

    def test_cleanup_card_content_normalizes_description_and_fills_obvious_local_fields(
        self,
    ) -> None:
        created = self.service.create_card(
            {
                "title": "Течь антифриза",
                "description": "Клиент: Иван Иванов\nТелефон: 89001112233\nVIN: WAUZZZ8V0JA000001\nТечь антифриза\nпроверить радиатор\nпроверить радиатор",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        result = self.service.cleanup_card_content({"card_id": card_id, "actor_name": "ОПЕРАТОР"})

        self.assertTrue(result["meta"]["changed"])
        self.assertTrue(result["meta"]["verify"]["passed"])
        self.assertEqual(result["meta"]["cleanup_mode"], "local_card_cleanup")
        self.assertIn("СУТЬ", result["card"]["description"])
        self.assertIn("ФАКТЫ", result["card"]["description"])
        self.assertIn("РАБОТЫ / ПРОВЕРКИ", result["card"]["description"])
        self.assertEqual(result["card"]["vehicle_profile"]["customer_name"], "Иван Иванов")
        self.assertEqual(result["card"]["vehicle_profile"]["vin"], "WAUZZZ8V0JA000001")
        self.assertEqual(result["card"]["vehicle_profile"]["customer_phone"], "+7 900 111-22-33")

    def test_cleanup_card_content_does_not_overwrite_manual_fields(self) -> None:
        created = self.service.create_card(
            {
                "title": "Диагностика",
                "description": "Клиент: Иван Иванов\nТелефон: 89001112233\nVIN: WAUZZZ8V0JA000001",
                "vehicle_profile": {
                    "customer_name": "Петр Петров",
                    "customer_phone": "+7 999 000-00-00",
                },
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        result = self.service.cleanup_card_content({"card_id": card_id, "actor_name": "ОПЕРАТОР"})

        self.assertEqual(result["card"]["vehicle_profile"]["customer_name"], "Петр Петров")
        self.assertEqual(result["card"]["vehicle_profile"]["customer_phone"], "+7 999 000-00-00")
        self.assertEqual(result["card"]["vehicle_profile"]["vin"], "WAUZZZ8V0JA000001")

    def test_run_full_card_enrichment_delegates_to_local_cleanup(self) -> None:
        created = self.service.create_card(
            {
                "title": "Enrichment",
                "description": "Клиент: Иван\nТелефон: 89001112233\nПроверить радиатор",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        result = self.service.run_full_card_enrichment({"card_id": card_id, "actor_name": "AI"})

        self.assertEqual(result["meta"]["scenario_id"], "card_cleanup")
        self.assertTrue(result["meta"]["retired"])
        self.assertEqual(result["meta"]["legacy_request"], "run_full_card_enrichment")
        self.assertEqual(result["meta"]["cleanup_mode"], "local_card_cleanup")
        self.assertFalse(result["card"]["ai_autofill_active"])

    def test_run_full_card_enrichment_enqueues_agent_task_when_agent_is_attached(self) -> None:
        agent_control = _FakeAgentControl()
        self.service.attach_agent_control(agent_control)
        created = self.service.create_card(
            {
                "title": "Agent enrichment",
                "description": "VIN: WAUZZZ8V0JA000001\nПроверить радиатор",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        result = self.service.run_full_card_enrichment(
            {
                "card_id": card_id,
                "actor_name": "AI",
                "context_packet": {
                    "kind": "compact_context",
                    "scenario_id": "full_card_enrichment",
                },
            }
        )

        self.assertTrue(result["meta"]["launched"])
        self.assertFalse(result["meta"]["already_running"])
        self.assertEqual(result["meta"]["scenario_id"], "full_card_enrichment")
        self.assertTrue(result["meta"]["server_available"])
        self.assertEqual(
            agent_control.autofill_calls[-1]["payload"]["scenario_id"], "full_card_enrichment"
        )
        self.assertEqual(agent_control.autofill_calls[-1]["purpose"], "full_card_enrichment")
        self.assertEqual(agent_control.autofill_calls[-1]["mode"], "full_card_enrichment")
        prompt_text = str(agent_control.autofill_calls[-1]["payload"].get("task_text", ""))
        self.assertIn("полное заполнение", prompt_text.lower())
        self.assertIn("update_card", prompt_text)
        self.assertIn("update_repair_order", prompt_text)
        self.assertIn("replace_repair_order_works", prompt_text)
        self.assertIn("replace_repair_order_materials", prompt_text)
        self.assertEqual(agent_control.autofill_calls[-1]["source"], "ui_full_card_enrichment")

    def test_set_card_ai_autofill_enqueues_agent_task_when_agent_is_attached(self) -> None:
        agent_control = _FakeAgentControl()
        self.service.attach_agent_control(agent_control)
        created = self.service.create_card(
            {
                "title": "Auto enrich",
                "description": "VIN: WAUZZZ8V0JA000001",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        result = self.service.set_card_ai_autofill(
            {
                "card_id": card_id,
                "enabled": True,
                "prompt": "Не переписывай лишнее",
                "actor_name": "AI",
            }
        )

        self.assertTrue(result["meta"]["enabled"])
        self.assertTrue(result["meta"]["launched"])
        self.assertTrue(result["meta"]["server_available"])
        self.assertEqual(agent_control.autofill_calls[-1]["source"], "ui_full_card_enrichment")
        self.assertEqual(agent_control.autofill_calls[-1]["trigger"], "manual_activate")
        self.assertEqual(agent_control.autofill_calls[-1]["purpose"], "full_card_enrichment")
        self.assertEqual(agent_control.autofill_calls[-1]["mode"], "full_card_enrichment")

    def test_trigger_due_ai_followups_is_disabled(self) -> None:
        self.assertEqual(self.service.trigger_due_ai_followups(), {"launched": [], "failed": []})

    def test_agent_originated_update_does_not_refresh_legacy_fingerprint(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota Corolla",
                "title": "Автосопровождение",
                "description": "Первичный текст",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == card_id)
        card.last_card_fingerprint = "legacy-fingerprint"
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "description": "Первичный текст\nVIN: WAUZZZ8V0JA000001",
                "actor_name": get_agent_name(),
            }
        )

        self.assertEqual(updated["card"]["last_card_fingerprint"], "legacy-fingerprint")

    def test_inconsistent_archived_card_with_open_repair_order_is_blocked_and_hidden(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota Corolla",
                "title": "Неконсистентная карточка",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == card_id)
        card.archived = True
        card.repair_order = RepairOrder.from_dict(
            {
                "number": "5",
                "status": "open",
                "client": "Иван",
                "vehicle": "Toyota Corolla",
                "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
            }
        )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        listed = self.service.list_repair_orders()
        self.assertEqual(listed["meta"]["inconsistent_total"], 1)
        self.assertFalse(any(item["card_id"] == card_id for item in listed["repair_orders"]))

        with self.assertRaises(ServiceError) as raised:
            self.service.get_repair_order({"card_id": card_id})

        self.assertEqual(raised.exception.code, "repair_order_archived_card_conflict")
        self.assertEqual(raised.exception.status_code, 409)

    def test_closing_repair_order_accrues_employee_salary(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Иван Мастер",
                "position": "Механик",
                "salary_mode": "salary_plus_percent",
                "base_salary": "50000",
                "work_percent": "30",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Mitsubishi L200",
                "title": "Начисление зарплаты",
                "description": "Проверка закрытия наряда",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        updated = self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "27",
                    "status": "open",
                    "client": "Витя Покровский",
                    "vehicle": "Mitsubishi L200",
                    "payments": [
                        {"amount": "5000", "paid_at": "05.04.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )
        self.assertEqual(updated["card"]["repair_order"]["works"][0]["executor_id"], employee["id"])

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        closed_row = closed["repair_order"]["works"][0]
        self.assertEqual(closed_row["executor_name"], "Иван Мастер")
        self.assertEqual(closed_row["salary_amount"], "1500")

        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        report = self.service.get_payroll_report({"month": closed_month})
        summary = next(item for item in report["summary"] if item["employee_id"] == employee["id"])
        self.assertEqual(summary["works_count"], 1)
        self.assertEqual(summary["accrued_total"], "1500")
        self.assertEqual(summary["base_salary"], "50000")
        self.assertEqual(summary["total_salary"], "51500")

    def test_reopening_repair_order_clears_employee_salary_snapshot(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Иван Мастер",
                "position": "Механик",
                "salary_mode": "salary_plus_percent",
                "base_salary": "50000",
                "work_percent": "30",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Mitsubishi L200",
                "title": "Снятие начисления",
                "description": "Проверка повторного открытия",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "28",
                    "status": "open",
                    "client": "Витя Покровский",
                    "vehicle": "Mitsubishi L200",
                    "payments": [
                        {"amount": "5000", "paid_at": "05.04.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        self.assertEqual(closed["repair_order"]["works"][0]["salary_amount"], "1500")

        reopened = self.service.set_repair_order_status({"card_id": card_id, "status": "open"})
        reopened_row = reopened["repair_order"]["works"][0]
        self.assertEqual(reopened_row["salary_mode_snapshot"], "")
        self.assertEqual(reopened_row["base_salary_snapshot"], "")
        self.assertEqual(reopened_row["work_percent_snapshot"], "")

    def test_employee_salary_ledger_combines_closed_orders_payouts_and_advances(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Антон Слесарь",
                "position": "Слесарь",
                "salary_mode": "salary_plus_percent",
                "base_salary": "30000",
                "work_percent": "20",
            }
        )["employee"]
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        open_card = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "Открытый наряд",
                "deadline": {"hours": 2},
            }
        )["card"]
        closed_card = self.service.create_card(
            {
                "vehicle": "BMW X5",
                "title": "Закрытый наряд",
                "deadline": {"hours": 2},
            }
        )["card"]

        self.service.update_card(
            {
                "card_id": open_card["id"],
                "repair_order": {
                    "number": "101",
                    "status": "open",
                    "vehicle": "KIA RIO",
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )
        self.service.update_card(
            {
                "card_id": closed_card["id"],
                "repair_order": {
                    "number": "102",
                    "status": "open",
                    "vehicle": "BMW X5",
                    "payments": [
                        {
                            "amount": "7000",
                            "paid_at": "16.04.2026 12:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Замена масла",
                            "quantity": "1",
                            "price": "7000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": closed_card["id"], "status": "closed"})

        payout = self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount": "6000",
                "actor_name": "ADMIN",
            }
        )["transaction"]
        advance = self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_advance",
                "amount": "2000",
                "actor_name": "ADMIN",
            }
        )["transaction"]

        bundle = self.service._store.read_bundle()
        old_transaction = next(
            item for item in bundle["cash_transactions"] if item.id == payout["id"]
        )
        old_transaction.created_at = (utc_now() - timedelta(days=220)).isoformat()
        self.service._store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(ledger["employee_id"], employee["id"])
        self.assertEqual(ledger["balance_total"], "-6600")
        self.assertEqual(ledger["accrued_total"], "1400")
        self.assertEqual(ledger["payout_total"], "6000")
        self.assertEqual(ledger["advance_total"], "2000")
        self.assertTrue(
            any(
                row["kind"] == "accrual" and row["card_id"] == closed_card["id"]
                for row in ledger["journal_rows"]
            )
        )
        self.assertFalse(
            any(
                row["kind"] == "accrual" and row["card_id"] == open_card["id"]
                for row in ledger["journal_rows"]
            )
        )
        self.assertFalse(
            any(
                row["kind"] == "salary_payout"
                and row["repair_order_number"] == ""
                and row["created_at"] == old_transaction.created_at
                for row in ledger["journal_rows"]
            )
        )

        reopened = self.service.set_repair_order_status(
            {"card_id": closed_card["id"], "status": "open"}
        )
        self.assertEqual(reopened["repair_order"]["works"][0]["salary_amount"], "")
        reopened_row = reopened["repair_order"]["works"][0]
        ledger_after_reopen = self.service.get_employee_salary_ledger(
            {"employee_id": employee["id"]}
        )
        self.assertEqual(ledger_after_reopen["balance_total"], "-8000")
        self.assertFalse(
            any(
                row["kind"] == "accrual" and row["card_id"] == closed_card["id"]
                for row in ledger_after_reopen["journal_rows"]
            )
        )
        self.assertEqual(reopened_row["salary_amount"], "")
        self.assertEqual(reopened_row["salary_accrued_at"], "")

    def test_employee_salary_report_excludes_entries_older_than_two_months(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Марина Бухгалтер",
                "position": "Бухгалтер",
                "salary_mode": "salary_plus_percent",
                "base_salary": "25000",
                "work_percent": "15",
            }
        )["employee"]
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]

        old_time = utc_now() - timedelta(days=220)
        recent_time = utc_now()

        old_card = self.service.create_card(
            {
                "vehicle": "Skoda Octavia",
                "title": "Старое начисление",
                "deadline": {"hours": 2},
            }
        )["card"]
        with (
            patch("minimal_kanban.services.card_service.utc_now", return_value=old_time),
            patch(
                "minimal_kanban.services.card_service.utc_now_iso",
                return_value=old_time.isoformat(),
            ),
            patch("minimal_kanban.models.utc_now", return_value=old_time),
        ):
            self.service.update_card(
                {
                    "card_id": old_card["id"],
                    "repair_order": {
                        "number": "301",
                        "status": "open",
                        "vehicle": "Skoda Octavia",
                        "payments": [
                            {
                                "amount": "10000",
                                "paid_at": "01.09.2025 10:00",
                                "payment_method": "cash",
                            }
                        ],
                        "works": [
                            {
                                "name": "Старый заказ",
                                "quantity": "1",
                                "price": "10000",
                                "executor_id": employee["id"],
                            }
                        ],
                    },
                }
            )
            self.service.set_repair_order_status({"card_id": old_card["id"], "status": "closed"})
            self.service.create_employee_salary_transaction(
                {
                    "employee_id": employee["id"],
                    "transaction_kind": "salary_payout",
                    "amount": "700",
                    "actor_name": "ADMIN",
                    "cashbox_id": cashbox["id"],
                    "note": "СТАРАЯ ВЫПЛАТА",
                }
            )
        bundle = self.service._store.read_bundle()
        old_closed_at = old_time.astimezone().strftime("%d.%m.%Y %H:%M")
        for card in bundle["cards"]:
            if card.id == old_card["id"]:
                card.repair_order.closed_at = old_closed_at
                break
        self.service._store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        recent_card = self.service.create_card(
            {
                "vehicle": "Honda Civic",
                "title": "Свежий наряд",
                "deadline": {"hours": 2},
            }
        )["card"]
        with (
            patch("minimal_kanban.services.card_service.utc_now", return_value=recent_time),
            patch(
                "minimal_kanban.services.card_service.utc_now_iso",
                return_value=recent_time.isoformat(),
            ),
            patch("minimal_kanban.models.utc_now", return_value=recent_time),
        ):
            self.service.update_card(
                {
                    "card_id": recent_card["id"],
                    "repair_order": {
                        "number": "302",
                        "status": "open",
                        "vehicle": "Honda Civic",
                        "payments": [
                            {
                                "amount": "12000",
                                "paid_at": "15.04.2026 10:00",
                                "payment_method": "cash",
                            }
                        ],
                        "works": [
                            {
                                "name": "Свежая работа",
                                "quantity": "1",
                                "price": "12000",
                                "executor_id": employee["id"],
                            }
                        ],
                    },
                }
            )
            self.service.set_repair_order_status({"card_id": recent_card["id"], "status": "closed"})
            self.service.create_employee_salary_transaction(
                {
                    "employee_id": employee["id"],
                    "transaction_kind": "salary_advance",
                    "amount": "500",
                    "actor_name": "ADMIN",
                    "cashbox_id": cashbox["id"],
                    "note": "СВЕЖИЙ АВАНС",
                }
            )

        report = self.service.get_employee_salary_report({"employee_id": employee["id"]})
        self.assertEqual(report["employee_id"], employee["id"])
        self.assertEqual(report["meta"]["months"], 2)
        self.assertEqual(report["meta"]["accrued_total"], "1800")
        self.assertEqual(report["meta"]["payout_total"], "0")
        self.assertEqual(report["meta"]["advance_total"], "500")
        self.assertEqual(report["meta"]["balance_total"], "1300")
        self.assertIn("ОТЧЕТ ПО ЗАРПЛАТЕ", report["text"])
        self.assertIn("Свежая работа", report["text"])
        self.assertIn("СВЕЖИЙ АВАНС", report["text"])
        self.assertNotIn("Старый заказ", report["text"])
        self.assertNotIn("СТАРАЯ ВЫПЛАТА", report["text"])

    def test_financial_history_cleanup_clears_balances_and_preserves_new_flows(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Иван Мастер",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "100",
            }
        )["employee"]
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {
                "vehicle": "Mitsubishi L200",
                "title": "Историческая оплата",
                "description": "Проверка очистки истории",
                "deadline": {"hours": 2},
            }
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "31",
                    "status": "open",
                    "client": "Клиент",
                    "vehicle": "Mitsubishi L200",
                    "payments": [
                        {"amount": "5000", "paid_at": "05.04.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )
        closed = self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
        self.assertEqual(closed["repair_order"]["works"][0]["salary_amount"], "5000")
        self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount": "5000",
                "cashbox_id": cashbox["id"],
                "actor_name": "ADMIN",
            }
        )
        self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "2500",
                "note": "Временное движение",
                "actor_name": "ADMIN",
            }
        )

        raw_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        sanitized = sanitize_financial_history_state(raw_state)
        self.state_file.write_text(
            json.dumps(sanitized, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        fresh_service = self._build_service()
        ledger = fresh_service.get_employee_salary_ledger({"employee_id": employee["id"]})
        cashbox_details = fresh_service.get_cashbox(
            {"cashbox_id": cashbox["id"], "transaction_limit": 10}
        )

        self.assertEqual(ledger["balance_total"], "0")
        self.assertEqual(ledger["journal_rows"], [])
        self.assertEqual(cashbox_details["cashbox"]["statistics"]["balance_minor"], 0)
        self.assertEqual(cashbox_details["cashbox"]["statistics"]["transactions_total"], 0)

        new_cash = fresh_service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "1000",
                "note": "Новая операция",
                "actor_name": "ADMIN",
            }
        )
        new_salary = fresh_service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount": "1000",
                "cashbox_id": cashbox["id"],
                "actor_name": "ADMIN",
            }
        )

        self.assertEqual(new_cash["transaction"]["amount_minor"], 100000)
        self.assertEqual(new_salary["transaction"]["amount_minor"], 100000)

    def test_employee_create_multiple_and_delete_keeps_distinct_records(self) -> None:
        first = self.service.save_employee({"name": "Иван", "position": "Мастер"})["employee"]
        second = self.service.save_employee({"name": "Пётр", "position": "Приёмщик"})["employee"]
        third = self.service.save_employee({"name": "Сергей", "position": "Диагност"})["employee"]

        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(second["id"], third["id"])

        listed = self.service.list_employees()
        self.assertEqual(len(listed["employees"]), 3)
        self.assertEqual(
            {item["id"] for item in listed["employees"]}, {first["id"], second["id"], third["id"]}
        )

        deleted = self.service.delete_employee({"employee_id": second["id"], "actor_name": "ADMIN"})
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["employee_id"], second["id"])
        self.assertEqual({item["id"] for item in deleted["employees"]}, {first["id"], third["id"]})

        listed_after = self.service.list_employees()
        self.assertEqual(len(listed_after["employees"]), 2)
        self.assertFalse(any(item["id"] == second["id"] for item in listed_after["employees"]))

    def test_employee_list_shows_current_balance_after_salary_payout(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Алексей Чупров",
                "position": "Снабженец",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "100",
            }
        )["employee"]
        card = self.service.create_card(
            {
                "vehicle": "Mazda Axela 2015",
                "title": "Закрыть начисление",
                "description": "Проверка выплаты зарплаты",
                "deadline": {"hours": 2},
            }
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "64",
                    "status": "open",
                    "client": "Тестовый клиент",
                    "vehicle": "Mazda Axela 2015",
                    "payments": [
                        {"amount": "2000", "paid_at": "18.04.2026 04:58", "payment_method": "cash"}
                    ],
                    "works": [
                        {
                            "name": "Доставка",
                            "quantity": "1",
                            "price": "2000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
        self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})
        self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount": "2000",
                "actor_name": "ADMIN",
            }
        )

        listed = self.service.list_employees()
        listed_employee = next(item for item in listed["employees"] if item["id"] == employee["id"])
        self.assertEqual(listed_employee["balance_total"], "0")

    def test_employee_supports_up_to_fifteen_records_without_overwrite(self) -> None:
        checkpoints = {1, 2, 3, 10, 15}
        created_ids: list[str] = []
        expected_modes: dict[str, tuple[str, str, str]] = {}
        modes = ("salary_only", "percent_only", "salary_plus_percent")

        for index in range(15):
            result = self.service.save_employee(
                {
                    "name": f"Сотрудник {index + 1}",
                    "position": f"Пост {index + 1}",
                    "salary_mode": modes[index % len(modes)],
                    "base_salary": str((index + 1) * 1000),
                    "work_percent": str(index + 5),
                }
            )["employee"]
            created_ids.append(result["id"])
            expected_modes[result["id"]] = (
                result["salary_mode"],
                result["base_salary"],
                result["work_percent"],
            )
            if (index + 1) in checkpoints:
                listed = self.service.list_employees()["employees"]
                self.assertEqual(len(listed), index + 1)
                self.assertEqual(len({item["id"] for item in listed}), index + 1)

        listed = self.service.list_employees()["employees"]
        self.assertEqual(len(listed), 15)
        self.assertEqual(set(item["id"] for item in listed), set(created_ids))
        for item in listed:
            salary_mode, base_salary, work_percent = expected_modes[item["id"]]
            self.assertEqual(item["salary_mode"], salary_mode)
            self.assertEqual(item["base_salary"], base_salary)
            self.assertEqual(item["work_percent"], work_percent)

    def test_employee_create_mode_ignores_stale_employee_id_and_creates_new_record(self) -> None:
        first = self.service.save_employee({"name": "Иван", "position": "Мастер"})["employee"]
        second = self.service.save_employee(
            {
                "employee_id": first["id"],
                "create_mode": True,
                "name": "Пётр",
                "position": "Приёмщик",
            }
        )["employee"]

        listed = self.service.list_employees()["employees"]
        self.assertEqual(len(listed), 2)
        self.assertNotEqual(first["id"], second["id"])
        self.assertCountEqual([item["name"] for item in listed], ["Иван", "Пётр"])

    def test_employee_toggle_updates_active_state(self) -> None:
        employee = self.service.save_employee({"name": "Иван", "position": "Мастер"})["employee"]

        toggled_off = self.service.toggle_employee(
            {"employee_id": employee["id"], "actor_name": "ADMIN"}
        )
        self.assertFalse(toggled_off["employee"]["is_active"])
        self.assertTrue(
            any(
                item["id"] == employee["id"] and not item["is_active"]
                for item in toggled_off["employees"]
            )
        )

        toggled_on = self.service.toggle_employee(
            {"employee_id": employee["id"], "actor_name": "ADMIN"}
        )
        self.assertTrue(toggled_on["employee"]["is_active"])
        self.assertTrue(
            any(
                item["id"] == employee["id"] and item["is_active"]
                for item in toggled_on["employees"]
            )
        )

    def test_employee_creation_rejects_more_than_fifteen_records(self) -> None:
        for index in range(15):
            self.service.save_employee({"name": f"Сотрудник {index + 1}"})
        with self.assertRaises(ServiceError) as ctx:
            self.service.save_employee({"name": "Сотрудник 16"})
        self.assertEqual(ctx.exception.code, "validation_error")
        self.assertIn("15", str(ctx.exception))

    def test_supports_large_card_description(self) -> None:
        large_description = "А" * 12000

        created = self.service.create_card(
            {
                "title": "Длинное описание",
                "description": large_description,
                "deadline": {"days": 0, "hours": 2},
            }
        )

        self.assertEqual(created["card"]["description"], large_description)
        self.assertGreater(len(created["card"]["description"]), 5000)

    def test_cashbox_lifecycle_tracks_balance_and_transactions(self) -> None:
        created = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})
        cashbox = created["cashbox"]
        self.assertEqual(cashbox["name"], "Наличный")
        self.assertEqual(cashbox["statistics"]["transactions_total"], 0)

        income = self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "1500,50",
                "note": "Предоплата",
                "actor_name": "ADMIN",
            }
        )
        self.assertEqual(income["transaction"]["direction"], "income")
        self.assertEqual(income["transaction"]["amount_minor"], 150050)

        expense = self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["short_id"],
                "direction": "expense",
                "amount_minor": 5050,
                "note": "Расходник",
                "actor_name": "ADMIN",
            }
        )
        self.assertEqual(expense["transaction"]["direction"], "expense")

        listed = self.service.list_cashboxes()
        self.assertEqual(listed["meta"]["total"], 1)
        listed_cashbox = listed["cashboxes"][0]
        self.assertEqual(listed_cashbox["statistics"]["transactions_total"], 2)
        self.assertEqual(listed_cashbox["statistics"]["balance_minor"], 145000)

        details = self.service.get_cashbox({"cashbox_id": cashbox["id"], "transaction_limit": 10})
        self.assertEqual(details["cashbox"]["id"], cashbox["id"])
        self.assertEqual(len(details["transactions"]), 2)
        self.assertEqual(details["transactions"][0]["note"], "Расходник")

        with self.assertRaisesRegex(ValueError, "Нельзя удалить кассу, пока в ней есть движения"):
            self.service.delete_cashbox({"cashbox_id": cashbox["short_id"], "actor_name": "ADMIN"})

        empty_cashbox = self.service.create_cashbox({"name": "Резерв", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        deleted = self.service.delete_cashbox(
            {"cashbox_id": empty_cashbox["short_id"], "actor_name": "ADMIN"}
        )
        self.assertTrue(deleted["meta"]["deleted"])
        self.assertEqual(deleted["meta"]["removed_transactions"], 0)
        self.assertEqual(self.service.list_cashboxes()["meta"]["total"], 1)

    def test_cashbox_reorder_persists_custom_order(self) -> None:
        first = self.service.create_cashbox({"name": "Касса A", "actor_name": "ADMIN"})["cashbox"]
        second = self.service.create_cashbox({"name": "Касса B", "actor_name": "ADMIN"})["cashbox"]
        third = self.service.create_cashbox({"name": "Касса C", "actor_name": "ADMIN"})["cashbox"]

        reordered = self.service.reorder_cashboxes(
            {
                "cashbox_id": third["id"],
                "before_cashbox_id": first["id"],
                "actor_name": "ADMIN",
            }
        )

        self.assertTrue(reordered["meta"]["changed"])
        self.assertEqual(
            [item["id"] for item in reordered["cashboxes"]],
            [third["id"], first["id"], second["id"]],
        )
        self.assertEqual([item["order"] for item in reordered["cashboxes"]], [0, 1, 2])

        listed = self.service.list_cashboxes()["cashboxes"]
        self.assertEqual([item["id"] for item in listed], [third["id"], first["id"], second["id"]])
        self.assertEqual([item["order"] for item in listed], [0, 1, 2])

    def test_cashbox_transfer_moves_money_between_cashboxes(self) -> None:
        source_cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        target_cashbox = self.service.create_cashbox(
            {"name": "Безналичный", "actor_name": "ADMIN"}
        )["cashbox"]

        self.service.create_cash_transaction(
            {
                "cashbox_id": source_cashbox["id"],
                "direction": "income",
                "amount": "1000",
                "note": "Стартовый остаток",
                "actor_name": "ADMIN",
            }
        )

        transferred = self.service.create_cashbox_transfer(
            {
                "from_cashbox_id": source_cashbox["short_id"],
                "to_cashbox_id": target_cashbox["short_id"],
                "amount": "250",
                "note": "На размен",
                "actor_name": "ADMIN",
            }
        )
        self.assertEqual(transferred["source_transaction"]["direction"], "expense")
        self.assertEqual(transferred["target_transaction"]["direction"], "income")
        self.assertIn("Перемещение в Безналичный", transferred["source_transaction"]["note"])
        self.assertIn("Перемещение из Наличный", transferred["target_transaction"]["note"])

        source_details = self.service.get_cashbox(
            {"cashbox_id": source_cashbox["id"], "transaction_limit": 10}
        )
        target_details = self.service.get_cashbox(
            {"cashbox_id": target_cashbox["id"], "transaction_limit": 10}
        )
        self.assertEqual(source_details["cashbox"]["statistics"]["balance_minor"], 75000)
        self.assertEqual(target_details["cashbox"]["statistics"]["balance_minor"], 25000)
        self.assertEqual(source_details["cashbox"]["statistics"]["transactions_total"], 2)
        self.assertEqual(target_details["cashbox"]["statistics"]["transactions_total"], 1)

    def test_cancel_last_cash_transaction_removes_latest_manual_movement(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        first = self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "1000",
                "note": "Старт",
                "actor_name": "ADMIN",
            }
        )["transaction"]
        last = self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "expense",
                "amount": "250",
                "note": "Расход",
                "actor_name": "ADMIN",
            }
        )["transaction"]

        cancelled = self.service.cancel_last_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "transaction_id": last["id"],
                "actor_name": "ADMIN",
            }
        )

        self.assertTrue(cancelled["meta"]["cancelled"])
        self.assertEqual(cancelled["cancelled_transaction"]["id"], last["id"])
        details = self.service.get_cashbox({"cashbox_id": cashbox["id"], "transaction_limit": 10})
        self.assertEqual(details["cashbox"]["statistics"]["transactions_total"], 1)
        self.assertEqual(details["cashbox"]["statistics"]["balance_minor"], 100000)
        self.assertEqual(details["transactions"][0]["id"], first["id"])

    def test_cancel_last_cash_transaction_removes_linked_repair_order_payment(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Безналичный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        created = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Оплата", "deadline": {"hours": 2}}
        )["card"]
        updated = self.service.update_card(
            {
                "card_id": created["id"],
                "repair_order": {
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "2000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:00",
                            "note": "Аванс",
                            "payment_method": "cashless",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )["card"]["repair_order"]
        payment = updated["payments"][0]

        cancelled = self.service.cancel_last_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "transaction_id": payment["cash_transaction_id"],
                "actor_name": "ADMIN",
            }
        )

        self.assertTrue(cancelled["meta"]["cancelled"])
        self.assertEqual(cancelled["meta"]["repair_order_card_id"], created["id"])
        card = self.service.get_card({"card_id": created["id"]})["card"]
        self.assertEqual(card["repair_order"]["payments"], [])
        self.assertEqual(card["repair_order"]["paid_total"], "0")
        cashbox_details = self.service.get_cashbox(
            {"cashbox_id": cashbox["id"], "transaction_limit": 10}
        )
        self.assertEqual(cashbox_details["cashbox"]["statistics"]["transactions_total"], 0)
        self.assertEqual(cashbox_details["transactions"], [])

    def test_cashbox_creation_is_capped_at_six_items(self) -> None:
        for index in range(6):
            created = self.service.create_cashbox(
                {"name": f"Касса {index + 1}", "actor_name": "ADMIN"}
            )
            self.assertEqual(created["cashbox"]["name"], f"Касса {index + 1}")

        with self.assertRaisesRegex(ValueError, "Нельзя создать больше 6 касс"):
            self.service.create_cashbox({"name": "Касса 7", "actor_name": "ADMIN"})

    def test_cash_journal_returns_recent_three_months_text(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "1000",
                "note": "Оплата клиента",
                "actor_name": "ADMIN",
            }
        )

        journal = self.service.get_cash_journal({"months": 3, "limit": 100})

        self.assertEqual(journal["meta"]["months"], 3)
        self.assertEqual(journal["meta"]["returned"], 1)
        self.assertIn("КАССОВЫЙ ЖУРНАЛ", journal["text"])
        self.assertIn("Наличный", journal["text"])
        self.assertIn("ОПЛАТА КЛИЕНТА", journal["text"].upper())
        self.assertIn("1 000 ₽", journal["text"])

    def test_move_card_can_reorder_within_same_column(self) -> None:
        first = self.service.create_card(
            {"title": "First", "column": "inbox", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"title": "Second", "column": "inbox", "deadline": {"hours": 2}}
        )
        third = self.service.create_card(
            {"title": "Third", "column": "inbox", "deadline": {"hours": 2}}
        )

        moved = self.service.move_card(
            {
                "card_id": third["card"]["id"],
                "column": "inbox",
                "before_card_id": second["card"]["id"],
            }
        )

        self.assertEqual(moved["card"]["column"], "inbox")
        self.assertEqual(moved["card"]["position"], 1)
        self.assertEqual(moved["affected_column_ids"], ["inbox"])
        self.assertEqual(
            [card["id"] for card in moved["affected_cards"][:3]],
            [first["card"]["id"], third["card"]["id"], second["card"]["id"]],
        )
        self.assertTrue(all("repair_order" not in card for card in moved["affected_cards"]))
        self.assertTrue(moved["meta"]["changed"])

        snapshot = self.service.get_board_snapshot()
        inbox_cards = sorted(
            [card for card in snapshot["cards"] if card["column"] == "inbox"],
            key=lambda item: item["position"],
        )
        self.assertEqual(
            [card["id"] for card in inbox_cards[:3]],
            [first["card"]["id"], third["card"]["id"], second["card"]["id"]],
        )

    def test_move_card_can_insert_before_card_in_another_column(self) -> None:
        source = self.service.create_card(
            {"title": "Source", "column": "inbox", "deadline": {"hours": 2}}
        )
        first_target = self.service.create_card(
            {"title": "Target A", "column": "in_progress", "deadline": {"hours": 2}}
        )
        second_target = self.service.create_card(
            {"title": "Target B", "column": "in_progress", "deadline": {"hours": 2}}
        )

        moved = self.service.move_card(
            {
                "card_id": source["card"]["id"],
                "column": "in_progress",
                "before_card_id": second_target["card"]["id"],
            }
        )

        self.assertEqual(moved["card"]["column"], "in_progress")
        self.assertEqual(moved["card"]["position"], 1)
        self.assertEqual(moved["affected_column_ids"], ["inbox", "in_progress"])
        self.assertEqual(
            [card["id"] for card in moved["affected_cards"]],
            [first_target["card"]["id"], source["card"]["id"], second_target["card"]["id"]],
        )

        snapshot = self.service.get_board_snapshot()
        target_cards = sorted(
            [card for card in snapshot["cards"] if card["column"] == "in_progress"],
            key=lambda item: item["position"],
        )
        self.assertEqual(
            [card["id"] for card in target_cards[:3]],
            [first_target["card"]["id"], source["card"]["id"], second_target["card"]["id"]],
        )

    def test_rejects_card_description_above_limit(self) -> None:
        too_large_description = "Б" * (CARD_DESCRIPTION_LIMIT + 1)

        with self.assertRaises(ServiceError) as description_error:
            self.service.create_card(
                {
                    "title": "Слишком длинное описание",
                    "description": too_large_description,
                    "deadline": {"days": 0, "hours": 2},
                }
            )

        self.assertEqual(description_error.exception.code, "validation_error")

    def test_create_card_repairs_autofill_profile_metadata(self) -> None:
        created = self.service.create_card(
            {
                "title": "Mazda CX-5",
                "description": "VIN JM3KF123456789012",
                "deadline": {"days": 1},
                "vehicle_profile": {
                    "make_display": "Mazda",
                    "model_display": "CX-5",
                    "vin": "JM3KF123456789012",
                    "autofilled_fields": ["make_display", "model_display", "vin"],
                    "field_sources": {
                        "make_display": "official_vin_decode_nhtsa",
                        "model_display": "official_vin_decode_nhtsa",
                        "vin": "official_vin_decode_nhtsa",
                    },
                    "source_links_or_refs": ["https://vpic.nhtsa.dot.gov/api/vehicles/example"],
                    "data_completion_state": "mostly_autofilled",
                },
            }
        )

        profile = created["card"]["vehicle_profile"]
        self.assertEqual(profile["source_summary"], "official VIN decode")
        self.assertGreater(profile["source_confidence"], 0.0)
        self.assertEqual(profile["model_display"], "CX-5")

    def test_autofill_vehicle_data_does_not_treat_model_digits_as_coolant_capacity(self) -> None:
        with patch.object(
            self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None
        ):
            autofilled = self.service.autofill_vehicle_data(
                {
                    "raw_text": "BMW 320I 2017\nТечь антифриза\nVIN X4X8A594905J20193",
                }
            )

        profile = autofilled["vehicle_profile"]
        self.assertEqual(profile["model_display"], "320I")
        self.assertIsNone(profile["coolant_capacity_l"])

    def test_mcp_created_card_is_unread_until_marked_seen(self) -> None:
        created = self.service.create_card(
            {
                "title": "Через GPT",
                "description": "Карточка из MCP",
                "deadline": {"hours": 1},
                "source": "mcp",
            }
        )
        card = created["card"]
        card_id = card["id"]
        self.assertTrue(card["is_unread"])
        self.assertEqual(card["events_count"], 1)
        updated_at = card["updated_at"]

        marked = self.service.mark_card_seen({"card_id": card_id})
        self.assertTrue(marked["meta"]["changed"])
        self.assertFalse(marked["card"]["is_unread"])
        self.assertEqual(marked["card"]["updated_at"], updated_at)

        marked_again = self.service.mark_card_seen({"card_id": card_id})
        self.assertFalse(marked_again["meta"]["changed"])
        self.assertFalse(marked_again["card"]["is_unread"])

    def test_seen_user_gets_updated_badge_after_other_user_edits_card(self) -> None:
        created = self.service.create_card(
            {
                "title": "Seen card",
                "description": "Initial",
                "deadline": {"hours": 2},
                "actor_name": "ALICE",
            }
        )
        card_id = created["card"]["id"]

        seen = self.service.mark_card_seen({"card_id": card_id, "actor_name": "ALICE"})
        self.assertFalse(seen["card"]["is_unread"])
        self.assertFalse(seen["card"]["has_unseen_update"])

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "description": "Updated by Bob",
                "actor_name": "BOB",
            }
        )
        self.assertFalse(updated["card"]["has_unseen_update"])

        alice_view = self.service.get_card({"card_id": card_id, "actor_name": "ALICE"})["card"]
        bob_view = self.service.get_card({"card_id": card_id, "actor_name": "BOB"})["card"]
        self.assertTrue(alice_view["has_unseen_update"])
        self.assertFalse(alice_view["is_unread"])
        self.assertFalse(bob_view["has_unseen_update"])

        marked = self.service.mark_card_seen({"card_id": card_id, "actor_name": "ALICE"})
        self.assertTrue(marked["meta"]["changed"])
        self.assertFalse(marked["card"]["has_unseen_update"])

    def test_deadline_status_transitions(self) -> None:
        base = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            created = self.service.create_card(
                {"title": "Срочная задача", "deadline": {"minutes": 1, "seconds": 40}}
            )
        card_id = created["card"]["id"]
        self.assertEqual(created["card"]["remaining_seconds"], 100)
        self.assertEqual(created["card"]["status"], "ok")

        warning_time = base + timedelta(seconds=40)
        with patch("minimal_kanban.models.utc_now", return_value=warning_time):
            warning = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(warning["remaining_seconds"], 60)
        self.assertEqual(warning["status"], "warning")
        self.assertEqual(warning["indicator"], "yellow")
        self.assertFalse(warning["is_blinking"])

        critical_time = base + timedelta(seconds=85)
        with patch("minimal_kanban.models.utc_now", return_value=critical_time):
            critical = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(critical["remaining_seconds"], 15)
        self.assertEqual(critical["status"], "critical")
        self.assertEqual(critical["indicator"], "red")
        self.assertFalse(critical["is_blinking"])

        blinking_time = base + timedelta(seconds=95)
        with patch("minimal_kanban.models.utc_now", return_value=blinking_time):
            blinking = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(blinking["remaining_seconds"], 5)
        self.assertEqual(blinking["indicator"], "red")
        self.assertTrue(blinking["is_blinking"])

        expired_time = base + timedelta(seconds=101)
        with patch("minimal_kanban.models.utc_now", return_value=expired_time):
            expired = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(expired["remaining_seconds"], 0)
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["indicator"], "red")
        self.assertTrue(expired["is_blinking"])

    def test_deadline_heat_progress_uses_five_percent_steps_and_resets_after_deadline_change(
        self,
    ) -> None:
        base = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            created = self.service.create_card(
                {"title": "Тепловая шкала", "deadline": {"minutes": 1, "seconds": 40}}
            )
        card_id = created["card"]["id"]

        self.assertEqual(created["card"]["deadline_progress_bucket"], 0)
        self.assertEqual(created["card"]["deadline_progress_step_percent"], 0)

        almost_first_step = base + timedelta(seconds=4)
        with patch("minimal_kanban.models.utc_now", return_value=almost_first_step):
            early = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(early["deadline_progress_bucket"], 0)
        self.assertEqual(early["deadline_heat_color"], created["card"]["deadline_heat_color"])

        first_step_time = base + timedelta(seconds=5)
        with patch("minimal_kanban.models.utc_now", return_value=first_step_time):
            first_step = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(first_step["deadline_progress_bucket"], 1)
        self.assertEqual(first_step["deadline_progress_step_percent"], 5)

        same_bucket_time = base + timedelta(seconds=9)
        with patch("minimal_kanban.models.utc_now", return_value=same_bucket_time):
            same_bucket = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(same_bucket["deadline_progress_bucket"], 1)
        self.assertEqual(same_bucket["deadline_heat_color"], first_step["deadline_heat_color"])

        later_time = base + timedelta(seconds=26)
        with patch("minimal_kanban.models.utc_now", return_value=later_time):
            later = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(later["deadline_progress_bucket"], 5)
        self.assertEqual(later["deadline_progress_step_percent"], 25)
        self.assertNotEqual(later["deadline_heat_color"], created["card"]["deadline_heat_color"])

        reset_patches = self._patch_time(later_time)
        with reset_patches[0], reset_patches[1], reset_patches[2]:
            reset = self.service.update_card(
                {"card_id": card_id, "deadline": {"minutes": 3, "seconds": 20}}
            )
        self.assertEqual(reset["card"]["deadline_progress_bucket"], 0)
        self.assertEqual(reset["card"]["deadline_progress_step_percent"], 0)
        self.assertEqual(
            reset["card"]["deadline_heat_color"], created["card"]["deadline_heat_color"]
        )

    def test_rejects_invalid_input(self) -> None:
        with self.assertRaises(ServiceError) as empty_title:
            self.service.create_card({"title": "   ", "deadline": {"days": 1, "hours": 0}})
        self.assertEqual(empty_title.exception.code, "validation_error")

        created = self.service.create_card(
            {"title": "Валидная карточка", "deadline": {"days": 1, "hours": 0}}
        )
        card_id = created["card"]["id"]

        with self.assertRaises(ServiceError) as invalid_bool:
            self.service.get_cards({"include_archived": "false"})
        self.assertEqual(invalid_bool.exception.code, "validation_error")

        with self.assertRaises(ServiceError) as update_without_fields:
            self.service.update_card({"card_id": card_id})
        self.assertEqual(update_without_fields.exception.code, "validation_error")

        with self.assertRaises(ServiceError) as invalid_column:
            self.service.move_card({"card_id": card_id, "column": "trash"})
        self.assertEqual(invalid_column.exception.code, "validation_error")

        with self.assertRaises(ServiceError) as invalid_deadline:
            self.service.create_card(
                {"title": "Сломанный срок", "deadline": {"days": 0, "hours": 0}}
            )
        self.assertEqual(invalid_deadline.exception.code, "validation_error")

        with self.assertRaises(ServiceError) as invalid_deadline_part:
            self.service.create_card(
                {"title": "Сломанный срок", "deadline": {"days": 0, "hours": 24}}
            )
        self.assertEqual(invalid_deadline_part.exception.code, "validation_error")

        self.service.create_column({"label": "Новый этап"})
        with self.assertRaises(ServiceError) as duplicate_column:
            self.service.create_column({"label": "новый этап"})
        self.assertEqual(duplicate_column.exception.code, "validation_error")

    def test_create_column_accepts_name_alias(self) -> None:
        created = self.service.create_column({"name": "Этап по имени"})

        self.assertEqual(created["column"]["label"], "Этап по имени")
        self.assertEqual(created["column"]["position"], 4)

    def test_archived_card_cannot_be_modified(self) -> None:
        created = self.service.create_card({"title": "Архив", "deadline": {"days": 1, "hours": 0}})
        card_id = created["card"]["id"]
        self.service.archive_card({"card_id": card_id})

        with self.assertRaises(ServiceError) as archived_error:
            self.service.update_card({"card_id": card_id, "title": "Нельзя"})
        self.assertEqual(archived_error.exception.code, "archived_card")

    def test_deadline_survives_service_reload(self) -> None:
        base = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            created = self.service.create_card(
                {"title": "Срок после перезапуска", "deadline": {"seconds": 10}}
            )
        card_id = created["card"]["id"]

        reloaded_store = JsonStore(state_file=self.state_file, logger=self.logger)
        reloaded_service = CardService(reloaded_store, self.logger)

        later = base + timedelta(seconds=3)
        with patch("minimal_kanban.models.utc_now", return_value=later):
            reloaded_card = reloaded_service.get_card({"card_id": card_id})["card"]
        self.assertEqual(reloaded_card["remaining_seconds"], 7)
        self.assertEqual(reloaded_card["status"], "ok")

        much_later = base + timedelta(seconds=11)
        with patch("minimal_kanban.models.utc_now", return_value=much_later):
            expired_card = reloaded_service.get_card({"card_id": card_id})["card"]
        self.assertEqual(expired_card["remaining_seconds"], 0)
        self.assertEqual(expired_card["status"], "expired")

    def test_custom_column_survives_reload(self) -> None:
        created_column = self.service.create_column({"label": "Блокеры"})
        column_id = created_column["column"]["id"]
        self.assertEqual(created_column["column"]["label"], "Блокеры")

        created_card = self.service.create_card(
            {
                "title": "Проверка столбца",
                "deadline": {"days": 0, "hours": 6},
                "column": column_id,
            }
        )
        card_id = created_card["card"]["id"]
        self.assertEqual(created_card["card"]["column"], column_id)

        reloaded_store = JsonStore(state_file=self.state_file, logger=self.logger)
        reloaded_service = CardService(reloaded_store, self.logger)

        columns = reloaded_service.list_columns()["columns"]
        self.assertTrue(
            any(column["id"] == column_id and column["label"] == "Блокеры" for column in columns)
        )

        card = reloaded_service.get_card({"card_id": card_id})["card"]
        self.assertEqual(card["column"], column_id)
        self.assertIn("deadline_timestamp", card)

    def test_delete_empty_column_removes_it_and_reorders_positions(self) -> None:
        created = self.service.create_column({"label": "TEMP DELETE"})
        column_id = created["column"]["id"]

        deleted = self.service.delete_column({"column_id": column_id})

        self.assertEqual(deleted["deleted_column"]["id"], column_id)
        remaining_ids = [column["id"] for column in deleted["columns"]]
        self.assertNotIn(column_id, remaining_ids)
        self.assertEqual(
            [column["position"] for column in deleted["columns"]],
            list(range(len(deleted["columns"]))),
        )

    def test_rename_column_updates_label_but_keeps_id(self) -> None:
        created = self.service.create_column({"label": "TEMP RENAME"})
        column_id = created["column"]["id"]

        renamed = self.service.rename_column({"column_id": column_id, "label": "READY FOR WORK"})

        self.assertEqual(renamed["column"]["id"], column_id)
        self.assertEqual(renamed["column"]["label"], "READY FOR WORK")
        self.assertTrue(renamed["meta"]["changed"])
        self.assertEqual(renamed["meta"]["previous_label"], "TEMP RENAME")
        listed = self.service.list_columns()["columns"]
        self.assertTrue(
            any(
                column["id"] == column_id and column["label"] == "READY FOR WORK"
                for column in listed
            )
        )

    def test_rename_column_rejects_duplicate_label(self) -> None:
        self.service.create_column({"label": "FIRST CUSTOM"})
        created = self.service.create_column({"label": "SECOND CUSTOM"})

        with self.assertRaises(ServiceError) as duplicate_label:
            self.service.rename_column(
                {"column_id": created["column"]["id"], "label": "FIRST CUSTOM"}
            )
        self.assertEqual(duplicate_label.exception.code, "validation_error")

    def test_rename_column_allows_noop_for_same_label(self) -> None:
        created = self.service.create_column({"label": "UNCHANGED"})

        renamed = self.service.rename_column(
            {"column_id": created["column"]["id"], "label": "UNCHANGED"}
        )

        self.assertFalse(renamed["meta"]["changed"])
        self.assertEqual(renamed["column"]["label"], "UNCHANGED")

    def test_move_column_reorders_positions_left_to_right(self) -> None:
        first = self.service.create_column({"label": "FIRST"})["column"]
        second = self.service.create_column({"label": "SECOND"})["column"]
        third = self.service.create_column({"label": "THIRD"})["column"]

        moved = self.service.move_column(
            {"column_id": third["id"], "before_column_id": first["id"]}
        )
        self.assertEqual(
            [column["id"] for column in moved["columns"]][-3:],
            [third["id"], first["id"], second["id"]],
        )
        self.assertTrue(moved["meta"]["changed"])

        moved_again = self.service.move_column({"column_id": third["id"]})
        self.assertEqual(
            [column["id"] for column in moved_again["columns"]][-3:],
            [first["id"], second["id"], third["id"]],
        )
        self.assertEqual(
            [column["position"] for column in moved_again["columns"]],
            list(range(len(moved_again["columns"]))),
        )

    def test_delete_column_rejects_non_empty(self) -> None:
        created_column = self.service.create_column({"label": "BLOCKED DELETE"})
        column_id = created_column["column"]["id"]
        self.service.create_card(
            {
                "title": "BOUND CARD",
                "deadline": {"hours": 2},
                "column": column_id,
            }
        )

        with self.assertRaises(ServiceError) as non_empty_error:
            self.service.delete_column({"column_id": column_id})
        self.assertEqual(non_empty_error.exception.code, "column_not_empty")

    def test_delete_native_column_allows_only_archived_cards(self) -> None:
        created_card = self.service.create_card(
            {
                "title": "ARCHIVED IN DONE",
                "deadline": {"hours": 2},
                "column": "done",
            }
        )
        card_id = created_card["card"]["id"]
        self.service.archive_card({"card_id": card_id})

        deleted = self.service.delete_column({"column_id": "done"})

        self.assertEqual(deleted["deleted_column"]["id"], "done")
        self.assertTrue(all(column["id"] != "done" for column in deleted["columns"]))
        archived_card = self.service.get_card({"card_id": card_id})["card"]
        self.assertTrue(archived_card["archived"])
        self.assertEqual(archived_card["column"], "inbox")
        self.assertEqual(archived_card["column_label"], "Входящие")

    def test_board_snapshot_returns_last_30_archived_cards_by_default(self) -> None:
        archived_ids: list[str] = []
        for index in range(35):
            created = self.service.create_card(
                {
                    "title": f"ARCHIVE {index}",
                    "description": f"Archived card {index}",
                    "deadline": {"hours": 1},
                }
            )
            card_id = created["card"]["id"]
            self.service.archive_card({"card_id": card_id})
            archived_ids.append(card_id)

        snapshot = self.service.get_board_snapshot()

        self.assertEqual(snapshot["meta"]["archive_limit"], 30)
        self.assertEqual(len(snapshot["archive"]), 30)
        self.assertEqual(
            [card["id"] for card in snapshot["archive"][:3]],
            archived_ids[-1:-4:-1],
        )

    def test_board_snapshot_compact_mode_skips_heavy_card_payload_fields(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "LEXUS IS F",
                "title": "Compact snapshot",
                "description": "Board card preview",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {"client": "Ivan", "phone": "+79001234567"},
            }
        )

        snapshot = self.service.get_board_snapshot({"compact": True})

        self.assertTrue(snapshot["meta"]["compact_cards"])
        compact_card = next(card for card in snapshot["cards"] if card["id"] == card_id)
        self.assertIn("tag_items", compact_card)
        self.assertIn("attachment_count", compact_card)
        self.assertIn("description_preview", compact_card)
        self.assertLessEqual(len(compact_card["description"]), 481)
        self.assertNotIn("repair_order", compact_card)
        self.assertNotIn("vehicle_profile", compact_card)
        self.assertNotIn("attachments", compact_card)

    def test_board_snapshot_can_skip_archive_payload_but_keep_archive_total(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "MITSUBISHI L200",
                "title": "Archive optimization",
                "description": "Archive payload should be lazy-loaded separately.",
                "deadline": {"hours": 2},
            }
        )
        self.service.archive_card({"card_id": created["card"]["id"]})

        snapshot = self.service.get_board_snapshot({"compact": True, "include_archive": False})

        self.assertTrue(snapshot["meta"]["compact_cards"])
        self.assertFalse(snapshot["meta"]["include_archive"])
        self.assertEqual(snapshot["archive"], [])
        self.assertEqual(snapshot["meta"]["archived_cards_total"], 1)

    def test_board_snapshot_revision_stays_stable_until_board_changes(self) -> None:
        first_snapshot = self.service.get_board_snapshot({"compact": True})
        second_snapshot = self.service.get_board_snapshot({"compact": True})

        self.assertEqual(first_snapshot["meta"]["revision"], second_snapshot["meta"]["revision"])

        self.service.create_card(
            {
                "vehicle": "Lexus IS F",
                "title": "Revision test",
                "deadline": {"hours": 2},
            }
        )
        changed_snapshot = self.service.get_board_snapshot({"compact": True})

        self.assertNotEqual(
            first_snapshot["meta"]["revision"], changed_snapshot["meta"]["revision"]
        )

    def test_board_snapshot_skips_expensive_prep_when_there_are_no_cards(self) -> None:
        snapshot_service = self.service._snapshot_service
        snapshot_service._column_labels = Mock(wraps=snapshot_service._column_labels)
        snapshot_service._event_counts = Mock(wraps=snapshot_service._event_counts)

        snapshot = self.service.get_board_snapshot()

        self.assertEqual(snapshot["cards"], [])
        self.assertEqual(snapshot["archive"], [])
        self.assertEqual(snapshot_service._column_labels.call_count, 0)
        self.assertEqual(snapshot_service._event_counts.call_count, 0)

    def test_list_archived_cards_skips_expensive_prep_when_archive_is_empty(self) -> None:
        snapshot_service = self.service._snapshot_service
        snapshot_service._column_labels = Mock(wraps=snapshot_service._column_labels)
        snapshot_service._event_counts = Mock(wraps=snapshot_service._event_counts)

        archived = self.service.list_archived_cards()

        self.assertEqual(archived["cards"], [])
        self.assertEqual(archived["meta"]["total"], 0)
        self.assertEqual(archived["meta"]["returned"], 0)
        self.assertFalse(archived["meta"]["has_more"])
        self.assertEqual(snapshot_service._column_labels.call_count, 0)
        self.assertEqual(snapshot_service._event_counts.call_count, 0)

    def test_store_keeps_only_latest_archived_cards_within_retention_limit(self) -> None:
        with patch("minimal_kanban.storage.json_store.ARCHIVED_CARD_RETENTION_LIMIT", 2):
            archived_ids: list[str] = []
            for index in range(3):
                created = self.service.create_card(
                    {
                        "title": f"RETENTION ARCHIVE {index}",
                        "deadline": {"hours": 1},
                    }
                )
                card_id = created["card"]["id"]
                self.service.archive_card({"card_id": card_id})
                archived_ids.append(card_id)

        archived_cards = [card for card in self.store.read_bundle()["cards"] if card.archived]

        self.assertEqual(len(archived_cards), 2)
        self.assertEqual({card.id for card in archived_cards}, set(archived_ids[-2:]))

    def test_store_prunes_old_audit_events_outside_retention_window(self) -> None:
        bundle = self.store.read_bundle()
        now = utc_now()
        events = [
            AuditEvent(
                id="old-event",
                timestamp=(now - timedelta(days=61)).isoformat(),
                actor_name="ADMIN",
                source="ui",
                action="card_archived",
                message="Old archived event",
                card_id="old-card",
                details={},
            ),
            AuditEvent(
                id="recent-event",
                timestamp=(now - timedelta(days=2)).isoformat(),
                actor_name="ADMIN",
                source="ui",
                action="card_moved",
                message="Recent move event",
                card_id="recent-card",
                details={},
            ),
        ]

        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=events,
            settings=bundle["settings"],
        )

        stored_events = self.store.read_bundle()["events"]

        self.assertEqual(len(stored_events), 1)
        self.assertEqual(stored_events[0].id, "recent-event")

    def test_delete_column_rejects_last_remaining_column(self) -> None:
        for doomed_id in ["done", "control", "in_progress"]:
            deleted = self.service.delete_column({"column_id": doomed_id})
            self.assertTrue(all(column["id"] != doomed_id for column in deleted["columns"]))

        with self.assertRaises(ServiceError) as last_column_error:
            self.service.delete_column({"column_id": "inbox"})
        self.assertEqual(last_column_error.exception.code, "last_column")

    def test_set_card_deadline_indicator_and_list_overdue(self) -> None:
        base = datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            created = self.service.create_card(
                {"title": "Удалённая задача", "deadline": {"total_seconds": 3 * 3600}}
            )
        card_id = created["card"]["id"]

        later = base + timedelta(minutes=5)
        patches = self._patch_time(later)
        with patches[0], patches[1], patches[2]:
            deadline_updated = self.service.set_card_deadline(
                {"card_id": card_id, "deadline": {"total_seconds": 60}}
            )
        self.assertLessEqual(deadline_updated["card"]["remaining_seconds"], 60)

        indicator_time = later + timedelta(seconds=5)
        patches = self._patch_time(indicator_time)
        with patches[0], patches[1], patches[2]:
            yellow = self.service.set_card_indicator({"card_id": card_id, "indicator": "yellow"})
        self.assertEqual(yellow["card"]["indicator"], "yellow")
        self.assertEqual(yellow["card"]["status"], "warning")

        expired_time = indicator_time + timedelta(seconds=1)
        patches = self._patch_time(expired_time)
        with patches[0], patches[1], patches[2]:
            red = self.service.set_card_indicator({"card_id": card_id, "indicator": "red"})
            overdue = self.service.list_overdue_cards()
        self.assertEqual(red["card"]["indicator"], "red")
        self.assertEqual(red["card"]["status"], "expired")
        self.assertTrue(any(card["id"] == card_id for card in overdue["cards"]))

    def test_list_overdue_cards_skips_expensive_prep_when_empty(self) -> None:
        snapshot_service = self.service._snapshot_service
        snapshot_service._column_labels = Mock(wraps=snapshot_service._column_labels)
        snapshot_service._event_counts = Mock(wraps=snapshot_service._event_counts)

        overdue = self.service.list_overdue_cards()

        self.assertEqual(overdue["cards"], [])
        self.assertEqual(snapshot_service._column_labels.call_count, 0)
        self.assertEqual(snapshot_service._event_counts.call_count, 0)

    def test_review_board_returns_operational_summary(self) -> None:
        base = datetime(2026, 4, 5, 10, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            overdue_card = self.service.create_card(
                {
                    "vehicle": "Toyota Camry",
                    "title": "Шум АКПП",
                    "description": "Проверить гидроблок",
                    "deadline": {"hours": 1},
                }
            )
            self.service.create_card(
                {
                    "vehicle": "Kia Rio",
                    "title": "Стук подвески",
                    "description": "Осмотр передней оси",
                    "deadline": {"days": 3},
                }
            )
            self.service.create_card(
                {
                    "vehicle": "Mazda CX-5",
                    "title": "Диагностика ABS",
                    "description": "Горит ABS",
                    "deadline": {"days": 3},
                }
            )
            archived_card = self.service.create_card(
                {
                    "vehicle": "Nissan X-Trail",
                    "title": "Архивный заказ",
                    "description": "Закрытая работа",
                    "deadline": {"hours": 6},
                }
            )
            self.service.archive_card({"card_id": archived_card["card"]["id"]})

        review_moment = base + timedelta(hours=50)
        with (
            patch("minimal_kanban.services.snapshot_service.utc_now", return_value=review_moment),
            patch(
                "minimal_kanban.services.snapshot_service.utc_now_iso",
                return_value=review_moment.isoformat(),
            ),
        ):
            review = self.service.review_board(
                {
                    "stale_hours": 24,
                    "overload_threshold": 2,
                    "priority_limit": 5,
                    "recent_event_limit": 5,
                }
            )

        self.assertEqual(review["summary"]["active_cards"], 3)
        self.assertEqual(review["summary"]["archived_cards"], 1)
        self.assertEqual(review["summary"]["overdue_cards"], 1)
        self.assertGreaterEqual(review["summary"]["critical_cards"], 1)
        self.assertEqual(review["summary"]["stale_cards"], 3)
        self.assertTrue(
            any(item["column_id"] == "inbox" and item["count"] == 3 for item in review["by_column"])
        )
        self.assertTrue(any("перегружена" in item for item in review["alerts"]))
        self.assertEqual(review["priority_cards"][0]["card_id"], overdue_card["card"]["id"])
        self.assertIn("Просрочена", review["priority_cards"][0]["short_reason"])
        self.assertTrue(any(item["type"] == "card_archived" for item in review["recent_events"]))
        self.assertIn("[BOARD REVIEW]", review["text"])

    def test_rejects_invalid_indicator(self) -> None:
        created = self.service.create_card({"title": "Индикатор", "deadline": {"hours": 1}})
        card_id = created["card"]["id"]
        with self.assertRaises(ServiceError) as invalid_indicator:
            self.service.set_card_indicator({"card_id": card_id, "indicator": "blue"})
        self.assertEqual(invalid_indicator.exception.code, "validation_error")

    def test_legacy_combined_title_is_split_on_load(self) -> None:
        card = Card.from_dict(
            {
                "id": "legacy-card",
                "title": "CAMRY 70 / НЕТ ЗАПУСКА",
                "description": "Проверить АКБ",
                "column": "inbox",
            },
            valid_columns={"inbox"},
        )
        self.assertEqual(card.vehicle, "CAMRY 70")
        self.assertEqual(card.title, "НЕТ ЗАПУСКА")

    def test_explicit_empty_vehicle_preserves_title_with_separator(self) -> None:
        card = Card.from_dict(
            {
                "id": "modern-card",
                "vehicle": "",
                "title": "MCP write flow / updated",
                "description": "Smoke test",
                "column": "inbox",
            },
            valid_columns={"inbox"},
        )
        self.assertEqual(card.vehicle, "")
        self.assertEqual(card.title, "MCP write flow / updated")

    def test_seeds_demo_board_once_for_pristine_store(self) -> None:
        seeded = self.service.ensure_demo_board()
        snapshot = self.service.get_board_snapshot()

        self.assertTrue(seeded)
        self.assertGreaterEqual(len(snapshot["columns"]), 6)
        self.assertGreaterEqual(len(snapshot["cards"]), 10)
        self.assertGreaterEqual(len(snapshot["archive"]), 2)
        self.assertTrue(any(column["label"] == "ПРИЁМКА" for column in snapshot["columns"]))
        self.assertTrue(
            any(
                card["vehicle"] == "CAMRY 70" and card["title"] == "НЕТ ЗАПУСКА"
                for card in snapshot["cards"]
            )
        )
        self.assertFalse(self.service.ensure_demo_board())

    def test_does_not_seed_demo_board_when_user_data_exists(self) -> None:
        created = self.service.create_card({"title": "Моя карточка", "deadline": {"hours": 2}})
        seeded = self.service.ensure_demo_board()
        cards = self.service.get_cards()["cards"]

        self.assertFalse(seeded)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["id"], created["card"]["id"])

    def test_seeds_demo_board_for_empty_generic_board_with_only_setup_events(self) -> None:
        bundle = self.store.read_bundle()
        bundle["columns"] = [column for column in bundle["columns"] if column.id != "control"]
        bundle["events"].append(
            AuditEvent(
                id="setup-column-delete",
                timestamp=utc_now().isoformat(),
                actor_name="ADMIN",
                source="ui",
                action="column_deleted",
                message="ADMIN удалил столбец",
                card_id=None,
                details={"column_id": "control", "label": "На контроле"},
            )
        )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        seeded = self.service.ensure_demo_board()
        snapshot = self.service.get_board_snapshot()

        self.assertTrue(seeded)
        self.assertGreaterEqual(len(snapshot["columns"]), 6)
        self.assertTrue(any(column["id"] == "priemka" for column in snapshot["columns"]))

    def test_get_cards_skips_expensive_prep_when_board_is_empty(self) -> None:
        snapshot_service = self.service._snapshot_service
        snapshot_service._column_labels = Mock(wraps=snapshot_service._column_labels)
        snapshot_service._event_counts = Mock(wraps=snapshot_service._event_counts)

        cards_payload = self.service.get_cards()
        cards = cards_payload["cards"]

        self.assertEqual(cards, [])
        self.assertEqual(cards_payload["meta"]["total"], 0)
        self.assertEqual(cards_payload["meta"]["returned"], 0)
        self.assertFalse(cards_payload["meta"]["has_more"])
        self.assertEqual(snapshot_service._column_labels.call_count, 0)
        self.assertEqual(snapshot_service._event_counts.call_count, 0)

    def test_get_cards_compact_redacts_phone_and_vin_from_description_preview(self) -> None:
        created = self.service.create_card(
            {
                "title": "Редакция описания",
                "description": "Клиент: +7 (923) 123-45-67\nVIN: X4XKCN81140CY67957\nНужно проверить.",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        compact_cards = self.service.get_cards({"compact": True})["cards"]
        compact_card = next(card for card in compact_cards if card["id"] == card_id)

        self.assertNotIn("+7 (923) 123-45-67", compact_card["description"])
        self.assertNotIn("X4XKCN81140CY67957", compact_card["description"])
        self.assertIn("[PHONE]", compact_card["description"])
        self.assertIn("[VIN]", compact_card["description"])
        self.assertEqual(compact_card["description"], compact_card["description_preview"])

    def test_search_cards_supports_query_filters_and_archive(self) -> None:
        created_column = self.service.create_column({"label": "ЭЛЕКТРИКИ"})
        column_id = created_column["column"]["id"]

        active = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "ПЛАВАЕТ ХОЛОСТОЙ ХОД",
                "description": "Проверить дроссель и датчик холостого хода",
                "column": column_id,
                "tags": ["СРОЧНО", "ДИАГНОСТИКА"],
                "deadline": {"hours": 12},
            }
        )
        archived = self.service.create_card(
            {
                "vehicle": "LADA VESTA",
                "title": "АРХИВНАЯ ПРОВЕРКА",
                "description": "Старый кейс для возврата из архива",
                "tags": ["АРХИВ"],
                "deadline": {"hours": 4},
            }
        )
        self.service.archive_card({"card_id": archived["card"]["id"]})

        found = self.service.search_cards(
            {
                "query": "rio дроссель",
                "column": column_id,
                "tag": "срочно",
                "limit": 10,
            }
        )
        self.assertEqual(found["meta"]["total_matches"], 1)
        self.assertFalse(found["meta"]["has_more"])
        self.assertEqual(found["cards"][0]["id"], active["card"]["id"])
        self.assertEqual(found["cards"][0]["column_label"], "ЭЛЕКТРИКИ")
        self.assertEqual(found["cards"][0]["heading"], "KIA RIO / ПЛАВАЕТ ХОЛОСТОЙ ХОД")
        self.assertIn("vehicle", found["cards"][0]["match"]["fields"])

        archived_found = self.service.search_cards({"query": "архивная", "include_archived": True})
        self.assertEqual(archived_found["meta"]["total_matches"], 1)
        self.assertTrue(archived_found["cards"][0]["archived"])

        with self.assertRaises(ServiceError) as empty_search:
            self.service.search_cards({})
        self.assertEqual(empty_search.exception.code, "validation_error")

    def test_get_card_log_supports_limit_and_meta(self) -> None:
        created = self.service.create_card(
            {
                "title": "ЛОГ КАРТОЧКИ",
                "description": "Проверка limit",
                "deadline": {"hours": 2},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "description": "Первое изменение",
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        self.service.update_card(
            {
                "card_id": card_id,
                "description": "Второе изменение",
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )

        log = self.service.get_card_log({"card_id": card_id, "limit": 2})

        self.assertEqual(log["meta"]["limit"], 2)
        self.assertEqual(log["meta"]["events_returned"], 2)
        self.assertGreaterEqual(log["meta"]["events_total"], 3)
        self.assertTrue(log["meta"]["has_more"])
        self.assertEqual(len(log["events"]), 2)

    def test_search_cards_skips_event_count_build_when_no_matches(self) -> None:
        self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "SEARCH MISS",
                "description": "Card for empty search result optimization check.",
                "deadline": {"hours": 2},
            }
        )
        snapshot_service = self.service._snapshot_service
        snapshot_service._column_labels = Mock(wraps=snapshot_service._column_labels)
        snapshot_service._event_counts = Mock(wraps=snapshot_service._event_counts)

        found = self.service.search_cards({"query": "totally-missing-query", "limit": 5})

        self.assertEqual(found["cards"], [])
        self.assertEqual(found["meta"]["total_matches"], 0)
        self.assertEqual(snapshot_service._column_labels.call_count, 0)
        self.assertEqual(snapshot_service._event_counts.call_count, 0)

    def test_search_cards_normalizes_punctuation_and_service_markers(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST-CAR",
                "title": "[MCP TEST] Поиск по маркерам",
                "description": "Проверка поиска по mcp-test, скобкам и дефисам.",
                "tags": ["MCP_TEST", "SEARCH-CHECK"],
                "deadline": {"hours": 3},
            }
        )
        card_id = created["card"]["id"]

        by_plain_text = self.service.search_cards({"query": "mcp test", "limit": 5})
        self.assertEqual(by_plain_text["meta"]["total_matches"], 1)
        self.assertEqual(by_plain_text["cards"][0]["id"], card_id)

        by_hyphenated = self.service.search_cards({"query": "mcp-test", "limit": 5})
        self.assertEqual(by_hyphenated["meta"]["total_matches"], 1)
        self.assertEqual(by_hyphenated["cards"][0]["id"], card_id)

        by_tag_variant = self.service.search_cards({"query": "search check", "limit": 5})
        self.assertEqual(by_tag_variant["meta"]["total_matches"], 1)
        self.assertEqual(by_tag_variant["cards"][0]["id"], card_id)
        self.assertIn("tags", by_tag_variant["cards"][0]["match"]["fields"])

    def test_search_cards_matches_cyrillic_and_latin_vehicle_variants(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Ниссан Тиида",
                "title": "Диагностика",
                "description": "Проверка поиска по смешанным латинским и кириллическим формам.",
                "deadline": {"hours": 4},
            }
        )
        card_id = created["card"]["id"]

        by_latin = self.service.search_cards({"query": "Nissan Tiida", "limit": 5})
        self.assertEqual(by_latin["meta"]["total_matches"], 1)
        self.assertEqual(by_latin["cards"][0]["id"], card_id)

        by_short_latin = self.service.search_cards({"query": "Tiida", "limit": 5})
        self.assertEqual(by_short_latin["meta"]["total_matches"], 1)
        self.assertEqual(by_short_latin["cards"][0]["id"], card_id)

        by_cyrillic = self.service.search_cards({"query": "Тиида", "limit": 5})
        self.assertEqual(by_cyrillic["meta"]["total_matches"], 1)
        self.assertEqual(by_cyrillic["cards"][0]["id"], card_id)

    def test_colored_tags_roundtrip_and_search_by_label(self) -> None:
        created = self.service.create_card(
            {
                "title": "Цветные метки",
                "description": "Проверка цветов",
                "tags": [
                    {"label": "СРОЧНО", "color": "red"},
                    {"label": "СОГЛАСОВАТЬ", "color": "yellow"},
                ],
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        self.assertEqual(created["card"]["tags"], ["СРОЧНО", "СОГЛАСОВАТЬ"])
        self.assertEqual(created["card"]["tag_items"][0]["color"], "red")
        self.assertEqual(created["card"]["tag_items"][1]["color"], "yellow")

        found = self.service.search_cards({"query": "согласовать", "tag": "срочно", "limit": 5})
        self.assertEqual(found["meta"]["total_matches"], 1)
        self.assertEqual(found["cards"][0]["id"], card_id)

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "tags": [
                    {"label": "СРОЧНО", "color": "yellow"},
                    {"label": "СОГЛАСОВАТЬ", "color": "green"},
                ],
            }
        )
        self.assertEqual(updated["card"]["tag_items"][0]["color"], "yellow")
        self.assertEqual(updated["card"]["tag_items"][1]["color"], "green")
        events = self.service.get_card_log({"card_id": card_id})["events"]
        self.assertTrue(
            any(
                event["action"] == "tag_color_changed" and "изменил цвет метки" in event["message"]
                for event in events
            )
        )

    def test_rejects_more_than_three_tags(self) -> None:
        with self.assertRaises(ServiceError) as tag_limit_error:
            self.service.create_card(
                {
                    "title": "Слишком много меток",
                    "description": "Проверка ограничения",
                    "tags": ["СРОЧНО", "ЖДЁМ", "СОГЛАСОВАТЬ", "ЗАКАЗАТЬ"],
                    "deadline": {"hours": 2},
                }
            )

        self.assertEqual(tag_limit_error.exception.code, "validation_error")

    def test_create_card_supports_vehicle_profile_and_resolves_vehicle_label(self) -> None:
        created = self.service.create_card(
            {
                "title": "Техкарта Swift",
                "description": "Нужно собрать данные по автомобилю",
                "deadline": {"hours": 6},
                "vehicle_profile": {
                    "make_display": "Suzuki",
                    "model_display": "Swift",
                    "production_year": 2014,
                    "vin": "JSAZC72S001234567",
                    "engine_code": "K12B",
                    "registration_plate": "А123ВС77",
                    "pts_series": "77AA",
                    "pts_number": "123456",
                },
            }
        )

        self.assertEqual(created["card"]["vehicle"], "Suzuki Swift 2014")
        self.assertEqual(created["card"]["vehicle_profile"]["vin"], "JSAZC72S001234567")
        self.assertEqual(created["card"]["vehicle_profile"]["registration_plate"], "А123ВС77")
        self.assertEqual(created["card"]["vehicle_profile_compact"]["vin"], "JSAZC72S001234567")
        self.assertEqual(
            created["card"]["vehicle_profile_compact"]["display_name"], "Suzuki Swift 2014"
        )
        self.assertIn("make_display", created["card"]["vehicle_profile"]["manual_fields"])
        self.assertIn("engine_code", created["card"]["vehicle_profile"]["manual_fields"])

    def test_update_card_accepts_vehicle_profile_ui_alias_fields(self) -> None:
        created = self.service.create_card(
            {
                "title": "Паспорт автомобиля",
                "description": "Проверка сохранения правой панели",
                "deadline": {"hours": 6},
            }
        )

        updated = self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "vehicle_profile": {
                    "display_name": "Toyota Camry",
                    "license_plate": "А111АА124",
                    "manual_fields": ["display_name", "license_plate"],
                    "field_sources": {
                        "display_name": "manual_ui",
                        "license_plate": "manual_ui",
                    },
                },
            }
        )

        profile = updated["card"]["vehicle_profile"]
        self.assertEqual(profile["display_name"], "Toyota Camry")
        self.assertEqual(profile["make_display"], "Toyota")
        self.assertEqual(profile["model_display"], "Camry")
        self.assertEqual(profile["registration_plate"], "А111АА124")
        self.assertIn("make_display", profile["manual_fields"])
        self.assertIn("model_display", profile["manual_fields"])
        self.assertIn("registration_plate", profile["manual_fields"])
        self.assertEqual(profile["field_sources"]["make_display"], "manual_ui")
        self.assertEqual(profile["field_sources"]["registration_plate"], "manual_ui")

    def test_update_card_persists_vehicle_profile_display_name_from_full_ui_payload(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota Camry 2014",
                "title": "Паспорт автомобиля",
                "description": "Проверка полного payload формы",
                "deadline": {"hours": 6},
                "vehicle_profile": {
                    "make_display": "Toyota",
                    "model_display": "Camry",
                    "production_year": 2014,
                },
            }
        )["card"]

        updated = self.service.update_card(
            {
                "card_id": created["id"],
                "actor_name": "UI",
                "source": "ui",
                "vehicle": created["vehicle"],
                "title": created["title"],
                "description": created["description"],
                "deadline": {"hours": 6},
                "tags": [],
                "vehicle_profile": {
                    **created["vehicle_profile"],
                    "display_name": "Honda Fit",
                    "manual_fields": ["display_name"],
                    "field_sources": {"display_name": "manual_ui"},
                },
            }
        )["card"]
        reopened = self.service.get_card({"card_id": created["id"]})["card"]

        self.assertEqual(updated["vehicle_profile"]["display_name"], "Honda Fit 2014")
        self.assertEqual(updated["vehicle_profile"]["make_display"], "Honda")
        self.assertEqual(updated["vehicle_profile"]["model_display"], "Fit")
        self.assertEqual(reopened["vehicle_profile"]["display_name"], "Honda Fit 2014")
        self.assertEqual(reopened["vehicle"], "Honda Fit 2014")

    def test_update_card_stores_repair_order_and_persists_it(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Безналичный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        created = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "Замена масла",
                "description": "Клиент просит срочное обслуживание",
                "deadline": {"hours": 4},
            }
        )
        card_id = created["card"]["id"]

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "vehicle": "KIA RIO",
                    "license_plate": "А123АА124",
                    "payment_method": "cash",
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "06.04.2026 12:30",
                            "note": "Аванс",
                            "payment_method": "cash",
                            "actor_name": "ADMIN",
                            "cashbox_id": cashbox["id"],
                        }
                    ],
                    "client_information": "Кратко объяснить клиенту объём работ и следующие шаги",
                    "works": [
                        {"name": "Замена масла", "quantity": "1", "price": "2500", "total": ""}
                    ],
                    "materials": [
                        {
                            "name": "Масло 5W-30",
                            "catalog_number": "08880-12345",
                            "quantity": "4",
                            "price": "700",
                            "total": "9999",
                        }
                    ],
                },
            }
        )

        order = updated["card"]["repair_order"]
        self.assertEqual(order["number"], "1")
        self.assertRegex(order["date"], r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$")
        self.assertEqual(order["client"], "Иван Иванов")
        self.assertEqual(order["comment"], "Кратко объяснить клиенту объём работ и следующие шаги")
        self.assertEqual(order["client_information"], order["comment"])
        self.assertEqual(order["works"][0]["name"], "Замена масла")
        self.assertEqual(order["works"][0]["total"], "2500")
        self.assertEqual(order["materials"][0]["catalog_number"], "08880-12345")
        self.assertEqual(order["materials"][0]["total"], "2800")
        self.assertEqual(order["payment_method"], "cashless")
        self.assertTrue(order["payment_method_label"])
        self.assertEqual(order["prepayment"], "1000")
        self.assertEqual(order["prepayment_display"], "1000")
        self.assertEqual(order["paid_total"], "1000")
        self.assertEqual(order["payment_status"], "unpaid")
        self.assertEqual(order["payment_status_label"], "Не оплачен")
        self.assertEqual(len(order["payments"]), 1)
        self.assertEqual(order["payments"][0]["note"], "Аванс")
        self.assertEqual(order["payments"][0]["actor_name"], "ADMIN")
        self.assertEqual(order["payments"][0]["cashbox_name"], cashbox["name"])
        self.assertTrue(order["payments"][0]["cash_transaction_id"])
        self.assertEqual(order["works_total"], "2500")
        self.assertEqual(order["materials_total"], "2800")
        self.assertEqual(order["subtotal_total"], "5300")
        self.assertEqual(order["taxes_total"], "795")
        self.assertEqual(order["grand_total"], "6095")
        self.assertEqual(order["due_total"], "5095")
        self.assertTrue(order["has_taxes"])
        self.assertTrue(order["has_prepayment"])

        reloaded = CardService(
            JsonStore(state_file=self.state_file, logger=self.logger), self.logger
        )
        stored = reloaded.get_card({"card_id": card_id})["card"]["repair_order"]
        self.assertEqual(stored["number"], "1")
        self.assertEqual(stored["license_plate"], "А123АА124")
        self.assertEqual(
            stored["client_information"], "Кратко объяснить клиенту объём работ и следующие шаги"
        )
        self.assertEqual(stored["works"][0]["quantity"], "1")
        self.assertEqual(stored["materials"][0]["catalog_number"], "08880-12345")
        self.assertEqual(stored["payment_method"], "cashless")
        self.assertTrue(stored["payment_method_label"])
        self.assertEqual(stored["prepayment"], "1000")
        self.assertEqual(stored["paid_total"], "1000")
        self.assertEqual(stored["payment_status"], "unpaid")
        self.assertEqual(len(stored["payments"]), 1)
        self.assertEqual(stored["payments"][0]["cashbox_name"], cashbox["name"])
        self.assertEqual(stored["taxes_total"], "795")
        self.assertEqual(stored["grand_total"], "6095")
        self.assertEqual(stored["due_total"], "5095")

    def test_repair_order_cash_taxes_depend_on_selected_cashbox(self) -> None:
        cashless_cashbox = self.service.create_cashbox(
            {"name": "Безналичный", "actor_name": "ADMIN"}
        )["cashbox"]
        cash_cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card_cashless = self.service.create_card(
            {"vehicle": "AUDI A4", "title": "Диагностика", "deadline": {"hours": 2}}
        )["card"]
        updated_cashless = self.service.update_card(
            {
                "card_id": card_cashless["id"],
                "repair_order": {
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:00",
                            "note": "Аванс",
                            "payment_method": "cash",
                            "actor_name": "ADMIN",
                            "cashbox_id": cashless_cashbox["id"],
                        }
                    ],
                },
            }
        )["card"]["repair_order"]
        self.assertEqual(updated_cashless["payment_method"], "cashless")
        self.assertEqual(updated_cashless["taxes_total"], "150")
        self.assertEqual(updated_cashless["grand_total"], "1150")
        self.assertEqual(updated_cashless["due_total"], "650")

        updated_mixed = self.service.update_card(
            {
                "card_id": card_cashless["id"],
                "repair_order": {
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:00",
                            "note": "Аванс",
                            "payment_method": "cash",
                            "actor_name": "ADMIN",
                            "cashbox_id": cashless_cashbox["id"],
                        },
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:10",
                            "note": "Доплата",
                            "payment_method": "cash",
                            "actor_name": "ADMIN",
                            "cashbox_id": cash_cashbox["id"],
                        },
                    ],
                },
            }
        )["card"]["repair_order"]
        self.assertEqual(updated_mixed["payment_method"], "cashless")
        self.assertEqual(updated_mixed["taxes_total"], "150")
        self.assertEqual(updated_mixed["grand_total"], "1150")
        self.assertEqual(updated_mixed["paid_total"], "1000")
        self.assertEqual(updated_mixed["due_total"], "150")

        maria_cashbox = self.service.create_cashbox({"name": "Карта Мария", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card_maria = self.service.create_card(
            {"vehicle": "BMW X5", "title": "Осмотр", "deadline": {"hours": 2}}
        )["card"]
        updated_maria = self.service.update_card(
            {
                "card_id": card_maria["id"],
                "repair_order": {
                    "works": [{"name": "Осмотр", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:05",
                            "note": "Оплата",
                            "payment_method": "cashless",
                            "actor_name": "ADMIN",
                            "cashbox_id": maria_cashbox["id"],
                        }
                    ],
                },
            }
        )["card"]["repair_order"]
        self.assertEqual(updated_maria["payment_method"], "card")
        self.assertEqual(updated_maria["taxes_total"], "0")
        self.assertEqual(updated_maria["grand_total"], "1000")
        self.assertEqual(updated_maria["due_total"], "500")

    def test_repair_order_payments_route_to_cashbox_by_payment_method(self) -> None:
        supplier_cashbox = self.service.create_cashbox(
            {"name": "Алексей Снабженец", "actor_name": "ADMIN"}
        )["cashbox"]
        cash_cashbox = self.service.create_cashbox(
            {"name": "Касса наличных оплат", "actor_name": "ADMIN"}
        )["cashbox"]
        cashless_cashbox = self.service.create_cashbox(
            {"name": "Безналичная касса", "actor_name": "ADMIN"}
        )["cashbox"]
        card_cashbox = self.service.create_cashbox({"name": "На карту", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "TOYOTA CAMRY", "title": "Оплата", "deadline": {"hours": 2}}
        )["card"]

        order = self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [{"name": "Работы", "quantity": "1", "price": "6000"}],
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "06.04.2026 10:00",
                            "payment_method": "cash",
                            "cashbox_id": supplier_cashbox["id"],
                        },
                        {
                            "amount": "2000",
                            "paid_at": "06.04.2026 10:10",
                            "payment_method": "cashless",
                            "cashbox_id": supplier_cashbox["id"],
                        },
                        {
                            "amount": "3000",
                            "paid_at": "06.04.2026 10:20",
                            "payment_method": "card",
                            "cashbox_id": supplier_cashbox["id"],
                        },
                    ],
                },
            }
        )["card"]["repair_order"]

        payments_by_method = {payment["payment_method"]: payment for payment in order["payments"]}
        self.assertEqual(payments_by_method["cash"]["cashbox_id"], cash_cashbox["id"])
        self.assertEqual(payments_by_method["cashless"]["cashbox_id"], cashless_cashbox["id"])
        self.assertEqual(payments_by_method["card"]["cashbox_id"], card_cashbox["id"])

        supplier_details = self.service.get_cashbox(
            {"cashbox_id": supplier_cashbox["id"], "transaction_limit": 10}
        )["cashbox"]
        cash_details = self.service.get_cashbox(
            {"cashbox_id": cash_cashbox["id"], "transaction_limit": 10}
        )["cashbox"]
        cashless_details = self.service.get_cashbox(
            {"cashbox_id": cashless_cashbox["id"], "transaction_limit": 10}
        )["cashbox"]
        card_details = self.service.get_cashbox(
            {"cashbox_id": card_cashbox["id"], "transaction_limit": 10}
        )["cashbox"]

        self.assertEqual(supplier_details["statistics"]["transactions_total"], 0)
        self.assertEqual(cash_details["statistics"]["income_total_minor"], 100000)
        self.assertEqual(cashless_details["statistics"]["income_total_minor"], 200000)
        self.assertEqual(card_details["statistics"]["income_total_minor"], 300000)

    def test_repair_order_payment_date_change_recreates_cash_transaction(self) -> None:
        cashbox = self.service.create_cashbox(
            {"name": "Касса наличных оплат", "actor_name": "ADMIN"}
        )["cashbox"]
        card = self.service.create_card(
            {"vehicle": "TOYOTA CAMRY", "title": "Оплата", "deadline": {"hours": 2}}
        )["card"]
        base_payment = {
            "amount": "1000",
            "paid_at": "06.04.2026 10:00",
            "note": "Аванс",
            "payment_method": "cash",
            "cashbox_id": cashbox["id"],
            "actor_name": "ADMIN",
        }

        first_order = self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [{"name": "Работы", "quantity": "1", "price": "6000"}],
                    "payments": [base_payment],
                },
            }
        )["card"]["repair_order"]
        first_payment = first_order["payments"][0]
        first_transaction_id = first_payment["cash_transaction_id"]
        first_details = self.service.get_cashbox(
            {"cashbox_id": cashbox["id"], "transaction_limit": 10}
        )
        self.assertEqual(first_details["cashbox"]["statistics"]["transactions_total"], 1)
        self.assertTrue(
            first_details["transactions"][0]["created_at"].startswith("2026-04-06T10:00:00")
        )

        second_order = self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [{"name": "Работы", "quantity": "1", "price": "6000"}],
                    "payments": [
                        {
                            **base_payment,
                            "id": first_payment["id"],
                            "paid_at": "07.04.2026 11:15",
                        }
                    ],
                },
            }
        )["card"]["repair_order"]
        second_payment = second_order["payments"][0]
        second_details = self.service.get_cashbox(
            {"cashbox_id": cashbox["id"], "transaction_limit": 10}
        )

        self.assertNotEqual(second_payment["cash_transaction_id"], first_transaction_id)
        self.assertEqual(second_details["cashbox"]["statistics"]["transactions_total"], 1)
        self.assertTrue(
            second_details["transactions"][0]["created_at"].startswith("2026-04-07T11:15:00")
        )

    def test_repair_order_payment_summary_handles_cash_cashless_and_mixed_payments(self) -> None:
        cashless_cashbox = self.service.create_cashbox(
            {"name": "Безналичный", "actor_name": "ADMIN"}
        )["cashbox"]
        cash_cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]

        def update_order(payments: list[dict[str, str]] | None = None) -> dict[str, str]:
            created = self.service.create_card(
                {"vehicle": "TOYOTA CAMRY", "title": "Сводка", "deadline": {"hours": 2}}
            )["card"]
            result = self.service.update_card(
                {
                    "card_id": created["id"],
                    "repair_order": {
                        "works": [{"name": "Ремонт", "quantity": "1", "price": "20000"}],
                        **({"payments": payments} if payments is not None else {}),
                    },
                }
            )
            return result["card"]["repair_order"]

        scenarios = [
            (
                "no_payments",
                None,
                {
                    "base_total": "20000",
                    "base_paid_cash": "0",
                    "base_paid_noncash": "0",
                    "base_remaining": "20000",
                    "cash_due": "20000",
                    "noncash_due": "23000",
                    "taxes_and_fees": "0",
                    "total_paid": "0",
                },
            ),
            (
                "cash_partial",
                [
                    {
                        "amount": "10000",
                        "paid_at": "06.04.2026 10:00",
                        "note": "Нал",
                        "payment_method": "cash",
                        "actor_name": "ADMIN",
                        "cashbox_id": cash_cashbox["id"],
                    }
                ],
                {
                    "base_total": "20000",
                    "base_paid_cash": "10000",
                    "base_paid_noncash": "0",
                    "base_remaining": "10000",
                    "cash_due": "10000",
                    "noncash_due": "11500",
                    "taxes_and_fees": "0",
                    "total_paid": "10000",
                },
            ),
            (
                "cashless_partial",
                [
                    {
                        "amount": "10000",
                        "paid_at": "06.04.2026 10:00",
                        "note": "Безнал",
                        "payment_method": "cash",
                        "actor_name": "ADMIN",
                        "cashbox_id": cashless_cashbox["id"],
                    }
                ],
                {
                    "base_total": "20000",
                    "base_paid_cash": "0",
                    "base_paid_noncash": "10000",
                    "base_remaining": "10000",
                    "cash_due": "10000",
                    "noncash_due": "11500",
                    "taxes_and_fees": "1500",
                    "total_paid": "10000",
                },
            ),
            (
                "mixed_payment",
                [
                    {
                        "amount": "5000",
                        "paid_at": "06.04.2026 10:00",
                        "note": "Нал",
                        "payment_method": "cash",
                        "actor_name": "ADMIN",
                        "cashbox_id": cash_cashbox["id"],
                    },
                    {
                        "amount": "5000",
                        "paid_at": "06.04.2026 10:10",
                        "note": "Безнал",
                        "payment_method": "cash",
                        "actor_name": "ADMIN",
                        "cashbox_id": cashless_cashbox["id"],
                    },
                ],
                {
                    "base_total": "20000",
                    "base_paid_cash": "5000",
                    "base_paid_noncash": "5000",
                    "base_remaining": "10000",
                    "cash_due": "10000",
                    "noncash_due": "11500",
                    "taxes_and_fees": "750",
                    "total_paid": "10000",
                },
            ),
            (
                "full_close",
                [
                    {
                        "amount": "20000",
                        "paid_at": "06.04.2026 10:00",
                        "note": "Закрытие",
                        "payment_method": "cash",
                        "actor_name": "ADMIN",
                        "cashbox_id": cash_cashbox["id"],
                    }
                ],
                {
                    "base_total": "20000",
                    "base_paid_cash": "20000",
                    "base_paid_noncash": "0",
                    "base_remaining": "0",
                    "cash_due": "0",
                    "noncash_due": "0",
                    "taxes_and_fees": "0",
                    "total_paid": "20000",
                },
            ),
        ]

        for scenario_name, payments, expected in scenarios:
            with self.subTest(scenario=scenario_name):
                order = update_order(payments)
                summary = order["payment_summary"]
                for key, value in expected.items():
                    self.assertEqual(summary[key], value)
                self.assertEqual(order["subtotal_total"], "20000")
                self.assertEqual(order["payment_summary"]["base_total"], order["subtotal_total"])
                self.assertEqual(
                    order["payment_summary"]["cash_due"], order["payment_summary"]["base_remaining"]
                )

    def test_list_repair_orders_creates_text_files_and_sorts_by_latest_number(self) -> None:
        first = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Первый заказ", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"vehicle": "LADA VESTA", "title": "Второй заказ", "deadline": {"hours": 2}}
        )

        first_id = first["card"]["id"]
        second_id = second["card"]["id"]

        self.service.update_card(
            {
                "card_id": first_id,
                "repair_order": {
                    "client": "Иван",
                    "comment": "Первый текстовый заказ-наряд",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1000", "total": "1000"}
                    ],
                },
            }
        )
        self.service.update_card(
            {
                "card_id": second_id,
                "repair_order": {
                    "client": "Петр",
                    "comment": "Второй текстовый заказ-наряд",
                    "materials": [
                        {"name": "Масло", "quantity": "4", "price": "700", "total": "2800"}
                    ],
                },
            }
        )

        listed = self.service.list_repair_orders()
        self.assertEqual(listed["meta"]["limit"], 300)
        self.assertEqual(listed["repair_orders"][0]["number"], "2")
        self.assertEqual(listed["repair_orders"][1]["number"], "1")
        self.assertEqual(listed["repair_orders"][0]["grand_total"], "2800")
        self.assertEqual(listed["repair_orders"][0]["paid_total"], "0")
        self.assertEqual(listed["repair_orders"][0]["payment_status"], "unpaid")
        self.assertEqual(listed["repair_orders"][0]["vehicle"], "LADA VESTA")
        self.assertEqual(listed["repair_orders"][0]["created_at"], second["card"]["created_at"])

        file_path = Path(listed["repair_orders"][0]["file_path"])
        self.assertTrue(file_path.exists())
        text = file_path.read_text(encoding="utf-8")
        self.assertIn("2", text)
        self.assertIn("2800", text)
        self.assertIn("LADA VESTA", text)
        self.assertIn("JSON:", text)

        download_path, file_name = self.service.get_repair_order_text_download(second_id)
        self.assertEqual(download_path.name, file_name)
        self.assertEqual(download_path, file_path)

    def test_list_repair_orders_serializes_only_requested_limit(self) -> None:
        for index in range(3):
            created = self.service.create_card(
                {"vehicle": f"CAR-{index}", "title": f"Order {index}", "deadline": {"hours": 2}}
            )
            self.service.update_card(
                {
                    "card_id": created["card"]["id"],
                    "repair_order": {
                        "client": f"Client {index}",
                        "works": [
                            {
                                "name": f"Work {index}",
                                "quantity": "1",
                                "price": "1000",
                                "total": "1000",
                            }
                        ],
                    },
                }
            )

        with patch.object(
            self.service,
            "_serialize_repair_order_list_item",
            wraps=self.service._serialize_repair_order_list_item,
        ) as serialize_item:
            listed = self.service.list_repair_orders({"limit": 2})

        self.assertEqual(listed["meta"]["total"], 3)
        self.assertEqual(listed["meta"]["limit"], 2)
        self.assertEqual(len(listed["repair_orders"]), 2)
        self.assertEqual(serialize_item.call_count, 2)
        self.assertEqual([item["number"] for item in listed["repair_orders"]], ["3", "2"])

    def test_list_repair_orders_cleans_up_old_text_files_beyond_retention_limit(self) -> None:
        first = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Order one", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"vehicle": "LADA VESTA", "title": "Order two", "deadline": {"hours": 2}}
        )

        self.service.update_card(
            {
                "card_id": first["card"]["id"],
                "repair_order": {
                    "client": "A",
                    "works": [{"name": "W1", "quantity": "1", "price": "1", "total": "1"}],
                },
            }
        )
        self.service.update_card(
            {
                "card_id": second["card"]["id"],
                "repair_order": {
                    "client": "B",
                    "works": [{"name": "W2", "quantity": "1", "price": "2", "total": "2"}],
                },
            }
        )

        first_path, _ = self.service.get_repair_order_text_download(first["card"]["id"])
        second_path, _ = self.service.get_repair_order_text_download(second["card"]["id"])
        self.assertTrue(first_path.exists())
        self.assertTrue(second_path.exists())

        with patch("minimal_kanban.services.card_service.REPAIR_ORDER_FILE_RETENTION_LIMIT", 1):
            self.service.list_repair_orders()

        self.assertFalse(first_path.exists())
        self.assertTrue(second_path.exists())

    def test_repair_order_text_file_name_sanitizes_windows_unsafe_characters(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "BMW X5",
                "title": "Диагностика: ограничение мощности / DSC?",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1000", "total": ""}
                    ],
                },
            }
        )

        path, file_name = self.service.get_repair_order_text_download(card_id)

        self.assertTrue(path.exists())
        self.assertEqual(path.name, file_name)
        self.assertNotIn(":", file_name)
        self.assertNotIn("?", file_name)
        self.assertNotIn("/", file_name)
        self.assertTrue(file_name.endswith(".txt"))
        self.assertIn("__", file_name)
        self.assertEqual(updated["card"]["repair_order"]["number"], "1")

    def test_list_repair_orders_separates_open_and_closed_orders(self) -> None:
        first = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Open order", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"vehicle": "LADA VESTA", "title": "Closed order", "deadline": {"hours": 2}}
        )

        self.service.update_card(
            {
                "card_id": first["card"]["id"],
                "repair_order": {
                    "client": "Иван",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1000", "total": ""}
                    ],
                },
            }
        )
        self.service.update_card(
            {
                "card_id": second["card"]["id"],
                "repair_order": {
                    "client": "Пётр",
                    "payments": [
                        {"amount": "2000", "paid_at": "06.04.2026 12:00", "payment_method": "cash"}
                    ],
                    "works": [{"name": "Ремонт", "quantity": "1", "price": "2000", "total": ""}],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": second["card"]["id"], "status": "closed"})

        active = self.service.list_repair_orders()
        archived = self.service.list_repair_orders({"status": "closed"})
        all_orders = self.service.list_repair_orders({"status": "all"})

        self.assertEqual(active["meta"]["status"], "open")
        self.assertEqual(active["meta"]["active_total"], 1)
        self.assertEqual(active["meta"]["archived_total"], 1)
        self.assertEqual(
            [item["card_id"] for item in active["repair_orders"]], [first["card"]["id"]]
        )

        self.assertEqual(archived["meta"]["status"], "closed")
        self.assertEqual(
            [item["card_id"] for item in archived["repair_orders"]], [second["card"]["id"]]
        )
        self.assertEqual(archived["repair_orders"][0]["status"], "closed")

        self.assertEqual(all_orders["meta"]["total"], 2)
        self.assertEqual(all_orders["repair_orders"][0]["card_id"], second["card"]["id"])

    def test_repair_order_numbers_follow_card_open_time_not_update_order(self) -> None:
        first = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "First card", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"vehicle": "LADA VESTA", "title": "Second card", "deadline": {"hours": 2}}
        )

        self.service.update_card(
            {
                "card_id": second["card"]["id"],
                "repair_order": {
                    "client": "Пётр",
                    "works": [
                        {
                            "name": "Поздняя в списке первая",
                            "quantity": "1",
                            "price": "1000",
                            "total": "",
                        }
                    ],
                },
            }
        )
        self.service.update_card(
            {
                "card_id": first["card"]["id"],
                "repair_order": {
                    "client": "Иван",
                    "works": [
                        {
                            "name": "Хронологически первая карточка",
                            "quantity": "1",
                            "price": "1000",
                            "total": "",
                        }
                    ],
                },
            }
        )

        listed = self.service.list_repair_orders({"status": "all"})
        by_card_id = {item["card_id"]: item for item in listed["repair_orders"]}

        self.assertEqual(by_card_id[first["card"]["id"]]["number"], "1")
        self.assertEqual(by_card_id[second["card"]["id"]]["number"], "2")

    def test_list_repair_orders_supports_query_sort_and_tags(self) -> None:
        first = self.service.create_card(
            {"vehicle": "Audi A6", "title": "Диагностика DSG", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"vehicle": "BMW X5", "title": "Замена масла", "deadline": {"hours": 2}}
        )

        self.service.update_repair_order(
            {
                "card_id": first["card"]["id"],
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "comment": "Проверить DSG и согласовать диагностику",
                    "tags": [
                        {"label": "Срочно", "color": "yellow"},
                        {"label": "DSG", "color": "green"},
                    ],
                    "works": [
                        {"name": "Диагностика DSG", "quantity": "1", "price": "2500", "total": ""}
                    ],
                },
            }
        )
        self.service.update_repair_order(
            {
                "card_id": second["card"]["id"],
                "repair_order": {
                    "client": "Петр Петров",
                    "phone": "+7 901 000-11-22",
                    "comment": "Стандартное ТО",
                    "works": [
                        {"name": "Замена масла", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            }
        )

        filtered = self.service.list_repair_orders(
            {
                "status": "all",
                "query": "срочно иван dsg",
                "sort_by": "number",
                "sort_dir": "asc",
            }
        )

        self.assertEqual(filtered["meta"]["status"], "all")
        self.assertEqual(filtered["meta"]["query"], "срочно иван dsg")
        self.assertEqual(filtered["meta"]["sort_by"], "number")
        self.assertEqual(filtered["meta"]["sort_dir"], "asc")
        self.assertEqual(len(filtered["repair_orders"]), 1)
        self.assertEqual(filtered["repair_orders"][0]["card_id"], first["card"]["id"])
        self.assertEqual(
            filtered["repair_orders"][0]["tags"],
            [
                {"label": "СРОЧНО", "color": "yellow"},
                {"label": "DSG", "color": "green"},
            ],
        )

        ordered = self.service.list_repair_orders(
            {"status": "all", "sort_by": "number", "sort_dir": "asc"}
        )
        self.assertEqual([item["number"] for item in ordered["repair_orders"]], ["1", "2"])

    def test_archived_card_retention_cleans_up_orphan_attachment_directories(self) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)

        with patch("minimal_kanban.storage.json_store.ARCHIVED_CARD_RETENTION_LIMIT", 1):
            first = service.create_card(
                {"vehicle": "KIA RIO", "title": "Archive one", "deadline": {"hours": 2}}
            )
            second = service.create_card(
                {"vehicle": "LADA VESTA", "title": "Archive two", "deadline": {"hours": 2}}
            )

            service.add_card_attachment(
                {
                    "card_id": first["card"]["id"],
                    "file_name": "first.txt",
                    "mime_type": "text/plain",
                    "content_base64": base64.b64encode(b"first").decode("ascii"),
                }
            )
            service.add_card_attachment(
                {
                    "card_id": second["card"]["id"],
                    "file_name": "second.txt",
                    "mime_type": "text/plain",
                    "content_base64": base64.b64encode(b"second").decode("ascii"),
                }
            )

            first_dir = attachments_dir / first["card"]["id"]
            second_dir = attachments_dir / second["card"]["id"]
            self.assertTrue(first_dir.exists())
            self.assertTrue(second_dir.exists())

            service.archive_card({"card_id": first["card"]["id"]})
            self.assertTrue(first_dir.exists())

            service.archive_card({"card_id": second["card"]["id"]})

        self.assertFalse(first_dir.exists())
        self.assertTrue(second_dir.exists())

    def test_remove_card_attachment_deletes_file_and_empty_card_directory(self) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)
        created = service.create_card(
            {"vehicle": "KIA RIO", "title": "Attachment remove", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]

        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "report.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"hello").decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        file_path, _ = service.get_attachment_download(card_id, attachment_id)

        self.assertTrue(file_path.exists())
        self.assertTrue(file_path.parent.exists())

        removed = service.remove_card_attachment(
            {"card_id": card_id, "attachment_id": attachment_id}
        )

        self.assertFalse(file_path.exists())
        self.assertFalse(file_path.parent.exists())
        self.assertEqual(removed["card"]["attachment_count"], 0)

    def test_allowed_attachment_roundtrip_preserves_name_mime_and_bytes(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "KIA RIO", "title": "Attachment roundtrip", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        samples = [
            ("клиент фото.png", "image/png", PNG_1X1_BYTES),
            ("клиент фото.jpg", "image/jpeg", JPEG_1X1_BYTES),
            ("клиент фото.jpeg", "image/jpeg", JPEG_1X1_BYTES),
            ("preview.gif", "image/gif", GIF_1X1_BYTES),
            (
                "report.final.v1.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                minimal_docx_bytes(),
            ),
            (
                "report.final.v1.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                minimal_xlsx_bytes(),
            ),
            ("диагностика финал.txt", "text/plain", minimal_text_bytes()),
            ("счёт.final copy.pdf", "application/pdf", minimal_pdf_bytes()),
        ]

        for file_name, mime_type, payload in samples:
            with self.subTest(file_name=file_name):
                added = service.add_card_attachment(
                    {
                        "card_id": card_id,
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "content_base64": base64.b64encode(payload).decode("ascii"),
                    }
                )
                attachment_id = added["attachment"]["id"]
                file_path, attachment = service.get_attachment_download(card_id, attachment_id)

                self.assertEqual(attachment.file_name, file_name)
                self.assertEqual(attachment.mime_type, mime_type)
                self.assertEqual(attachment.size_bytes, len(payload))
                self.assertEqual(file_path.suffix.lower(), Path(file_name).suffix.lower())
                self.assertEqual(file_path.read_bytes(), payload)

    def test_attachment_upload_generates_safe_name_for_missing_clipboard_image_name(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "BMW", "title": "Clipboard image", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]

        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
            }
        )

        attachment = added["attachment"]
        self.assertTrue(attachment["file_name"].startswith("attachment-"))
        self.assertTrue(attachment["file_name"].endswith(".png"))
        self.assertEqual(attachment["mime_type"], "image/png")

    def test_attachment_long_file_name_keeps_pdf_extension_after_truncation(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "BMW", "title": "Long attachment name", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        long_file_name = ("очень длинное имя файла." * 20) + "pdf"

        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": long_file_name,
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(minimal_pdf_bytes()).decode("ascii"),
            }
        )

        attachment = added["attachment"]
        self.assertLessEqual(len(attachment["file_name"]), 240)
        self.assertTrue(attachment["file_name"].endswith(".pdf"))

    def test_attachment_upload_rejects_disallowed_extensions_double_extensions_and_fake_mime(
        self,
    ) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "KIA RIO", "title": "Attachment validation", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        cases = [
            ("payload.exe", "application/x-msdownload", b"MZ\x90\x00", "Разрешены только"),
            ("payload.js", "application/javascript", b"alert(1);", "Разрешены только"),
            ("payload.exe.pdf", "application/pdf", minimal_pdf_bytes(), "двойное расширение"),
            ("payload.pdf", "application/pdf", b"MZ\x00\x02\x03\x00\x00", "не распознан"),
        ]

        for file_name, mime_type, payload, message_part in cases:
            with self.subTest(file_name=file_name):
                with self.assertRaises(ServiceError) as exc:
                    service.add_card_attachment(
                        {
                            "card_id": card_id,
                            "file_name": file_name,
                            "mime_type": mime_type,
                            "content_base64": base64.b64encode(payload).decode("ascii"),
                        }
                    )
                self.assertEqual(exc.exception.code, "validation_error")
                self.assertIn(message_part, exc.exception.message)

    def test_attachment_download_repairs_legacy_extension_mime_and_storage_name(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "AUDI", "title": "Legacy attachment", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        payload = minimal_pdf_bytes()
        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "Отчёт клиента.final.pdf",
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(payload).decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        current_path, _ = service.get_attachment_download(card_id, attachment_id)
        legacy_path = current_path.with_name(attachment_id)
        current_path.rename(legacy_path)

        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == card_id)
        attachment = next(item for item in card.attachments if item.id == attachment_id)
        attachment.file_name = "Отчёт клиента.final"
        attachment.mime_type = "application/octet-stream"
        attachment.stored_name = attachment_id
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        repaired_path, repaired_attachment = service.get_attachment_download(card_id, attachment_id)

        self.assertEqual(repaired_attachment.file_name, "Отчёт клиента.final.pdf")
        self.assertEqual(repaired_attachment.mime_type, "application/pdf")
        self.assertEqual(repaired_path.name, f"{attachment_id}.pdf")
        self.assertEqual(repaired_path.read_bytes(), payload)

    def test_attachment_persistence_survives_service_restart(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "VW", "title": "Attachment persistence", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        payload = minimal_docx_bytes("Persistence check")
        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "Persistence финал.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "content_base64": base64.b64encode(payload).decode("ascii"),
            }
        )

        restarted = self._build_service()
        card = restarted.get_card({"card_id": card_id})["card"]
        attachment = card["attachments"][0]
        repaired_path, repaired_attachment = restarted.get_attachment_download(
            card_id, added["attachment"]["id"]
        )

        self.assertEqual(attachment["file_name"], "Persistence финал.docx")
        self.assertEqual(
            attachment["mime_type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertEqual(repaired_attachment.file_name, "Persistence финал.docx")
        self.assertEqual(repaired_path.read_bytes(), payload)

    def test_agent_attachment_read_extracts_text_office_and_image_payloads(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "VW", "title": "Agent attachment read", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        text_attachment = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "client-note.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(minimal_text_bytes()).decode("ascii"),
            }
        )["attachment"]
        docx_attachment = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "agent-report.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "content_base64": base64.b64encode(
                    minimal_docx_bytes("Agent DOCX text")
                ).decode("ascii"),
            }
        )["attachment"]
        image_attachment = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "photo.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
            }
        )["attachment"]

        listed = service.list_card_attachments({"card_id": card_id})
        self.assertEqual(listed["meta"]["total"], 3)
        listed_by_id = {item["id"]: item for item in listed["attachments"]}
        self.assertEqual(listed_by_id[text_attachment["id"]]["content_kind"], "text")
        self.assertTrue(listed_by_id[docx_attachment["id"]]["readable_as_text"])
        self.assertEqual(listed_by_id[image_attachment["id"]]["content_kind"], "image")

        text_read = service.read_card_attachment(
            {"card_id": card_id, "attachment_id": text_attachment["id"], "mode": "text"}
        )
        self.assertIn("AutoStop CRM", text_read["content"]["text"])
        self.assertEqual(text_read["content"]["extraction_status"], "ok")

        docx_read = service.read_card_attachment(
            {"card_id": card_id, "attachment_id": docx_attachment["id"], "mode": "text"}
        )
        self.assertIn("Agent DOCX text", docx_read["content"]["text"])
        self.assertEqual(docx_read["content"]["encoding"], "office-openxml")

        image_read = service.read_card_attachment(
            {
                "card_id": card_id,
                "attachment_id": image_attachment["id"],
                "mode": "base64",
                "max_base64_bytes": 10000,
            }
        )
        self.assertEqual(image_read["content"]["image"], {"width": 1, "height": 1})
        self.assertTrue(image_read["content"]["base64_included"])
        self.assertTrue(image_read["content"]["data_url"].startswith("data:image/png;base64,"))

    def test_get_card_context_returns_repair_order_text_and_board_context(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "BMW 320i",
                "title": "Горит чек",
                "description": "Клиент жалуется на нестабильную работу двигателя",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1200", "total": ""}
                    ],
                },
            }
        )

        context = self.service.get_card_context({"card_id": card_id, "event_limit": 10})

        self.assertEqual(context["card"]["id"], card_id)
        self.assertEqual(context["card"]["events_count"], 2)
        self.assertTrue(context["meta"]["has_repair_order"])
        self.assertEqual(context["meta"]["events_returned"], 2)
        self.assertIn("Current AutoStop CRM Board", context["board_context"]["text"])
        self.assertIn("repair_order_updated", {event["action"] for event in context["events"]})
        self.assertIn("ЗАКАЗ-НАРЯД", context["repair_order_text"]["text"])
        self.assertIn("Стоимость заказ-наряда: 1200", context["repair_order_text"]["text"])
        self.assertIn("Итого по заказ-наряду: 1200", context["repair_order_text"]["text"])
        self.assertIn("К доплате: 1200", context["repair_order_text"]["text"])

    def test_repair_order_patch_and_row_replacement_tools_update_order(self) -> None:
        created = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Ремонт", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]

        patched = self.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Петров Пётр",
                    "phone": "+7 999 123-45-67",
                    "client_information": "Нужно согласовать объём работ",
                },
            }
        )
        self.assertEqual(patched["repair_order"]["client"], "Петров Пётр")
        self.assertEqual(patched["repair_order"]["comment"], "Нужно согласовать объём работ")

        works = self.service.replace_repair_order_works(
            {
                "card_id": card_id,
                "rows": [
                    {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""},
                    {"name": "Снятие ошибок", "quantity": "1", "price": "500", "total": ""},
                ],
            }
        )
        self.assertEqual(len(works["repair_order"]["works"]), 2)
        self.assertEqual(works["repair_order"]["works_total"], "2000")

        materials = self.service.replace_repair_order_materials(
            {
                "card_id": card_id,
                "rows": [
                    {"name": "Очиститель контактов", "quantity": "2", "price": "350", "total": ""},
                ],
            }
        )
        self.assertEqual(materials["repair_order"]["materials_total"], "700")
        self.assertEqual(materials["repair_order"]["grand_total"], "2700")

    def test_search_cards_matches_repair_order_fields(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Nissan Teana J32",
                "title": "АКПП",
                "description": "Госномер В003НК124",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "license_plate": "В003НК124",
                    "works": [
                        {"name": "Диагностика АКПП", "quantity": "1", "price": "2000", "total": ""}
                    ],
                },
            }
        )

        by_number = self.service.search_cards({"query": "1", "limit": 10})
        self.assertTrue(any(card["id"] == card_id for card in by_number["cards"]))

        by_client = self.service.search_cards({"query": "Иван Иванов", "limit": 10})
        self.assertEqual(by_client["cards"][0]["id"], card_id)
        self.assertIn("repair_order_client", by_client["cards"][0]["match"]["fields"])

        by_plate = self.service.search_cards({"query": "В003НК124", "limit": 10})
        self.assertEqual(by_plate["cards"][0]["id"], card_id)
        self.assertIn("repair_order_license_plate", by_plate["cards"][0]["match"]["fields"])

    def test_autofill_repair_order_preserves_manual_values_and_fills_missing_fields(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Volkswagen Tiguan II",
                "title": "ТО DSG/АКПП",
                "description": "Госномер А123АА124. Выполнить обслуживание и замену расходников.",
                "deadline": {"hours": 6},
                "vehicle_profile": {
                    "customer_name": "Петров Пётр",
                    "customer_phone": "+7 999 000-11-22",
                    "make_display": "Volkswagen",
                    "model_display": "Tiguan II",
                    "production_year": 2019,
                    "mileage": 98000,
                },
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "РУЧНОЙ КЛИЕНТ",
                    "materials": [{"name": "ATF", "quantity": "1", "price": "", "total": ""}],
                },
            }
        )

        autofilled = self.service.autofill_repair_order({"card_id": card_id})

        order = autofilled["repair_order"]
        self.assertEqual(order["number"], "1")
        self.assertEqual(order["client"], "РУЧНОЙ КЛИЕНТ")
        self.assertEqual(order["phone"], "+7 999 000-11-22")
        self.assertEqual(order["vehicle"], "Volkswagen Tiguan II")
        self.assertEqual(order["mileage"], "98000")
        self.assertEqual(order["license_plate"], "А123АА124")
        self.assertIn("заявка принята", order["comment"].lower())
        self.assertIn("автомобиль: volkswagen tiguan ii", order["comment"].lower())
        self.assertEqual(order["works"], [])
        self.assertEqual(order["materials"][0]["name"], "ATF")
        self.assertEqual(order["materials"][0]["price"], "")

    def test_autofill_repair_order_extracts_structured_rows_and_client_summary_from_text(
        self,
    ) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Volkswagen Tiguan II",
                "title": "Пинки АКПП на 2-3 передаче",
                "description": (
                    "Клиент: Иван Иванов\n"
                    "Телефон: +7 900 123-45-67\n"
                    "Госномер А123АА124\n"
                    "VIN WVWZZZ1KZBP123456\n"
                    "Пробег: 145000\n"
                    "Жалоба: пинки DSG на 2-3 передаче, течь поддона.\n"
                    "Обнаружили: загрязнение масла и запотевание поддона.\n"
                    "Работы: диагностика DSG, адаптация DSG, замена масла АКПП\n"
                    "Материалы: ATF 6 л, фильтр АКПП 1 шт, прокладка поддона 1 шт\n"
                    "Рекомендовано: контрольный осмотр через 1000 км."
                ),
                "deadline": {"hours": 6},
                "vehicle_profile": {
                    "make_display": "Volkswagen",
                    "model_display": "Tiguan II",
                    "production_year": 2019,
                },
            }
        )

        autofilled = self.service.autofill_repair_order({"card_id": created["card"]["id"]})

        order = autofilled["repair_order"]
        self.assertEqual(order["client"], "Иван Иванов")
        self.assertEqual(order["phone"], "+7 900 123-45-67")
        self.assertEqual(order["license_plate"], "А123АА124")
        self.assertEqual(order["vin"], "WVWZZZ1KZBP123456")
        self.assertEqual(order["mileage"], "145000")
        self.assertIn("пинки dsg", order["reason"].lower())
        self.assertIn("Заявка принята", order["client_information"])
        self.assertIn("клиент: Иван Иванов", order["client_information"])
        self.assertIn("В ходе проверки выявлено", order["client_information"])
        self.assertIn("Технические замечания", order["note"])
        self.assertEqual(order["works"], [])
        self.assertEqual(order["materials"], [])

    def test_autofill_repair_order_keeps_money_and_rows_untouched(self) -> None:
        vin = "WVWZZZ1KZBP123456"
        current = self.service.create_card(
            {
                "vehicle": "Volkswagen Tiguan II",
                "title": "Жалоба DSG",
                "description": "VIN WVWZZZ1KZBP123456\nЖалоба: пинки DSG.\nРаботы: Диагностика DSG\nМатериалы: ATF 6 л",
                "deadline": {"hours": 4},
                "vehicle_profile": {"vin": vin},
            }
        )
        card_id = current["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "materials": [{"name": "ATF", "quantity": "", "price": "", "total": ""}],
                },
            }
        )

        autofilled = self.service.autofill_repair_order({"card_id": card_id})

        order = autofilled["repair_order"]
        self.assertEqual(len(order["materials"]), 1)
        self.assertEqual(order["materials"][0]["name"], "ATF")
        self.assertEqual(order["materials"][0]["quantity"], "")
        self.assertEqual(order["materials"][0]["price"], "")
        self.assertEqual(order["grand_total"], "0")
        self.assertIn("filled_fields", autofilled["meta"]["autofill_report"])

    def test_search_cards_matches_vehicle_profile_fields(self) -> None:
        created = self.service.create_card(
            {
                "title": "Проверка поиска по техкарте",
                "description": "Карточка без явного текста в описании по VIN",
                "deadline": {"hours": 4},
                "vehicle_profile": {
                    "make_display": "Suzuki",
                    "model_display": "Swift",
                    "production_year": 2014,
                    "vin": "JSAZC72S001234567",
                    "engine_code": "K12B",
                },
            }
        )
        card_id = created["card"]["id"]

        by_vin = self.service.search_cards({"query": "JSAZC72S001234567", "limit": 5})
        self.assertEqual(by_vin["meta"]["total_matches"], 1)
        self.assertEqual(by_vin["cards"][0]["id"], card_id)
        self.assertIn("vin", by_vin["cards"][0]["match"]["fields"])

        by_engine = self.service.search_cards({"query": "K12B", "limit": 5})
        self.assertEqual(by_engine["meta"]["total_matches"], 1)
        self.assertEqual(by_engine["cards"][0]["id"], card_id)
        self.assertIn("engine_code", by_engine["cards"][0]["match"]["fields"])

    def test_autofill_vehicle_data_preserves_manual_fields_and_enriches_missing_values(
        self,
    ) -> None:
        with patch.object(
            self.service._vehicle_profiles,
            "_enrich_from_vin_decode",
            return_value=VehicleProfile.from_dict(
                {
                    "gearbox_model": "A6GF1",
                    "gearbox_type": "automatic",
                    "source_summary": "VIN decoded",
                    "source_confidence": 0.91,
                    "autofilled_fields": ["gearbox_model", "gearbox_type"],
                    "field_sources": {
                        "gearbox_model": "official_vin_decode_nhtsa",
                        "gearbox_type": "official_vin_decode_nhtsa",
                    },
                    "source_links_or_refs": [
                        "vin:https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended"
                    ],
                }
            ),
        ):
            autofilled = self.service.autofill_vehicle_data(
                {
                    "raw_text": "Suzuki Swift 2014 VIN JSAZC72S001234567, нужен осмотр подвески",
                    "existing_profile": {
                        "make_display": "Suzuki",
                        "model_display": "Swift",
                        "production_year": 2014,
                        "engine_code": "CUSTOM-ENGINE",
                        "manual_fields": [
                            "engine_code",
                            "make_display",
                            "model_display",
                            "production_year",
                        ],
                    },
                    "explicit_description": "Клиент жалуется на стук спереди",
                }
            )

        profile = autofilled["vehicle_profile"]
        self.assertEqual(profile["engine_code"], "CUSTOM-ENGINE")
        self.assertEqual(profile["gearbox_model"], "A6GF1")
        self.assertEqual(profile["gearbox_type"], "automatic")
        self.assertIn("engine_code", profile["manual_fields"])
        self.assertNotIn("engine_code", profile["autofilled_fields"])
        self.assertIn("gearbox_model", profile["autofilled_fields"])
        self.assertEqual(autofilled["card_draft"]["vehicle"], "Suzuki Swift 2014")

    def test_autofill_vehicle_data_extracts_contact_platform_and_transmission_details(self) -> None:
        with patch.object(
            self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None
        ):
            autofilled = self.service.autofill_vehicle_data(
                {
                    "raw_text": (
                        "Toyota Camry XV70 2019\n"
                        "Пробег: 185000\n"
                        "Клиент: Иван Петров\n"
                        "Телефон: +7 (900) 123-45-67\n"
                        "VIN JTNB11HK103456789\n"
                        "Двигатель: A25A-FKS\n"
                        "АКПП UA80E\n"
                        "Передний привод, бензин"
                    ),
                }
            )

        profile = autofilled["vehicle_profile"]
        self.assertEqual(profile["make_display"], "Toyota")
        self.assertEqual(profile["model_display"], "Camry")
        self.assertEqual(profile["generation_or_platform"], "XV70")
        self.assertEqual(profile["mileage"], 185000)
        self.assertEqual(profile["customer_name"], "Иван Петров")
        self.assertEqual(profile["customer_phone"], "+7 900 123-45-67")
        self.assertEqual(profile["gearbox_model"], "UA80E")
        self.assertEqual(profile["gearbox_type"], "automatic")
        self.assertEqual(profile["drivetrain"], "FWD")
        self.assertEqual(profile["fuel_type"], "gasoline")

    def test_autofill_vehicle_data_handles_bad_image_payload_without_crash(self) -> None:
        autofilled = self.service.autofill_vehicle_data(
            {
                "raw_text": "Toyota Camry 2019, мотор 2.5",
                "image_base64": "%%%broken-base64%%%",
                "image_filename": "vehicle.png",
                "image_mime_type": "image/png",
            }
        )

        self.assertEqual(autofilled["image_parse_status"], "image_decode_error")
        self.assertTrue(autofilled["warnings"])
        self.assertEqual(autofilled["vehicle_profile"]["make_display"], "Toyota")
        self.assertEqual(autofilled["vehicle_profile"]["model_display"], "Camry")

    def test_autofill_vehicle_data_uses_card_fields_when_raw_text_is_empty(self) -> None:
        with patch.object(
            self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None
        ):
            autofilled = self.service.autofill_vehicle_data(
                {
                    "vehicle": "Suzuki Swift 2014",
                    "title": "Suzuki Swift 2014 / подбор запчастей",
                    "description": "VIN JSAZC72S001234567\nДвигатель: K12B\nКоробка: Aisin\nПередний привод.",
                    "existing_profile": {},
                }
            )

        profile = autofilled["vehicle_profile"]
        self.assertEqual(profile["make_display"], "Suzuki")
        self.assertEqual(profile["model_display"], "Swift")
        self.assertEqual(profile["production_year"], 2014)
        self.assertEqual(profile["vin"], "JSAZC72S001234567")
        self.assertEqual(profile["engine_model"], "K12B")
        self.assertEqual(profile["gearbox_model"], "Aisin")
        self.assertEqual(profile["drivetrain"], "FWD")

    def test_autofill_vehicle_data_skips_vin_decode_when_text_already_identifies_vehicle(
        self,
    ) -> None:
        with patch.object(
            self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None
        ) as enrich:
            autofilled = self.service.autofill_vehicle_data(
                {
                    "raw_text": "Mazda CX 5 2019 VIN JM3KF123456789012",
                }
            )

        enrich.assert_not_called()
        profile = autofilled["vehicle_profile"]
        self.assertEqual(profile["make_display"], "Mazda")
        self.assertEqual(profile["model_display"], "CX-5")
        self.assertEqual(profile["production_year"], 2019)
        self.assertEqual(profile["vin"], "JM3KF123456789012")

    def test_autofill_vehicle_data_uses_vin_decode_when_identity_fields_are_missing(self) -> None:
        with patch.object(
            self.service._vehicle_profiles,
            "_enrich_from_vin_decode",
            return_value=VehicleProfile.from_dict(
                {
                    "make_display": "Mazda",
                    "model_display": "CX-5",
                    "production_year": 2019,
                    "vin": "JM3KF123456789012",
                    "source_summary": "VIN decoded",
                    "source_confidence": 0.91,
                    "autofilled_fields": [
                        "make_display",
                        "model_display",
                        "production_year",
                        "vin",
                    ],
                }
            ),
        ) as enrich:
            autofilled = self.service.autofill_vehicle_data(
                {
                    "raw_text": "VIN JM3KF123456789012",
                }
            )

        enrich.assert_called_once_with("JM3KF123456789012")
        profile = autofilled["vehicle_profile"]
        self.assertEqual(profile["make_display"], "Mazda")
        self.assertEqual(profile["model_display"], "CX-5")
        self.assertEqual(profile["production_year"], 2019)

    def test_bulk_move_cards_moves_many_cards_and_reports_partial_failures(self) -> None:
        created_column = self.service.create_column({"label": "MCP TEST COLUMN"})
        target_column = created_column["column"]["id"]

        first = self.service.create_card(
            {"vehicle": "CAR-1", "title": "Bulk one", "column": "inbox", "deadline": {"hours": 3}}
        )
        second = self.service.create_card(
            {
                "vehicle": "CAR-2",
                "title": "Bulk two",
                "column": "in_progress",
                "deadline": {"hours": 3},
            }
        )
        already_there = self.service.create_card(
            {
                "vehicle": "CAR-3",
                "title": "Bulk three",
                "column": target_column,
                "deadline": {"hours": 3},
            }
        )
        archived = self.service.create_card(
            {
                "vehicle": "CAR-4",
                "title": "Bulk archived",
                "column": "done",
                "deadline": {"hours": 3},
            }
        )
        self.service.archive_card({"card_id": archived["card"]["id"]})

        moved = self.service.bulk_move_cards(
            {
                "card_ids": [
                    first["card"]["id"],
                    second["card"]["id"],
                    already_there["card"]["id"],
                    archived["card"]["id"],
                    "missing-card",
                    first["card"]["id"],
                ],
                "column": target_column,
                "actor_name": "MCP TEST",
                "source": "mcp",
            }
        )

        self.assertEqual(moved["meta"]["requested"], 5)
        self.assertEqual(moved["meta"]["moved"], 2)
        self.assertEqual(moved["meta"]["unchanged"], 1)
        self.assertEqual(moved["meta"]["errors"], 2)
        self.assertTrue(moved["meta"]["partial_failure"])
        self.assertTrue(all(card["column"] == target_column for card in moved["moved_cards"]))
        self.assertTrue(
            any(card["id"] == already_there["card"]["id"] for card in moved["unchanged_cards"])
        )
        self.assertTrue(
            any(
                item["card_id"] == archived["card"]["id"] and item["code"] == "archived_card"
                for item in moved["errors"]
            )
        )
        self.assertTrue(
            any(
                item["card_id"] == "missing-card" and item["code"] == "not_found"
                for item in moved["errors"]
            )
        )

        first_after = self.service.get_card({"card_id": first["card"]["id"]})["card"]
        second_after = self.service.get_card({"card_id": second["card"]["id"]})["card"]
        self.assertEqual(first_after["column"], target_column)
        self.assertEqual(second_after["column"], target_column)

        first_log = self.service.get_card_log({"card_id": first["card"]["id"]})["events"]
        self.assertTrue(any(event["action"] == "card_moved" for event in first_log))

    def test_bulk_move_cards_handles_large_batches(self) -> None:
        created_column = self.service.create_column({"label": "BATCH TARGET"})
        target_column = created_column["column"]["id"]
        source_columns = ["inbox", "in_progress", "done"]

        card_ids: list[str] = []
        for index in range(24):
            created = self.service.create_card(
                {
                    "vehicle": f"CAR-{index}",
                    "title": f"Batch {index}",
                    "column": source_columns[index % len(source_columns)],
                    "deadline": {"hours": 2},
                }
            )
            card_ids.append(created["card"]["id"])

        moved = self.service.bulk_move_cards(
            {
                "card_ids": card_ids,
                "column": target_column,
                "actor_name": "MCP TEST",
                "source": "mcp",
            }
        )

        self.assertEqual(moved["meta"]["requested"], 24)
        self.assertEqual(moved["meta"]["moved"], 24)
        self.assertEqual(moved["meta"]["errors"], 0)
        self.assertFalse(moved["meta"]["partial_failure"])

        snapshot_cards = self.service.get_cards()["cards"]
        moved_ids = {card["id"] for card in moved["moved_cards"]}
        self.assertEqual(moved_ids, set(card_ids))
        self.assertTrue(
            all(
                card["column"] == target_column
                for card in snapshot_cards
                if card["id"] in moved_ids
            )
        )

    def test_board_settings_are_exported_in_snapshot(self) -> None:
        snapshot = self.service.get_board_snapshot()

        self.assertIn("settings", snapshot)
        self.assertEqual(snapshot["settings"]["board_scale"], 1.0)

    def test_board_scale_updates_are_saved_and_audited(self) -> None:
        updated = self.service.update_board_settings(
            {"board_scale": 1.25, "actor_name": "ОПЕРАТОР"}
        )
        snapshot = self.service.get_board_snapshot()
        events = self.store.read_bundle()["events"]

        self.assertEqual(updated["settings"]["board_scale"], 1.25)
        self.assertEqual(updated["meta"]["previous_board_scale"], 1.0)
        self.assertTrue(updated["meta"]["changed"])
        self.assertEqual(snapshot["settings"]["board_scale"], 1.25)
        self.assertTrue(any(event.action == "board_scale_changed" for event in events))

    def test_board_control_settings_are_saved_and_audited(self) -> None:
        updated = self.service.update_board_settings(
            {
                "actor_name": "ОПЕРАТОР",
                "ai_board_control": {
                    "enabled": True,
                    "interval_minutes": 30,
                    "cooldown_minutes": 90,
                },
            }
        )
        snapshot = self.service.get_board_snapshot()
        events = self.store.read_bundle()["events"]

        self.assertEqual(
            updated["settings"]["ai_board_control"],
            {"enabled": True, "interval_minutes": 30, "cooldown_minutes": 90},
        )
        self.assertEqual(
            updated["meta"]["previous_ai_board_control"],
            {"enabled": False, "interval_minutes": 20, "cooldown_minutes": 60},
        )
        self.assertTrue(updated["meta"]["board_control_changed"])
        self.assertEqual(
            snapshot["settings"]["ai_board_control"],
            {"enabled": True, "interval_minutes": 30, "cooldown_minutes": 90},
        )
        self.assertTrue(any(event.action == "board_ai_control_changed" for event in events))

    def test_rejects_invalid_board_scale(self) -> None:
        with self.assertRaises(ServiceError) as invalid_scale:
            self.service.update_board_settings({"board_scale": 2.0})
        self.assertEqual(invalid_scale.exception.code, "validation_error")

    def test_sticky_notes_are_created_moved_updated_and_deleted(self) -> None:
        created = self.service.create_sticky(
            {
                "text": "Проверить сход-развал",
                "x": 120,
                "y": 80,
                "deadline": {"hours": 4},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        sticky_id = created["sticky"]["id"]
        self.assertTrue(created["sticky"]["short_id"].startswith("S-"))

        snapshot = self.service.get_board_snapshot()
        self.assertIn("stickies", snapshot)
        self.assertTrue(any(item["id"] == sticky_id for item in snapshot["stickies"]))
        self.assertGreater(snapshot["meta"]["stickies_total"], 0)

        moved = self.service.move_sticky(
            {"sticky_id": sticky_id, "x": 240, "y": 160, "actor_name": "МАСТЕР", "source": "api"}
        )
        self.assertEqual(moved["sticky"]["x"], 240)
        self.assertEqual(moved["sticky"]["y"], 160)

        updated = self.service.update_sticky(
            {
                "sticky_id": sticky_id,
                "text": "Проверить сход-развал после замены рулевых тяг",
                "deadline": {"hours": 6},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        self.assertIn("после замены", updated["sticky"]["text"])

        deleted = self.service.delete_sticky(
            {"sticky_id": sticky_id, "actor_name": "МАСТЕР", "source": "api"}
        )
        self.assertTrue(deleted["deleted"])
        self.assertFalse(any(item["id"] == sticky_id for item in deleted["stickies"]))

        events = self.store.read_bundle()["events"]
        self.assertTrue(any(event.action == "sticky_created" for event in events))
        self.assertTrue(any(event.action == "sticky_moved" for event in events))
        self.assertTrue(any(event.action == "sticky_text_changed" for event in events))
        self.assertTrue(any(event.action == "sticky_deleted" for event in events))

    def test_sticky_notes_accept_total_seconds_and_short_id_lookup(self) -> None:
        created = self.service.create_sticky(
            {
                "text": "Перезвонить клиенту",
                "deadline": {"total_seconds": 3600},
                "x": 10,
                "y": 20,
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        sticky_id = created["sticky"]["id"]
        sticky_short_id = created["sticky"]["short_id"]
        self.assertGreater(created["sticky"]["remaining_seconds"], 0)

        updated = self.service.update_sticky(
            {
                "sticky_id": sticky_short_id,
                "text": "Перезвонить клиенту после согласования",
                "deadline": {"minutes": 45},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        self.assertEqual(updated["sticky"]["id"], sticky_id)
        self.assertIn("после согласования", updated["sticky"]["text"])

        deleted = self.service.delete_sticky(
            {"sticky_id": sticky_short_id, "actor_name": "МАСТЕР", "source": "api"}
        )
        self.assertTrue(deleted["deleted"])
        self.assertFalse(any(item["id"] == sticky_id for item in deleted["stickies"]))

    def test_gpt_wall_returns_full_context_layer(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "ПЛАВАЕТ ХОЛОСТОЙ ХОД",
                "description": "Проверить дроссель и датчик холостого хода",
                "tags": ["СРОЧНО"],
                "deadline": {"hours": 6},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        card_short_id = created["card"]["short_id"]
        self.service.move_card(
            {"card_id": card_id, "column": "in_progress", "actor_name": "МАСТЕР", "source": "api"}
        )

        self.service.archive_card({"card_id": card_id, "actor_name": "MASTER", "source": "api"})
        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 50})
        searched = self.service.search_cards(
            {"query": card_short_id, "limit": 5, "include_archived": True}
        )

        self.assertIn("text", wall)
        self.assertTrue(wall["text"].startswith("# AutoStop CRM Board Content"))
        self.assertEqual(wall["meta"]["text_format"], "markdown")
        self.assertEqual(wall["meta"]["section_kind"], "gpt_wall")
        self.assertEqual(wall["meta"]["event_order"], "newest_first")
        self.assertTrue(wall["meta"]["include_archived"])
        self.assertFalse(wall["meta"]["cards_compact"])
        self.assertIn("board_context", wall)
        self.assertIn("sections", wall)
        self.assertIn("board_content", wall["sections"])
        self.assertIn("event_log", wall["sections"])
        self.assertTrue(
            wall["sections"]["board_content"]["text"].startswith("# AutoStop CRM Board Content")
        )
        self.assertTrue(
            wall["sections"]["event_log"]["text"].startswith("# AutoStop CRM Event Log")
        )
        self.assertEqual(wall["sections"]["board_content"]["meta"]["text_format"], "markdown")
        self.assertEqual(wall["sections"]["board_content"]["meta"]["section_kind"], "board_content")
        self.assertEqual(wall["sections"]["event_log"]["meta"]["text_format"], "markdown")
        self.assertEqual(wall["sections"]["event_log"]["meta"]["section_kind"], "event_log")
        self.assertEqual(wall["sections"]["event_log"]["meta"]["event_order"], "newest_first")
        self.assertIn(card_short_id, wall["text"])
        self.assertTrue(any(card["id"] == card_id for card in wall["cards"]))
        wall_card = next(card for card in wall["cards"] if card["id"] == card_id)
        self.assertIn("vehicle_profile_compact", wall_card)
        self.assertFalse(wall_card["vehicle_profile_compact"]["has_any_data"])
        self.assertTrue(any(event["card_id"] == card_id for event in wall["events"]))
        self.assertIn(card_short_id, wall["sections"]["board_content"]["text"])
        self.assertTrue(
            any(event["card_id"] == card_id for event in wall["sections"]["event_log"]["events"])
        )
        self.assertEqual(
            wall["board_context"]["context"]["board_scope"], "single_local_board_instance"
        )
        self.assertEqual(
            wall["meta"]["active_cards"], wall["board_context"]["context"]["active_cards_total"]
        )
        self.assertEqual(
            wall["meta"]["archived_cards"], wall["board_context"]["context"]["archived_cards_total"]
        )
        self.assertEqual(searched["cards"][0]["id"], card_id)
        self.assertIn("short_id: " + card_short_id, wall["text"])
        self.assertIn(card_short_id, wall["sections"]["event_log"]["text"])
        self.assertIn("## Cards By Column", wall["sections"]["board_content"]["text"])
        self.assertIn("## Archived Cards", wall["sections"]["board_content"]["text"])
        self.assertIn("card_id: " + card_id, wall["sections"]["board_content"]["text"])
        self.assertIn("status:", wall["sections"]["board_content"]["text"])
        self.assertIn("KIA RIO", wall["text"])
        self.assertIn("ПЛАВАЕТ ХОЛОСТОЙ ХОД", wall["text"])
        self.assertIn("МАСТЕР", wall["text"])

    def test_gpt_wall_can_return_compact_cards_for_agent_reads(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "AUDI A6",
                "title": "AGENT COMPACT",
                "description": "Проверка компактного режима стены",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "make_display": "Audi",
                    "model_display": "A6",
                    "customer_name": "Тестовый клиент",
                },
            }
        )
        card_id = created["card"]["id"]

        wall = self.service.get_gpt_wall(
            {"include_archived": True, "event_limit": 20, "compact": True}
        )
        wall_card = next(card for card in wall["cards"] if card["id"] == card_id)

        self.assertTrue(wall["meta"]["cards_compact"])
        self.assertEqual(wall["meta"]["event_limit"], 20)
        self.assertNotIn("vehicle_profile", wall_card)
        self.assertIn("vehicle_profile_compact", wall_card)

    def test_gpt_wall_defaults_to_markdown_and_archived_cards(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "ARCHIVE DEFAULT",
                "title": "DEFAULT WALL INCLUDE",
                "description": "Архивная карточка должна входить в машинный снимок",
                "deadline": {"hours": 1},
                "actor_name": "MASTER",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        card_short_id = created["card"]["short_id"]
        self.service.archive_card({"card_id": card_id, "actor_name": "MASTER", "source": "api"})

        wall = self.service.get_gpt_wall({})

        self.assertEqual(wall["meta"]["text_format"], "markdown")
        self.assertEqual(wall["meta"]["event_limit"], 100)
        self.assertTrue(wall["meta"]["include_archived"])
        self.assertEqual(wall["sections"]["event_log"]["meta"]["event_limit"], 100)
        self.assertTrue(any(card["id"] == card_id for card in wall["cards"]))
        self.assertIn("## Archived Cards", wall["sections"]["board_content"]["text"])
        self.assertIn("card_id: " + card_id, wall["sections"]["board_content"]["text"])
        self.assertIn("short_id: " + card_short_id, wall["sections"]["board_content"]["text"])

    def test_gpt_wall_event_log_uses_structured_lines(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST CAR",
                "title": "LOG FORMAT",
                "description": "Проверка читаемости журнала",
                "deadline": {"hours": 1},
                "actor_name": "MASTER",
                "source": "api",
            }
        )
        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 20})
        event_text = wall["sections"]["event_log"]["text"]

        self.assertTrue(event_text.startswith("# AutoStop CRM Event Log"))
        self.assertIn("## Metadata", event_text)
        self.assertIn("text_format: markdown", event_text)
        self.assertIn("section_kind: event_log", event_text)
        self.assertIn("event_order: newest_first", event_text)
        self.assertIn("## Events", event_text)
        self.assertIn("### Event 1", event_text)
        self.assertIn("time:", event_text)
        self.assertIn("actor:", event_text)
        self.assertIn("source:", event_text)
        self.assertIn("action:", event_text)
        self.assertIn("message:", event_text)
        self.assertIn(created["card"]["short_id"], event_text)

    def test_gpt_wall_repairs_mojibake_event_text(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST CAR",
                "title": "ENCODING CHECK",
                "description": "Проверка repair для event log",
                "deadline": {"hours": 1},
                "actor_name": "MASTER",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        broken_message = "CHATGPT_AUDIT удалил столбец".encode("utf-8").decode("cp1251")
        broken_detail = "Диагностика".encode("utf-8").decode("cp1251")
        bundle = self.store.read_bundle()
        bundle["events"].append(
            AuditEvent(
                id="encoding-event",
                timestamp=utc_now().isoformat(),
                actor_name="CHATGPT_AUDIT",
                source="mcp",
                action="column_deleted",
                message=broken_message,
                details={"after": broken_detail},
                card_id=card_id,
            )
        )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 20})
        repaired_event = next(event for event in wall["events"] if event["id"] == "encoding-event")

        self.assertEqual(repaired_event["message"], "CHATGPT_AUDIT удалил столбец")
        self.assertIn("Диагностика", repaired_event["details_text"])
        self.assertIn("CHATGPT_AUDIT удалил столбец", wall["sections"]["event_log"]["text"])

    def test_gpt_wall_includes_customer_contact_fields(self) -> None:
        self.service.create_card(
            {
                "vehicle": "AUDI A4",
                "title": "КЛИЕНТ НА СВЯЗИ",
                "description": "Проверить контакты в стене GPT",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "make_display": "Audi",
                    "model_display": "A4",
                    "customer_phone": "+7 900 123-45-67",
                    "customer_name": "Иван Иванов",
                },
            }
        )

        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 20})

        self.assertIn('"customer_phone":"+7 900 123-45-67"', wall["text"])
        self.assertIn('"customer_name":"Иван Иванов"', wall["text"])

    def test_gpt_wall_text_is_limited_to_3000_lines(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST CAR",
                "title": "Много событий",
                "description": "Проверка усечения стены",
                "deadline": {"hours": 4},
            }
        )
        card_id = created["card"]["id"]

        bundle = self.store.read_bundle()
        for index in range(3600):
            bundle["events"].append(
                AuditEvent(
                    id=f"event-{index}",
                    timestamp=f"2026-04-02T12:00:00+00:00#{index:04d}",
                    actor_name="ТЕСТ",
                    source="api",
                    action="bulk_log",
                    message=f"Событие {index}",
                    details={"step": index},
                    card_id=card_id,
                )
            )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 5000})

        self.assertLessEqual(len(wall["text"].splitlines()), 3000)
        self.assertIn("[WALL TRUNCATED]", wall["text"])
        self.assertIn("### Event 1", wall["text"])
        self.assertIn("time:", wall["text"])
        self.assertIn("actor:", wall["text"])
        self.assertIn("action:", wall["text"])

    def test_board_context_describes_current_board_only(self) -> None:
        created_column = self.service.create_column({"label": "КУЗОВНОЙ ЦЕХ"})
        column_id = created_column["column"]["id"]
        self.service.create_card(
            {
                "vehicle": "VW POLO",
                "title": "ПОДТЯНУТЬ ГЕОМЕТРИЮ ДВЕРИ",
                "column": column_id,
                "deadline": {"hours": 6},
            }
        )
        self.service.create_sticky(
            {
                "text": "Согласовать покраску с клиентом",
                "deadline": {"hours": 2},
                "x": 80,
                "y": 120,
            }
        )

        context = self.service.get_board_context()

        self.assertEqual(context["context"]["product_name"], "AutoStop CRM")
        self.assertEqual(context["context"]["board_name"], "Current AutoStop CRM Board")
        self.assertEqual(context["context"]["board_scope"], "single_local_board_instance")
        self.assertIn("Do not use it for Trello, YouGile", context["context"]["scope_rule"])
        self.assertEqual(context["context"]["vehicle_profile_autofill_mode"], "card_content_first")
        self.assertIn("vin", context["context"]["vehicle_profile_compact_fields"])
        self.assertGreaterEqual(context["context"]["columns_total"], 1)
        self.assertEqual(context["context"]["stickies_total"], 1)
        self.assertTrue(any(column["id"] == column_id for column in context["context"]["columns"]))
        body_column = next(
            column for column in context["context"]["columns"] if column["id"] == column_id
        )
        self.assertEqual(body_column["active_cards"], 1)
        self.assertEqual(body_column["archived_cards"], 0)
        self.assertIn("[BOARD CONTEXT]", context["text"])
        self.assertIn("allowed_columns:", context["text"])
        self.assertIn("vehicle_profile_compact_fields:", context["text"])

    def test_vehicle_profile_preserves_customer_contact_fields(self) -> None:
        profile = VehicleProfile.from_dict(
            {
                "make_display": "Audi",
                "model_display": "A4",
                "mileage": 185000,
                "customer_phone": "+7 900 123-45-67",
                "customer_name": "Иван Иванов",
            }
        )

        payload = profile.to_dict()
        stored = profile.to_storage_dict()

        self.assertEqual(payload["mileage"], 185000)
        self.assertEqual(payload["customer_phone"], "+7 900 123-45-67")
        self.assertEqual(payload["customer_name"], "Иван Иванов")
        self.assertEqual(stored["mileage"], 185000)
        self.assertEqual(stored["customer_phone"], "+7 900 123-45-67")
        self.assertEqual(stored["customer_name"], "Иван Иванов")
        self.assertTrue(payload["has_any_data"])


if __name__ == "__main__":
    unittest.main()
