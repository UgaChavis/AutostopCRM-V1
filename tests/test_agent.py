from __future__ import annotations

import json
import logging
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.control import AgentControlService
from minimal_kanban.agent.automotive_tools import AutomotiveLookupService
from minimal_kanban.agent.contracts import PatchResult, ToolResult, VerifyResult
from minimal_kanban.agent.instructions import build_default_system_prompt
from minimal_kanban.agent.runner import AgentRunner
from minimal_kanban.agent.storage import AgentStorage
from minimal_kanban.api.server import ApiServer
from minimal_kanban.models import utc_now_iso
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _FakeModelClient:
    def __init__(self, decisions: list[dict]) -> None:
        self._decisions = list(decisions)
        self.model = "fake-model"
        self.calls: list[dict[str, object]] = []

    def next_step(self, *, system_prompt: str, messages: list[dict[str, str]]) -> dict:
        self.calls.append({"system_prompt": system_prompt, "messages": list(messages)})
        return self._decisions.pop(0)


class _FakeBoardApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.cards = {
            "card-1": {
                "id": "card-1",
                "title": "Черновик",
                "description": "Исходный текст карточки.\nЦена детали 5000.\nАртикул ABC-123.",
            }
        }

    def health(self) -> dict:
        self.calls.append(("health", {}))
        return {"ok": True}

    def review_board(self, **kwargs) -> dict:
        self.calls.append(("review_board", kwargs))
        return {"ok": True, "data": {"summary": {"active_cards": 1}}, "text": "board summary"}

    def get_board_context(self) -> dict:
        self.calls.append(("get_board_context", {}))
        return {"context": {"board_name": "Current AutoStop CRM Board", "active_cards_total": len(self.cards)}}

    def search_cards(self, **kwargs) -> dict:
        self.calls.append(("search_cards", kwargs))
        query = str(kwargs.get("query", "") or "").strip().upper()
        cards: list[dict[str, object]] = []
        for card_id, card in self.cards.items():
            haystack = " ".join(
                [
                    str(card.get("id", "") or ""),
                    str(card.get("title", "") or ""),
                    str(card.get("vehicle", "") or ""),
                    str(card.get("description", "") or ""),
                ]
            ).upper()
            if query and query not in haystack:
                continue
            cards.append(
                {
                    "id": card_id,
                    "vehicle": card.get("vehicle", ""),
                    "title": card.get("title", ""),
                    "column": "inbox",
                    "tags": [],
                }
            )
        return {"cards": cards}

    def get_card(self, card_id: str) -> dict:
        self.calls.append(("get_card", {"card_id": card_id}))
        return {"card": dict(self.cards.get(card_id, {"id": card_id, "description": ""}))}

    def update_card(self, **kwargs) -> dict:
        self.calls.append(("update_card", kwargs))
        card_id = str(kwargs.get("card_id") or "").strip()
        if card_id:
            card = dict(self.cards.get(card_id, {"id": card_id}))
            card.update({key: value for key, value in kwargs.items() if key in {"title", "description", "vehicle", "tags", "vehicle_profile"}})
            self.cards[card_id] = card
        return {
            "ok": True,
            "card_id": kwargs.get("card_id"),
            "changed": ["title", "description"],
            "meta": {"changed": True, "changed_fields": ["title", "description"]},
        }


class _FakeWrappedBoardApi(_FakeBoardApi):
    def review_board(self, **kwargs) -> dict:
        raw = super().review_board(**kwargs)
        return {"ok": True, "data": raw.get("data", {})}

    def get_card(self, card_id: str) -> dict:
        raw = super().get_card(card_id)
        return {"ok": True, "data": raw}

    def get_card_context(self, card_id: str, **kwargs) -> dict:
        self.calls.append(("get_card_context", {"card_id": card_id, **kwargs}))
        card = dict(self.cards.get(card_id, {"id": card_id, "description": ""}))
        vehicle_profile = {"vin": "WBAPF71060A798127"}
        repair_order = {"number": "", "status": "open", "works": [], "materials": [], "vin": ""}
        return {
            "ok": True,
            "data": {
                "card": {
                    **card,
                    "vehicle": "BMW 320I",
                    "title": card.get("title", ""),
                    "column": "inbox",
                    "tags": [],
                    "ai_autofill_prompt": "Добавь короткую ИИ-заметку.",
                    "ai_autofill_log": [{"level": "RUN", "message": "Автосопровождение включено."}],
                    "vehicle_profile": vehicle_profile,
                    "repair_order": repair_order,
                },
                "events": [{"action": "card_created"}],
            },
        }

    def search_cards(self, **kwargs) -> dict:
        self.calls.append(("search_cards", kwargs))
        return {
            "ok": True,
            "data": {
                "cards": [
                    {
                        "id": "card-1",
                        "vehicle": "BMW 320I",
                        "title": "Черновик",
                        "column": "inbox",
                        "tags": [],
                    }
                ]
            },
        }

    def update_card(self, **kwargs) -> dict:
        raw = super().update_card(**kwargs)
        return {"ok": True, "data": raw}


class _ConsistentWrappedBoardApi(_FakeWrappedBoardApi):
    def __init__(self) -> None:
        super().__init__()
        self.cards["card-1"].update(
            {
                "vehicle": "BMW 320I",
                "tags": [],
                "vehicle_profile": {"vin": "WBAPF71060A798127"},
                "repair_order": {"number": "", "status": "open", "works": [], "materials": [], "vin": ""},
            }
        )

    def get_card_context(self, card_id: str, **kwargs) -> dict:
        self.calls.append(("get_card_context", {"card_id": card_id, **kwargs}))
        card = dict(self.cards.get(card_id, {"id": card_id, "description": ""}))
        vehicle_profile = dict(card.get("vehicle_profile") or {})
        repair_order = dict(card.get("repair_order") or {"number": "", "status": "open", "works": [], "materials": [], "vin": ""})
        return {
            "ok": True,
            "data": {
                "card": {
                    **card,
                    "vehicle": str(card.get("vehicle", "") or ""),
                    "title": str(card.get("title", "") or ""),
                    "column": "inbox",
                    "tags": list(card.get("tags") or []),
                    "ai_autofill_prompt": "",
                    "ai_autofill_log": [],
                    "vehicle_profile": vehicle_profile,
                    "repair_order": repair_order,
                },
                "events": [{"action": "card_created"}],
            },
        }


class _FakeAutomotiveService:
    def decode_vin(self, vin: str) -> dict:
        return {
            "vin": vin,
            "make": "BMW",
            "model": "320i",
            "model_year": "2016",
            "engine_model": "2.0 N20",
            "transmission": "AT",
            "drive_type": "RWD",
            "plant_country": "Germany",
            "source_url": "https://example.test/vin",
        }

    def find_part_numbers(self, *, query: str, vehicle: dict[str, object] | str | None = None, limit: int = 5) -> dict:
        return {
            "query": query,
            "vehicle_context": vehicle if isinstance(vehicle, dict) else {"vehicle": str(vehicle or "").strip()},
            "part_numbers": [
                {"value": "17118625431", "label": "OEM"},
                {"value": "AVA BW2285", "label": "analog"},
            ],
            "results": [{"title": "BMW radiator", "snippet": "OEM 17118625431", "url": "https://example.test/rad"}],
        }

    def estimate_price_ru(self, *, part_number: str, vehicle: dict[str, object] | str | None = None, limit: int = 5) -> dict:
        return {
            "part_number": part_number,
            "vehicle_context": vehicle if isinstance(vehicle, dict) else {"vehicle": str(vehicle or "").strip()},
            "price_summary": {"offers_total": 3, "min_rub": 14500, "max_rub": 21900},
            "results": [{"title": "Цена", "snippet": "14 500 ₽", "url": "https://example.test/price"}],
        }

    def decode_dtc(
        self,
        *,
        code: str,
        vehicle_context: dict[str, object] | None = None,
        vehicle: dict[str, object] | str | None = None,
        limit: int = 5,
    ) -> dict:
        return {
            "code": code,
            "vehicle_context": vehicle_context or (vehicle if isinstance(vehicle, dict) else {"vehicle": str(vehicle or "").strip()}),
            "results": [{"title": "DTC", "snippet": "Пропуски воспламенения, сначала проверить свечи и катушки."}],
        }

    def search_fault_info(
        self,
        *,
        query: str,
        vehicle_context: dict[str, object] | None = None,
        vehicle: dict[str, object] | str | None = None,
        limit: int = 5,
    ) -> dict:
        return {
            "query": query,
            "vehicle_context": vehicle_context or (vehicle if isinstance(vehicle, dict) else {"vehicle": str(vehicle or "").strip()}),
            "results": [{"title": "Fault", "snippet": "Типовая причина — течь радиатора или патрубков, проверить бачок и опрессовку."}],
        }

    def estimate_maintenance(self, *, vehicle_context: dict[str, object] | None, service_type: str = "ТО") -> dict:
        return {
            "service_type": service_type,
            "vehicle_context": vehicle_context or {},
            "works": [{"name": "Замена масла", "quantity": "1"}],
            "materials": [{"name": "Масляный фильтр", "quantity": "1"}],
            "notes": ["План предварительный."],
        }


