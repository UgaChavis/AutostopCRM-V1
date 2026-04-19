# ruff: noqa: E402
from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.contracts import PlanResult, ToolResult
from minimal_kanban.agent.runner import AgentRunner
from minimal_kanban.agent.scenarios import ScenarioContext, build_default_scenario_registry
from minimal_kanban.agent.scenarios.base import ScenarioExecutionResult
from minimal_kanban.agent.scenarios.vin_enrichment import (
    VinEnrichmentScenarioExecutor,
    _merge_web_enrichment,
)
from minimal_kanban.agent.storage import AgentStorage
from minimal_kanban.services.vehicle_profile_service import VehicleProfileService


class AgentScenarioRegistryTests(unittest.TestCase):
    def test_default_registry_contains_expected_scenarios(self) -> None:
        registry = build_default_scenario_registry()
        self.assertEqual(
            registry.names(),
            [
                "board_review",
                "cash_review",
                "dtc_lookup",
                "fault_research",
                "freeform_manual",
                "maintenance_lookup",
                "normalization",
                "parts_lookup",
                "repair_order_assistance",
                "vin_enrichment",
            ],
        )

    def test_registered_scenario_returns_passthrough_result(self) -> None:
        registry = build_default_scenario_registry()
        executor = registry.get("normalization")
        self.assertIsNotNone(executor)
        result = executor.execute(
            ScenarioContext(
                scenario_id="normalization",
                task_id="task-1",
                run_id="run-1",
                metadata={"purpose": "card_autofill"},
                facts={"vin": "WBAPF71060A798127"},
            )
        )
        self.assertEqual(result.scenario_id, "normalization")
        self.assertEqual(result.status, "registered")
        self.assertIn("legacy runner logic", result.notes[0])


