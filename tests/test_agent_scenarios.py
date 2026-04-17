# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.contracts import ToolResult
from minimal_kanban.agent.scenarios import ScenarioContext, build_default_scenario_registry
from minimal_kanban.agent.scenarios.vin_enrichment import VinEnrichmentScenarioExecutor
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
        self.assertEqual(decoded["gearbox_model"], "ZF 8HP55")
        self.assertEqual(decoded["engine_power_hp"], 211)
        self.assertIn("web_source_urls", decoded)
        self.assertIn("web_enrichment_fields", decoded)
        self.assertIn("VIN web enrichment produced additional confirmed facts.", runtime.logs)


if __name__ == "__main__":
    unittest.main()