class AgentStorageTests(unittest.TestCase):
    def test_queue_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            created = storage.enqueue_task(task_text="Review board", source="manual")
            self.assertEqual(created["status"], "pending")

            claimed = storage.claim_next_task()
            assert claimed is not None
            self.assertEqual(claimed["id"], created["id"])
            self.assertEqual(claimed["status"], "running")

            completed = storage.complete_task(
                task_id=created["id"],
                run_id="agrun_test",
                summary="done",
                result="ok",
                display={"title": "done", "summary": "ok", "tone": "success", "sections": [], "actions": []},
                tool_calls=1,
            )
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(storage.list_tasks(limit=10)[0]["summary"], "done")

    def test_storage_prunes_old_finished_tasks_but_keeps_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir), max_finished_tasks=2)
            first = storage.enqueue_task(task_text="one")
            second = storage.enqueue_task(task_text="two")
            third = storage.enqueue_task(task_text="three")
            pending = storage.enqueue_task(task_text="pending")

            for task in [first, second, third]:
                claimed = storage.claim_next_task()
                assert claimed is not None
                storage.complete_task(
                    task_id=claimed["id"],
                    run_id=f"run_{claimed['id']}",
                    summary=claimed["task_text"],
                    result="ok",
                    display={},
                    tool_calls=1,
                )

            tasks = storage.list_tasks(limit=10)
            task_ids = {item["id"] for item in tasks}
            self.assertNotIn(first["id"], task_ids)
            self.assertIn(second["id"], task_ids)
            self.assertIn(third["id"], task_ids)
            self.assertIn(pending["id"], task_ids)

    def test_storage_compacts_runs_and_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(
                base_dir=Path(temp_dir),
                max_runs=2,
                max_actions=3,
                compact_threshold_bytes=1,
            )
            for index in range(4):
                storage.append_run({"id": f"run-{index}"})
            for index in range(6):
                storage.append_action({"id": f"action-{index}"})

            self.assertEqual([item["id"] for item in storage.list_runs(limit=10)], ["run-3", "run-2"])
            self.assertEqual(
                [item["id"] for item in storage.list_actions(limit=10)],
                ["action-5", "action-4", "action-3"],
            )