class VinEnrichmentScenarioTests(unittest.TestCase):
    def test_sparse_decode_triggers_web_enrichment(self) -> None:
        service = VehicleProfileService()

        class _FakeRuntime:
            def __init__(self) -> None:
                self.actions: list[tuple[str, dict[str, object]]] = []
                self.logs: list[str] = []

            def _record_log_action(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
                message = str(kwargs.get("message", "") or "").strip()
                if message:
                    self.logs.append(message)

            def _run_autofill_tool(self, *, tool_name: str, args: dict[str, object], **kwargs):  # type: ignore[no-untyped-def]
                self.actions.append((tool_name, dict(args)))
                if tool_name == "decode_vin":
                    return {
                        "ok": True,
                        "data": {
                            "vin": str(args["vin"]),
                            "make": "AUDI",
                            "model": "A6",
                            "model_year": "2012",
                            "source_url": "https://vpic.nhtsa.dot.gov/api/vehicles/example",
                        },
                    }
                if tool_name == "search_web":
                    return {
                        "ok": True,
                        "data": {
                            "results": [
                                {
                                    "title": "AUDI A6 engine CDN 211 HP",
                                    "snippet": "Transmission: ZF 8HP55",
                                    "url": "https://example.com/specs",
                                }
                            ]
                        },
                    }
                if tool_name == "fetch_page_excerpt":
                    return {
                        "ok": True,
                        "data": {
                            "title": "Audi A6 2.0 TFSI specs",
                            "excerpt": "Engine: CDN. Transmission: ZF 8HP55. Power: 211 HP.",
                            "url": str(args["url"]),
                        },
                    }
                raise AssertionError(f"unexpected tool: {tool_name}")

            def _response_data(self, payload):  # type: ignore[no-untyped-def]
                if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                    return payload["data"]
                return payload if isinstance(payload, dict) else {}

            def _vin_decode_status(self, payload):  # type: ignore[no-untyped-def]
                data = payload if isinstance(payload, dict) else {}
                if any(
                    str(data.get(key, "") or "").strip()
                    for key in ("model", "model_year", "engine_model", "transmission", "drive_type")
                ):
                    return "success"
                if any(
                    str(data.get(key, "") or "").strip() for key in ("make", "plant_country", "vin")
                ):
                    return "insufficient"
                return "failed"

            def _merge_vehicle_context(self, current, decoded):  # type: ignore[no-untyped-def]
                merged = dict(current or {})
                for key, target in (
                    ("make", "make"),
                    ("model", "model"),
                    ("model_year", "year"),
                    ("engine_model", "engine"),
                    ("transmission", "gearbox"),
                    ("drive_type", "drivetrain"),
                    ("vin", "vin"),
                ):
                    if not merged.get(target) and decoded.get(key):
                        merged[target] = decoded.get(key)
                return merged

            def _build_tool_result(self, tool_name, payload, **kwargs):  # type: ignore[no-untyped-def]
                return ToolResult(
                    tool_name=tool_name,
                    status="success",
                    source_type="external_vin",
                    confidence=0.9,
                    data={"ok": True},
                    raw_ref="test",
                    evidence_ref=str(kwargs.get("evidence_ref", "")),
                    reason=str(kwargs.get("reason", "")),
                )

            def _vin_web_enrichment_required(self, decoded_vin):  # type: ignore[no-untyped-def]
                return True

            def _parse_vehicle_profile_text(
                self, text: str, *, explicit_vehicle: str = ""
            ) -> dict[str, object]:
                profile, _, _ = service._parse_text_payload(text, explicit_vehicle=explicit_vehicle)
                return profile.to_dict()

        runtime = _FakeRuntime()
        facts = {
            "vin": "WAUZZZ8V0JA000001",
            "vehicle_context": {},
            "vehicle_profile": {},
            "evidence_model": {},
        }
        result = VinEnrichmentScenarioExecutor().execute(
            ScenarioContext(
                scenario_id="vin_enrichment",
                task_id="task-1",
                run_id="run-1",
                metadata={"context": {"kind": "card", "card_id": "card-1"}},
                facts=facts,
                runtime=runtime,
            )
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.tool_calls_used, 3)
        self.assertEqual(
            [item[0] for item in runtime.actions],
            ["decode_vin", "search_web", "fetch_page_excerpt"],
        )
        decoded = result.orchestration_updates["decode_vin"]
        self.assertEqual(decoded["make"], "AUDI")
        self.assertEqual(decoded["engine_model"], "CDN")
        self.assertEqual(decoded["engine_power_hp"], 211)
        self.assertEqual(decoded.get("gearbox_model", ""), "")
        self.assertIn("web_source_urls", decoded)
        self.assertIn("web_enrichment_fields", decoded)
        self.assertIn("VIN web enrichment produced additional confirmed facts.", runtime.logs)

    def test_insufficient_decode_still_runs_web_followup(self) -> None:
        service = VehicleProfileService()

        class _FakeRuntime:
            def __init__(self) -> None:
                self.actions: list[tuple[str, dict[str, object]]] = []
                self.logs: list[str] = []

            def _record_log_action(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
                message = str(kwargs.get("message", "") or "").strip()
                if message:
                    self.logs.append(message)

            def _run_autofill_tool(self, *, tool_name: str, args: dict[str, object], **kwargs):  # type: ignore[no-untyped-def]
                self.actions.append((tool_name, dict(args)))
                if tool_name == "decode_vin":
                    return {
                        "ok": True,
                        "data": {
                            "vin": str(args["vin"]),
                            "make": "AUDI",
                            "source_url": "https://vpic.nhtsa.dot.gov/api/vehicles/example",
                        },
                    }
                if tool_name == "search_web":
                    return {
                        "ok": True,
                        "data": {
                            "results": [
                                {
                                    "title": "1988 Audi 90 Quattro Specs Review (97 kW / 132 PS / 130 hp)",
                                    "snippet": "Transmission: ZF 8HP55. Drivetrain: AWD.",
                                    "url": "https://example.com/audi-90",
                                }
                            ]
                        },
                    }
                if tool_name == "fetch_page_excerpt":
                    return {
                        "ok": True,
                        "data": {
                            "title": "1988 Audi 90 Quattro Specs Review",
                            "excerpt": "1988 Audi 90 Quattro Specs Review (97 kW / 132 PS / 130 hp). Transmission: ZF 8HP55. Drivetrain: AWD.",
                            "url": str(args["url"]),
                        },
                    }
                raise AssertionError(f"unexpected tool: {tool_name}")

            def _response_data(self, payload):  # type: ignore[no-untyped-def]
                if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                    return payload["data"]
                return payload if isinstance(payload, dict) else {}

            def _vin_decode_status(self, payload):  # type: ignore[no-untyped-def]
                data = payload if isinstance(payload, dict) else {}
                if any(
                    str(data.get(key, "") or "").strip()
                    for key in ("model", "model_year", "engine_model", "transmission", "drive_type")
                ):
                    return "success"
                if any(
                    str(data.get(key, "") or "").strip() for key in ("make", "plant_country", "vin")
                ):
                    return "insufficient"
                return "failed"

            def _merge_vehicle_context(self, current, decoded):  # type: ignore[no-untyped-def]
                merged = dict(current or {})
                for key, target in (
                    ("make", "make"),
                    ("model", "model"),
                    ("model_year", "year"),
                    ("engine_model", "engine"),
                    ("transmission", "gearbox"),
                    ("drive_type", "drivetrain"),
                    ("vin", "vin"),
                ):
                    if not merged.get(target) and decoded.get(key):
                        merged[target] = decoded.get(key)
                return merged

            def _build_tool_result(self, tool_name, payload, **kwargs):  # type: ignore[no-untyped-def]
                return ToolResult(
                    tool_name=tool_name,
                    status="success",
                    source_type="external_vin",
                    confidence=0.9,
                    data={"ok": True},
                    raw_ref="test",
                    evidence_ref=str(kwargs.get("evidence_ref", "")),
                    reason=str(kwargs.get("reason", "")),
                )

            def _vin_web_enrichment_required(self, decoded_vin):  # type: ignore[no-untyped-def]
                return True

            def _parse_vehicle_profile_text(
                self, text: str, *, explicit_vehicle: str = ""
            ) -> dict[str, object]:
                profile, _, _ = service._parse_text_payload(text, explicit_vehicle=explicit_vehicle)
                return profile.to_dict()

        runtime = _FakeRuntime()
        facts = {
            "vin": "WAUZZZ8V0JA000001",
            "vehicle_context": {},
            "vehicle_profile": {},
            "evidence_model": {},
        }
        result = VinEnrichmentScenarioExecutor().execute(
            ScenarioContext(
                scenario_id="vin_enrichment",
                task_id="task-2b",
                run_id="run-2b",
                metadata={"context": {"kind": "card", "card_id": "card-2b"}},
                facts=facts,
                runtime=runtime,
            )
        )

        self.assertEqual(result.status, "success")
        self.assertFalse(result.needs_followup)
        self.assertEqual(
            [item[0] for item in runtime.actions],
            ["decode_vin", "search_web", "fetch_page_excerpt"],
        )
        decoded = result.orchestration_updates["decode_vin"]
        self.assertEqual(decoded["make"], "AUDI")
        self.assertEqual(decoded["model"], "90")
        self.assertEqual(decoded["engine_power_hp"], 130)
        self.assertEqual(decoded["drive_type"], "AWD")
        self.assertIn("web_source_urls", decoded)
        self.assertIn("web_enrichment_fields", decoded)
        self.assertIn("engine_power_hp", decoded["web_enrichment_fields"])
        self.assertIn("drive_type", decoded["web_enrichment_fields"])
        self.assertIn("VIN web enrichment produced additional confirmed facts.", runtime.logs)

    def test_web_text_prefers_clean_vehicle_fields(self) -> None:
        service = VehicleProfileService()
        text = (
            "1988 Audi 90 Quattro Specs Review (97 kW / 132 PS / 130 hp) (since mid-year ...\n"
            "Audi V8 D1 (44) data and specifications catalogue - Automobile-Catalog\n"
            "1988 Audi 80 Specifications, Features, Safety & Warranty\n"
            "1988 Audi 80 - AudiWorld\n"
            "AUDI V8 Specs, Performance & Photos - 1988, 1989, 1990, 1991, 1992 ..."
        )
        profile, _, _ = service._parse_text_payload(text, explicit_vehicle="AUDI 1988")
        profile_data = profile.to_dict()
        self.assertEqual(profile_data["make_display"], "Audi")
        self.assertEqual(profile_data["model_display"], "90")
        self.assertEqual(profile_data["production_year"], 1988)
        self.assertEqual(profile_data["engine_power_hp"], 130)
        self.assertEqual(profile_data["drivetrain"], "AWD")
        self.assertEqual(profile_data["engine_model"], "")
        self.assertEqual(profile_data["gearbox_model"], "")

    def test_web_enrichment_keeps_gearbox_type_out_of_gearbox_model(self) -> None:
        merged, fields = _merge_web_enrichment(
            {"vin": "WAUZZZ8V0JA000001", "make": "AUDI", "model": "90"},
            {"gearbox_type": "automatic", "drivetrain": "AWD"},
        )

        self.assertEqual(merged.get("gearbox_model", ""), "")
        self.assertEqual(merged.get("transmission"), "automatic")
        self.assertEqual(merged.get("drive_type"), "AWD")
        self.assertIn("gearbox_type", fields)

    def test_runner_patch_keeps_gearbox_type_separate_from_gearbox_model(self) -> None:
        runner = object.__new__(AgentRunner)
        patch = AgentRunner._autofill_vehicle_patch(
            runner,
            facts={"vehicle_profile": {}, "vin": "WAUZZZ8V0JA000001"},
            decoded_vin={
                "vin": "WAUZZZ8V0JA000001",
                "make": "AUDI",
                "model": "90",
                "model_year": "1988",
                "gearbox_type": "automatic",
                "drive_type": "AWD",
            },
            vin_decode_status="success",
        )

        self.assertEqual(patch["gearbox_type"], "automatic")
        self.assertNotIn("gearbox_model", patch)
        self.assertEqual(patch["drivetrain"], "AWD")

    def test_sparse_decode_keeps_trying_after_failed_excerpts(self) -> None:
        service = VehicleProfileService()

        class _FakeRuntime:
            def __init__(self) -> None:
                self.actions: list[tuple[str, dict[str, object]]] = []

            def _record_log_action(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
                return None

            def _run_autofill_tool(self, *, tool_name: str, args: dict[str, object], **kwargs):  # type: ignore[no-untyped-def]
                self.actions.append((tool_name, dict(args)))
                if tool_name == "decode_vin":
                    return {
                        "ok": True,
                        "data": {
                            "vin": str(args["vin"]),
                            "make": "AUDI",
                            "model": "",
                            "model_year": "1988",
                            "source_url": "https://vpic.nhtsa.dot.gov/api/vehicles/example",
                        },
                    }
                if tool_name == "search_web":
                    return {
                        "ok": True,
                        "data": {
                            "results": [
                                {
                                    "title": "Blocked page one",
                                    "snippet": "",
                                    "url": "https://blocked.example/one",
                                },
                                {
                                    "title": "Blocked page two",
                                    "snippet": "",
                                    "url": "https://blocked.example/two",
                                },
                                {
                                    "title": "1988 Audi 90 Quattro Specs Review (97 kW / 132 PS / 130 hp)",
                                    "snippet": "",
                                    "url": "https://example.com/audi-90",
                                },
                                {
                                    "title": "Extra page",
                                    "snippet": "",
                                    "url": "https://example.com/extra",
                                },
                            ]
                        },
                    }
                if tool_name == "fetch_page_excerpt":
                    url = str(args["url"])
                    if "blocked.example" in url:
                        return None
                    return {
                        "ok": True,
                        "data": {
                            "title": "1988 Audi 90 Quattro Specs Review",
                            "excerpt": "1988 Audi 90 Quattro Specs Review (97 kW / 132 PS / 130 hp). What is the drivetrain? All wheel drive (4x4).",
                            "url": url,
                        },
                    }
                raise AssertionError(f"unexpected tool: {tool_name}")

            def _response_data(self, payload):  # type: ignore[no-untyped-def]
                if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                    return payload["data"]
                return payload if isinstance(payload, dict) else {}

            def _vin_decode_status(self, payload):  # type: ignore[no-untyped-def]
                data = payload if isinstance(payload, dict) else {}
                if any(
                    str(data.get(key, "") or "").strip()
                    for key in ("model", "model_year", "engine_model", "transmission", "drive_type")
                ):
                    return "success"
                if any(
                    str(data.get(key, "") or "").strip() for key in ("make", "plant_country", "vin")
                ):
                    return "insufficient"
                return "failed"

            def _merge_vehicle_context(self, current, decoded):  # type: ignore[no-untyped-def]
                merged = dict(current or {})
                for key, target in (
                    ("make", "make"),
                    ("model", "model"),
                    ("model_year", "year"),
                    ("engine_model", "engine"),
                    ("transmission", "gearbox"),
                    ("drive_type", "drivetrain"),
                    ("vin", "vin"),
                ):
                    if not merged.get(target) and decoded.get(key):
                        merged[target] = decoded.get(key)
                return merged

            def _build_tool_result(self, tool_name, payload, **kwargs):  # type: ignore[no-untyped-def]
                return ToolResult(
                    tool_name=tool_name,
                    status="success",
                    source_type="external_vin",
                    confidence=0.9,
                    data={"ok": True},
                    raw_ref="test",
                    evidence_ref=str(kwargs.get("evidence_ref", "")),
                    reason=str(kwargs.get("reason", "")),
                )

            def _vin_web_enrichment_required(self, decoded_vin):  # type: ignore[no-untyped-def]
                return True

            def _parse_vehicle_profile_text(
                self, text: str, *, explicit_vehicle: str = ""
            ) -> dict[str, object]:
                profile, _, _ = service._parse_text_payload(text, explicit_vehicle=explicit_vehicle)
                return profile.to_dict()

        runtime = _FakeRuntime()
        facts = {
            "vin": "WAUZZZ8V0JA000001",
            "vehicle_context": {},
            "vehicle_profile": {},
            "evidence_model": {},
        }
        result = VinEnrichmentScenarioExecutor().execute(
            ScenarioContext(
                scenario_id="vin_enrichment",
                task_id="task-2",
                run_id="run-2",
                metadata={"context": {"kind": "card", "card_id": "card-2"}},
                facts=facts,
                runtime=runtime,
            )
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.tool_calls_used, 3)
        fetch_urls = [
            str(args.get("url", "") or "")
            for tool_name, args in runtime.actions
            if tool_name == "fetch_page_excerpt"
        ]
        self.assertTrue(fetch_urls)
        self.assertEqual(fetch_urls[0], "https://example.com/audi-90")
        self.assertNotIn("https://blocked.example/one", fetch_urls)
        self.assertNotIn("https://blocked.example/two", fetch_urls)
        decoded = result.orchestration_updates["decode_vin"]
        self.assertEqual(decoded["model"], "90")
        self.assertEqual(decoded["engine_power_hp"], 130)
        self.assertEqual(decoded["drive_type"], "AWD")

    def test_same_vin_board_profile_fills_sparse_decode(self) -> None:
        runner = object.__new__(AgentRunner)
        facts = {
            "vin": "XW8ZZZ7PZBG008034",
            "vin_decode_attempted": True,
            "vin_decode_status": "insufficient",
            "card": {
                "id": "card-current",
                "vehicle": "Тестовая карточка",
                "description": "",
            },
            "vehicle_context": {},
            "vehicle_profile": {},
            "related_cards": [
                {
                    "id": "card-related",
                    "vehicle": "Volkswagen Touareg",
                    "title": "Touareg — нет жидкости в расширительном бачке",
                    "column": "Машины в ремонте",
                    "vehicle_profile_compact": {
                        "vin": "XW8ZZZ7PZBG008034",
                        "make_display": "Volkswagen",
                        "model_display": "Touareg",
                        "production_year": 2011,
                        "engine_model": "CASA",
                        "engine_power_hp": 240,
                        "gearbox_model": "0C8",
                        "drivetrain": "4Motion",
                    },
                }
            ],
            "evidence_model": {},
        }
        orchestration_results = {
            "decode_vin": {
                "vin": "XW8ZZZ7PZBG008034",
                "make": "",
                "model": "",
                "model_year": "",
                "engine_model": "",
                "engine_power_hp": None,
                "gearbox_model": "",
                "gearbox_type": "",
                "transmission": "",
                "drive_type": "",
                "source_url": "https://vpic.nhtsa.dot.gov/api/vehicles/example",
            }
        }

        update_args, display_sections = AgentRunner._compose_card_autofill_update(
            runner,
            card_id="card-current",
            facts=facts,
            orchestration_results=orchestration_results,
        )

        self.assertIsNotNone(update_args)
        assert update_args is not None
        self.assertEqual(update_args["vehicle"], "Volkswagen Touareg 2011")
        self.assertIn("Volkswagen", update_args["description"])
        self.assertIn("Touareg", update_args["description"])
        self.assertIn("2011", update_args["description"])
        self.assertEqual(update_args["vehicle_profile"]["make_display"], "Volkswagen")
        self.assertEqual(update_args["vehicle_profile"]["model_display"], "Touareg")
        self.assertEqual(update_args["vehicle_profile"]["production_year"], 2011)
        self.assertEqual(update_args["vehicle_profile"]["engine_model"], "CASA")
        self.assertEqual(update_args["vehicle_profile"]["engine_power_hp"], 240)
        self.assertEqual(update_args["vehicle_profile"]["gearbox_model"], "0C8")
        self.assertEqual(update_args["vehicle_profile"]["drivetrain"], "4Motion")
        self.assertIn("same VIN board context", update_args["vehicle_profile"]["source_summary"])
        self.assertTrue(display_sections)
        self.assertEqual(facts["vin_decode_status"], "success")

    def test_web_merge_keeps_same_vin_fallback_on_conflict(self) -> None:
        decoded_vin = {
            "vin": "KNALU412BD6015036",
            "make": "KIA",
            "model": "Rio",
            "model_year": "1983",
            "engine_model": "YOUCANIC",
            "engine_power_hp": None,
            "gearbox_model": "8AT",
            "gearbox_type": "8AT",
            "transmission": "8AT",
            "drive_type": "RWD",
        }
        parsed_profile = {
            "make_display": "Kia",
            "model_display": "Rio",
            "production_year": 1983,
            "engine_model": "YOUCANIC",
            "engine_power_hp": 130,
            "gearbox_model": "8AT",
            "gearbox_type": "8AT",
            "drivetrain": "RWD",
        }
        fallback_profile = {
            "vin": "KNALU412BD6015036",
            "make_display": "Kia",
            "model_display": "Quoris",
            "production_year": 2013,
            "engine_model": "3.8 MPI",
            "engine_power_hp": 240,
            "gearbox_model": "8AT",
            "gearbox_type": "AT",
            "drivetrain": "RWD",
        }

        merged, enriched_fields = _merge_web_enrichment(
            decoded_vin,
            parsed_profile,
            fallback_profile=fallback_profile,
        )

        self.assertEqual(merged["make"], "Kia")
        self.assertEqual(merged["model"], "Quoris")
        self.assertEqual(merged["model_year"], 2013)
        self.assertEqual(merged["engine_model"], "3.8 MPI")
        self.assertEqual(merged["gearbox_model"], "8AT")
        self.assertEqual(merged["transmission"], "AT")
        self.assertEqual(merged["drive_type"], "RWD")
        self.assertEqual(enriched_fields, [])

    def test_same_vin_board_profile_wins_over_conflicting_decode(self) -> None:
        runner = object.__new__(AgentRunner)
        facts = {
            "vin": "KNALU412BD6015036",
            "vin_decode_attempted": True,
            "vin_decode_status": "success",
            "card": {
                "id": "card-current",
                "vehicle": "Kia Quoris",
                "description": "KNALU412BD6015036",
            },
            "vehicle_context": {},
            "vehicle_profile": {},
            "related_cards": [
                {
                    "id": "card-related",
                    "vehicle": "Kia Quoris",
                    "title": "Заказ запчастей — Kia Quoris",
                    "column": "Заказы запчастей",
                    "vehicle_profile_compact": {
                        "vin": "KNALU412BD6015036",
                        "make_display": "Kia",
                        "model_display": "Quoris",
                        "production_year": 2013,
                        "engine_model": "3.8 MPI",
                        "engine_power_hp": 240,
                        "gearbox_model": "8AT",
                        "gearbox_type": "AT",
                        "drivetrain": "RWD",
                    },
                }
            ],
            "evidence_model": {},
        }
        orchestration_results = {
            "decode_vin": {
                "vin": "KNALU412BD6015036",
                "make": "KIA",
                "model": "Rio",
                "model_year": "1983",
                "engine_model": "YOUCANIC",
                "engine_power_hp": None,
                "gearbox_model": "8AT",
                "gearbox_type": "8AT",
                "transmission": "8AT",
                "drive_type": "RWD",
                "source_url": "https://vpic.nhtsa.dot.gov/api/vehicles/example",
                "web_enrichment_fields": ["model_display", "engine_model"],
            }
        }

        update_args, display_sections = AgentRunner._compose_card_autofill_update(
            runner,
            card_id="card-current",
            facts=facts,
            orchestration_results=orchestration_results,
        )

        self.assertIsNotNone(update_args)
        assert update_args is not None
        self.assertEqual(update_args["vehicle"], "Kia Quoris 2013")
        self.assertIn("Kia", update_args["description"])
        self.assertIn("Quoris", update_args["description"])
        self.assertIn("2013", update_args["description"])
        self.assertNotIn("Rio", update_args["description"])
        self.assertNotIn("1983", update_args["description"])
        self.assertEqual(update_args["vehicle_profile"]["make_display"], "Kia")
        self.assertEqual(update_args["vehicle_profile"]["model_display"], "Quoris")
        self.assertEqual(update_args["vehicle_profile"]["production_year"], 2013)
        self.assertEqual(update_args["vehicle_profile"]["engine_model"], "3.8 MPI")
        self.assertEqual(update_args["vehicle_profile"]["gearbox_model"], "8AT")
        self.assertEqual(update_args["vehicle_profile"]["drivetrain"], "RWD")
        self.assertIn("same VIN board context", update_args["vehicle_profile"]["source_summary"])
        self.assertTrue(display_sections)

    def test_same_vin_board_context_wins_for_vehicle_label(self) -> None:
        runner = object.__new__(AgentRunner)
        facts = {
            "card": {
                "id": "card-current",
                "vehicle": "",
                "description": "VIN: KNALU412BD6015036",
            },
            "vehicle_context": {},
        }

        label = AgentRunner._autofill_vehicle_label_patch(
            runner,
            facts=facts,
            decoded_vin={
                "make": "KIA",
                "model": "Rio",
                "model_year": "1983",
            },
            vin_decode_status="success",
            fallback_vehicle_profile={
                "make_display": "Kia",
                "model_display": "Quoris",
                "production_year": 2013,
            },
        )

        self.assertEqual(label, "Kia Quoris 2013")

    def test_scenario_patch_triggers_update_card_writeback(self) -> None:
        class _PatchOnlyExecutor:
            scenario_id = "vin_enrichment"

            def execute(self, context):  # type: ignore[no-untyped-def]
                del context
                return ScenarioExecutionResult(
                    scenario_id=self.scenario_id,
                    status="success",
                    patch={
                        "description": "По VIN подтверждено: Toyota, Land Cruiser 4.0.",
                        "vehicle": "Toyota Land Cruiser 4.0",
                        "vehicle_profile": {
                            "make_display": "Toyota",
                            "model_display": "Land Cruiser 4.0",
                            "production_year": 2013,
                            "drivetrain": "AWD",
                        },
                    },
                )

        class _FakeBoardApi:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []
                self.card = {
                    "id": "card-1",
                    "title": "VIN bridge test",
                    "description": "VIN: JTEBU3FJX05027767\nПроверить запись из scenario patch.",
                    "vehicle": "",
                    "vehicle_profile": {},
                }

            def get_card_context(
                self,
                card_id: str,
                *,
                event_limit: int = 20,
                include_repair_order_text: bool = True,
            ) -> dict[str, object]:
                del event_limit, include_repair_order_text
                self.calls.append({"method": "get_card_context", "card_id": card_id})
                return {"data": {"card": dict(self.card), "events": []}}

            def get_card(self, card_id: str) -> dict[str, object]:
                self.calls.append({"method": "get_card", "card_id": card_id})
                return {"data": {"card": dict(self.card)}}

            def search_cards(self, **kwargs) -> dict[str, object]:  # type: ignore[no-untyped-def]
                self.calls.append({"method": "search_cards", **kwargs})
                return {"data": {"cards": []}}

            def update_card(self, **kwargs) -> dict[str, object]:
                self.calls.append({"method": "update_card", **kwargs})
                for key in ("vehicle", "description", "vehicle_profile"):
                    if key in kwargs and kwargs[key] is not None:
                        self.card[key] = kwargs[key]
                changed = [
                    key for key in ("vehicle", "description", "vehicle_profile") if key in kwargs
                ]
                return {
                    "data": {
                        "card": dict(self.card),
                        "changed": changed,
                        "meta": {"changed_fields": changed},
                    }
                }

        class _NullModel:
            model = "offline-null"

        with tempfile.TemporaryDirectory(prefix="autostopcrm-test-") as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            board_api = _FakeBoardApi()
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,  # type: ignore[arg-type]
                model_client=_NullModel(),  # type: ignore[arg-type]
                logger=logging.getLogger("autostopcrm.test"),
            )
            runner._scenario_registry.register(_PatchOnlyExecutor())
            facts = {
                "card": dict(board_api.card),
                "vehicle_profile": {},
                "vehicle_context": {},
                "vin": "JTEBU3FJX05027767",
                "autofill_plan": {
                    "scenarios": [{"name": "vin_enrichment", "label": "VIN", "cost": 1}]
                },
            }
            plan = PlanResult(
                scenario_id="vin_enrichment",
                scenario_chain=["vin_enrichment"],
                execution_mode="structured_card",
                needs_external_tools=True,
                required_tools=["research_vin"],
                optional_tools=[],
                tool_order=["research_vin"],
                allowed_write_targets=["description", "vehicle", "vehicle_profile"],
                forbidden_write_targets=[],
                stop_conditions=[],
                followup_policy={"enabled": True},
                confidence_mode="best_effort",
                write_mode="patch_only",
                notes=[],
            )

            runner._execute_card_autofill_task(
                {"id": "task-1", "task_text": "Обогати карточку по VIN."},
                run_id="run-1",
                metadata={
                    "purpose": "card_enrichment",
                    "context": {"kind": "card", "card_id": "card-1"},
                    "card_enrichment": {"card_id": "card-1", "card_heading": "VIN bridge test"},
                },
                facts=facts,
                plan=plan,
            )

        self.assertTrue(any(call["method"] == "update_card" for call in board_api.calls))
        self.assertEqual(board_api.card["vehicle"], "Toyota Land Cruiser 4.0")
        self.assertEqual(board_api.card["vehicle_profile"]["make_display"], "Toyota")


if __name__ == "__main__":
    unittest.main()
