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
from minimal_kanban.models import (
    CARD_DESCRIPTION_LIMIT,
    AuditEvent,
    Card,
    CashBox,
    CashTransaction,
    utc_now,
)
from minimal_kanban.agent.config import get_agent_name
from minimal_kanban.repair_order import RepairOrder
from minimal_kanban.services.card_service import CardService, ServiceError
from minimal_kanban.services.finance_read_core import FinanceReadCore
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

    def test_create_client_reuses_exact_duplicate_without_explicit_id(self) -> None:
        first = self.service.create_client(
            {
                "display_name": "Дубль клиента",
                "phone": "8 953 586-86-35",
                "vehicles": [
                    {
                        "vehicle": "Kia Spectra",
                        "vin": "XWKFB227370040491",
                        "license_plate": "Т896ТЕ124",
                        "year": "2007",
                    }
                ],
            }
        )["client"]
        duplicate = self.service.create_client(
            {
                "display_name": "Дубль клиента",
                "phone": "+7 953 586-86-35",
                "vehicles": [
                    {
                        "vehicle": "Kia Spectra",
                        "vin": "XWKFB227370040491",
                        "license_plate": "т896те124",
                        "year": "2007",
                    }
                ],
            }
        )

        self.assertFalse(duplicate["meta"]["created"])
        self.assertTrue(duplicate["meta"]["duplicate"])
        self.assertEqual(duplicate["client"]["id"], first["id"])
        self.assertEqual(len(self.service.list_clients({"limit": 10})["clients"]), 1)

    def test_create_client_allows_same_phone_with_different_name(self) -> None:
        first = self.service.create_client(
            {"display_name": "Первый клиент", "phone": "+7 953 586-86-35"}
        )["client"]
        second = self.service.create_client(
            {"display_name": "Второй клиент", "phone": "8 953 586-86-35"}
        )["client"]

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.service.list_clients({"limit": 10})["clients"]), 2)

    def test_create_client_keeps_new_vehicle_when_existing_has_none(self) -> None:
        first = self.service.create_client(
            {"display_name": "Клиент с новым авто позже", "phone": "+7 953 586-86-35"}
        )["client"]
        second = self.service.create_client(
            {
                "display_name": "Клиент с новым авто позже",
                "phone": "8 953 586-86-35",
                "vehicles": [{"vehicle": "Toyota Camry", "license_plate": "А123ВС124"}],
            }
        )

        self.assertTrue(second["meta"]["created"])
        self.assertNotEqual(first["id"], second["client"]["id"])
        self.assertEqual(len(self.service.list_clients({"limit": 10})["clients"]), 2)

    def test_list_clients_batches_related_cards_when_stats_requested(self) -> None:
        clients = [
            self.service.create_client(
                {
                    "display_name": f"Клиент пакетной статистики {index}",
                    "phone": f"+7 900 000-00-0{index}",
                }
            )["client"]
            for index in range(1, 4)
        ]
        for index, client in enumerate(clients, start=1):
            self.service.create_card(
                {
                    "vehicle": f"Toyota Test {index}",
                    "title": f"Работа {index}",
                    "description": "Проверка пакетной клиентской статистики",
                    "deadline": {"hours": 1},
                    "client_id": client["id"],
                    "vehicle_profile": {"vin": f"TESTVIN000000000{index}"},
                }
            )

        with patch.object(
            self.service,
            "_client_related_cards",
            side_effect=AssertionError("list_clients must use batched related-card lookup"),
        ):
            listed = self.service.list_clients({"limit": 10, "include_stats": True})

        listed_by_id = {client["id"]: client for client in listed["clients"]}
        self.assertEqual(listed["meta"]["returned"], 3)
        for client in clients:
            row = listed_by_id[client["id"]]
            self.assertEqual(row["stats"]["cards_total"], 1)
            self.assertEqual(row["stats"]["vehicles_total"], 1)
            self.assertEqual(
                row["vehicles_preview"][0]["vin"],
                "TESTVIN000000000" + client["phone"][-1],
            )

    def test_client_profile_supports_up_to_three_phones(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Клиент с несколькими телефонами",
                "phone": "+7 900 000-00-01",
                "phones": [
                    "+7 900 000-00-01",
                    "8 901 000-00-02",
                    "+7 902 000-00-03",
                    "+7 903 000-00-04",
                ],
            }
        )["client"]

        self.assertEqual(client["phone"], "+7 900 000-00-01")
        self.assertEqual(
            client["phones"],
            ["+7 900 000-00-01", "8 901 000-00-02", "+7 902 000-00-03"],
        )

        search = self.service.search_clients({"query": "79020000003", "limit": 5})
        self.assertEqual(search["clients"][0]["id"], client["id"])

    def test_client_profile_deduplicates_russian_phone_formats(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Клиент с дублем телефона",
                "phone": "89535868635",
                "phones": ["+7 953 586-86-35", "+7 913 000-00-01"],
            }
        )["client"]

        self.assertEqual(client["phone"], "89535868635")
        self.assertEqual(client["phones"], ["89535868635", "+7 913 000-00-01"])

    def test_card_vehicle_profile_keeps_three_customer_phones(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Клиент для карточки",
                "phones": [
                    "+7 900 111-11-11",
                    "+7 901 222-22-22",
                    "+7 902 333-33-33",
                ],
            }
        )["client"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota",
                "title": "Осмотр",
                "deadline": {"hours": 1},
                "vehicle_profile": {
                    "customer_name": "Клиент для карточки",
                    "customer_phones": [
                        "+7 999 111-11-11",
                        "+7 999 222-22-22",
                        "+7 999 333-33-33",
                        "+7 999 444-44-44",
                    ],
                },
            }
        )["card"]

        self.assertEqual(created["vehicle_profile"]["customer_phone"], "+7 999 111-11-11")
        self.assertEqual(
            created["vehicle_profile"]["customer_phones"],
            ["+7 999 111-11-11", "+7 999 222-22-22", "+7 999 333-33-33"],
        )

        empty_card = self.service.create_card(
            {
                "vehicle": "Toyota",
                "title": "Пустой телефон",
                "deadline": {"hours": 1},
            }
        )["card"]
        linked = self.service.link_card_to_client(
            {"card_id": empty_card["id"], "client_id": client["id"], "sync_fields": True}
        )["card"]
        self.assertEqual(linked["vehicle_profile"]["customer_phone"], "+7 900 111-11-11")
        self.assertEqual(
            linked["vehicle_profile"]["customer_phones"],
            ["+7 900 111-11-11", "+7 901 222-22-22", "+7 902 333-33-33"],
        )

    def test_card_vehicle_profile_deduplicates_russian_customer_phone_formats(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota",
                "title": "Дубли телефонов",
                "deadline": {"hours": 1},
                "vehicle_profile": {
                    "customer_phone": "8 913 222-33-44",
                    "customer_phones": [
                        "+7 913 222-33-44",
                        "+7 914 222-33-44",
                    ],
                },
            }
        )["card"]

        self.assertEqual(created["vehicle_profile"]["customer_phone"], "8 913 222-33-44")
        self.assertEqual(
            created["vehicle_profile"]["customer_phones"],
            ["8 913 222-33-44", "+7 914 222-33-44"],
        )

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
                self.assertEqual(
                    search["clients"][0]["vehicles_preview"][0]["vehicle"], "Toyota Camry"
                )
                self.assertEqual(
                    search["clients"][0]["vehicles_preview"][0]["vin"], "JTDBE32K620654321"
                )

    def test_client_search_ignores_placeholder_vehicle_vins(self) -> None:
        placeholder = self.service.create_client(
            {
                "display_name": "Плейсхолдер VIN",
                "vehicles": [
                    {
                        "vehicle": "Toyota Placeholder",
                        "vin": "1111111111111",
                    },
                    {
                        "vehicle": "Short Placeholder",
                        "vin": "-",
                    },
                ],
            }
        )["client"]
        valid = self.service.create_client(
            {
                "display_name": "Нормальный VIN",
                "vehicles": [
                    {
                        "vehicle": "Toyota Probox",
                        "vin": "NCP165-0033993",
                    }
                ],
            }
        )["client"]

        by_placeholder = self.service.search_clients({"query": "1111111111111", "limit": 5})
        by_short_placeholder = self.service.search_clients({"query": "-", "limit": 5})
        by_valid = self.service.search_clients({"query": "NCP165-0033993", "limit": 5})

        self.assertFalse(
            any(client["id"] == placeholder["id"] for client in by_placeholder["clients"])
        )
        self.assertFalse(
            any(client["id"] == placeholder["id"] for client in by_short_placeholder["clients"])
        )
        self.assertTrue(by_valid["clients"])
        self.assertEqual(by_valid["clients"][0]["id"], valid["id"])

    def test_client_search_ignores_placeholder_vins_from_related_cards(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Клиент с мусорным VIN в истории",
                "phone": "+7 913 111-22-33",
            }
        )["client"]
        self.service.create_card(
            {
                "vehicle": "Toyota Corolla",
                "title": "Связанная карточка с плейсхолдером",
                "deadline": {"hours": 1},
                "client_id": client["id"],
                "vehicle_profile": {
                    "customer_name": "Клиент с мусорным VIN в истории",
                    "customer_phone": "+7 913 111-22-33",
                    "vin": "1111111111111",
                },
                "repair_order": {
                    "client": "Клиент с мусорным VIN в истории",
                    "phone": "+7 913 111-22-33",
                    "vin": "ABC",
                },
            }
        )

        by_repeated_placeholder = self.service.search_clients(
            {"query": "1111111111111", "limit": 5}
        )
        by_short_placeholder = self.service.search_clients({"query": "ABC", "limit": 5})
        by_phone = self.service.search_clients({"query": "89131112233", "limit": 5})

        self.assertFalse(
            any(
                client_result["id"] == client["id"]
                for client_result in by_repeated_placeholder["clients"]
            )
        )
        self.assertFalse(
            any(
                client_result["id"] == client["id"]
                for client_result in by_short_placeholder["clients"]
            )
        )
        self.assertTrue(by_phone["clients"])
        self.assertEqual(by_phone["clients"][0]["id"], client["id"])

    def test_client_search_uses_secondary_card_customer_phone_for_related_vehicle(self) -> None:
        client = self.service.create_client(
            {
                "last_name": "Федоров",
                "first_name": "Игорь",
                "phone": "+7 901 222-33-44",
            }
        )["client"]
        card = self.service.create_card(
            {
                "vehicle": "Honda Fit",
                "title": "Плановый осмотр",
                "description": "В карточке основной телефон другой",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "customer_name": "Другой контакт",
                    "customer_phone": "+7 900 000-00-01",
                    "customer_phones": ["+7 900 000-00-01", "8 901 222-33-44"],
                    "vin": "GD123456789",
                    "registration_plate": "В222ВВ124",
                },
            }
        )["card"]

        by_plate = self.service.search_clients({"query": "В222ВВ124", "limit": 5})
        self.assertTrue(by_plate["clients"])
        self.assertEqual(by_plate["clients"][0]["id"], client["id"])
        self.assertEqual(by_plate["clients"][0]["vehicles_preview"][0]["vehicle"], "Honda Fit")

        suggestions = self.service.suggest_clients_for_card({"card_id": card["id"], "limit": 5})
        self.assertTrue(suggestions["clients"])
        self.assertEqual(suggestions["clients"][0]["id"], client["id"])

    def test_client_profile_can_store_imported_vehicles(self) -> None:
        client = self.service.create_client(
            {
                "client_type": "ooo",
                "short_name": "ГрандСервис",
                "legal_name": 'ООО "ГрандСервис"',
                "phone": "+7 923 339-78-84",
                "inn": "2465257740",
                "vehicles": [
                    {
                        "brand": "Toyota",
                        "model": "Probox",
                        "vin": "ncp165-0033993",
                        "year": 2017,
                    }
                ],
            }
        )["client"]

        profile = self.service.get_client({"client_id": client["id"]})
        self.assertEqual(profile["vehicles"][0]["vehicle"], "Toyota Probox")
        self.assertEqual(profile["vehicles"][0]["vin"], "NCP165-0033993")
        self.assertEqual(profile["vehicles"][0]["year"], "2017")

        search = self.service.search_clients({"query": "Probox", "limit": 5})
        self.assertEqual(search["clients"][0]["id"], client["id"])
        self.assertEqual(search["clients"][0]["vehicles_preview"][0]["vehicle"], "Toyota Probox")
        self.assertTrue(search["clients"][0]["vehicles_preview"][0]["id"])

    def test_client_search_reuses_related_cards_for_selected_results(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Оптимизация поиска",
                "vehicles": [
                    {
                        "vehicle": "Toyota Prado",
                        "vin": "JTEBU3FJX05027767",
                        "license_plate": "О777ОО124",
                    }
                ],
            }
        )["client"]
        self.service.create_card(
            {
                "title": "Связанная история",
                "vehicle": "Toyota Prado",
                "vehicle_profile": {
                    "customer_name": "Оптимизация поиска",
                    "vin": "JTEBU3FJX05027767",
                    "registration_plate": "О777ОО124",
                },
                "deadline": {"hours": 1},
            }
        )

        with patch.object(
            self.service,
            "_client_related_cards",
            wraps=self.service._client_related_cards,
        ) as related_cards:
            search = self.service.search_clients({"query": "Toyota Prado", "limit": 5})

        self.assertEqual(search["clients"][0]["id"], client["id"])
        self.assertEqual(related_cards.call_count, 0)

    def test_client_search_builds_related_vehicle_index_once_on_miss(self) -> None:
        self.service.create_client({"display_name": "Клиент без совпадения"})["client"]
        self.service.create_card(
            {
                "title": "История без совпадения",
                "vehicle": "Toyota Corolla",
                "description": "Проверка промаха поиска",
                "deadline": {"hours": 1},
                "vehicle_profile": {"vin": "NOMATCH0000000001"},
            }
        )

        with patch.object(
            self.service,
            "_client_related_vehicle_fields_index",
            wraps=self.service._client_related_vehicle_fields_index,
        ) as related_index:
            search = self.service.search_clients({"query": "ZZZ-UNKNOWN-999", "limit": 5})

        self.assertEqual(search["clients"], [])
        self.assertEqual(related_index.call_count, 1)

    def test_client_search_reuses_related_vehicle_index_between_queries(self) -> None:
        client = self.service.create_client({"display_name": "Клиент с кэшем поиска"})["client"]
        self.service.create_card(
            {
                "title": "История для кэша поиска",
                "vehicle": "Nissan Note",
                "description": "Проверка повторного поиска",
                "deadline": {"hours": 1},
                "client_id": client["id"],
                "vehicle_profile": {
                    "vin": "SJNFAAE11U0123456",
                    "registration_plate": "К456КК124",
                },
            }
        )

        with patch.object(
            self.service,
            "_client_related_vehicle_fields_index",
            wraps=self.service._client_related_vehicle_fields_index,
        ) as related_index:
            first = self.service.search_clients({"query": "SJNFAAE11U0123456", "limit": 5})
            second = self.service.search_clients({"query": "К456КК124", "limit": 5})

        self.assertEqual(first["clients"][0]["id"], client["id"])
        self.assertEqual(second["clients"][0]["id"], client["id"])
        self.assertEqual(related_index.call_count, 1)

    def test_suggest_clients_for_card_uses_related_card_vehicle_fields(self) -> None:
        client = self.service.create_client({"display_name": "Клиент из истории VIN"})["client"]
        self.service.create_card(
            {
                "title": "Историческая привязка",
                "vehicle": "Subaru Forester",
                "description": "VIN есть только в связанной карточке",
                "deadline": {"hours": 1},
                "client_id": client["id"],
                "vehicle_profile": {
                    "vin": "JF1SJ5LC5DG012345",
                    "registration_plate": "С123СС124",
                },
            }
        )
        candidate = self.service.create_card(
            {
                "title": "Новая карточка по VIN",
                "vehicle": "Subaru Forester",
                "description": "Клиента еще не выбрали",
                "deadline": {"hours": 1},
                "vehicle_profile": {"vin": "JF1SJ5LC5DG012345"},
            }
        )["card"]

        with patch.object(
            self.service,
            "_client_related_cards",
            side_effect=AssertionError("suggest_clients_for_card must use batched lookup"),
        ):
            suggestions = self.service.suggest_clients_for_card(
                {"card_id": candidate["id"], "limit": 5}
            )

        self.assertTrue(suggestions["clients"])
        self.assertEqual(suggestions["clients"][0]["id"], client["id"])
        self.assertEqual(
            suggestions["clients"][0]["vehicles_preview"][0]["vin"], "JF1SJ5LC5DG012345"
        )

    def test_card_can_link_to_specific_client_vehicle(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Мульти Клиент",
                "phone": "+7 913 777-88-99",
                "vehicles": [
                    {
                        "vehicle": "Toyota Camry 2018",
                        "brand": "Toyota",
                        "model": "Camry",
                        "vin": "JTDBE32K620654321",
                        "license_plate": "А777ВС124",
                        "year": "2018",
                    },
                    {
                        "vehicle": "Mercedes-Benz E200 2014",
                        "brand": "Mercedes-Benz",
                        "model": "E200",
                        "vin": "WDD2120341B009639",
                        "license_plate": "У867РУ124",
                        "year": "2014",
                    },
                ],
            }
        )["client"]
        vehicle_id = client["vehicles"][1]["id"]
        created = self.service.create_card(
            {
                "title": "Выбор автомобиля",
                "description": "Клиент приехал на Mercedes",
                "deadline": {"hours": 2},
                "vehicle_profile": {"customer_name": "Мульти Клиент"},
            }
        )["card"]

        linked = self.service.link_card_to_client(
            {
                "card_id": created["id"],
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "sync_vehicle_fields": True,
            }
        )

        self.assertEqual(linked["card"]["client_id"], client["id"])
        self.assertEqual(linked["card"]["client_vehicle_id"], vehicle_id)
        self.assertEqual(linked["card"]["vehicle_profile"]["vin"], "WDD2120341B009639")
        self.assertEqual(linked["card"]["vehicle_profile"]["registration_plate"], "у867ру124")
        self.assertEqual(linked["card"]["vehicle_profile"]["make_display"], "Mercedes-Benz")

    def test_link_card_to_client_can_create_vehicle_from_card_and_sync_back(self) -> None:
        client = self.service.create_client(
            {"display_name": "Клиент с новым авто", "phone": "+7 913 111-22-33"}
        )["client"]
        created = self.service.create_card(
            {
                "vehicle": "Nissan X-Trail 2019",
                "title": "Новый автомобиль",
                "description": "Первичный осмотр",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "make_display": "Nissan",
                    "model_display": "X-Trail",
                    "production_year": 2019,
                    "vin": "JN1TANT32U0012345",
                    "registration_plate": "Н111НН124",
                },
            }
        )["card"]

        linked = self.service.link_card_to_client(
            {
                "card_id": created["id"],
                "client_id": client["id"],
                "create_vehicle_from_card": True,
            }
        )
        vehicle_id = linked["card"]["client_vehicle_id"]
        self.assertTrue(vehicle_id)

        profile = self.service.get_client({"client_id": client["id"]})
        self.assertEqual(profile["vehicles"][0]["id"], vehicle_id)
        self.assertEqual(profile["vehicles"][0]["vin"], "JN1TANT32U0012345")

        self.service.update_card(
            {
                "card_id": created["id"],
                "vehicle_profile": {
                    "vin": "JN1TANT32U0099999",
                    "registration_plate": "Н999НН124",
                },
            }
        )
        updated_profile = self.service.get_client({"client_id": client["id"]})
        self.assertEqual(updated_profile["vehicles"][0]["vin"], "JN1TANT32U0099999")
        self.assertEqual(updated_profile["vehicles"][0]["license_plate"], "н999нн124")

    def test_client_vehicle_crud_syncs_and_hides_deleted_vehicle(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Клиент CRUD авто",
                "vehicles": [
                    {
                        "vehicle": "ГАЗ 2217 Соболь",
                        "vin": "X96221700G0801473",
                        "license_plate": "А111АА124",
                    }
                ],
            }
        )["client"]
        vehicle_id = client["vehicles"][0]["id"]
        card = self.service.create_card(
            {
                "title": "Связанная машина",
                "vehicle": "ГАЗ 2217 Соболь",
                "vehicle_profile": {"vin": "OLDVIN", "registration_plate": "О111ОО124"},
                "deadline": {"hours": 1},
            }
        )["card"]
        self.service.link_card_to_client(
            {
                "card_id": card["id"],
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "sync_vehicle_fields": True,
            }
        )

        updated = self.service.upsert_client_vehicle(
            {
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "vehicle": {
                    "vehicle": "ГАЗ 2217 Соболь",
                    "vin": "X96221700G0999999",
                    "license_plate": "В222ВВ124",
                },
            }
        )
        self.assertIn(card["id"], updated["meta"]["synced_card_ids"])
        synced_card = self.service.get_card({"card_id": card["id"]})["card"]
        self.assertEqual(synced_card["vehicle_profile"]["vin"], "X96221700G0999999")
        self.assertEqual(synced_card["vehicle_profile"]["registration_plate"], "в222вв124")

        deleted = self.service.delete_client_vehicle(
            {
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "unlink_cards": True,
            }
        )
        self.assertTrue(deleted["meta"]["deleted"])
        self.assertEqual(deleted["meta"]["linked_cards_unlinked"], 1)
        unlinked_card = self.service.get_card({"card_id": card["id"]})["card"]
        self.assertEqual(unlinked_card["client_id"], client["id"])
        self.assertEqual(unlinked_card["client_vehicle_id"], "")
        profile = self.service.get_client({"client_id": client["id"]})
        self.assertEqual(profile["vehicles"], [])

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

        prefix_client = self.service.create_client(
            {
                "last_name": "Кузнецов",
                "first_name": "Павел",
                "phone": "+7 (902) 222-33-44",
            }
        )["client"]
        for query in ("8-902", "902 222 33", "89022223344", "79022223344"):
            with self.subTest(query=query):
                search = self.service.search_clients({"query": query, "limit": 5})
                self.assertTrue(search["clients"])
                self.assertEqual(search["clients"][0]["id"], prefix_client["id"])

    def test_client_search_uses_explicit_linked_card_phone(self) -> None:
        linked_client = self.service.create_client(
            {"display_name": "Связанный клиент без телефона"}
        )["client"]
        unlinked_same_name = self.service.create_client(
            {"display_name": "Связанный клиент без телефона"}
        )["client"]
        direct_client = self.service.create_client(
            {
                "display_name": "Прямой клиент с тем же телефоном",
                "phone": "+7 961 738-01-11",
            }
        )["client"]
        card = self.service.create_card(
            {
                "title": "Связанная карточка с телефоном",
                "vehicle": "BMW X5",
                "vehicle_profile": {
                    "customer_name": "Связанный клиент без телефона",
                    "customer_phone": "8 961 738-01-11",
                },
                "deadline": {"hours": 2},
            }
        )["card"]
        self.service.link_card_to_client(
            {"card_id": card["id"], "client_id": linked_client["id"], "sync_fields": False}
        )

        search = self.service.search_clients({"query": "89617380111", "limit": 10})
        found_ids = {client["id"] for client in search["clients"]}

        self.assertIn(linked_client["id"], found_ids)
        self.assertIn(direct_client["id"], found_ids)
        self.assertNotIn(unlinked_same_name["id"], found_ids)

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

    def test_archive_card_rejects_legacy_closed_unpaid_repair_order(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Mazda Axela",
                "title": "Legacy закрыт без оплаты",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "64",
                    "status": "open",
                    "client": "Егорова Таисия",
                    "vehicle": "Mazda Axela",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                },
            }
        )

        bundle = self.store.read_bundle()
        stored_card = next(item for item in bundle["cards"] if item.id == card_id)
        stored_card.repair_order.status = "closed"
        stored_card.repair_order.closed_at = "15.04.2026 09:10"
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        with self.assertRaises(ServiceError) as raised:
            self.service.archive_card({"card_id": card_id})

        self.assertEqual(raised.exception.code, "repair_order_unpaid_archive_blocked")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.details["due_total"], "1000")

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

    def test_update_paid_closed_repair_order_rejects_financial_edit_that_creates_underpayment(
        self,
    ) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        created = self.service.create_card(
            {
                "vehicle": "Toyota Corolla",
                "title": "Закрытый наряд нельзя сделать неоплаченным",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "06.04.2026 10:00",
                            "payment_method": "cash",
                            "cashbox_id": cashbox["id"],
                        }
                    ],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})

        with self.assertRaises(ServiceError) as raised:
            self.service.update_repair_order(
                {
                    "card_id": card_id,
                    "repair_order": {
                        "works": [
                            {"name": "Диагностика", "quantity": "1", "price": "1500"},
                        ],
                    },
                }
            )

        self.assertEqual(raised.exception.code, "repair_order_payment_required")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.details["due_total"], "500")

    def test_unpaid_closed_repair_order_does_not_accrue_work_salary_on_legacy_edit(
        self,
    ) -> None:
        employee = self.service.save_employee(
            {
                "name": "Иван Исполнитель",
                "salary_mode": "percent_only",
                "work_percent": "50",
                "material_percent": "10",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota Corolla",
                "title": "Исторически закрыт без оплаты",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                    "materials": [
                        {
                            "name": "Фильтр",
                            "quantity": "1",
                            "cost_price": "500",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )
        bundle = self.store.read_bundle()
        stored_card = next(item for item in bundle["cards"] if item.id == card_id)
        stored_card.repair_order.status = "closed"
        stored_card.repair_order.closed_at = "18.05.2026 12:39"
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        edited = self.service.update_repair_order(
            {"card_id": card_id, "repair_order": {"comment": "Историческая правка"}}
        )["repair_order"]

        work = edited["works"][0]
        material = edited["materials"][0]
        self.assertEqual(work["salary_amount"], "")
        self.assertEqual(work["salary_accrued_at"], "")
        self.assertEqual(work["salary_mode_snapshot"], "")
        self.assertEqual(material["material_salary_amount"], "")
        self.assertEqual(material["material_salary_accrued_at"], "")

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

    def test_get_repair_order_prefills_vehicle_passport_fields(self) -> None:
        created = self.service.create_card(
            {
                "title": "Замена масла",
                "description": "Проверить течь.",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "customer_name": "Иван Иванов",
                    "customer_phone": "+7 999 111-22-33",
                    "make_display": "Mercedes-Benz",
                    "model_display": "E200",
                    "production_year": 2014,
                    "registration_plate": "У867РУ124",
                    "vin": "WDD2120341B009639",
                    "mileage": 185000,
                },
            }
        )

        fetched = self.service.get_repair_order({"card_id": created["card"]["id"]})

        order = fetched["repair_order"]
        self.assertEqual(order["client"], "Иван Иванов")
        self.assertEqual(order["phone"], "+7 999 111-22-33")
        self.assertEqual(order["vehicle"], "Mercedes-Benz E200 2014")
        self.assertEqual(order["license_plate"], "у867ру124")
        self.assertEqual(order["vin"], "WDD2120341B009639")
        self.assertEqual(order["mileage"], "185000")

    def test_license_plate_is_normalized_to_lowercase_across_modules(self) -> None:
        client = self.service.create_client(
            {
                "client": {
                    "client_type": "person",
                    "last_name": "Петров",
                    "phone": "+7 999 111-22-33",
                    "vehicles": [
                        {
                            "vehicle": "Mercedes-Benz E200",
                            "license_plate": "У867РУ124",
                        }
                    ],
                }
            }
        )["client"]
        created = self.service.create_card(
            {
                "title": "Диагностика",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "registration_plate": "А123АА124",
                    "vin": "WDD2120341B009639",
                },
            }
        )
        self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "repair_order": {"license_plate": "В003НК124"},
            }
        )

        fetched_client = self.service.get_client({"client_id": client["id"]})["client"]
        fetched_card = self.service.get_card({"card_id": created["card"]["id"]})["card"]
        fetched_order = self.service.get_repair_order({"card_id": created["card"]["id"]})[
            "repair_order"
        ]

        self.assertEqual(fetched_client["vehicles"][0]["license_plate"], "у867ру124")
        self.assertEqual(fetched_card["vehicle_profile"]["registration_plate"], "а123аа124")
        self.assertEqual(fetched_order["license_plate"], "в003нк124")

    def test_existing_repair_order_get_fills_missing_vehicle_passport_fields(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Mercedes-Benz E200 2014",
                "title": "Диагностика",
                "description": "Проверить подвеску.",
                "deadline": {"hours": 2},
            }
        )
        self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "repair_order": {"client": "Ручной клиент"},
            }
        )

        self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "vehicle_profile": {
                    "registration_plate": "У867РУ124",
                    "vin": "WDD2120341B009639",
                    "mileage": 185000,
                },
            }
        )
        fetched = self.service.get_repair_order({"card_id": created["card"]["id"]})

        order = fetched["repair_order"]
        self.assertEqual(order["client"], "Ручной клиент")
        self.assertEqual(order["license_plate"], "у867ру124")
        self.assertEqual(order["vin"], "WDD2120341B009639")
        self.assertEqual(order["mileage"], "185000")

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

    def test_run_full_card_enrichment_is_disabled_without_editing(self) -> None:
        created = self.service.create_card(
            {
                "title": "Enrichment",
                "description": "Клиент: Иван\nТелефон: 89001112233\nПроверить радиатор",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        result = self.service.run_full_card_enrichment({"card_id": card_id, "actor_name": "AI"})

        self.assertFalse(result["meta"]["changed"])
        self.assertEqual(result["meta"]["scenario_id"], "manual_only")
        self.assertTrue(result["meta"]["retired"])
        self.assertEqual(result["meta"]["legacy_request"], "run_full_card_enrichment")
        self.assertEqual(result["meta"]["reason"], "automatic_card_cleanup_disabled")
        self.assertNotIn("cleanup_mode", result["meta"])
        self.assertFalse(result["card"]["ai_autofill_active"])

    def test_run_full_card_enrichment_enqueues_agent_task_when_attached(self) -> None:
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
        self.assertEqual(summary["base_salary_accrued_total"], "0")
        self.assertEqual(summary["total_salary"], "1500")

    def test_repair_order_work_salary_override_accrues_guarantee_plus_percent(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Иван Мастер",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "30",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Mercedes GLA",
                "title": "Индивидуальная зарплата по строке",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "77",
                    "status": "open",
                    "vehicle": "Mercedes GLA",
                    "license_plate": "К777КК124",
                    "payments": [
                        {
                            "amount": "20000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Ремонт модуля SCM",
                            "quantity": "1",
                            "price": "20000",
                            "executor_id": employee["id"],
                            "work_salary_override_enabled": "true",
                            "work_salary_guarantee": "5000",
                            "work_salary_percent_override": "45",
                            "work_salary_note": "Премия за сложную работу",
                        }
                    ],
                },
            }
        )
        stored_row = updated["card"]["repair_order"]["works"][0]
        self.assertEqual(stored_row["work_salary_override_enabled"], "true")
        self.assertEqual(stored_row["work_salary_guarantee"], "5000")
        self.assertEqual(stored_row["work_salary_percent_override"], "45")

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        closed_row = closed["repair_order"]["works"][0]
        self.assertEqual(closed_row["work_percent_snapshot"], "45")
        self.assertEqual(closed_row["salary_amount"], "11750")

        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        report = self.service.get_payroll_report(
            {"month": closed_month, "employee_id": employee["id"]}
        )
        detail_row = next(
            row for row in report["detail_rows"] if row["employee_id"] == employee["id"]
        )
        self.assertEqual(detail_row["salary_amount"], "11750")
        employee_report = self.service.get_employee_salary_report(
            {"month": closed_month, "employee_id": employee["id"]}
        )
        self.assertIn("Выплата исполнителю", employee_report["text"])
        self.assertIn("45", employee_report["text"])

        reconciliation = self.service.get_employee_salary_reconciliation(
            {"employee_id": employee["id"]}
        )
        work_row = next(row for row in reconciliation["rows"] if row["kind"] == "work_accrual")
        self.assertEqual(work_row["accrued"], "11750")
        self.assertIn("Выплата исполнителю 5 000,00 ₽ + 45%", work_row["scheme"])
        self.assertIn("Работа 20 000,00 ₽", work_row["calculation_base"])

    def test_repair_order_work_salary_cost_price_reduces_percent_base(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Мастер с себестоимостью",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "30",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota Camry",
                "title": "Себестоимость работы",
                "deadline": {"hours": 2},
            }
        )
        updated = self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "repair_order": {
                    "number": "79",
                    "status": "open",
                    "vehicle": "Toyota Camry",
                    "payments": [
                        {
                            "amount": "20000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Работа с подрядом",
                            "quantity": "1",
                            "price": "20000",
                            "executor_id": employee["id"],
                            "work_salary_override_enabled": "true",
                            "work_salary_guarantee": "5000",
                            "work_salary_percent_override": "45",
                            "work_salary_cost_price": "3000",
                        }
                    ],
                },
            }
        )
        stored_row = updated["card"]["repair_order"]["works"][0]
        self.assertEqual(stored_row["work_salary_cost_price"], "3000")

        closed = self.service.set_repair_order_status(
            {"card_id": created["card"]["id"], "status": "closed"}
        )
        closed_row = closed["repair_order"]["works"][0]
        self.assertEqual(closed_row["work_percent_snapshot"], "45")
        self.assertEqual(closed_row["salary_amount"], "10400")

        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        reconciliation = self.service.get_employee_salary_reconciliation(
            {"month": closed_month, "employee_id": employee["id"]}
        )
        work_row = next(row for row in reconciliation["rows"] if row["kind"] == "work_accrual")
        self.assertEqual(work_row["accrued"], "10400")
        self.assertIn("Работа 20 000,00 ₽", work_row["calculation_base"])
        self.assertIn("Себестоимость работы 3 000,00 ₽", work_row["calculation_base"])

    def test_repair_order_work_salary_cost_price_reduces_default_percent_accrual(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Процентный мастер",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "30",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Nissan X-Trail",
                "title": "Себестоимость без индивидуального процента",
                "deadline": {"hours": 2},
            }
        )
        self.service.update_card(
            {
                "card_id": created["card"]["id"],
                "repair_order": {
                    "number": "80",
                    "status": "open",
                    "vehicle": "Nissan X-Trail",
                    "payments": [
                        {"amount": "5000", "paid_at": "05.04.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {
                            "name": "Работа с сервисной себестоимостью",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee["id"],
                            "work_salary_cost_price": "1000",
                        }
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status(
            {"card_id": created["card"]["id"], "status": "closed"}
        )
        closed_row = closed["repair_order"]["works"][0]
        self.assertEqual(closed_row["work_percent_snapshot"], "30")
        self.assertEqual(closed_row["salary_amount"], "1200")

    def test_work_salary_snapshot_keeps_original_override_terms_after_closed_order_edit(
        self,
    ) -> None:
        employee = self.service.save_employee(
            {
                "name": "Мастер исторической себестоимости",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "30",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota Camry",
                "title": "История выплаты исполнителю",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "81",
                    "status": "open",
                    "vehicle": "Toyota Camry",
                    "payments": [
                        {
                            "amount": "20000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Работа с подрядом",
                            "quantity": "1",
                            "price": "20000",
                            "executor_id": employee["id"],
                            "work_salary_override_enabled": "true",
                            "work_salary_guarantee": "5000",
                            "work_salary_percent_override": "45",
                            "work_salary_cost_price": "3000",
                            "work_salary_note": "Первичная договоренность",
                        }
                    ],
                },
            }
        )
        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        self.assertEqual(closed["repair_order"]["works"][0]["salary_amount"], "10400")

        edited_work = dict(closed["repair_order"]["works"][0])
        edited_work.update(
            {
                "salary_amount": "1",
                "work_percent_snapshot": "10",
                "work_salary_guarantee": "1000",
                "work_salary_percent_override": "10",
                "work_salary_cost_price": "9000",
                "work_salary_note": "Поздняя правка",
            }
        )
        updated = self.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {
                    **closed["repair_order"],
                    "comment": "Правка после закрытия не меняет payroll snapshot",
                    "works": [edited_work],
                },
            }
        )
        work = updated["repair_order"]["works"][0]
        self.assertEqual(work["salary_amount"], "10400")
        self.assertEqual(work["work_percent_snapshot"], "45")
        self.assertEqual(work["work_salary_guarantee"], "5000")
        self.assertEqual(work["work_salary_percent_override"], "45")
        self.assertEqual(work["work_salary_cost_price"], "3000")
        self.assertEqual(work["work_salary_note"], "Первичная договоренность")

        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        reconciliation = self.service.get_employee_salary_reconciliation(
            {"month": closed_month, "employee_id": employee["id"]}
        )
        work_row = next(row for row in reconciliation["rows"] if row["kind"] == "work_accrual")
        self.assertEqual(work_row["accrued"], "10400")
        self.assertIn("Выплата исполнителю 5 000,00 ₽ + 45%", work_row["scheme"])
        self.assertIn("Себестоимость работы 3 000,00 ₽", work_row["calculation_base"])
        self.assertNotIn("9 000,00 ₽", work_row["calculation_base"])

    def test_repair_order_work_salary_override_guarantee_above_total_uses_zero_base(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Премиальный Мастер",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "10",
            }
        )["employee"]
        card = self.service.create_card(
            {
                "vehicle": "Honda Civic",
                "title": "Гарантия выше суммы",
                "deadline": {"hours": 2},
            }
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "78",
                    "status": "open",
                    "vehicle": "Honda Civic",
                    "payments": [
                        {
                            "amount": "3000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Сложная диагностика",
                            "quantity": "1",
                            "price": "3000",
                            "executor_id": employee["id"],
                            "work_salary_override_enabled": "true",
                            "work_salary_guarantee": "5000",
                            "work_salary_percent_override": "80",
                        }
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
        self.assertEqual(closed["repair_order"]["works"][0]["salary_amount"], "5000")
        self.assertEqual(closed["repair_order"]["works"][0]["work_percent_snapshot"], "80")

    def test_weekly_base_salary_accrues_on_friday_evening(self) -> None:
        created_at = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        as_of = datetime(2026, 5, 16, 14, 0, tzinfo=timezone.utc)
        created_patches = self._patch_time(created_at)
        with created_patches[0], created_patches[1], created_patches[2]:
            employee = self.service.save_employee(
                {
                    "name": "Пятничный Оклад",
                    "position": "Мастер",
                    "salary_mode": "salary_only",
                    "base_salary": "1000",
                }
            )["employee"]

        patches = self._patch_time(as_of)
        with patches[0], patches[1], patches[2]:
            report = self.service.get_payroll_report({"month": "2026-05"})
            ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})

        summary = next(item for item in report["summary"] if item["employee_id"] == employee["id"])
        self.assertEqual(summary["base_salary"], "1000")
        self.assertEqual(summary["base_salary_accruals_count"], 3)
        self.assertEqual(summary["base_salary_accrued_total"], "3000")
        self.assertEqual(summary["accrued_total"], "3000")
        self.assertEqual(summary["total_salary"], "3000")
        base_rows = [
            item
            for item in report["detail_rows"]
            if item["employee_id"] == employee["id"] and item["row_type"] == "base_salary"
        ]
        self.assertEqual(len(base_rows), 3)
        self.assertEqual({item["salary_amount"] for item in base_rows}, {"1000"})
        self.assertEqual(ledger["balance_total"], "3000")
        self.assertEqual(ledger["accrued_total"], "3000")
        self.assertEqual(
            len([row for row in ledger["journal_rows"] if row["kind"] == "base_salary_accrual"]),
            3,
        )

    def test_payroll_report_groups_detail_rows_by_repair_order(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Иван Мастер",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "100",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Mitsubishi L200",
                "title": "Сводный отчёт",
                "description": "Проверка группировки работ по заказ-наряду",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        updated = self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "29",
                    "status": "open",
                    "client": "Витя Покровский",
                    "vehicle": "Mitsubishi L200",
                    "payments": [
                        {"amount": "3000", "paid_at": "05.04.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": employee["id"],
                        },
                        {
                            "name": "Замена масла",
                            "quantity": "1",
                            "price": "2000",
                            "executor_id": employee["id"],
                        },
                    ],
                },
            }
        )
        self.assertEqual(len(updated["card"]["repair_order"]["works"]), 2)

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        self.assertEqual(closed["repair_order"]["works"][0]["salary_amount"], "1000")
        self.assertEqual(closed["repair_order"]["works"][1]["salary_amount"], "2000")

        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        report = self.service.get_payroll_report({"month": closed_month})
        detail_rows = [row for row in report["detail_rows"] if row["employee_id"] == employee["id"]]
        self.assertEqual(len(detail_rows), 1)
        self.assertNotIn("work_name", detail_rows[0])
        self.assertEqual(detail_rows[0]["works_count"], 2)
        self.assertEqual(detail_rows[0]["work_total"], "3000")
        self.assertEqual(detail_rows[0]["salary_amount"], "3000")

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

    def test_work_salary_snapshot_keeps_original_executor_after_closed_order_edit(self) -> None:
        original_employee = self.service.save_employee(
            {
                "name": "Оригинальный Мастер",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "10",
            }
        )["employee"]
        other_employee = self.service.save_employee(
            {
                "name": "Другой Мастер",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "50",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Skoda Rapid",
                "title": "Снимок исполнителя работы",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "46",
                    "status": "open",
                    "vehicle": "Skoda Rapid",
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": original_employee["id"],
                        }
                    ],
                },
            }
        )
        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        self.assertEqual(closed["repair_order"]["works"][0]["salary_amount"], "100")

        bundle = self.store.read_bundle()
        stored_card = next(item for item in bundle["cards"] if item.id == card_id)
        stored_card.repair_order.works[0].work_executor_id_snapshot = ""
        stored_card.repair_order.works[0].work_executor_name_snapshot = ""
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        self.service.save_employee(
            {
                "employee_id": original_employee["id"],
                "name": original_employee["name"],
                "position": original_employee["position"],
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "90",
            }
        )
        edited_work = {
            **closed["repair_order"]["works"][0],
            "work_executor_id_snapshot": "",
            "work_executor_name_snapshot": "",
            "executor_id": other_employee["id"],
            "executor_name": other_employee["name"],
        }
        updated = self.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {
                    **closed["repair_order"],
                    "note": "Редактирование после закрытия не переносит начисление",
                    "works": [edited_work],
                },
            }
        )

        work = updated["repair_order"]["works"][0]
        self.assertEqual(work["executor_id"], other_employee["id"])
        self.assertEqual(work["work_executor_id_snapshot"], original_employee["id"])
        self.assertEqual(work["work_executor_name_snapshot"], original_employee["name"])
        self.assertEqual(work["work_percent_snapshot"], "10")
        self.assertEqual(work["salary_amount"], "100")

        report = self.service.get_payroll_report({"month": closed_month})
        original_summary = next(
            item for item in report["summary"] if item["employee_id"] == original_employee["id"]
        )
        other_summary = next(
            item for item in report["summary"] if item["employee_id"] == other_employee["id"]
        )
        self.assertEqual(original_summary["works_count"], 1)
        self.assertEqual(original_summary["work_accrued_total"], "100")
        self.assertEqual(other_summary["works_count"], 0)
        self.assertEqual(other_summary["work_accrued_total"], "0")

    def test_work_salary_snapshot_keeps_original_sale_after_closed_order_edit(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Мастер суммы работы",
                "position": "Механик",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "10",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota RAV4",
                "title": "Снимок суммы работы",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "48",
                    "status": "open",
                    "vehicle": "Toyota RAV4",
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        self.assertEqual(closed["repair_order"]["works"][0]["salary_amount"], "100")
        self.assertEqual(closed["repair_order"]["works"][0]["work_quantity_snapshot"], "1")
        self.assertEqual(closed["repair_order"]["works"][0]["work_price_snapshot"], "1000")

        edited_work = dict(closed["repair_order"]["works"][0])
        for field_name in ("work_quantity_snapshot", "work_price_snapshot", "work_total_snapshot"):
            edited_work.pop(field_name, None)
        edited_work.update({"quantity": "5", "price": "1000"})
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    **closed["repair_order"],
                    "payments": [
                        {
                            "amount": "5000",
                            "paid_at": "05.04.2026 10:05",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [edited_work],
                },
            }
        )

        report = self.service.get_payroll_report({"month": closed_month})
        summary = next(item for item in report["summary"] if item["employee_id"] == employee["id"])
        detail_rows = [row for row in report["detail_rows"] if row["employee_id"] == employee["id"]]
        self.assertEqual(summary["works_total"], "1000")
        self.assertEqual(summary["work_accrued_total"], "100")
        self.assertEqual(detail_rows[0]["work_total"], "1000")
        self.assertEqual(detail_rows[0]["salary_amount"], "100")

    def test_material_rows_persist_cost_and_executor_through_section_replace(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Сергей Снабженец",
                "position": "Снабженец",
                "material_percent": "12.5",
            }
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota RAV4",
                "title": "Материалы с исполнителем",
                "deadline": {"hours": 2},
            }
        )

        replaced = self.service.replace_repair_order_materials(
            {
                "card_id": created["card"]["id"],
                "rows": [
                    {
                        "name": "Фильтр салона",
                        "catalog_number": "87139-06080",
                        "quantity": "2",
                        "cost_price": "700",
                        "price": "1000",
                        "executor_id": employee["id"],
                    }
                ],
            }
        )

        row = replaced["repair_order"]["materials"][0]
        self.assertEqual(row["catalog_number"], "87139-06080")
        self.assertEqual(row["cost_price"], "700")
        self.assertEqual(row["executor_id"], employee["id"])
        self.assertEqual(row["executor_name"], "")
        self.assertEqual(row["material_salary_amount"], "")

    def test_closing_paid_repair_order_accrues_material_profit_salary(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Сергей Снабженец",
                "position": "Снабженец",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "0",
                "material_percent": "10",
            }
        )["employee"]
        self.assertEqual(employee["material_percent"], "10")
        created = self.service.create_card(
            {
                "vehicle": "Toyota RAV4",
                "title": "Начисление с материалов",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "41",
                    "status": "open",
                    "client": "Клиент",
                    "vehicle": "Toyota RAV4",
                    "payments": [
                        {
                            "amount": "2000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "materials": [
                        {
                            "name": "Фильтр салона",
                            "catalog_number": "87139-06080",
                            "quantity": "2",
                            "cost_price": "700",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        material = closed["repair_order"]["materials"][0]
        self.assertEqual(material["executor_name"], "Сергей Снабженец")
        self.assertEqual(material["cost_price"], "700")
        self.assertEqual(material["material_percent_snapshot"], "10")
        self.assertEqual(material["material_profit"], "600")
        self.assertEqual(material["material_salary_amount"], "60")
        self.assertEqual(
            material["material_salary_accrued_at"], closed["repair_order"]["closed_at"]
        )

        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        report = self.service.get_payroll_report({"month": closed_month})
        summary = next(item for item in report["summary"] if item["employee_id"] == employee["id"])
        self.assertEqual(summary["works_count"], 0)
        self.assertEqual(summary["materials_count"], 1)
        self.assertEqual(summary["materials_total"], "2000")
        self.assertEqual(summary["materials_cost_total"], "1400")
        self.assertEqual(summary["materials_profit_total"], "600")
        self.assertEqual(summary["work_accrued_total"], "0")
        self.assertEqual(summary["materials_accrued_total"], "60")
        self.assertEqual(summary["accrued_total"], "60")
        self.assertEqual(summary["total_salary"], "60")

        detail_rows = [row for row in report["detail_rows"] if row["employee_id"] == employee["id"]]
        self.assertEqual(len(detail_rows), 1)
        self.assertEqual(detail_rows[0]["row_type"], "material")
        self.assertEqual(detail_rows[0]["type_label"], "Материал")
        self.assertEqual(detail_rows[0]["material_name"], "Фильтр салона")
        self.assertEqual(detail_rows[0]["material_total"], "2000")
        self.assertEqual(detail_rows[0]["material_cost_total"], "1400")
        self.assertEqual(detail_rows[0]["material_profit"], "600")
        self.assertEqual(detail_rows[0]["material_percent"], "10")
        self.assertEqual(detail_rows[0]["salary_amount"], "60")

        ledger = self.service.get_employee_salary_ledger({"employee_id": employee["id"]})
        self.assertEqual(ledger["accrued_total"], "60")
        self.assertEqual(ledger["balance_total"], "60")
        self.assertEqual(ledger["journal_rows"][0]["kind"], "material_accrual")
        self.assertEqual(ledger["journal_rows"][0]["work_name"], "Фильтр салона")

        salary_report = self.service.get_employee_salary_report(
            {"employee_id": employee["id"], "month": closed_month}
        )
        self.assertEqual(salary_report["totals"]["material_count"], 1)
        self.assertEqual(salary_report["totals"]["material_profit_total"], "600")
        self.assertEqual(salary_report["totals"]["accrued_total"], "60")
        self.assertIn("Материалов: 1", salary_report["text"])
        self.assertIn("Прибыль: 600,00 ₽", salary_report["text"])

    def test_material_salary_ignores_missing_cost_and_negative_profit(self) -> None:
        employee = self.service.save_employee({"name": "Иван Снабженец"})["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Nissan X-Trail",
                "title": "Материалы без прибыли",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "42",
                    "status": "open",
                    "vehicle": "Nissan X-Trail",
                    "payments": [
                        {
                            "amount": "1500",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "materials": [
                        {
                            "name": "Без закупки",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": employee["id"],
                        },
                        {
                            "name": "Минусовая маржа",
                            "quantity": "1",
                            "cost_price": "700",
                            "price": "500",
                            "executor_id": employee["id"],
                        },
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        missing_cost, negative_profit = closed["repair_order"]["materials"]
        self.assertEqual(missing_cost["material_percent_snapshot"], "")
        self.assertEqual(missing_cost["material_profit"], "")
        self.assertEqual(missing_cost["material_salary_amount"], "")
        self.assertEqual(negative_profit["material_percent_snapshot"], "10")
        self.assertEqual(negative_profit["material_profit"], "0")
        self.assertEqual(negative_profit["material_salary_amount"], "0")

    def test_material_salary_snapshot_is_frozen_after_employee_percent_change(self) -> None:
        employee = self.service.save_employee({"name": "Олег Снабженец", "material_percent": "10"})[
            "employee"
        ]
        created = self.service.create_card(
            {
                "vehicle": "Mazda CX-5",
                "title": "Заморозка материалов",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "43",
                    "status": "open",
                    "vehicle": "Mazda CX-5",
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "materials": [
                        {
                            "name": "Щётка стеклоочистителя",
                            "quantity": "1",
                            "cost_price": "700",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        self.assertEqual(closed["repair_order"]["materials"][0]["material_salary_amount"], "30")

        self.service.save_employee(
            {
                "employee_id": employee["id"],
                "name": "Олег Снабженец",
                "material_percent": "50",
            }
        )
        updated = self.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {
                    **closed["repair_order"],
                    "note": "После изменения процента",
                },
            }
        )

        material = updated["repair_order"]["materials"][0]
        self.assertEqual(material["material_percent_snapshot"], "10")
        self.assertEqual(material["material_profit"], "300")
        self.assertEqual(material["material_salary_amount"], "30")

        reopened = self.service.set_repair_order_status({"card_id": card_id, "status": "open"})
        reopened_material = reopened["repair_order"]["materials"][0]
        self.assertEqual(reopened_material["material_percent_snapshot"], "")
        self.assertEqual(reopened_material["material_profit"], "")
        self.assertEqual(reopened_material["material_salary_amount"], "")

    def test_material_salary_snapshot_keeps_original_executor_after_row_edit(self) -> None:
        original_employee = self.service.save_employee(
            {"name": "Оригинальный Снабженец", "material_percent": "10"}
        )["employee"]
        other_employee = self.service.save_employee(
            {"name": "Другой Снабженец", "material_percent": "50"}
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Hyundai Solaris",
                "title": "Заморозка исполнителя материалов",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "44",
                    "status": "open",
                    "vehicle": "Hyundai Solaris",
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "materials": [
                        {
                            "name": "Фара",
                            "quantity": "1",
                            "cost_price": "400",
                            "price": "1000",
                            "executor_id": original_employee["id"],
                        }
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        edited_material = {
            **closed["repair_order"]["materials"][0],
            "executor_id": other_employee["id"],
            "executor_name": other_employee["name"],
        }
        updated = self.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {
                    **closed["repair_order"],
                    "materials": [edited_material],
                },
            }
        )

        material = updated["repair_order"]["materials"][0]
        self.assertEqual(material["material_executor_id_snapshot"], original_employee["id"])
        self.assertEqual(material["material_executor_name_snapshot"], original_employee["name"])
        self.assertEqual(material["material_salary_amount"], "60")

        report = self.service.get_payroll_report({"month": closed_month})
        original_summary = next(
            item for item in report["summary"] if item["employee_id"] == original_employee["id"]
        )
        other_summary = next(
            item for item in report["summary"] if item["employee_id"] == other_employee["id"]
        )
        self.assertEqual(original_summary["materials_count"], 1)
        self.assertEqual(original_summary["materials_accrued_total"], "60")
        self.assertEqual(other_summary["materials_count"], 0)
        self.assertEqual(other_summary["materials_accrued_total"], "0")

    def test_material_salary_snapshot_survives_sparse_closed_order_update(self) -> None:
        employee = self.service.save_employee({"name": "Снабженец API", "material_percent": "10"})[
            "employee"
        ]
        created = self.service.create_card(
            {
                "vehicle": "Hyundai Solaris",
                "title": "Sparse material snapshot update",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "47",
                    "status": "open",
                    "vehicle": "Hyundai Solaris",
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "materials": [
                        {
                            "name": "Фара",
                            "quantity": "1",
                            "cost_price": "400",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )
        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        self.assertEqual(closed["repair_order"]["materials"][0]["material_salary_amount"], "60")

        self.service.save_employee(
            {
                "employee_id": employee["id"],
                "name": employee["name"],
                "material_percent": "50",
            }
        )
        updated = self.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {
                    **closed["repair_order"],
                    "note": "Клиент прислал строку без скрытых snapshot-полей",
                    "materials": [
                        {
                            "name": "Фара",
                            "quantity": "1",
                            "cost_price": "400",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )

        material = updated["repair_order"]["materials"][0]
        self.assertEqual(material["material_percent_snapshot"], "10")
        self.assertEqual(material["material_profit"], "600")
        self.assertEqual(material["material_salary_amount"], "60")
        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        report = self.service.get_payroll_report(
            {"month": closed_month, "employee_id": employee["id"]}
        )
        self.assertEqual(report["summary"][0]["materials_accrued_total"], "60")

    def test_material_salary_snapshot_keeps_original_sale_and_cost_after_row_edit(self) -> None:
        employee = self.service.save_employee(
            {"name": "Снабженец со снимком", "material_percent": "10"}
        )["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Toyota RAV4",
                "title": "Снимок суммы материалов",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "43",
                    "status": "open",
                    "vehicle": "Toyota RAV4",
                    "payments": [
                        {
                            "amount": "2000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "materials": [
                        {
                            "name": "Фильтр масляный",
                            "quantity": "2",
                            "cost_price": "700",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        closed_month = dt.strptime(closed["repair_order"]["closed_at"], "%d.%m.%Y %H:%M").strftime(
            "%Y-%m"
        )
        self.assertEqual(closed["repair_order"]["materials"][0]["material_quantity_snapshot"], "2")
        material = dict(closed["repair_order"]["materials"][0])
        material.update({"quantity": "5", "cost_price": "1", "price": "9999"})
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    **closed["repair_order"],
                    "payments": [
                        {
                            "amount": "49995",
                            "paid_at": "05.04.2026 10:05",
                            "payment_method": "cash",
                        }
                    ],
                    "materials": [material],
                },
            }
        )

        report = self.service.get_payroll_report({"month": closed_month})
        summary = next(item for item in report["summary"] if item["employee_id"] == employee["id"])
        detail_rows = [row for row in report["detail_rows"] if row["employee_id"] == employee["id"]]
        self.assertEqual(summary["materials_total"], "2000")
        self.assertEqual(summary["materials_cost_total"], "1400")
        self.assertEqual(summary["materials_profit_total"], "600")
        self.assertEqual(detail_rows[0]["material_total"], "2000")
        self.assertEqual(detail_rows[0]["material_cost_total"], "1400")
        self.assertEqual(detail_rows[0]["material_profit"], "600")

    def test_closed_order_rejects_payment_removal_that_would_leave_material_unpaid(self) -> None:
        employee = self.service.save_employee({"name": "Снабженец Оплаты"})["employee"]
        created = self.service.create_card(
            {
                "vehicle": "Skoda Rapid",
                "title": "Материалы без оплаты после закрытия",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "45",
                    "status": "open",
                    "vehicle": "Skoda Rapid",
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "05.04.2026 10:00",
                            "payment_method": "cash",
                        }
                    ],
                    "materials": [
                        {
                            "name": "Радиатор",
                            "quantity": "1",
                            "cost_price": "800",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )

        closed = self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
        self.assertEqual(closed["repair_order"]["materials"][0]["material_salary_amount"], "20")

        with self.assertRaises(ServiceError) as raised:
            self.service.update_repair_order(
                {
                    "card_id": card_id,
                    "repair_order": {
                        **closed["repair_order"],
                        "payments": [],
                        "prepayment": "0",
                    },
                }
            )

        self.assertEqual(raised.exception.code, "repair_order_payment_required")
        self.assertEqual(raised.exception.status_code, 409)
        stored = self.service.get_repair_order({"card_id": card_id})["repair_order"]
        material = stored["materials"][0]
        self.assertEqual(material["material_percent_snapshot"], "10")
        self.assertEqual(material["material_profit"], "200")
        self.assertEqual(material["material_salary_amount"], "20")
        self.assertTrue(material["material_salary_accrued_at"])

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

    def test_employee_salary_transaction_uses_selected_cashbox(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Алексей Снабженец",
                "position": "Снабженец",
                "salary_mode": "percent_only",
                "base_salary": "0",
            }
        )["employee"]
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        supplier_cashbox = self.service.create_cashbox(
            {"name": "Касса снабженца", "actor_name": "ADMIN"}
        )["cashbox"]

        transaction = self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_advance",
                "amount": "10000",
                "cashbox_id": supplier_cashbox["id"],
                "actor_name": "ADMIN",
            }
        )["transaction"]

        self.assertEqual(transaction["cashbox_id"], supplier_cashbox["id"])
        supplier_details = self.service.get_cashbox(
            {"cashbox_id": supplier_cashbox["id"], "transaction_limit": 10}
        )
        cash_details = self.service.get_cashbox(
            {"cashbox_id": cashbox["id"], "transaction_limit": 10}
        )
        self.assertEqual(supplier_details["cashbox"]["statistics"]["balance_minor"], -1000000)
        self.assertEqual(cash_details["cashbox"]["statistics"]["balance_minor"], 0)

    def test_employee_salary_report_builds_monthly_accrual_register(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Марина Бухгалтер",
                "position": "Бухгалтер",
                "salary_mode": "salary_plus_percent",
                "base_salary": "25000",
                "work_percent": "15",
            }
        )["employee"]
        other_employee = self.service.save_employee(
            {
                "name": "Другой Мастер",
                "position": "Мастер",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "10",
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
                        "license_plate": "А123ВС124",
                        "payments": [
                            {
                                "amount": "22000",
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
                            },
                            {
                                "name": "Дополнительная диагностика",
                                "quantity": "2",
                                "price": "1000",
                                "executor_id": employee["id"],
                            },
                            {
                                "name": "Итоговая строка",
                                "total": "3000",
                                "executor_id": employee["id"],
                            },
                            {
                                "name": "Чужая работа",
                                "quantity": "1",
                                "price": "5000",
                                "executor_id": other_employee["id"],
                            },
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

        open_card = self.service.create_card(
            {
                "vehicle": "Open Car",
                "title": "Открытый наряд",
                "deadline": {"hours": 2},
            }
        )["card"]
        self.service.update_card(
            {
                "card_id": open_card["id"],
                "repair_order": {
                    "number": "303",
                    "status": "open",
                    "vehicle": "Open Car",
                    "license_plate": "О111ОО124",
                    "works": [
                        {
                            "name": "Открытая работа",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )

        ready_card = self.service.create_card(
            {
                "vehicle": "Ready Car",
                "title": "Готовый наряд",
                "deadline": {"hours": 2},
            }
        )["card"]
        self.service.update_card(
            {
                "card_id": ready_card["id"],
                "repair_order": {
                    "number": "304",
                    "status": "ready",
                    "vehicle": "Ready Car",
                    "license_plate": "Р222РР124",
                    "works": [
                        {
                            "name": "Готовая работа",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )

        report_month = recent_time.astimezone().strftime("%Y-%m")
        report = self.service.get_employee_salary_report(
            {"employee_id": employee["id"], "month": report_month}
        )
        report_text = report["text"]

        self.assertEqual(report["employee_id"], employee["id"])
        self.assertEqual(report["period"]["month"], report_month)
        self.assertEqual(report["meta"]["schema_version"], "employee_salary_report.v3")
        self.assertEqual(report["totals"]["repair_order_count"], 1)
        self.assertEqual(report["totals"]["work_count"], 3)
        self.assertEqual(report["totals"]["work_total"], "17000")
        self.assertEqual(report["totals"]["accrued_total"], "2550")
        self.assertIn("ОТЧЕТ ПО НАЧИСЛЕНИЯМ", report_text)
        self.assertIn("Сотрудник: Марина Бухгалтер", report_text)
        self.assertIn("ЗН 302 | Honda Civic | госномер: а123вс124", report_text)
        self.assertIn("Свежая работа", report_text)
        self.assertIn("Дополнительная диагностика", report_text)
        self.assertIn("Итоговая строка", report_text)
        self.assertIn("Стоимость: 12 000,00 ₽", report_text)
        self.assertIn("Начислено: 1 800,00 ₽", report_text)
        self.assertIn("Стоимость: 3 000,00 ₽", report_text)
        self.assertIn("Начислено: 450,00 ₽", report_text)
        self.assertNotIn("Цена: 0,00 ₽", report_text)
        self.assertNotIn("Чужая работа", report_text)
        self.assertNotIn("Открытая работа", report_text)
        self.assertNotIn("Готовая работа", report_text)
        self.assertNotIn("СВЕЖИЙ АВАНС", report_text)
        self.assertNotIn("Старый заказ", report_text)
        self.assertNotIn("СТАРАЯ ВЫПЛАТА", report_text)
        self.assertNotIn("Выплачено", report_text)
        self.assertNotIn("Авансы", report_text)
        self.assertEqual(len(report["days"]), 1)
        order = report["days"][0]["repair_orders"][0]
        self.assertEqual(order["repair_order_number"], "302")
        self.assertEqual(order["license_plate"], "а123вс124")
        self.assertEqual(order["work_count"], 3)
        self.assertEqual(len(order["works"]), 3)
        self.assertEqual(order["works"][0]["total_display"], "12 000,00 ₽")
        self.assertEqual(order["works"][0]["accrued_display"], "1 800,00 ₽")
        self.assertEqual(order["works"][2]["price"], "")
        self.assertEqual(order["works"][2]["price_display"], "")
        self.assertEqual(order["works"][2]["total_display"], "3 000,00 ₽")

    def test_employee_salary_report_empty_month_returns_text_message(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Пустой Месяц",
                "position": "Мастер",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "10",
            }
        )["employee"]

        report = self.service.get_employee_salary_report(
            {"employee_id": employee["id"], "month": "2026-01"}
        )

        self.assertEqual(report["meta"]["schema_version"], "employee_salary_report.v3")
        self.assertEqual(report["period"]["month"], "2026-01")
        self.assertEqual(report["totals"]["repair_order_count"], 0)
        self.assertEqual(report["totals"]["work_count"], 0)
        self.assertEqual(report["days"], [])
        self.assertIn(
            "За выбранный период начислений по закрытым заказ-нарядам нет.",
            report["text"],
        )

    def test_employee_salary_reconciliation_builds_printable_30_day_statement(self) -> None:
        created_at = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        as_of = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
        create_patches = self._patch_time(created_at)
        with create_patches[0], create_patches[1], create_patches[2]:
            employee = self.service.save_employee(
                {
                    "name": "Марина Бухгалтер",
                    "position": "Бухгалтер",
                    "salary_mode": "salary_plus_percent",
                    "base_salary": "1000",
                    "work_percent": "20",
                    "material_percent": "10",
                }
            )["employee"]
            cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
                "cashbox"
            ]

        patches = self._patch_time(as_of)
        with patches[0], patches[1], patches[2]:
            card = self.service.create_card(
                {
                    "vehicle": "Honda Civic",
                    "title": "Акт сверки зарплаты",
                    "deadline": {"hours": 2},
                }
            )["card"]
            self.service.update_card(
                {
                    "card_id": card["id"],
                    "repair_order": {
                        "number": "302",
                        "status": "open",
                        "vehicle": "Honda Civic",
                        "license_plate": "А123ВС124",
                        "payments": [
                            {
                                "amount": "13000",
                                "paid_at": "28.05.2026 10:00",
                                "payment_method": "cash",
                            }
                        ],
                        "works": [
                            {
                                "name": "Замена генератора",
                                "quantity": "1",
                                "price": "10000",
                                "executor_id": employee["id"],
                            }
                        ],
                        "materials": [
                            {
                                "name": "Фильтр салона",
                                "quantity": "1",
                                "price": "3000",
                                "cost_price": "2000",
                                "executor_id": employee["id"],
                            }
                        ],
                    },
                }
            )
            self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
            self.service.create_employee_salary_transaction(
                {
                    "employee_id": employee["id"],
                    "transaction_kind": "salary_payout",
                    "amount": "700",
                    "cashbox_id": cashbox["id"],
                    "actor_name": "ADMIN",
                    "note": "Выплата за период",
                }
            )
            self.service.create_employee_salary_transaction(
                {
                    "employee_id": employee["id"],
                    "transaction_kind": "salary_advance",
                    "amount": "300",
                    "cashbox_id": cashbox["id"],
                    "actor_name": "ADMIN",
                    "note": "Аванс за период",
                }
            )
            report = self.service.get_employee_salary_reconciliation(
                {"employee_id": employee["id"]}
            )

        self.assertEqual(report["meta"]["schema_version"], "employee_salary_reconciliation.v1")
        self.assertEqual(report["employee"]["id"], employee["id"])
        self.assertEqual(report["employee"]["position"], "Бухгалтер")
        self.assertEqual(report["period"]["days"], 30)
        self.assertEqual(report["totals"]["accrued_total"], "6100")
        self.assertEqual(report["totals"]["payout_total"], "700")
        self.assertEqual(report["totals"]["advance_total"], "300")
        self.assertEqual(report["totals"]["amount_due_total"], "5100")

        row_kinds = [row["kind"] for row in report["rows"]]
        self.assertEqual(row_kinds.count("base_salary_accrual"), 4)
        self.assertIn("work_accrual", row_kinds)
        self.assertIn("material_accrual", row_kinds)
        self.assertIn("salary_payout", row_kinds)
        self.assertIn("salary_advance", row_kinds)
        self.assertEqual([row["number"] for row in report["rows"]], list(range(1, 9)))
        self.assertEqual(
            [row["date_iso"] for row in report["rows"]],
            sorted(row["date_iso"] for row in report["rows"]),
        )

        work_row = next(row for row in report["rows"] if row["kind"] == "work_accrual")
        self.assertEqual(work_row["repair_order_number"], "302")
        self.assertEqual(work_row["vehicle"], "Honda Civic")
        self.assertEqual(work_row["license_plate"], "а123вс124")
        self.assertEqual(work_row["item"], "Замена генератора")
        self.assertIn("10 000,00 ₽", work_row["calculation_base"])
        self.assertIn("20", work_row["scheme"])
        self.assertEqual(work_row["accrued"], "2000")

        material_row = next(row for row in report["rows"] if row["kind"] == "material_accrual")
        self.assertEqual(material_row["item"], "Фильтр салона")
        self.assertIn("1 000,00 ₽", material_row["calculation_base"])
        self.assertIn("10", material_row["scheme"])
        self.assertEqual(material_row["accrued"], "100")

        payout_row = next(row for row in report["rows"] if row["kind"] == "salary_payout")
        advance_row = next(row for row in report["rows"] if row["kind"] == "salary_advance")
        self.assertEqual(payout_row["payment"], "700")
        self.assertEqual(advance_row["payment"], "300")
        self.assertEqual(payout_row["note"], "Выплата за период")
        self.assertEqual(advance_row["note"], "Аванс за период")

    def test_employee_salary_reconciliation_ignores_rows_outside_30_days(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Старый Мастер",
                "position": "Мастер",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "10",
            }
        )["employee"]
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]

        old_time = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
        old_patches = self._patch_time(old_time)
        with old_patches[0], old_patches[1], old_patches[2]:
            card = self.service.create_card(
                {"vehicle": "Skoda Octavia", "title": "Старый ЗН", "deadline": {"hours": 2}}
            )["card"]
            self.service.update_card(
                {
                    "card_id": card["id"],
                    "repair_order": {
                        "number": "901",
                        "status": "open",
                        "vehicle": "Skoda Octavia",
                        "payments": [
                            {
                                "amount": "10000",
                                "paid_at": "20.04.2026 10:00",
                                "payment_method": "cash",
                            }
                        ],
                        "works": [
                            {
                                "name": "Старая работа",
                                "quantity": "1",
                                "price": "10000",
                                "executor_id": employee["id"],
                            }
                        ],
                    },
                }
            )
            self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
            self.service.create_employee_salary_transaction(
                {
                    "employee_id": employee["id"],
                    "transaction_kind": "salary_payout",
                    "amount": "500",
                    "cashbox_id": cashbox["id"],
                    "actor_name": "ADMIN",
                }
            )
        bundle = self.service._store.read_bundle()
        for stored_card in bundle["cards"]:
            if stored_card.id == card["id"]:
                stored_card.repair_order.closed_at = "20.04.2026 19:00"
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

        as_of = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(as_of)
        with patches[0], patches[1], patches[2]:
            report = self.service.get_employee_salary_reconciliation(
                {"employee_id": employee["id"]}
            )

        self.assertEqual(report["rows"], [])
        self.assertEqual(report["totals"]["accrued_total"], "0")
        self.assertEqual(report["totals"]["payout_total"], "0")
        self.assertEqual(report["totals"]["advance_total"], "0")
        self.assertEqual(report["totals"]["amount_due_total"], "0")

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

    def test_employee_delete_rejects_employee_with_payroll_references(self) -> None:
        employee = self.service.save_employee(
            {"name": "Олег Мастер", "position": "Механик", "work_percent": "30"}
        )["employee"]
        cashbox = self.service.create_cashbox({"name": "Зарплатная касса"})["cashbox"]
        self.service.create_employee_salary_transaction(
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount": "1000",
                "cashbox_id": cashbox["id"],
            }
        )
        self.service.create_employee_shift_accrual(
            {"employee_id": employee["id"], "amount": "2000"}
        )
        card = self.service.create_card(
            {
                "vehicle": "Toyota Camry",
                "title": "Ссылки на сотрудника",
                "deadline": {"hours": 2},
            }
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "501",
                    "status": "open",
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee["id"],
                        }
                    ],
                    "materials": [
                        {
                            "name": "Фильтр",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            }
        )

        with self.assertRaises(ServiceError) as raised:
            self.service.delete_employee({"employee_id": employee["id"], "actor_name": "ADMIN"})

        self.assertEqual(raised.exception.code, "validation_error")
        self.assertIn("нельзя удалить", raised.exception.message)
        self.assertEqual(
            raised.exception.details["usage"],
            {
                "repair_order_works": 1,
                "repair_order_materials": 1,
                "salary_transactions": 1,
                "shift_accruals": 1,
            },
        )
        listed_after = self.service.list_employees()
        self.assertTrue(any(item["id"] == employee["id"] for item in listed_after["employees"]))

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

    def test_employee_can_be_saved_without_accrual_rules(self) -> None:
        employee = self.service.save_employee(
            {
                "name": "Без начислений",
                "position": "Стажёр",
                "salary_mode": "none",
                "base_salary": "",
                "work_percent": "",
                "material_percent": "0",
            }
        )["employee"]

        self.assertEqual(employee["salary_mode"], "none")
        self.assertEqual(employee["base_salary"], "0")
        self.assertEqual(employee["work_percent"], "0")
        self.assertEqual(employee["material_percent"], "0")

        listed = self.service.list_employees()["employees"]
        listed_employee = next(item for item in listed if item["id"] == employee["id"])
        self.assertEqual(listed_employee["salary_mode"], "none")
        self.assertEqual(listed_employee["balance_total"], "0")

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

    def test_employee_toggle_closes_legacy_active_period_at_toggle_time(self) -> None:
        created_at = datetime(2026, 5, 4, 3, 0, tzinfo=timezone.utc)
        stale_updated_at = datetime(2026, 5, 5, 3, 0, tzinfo=timezone.utc)
        deactivated_at = datetime(2026, 5, 20, 3, 0, tzinfo=timezone.utc)
        ledger_at = datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc)

        patches = self._patch_time(created_at)
        with patches[0], patches[1], patches[2]:
            employee = self.service.save_employee(
                {
                    "name": "Legacy оклад",
                    "position": "Мастер",
                    "salary_mode": "salary_only",
                    "base_salary": "1000",
                }
            )["employee"]

        bundle = self.store.read_bundle()
        for item in bundle["settings"]["employees"]:
            if item["id"] == employee["id"]:
                item.pop("active_periods", None)
                item["updated_at"] = stale_updated_at.isoformat()
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        patches = self._patch_time(deactivated_at)
        with patches[0], patches[1], patches[2]:
            toggled_off = self.service.toggle_employee(
                {"employee_id": employee["id"], "actor_name": "ADMIN"}
            )["employee"]

        self.assertEqual(toggled_off["active_periods"][-1]["start_at"], created_at.isoformat())
        self.assertEqual(toggled_off["active_periods"][-1]["end_at"], deactivated_at.isoformat())

        patches = self._patch_time(ledger_at)
        with patches[0], patches[1], patches[2]:
            ledger = self.service.get_employee_salary_ledger(
                {"employee_id": employee["id"], "months": 1}
            )

        self.assertEqual(ledger["accrued_total"], "2000")
        self.assertEqual(
            sum(1 for row in ledger["journal_rows"] if row["kind"] == "base_salary_accrual"),
            2,
        )

    def test_weekly_base_salary_stops_during_employee_inactive_period(self) -> None:
        created_at = datetime(2026, 5, 4, 3, 0, tzinfo=timezone.utc)
        first_friday_after_accrual = datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc)
        deactivated_at = datetime(2026, 5, 9, 3, 0, tzinfo=timezone.utc)
        inactive_friday_after_accrual = datetime(2026, 5, 15, 14, 0, tzinfo=timezone.utc)
        reactivated_at = datetime(2026, 5, 16, 3, 0, tzinfo=timezone.utc)
        reactivated_friday_after_accrual = datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc)

        patches = self._patch_time(created_at)
        with patches[0], patches[1], patches[2]:
            employee = self.service.save_employee(
                {
                    "name": "Окладный мастер",
                    "position": "Мастер",
                    "salary_mode": "salary_only",
                    "base_salary": "1000",
                }
            )["employee"]

        patches = self._patch_time(first_friday_after_accrual)
        with patches[0], patches[1], patches[2]:
            active_ledger = self.service.get_employee_salary_ledger(
                {"employee_id": employee["id"], "months": 1}
            )

        self.assertEqual(active_ledger["accrued_total"], "1000")
        self.assertEqual(
            sum(1 for row in active_ledger["journal_rows"] if row["kind"] == "base_salary_accrual"),
            1,
        )

        patches = self._patch_time(deactivated_at)
        with patches[0], patches[1], patches[2]:
            toggled_off = self.service.toggle_employee(
                {"employee_id": employee["id"], "actor_name": "ADMIN"}
            )["employee"]

        self.assertFalse(toggled_off["is_active"])
        self.assertEqual(toggled_off["active_periods"][-1]["end_at"], deactivated_at.isoformat())

        patches = self._patch_time(inactive_friday_after_accrual)
        with patches[0], patches[1], patches[2]:
            inactive_ledger = self.service.get_employee_salary_ledger(
                {"employee_id": employee["id"], "months": 1}
            )

        self.assertEqual(inactive_ledger["accrued_total"], "1000")
        self.assertEqual(
            sum(
                1 for row in inactive_ledger["journal_rows"] if row["kind"] == "base_salary_accrual"
            ),
            1,
        )

        patches = self._patch_time(reactivated_at)
        with patches[0], patches[1], patches[2]:
            toggled_on = self.service.toggle_employee(
                {"employee_id": employee["id"], "actor_name": "ADMIN"}
            )["employee"]

        self.assertTrue(toggled_on["is_active"])
        self.assertEqual(toggled_on["active_periods"][-1]["start_at"], reactivated_at.isoformat())
        self.assertEqual(toggled_on["active_periods"][-1]["end_at"], "")

        patches = self._patch_time(reactivated_friday_after_accrual)
        with patches[0], patches[1], patches[2]:
            reactivated_ledger = self.service.get_employee_salary_ledger(
                {"employee_id": employee["id"], "months": 1}
            )

        self.assertEqual(reactivated_ledger["accrued_total"], "2000")
        self.assertEqual(
            sum(
                1
                for row in reactivated_ledger["journal_rows"]
                if row["kind"] == "base_salary_accrual"
            ),
            2,
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
                "note": "Расходник цеха",
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
        self.assertEqual(details["transactions"][0]["note"], "Расходник цеха")

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

    def test_manual_cashbox_expense_requires_note_minimum(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]

        allowed_income = self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "100",
                "note": "",
                "actor_name": "ADMIN",
            }
        )
        self.assertEqual(allowed_income["transaction"]["direction"], "income")

        for note in ("", "Расход"):
            with self.subTest(note=note):
                with self.assertRaises(ServiceError) as blocked:
                    self.service.create_cash_transaction(
                        {
                            "cashbox_id": cashbox["id"],
                            "direction": "expense",
                            "amount": "100",
                            "note": note,
                            "actor_name": "ADMIN",
                        }
                    )
                self.assertEqual(blocked.exception.code, "validation_error")
                self.assertEqual(blocked.exception.details["field"], "note")
                self.assertEqual(blocked.exception.details["min_length"], 10)

        allowed_expense = self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "expense",
                "amount": "100",
                "note": "Покупка масла",
                "actor_name": "ADMIN",
            }
        )
        self.assertEqual(allowed_expense["transaction"]["note"], "Покупка масла")

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
        self.assertTrue(transferred["source_transaction"]["transfer_group_id"])
        self.assertEqual(
            transferred["source_transaction"]["transfer_group_id"],
            transferred["target_transaction"]["transfer_group_id"],
        )
        self.assertEqual(
            transferred["source_transaction"]["related_transaction_id"],
            transferred["target_transaction"]["id"],
        )
        self.assertEqual(
            transferred["target_transaction"]["related_transaction_id"],
            transferred["source_transaction"]["id"],
        )

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

        cancelled = self.service.cancel_last_cash_transaction(
            {
                "cashbox_id": target_cashbox["id"],
                "transaction_id": transferred["target_transaction"]["id"],
                "actor_name": "ADMIN",
            }
        )
        self.assertTrue(cancelled["meta"]["cancelled_pair"])
        source_after = self.service.get_cashbox(
            {"cashbox_id": source_cashbox["id"], "transaction_limit": 10}
        )
        target_after = self.service.get_cashbox(
            {"cashbox_id": target_cashbox["id"], "transaction_limit": 10}
        )
        self.assertEqual(source_after["cashbox"]["statistics"]["balance_minor"], 100000)
        self.assertEqual(target_after["cashbox"]["statistics"]["balance_minor"], 0)

    def test_manual_cash_transaction_cannot_impersonate_repair_order_payment(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]

        with self.assertRaises(ServiceError) as blocked:
            self.service.create_cash_transaction(
                {
                    "cashbox_id": cashbox["id"],
                    "direction": "income",
                    "amount": "4000",
                    "note": "Заказ-наряд №214",
                    "actor_name": "ADMIN",
                }
            )

        self.assertEqual(blocked.exception.code, "manual_repair_order_cash_note_blocked")

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
                "note": "Расход по кассе",
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

    def test_get_cashbox_paginates_transactions_with_stable_order(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        for index in range(5):
            self.service.create_cash_transaction(
                {
                    "cashbox_id": cashbox["id"],
                    "direction": "income",
                    "amount": str(100 + index),
                    "note": f"Операция {index}",
                    "actor_name": "ADMIN",
                }
            )

        first_page = self.service.get_cashbox({"cashbox_id": cashbox["id"], "transaction_limit": 2})
        second_page = self.service.get_cashbox(
            {"cashbox_id": cashbox["id"], "transaction_limit": 2, "transaction_offset": 2}
        )
        last_page = self.service.get_cashbox(
            {"cashbox_id": cashbox["id"], "transaction_limit": 2, "transaction_offset": 4}
        )

        self.assertEqual(first_page["meta"]["transactions_total"], 5)
        self.assertEqual(first_page["meta"]["transaction_offset"], 0)
        self.assertEqual(first_page["meta"]["transactions_returned"], 2)
        self.assertTrue(first_page["meta"]["has_more"])
        self.assertEqual(second_page["meta"]["transaction_offset"], 2)
        self.assertTrue(second_page["meta"]["has_more"])
        self.assertEqual(last_page["meta"]["transaction_offset"], 4)
        self.assertFalse(last_page["meta"]["has_more"])
        self.assertEqual(len(last_page["transactions"]), 1)
        self.assertTrue(
            {item["id"] for item in first_page["transactions"]}.isdisjoint(
                {item["id"] for item in second_page["transactions"]}
            )
        )

    def test_get_cashbox_compact_transactions_keep_ui_fields_without_verbose_dates(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "expense",
                "amount": "500",
                "note": "Покупка расходников",
                "actor_name": "ADMIN",
            }
        )

        full = self.service.get_cashbox({"cashbox_id": cashbox["id"], "transaction_limit": 1})
        compact = self.service.get_cashbox(
            {"cashbox_id": cashbox["id"], "transaction_limit": 1, "compact": True}
        )

        full_transaction = full["transactions"][0]
        compact_transaction = compact["transactions"][0]
        self.assertFalse(full["meta"]["compact"])
        self.assertTrue(compact["meta"]["compact"])
        for field_name in (
            "id",
            "cashbox_id",
            "direction",
            "amount_minor",
            "amount_display",
            "note",
            "created_at",
            "actor_name",
            "source",
            "business_datetime_display",
            "source_label",
            "link_status",
        ):
            self.assertEqual(compact_transaction[field_name], full_transaction[field_name])
        for verbose_field in (
            "business_datetime",
            "business_date",
            "business_time",
            "created_at_utc",
            "created_at_original",
            "short_id",
            "direction_label",
        ):
            self.assertNotIn(verbose_field, compact_transaction)
        self.assertLess(len(compact_transaction), len(full_transaction))

    def test_cash_journal_returns_structured_markdown_report(self) -> None:
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
        self.assertEqual(journal["meta"]["schema_version"], "cash_journal.v2")
        self.assertEqual(journal["meta"]["returned"], 1)
        self.assertEqual(journal["text"], journal["markdown"])
        self.assertIn("Кассовый журнал", journal["markdown"])
        self.assertIn("Наличный", journal["markdown"])
        self.assertIn("ОПЛАТА КЛИЕНТА", journal["markdown"].upper())
        self.assertIn("+1 000 ₽", journal["markdown"])
        self.assertEqual(journal["entries"][0]["cashbox_name"], "Наличный")
        self.assertEqual(journal["entries"][0]["source_label"], "api")
        self.assertEqual(journal["entries"][0]["signed_amount_minor"], 100000)
        self.assertEqual(journal["totals"]["income_minor"], 100000)
        self.assertEqual(journal["totals"]["balance_minor"], 100000)
        self.assertEqual(journal["days"][0]["count"], 1)
        self.assertEqual(journal["days"][0]["opening_total_minor"], 0)
        self.assertEqual(journal["days"][0]["opening_balances"][0]["cashbox_name"], "Наличный")
        self.assertEqual(journal["days"][0]["opening_balances"][0]["balance_minor"], 0)
        self.assertIn("Остаток на начало дня", journal["markdown"])
        self.assertNotIn("**", journal["markdown"])
        self.assertEqual(journal["weeks"][0]["count"], 1)
        self.assertEqual(journal["months"][0]["count"], 1)

    def test_cash_journal_can_omit_markdown_for_ui_payload(self) -> None:
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

        journal = self.service.get_cash_journal(
            {"months": 3, "limit": 100, "include_markdown": False}
        )

        self.assertNotIn("markdown", journal)
        self.assertNotIn("text", journal)
        self.assertEqual(journal["meta"]["schema_version"], "cash_journal.v2")
        self.assertEqual(journal["meta"]["format"], "json")
        self.assertFalse(journal["meta"]["include_markdown"])
        self.assertEqual(journal["entries"][0]["cashbox_name"], "Наличный")
        self.assertEqual(journal["days"][0]["count"], 1)
        self.assertEqual(journal["totals"]["income_minor"], 100000)

        compact = self.service.get_cash_journal(
            {
                "months": 3,
                "limit": 100,
                "include_markdown": False,
                "compact_groups": True,
            }
        )
        self.assertEqual(len(compact["entries"]), 1)
        self.assertFalse(compact["meta"]["include_markdown"])
        self.assertTrue(compact["meta"]["compact_groups"])
        self.assertNotIn("entries", compact["days"][0])
        self.assertNotIn("entries", compact["weeks"][0])
        self.assertNotIn("entries", compact["months"][0])
        self.assertEqual(compact["days"][0]["opening_total_minor"], 0)

    def test_cash_journal_includes_daily_opening_balances_by_cashbox(self) -> None:
        cashbox_created_at = "2026-04-01T00:00:00+00:00"
        cashbox = CashBox(
            id="cashbox-cash",
            name="Наличный",
            order=0,
            created_at=cashbox_created_at,
            updated_at=cashbox_created_at,
        )
        card_cashbox = CashBox(
            id="cashbox-card",
            name="Карта Мария",
            order=1,
            created_at=cashbox_created_at,
            updated_at=cashbox_created_at,
        )
        transactions = [
            CashTransaction(
                id="tx-1",
                cashbox_id=cashbox.id,
                direction="income",
                amount_minor=100075,
                note="Оплата клиента",
                created_at="2026-04-01T10:00:00+00:00",
                actor_name="ADMIN",
                source="api",
            ),
            CashTransaction(
                id="tx-2",
                cashbox_id=cashbox.id,
                direction="expense",
                amount_minor=25000,
                note="Списание",
                created_at="2026-04-02T09:00:00+00:00",
                actor_name="ADMIN",
                source="api",
            ),
            CashTransaction(
                id="tx-3",
                cashbox_id=card_cashbox.id,
                direction="income",
                amount_minor=50000,
                note="Оплата по карте",
                created_at="2026-04-02T11:00:00+00:00",
                actor_name="ADMIN",
                source="ui",
            ),
        ]

        journal = self.service._build_cash_journal(
            transactions,
            {cashbox.id: cashbox, card_cashbox.id: card_cashbox},
            months=3,
            limit=100,
            total=len(transactions),
            period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            all_transactions=transactions,
            cashboxes=[cashbox, card_cashbox],
        )

        second_day = next(day for day in journal["days"] if day["date"] == "2026-04-02")
        balances = {
            item["cashbox_name"]: item["balance_minor"] for item in second_day["opening_balances"]
        }
        self.assertEqual(balances, {"Наличный": 100075, "Карта Мария": 0})
        self.assertEqual(second_day["opening_total_minor"], 100075)
        self.assertIn("- Наличный: 1 001 ₽", journal["markdown"])
        self.assertIn("- Карта Мария: 0 ₽", journal["markdown"])

    def test_cash_journal_markdown_compacts_transfer_pairs(self) -> None:
        source = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})["cashbox"]
        target = self.service.create_cashbox({"name": "Карта Мария", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        self.service.create_cashbox_transfer(
            {
                "from_cashbox_id": source["id"],
                "to_cashbox_id": target["id"],
                "amount": "3000",
                "actor_name": "MARIA",
            }
        )

        journal = self.service.get_cash_journal({"months": 3, "limit": 100})

        self.assertIn("Наличный → Карта Мария", journal["markdown"])
        self.assertIn("Внутренние перемещения: пришло 3 000 ₽ | ушло 3 000 ₽", journal["markdown"])
        self.assertNotIn("`", journal["markdown"])
        self.assertEqual(journal["totals"]["transfer_income_minor"], 300000)
        self.assertEqual(journal["totals"]["transfer_expense_minor"], 300000)

    def test_finance_read_core_preserves_cashbox_and_audit_facades(self) -> None:
        self.assertIsInstance(self.service._finance_read_core, FinanceReadCore)
        cashbox = self.service.create_cashbox({"name": "Основная касса"})["cashbox"]
        self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "1200",
                "note": "Тестовая операция",
            }
        )

        self.assertEqual(
            self.service.list_cashboxes({"limit": 20}),
            self.service._finance_read_core.list_cashboxes({"limit": 20}),
        )
        self.assertEqual(
            self.service.get_cashbox({"cashbox_id": cashbox["id"], "transaction_limit": 5}),
            self.service._finance_read_core.get_cashbox(
                {"cashbox_id": cashbox["id"], "transaction_limit": 5}
            ),
        )
        facade_journal = self.service.get_cash_journal({"months": 3, "limit": 100})
        core_journal = self.service._finance_read_core.get_cash_journal({"months": 3, "limit": 100})
        facade_journal["meta"]["period_start"] = ""
        core_journal["meta"]["period_start"] = ""
        self.assertEqual(facade_journal, core_journal)

        facade_audit = self.service.get_finance_audit()
        core_audit = self.service._finance_read_core.get_finance_audit()
        facade_audit["meta"]["generated_at"] = ""
        core_audit["meta"]["generated_at"] = ""
        self.assertEqual(facade_audit, core_audit)

    def test_repair_order_number_audit_service_is_read_only_and_reports_context(self) -> None:
        card = self.service.create_card(
            {"vehicle": "Skoda Rapid", "title": "Аудит номеров", "deadline": {"hours": 2}}
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "10",
                    "client": "Клиент",
                    "works": [{"name": "Работа", "quantity": "1", "price": "1000"}],
                },
            }
        )
        bundle = self.store.read_bundle()
        stored_card = next(item for item in bundle["cards"] if item.id == card["id"])
        stored_card.repair_order.number = ""
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        before = self.state_file.read_text(encoding="utf-8")
        audit = self.service.get_repair_order_number_audit()

        self.assertTrue(audit["meta"]["read_only"])
        self.assertTrue(audit["meta"]["dry_run"])
        self.assertEqual(audit["summary"]["safe_fix_count"], 0)
        issue = next(issue for issue in audit["issues"] if issue["code"] == "missing_number")
        self.assertEqual(issue["card_id"], card["id"])
        self.assertFalse(issue["safe_fix_available"])
        self.assertEqual(self.state_file.read_text(encoding="utf-8"), before)

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

    def test_ready_column_is_system_locked_but_can_move(self) -> None:
        columns = self.service.list_columns()["columns"]
        ready_column = next(column for column in columns if column["label"] == "Готовые автомобили")

        with self.assertRaises(ServiceError) as rename_error:
            self.service.rename_column(
                {"column_id": ready_column["id"], "label": "ГОТОВО К ВЫДАЧЕ"}
            )
        self.assertEqual(rename_error.exception.code, "system_column_locked")

        with self.assertRaises(ServiceError) as delete_error:
            self.service.delete_column({"column_id": ready_column["id"]})
        self.assertEqual(delete_error.exception.code, "system_column_locked")

        moved = self.service.move_column(
            {"column_id": ready_column["id"], "before_column_id": "inbox"}
        )
        self.assertTrue(moved["meta"]["changed"])
        self.assertEqual(moved["columns"][0]["id"], ready_column["id"])

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

    def test_board_snapshot_accepts_zero_archive_limit_when_archive_disabled(self) -> None:
        snapshot = self.service.get_board_snapshot(
            {"include_archive": False, "archive_limit": 0, "compact": True}
        )

        self.assertEqual(snapshot["meta"]["archive_limit"], 0)
        self.assertFalse(snapshot["meta"]["include_archive"])
        self.assertEqual(snapshot["archive"], [])

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

    def test_card_description_preview_strips_minimal_formatting_markers(self) -> None:
        formatted_description = (
            "Проверить **подвеску**, *руль* и ++датчик ABS++.\n"
            "Комментарий Codex: ✅ оставить полный текст."
        )
        created = self.service.create_card(
            {
                "vehicle": "FORD FOCUS",
                "title": "Formatting preview",
                "description": formatted_description,
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        full_card = self.service.get_card({"card_id": card_id})["card"]
        snapshot = self.service.get_board_snapshot({"compact": True})
        compact_card = next(card for card in snapshot["cards"] if card["id"] == card_id)

        self.assertEqual(full_card["description"], formatted_description)
        self.assertNotIn("**", compact_card["description_preview"])
        self.assertNotIn("*руль*", compact_card["description_preview"])
        self.assertNotIn("++", compact_card["description_preview"])
        self.assertIn("подвеску", compact_card["description_preview"])
        self.assertIn("руль", compact_card["description_preview"])
        self.assertIn("датчик ABS", compact_card["description_preview"])
        self.assertIn("✅", compact_card["description_preview"])
        self.assertEqual(compact_card["description"], compact_card["description_preview"])

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

    def test_board_revision_matches_snapshot_revision_without_card_payload(self) -> None:
        self.service.create_card(
            {
                "vehicle": "Ford Focus",
                "title": "Revision probe",
                "description": "Проверить развал-схождение",
                "deadline": {"hours": 2},
            }
        )

        snapshot = self.service.get_board_snapshot({"compact": True, "include_archive": False})
        revision = self.service.get_board_revision({"compact": True, "include_archive": False})

        self.assertEqual(revision["revision"], snapshot["meta"]["revision"])
        self.assertEqual(revision["meta"]["revision"], snapshot["meta"]["revision"])
        self.assertEqual(revision["counts"]["cards"], len(snapshot["cards"]))
        self.assertNotIn("cards", revision)
        self.assertNotIn("archive", revision)

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

    def test_store_retains_archived_repair_orders_beyond_plain_archive_limit(self) -> None:
        with patch("minimal_kanban.storage.json_store.ARCHIVED_CARD_RETENTION_LIMIT", 1):
            plain_ids: list[str] = []
            for index in range(2):
                created = self.service.create_card(
                    {
                        "title": f"PLAIN ARCHIVE {index}",
                        "deadline": {"hours": 1},
                    }
                )
                self.service.archive_card({"card_id": created["card"]["id"]})
                plain_ids.append(created["card"]["id"])

            repair_order_ids: list[str] = []
            for index in range(2):
                created = self.service.create_card(
                    {
                        "vehicle": f"REPAIR ARCHIVE {index}",
                        "title": f"Repair archive {index}",
                        "deadline": {"hours": 1},
                    }
                )
                card_id = created["card"]["id"]
                self.service.update_card(
                    {
                        "card_id": card_id,
                        "repair_order": {
                            "works": [
                                {
                                    "name": "Сохранить историю заказ-наряда",
                                    "quantity": "1",
                                    "price": "1000",
                                }
                            ],
                            "payments": [
                                {
                                    "amount": "1000",
                                    "paid_at": "29.05.2026 12:00",
                                    "payment_method": "cash",
                                }
                            ],
                        },
                    }
                )
                self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})
                self.service.archive_card({"card_id": card_id})
                repair_order_ids.append(card_id)

        archived_cards = [card for card in self.store.read_bundle()["cards"] if card.archived]
        archived_ids = {card.id for card in archived_cards}

        self.assertEqual(len(archived_cards), 3)
        self.assertIn(plain_ids[-1], archived_ids)
        self.assertNotIn(plain_ids[0], archived_ids)
        self.assertTrue(set(repair_order_ids).issubset(archived_ids))

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
        for doomed_id in ["control", "in_progress"]:
            deleted = self.service.delete_column({"column_id": doomed_id})
            self.assertTrue(all(column["id"] != doomed_id for column in deleted["columns"]))

        with self.assertRaises(ServiceError) as ready_column_error:
            self.service.delete_column({"column_id": "done"})
        self.assertEqual(ready_column_error.exception.code, "system_column_locked")

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

    def test_set_card_board_summary_updates_hidden_board_preview_and_staleness(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "AUDI A4",
                "title": "PCV И КОЛОДКИ",
                "description": "VIN: WAUZZZ8K9DA123456\nДвигатель: EA888\nТехнический текст\nПроверить PCV",
                "deadline": {"hours": 2},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        summary = "\n".join(
            [
                "Что сейчас: проверить жалобу по PCV и тормозам.",
                "Стадия: диагностика, нужна проверка причины.",
                "Следующее действие: подтвердить неисправность и согласовать работы.",
                "Важно: не показывать VIN и лишние техданные на доске.",
            ]
        )

        updated = self.service.set_card_board_summary(
            {
                "card_id": card_id,
                "summary": summary,
                "actor_name": "AI",
                "source": "mcp",
            }
        )
        card = updated["card"]

        self.assertEqual(card["board_summary"], summary)
        self.assertFalse(card["board_summary_stale"])
        self.assertEqual(card["board_summary_source"], "mcp")
        self.assertTrue(card["board_summary_updated_at"])
        self.assertTrue(card["board_summary_card_fingerprint"])
        self.assertEqual(card["description"], created["card"]["description"])

        compact_card = next(
            card
            for card in self.service.get_cards({"compact": True})["cards"]
            if card["id"] == card_id
        )
        self.assertEqual(compact_card["board_summary"], summary)
        self.assertFalse(compact_card["board_summary_stale"])
        self.assertIn("VIN", compact_card["description"])

        self.service.update_card(
            {
                "card_id": card_id,
                "description": "Обновили внутреннее описание после summary",
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        stale_card = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(stale_card["board_summary"], summary)
        self.assertTrue(stale_card["board_summary_stale"])

        refreshed = self.service.set_card_board_summary(
            {
                "card_id": card_id,
                "summary": summary,
                "actor_name": "AI",
                "source": "mcp",
            }
        )
        self.assertTrue(refreshed["meta"]["changed"])
        self.assertFalse(refreshed["card"]["board_summary_stale"])

        log = self.service.get_card_log({"card_id": card_id})
        summary_entry = next(
            item for item in log["entries"] if item["action"] == "board_summary_changed"
        )
        self.assertEqual(summary_entry["action_label"], "Обновлена краткая суть для доски")
        self.assertEqual(summary_entry["source_label"], "MCP/GPT")
        self.assertEqual(summary_entry["changes"][0]["field"], "board_summary")
        self.assertIn("Что сейчас", summary_entry["changes"][0]["after"])

    def test_set_card_board_summary_rejects_more_than_five_lines(self) -> None:
        created = self.service.create_card({"title": "Лимит summary", "deadline": {"hours": 2}})
        too_many_lines = "\n".join([f"Строка {index}" for index in range(1, 7)])

        with self.assertRaises(ServiceError) as ctx:
            self.service.set_card_board_summary(
                {"card_id": created["card"]["id"], "summary": too_many_lines}
            )

        self.assertEqual(ctx.exception.code, "validation_error")
        self.assertIn("5 строк", ctx.exception.message)

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
        self.assertIn("Для поиска нужно передать query", empty_search.exception.message)

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

        self.assertEqual(log["meta"]["schema_version"], "card_journal.v2")
        self.assertEqual(log["meta"]["limit"], 2)
        self.assertEqual(log["meta"]["events_returned"], 2)
        self.assertGreaterEqual(log["meta"]["events_total"], 3)
        self.assertTrue(log["meta"]["has_more"])
        self.assertEqual(log["meta"]["event_order"], "newest_first")
        self.assertEqual(len(log["events"]), 2)
        self.assertEqual(len(log["entries"]), 2)
        self.assertGreaterEqual(len(log["days"]), 1)
        self.assertGreaterEqual(len(log["weeks"]), 1)
        self.assertGreaterEqual(len(log["months"]), 1)
        self.assertEqual(log["timeline"], log["entries"])
        self.assertIn("markdown", log)
        self.assertIn("text", log)
        self.assertEqual(log["text"], log["markdown"])
        self.assertTrue(log["markdown"].startswith("# 🧾 Журнал карточки"))
        self.assertIn("## 📊 Итоги карточки", log["markdown"])
        self.assertIn("## 🗓️ По месяцам", log["markdown"])
        self.assertIn("## 📅 По неделям", log["markdown"])
        self.assertIn("## 🧾 События по дням", log["markdown"])
        self.assertNotIn("ID:", log["markdown"])
        self.assertNotIn("inbox", log["markdown"])
        self.assertNotIn("событ.", log["markdown"])
        self.assertNotIn("изм.", log["markdown"])
        self.assertNotIn("участн.", log["markdown"])
        self.assertNotIn("T", log["markdown"].split("## 🗓️ По месяцам", 1)[0])
        self.assertEqual(log["entries"][0]["schema_version"], "card_journal.entry.v2")
        self.assertIn("display_line", log["entries"][0])
        self.assertIn("detail_lines", log["entries"][0])
        self.assertIn("journal_blocks", log["entries"][0])

        compact_log = self.service.get_card_log({"card_id": card_id, "compact": True, "limit": 2})

        self.assertEqual(compact_log["meta"]["schema_version"], "card_journal.v2")
        self.assertTrue(compact_log["meta"]["compact"])
        self.assertEqual(compact_log["meta"]["format"], "json_compact")
        self.assertEqual(compact_log["meta"]["limit"], 2)
        self.assertEqual(compact_log["meta"]["events_returned"], 2)
        self.assertEqual(len(compact_log["entries"]), 2)
        self.assertEqual(compact_log["timeline"], compact_log["entries"])
        self.assertIn("days", compact_log)
        self.assertIn("totals", compact_log)
        self.assertNotIn("events", compact_log)
        self.assertNotIn("markdown", compact_log)
        self.assertNotIn("text", compact_log)

    def test_get_card_log_compact_defaults_to_50_and_truncates_heavy_values(self) -> None:
        original_description = "Исходная строка. " * 120
        updated_description = "Обновленная длинная строка журнала. " * 120
        created = self.service.create_card(
            {
                "title": "КОМПАКТНЫЙ ЖУРНАЛ",
                "description": original_description,
                "deadline": {"hours": 2},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "description": updated_description,
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )

        compact_log = self.service.get_card_log({"card_id": card_id, "compact": True})
        entry = compact_log["entries"][0]
        block = entry["journal_blocks"][0]

        self.assertEqual(compact_log["meta"]["limit"], 50)
        self.assertTrue(compact_log["meta"]["compact"])
        self.assertLessEqual(len(block["text"]), 1200)
        raw_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        raw_event = next(
            item for item in raw_state["events"] if item["action"] == "description_changed"
        )
        self.assertTrue(raw_event["details"]["full_details_archived"])
        self.assertNotIn("before", raw_event["details"])
        self.assertNotIn("after", raw_event["details"])
        self.assertNotIn("published_text", entry)
        self.assertNotIn("published_blocks", entry)
        self.assertNotIn("details_text", entry)
        self.assertNotIn("entries", compact_log["days"][0])
        self.assertNotIn("events", compact_log)

    def test_heavy_description_event_archives_full_details_and_hydrates_on_request(
        self,
    ) -> None:
        original_description = "Исходная строка. " * 80
        updated_description = "Новая строка. " * 80
        created = self.service.create_card(
            {
                "title": "АРХИВ АУДИТА",
                "description": original_description,
                "deadline": {"hours": 2},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "description": updated_description,
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )

        raw_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        event = next(
            item for item in raw_state["events"] if item["action"] == "description_changed"
        )
        details = event["details"]

        self.assertTrue(details["full_details_archived"])
        self.assertNotIn("before", details)
        self.assertNotIn("after", details)
        archive_ref = details["full_details_ref"]
        archive_file = self.state_file.parent / "audit-archive" / archive_ref.split("#", 1)[0]
        self.assertTrue(archive_file.exists())

        default_log = self.service.get_card_log({"card_id": card_id, "limit": 1})
        default_entry = default_log["entries"][0]
        self.assertTrue(default_entry["details"]["full_details_archived"])
        self.assertNotIn("before", default_entry["details"])
        self.assertNotIn("after", default_entry["details"])
        self.assertIn("Новая строка", default_entry["journal_blocks"][0]["text"])

        full_log = self.service.get_card_log(
            {"card_id": card_id, "limit": 1, "include_full_details": True}
        )
        full_entry = full_log["entries"][0]
        self.assertEqual(full_entry["details"]["before"], original_description.strip())
        self.assertEqual(full_entry["details"]["after"], updated_description.strip())
        self.assertEqual(full_log["meta"]["include_full_details"], True)

    def test_get_card_log_exposes_full_before_after_changes(self) -> None:
        created = self.service.create_card(
            {
                "title": "ЖУРНАЛ ИЗМЕНЕНИЙ",
                "description": "Первая строка\nВторая строка с важной информацией",
                "deadline": {"hours": 2},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "description": "Новая строка\nВторая строка заменена",
                "actor_name": "ПРИЁМЩИК",
                "source": "ui",
            }
        )

        full_log = self.service.get_card_log({"card_id": card_id})
        created_entry = next(
            item for item in full_log["entries"] if item["action"] == "card_created"
        )
        created_description = next(
            block for block in created_entry["journal_blocks"] if block["field"] == "description"
        )
        self.assertEqual(
            created_description["text"], "Первая строка\nВторая строка с важной информацией"
        )

        log = self.service.get_card_log(
            {"card_id": card_id, "limit": 1, "include_full_details": True}
        )
        entry = next(item for item in log["entries"] if item["action"] == "description_changed")

        self.assertEqual(entry["icon"], "📝")
        self.assertEqual(entry["action_label"], "Изменено описание")
        self.assertEqual(entry["source_label"], "интерфейс")
        self.assertEqual(entry["change_count"], 1)
        self.assertFalse(entry["has_deletion"])
        self.assertEqual(len(entry["changes"]), 1)
        change = entry["changes"][0]
        self.assertEqual(change["field"], "description")
        self.assertEqual(change["label"], "Описание")
        self.assertEqual(change["schema_version"], "card_journal.change.v2")
        self.assertEqual(change["before"], "Первая строка\nВторая строка с важной информацией")
        self.assertEqual(change["after"], "Новая строка\nВторая строка заменена")
        self.assertEqual(len(entry["journal_blocks"]), 1)
        self.assertEqual(entry["journal_blocks"][0]["title"], "Описание обновлено")
        self.assertEqual(entry["journal_blocks"][0]["text"], "Новая строка\nВторая строка заменена")
        self.assertTrue(entry["journal_blocks"][0]["is_full_value"])
        self.assertIn("📝", log["markdown"])
        self.assertIn("Описание обновлено", log["markdown"])
        self.assertNotIn("Изменено поле", log["markdown"])
        self.assertNotIn("до:", log["markdown"])
        self.assertNotIn("после:", log["markdown"])
        self.assertNotIn("Первая строка", log["markdown"])
        self.assertNotIn("Вторая строка с важной информацией", log["markdown"])
        self.assertIn("Новая строка", log["markdown"])
        self.assertIn("Вторая строка заменена", log["markdown"])
        self.assertIn("ПРИЁМЩИК", entry["display_line"])
        self.assertFalse(any("до:" in line or "после:" in line for line in entry["detail_lines"]))

    def test_get_card_log_marks_cleared_fields_as_deletions(self) -> None:
        created = self.service.create_card(
            {
                "title": "ЖУРНАЛ УДАЛЕНИЯ",
                "description": "Текст, который нельзя потерять",
                "deadline": {"hours": 2},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "description": "",
                "actor_name": "GPT",
                "source": "mcp",
            }
        )

        log = self.service.get_card_log({"card_id": card_id, "limit": 1})
        entry = next(item for item in log["entries"] if item["action"] == "description_changed")

        self.assertTrue(entry["has_deletion"])
        self.assertEqual(entry["source_label"], "MCP/GPT")
        self.assertEqual(entry["changes"][0]["kind"], "removed")
        self.assertEqual(entry["changes"][0]["before"], "Текст, который нельзя потерять")
        self.assertEqual(entry["changes"][0]["after"], "")
        self.assertEqual(entry["journal_blocks"][0]["title"], "⚠️ Описание очищено")
        self.assertEqual(entry["journal_blocks"][0]["text"], "")
        self.assertGreaterEqual(log["totals"]["deletions"], 1)
        self.assertIn("⚠️ Описание очищено", log["markdown"])
        self.assertNotIn("Очищено поле", log["markdown"])
        self.assertNotIn("Текст, который нельзя потерять", log["text"])

    def test_repair_order_updates_keep_previous_snapshot_in_card_log(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TOYOTA CAMRY",
                "title": "ЗАКАЗ-НАРЯД ЖУРНАЛ",
                "description": "Проверка заказ-наряда",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "reason": "Первичная причина",
                    "works": [{"name": "Диагностика", "qty": "1", "price": "1000"}],
                },
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "reason": "Причина изменена",
                    "works": [],
                },
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )

        log = self.service.get_card_log({"card_id": card_id, "include_full_details": True})
        entry = next(item for item in log["entries"] if item["action"] == "repair_order_updated")
        repair_order_change = next(
            change for change in entry["changes"] if change["field"] == "repair_order"
        )

        self.assertEqual(repair_order_change["label"], "Заказ-наряд")
        self.assertIn("Первичная причина", repair_order_change["before"])
        self.assertIn("Диагностика", repair_order_change["before"])
        self.assertIn("Причина изменена", repair_order_change["after"])
        self.assertIn("Диагностика", log["markdown"])
        self.assertIn("Заказ-наряд обновлён", log["markdown"])
        self.assertIn("Клиент: Иван", log["markdown"])
        self.assertIn("Причина обращения: Первичная причина", log["markdown"])
        self.assertIn("Работы: 1 позиция", log["markdown"])
        self.assertNotIn('"works"', log["markdown"])
        self.assertNotIn('"client"', log["markdown"])
        self.assertNotIn("Оплата: cash", log["markdown"])
        self.assertNotIn("через API", log["markdown"])
        self.assertNotIn("API", log["markdown"])
        self.assertNotIn("inbox", log["markdown"])
        self.assertNotIn("{", log["markdown"])
        self.assertIn('"works"', repair_order_change["before"])

    def test_get_card_log_humanizes_vehicle_profile_snapshots(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Volkswagen Golf",
                "title": "ТЕХКАРТА В ЖУРНАЛЕ",
                "description": "Проверка читаемости техкарты",
                "deadline": {"hours": 2},
                "vehicle_profile": {"customer_name": ","},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "vehicle_profile": {
                    "make_display": "Volkswagen",
                    "model_display": "Golf",
                    "vin": "WVWZZZAUZFP518988",
                    "registration_plate": "М276УВ124",
                    "customer_name": "Иван",
                    "customer_phone": "89080162605",
                    "field_sources": {"vin": "manual_ui"},
                    "source_confidence": 0.95,
                    "warnings": [],
                },
                "actor_name": "ADMIN",
                "source": "ui",
            }
        )

        log = self.service.get_card_log({"card_id": card_id, "include_full_details": True})
        entry = next(item for item in log["entries"] if item["action"] == "vehicle_profile_updated")
        change = entry["changes"][0]

        self.assertIn('"field_sources"', change["after"])
        self.assertIn("Техкарта автомобиля заполнена", log["markdown"])
        self.assertIn("Марка: Volkswagen", log["markdown"])
        self.assertIn("Модель: Golf", log["markdown"])
        self.assertIn("Госномер: М276УВ124", log["markdown"])
        self.assertIn("Клиент: Иван", log["markdown"])
        self.assertNotIn("Клиент: ,", log["markdown"])
        self.assertNotIn("field_sources", log["markdown"])
        self.assertNotIn("source_confidence", log["markdown"])
        self.assertNotIn("manual_ui", log["markdown"])
        self.assertNotIn("{", log["markdown"])

    def test_get_card_log_humanizes_client_link_details(self) -> None:
        client = self.service.create_client(
            {"display_name": "Иван Клиент", "phone": "+7 913 111-22-33"}
        )["client"]
        created = self.service.create_card(
            {
                "vehicle": "Nissan X-Trail",
                "title": "ПРИВЯЗКА КЛИЕНТА",
                "description": "Проверка журнала клиента",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "make_display": "Nissan",
                    "model_display": "X-Trail",
                    "vin": "JN1TANT32U0012345",
                    "registration_plate": "Н111НН124",
                },
            }
        )["card"]

        self.service.link_card_to_client(
            {
                "card_id": created["id"],
                "client_id": client["id"],
                "create_vehicle_from_card": True,
                "actor_name": "ADMIN",
                "source": "api",
            }
        )

        log = self.service.get_card_log({"card_id": created["id"]})
        entry = next(item for item in log["entries"] if item["action"] == "card_client_linked")

        self.assertIn("Клиент: Иван Клиент", log["markdown"])
        self.assertIn("Автомобиль клиента: создан из карточки", log["markdown"])
        self.assertNotIn("client id", log["markdown"].lower())
        self.assertNotIn("client vehicle id", log["markdown"].lower())
        self.assertNotIn("vehicle created", log["markdown"].lower())
        self.assertNotIn(client["id"], log["markdown"])
        self.assertEqual(entry["details"]["client_id"], client["id"])

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
        self.assertEqual(created["card"]["vehicle_profile"]["registration_plate"], "а123вс77")
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
        self.assertEqual(profile["registration_plate"], "а111аа124")
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
        self.assertEqual(stored["license_plate"], "а123аа124")
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

    def test_repair_order_payment_transaction_stores_business_timezone_iso(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "TOYOTA CAMRY", "title": "Предоплата", "deadline": {"hours": 2}}
        )["card"]

        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [{"name": "Работы", "quantity": "1", "price": "6000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "03.05.2026 21:26",
                            "note": "Предоплата",
                            "payment_method": "cash",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )

        stored_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        stored_transaction = stored_state["cash_transactions"][0]
        self.assertEqual(stored_transaction["created_at"], "2026-05-03T21:26:00+07:00")
        self.assertEqual(stored_transaction["transaction_kind"], "repair_order_payment")

        details = self.service.get_cashbox({"cashbox_id": cashbox["id"], "transaction_limit": 5})
        self.assertEqual(details["transactions"][0]["created_at"], "2026-05-03T21:26:00+07:00")
        self.assertEqual(details["transactions"][0]["source_label"], "заказ-наряд")

        stored_state["cash_transactions"][0]["transaction_kind"] = ""
        self.state_file.write_text(
            json.dumps(stored_state, ensure_ascii=False),
            encoding="utf-8",
        )
        reloaded = CardService(
            JsonStore(state_file=self.state_file, logger=self.logger), self.logger
        )
        legacy_details = reloaded.get_cashbox({"cashbox_id": cashbox["id"], "transaction_limit": 5})
        self.assertEqual(legacy_details["transactions"][0]["source_label"], "заказ-наряд")

    def test_repair_order_payment_rejects_zero_or_missing_amount(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]

        for raw_amount in ("", "0"):
            with self.subTest(raw_amount=raw_amount):
                card = self.service.create_card(
                    {
                        "vehicle": "TOYOTA CAMRY",
                        "title": "Некорректная оплата",
                        "deadline": {"hours": 2},
                    }
                )["card"]

                with self.assertRaises(ServiceError) as error:
                    self.service.update_card(
                        {
                            "card_id": card["id"],
                            "repair_order": {
                                "works": [{"name": "Работы", "quantity": "1", "price": "6000"}],
                                "payments": [
                                    {
                                        "amount": raw_amount,
                                        "paid_at": "03.05.2026 21:26",
                                        "note": "Пустая сумма не должна создать кассу",
                                        "payment_method": "cash",
                                        "cashbox_id": cashbox["id"],
                                        "actor_name": "ADMIN",
                                    }
                                ],
                            },
                        }
                    )

                self.assertEqual(error.exception.code, "validation_error")

        details = self.service.get_cashbox({"cashbox_id": cashbox["id"], "transaction_limit": 5})
        self.assertEqual(details["cashbox"]["statistics"]["transactions_total"], 0)
        self.assertEqual(details["transactions"], [])

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

    def test_mark_card_ready_moves_order_between_open_and_ready_lists(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "Ready order",
                "deadline": {"hours": 2},
                "tags": ["СРОЧНО", "ДИАГНОСТИКА", "КЛИЕНТ"],
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
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

        marked = self.service.mark_card_ready({"card_id": card_id})

        self.assertEqual(marked["card"]["column_label"], "Готовые автомобили")
        self.assertEqual(marked["card"]["repair_order"]["status"], "ready")
        self.assertIn("ГОТОВ", marked["card"]["tags"])
        self.assertEqual(len(marked["card"]["tags"]), 4)
        ready = self.service.list_repair_orders({"status": "ready"})
        active = self.service.list_repair_orders()
        self.assertEqual([item["card_id"] for item in ready["repair_orders"]], [card_id])
        self.assertFalse(any(item["card_id"] == card_id for item in active["repair_orders"]))

        reopened = self.service.move_card({"card_id": card_id, "column": "inbox"})

        self.assertEqual(reopened["card"]["repair_order"]["status"], "open")
        self.assertNotIn("ГОТОВ", reopened["card"]["tags"])
        self.assertEqual(len(reopened["card"]["tags"]), 3)

    def test_mark_card_ready_without_repair_order_returns_warning(self) -> None:
        created = self.service.create_card(
            {"vehicle": "LADA VESTA", "title": "No order", "deadline": {"hours": 2}}
        )

        marked = self.service.mark_card_ready({"card_id": created["card"]["id"]})

        self.assertEqual(marked["card"]["column_label"], "Готовые автомобили")
        self.assertIn("ГОТОВ", marked["card"]["tags"])
        self.assertEqual(
            marked["meta"]["warnings"][0]["code"],
            "repair_order_missing",
        )

    def test_closed_repair_order_is_not_reopened_by_ready_column_moves(self) -> None:
        created = self.service.create_card(
            {"vehicle": "NISSAN", "title": "Closed ready", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Пётр",
                    "payments": [
                        {"amount": "2000", "paid_at": "06.04.2026 12:00", "payment_method": "cash"}
                    ],
                    "works": [{"name": "Ремонт", "quantity": "1", "price": "2000", "total": ""}],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})

        marked = self.service.mark_card_ready({"card_id": card_id})
        moved_back = self.service.move_card({"card_id": card_id, "column": "inbox"})

        self.assertEqual(marked["card"]["repair_order"]["status"], "closed")
        self.assertEqual(moved_back["card"]["repair_order"]["status"], "closed")

    def test_repair_order_numbers_remain_stable_after_assignment(self) -> None:
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

        self.assertEqual(by_card_id[second["card"]["id"]]["number"], "1")
        self.assertEqual(by_card_id[first["card"]["id"]]["number"], "2")

    def test_partial_update_card_keeps_assigned_repair_order_number(self) -> None:
        first = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "First card", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"vehicle": "LADA VESTA", "title": "Second card", "deadline": {"hours": 2}}
        )
        first_id = first["card"]["id"]
        second_id = second["card"]["id"]
        self.service.get_repair_order({"card_id": first_id})
        self.service.get_repair_order({"card_id": second_id})
        self.service.update_repair_order(
            {
                "card_id": first_id,
                "repair_order": {
                    "works": [{"name": "Первичная диагностика", "quantity": "1", "price": "900"}]
                },
            }
        )

        updated = self.service.update_card(
            {
                "card_id": first_id,
                "repair_order": {
                    "client": "Иван",
                },
            }
        )

        listed = self.service.list_repair_orders({"status": "all"})
        by_card_id = {item["card_id"]: item for item in listed["repair_orders"]}
        self.assertEqual(updated["card"]["repair_order"]["number"], "1")
        self.assertEqual(
            updated["card"]["repair_order"]["works"][0]["name"], "Первичная диагностика"
        )
        self.assertEqual(by_card_id[first_id]["number"], "1")
        self.assertEqual(by_card_id[second_id]["number"], "2")

    def test_repair_order_number_is_immutable_after_assignment(self) -> None:
        created = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Immutable number", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        self.service.get_repair_order({"card_id": card_id})

        patched = self.service.update_repair_order(
            {"card_id": card_id, "repair_order": {"client": "Иван"}}
        )["repair_order"]
        self.assertEqual(patched["number"], "1")

        empty_number = self.service.update_repair_order(
            {"card_id": card_id, "repair_order": {"number": "", "phone": "+7 900 123-45-67"}}
        )["repair_order"]
        self.assertEqual(empty_number["number"], "1")
        self.assertEqual(empty_number["phone"], "+7 900 123-45-67")

        with self.assertRaises(ServiceError) as immutable:
            self.service.update_repair_order({"card_id": card_id, "repair_order": {"number": "99"}})
        self.assertEqual(immutable.exception.code, "repair_order_number_immutable")

        reread = self.service.get_repair_order({"card_id": card_id})["repair_order"]
        self.assertEqual(reread["number"], "1")

    def test_legacy_repair_order_without_number_gets_one_number_once(self) -> None:
        created = self.service.create_card(
            {"vehicle": "Legacy", "title": "No number", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == card_id)
        card.repair_order = RepairOrder(client="Legacy client")
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        listed = self.service.list_repair_orders({"status": "all"})
        self.assertEqual(listed["repair_orders"][0]["number"], "1")

        updated = self.service.update_repair_order(
            {"card_id": card_id, "repair_order": {"comment": "Дополнено без смены номера"}}
        )
        reread = self.service.get_repair_order({"card_id": card_id})

        self.assertEqual(updated["repair_order"]["number"], "1")
        self.assertEqual(reread["repair_order"]["number"], "1")

    def test_repair_order_number_duplicates_are_rejected(self) -> None:
        first = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "First card", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"vehicle": "LADA VESTA", "title": "Second card", "deadline": {"hours": 2}}
        )
        third = self.service.create_card(
            {"vehicle": "VW Polo", "title": "Third card", "deadline": {"hours": 2}}
        )
        first_order = self.service.update_card(
            {"card_id": first["card"]["id"], "repair_order": {"client": "Иван"}}
        )["card"]["repair_order"]
        second_order = self.service.update_card(
            {"card_id": second["card"]["id"], "repair_order": {"client": "Пётр"}}
        )["card"]["repair_order"]

        with self.assertRaises(ServiceError) as immutable:
            self.service.update_card(
                {
                    "card_id": second["card"]["id"],
                    "repair_order": {
                        **second_order,
                        "number": first_order["number"],
                    },
                }
            )
        self.assertEqual(immutable.exception.code, "repair_order_number_immutable")

        with self.assertRaises(ServiceError) as duplicate:
            self.service.update_card(
                {
                    "card_id": third["card"]["id"],
                    "repair_order": {
                        "number": first_order["number"],
                        "client": "Мария",
                    },
                }
            )

        self.assertEqual(duplicate.exception.code, "repair_order_number_duplicate")

    def test_repair_order_number_correction_route_is_blocked(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Карта Мария", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {
                "vehicle": "Skoda Rapid 2020",
                "title": "Оплата с измененным номером",
                "deadline": {"hours": 2},
            }
        )["card"]

        created_order = self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "257",
                    "works": [{"name": "Работа", "quantity": "1", "price": "4000"}],
                    "payments": [
                        {
                            "amount": "4000",
                            "paid_at": "18.05.2026 12:39",
                            "payment_method": "card",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "KATYA",
                        }
                    ],
                },
            }
        )["card"]["repair_order"]
        payment = created_order["payments"][0]

        with self.assertRaises(ServiceError) as immutable:
            self.service.update_card(
                {
                    "card_id": card["id"],
                    "repair_order": {
                        **created_order,
                        "number": "259",
                        "payments": [payment],
                    },
                }
            )
        self.assertEqual(immutable.exception.code, "repair_order_number_immutable")

        with self.assertRaises(ServiceError) as correction:
            self.service.correct_repair_order_number(
                {
                    "card_id": card["id"],
                    "number": "259",
                    "reason": "Исправление номера после сверки",
                    "actor_name": "ADMIN",
                }
            )
        self.assertEqual(correction.exception.code, "repair_order_number_immutable")

        details = self.service.get_cashbox({"cashbox_id": cashbox["id"], "transaction_limit": 10})
        self.assertEqual(payment["cash_transaction_id"], details["transactions"][0]["id"])
        self.assertEqual(details["transactions"][0]["repair_order_number"], "257")
        self.assertEqual(details["transactions"][0]["note"], "Заказ-наряд №257")
        self.assertEqual(details["transactions"][0]["stored_note"], "Заказ-наряд №257")

        journal = self.service.get_cash_journal({"months": 3, "limit": 100})
        self.assertEqual(journal["entries"][0]["repair_order_number"], "257")
        self.assertEqual(journal["entries"][0]["note"], "Заказ-наряд №257")
        self.assertEqual(journal["entries"][0]["business_date"], "2026-05-18")
        self.assertEqual(journal["entries"][0]["business_time"], "12:39:00")

    def test_finance_audit_finds_legacy_payment_links_and_safe_fixes_them(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Карта Мария", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "Skoda Rapid", "title": "Сверка", "deadline": {"hours": 2}}
        )["card"]
        order = self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "214",
                    "works": [{"name": "Работа", "quantity": "1", "price": "4000"}],
                    "payments": [
                        {
                            "amount": "4000",
                            "paid_at": "18.05.2026 12:39",
                            "payment_method": "card",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "KATYA",
                        }
                    ],
                },
            }
        )["card"]["repair_order"]
        transaction_id = order["payments"][0]["cash_transaction_id"]
        bundle = self.store.read_bundle()
        transaction = next(
            item for item in bundle["cash_transactions"] if item.id == transaction_id
        )
        transaction.transaction_kind = ""
        transaction.note = "Заказ-наряд №257"
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        audit = self.service.get_finance_audit()
        codes = {issue["code"] for issue in audit["issues"]}

        self.assertIn("linked_payment_transaction_missing_kind", codes)
        self.assertIn("stale_default_repair_order_note", codes)
        self.assertGreaterEqual(audit["summary"]["safe_fix_count"], 2)

        dry_run = self.service.apply_finance_audit_safe_fixes()
        self.assertTrue(dry_run["meta"]["dry_run"])
        before_apply = self.service.get_cashbox(
            {"cashbox_id": cashbox["id"], "transaction_limit": 10}
        )["transactions"][0]
        self.assertEqual(before_apply["transaction_kind"], "")
        self.assertEqual(before_apply["stored_note"], "Заказ-наряд №257")

        applied = self.service.apply_finance_audit_safe_fixes(
            {"dry_run": False, "actor_name": "ADMIN"}
        )

        self.assertFalse(applied["meta"]["dry_run"])
        after_apply = self.service.get_cashbox(
            {"cashbox_id": cashbox["id"], "transaction_limit": 10}
        )["transactions"][0]
        self.assertEqual(after_apply["transaction_kind"], "repair_order_payment")
        self.assertEqual(after_apply["note"], "Заказ-наряд №214")
        self.assertNotIn(
            "stale_default_repair_order_note", {issue["code"] for issue in applied["issues"]}
        )

    def test_finance_audit_safe_fix_creates_missing_payment_cash_transaction(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "Skoda Rapid", "title": "Сверка оплаты", "deadline": {"hours": 2}}
        )["card"]
        order = self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "305",
                    "works": [{"name": "Работа", "quantity": "1", "price": "4000"}],
                    "payments": [
                        {
                            "amount": "4000",
                            "paid_at": "18.05.2026 12:39",
                            "payment_method": "cash",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )["card"]["repair_order"]
        payment_id = order["payments"][0]["id"]
        bundle = self.store.read_bundle()
        stored_card = next(item for item in bundle["cards"] if item.id == card["id"])
        stored_card.repair_order.payments[0].cash_transaction_id = ""
        bundle["cash_transactions"] = []
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        audit = self.service.get_finance_audit()
        issue = next(
            issue
            for issue in audit["issues"]
            if issue["code"] == "payment_without_cash_transaction_id"
        )

        self.assertEqual(issue["repair_order_payment_id"], payment_id)
        self.assertEqual(issue["cashbox_id"], cashbox["id"])
        self.assertEqual(issue["amount_minor"], 400000)
        self.assertTrue(issue["safe_fix_available"])
        self.assertEqual(issue["safe_fix"]["kind"], "create_missing_payment_cash_transaction")

        dry_run = self.service.apply_finance_audit_safe_fixes()
        self.assertTrue(dry_run["meta"]["dry_run"])
        self.assertEqual(dry_run["meta"]["planned"], 1)
        self.assertEqual(
            self.service.get_cashbox({"cashbox_id": cashbox["id"]})["transactions"], []
        )

        applied = self.service.apply_finance_audit_safe_fixes(
            {"dry_run": False, "actor_name": "ADMIN"}
        )
        self.assertEqual(applied["meta"]["applied"], 1)

        refreshed_order = self.service.get_repair_order({"card_id": card["id"]})["repair_order"]
        refreshed_payment = refreshed_order["payments"][0]
        cashbox_details = self.service.get_cashbox(
            {"cashbox_id": cashbox["id"], "transaction_limit": 10}
        )
        transaction = cashbox_details["transactions"][0]

        self.assertEqual(refreshed_payment["cash_transaction_id"], transaction["id"])
        self.assertEqual(transaction["transaction_kind"], "repair_order_payment")
        self.assertEqual(transaction["amount_minor"], 400000)
        self.assertEqual(transaction["note"], "Заказ-наряд №305")
        self.assertTrue(transaction["created_at"].startswith("2026-05-18T12:39:00"))

        next_audit = self.service.get_finance_audit()
        self.assertNotIn(
            payment_id,
            {
                item["repair_order_payment_id"]
                for item in next_audit["issues"]
                if item["code"] == "payment_without_cash_transaction_id"
            },
        )

    def test_finance_audit_does_not_safe_fix_ambiguous_missing_payment_link(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "Skoda Rapid", "title": "Сверка оплаты", "deadline": {"hours": 2}}
        )["card"]
        order = self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "306",
                    "works": [{"name": "Работа", "quantity": "1", "price": "4000"}],
                    "payments": [
                        {
                            "amount": "4000",
                            "paid_at": "18.05.2026 12:39",
                            "payment_method": "cash",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )["card"]["repair_order"]
        payment_id = order["payments"][0]["id"]
        transaction_id = order["payments"][0]["cash_transaction_id"]
        bundle = self.store.read_bundle()
        stored_card = next(item for item in bundle["cards"] if item.id == card["id"])
        stored_card.repair_order.payments[0].cash_transaction_id = ""
        transaction = next(
            item for item in bundle["cash_transactions"] if item.id == transaction_id
        )
        transaction.transaction_kind = ""
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        audit = self.service.get_finance_audit()
        issue = next(
            issue
            for issue in audit["issues"]
            if issue["code"] == "payment_without_cash_transaction_id"
            and issue["repair_order_payment_id"] == payment_id
        )

        self.assertFalse(issue["safe_fix_available"])

    def test_finance_audit_does_not_flag_closed_noncash_fee_as_underpaid(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Безнал", "actor_name": "ADMIN"})["cashbox"]
        card = self.service.create_card(
            {"vehicle": "Skoda Rapid", "title": "Безналичная оплата", "deadline": {"hours": 2}}
        )["card"]
        card_id = card["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "301",
                    "works": [{"name": "Работа", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "18.05.2026 12:39",
                            "payment_method": "cashless",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": card_id, "status": "closed"})

        audit = self.service.get_finance_audit()
        issues = [
            issue
            for issue in audit["issues"]
            if issue["code"] == "closed_underpaid" and issue["card_id"] == card_id
        ]

        self.assertEqual(issues, [])

    def test_finance_audit_reports_closed_order_with_base_underpayment(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "Skoda Rapid", "title": "Недоплата", "deadline": {"hours": 2}}
        )["card"]
        card_id = card["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "302",
                    "works": [{"name": "Работа", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "18.05.2026 12:39",
                            "payment_method": "cash",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )
        bundle = self.store.read_bundle()
        stored_card = next(item for item in bundle["cards"] if item.id == card_id)
        stored_card.repair_order.status = "closed"
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        audit = self.service.get_finance_audit()
        issue = next(
            issue
            for issue in audit["issues"]
            if issue["code"] == "closed_underpaid" and issue["card_id"] == card_id
        )

        self.assertEqual(issue["data"]["due_total"], "500")
        self.assertEqual(issue["data"]["paid_total"], "500")
        self.assertEqual(issue["data"]["grand_total"], "1000")

    def test_finance_audit_treats_open_zero_total_prepayment_as_info_only(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Предоплата", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "Audi A6", "title": "Предоплата до согласования", "deadline": {"hours": 2}}
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "303",
                    "status": "open",
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "18.05.2026 12:39",
                            "payment_method": "cash",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )

        audit = self.service.get_finance_audit()
        codes = {issue["code"] for issue in audit["issues"] if issue["card_id"] == card["id"]}

        self.assertIn("open_with_payments", codes)
        self.assertNotIn("paid_zero_total", codes)

    def test_finance_audit_warns_when_non_open_order_has_payment_but_zero_total(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Предоплата", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "Audi A6", "title": "Закрытая нулевая сумма", "deadline": {"hours": 2}}
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "304",
                    "status": "open",
                    "payments": [
                        {
                            "amount": "1000",
                            "paid_at": "18.05.2026 12:39",
                            "payment_method": "cash",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )
        bundle = self.store.read_bundle()
        stored_card = next(item for item in bundle["cards"] if item.id == card["id"])
        stored_card.repair_order.status = "closed"
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        audit = self.service.get_finance_audit()
        codes = {issue["code"] for issue in audit["issues"] if issue["card_id"] == card["id"]}

        self.assertIn("paid_zero_total", codes)
        self.assertNotIn("open_with_payments", codes)

    def test_finance_audit_safe_fix_restores_missing_salary_employee(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        bundle = self.store.read_bundle()
        bundle["cash_transactions"].append(
            CashTransaction(
                id="tx-salary-restorable-employee",
                cashbox_id=cashbox["id"],
                direction="expense",
                amount_minor=100000,
                note="Выплата зарплаты: Удаленный сотрудник",
                created_at="2026-05-18T10:00:00+07:00",
                actor_name="ADMIN",
                source="api",
                employee_id="employee-restorable",
                employee_name="Удаленный сотрудник",
                transaction_kind="salary_payout",
            )
        )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        audit = self.service.get_finance_audit()
        issue = next(
            issue
            for issue in audit["issues"]
            if issue["code"] == "salary_transaction_missing_employee"
        )
        self.assertTrue(issue["safe_fix_available"])
        self.assertEqual(issue["safe_fix"]["kind"], "restore_missing_employee")
        dry_run = self.service.apply_finance_audit_safe_fixes()
        self.assertEqual(dry_run["meta"]["planned"], 1)

        applied = self.service.apply_finance_audit_safe_fixes(
            {"dry_run": False, "actor_name": "ADMIN"}
        )

        self.assertEqual(applied["meta"]["applied"], 1)
        self.assertNotIn(
            "salary_transaction_missing_employee",
            {issue["code"] for issue in applied["issues"]},
        )
        employee = next(
            item
            for item in self.service.list_employees()["employees"]
            if item["id"] == "employee-restorable"
        )
        self.assertEqual(employee["name"], "Удаленный сотрудник")
        self.assertFalse(employee["is_active"])

    def test_finance_audit_reports_salary_transaction_with_wrong_direction(self) -> None:
        employee = self.service.save_employee({"name": "Мастер выплаты"})["employee"]
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        bundle = self.store.read_bundle()
        bundle["cash_transactions"].append(
            CashTransaction(
                id="tx-salary-income",
                cashbox_id=cashbox["id"],
                direction="income",
                amount_minor=100000,
                note="Выплата зарплаты ошибочным приходом",
                created_at="2026-05-18T10:00:00+07:00",
                actor_name="ADMIN",
                source="api",
                employee_id=employee["id"],
                employee_name=employee["name"],
                transaction_kind="salary_payout",
            )
        )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        audit = self.service.get_finance_audit()
        issue = next(
            issue
            for issue in audit["issues"]
            if issue["code"] == "salary_transaction_wrong_direction"
        )

        self.assertEqual(issue["severity"], "error")
        self.assertEqual(issue["cash_transaction_id"], "tx-salary-income")
        self.assertEqual(issue["data"]["direction"], "income")
        self.assertEqual(issue["data"]["expected_direction"], "expense")
        self.assertFalse(issue["safe_fix_available"])

    def test_finance_audit_reports_cash_transactions_without_cashbox(self) -> None:
        cashbox = CashBox(
            id="cashbox-existing",
            name="Наличный",
            order=0,
            created_at="2026-05-18T08:00:00+00:00",
            updated_at="2026-05-18T08:00:00+00:00",
        )
        orphan_transaction = CashTransaction(
            id="tx-orphan",
            cashbox_id="cashbox-missing",
            direction="income",
            amount_minor=250000,
            note="Оплата без кассы",
            created_at="2026-05-18T09:00:00+00:00",
            actor_name="ADMIN",
            source="api",
        )
        bundle = self.store.read_bundle()
        audit = self.service._build_finance_audit(
            {**bundle, "cashboxes": [cashbox], "cash_transactions": [orphan_transaction]}
        )
        issues = {issue["code"]: issue for issue in audit["issues"]}

        self.assertIn("cash_transaction_missing_cashbox", issues)
        self.assertEqual(
            issues["cash_transaction_missing_cashbox"]["cash_transaction_id"], "tx-orphan"
        )
        self.assertEqual(
            issues["cash_transaction_missing_cashbox"]["cashbox_id"], "cashbox-missing"
        )
        self.assertFalse(issues["cash_transaction_missing_cashbox"]["safe_fix_available"])

    def test_finance_audit_reports_linked_payment_cash_transaction_mismatch(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        other_cashbox = self.service.create_cashbox({"name": "Безнал", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "Skoda Rapid", "title": "Сверка оплаты", "deadline": {"hours": 2}}
        )["card"]
        order = self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "213",
                    "works": [{"name": "Работа", "quantity": "1", "price": "4000"}],
                    "payments": [
                        {
                            "amount": "4000",
                            "paid_at": "18.05.2026 12:39",
                            "payment_method": "cash",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )["card"]["repair_order"]
        transaction_id = order["payments"][0]["cash_transaction_id"]

        bundle = self.store.read_bundle()
        transaction = next(
            item for item in bundle["cash_transactions"] if item.id == transaction_id
        )
        transaction.direction = "expense"
        transaction.amount_minor = 350000
        transaction.cashbox_id = other_cashbox["id"]
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        audit = self.service.get_finance_audit()
        issue = next(
            item
            for item in audit["issues"]
            if item["code"] == "linked_payment_cash_transaction_mismatch"
        )

        self.assertEqual(issue["severity"], "error")
        self.assertFalse(issue["safe_fix_available"])
        self.assertCountEqual(
            issue["data"]["mismatch_reasons"],
            ["direction", "amount", "cashbox"],
        )
        self.assertEqual(issue["data"]["expected_amount_minor"], 400000)
        self.assertEqual(issue["data"]["amount_minor"], 350000)
        self.assertEqual(issue["data"]["expected_cashbox_id"], cashbox["id"])
        self.assertEqual(issue["data"]["cashbox_id"], other_cashbox["id"])

    def test_finance_audit_reports_read_only_cross_link_issues(self) -> None:
        employee = self.service.save_employee({"name": "Сотрудник сверки"})["employee"]
        cashbox = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})[
            "cashbox"
        ]
        card = self.service.create_card(
            {"vehicle": "Skoda Rapid", "title": "Сверка", "deadline": {"hours": 2}}
        )["card"]
        order = self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {
                    "number": "214",
                    "works": [{"name": "Работа", "quantity": "1", "price": "4000"}],
                    "payments": [
                        {
                            "amount": "4000",
                            "paid_at": "18.05.2026 12:39",
                            "payment_method": "cash",
                            "cashbox_id": cashbox["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            }
        )["card"]["repair_order"]
        transaction_id = order["payments"][0]["cash_transaction_id"]
        duplicate_card = self.service.create_card(
            {"vehicle": "Audi A6", "title": "Дубль оплаты", "deadline": {"hours": 2}}
        )["card"]
        bundle = self.store.read_bundle()
        card_to_duplicate = next(
            item for item in bundle["cards"] if item.id == duplicate_card["id"]
        )
        card_to_duplicate.repair_order = RepairOrder.from_dict(
            {
                "number": "215",
                "works": [{"name": "Работа", "quantity": "1", "price": "4000"}],
                "payments": [
                    {
                        "id": "payment-duplicate",
                        "amount": "4000",
                        "paid_at": "18.05.2026 13:00",
                        "payment_method": "cash",
                        "cashbox_id": cashbox["id"],
                        "cashbox_name": "Наличный",
                        "cash_transaction_id": transaction_id,
                    }
                ],
            }
        )
        bundle["cash_transactions"].extend(
            [
                CashTransaction(
                    id="tx-transfer-out",
                    cashbox_id=cashbox["id"],
                    direction="expense",
                    amount_minor=300000,
                    note="Перемещение",
                    created_at="2026-05-18T09:00:00+07:00",
                    actor_name="ADMIN",
                    source="api",
                    transfer_group_id="transfer-broken",
                    related_transaction_id="tx-transfer-in",
                ),
                CashTransaction(
                    id="tx-transfer-in",
                    cashbox_id=cashbox["id"],
                    direction="income",
                    amount_minor=250000,
                    note="Перемещение",
                    created_at="2026-05-18T09:00:00+07:00",
                    actor_name="ADMIN",
                    source="api",
                    transfer_group_id="transfer-broken",
                    related_transaction_id="tx-transfer-out",
                ),
                CashTransaction(
                    id="tx-salary-missing-employee",
                    cashbox_id=cashbox["id"],
                    direction="expense",
                    amount_minor=100000,
                    note="Зарплата",
                    created_at="2026-05-18T10:00:00+07:00",
                    actor_name="ADMIN",
                    source="api",
                    employee_id="employee-missing",
                    transaction_kind="salary_payout",
                ),
                CashTransaction(
                    id="tx-salary-wrong-direction",
                    cashbox_id=cashbox["id"],
                    direction="income",
                    amount_minor=200000,
                    note="Ошибочная выплата приходом",
                    created_at="2026-05-18T10:05:00+07:00",
                    actor_name="ADMIN",
                    source="api",
                    employee_id=employee["id"],
                    transaction_kind="salary_advance",
                ),
            ]
        )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        audit = self.service.get_finance_audit()
        codes = {issue["code"] for issue in audit["issues"]}

        self.assertIn("duplicate_repair_order_payment_cash_link", codes)
        self.assertIn("transfer_pair_amount_mismatch", codes)
        self.assertIn("salary_transaction_missing_employee", codes)
        self.assertIn("salary_transaction_wrong_direction", codes)
        for issue in audit["issues"]:
            if issue["code"] in {
                "duplicate_repair_order_payment_cash_link",
                "transfer_pair_amount_mismatch",
                "salary_transaction_missing_employee",
                "salary_transaction_wrong_direction",
            }:
                self.assertFalse(issue["safe_fix_available"])

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

    def test_card_serialization_marks_missing_attachment_files(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "VW", "title": "Missing attachment marker", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "photo.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        file_path, _ = service.get_attachment_download(card_id, attachment_id)

        available_card = service.get_card({"card_id": card_id})["card"]
        self.assertTrue(available_card["attachments"][0]["exists_on_disk"])

        file_path.unlink()

        missing_card = service.get_card({"card_id": card_id})["card"]
        self.assertFalse(missing_card["attachments"][0]["exists_on_disk"])

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
                "content_base64": base64.b64encode(minimal_docx_bytes("Agent DOCX text")).decode(
                    "ascii"
                ),
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

    def test_repair_order_patch_validation_reports_ignored_fields(self) -> None:
        created = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Ремонт", "deadline": {"hours": 2}}
        )

        with self.assertRaises(ServiceError) as raised:
            self.service.update_repair_order(
                {
                    "card_id": created["card"]["id"],
                    "repair_order": {"comment_text": "Нужно согласовать"},
                }
            )

        self.assertEqual(raised.exception.code, "validation_error")
        self.assertIn("comment_text", raised.exception.details["received_fields"])
        self.assertIn("comment_text", raised.exception.details["ignored_fields"])
        self.assertIn("comment", raised.exception.details["fields"])
        self.assertEqual(
            raised.exception.details["common_aliases"]["client_information"],
            "comment",
        )

    def test_repair_order_patch_normalizes_common_aliases(self) -> None:
        created = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Ремонт", "deadline": {"hours": 2}}
        )

        patched = self.service.update_repair_order(
            {
                "card_id": created["card"]["id"],
                "repair_order": {
                    "paymentMethod": "cashless",
                    "advancePayment": "500",
                    "licensePlate": "А123АА124",
                    "odometer": "120000",
                    "masterComment": "Комментарий мастера",
                },
            }
        )

        order = patched["repair_order"]
        self.assertEqual(order["payment_method"], "cashless")
        self.assertEqual(order["prepayment"], "500")
        self.assertEqual(order["license_plate"], "а123аа124")
        self.assertEqual(order["mileage"], "120000")
        self.assertEqual(order["note"], "Комментарий мастера")

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
        self.assertEqual(order["license_plate"], "а123аа124")
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
        self.assertEqual(order["license_plate"], "а123аа124")
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

        board_content = self.service.get_board_content(
            {"include_archived": True, "view_mode": "agent"}
        )
        self.assertTrue(board_content["text"].startswith("# AutoStop CRM Board Content"))
        self.assertEqual(board_content["meta"]["section_kind"], "board_content")
        self.assertEqual(board_content["meta"]["response_mode"], "agent_context")
        self.assertEqual(board_content["meta"]["view_mode"], "agent")
        self.assertTrue(board_content["meta"]["cards_compact"])
        self.assertIn(card_short_id, board_content["text"])

        board_events = self.service.get_board_events({"include_archived": True, "event_limit": 50})
        self.assertTrue(board_events["text"].startswith("# AutoStop CRM Event Log"))
        self.assertEqual(board_events["meta"]["section_kind"], "event_log")
        self.assertEqual(board_events["meta"]["response_mode"], "audit")
        self.assertEqual(board_events["meta"]["event_limit"], 50)
        self.assertTrue(any(event["card_id"] == card_id for event in board_events["events"]))

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