class AgentControlStatusTests(unittest.TestCase):
    def test_agent_status_uses_fresh_shared_heartbeat_for_external_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            "os.environ",
            {
                "MINIMAL_KANBAN_AGENT_ENABLED": "1",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.update_status(
                running=False,
                current_task_id=None,
                current_run_id=None,
                last_heartbeat=utc_now_iso(),
                last_error="",
            )
            service = AgentControlService(storage)
            payload = service.agent_status()
            self.assertTrue(payload["agent"]["enabled"])
            self.assertFalse(payload["agent"]["configured"])
            self.assertTrue(payload["agent"]["available"])
            self.assertTrue(payload["worker"]["heartbeat_fresh"])

    def test_agent_status_throttles_scheduler_side_effects_between_fast_polls(self) -> None:
        class _BoardService:
            def __init__(self) -> None:
                self.calls = 0

            def trigger_due_ai_followups(self) -> dict:
                self.calls += 1
                return {"launched": [], "failed": []}

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            "os.environ",
            {
                "MINIMAL_KANBAN_AGENT_ENABLED": "1",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            storage = AgentStorage(base_dir=Path(temp_dir))
            service = AgentControlService(storage, scheduler_interval_seconds=20.0)
            board = _BoardService()
            service.bind_board_service(board)
            service.agent_status()
            service.agent_status()
            self.assertEqual(board.calls, 1)


class AgentRunnerTests(unittest.TestCase):
    def test_default_prompt_includes_card_cleanup_rules(self) -> None:
        prompt = build_default_system_prompt()
        self.assertIn("tidy up, clean up, or structure a card", prompt)
        self.assertIn("preserve all facts from the card", prompt)
        self.assertIn("apply confident changes with update_card", prompt)
        self.assertIn("card_autofill tasks", prompt)
        self.assertIn("Do not repeat the current description verbatim", prompt)
        self.assertIn("Treat existing vehicle_profile and repair_order fields as grounded known facts", prompt)
        self.assertIn('must be labeled with "ИИ:" or "AI:"', prompt)
        self.assertIn("card-context-grounded", prompt)
        self.assertIn("VIN-only cards stay VIN-only", prompt)

    def test_card_autofill_scenario_selection_keeps_vin_only_cards_vin_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AgentRunner(
                storage=AgentStorage(base_dir=Path(temp_dir)),
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient([]),
                logger=logging.getLogger("test.agent.runner.scenario.vin"),
            )
            facts = runner._analyze_card_autofill_context(
                {
                    "card": {
                        "id": "card-1",
                        "title": "Проверить VIN",
                        "vehicle": "",
                        "description": "VIN: WBAPF71060A798127",
                        "vehicle_profile": {},
                        "repair_order": {},
                        "ai_autofill_prompt": "Подготовь ТО",
                        "ai_autofill_log": [{"message": "Раньше искали ТО"}],
                    },
                    "events": [],
                    "repair_order_text": {"text": ""},
                },
                task_text="Сделай ТО и подбери запчасти.",
            )
            scenario_names = [item["name"] for item in runner._select_card_autofill_scenarios(facts)]
            self.assertEqual(scenario_names, ["vin_enrichment", "normalization"])
            self.assertFalse(facts["scenario_evidence"]["maintenance_lookup"]["trigger_found"])
            self.assertFalse(facts["scenario_evidence"]["parts_lookup"]["trigger_found"])

    def test_card_autofill_scenario_selection_runs_parts_only_from_explicit_part_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AgentRunner(
                storage=AgentStorage(base_dir=Path(temp_dir)),
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient([]),
                logger=logging.getLogger("test.agent.runner.scenario.parts"),
            )
            facts = runner._analyze_card_autofill_context(
                {
                    "card": {
                        "id": "card-1",
                        "title": "BMW 320i",
                        "vehicle": "BMW 320i",
                        "description": "VIN: WBAPF71060A798127\nБежит антифриз.\nНужно найти радиатор и сориентировать по цене.",
                        "vehicle_profile": {},
                        "repair_order": {},
                    },
                    "events": [],
                    "repair_order_text": {"text": ""},
                }
            )
            scenario_names = [item["name"] for item in runner._select_card_autofill_scenarios(facts)]
            self.assertEqual(scenario_names, ["vin_enrichment", "parts_lookup", "normalization"])

    def test_card_autofill_scenario_selection_runs_maintenance_only_with_explicit_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AgentRunner(
                storage=AgentStorage(base_dir=Path(temp_dir)),
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient([]),
                logger=logging.getLogger("test.agent.runner.scenario.maintenance"),
            )
            facts = runner._analyze_card_autofill_context(
                {
                    "card": {
                        "id": "card-1",
                        "title": "ТО",
                        "vehicle": "BMW 320i",
                        "description": "Пробег: 120000\nНужно большое ТО с заменой масла.",
                        "vehicle_profile": {},
                        "repair_order": {},
                    },
                    "events": [],
                    "repair_order_text": {"text": ""},
                }
            )
            scenario_names = [item["name"] for item in runner._select_card_autofill_scenarios(facts)]
            self.assertEqual(scenario_names, ["maintenance_lookup", "normalization"])

    def test_build_orchestration_evidence_includes_structured_fact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AgentRunner(
                storage=AgentStorage(base_dir=Path(temp_dir)),
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient([]),
                logger=logging.getLogger("test.agent.runner.evidence"),
            )
            context_data = {
                "card": {
                    "id": "card-1",
                    "title": "BMW 320i",
                    "vehicle": "BMW 320i",
                    "description": "VIN: WBAPF71060A798127\nP0420\nТечь антифриза.",
                    "vehicle_profile": {"mileage": "123000"},
                    "repair_order": {},
                },
                "events": [],
                "repair_order_text": {"text": ""},
            }
            evidence, facts = runner._build_orchestration_evidence(
                task={"id": "task-1", "task_text": "Проверь карточку"},
                metadata={"context": {"card_id": "card-1"}},
                task_type="card_cleanup",
                context_kind="card",
                context_data=context_data,
                raw_context_ref="ctx:test",
            )
            self.assertIn("vin", evidence.fact_evidence)
            self.assertEqual(evidence.fact_evidence["vin"].status, "confirmed")
            self.assertEqual(evidence.fact_evidence["part_queries"].status, "absent")
            self.assertEqual(evidence.fact_evidence["waiting_state"].status, "absent")
            self.assertEqual(evidence.fact_evidence["vehicle_context"].source, "card_context_aggregate")
            self.assertEqual(facts["vin"], "WBAPF71060A798127")
            self.assertFalse(facts["scenario_evidence"]["parts_lookup"]["confidence_enough"])

    def test_card_autofill_scenario_selection_runs_dtc_only_from_explicit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AgentRunner(
                storage=AgentStorage(base_dir=Path(temp_dir)),
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient([]),
                logger=logging.getLogger("test.agent.runner.scenario.dtc"),
            )
            facts = runner._analyze_card_autofill_context(
                {
                    "card": {
                        "id": "card-1",
                        "title": "Ошибка двигателя",
                        "vehicle": "BMW 320i",
                        "description": "Горит check engine. DTC P0300.",
                        "vehicle_profile": {},
                        "repair_order": {},
                    },
                    "events": [],
                    "repair_order_text": {"text": ""},
                }
            )
            scenario_names = [item["name"] for item in runner._select_card_autofill_scenarios(facts)]
            self.assertEqual(scenario_names, ["dtc_lookup", "normalization"])

    def test_card_autofill_scenario_selection_skips_external_steps_for_weak_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AgentRunner(
                storage=AgentStorage(base_dir=Path(temp_dir)),
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient([]),
                logger=logging.getLogger("test.agent.runner.scenario.weak"),
            )
            facts = runner._analyze_card_autofill_context(
                {
                    "card": {
                        "id": "card-1",
                        "title": "Осмотр",
                        "vehicle": "",
                        "description": "Нужно посмотреть машину.",
                        "vehicle_profile": {},
                        "repair_order": {},
                        "ai_autofill_prompt": "Поищи ТО и детали",
                    },
                    "events": [],
                    "repair_order_text": {"text": ""},
                },
                task_text="Сделай полное ТО и оцени запчасти.",
            )
            scenario_names = [item["name"] for item in runner._select_card_autofill_scenarios(facts)]
            self.assertEqual(scenario_names, ["normalization"])

    def test_card_autofill_analysis_builds_explicit_evidence_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AgentRunner(
                storage=AgentStorage(base_dir=Path(temp_dir)),
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient([]),
                logger=logging.getLogger("test.agent.runner.scenario.evidence"),
            )
            facts = runner._analyze_card_autofill_context(
                {
                    "card": {
                        "id": "card-1",
                        "title": "BMW 320i",
                        "vehicle": "",
                        "description": "VIN: WBAPF71060A798127\nНужно найти радиатор.\nПробег: 120000\nНужно ТО с заменой масла.\nDTC P0300.",
                        "vehicle_profile": {},
                        "repair_order": {},
                    },
                    "events": [],
                    "repair_order_text": {"text": ""},
                }
            )
            evidence = facts["evidence_model"]
            self.assertTrue(evidence["vin_found"])
            self.assertTrue(evidence["explicit_part_found"])
            self.assertTrue(evidence["maintenance_context_found"])
            self.assertTrue(evidence["mileage_found"])
            self.assertTrue(evidence["dtc_found"])
            self.assertFalse(evidence["fault_symptoms_found"])

    def test_card_autofill_analysis_builds_clean_symptom_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AgentRunner(
                storage=AgentStorage(base_dir=Path(temp_dir)),
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient([]),
                logger=logging.getLogger("test.agent.runner.scenario.symptom_query"),
            )
            facts = runner._analyze_card_autofill_context(
                {
                    "card": {
                        "id": "card-1",
                        "title": "Течь антифриза — BMW 320I 2017",
                        "vehicle": "BMW 320I 2017",
                        "description": (
                            "Клиент: Ибрагимова Диана Евгеньевна\n"
                            "Телефон: +7 (967) 609-76-79\n"
                            "VIN: X4X8A594905J20193\n"
                            "Течь антифриза"
                        ),
                        "vehicle_profile": {
                            "make_display": "BMW",
                            "model_display": "320I",
                            "production_year": 2017,
                            "vin": "X4X8A594905J20193",
                        },
                        "repair_order": {},
                    },
                    "events": [],
                    "repair_order_text": {"text": ""},
                }
            )
            self.assertIn("Течь антифриза", facts["symptom_query"])
            self.assertNotIn("Ибрагимова", facts["symptom_query"])
            self.assertNotIn("+7", facts["symptom_query"])

    def test_card_autofill_plan_normalizes_scenario_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AgentRunner(
                storage=AgentStorage(base_dir=Path(temp_dir)),
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient([]),
                logger=logging.getLogger("test.agent.runner.plan.labels"),
            )
            facts = runner._analyze_card_autofill_context(
                {
                    "card": {
                        "id": "card-1",
                        "title": "BMW 320i",
                        "vehicle": "BMW 320i",
                        "description": "VIN: WBAPF71060A798127\nНужно найти радиатор и цену.\nDTC P0300.",
                        "vehicle_profile": {},
                        "repair_order": {},
                    },
                    "events": [],
                    "repair_order_text": {"text": ""},
                }
            )
            plan = runner._build_card_autofill_plan(facts)
            labels = [item.get("label", "") for item in plan["scenarios"] if item.get("name") != "normalization"]
            self.assertEqual(labels, ["VIN", "PARTS", "DTC"])

    def test_card_autofill_plan_message_uses_clean_ascii_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AgentRunner(
                storage=AgentStorage(base_dir=Path(temp_dir)),
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient([]),
                logger=logging.getLogger("test.agent.runner.plan.message"),
            )
            message = runner._build_card_autofill_plan_message(
                [
                    {"name": "vin_enrichment", "label": "VIN"},
                    {"name": "parts_lookup", "label": "PARTS"},
                    {"name": "normalization", "label": "STRUCTURE"},
                ],
                facts={
                    "autofill_plan": {"skipped": [{"name": "maintenance_lookup", "reason": "no mileage"}]},
                    "related_cards": [{"id": "card-2"}],
                },
            )
            self.assertIn("План: VIN -> PARTS -> STRUCTURE", message)
            self.assertIn("Gated: maintenance_lookup.", message)
            self.assertIn("Связанных карточек на доске: 1.", message)
            self.assertNotIn("Р ", message)
            self.assertNotIn("вЂ", message)

    def test_runner_executes_tool_and_completes_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(task_text="Review the board.")
            logger = logging.getLogger("test.agent.runner")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient(
                    [
                        {"type": "tool", "tool": "review_board", "args": {"priority_limit": 3}, "reason": "Need board state"},
                        {"type": "final", "summary": "Board reviewed", "result": "No blocking issues."},
                    ]
                ),
                logger=logger,
            )
            processed = runner.run_once()
            self.assertTrue(processed)
            task = storage.list_tasks(limit=1)[0]
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["summary"], "Board reviewed")
            self.assertEqual(task["display"]["title"], "Board reviewed")
            actions = storage.list_actions(limit=10)
            self.assertEqual(len(actions), 4)
            self.assertEqual(actions[0]["kind"], "log")
            self.assertEqual(actions[1]["kind"], "tool")
            self.assertEqual(actions[-1]["message"], "Задача агента запущена.")

    def test_runner_persists_orchestration_trace_in_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(task_text="Review the board.")
            logger = logging.getLogger("test.agent.runner.trace.board")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient(
                    [
                        {"type": "tool", "tool": "review_board", "args": {"priority_limit": 3}, "reason": "Need board state"},
                        {"type": "final", "summary": "Board reviewed", "result": "No blocking issues."},
                    ]
                ),
                logger=logger,
            )
            self.assertTrue(runner.run_once())
            run = storage.list_runs(limit=1)[0]
            orchestration = run.get("orchestration") if isinstance(run.get("orchestration"), dict) else {}
            self.assertEqual(orchestration.get("version"), "agent_orchestrator_v1")
            self.assertEqual(orchestration.get("plan", {}).get("scenario_id"), "board_review")
            self.assertIn("confidence_mode", orchestration.get("plan", {}))
            self.assertIn("write_mode", orchestration.get("plan", {}))
            self.assertTrue(orchestration.get("verify", {}).get("scenario_completed"))

    def test_runner_policy_gate_requires_vin_tool_before_final(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Расшифруй VIN по этой карточке.",
                metadata={"context": {"kind": "card", "card_id": "card-1", "title": "Диагностика"}},
            )
            logger = logging.getLogger("test.agent.runner.policy.vin")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            model = _FakeModelClient(
                [
                    {"type": "final", "summary": "Готово", "result": "VIN вроде понятен."},
                    {"type": "tool", "tool": "decode_vin", "args": {"vin": "WBAPF71060A798127"}, "reason": "Need required VIN decode"},
                    {"type": "final", "summary": "VIN decoded", "result": "Confirmed via tool."},
                ]
            )
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeWrappedBoardApi(),
                model_client=model,
                logger=logger,
            )
            runner._tools._automotive = _FakeAutomotiveService()
            self.assertTrue(runner.run_once())
            self.assertEqual(len(model.calls), 3)
            self.assertIn("Policy gate", model.calls[1]["messages"][-1]["content"])
            run = storage.list_runs(limit=1)[0]
            self.assertIn("decode_vin", run["orchestration"]["plan"]["required_tools"])

    def test_runner_verify_uses_completed_tool_results_for_required_tool_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Расшифруй VIN по этой карточке и внеси подтвержденные данные.",
                metadata={"context": {"kind": "card", "card_id": "card-1", "title": "Диагностика"}},
            )
            logger = logging.getLogger("test.agent.runner.verify.required")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _ConsistentWrappedBoardApi()
            model = _FakeModelClient(
                [
                    {"type": "tool", "tool": "decode_vin", "args": {"vin": "WBAPF71060A798127"}, "reason": "Need VIN facts"},
                    {
                        "type": "final",
                        "summary": "VIN decoded",
                        "result": "Applied VIN data.",
                        "apply": {
                            "type": "update_card",
                            "card_id": "card-1",
                            "payload": {
                                "description": "ИИ: VIN подтвержден.",
                                "vehicle_profile": {"vin": "WBAPF71060A798127"},
                            },
                        },
                    },
                ]
            )
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=model,
                logger=logger,
            )
            runner._tools._automotive = _FakeAutomotiveService()
            self.assertTrue(runner.run_once())
            run = storage.list_runs(limit=1)[0]
            verify = run["orchestration"]["verify"]
            self.assertTrue(verify["applied_ok"])
            self.assertTrue(verify["scenario_completed"])
            self.assertFalse(verify["needs_followup"])
            self.assertEqual(verify["outcome_state"], "needs_human_review")
            self.assertNotIn("missing required tools: decode_vin", verify["warnings"])

    def test_finalize_verify_result_escalates_manual_field_drift_to_human_review(self) -> None:
        runner = AgentRunner(
            storage=AgentStorage(base_dir=Path(tempfile.mkdtemp())),
            board_api=_FakeBoardApi(),
            model_client=_FakeModelClient([]),
            logger=logging.getLogger("test.agent.runner.verify.manual_drift"),
        )
        plan = runner._policy.build_plan(
            scenario_chain=["vin_enrichment"],
            execution_mode="structured_card",
            followup_enabled=True,
        )
        verify = VerifyResult(
            applied_ok=True,
            fields_changed=["vehicle_profile"],
            manual_fields_preserved=False,
            scenario_completed=True,
            needs_followup=False,
            outcome_state="write_applied",
            warnings=["title changed outside planned patch"],
            context_ref="verify:card-1",
        )
        tool_results = [
            ToolResult(
                tool_name="decode_vin",
                status="success",
                source_type="external_vin",
                confidence=0.92,
                data={"vin": "WBAPF71060A798127"},
                raw_ref="vin_enrichment:decode_vin",
                evidence_ref="vin",
                reason="VIN decode completed",
            )
        ]
        final = runner._finalize_verify_result(plan=plan, verify=verify, tool_results=tool_results)
        self.assertEqual(final.outcome_state, "needs_human_review")
        self.assertTrue(final.scenario_completed)
        self.assertFalse(final.manual_fields_preserved)
        self.assertFalse(final.needs_followup)

    def test_merge_verify_feedback_preserves_executor_followup_hints(self) -> None:
        runner = AgentRunner(
            storage=AgentStorage(base_dir=Path(tempfile.mkdtemp())),
            board_api=_FakeBoardApi(),
            model_client=_FakeModelClient([]),
            logger=logging.getLogger("test.agent.runner.verify.feedback"),
        )
        verify = VerifyResult(
            applied_ok=True,
            fields_changed=["description"],
            manual_fields_preserved=True,
            scenario_completed=True,
            needs_followup=False,
            outcome_state="write_applied",
            warnings=[],
            context_ref="verify:card-1",
        )
        merged = runner._merge_verify_feedback(
            verify,
            warnings=["scenario warning"],
            needs_followup=True,
            followup_reason="scenario_requested_followup",
        )
        self.assertTrue(merged.applied_ok)
        self.assertTrue(merged.needs_followup)
        self.assertEqual(merged.followup_reason, "scenario_requested_followup")
        self.assertIn("scenario warning", merged.warnings)

    def test_finalize_verify_result_preserves_requested_followup_for_completed_run(self) -> None:
        runner = AgentRunner(
            storage=AgentStorage(base_dir=Path(tempfile.mkdtemp())),
            board_api=_FakeBoardApi(),
            model_client=_FakeModelClient([]),
            logger=logging.getLogger("test.agent.runner.verify.requested_followup"),
        )
        plan = runner._policy.build_plan(
            scenario_chain=["normalization"],
            execution_mode="structured_card",
            followup_enabled=True,
        )
        verify = VerifyResult(
            applied_ok=True,
            fields_changed=["description"],
            manual_fields_preserved=True,
            scenario_completed=True,
            needs_followup=True,
            outcome_state="write_applied",
            warnings=["scenario warning"],
            context_ref="verify:card-1",
            followup_reason="scenario_requested_followup",
        )
        final = runner._finalize_verify_result(plan=plan, verify=verify, tool_results=[])
        self.assertTrue(final.applied_ok)
        self.assertTrue(final.scenario_completed)
        self.assertTrue(final.needs_followup)
        self.assertEqual(final.followup_reason, "scenario_requested_followup")

    def test_verify_contract_write_requires_full_patch_match_for_confirmed_success(self) -> None:
        runner = AgentRunner(
            storage=AgentStorage(base_dir=Path(tempfile.mkdtemp())),
            board_api=_FakeBoardApi(),
            model_client=_FakeModelClient([]),
            logger=logging.getLogger("test.agent.runner.verify.partial_patch"),
        )
        plan = runner._policy.build_plan(
            scenario_chain=["vin_enrichment"],
            execution_mode="structured_card",
            followup_enabled=True,
        )
        before_state = {
            "card": {
                "id": "card-1",
                "description": "Исходный текст карточки.",
                "title": "Черновик",
                "vehicle": "BMW 320I",
                "tags": [],
                "vehicle_profile": {},
            }
        }
        after_state = {
            "card": {
                "id": "card-1",
                "description": "Исходный текст карточки.\nИИ: VIN подтвержден.",
                "title": "Черновик",
                "vehicle": "BMW 320I",
                "tags": [],
                "vehicle_profile": {},
            }
        }
        patch = runner._policy.filter_patch(
            plan,
            PatchResult(
                card_patch={
                    "description": "Исходный текст карточки.\nИИ: VIN подтвержден.",
                    "vehicle_profile": {"vin": "WBAPF71060A798127"},
                }
            ),
        )
        with mock.patch.object(runner, "_read_verification_state", return_value=after_state):
            verify = runner._verify_contract_write(
                tool_name="update_card",
                card_id="card-1",
                before_state=before_state,
                patch=patch,
                plan=plan,
            )
        self.assertFalse(verify.applied_ok)
        self.assertFalse(verify.scenario_completed)
        self.assertIn("description", verify.fields_changed)
        self.assertIn("vehicle_profile verification mismatch", verify.warnings)

    def test_runner_persists_structured_display_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(task_text="List overdue cards.")
            logger = logging.getLogger("test.agent.runner.display")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient(
                    [
                        {
                            "type": "final",
                            "summary": "Overdue cards found",
                            "result": "Fallback text.",
                            "display": {
                                "emoji": "⚠️",
                                "title": "Просрочки",
                                "summary": "Найдены карточки с просрочкой.",
                                "tone": "warning",
                                "sections": [{"title": "Приоритет", "items": ["BMW X5", "Renault Duster"]}],
                                "actions": ["Покажи детали просрочек"],
                            },
                        }
                    ]
                ),
                logger=logger,
            )
            processed = runner.run_once()
            self.assertTrue(processed)
            task = storage.list_tasks(limit=1)[0]
            self.assertEqual(task["display"]["emoji"], "⚠️")
            self.assertEqual(task["display"]["tone"], "warning")
            self.assertEqual(task["display"]["sections"][0]["title"], "Приоритет")

    def test_runner_records_card_autofill_log_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Проверь карточку и дополни только при необходимости.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Диагностика"},
                },
            )
            logger = logging.getLogger("test.agent.runner.autofill")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient(
                    [
                        {"type": "final", "summary": "Готово", "result": "Изменения не требуются."},
                        {"type": "final", "summary": "done", "result": "No safe card changes were needed."},
                    ]
                ),
                logger=logger,
            )
            processed = runner.run_once()
            self.assertTrue(processed)
            actions = storage.list_actions(limit=10)
            log_messages = [item.get("message", "") for item in actions if item.get("kind") == "log"]
            self.assertIn("Первый проход автосопровождения запущен.", log_messages)
            self.assertIn("Начат анализ карточки.", log_messages)
            self.assertTrue(any(message.startswith("План:") for message in log_messages))
            self.assertIn("Изменений не обнаружено.", log_messages)

    def test_runner_merges_card_autofill_description_in_additive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Дополняй карточку краткими ИИ-комментариями по делу.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Черновик"},
                },
            )
            logger = logging.getLogger("test.agent.runner.autofill.apply")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeBoardApi()
            board_api.cards["card-1"]["description"] = (
                "VIN: WBAPF71060A798127\n"
                "Бежит антифриз.\n"
                "Проверили: течь в основном радиаторе.\n"
                "Нужно найти радиатор и сориентировать по цене.\n"
                "Цена детали 5000.\n"
                "Артикул ABC-123."
            )
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient([{"type": "final", "summary": "unused", "result": "unused"}]),
                logger=logger,
            )
            runner._tools._automotive = _FakeAutomotiveService()
            self.assertTrue(runner.run_once())
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertIn("Бежит антифриз.", update_call[1]["description"])
            self.assertIn("Цена детали 5000.", update_call[1]["description"])
            self.assertIn("Артикул ABC-123.", update_call[1]["description"])
            self.assertIn("ИИ:", update_call[1]["description"])
            self.assertIn("По VIN подтверждено", update_call[1]["description"])
            self.assertIn("Радиатор: OEM 17118625431; аналоги: AVA BW2285.", update_call[1]["description"])
            self.assertIn("Ориентир по РФ: 14 500-21 900 ₽ (3 предложений).", update_call[1]["description"])
            self.assertEqual(update_call[1]["vehicle"], "BMW 320i 2016")
            self.assertIsInstance(update_call[1]["vehicle_profile"], dict)
            self.assertEqual(update_call[1]["vehicle_profile"]["make_display"], "BMW")

    def test_runner_card_autofill_dedupes_repeated_description_blocks(self) -> None:
        runner = AgentRunner(
            storage=AgentStorage(base_dir=Path(tempfile.mkdtemp())),
            board_api=_FakeBoardApi(),
            model_client=_FakeModelClient([{"type": "final", "summary": "unused", "result": "unused"}]),
            logger=logging.getLogger("test.agent.runner.autofill.dedupe"),
        )
        current = "Строка 1.\nСтрока 2."
        proposed = current + "\n\n" + current + "\n\nAI: Подтвержден VIN и нужна проверка радиатора."
        merged = runner._merge_card_autofill_description(current, proposed)
        for line in [item.strip() for item in current.splitlines() if item.strip()]:
            self.assertEqual(merged.count(line), 1)
        self.assertIn("AI: Подтвержден VIN и нужна проверка радиатора.", merged)

    def test_runner_card_autofill_calls_external_decode_before_parts_lookup(self) -> None:
        class _RecordingAutomotive(_FakeAutomotiveService):
            def __init__(self) -> None:
                self.calls: list[str] = []

            def decode_vin(self, vin: str) -> dict:
                self.calls.append("decode_vin")
                return super().decode_vin(vin)

            def find_part_numbers(self, *, query: str, vehicle: dict[str, object] | str | None = None, limit: int = 5) -> dict:
                self.calls.append("find_part_numbers")
                return super().find_part_numbers(query=query, vehicle=vehicle, limit=limit)

            def estimate_price_ru(self, *, part_number: str, vehicle: dict[str, object] | str | None = None, limit: int = 5) -> dict:
                self.calls.append("estimate_price_ru")
                return super().estimate_price_ru(part_number=part_number, vehicle=vehicle, limit=limit)

        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Автосопровождение карточки.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Радиатор"},
                },
            )
            logger = logging.getLogger("test.agent.runner.autofill.vin.parts")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeWrappedBoardApi()
            board_api.cards["card-1"]["description"] = (
                "VIN: WBAPF71060A798127\n"
                "Бежит антифриз.\n"
                "Течет основной радиатор.\n"
                "Нужно найти радиатор и сориентировать по цене."
            )
            automotive = _RecordingAutomotive()
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient([{"type": "final", "summary": "unused", "result": "unused"}]),
                logger=logger,
            )
            runner._tools._automotive = automotive
            self.assertTrue(runner.run_once())
            self.assertEqual(automotive.calls[:3], ["decode_vin", "find_part_numbers", "estimate_price_ru"])
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertIn("По VIN подтверждено: BMW, 320i, 2016", update_call[1]["description"])
            self.assertIn("Радиатор: OEM 17118625431; аналоги: AVA BW2285.", update_call[1]["description"])
            self.assertNotIn("уточнить модель", update_call[1]["description"].lower())
            log_messages = [item.get("message", "") for item in storage.list_actions(limit=50) if item.get("kind") == "log"]
            self.assertIn("VIN found.", log_messages)
            self.assertIn("decode_vin requested.", log_messages)
            self.assertIn("decode_vin success.", log_messages)
            self.assertIn("parts lookup started.", log_messages)
            self.assertIn("fields updated.", log_messages)

    def test_runner_card_autofill_does_not_guess_vin_decode_when_external_result_is_insufficient(self) -> None:
        class _InsufficientVinAutomotive(_FakeAutomotiveService):
            def decode_vin(self, vin: str) -> dict:
                return {
                    "vin": vin,
                    "make": "BMW",
                    "plant_country": "Germany",
                    "source_url": "https://example.test/vin",
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Автосопровождение карточки.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "VIN only"},
                },
            )
            logger = logging.getLogger("test.agent.runner.autofill.vin.insufficient")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeWrappedBoardApi()
            board_api.cards["card-1"]["description"] = "VIN: WBAPF71060A798127"
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient([{"type": "final", "summary": "unused", "result": "unused"}]),
                logger=logger,
            )
            runner._tools._automotive = _InsufficientVinAutomotive()
            self.assertTrue(runner.run_once())
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertIn("выполнена внешняя расшифровка, но данных недостаточно", update_call[1]["description"])
            self.assertNotIn("По VIN подтверждено: BMW", update_call[1]["description"])
            self.assertFalse(update_call[1].get("vehicle_profile"))
            self.assertFalse(update_call[1].get("vehicle"))
            log_messages = [item.get("message", "") for item in storage.list_actions(limit=50) if item.get("kind") == "log"]
            self.assertIn("decode_vin requested.", log_messages)
            self.assertIn("decode_vin insufficient.", log_messages)
            run = storage.list_runs(limit=1)[0]
            verify = run["orchestration"]["verify"]
            self.assertEqual(verify["outcome_state"], "blocked_missing_source_data")
            self.assertEqual(verify["followup_reason"], "vin_decode_insufficient")
            fact_evidence = run["orchestration"]["evidence"]["fact_evidence"]
            self.assertIn("vin_fallback_context", fact_evidence)

    def test_runner_card_autofill_skips_blind_parts_lookup_after_failed_vin_gate(self) -> None:
        class _BlankVehicleWrappedBoardApi(_FakeWrappedBoardApi):
            def get_card_context(self, card_id: str, **kwargs) -> dict:
                payload = super().get_card_context(card_id, **kwargs)
                payload["data"]["card"]["vehicle"] = ""
                payload["data"]["card"]["vehicle_profile"] = {"vin": "WBAPF71060A798127"}
                return payload

        class _FailedVinAutomotive(_FakeAutomotiveService):
            def __init__(self) -> None:
                self.calls: list[str] = []

            def decode_vin(self, vin: str) -> dict:
                self.calls.append("decode_vin")
                return {}

            def find_part_numbers(self, *, query: str, vehicle: dict[str, object] | str | None = None, limit: int = 5) -> dict:
                self.calls.append("find_part_numbers")
                return super().find_part_numbers(query=query, vehicle=vehicle, limit=limit)

        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Автосопровождение карточки.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Радиатор"},
                },
            )
            logger = logging.getLogger("test.agent.runner.autofill.vin.gated.parts")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _BlankVehicleWrappedBoardApi()
            board_api.cards["card-1"]["description"] = (
                "VIN: WBAPF71060A798127\n"
                "Течет основной радиатор.\n"
                "Нужно найти радиатор и сориентировать по цене."
            )
            automotive = _FailedVinAutomotive()
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient([{"type": "final", "summary": "unused", "result": "unused"}]),
                logger=logger,
            )
            runner._tools._automotive = automotive
            self.assertTrue(runner.run_once())
            self.assertEqual(automotive.calls, ["decode_vin"])
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertIn("внешней расшифровки", update_call[1]["description"])
            self.assertNotIn("OEM", update_call[1]["description"])
            log_messages = [item.get("message", "") for item in storage.list_actions(limit=50) if item.get("kind") == "log"]
            self.assertIn("parts lookup skipped: no trusted vehicle context after VIN gate.", log_messages)

    def test_runner_card_autofill_marks_empty_parts_lookup_as_partial_followup(self) -> None:
        class _EmptyPartsAutomotive(_FakeAutomotiveService):
            def find_part_numbers(self, *, query: str, vehicle: dict[str, object] | str | None = None, limit: int = 5) -> dict:
                return {
                    "query": query,
                    "vehicle_context": vehicle if isinstance(vehicle, dict) else {"vehicle": str(vehicle or "").strip()},
                    "part_numbers": [],
                    "results": [],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Автосопровождение карточки.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Радиатор"},
                },
            )
            logger = logging.getLogger("test.agent.runner.autofill.parts.partial")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeWrappedBoardApi()
            board_api.cards["card-1"]["description"] = (
                "VIN: WBAPF71060A798127\n"
                "Течет основной радиатор.\n"
                "Нужно найти радиатор и сориентировать по цене."
            )
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient([{"type": "final", "summary": "unused", "result": "unused"}]),
                logger=logger,
            )
            runner._tools._automotive = _EmptyPartsAutomotive()
            self.assertTrue(runner.run_once())
            run = storage.list_runs(limit=1)[0]
            verify = run["orchestration"]["verify"]
            self.assertTrue(verify["applied_ok"])
            self.assertFalse(verify["scenario_completed"])
            self.assertTrue(verify["needs_followup"])
            self.assertEqual(verify["outcome_state"], "completed_partial")
            self.assertEqual(verify["followup_reason"], "parts_lookup_insufficient")
            self.assertIn("parts lookup completed without reliable candidate parts", verify["warnings"])

    def test_runner_card_autofill_marks_failed_parts_lookup_as_partial_followup(self) -> None:
        class _FailingPartsAutomotive(_FakeAutomotiveService):
            def find_part_numbers(self, *, query: str, vehicle: dict[str, object] | str | None = None, limit: int = 5) -> dict:
                raise RuntimeError("temporary lookup failure")

        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Автосопровождение карточки.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Радиатор"},
                },
            )
            logger = logging.getLogger("test.agent.runner.autofill.parts.failed")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeWrappedBoardApi()
            board_api.cards["card-1"]["description"] = (
                "VIN: WBAPF71060A798127\n"
                "Течет основной радиатор.\n"
                "Нужно найти радиатор и сориентировать по цене."
            )
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient([{"type": "final", "summary": "unused", "result": "unused"}]),
                logger=logger,
            )
            runner._tools._automotive = _FailingPartsAutomotive()
            self.assertTrue(runner.run_once())
            run = storage.list_runs(limit=1)[0]
            verify = run["orchestration"]["verify"]
            self.assertTrue(verify["applied_ok"])
            self.assertFalse(verify["scenario_completed"])
            self.assertTrue(verify["needs_followup"])
            self.assertEqual(verify["outcome_state"], "completed_partial")
            self.assertEqual(verify["followup_reason"], "parts_lookup_failed")
            self.assertIn("parts lookup request failed", verify["warnings"])

    def test_runner_unwraps_wrapped_card_context_for_deterministic_autofill(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Расшифруй VIN и добавь краткую полезную ИИ-заметку.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Черновик"},
                },
            )
            logger = logging.getLogger("test.agent.runner.wrapped.context")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeWrappedBoardApi()
            board_api.cards["card-1"]["description"] = "VIN: WBAPF71060A798127\nНужно понять, что с машиной."
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient([{"type": "final", "summary": "unused", "result": "unused"}]),
                logger=logger,
            )
            runner._tools._automotive = _FakeAutomotiveService()
            self.assertTrue(runner.run_once())
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertIn("VIN: WBAPF71060A798127", update_call[1]["description"])
            self.assertIn("По VIN подтверждено: BMW, 320i, 2016", update_call[1]["description"])
            self.assertEqual(update_call[1]["vehicle"], "BMW 320i 2016")
            self.assertEqual(update_call[1]["vehicle_profile"]["production_year"], 2016)

    def test_runner_adds_maintenance_context_with_mileage_and_capacities(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Подготовь полезное ТО по этой карточке.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "ТО"},
                },
            )
            logger = logging.getLogger("test.agent.runner.autofill.maintenance")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeWrappedBoardApi()
            board_api.cards["card-1"]["description"] = "Пробег: 120000\nНужно большое ТО с заменой масла."
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient([{"type": "final", "summary": "unused", "result": "unused"}]),
                logger=logger,
            )
            runner._tools._automotive = _FakeAutomotiveService()
            self.assertTrue(runner.run_once())
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertIn("ТО", update_call[1]["description"])
            self.assertIn("Расходники", update_call[1]["description"])

    def test_runner_verify_accepts_additive_description_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Расшифруй VIN и добавь краткую полезную ИИ-заметку.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Черновик"},
                },
            )
            logger = logging.getLogger("test.agent.runner.verify.additive")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeWrappedBoardApi()
            board_api.cards["card-1"]["description"] = "Исходный текст карточки.\nНужно понять, что с машиной."
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient([{"type": "final", "summary": "unused", "result": "unused"}]),
                logger=logger,
            )
            runner._tools._automotive = _FakeAutomotiveService()
            self.assertTrue(runner.run_once())
            run = storage.list_runs(limit=1)[0]
            verify = run["orchestration"]["verify"]
            self.assertTrue(verify["applied_ok"])
            self.assertNotIn("description verification mismatch", verify["warnings"])

    def test_runner_merges_card_autofill_description_with_wrapped_get_card_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Дополняй карточку только короткой ИИ-заметкой.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Черновик"},
                },
            )
            logger = logging.getLogger("test.agent.runner.wrapped.apply")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeWrappedBoardApi()
            board_api.cards["card-1"]["description"] = (
                "VIN: WBAPF71060A798127\n"
                "Бежит антифриз.\n"
                "Проверили течь: основной радиатор."
            )
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient([{"type": "final", "summary": "unused", "result": "unused"}]),
                logger=logger,
            )
            runner._tools._automotive = _FakeAutomotiveService()
            self.assertTrue(runner.run_once())
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertIn("Бежит антифриз.", update_call[1]["description"])
            self.assertIn("ИИ:", update_call[1]["description"])
            self.assertIn("По VIN подтверждено: BMW, 320i, 2016", update_call[1]["description"])

    def test_runner_includes_card_context_in_model_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Расшифруй VIN",
                metadata={
                    "requested_by": "operator",
                    "context": {
                        "kind": "card",
                        "card_id": "card-1",
                        "title": "Диагностика",
                        "vin": "MMCJJKL10NH019836",
                    },
                },
            )
            logger = logging.getLogger("test.agent.runner.context")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            model = _FakeModelClient([{"type": "final", "summary": "done", "result": "ok"}])
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeBoardApi(),
                model_client=model,
                logger=logger,
            )
            processed = runner.run_once()
            self.assertTrue(processed)
            call = model.calls[0]
            self.assertIn("decode_vin", str(call["system_prompt"]))
            self.assertIn("This task was opened from a card.", call["messages"][0]["content"])
            self.assertIn('"card_id": "card-1"', call["messages"][0]["content"])

    def test_runner_requires_update_card_before_finalizing_card_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Наведи порядок в этой карточке и структурируй данные.",
                metadata={
                    "context": {
                        "kind": "card",
                        "card_id": "card-1",
                        "title": "Черновик",
                    }
                },
            )
            logger = logging.getLogger("test.agent.runner.cleanup")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeBoardApi()
            model = _FakeModelClient(
                [
                    {"type": "final", "summary": "Готово", "result": "Предлагаю обновления."},
                    {
                        "type": "tool",
                        "tool": "update_card",
                        "args": {
                            "card_id": "card-1",
                            "title": "Mitsubishi L200 / Диагностика подвески",
                            "description": "Структурированное описание без потери данных.",
                            "tags": ["диагностика", "подвеска"],
                        },
                        "reason": "Apply cleanup changes to the current card",
                    },
                    {"type": "final", "summary": "Карточка обновлена", "result": "Изменения применены."},
                ]
            )
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=model,
                logger=logger,
            )
            processed = runner.run_once()
            self.assertTrue(processed)
            task = storage.list_tasks(limit=1)[0]
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["summary"], "Карточка обновлена")
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertEqual(update_call[1]["card_id"], "card-1")
            self.assertEqual(update_call[1]["title"], "Mitsubishi L200 / Диагностика подвески")
            self.assertEqual(update_call[1]["description"], "Структурированное описание без потери данных.")
            self.assertEqual(update_call[1]["tags"], ["диагностика", "подвеска"])
            self.assertEqual(len(model.calls), 3)
            self.assertIn("Apply confident changes to card card-1 with update_card before the final answer.", model.calls[1]["messages"][-1]["content"])

    def test_runner_applies_structured_card_update_from_final_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Наведи порядок в этой карточке.",
                metadata={"context": {"kind": "card", "card_id": "card-1", "title": "Черновик"}},
            )
            logger = logging.getLogger("test.agent.runner.apply")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeBoardApi()
            model = _FakeModelClient(
                [
                    {
                        "type": "final",
                        "summary": "Карточка приведена в порядок",
                        "result": "Изменения применены.",
                        "display": {"title": "Карточка обновлена", "summary": "Нормализованы поля."},
                        "apply": {
                            "type": "update_card",
                            "card_id": "card-1",
                            "payload": {
                                "title": "Mitsubishi L200 / диагностика",
                                "description": "Структурированное описание.",
                                "tags": ["диагностика"],
                            },
                        },
                    }
                ]
            )
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=model,
                logger=logger,
            )
            self.assertTrue(runner.run_once())
            task = storage.list_tasks(limit=1)[0]
            self.assertEqual(task["status"], "completed")
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertEqual(update_call[1]["card_id"], "card-1")
            self.assertEqual(update_call[1]["title"], "Mitsubishi L200 / диагностика")
            applied_section = task["display"]["sections"][0]
            self.assertEqual(applied_section["title"], "Применено")
            self.assertTrue(any("краткая суть" in item for item in applied_section["items"]))
            run = storage.list_runs(limit=1)[0]
            verify = run["orchestration"]["verify"]
            self.assertTrue(verify["applied_ok"])
            self.assertIn("title", verify["fields_changed"])

    def test_tool_executor_exposes_automotive_internet_tools(self) -> None:
        executor = AgentRunner(
            storage=AgentStorage(base_dir=Path(tempfile.mkdtemp())),
            board_api=_FakeBoardApi(),
            model_client=_FakeModelClient([{"type": "final", "summary": "done", "result": "ok"}]),
            logger=logging.getLogger("test.agent.runner.tools"),
        )._tools
        tool_names = {item.name for item in executor.definitions}
        self.assertIn("decode_vin", tool_names)
        self.assertIn("search_part_numbers", tool_names)
        self.assertIn("lookup_part_prices", tool_names)
        self.assertIn("estimate_maintenance", tool_names)


class AutomotiveLookupServiceTests(unittest.TestCase):
    class _FakeSearch:
        def __init__(self, *, search_results: list | None = None, excerpts: dict[str, dict] | None = None) -> None:
            self._search_results = list(search_results or [])
            self._excerpts = dict(excerpts or {})

        def search(self, query: str, *, limit: int = 5, allowed_domains: list[str] | None = None) -> list:
            return self._search_results[:limit]

        def fetch_page_excerpt(self, url: str, *, max_chars: int = 2500) -> dict:
            return self._excerpts.get(url, {"url": url, "domain": "", "excerpt": ""})

    class _FakeResult:
        def __init__(self, *, title: str, url: str, snippet: str, domain: str) -> None:
            self.title = title
            self.url = url
            self.snippet = snippet
            self.domain = domain

        def to_dict(self) -> dict[str, str]:
            return {
                "title": self.title,
                "url": self.url,
                "snippet": self.snippet,
                "domain": self.domain,
            }

    def test_estimate_maintenance_returns_readable_russian_plan(self) -> None:
        service = AutomotiveLookupService()
        result = service.estimate_maintenance(
            vehicle_context={"make": "Mitsubishi", "model": "L200", "year": "2022", "vin": "MMCJJKL10NH019836"},
            service_type="ТО и тормоза",
        )
        self.assertEqual(result["service_type"], "ТО и тормоза")
        self.assertIn("Замена моторного масла", {item["name"] for item in result["works"]})
        self.assertIn("Осмотр тормозной системы", {item["name"] for item in result["works"]})
        self.assertIn("Тормозная жидкость", {item["name"] for item in result["materials"]})
        self.assertTrue(any("VIN доступен" in note for note in result["notes"]))

    def test_lookup_part_prices_extracts_price_from_snippet_before_fetch(self) -> None:
        service = AutomotiveLookupService()
        service._search = self._FakeSearch(
            search_results=[
                self._FakeResult(
                    title="Амортизатор KYB 12 500 ₽",
                    url="https://example.test/part",
                    snippet="В наличии, цена 12 500 ₽",
                    domain="example.test",
                )
            ]
        )
        result = service.lookup_part_prices(
            vehicle_context={"make": "Mitsubishi", "model": "L200", "year": "2022"},
            part_number_or_query="KYB 344123",
        )
        self.assertEqual(result["query"], "KYB 344123")
        self.assertEqual(result["results"][0]["prices"][0]["amount"], "12 500")
        self.assertEqual(result["results"][0]["prices"][0]["currency"], "₽")

    def test_search_part_numbers_uses_vin_query_when_available(self) -> None:
        calls: list[str] = []

        class _RecordingSearch(self._FakeSearch):
            def search(self, query: str, *, limit: int = 5, allowed_domains: list[str] | None = None) -> list:
                calls.append(query)
                return []

        service = AutomotiveLookupService()
        service._search = _RecordingSearch()
        result = service.search_part_numbers(
            vehicle_context={"make": "Mitsubishi", "model": "L200", "year": "2022", "vin": "MMCJJKL10NH019836"},
            part_query="рычаг передний нижний",
            limit=3,
        )
        self.assertEqual(result["vehicle_context"]["vehicle"], "Mitsubishi L200 2022")
        self.assertTrue(calls)
        self.assertIn("MMCJJKL10NH019836", calls[0])

    def test_search_part_numbers_expands_russian_part_aliases(self) -> None:
        calls: list[str] = []

        class _RecordingSearch(self._FakeSearch):
            def search(self, query: str, *, limit: int = 5, allowed_domains: list[str] | None = None) -> list:
                calls.append(query)
                return []

        service = AutomotiveLookupService()
        service._search = _RecordingSearch()
        result = service.search_part_numbers(
            vehicle_context={"make": "BMW", "model": "320i", "year": "2016", "vin": "WBAPF71060A798127"},
            part_query="радиатор",
            limit=3,
        )
        self.assertIn("radiator", " | ".join(calls).lower())
        self.assertIn("query_variants", result)
        self.assertIn("radiator", [item.lower() for item in result["query_variants"]])

    def test_search_part_numbers_prefers_plausible_oem_and_filters_catalog_noise(self) -> None:
        service = AutomotiveLookupService()
        service._search = self._FakeSearch(
            search_results=[
                self._FakeResult(
                    title="BMW exterior radiator",
                    url="https://partsouq.com/en/catalog/example-radiator",
                    snippet="Exterior radiator for BMW 320i",
                    domain="partsouq.com",
                )
            ],
            excerpts={
                "https://partsouq.com/en/catalog/example-radiator": {
                    "url": "https://partsouq.com/en/catalog/example-radiator",
                    "domain": "partsouq.com",
                    "excerpt": "OEM 17118625431. Analog BW2285. Exterior radiator. Fits 17-20 model years.",
                }
            },
        )
        result = service.search_part_numbers(
            vehicle_context={"make": "BMW", "model": "320i", "year": "2016", "vin": "WBAPF71060A798127"},
            part_query="radiator",
            limit=3,
        )
        values = [str(item.get("value", "")) for item in result["part_numbers"]]
        self.assertTrue(values)
        self.assertEqual(values[0], "17118625431")
        self.assertIn("BW2285", values)
        self.assertNotIn("RADIATOR", values)
        self.assertNotIn("EXTERIOR", values)
        self.assertNotIn("17-20", values)


class AgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(f"test.agent.api.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(state_file=state_file, logger=logger)
        self.service = CardService(self.store, logger)
        self.operator_service = OperatorAuthService(
            self.store,
            self.service,
            users_file=Path(self.temp_dir.name) / "users.json",
            logger=logger,
        )
        self.agent_storage = AgentStorage(base_dir=Path(self.temp_dir.name) / "agent")
        self.agent_service = AgentControlService(self.agent_storage)
        self.service.attach_agent_control(self.agent_service)
        self.agent_service.bind_board_service(self.service)
        self.port = reserve_port()
        self.server = ApiServer(
            self.service,
            logger,
            operator_service=self.operator_service,
            agent_service=self.agent_service,
            start_port=self.port,
            fallback_limit=1,
        )
        self.server.start()
        self.base_url = self.server.base_url

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def _request(self, path: str, payload: dict | None = None, *, method: str = "POST", headers: dict[str, str] | None = None) -> dict:
        body = None
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}{path}", data=body, headers=request_headers, method=method)
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_agent_routes_allow_operator_session_and_enqueue_task(self) -> None:
        admin_login = self._request("/api/login_operator", {"username": "admin", "password": "admin"})
        admin_token = admin_login["data"]["session"]["token"]
        admin_headers = {"X-Operator-Session": admin_token}
        self._request(
            "/api/save_operator_user",
            {"username": "worker", "password": "worker-pass"},
            headers=admin_headers,
        )
        login = self._request("/api/login_operator", {"username": "worker", "password": "worker-pass"})
        token = login["data"]["session"]["token"]
        headers = {"X-Operator-Session": token}
        queued = self._request("/api/agent_enqueue_task", {"task_text": "Review board"}, headers=headers)
        self.assertEqual(queued["data"]["task"]["status"], "pending")
        status = self._request("/api/agent_status", method="GET", headers=headers)
        self.assertEqual(status["data"]["queue"]["pending_total"], 1)

    def test_agent_schedule_routes_create_pause_resume_and_run_tasks(self) -> None:
        login = self._request("/api/login_operator", {"username": "admin", "password": "admin"})
        headers = {"X-Operator-Session": login["data"]["session"]["token"]}

        saved = self._request(
            "/api/save_agent_scheduled_task",
            {
                "name": "Проверка оплат",
                "prompt": "Проверь неоплаченные заказ-наряды и обнови карточки через MCP.",
                "scope_type": "all_cards",
                "schedule_type": "interval",
                "interval_value": 1,
                "interval_unit": "hour",
                "active": True,
            },
            headers=headers,
        )
        task_id = saved["data"]["task"]["id"]
        self.assertEqual(saved["data"]["task"]["period"], "1h")
        self.assertEqual(saved["data"]["task"]["status"], "active")

        listed = self._request("/api/agent_scheduled_tasks", method="GET", headers=headers)
        self.assertTrue(any(item["id"] == task_id for item in listed["data"]["tasks"]))

        paused = self._request("/api/pause_agent_scheduled_task", {"task_id": task_id}, headers=headers)
        self.assertEqual(paused["data"]["task"]["status"], "paused")

        resumed = self._request("/api/resume_agent_scheduled_task", {"task_id": task_id}, headers=headers)
        self.assertEqual(resumed["data"]["task"]["status"], "active")

        queued = self._request("/api/run_agent_scheduled_task", {"task_id": task_id}, headers=headers)
        self.assertEqual(queued["data"]["task"]["status"], "pending")
        self.assertEqual(queued["data"]["scheduled_task"]["id"], task_id)

    def test_agent_on_create_schedule_enqueues_single_task_for_matching_card(self) -> None:
        login = self._request("/api/login_operator", {"username": "admin", "password": "admin"})
        headers = {"X-Operator-Session": login["data"]["session"]["token"]}
        default_column = self.service.list_columns()["columns"][0]["id"]
        saved = self._request(
            "/api/save_agent_scheduled_task",
            {
                "name": "On create inbox",
                "prompt": "Inspect new inbox cards and enrich them through MCP.",
                "scope_type": "column",
                "scope_column": default_column,
                "schedule_type": "on_create",
                "active": True,
            },
            headers=headers,
        )
        task_id = saved["data"]["task"]["id"]
        self.assertEqual(saved["data"]["task"]["period"], "on_create")

        created = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Fresh card", "column": default_column, "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        queued = self.agent_storage.list_tasks(limit=10)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["metadata"]["scheduled_task_id"], task_id)
        self.assertEqual(queued[0]["metadata"]["purpose"], "scheduled_on_create")
        self.assertEqual(queued[0]["metadata"]["context"]["card_id"], card_id)

        duplicate = self.agent_service.handle_card_created({"card_id": card_id, "column": default_column})
        self.assertEqual(duplicate["launched"], [])
        self.assertEqual(len(self.agent_storage.list_tasks(limit=10)), 1)

    def test_set_card_ai_autofill_route_enqueues_current_card_task(self) -> None:
        created = self.service.create_card({"vehicle": "Toyota Corolla", "title": "AI follow-up", "deadline": {"hours": 2}})
        card_id = created["card"]["id"]

        enabled = self._request(
            "/api/set_card_ai_autofill",
            {
                "card_id": card_id,
                "enabled": True,
                "actor_name": "AI",
            },
        )
        self.assertTrue(enabled["data"]["meta"]["enabled"])
        self.assertTrue(enabled["data"]["meta"]["launched"])
        self.assertTrue(enabled["data"]["card"]["ai_autofill_active"])
        self.assertEqual(enabled["data"]["card"]["ai_run_count"], 1)

        queued = self.agent_storage.list_tasks(limit=10)
        self.assertEqual(len(queued), 1)
        task = queued[0]
        self.assertEqual(task["mode"], "card_autofill")
        self.assertEqual(task["metadata"]["scope"]["type"], "current_card")
        self.assertEqual(task["metadata"]["scope"]["card_id"], card_id)
        self.assertEqual(task["metadata"]["purpose"], "card_autofill")
