# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.contracts import PatchResult, PlanResult
from minimal_kanban.agent.instructions import build_default_system_prompt
from minimal_kanban.agent.policy import ToolPolicyEngine
from minimal_kanban.agent.tools import AgentToolExecutor


class ToolPolicyEngineTests(unittest.TestCase):
    def test_build_plan_deduplicates_advisory_sources_and_normalizes_execution_mode(self) -> None:
        engine = ToolPolicyEngine()
        plan = engine.build_plan(
            scenario_chain=["VIN_ENRICHMENT", "vin_enrichment", "normalization", "normalization"],
            execution_mode="MODEL_LOOP",
            followup_enabled=True,
            notes=["first note"],
        )

        self.assertEqual(plan.scenario_chain, ["vin_enrichment", "normalization"])
        self.assertEqual(plan.scenario_id, "vin_enrichment")
        self.assertEqual(plan.execution_mode, "model_loop")
        self.assertEqual(plan.required_tools, [])
        self.assertEqual(
            plan.optional_tools,
            [
                "decode_vin",
                "search_web_multi",
                "fetch_page_excerpt",
                "fetch_page_browser",
            ],
        )
        self.assertEqual(plan.tool_order, [])
        self.assertEqual(plan.stop_conditions, [])
        self.assertEqual(plan.confidence_mode, "evidence_guided")
        self.assertEqual(plan.write_mode, "patch_only")
        self.assertTrue(plan.followup_policy["enabled"])

    def test_filter_patch_preserves_evidence_backed_fields_despite_scenario_labels(self) -> None:
        engine = ToolPolicyEngine()
        plan = PlanResult(
            scenario_id="custom",
            scenario_chain=["custom"],
            execution_mode="model_loop",
            needs_external_tools=False,
            allowed_write_targets=[
                "title",
                "vehicle_profile",
                "repair_order",
                "repair_order_works",
                "repair_order_materials",
            ],
            forbidden_write_targets=["vehicle_profile", "repair_order_works"],
        )
        patch = PatchResult(
            card_patch={
                "title": "Updated title",
                "vehicle_profile": {"vin": "WBAPF71060A798127"},
                "description": "Should be removed",
            },
            repair_order_patch={"status": "open"},
            repair_order_works=[{"name": "ignored"}],
            repair_order_materials=[{"name": "kept"}],
            append_only_notes=["note"],
        )

        filtered = engine.filter_patch(plan, patch)

        self.assertEqual(filtered.card_patch, patch.card_patch)
        self.assertEqual(filtered.repair_order_patch, patch.repair_order_patch)
        self.assertEqual(filtered.repair_order_works, patch.repair_order_works)
        self.assertEqual(filtered.repair_order_materials, patch.repair_order_materials)
        self.assertEqual(filtered.append_only_notes, ["note"])

    def test_filter_patch_is_scenario_agnostic(self) -> None:
        engine = ToolPolicyEngine()
        plan = PlanResult(
            scenario_id="vin_enrichment",
            scenario_chain=["vin_enrichment"],
            execution_mode="model_loop",
            needs_external_tools=True,
            allowed_write_targets=[],
            forbidden_write_targets=["vehicle_profile"],
        )
        patch = PatchResult(
            card_patch={
                "vehicle": "MERCEDES-BENZ ML320 CDI4 2001",
                "vehicle_profile": {"vin": "WDC1641221A444349"},
            },
            append_only_notes=["note"],
        )

        filtered = engine.filter_patch(plan, patch)

        self.assertEqual(filtered.card_patch, patch.card_patch)
        self.assertEqual(filtered.append_only_notes, ["note"])

    def test_tool_source_type_normalizes_tool_name_case(self) -> None:
        engine = ToolPolicyEngine()
        self.assertEqual(engine.tool_source_type("DECODE_VIN"), "external_vin")
        self.assertEqual(engine.tool_source_type("Search_Fault_Info"), "external_fault")
        self.assertEqual(engine.tool_source_type("Fetch_Page_Browser"), "external_page_browser")
        self.assertEqual(
            engine.tool_source_type("research_drive2_cases"), "external_drive2_case_research"
        )

    def test_build_plan_ignores_unknown_scenarios_and_falls_back_cleanly(self) -> None:
        engine = ToolPolicyEngine()
        plan = engine.build_plan(
            scenario_chain=["unknown_scenario", "also_unknown"],
            execution_mode="MODEL_LOOP",
            followup_enabled=False,
        )

        self.assertEqual(plan.scenario_id, "freeform_manual")
        self.assertEqual(plan.scenario_chain, ["freeform_manual"])
        self.assertEqual(plan.execution_mode, "model_loop")
        self.assertEqual(plan.required_tools, [])
        self.assertEqual(plan.allowed_write_targets, [])
        self.assertEqual(plan.forbidden_write_targets, [])
        self.assertEqual(plan.followup_policy["mode"], "none")

    def test_build_plan_supports_full_card_enrichment_with_advisory_sources(self) -> None:
        engine = ToolPolicyEngine()
        plan = engine.build_plan(
            scenario_chain=["FULL_CARD_ENRICHMENT"],
            execution_mode="MODEL_LOOP",
            followup_enabled=False,
        )

        self.assertEqual(plan.scenario_id, "full_card_enrichment")
        self.assertEqual(plan.scenario_chain, ["full_card_enrichment"])
        self.assertEqual(plan.execution_mode, "model_loop")
        self.assertEqual(
            plan.optional_tools,
            ["decode_vin", "find_part_numbers", "lookup_part_prices"],
        )
        self.assertEqual(plan.tool_order, [])
        self.assertEqual(plan.allowed_write_targets, [])
        self.assertEqual(plan.forbidden_write_targets, [])

    def test_agent_tool_executor_accepts_mixed_case_tool_names(self) -> None:
        class _FakeBoardApi:
            def health(self) -> dict[str, object]:
                return {"ok": True}

            def review_board(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def list_columns(self) -> dict[str, object]:
                return {"ok": True}

            def get_board_snapshot(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def search_cards(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def get_card(self, card_id: str) -> dict[str, object]:
                return {"ok": True, "card_id": card_id}

            def get_card_context(self, card_id: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "card_id": card_id}

            def create_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def update_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def move_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def archive_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def restore_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def list_repair_orders(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def get_repair_order(self, card_id: str) -> dict[str, object]:
                return {"ok": True, "card_id": card_id}

            def update_repair_order(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def replace_repair_order_works(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def replace_repair_order_materials(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def set_repair_order_status(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def list_cashboxes(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def get_cashbox(self, cashbox_id: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "cashbox_id": cashbox_id}

            def create_cashbox(self, name: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "name": name}

            def delete_cashbox(self, cashbox_id: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "cashbox_id": cashbox_id}

            def create_cash_transaction(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

        executor = AgentToolExecutor(_FakeBoardApi())
        tool_names = {definition.name for definition in executor.definitions}
        self.assertNotIn("autofill_repair_order", tool_names)
        with patch.object(
            executor._automotive,
            "decode_vin",
            return_value={"vin": "WBAPF71060A798127"},
        ) as decode_vin:
            payload = executor.execute("DECODE_VIN", {"vin": "WBAPF71060A798127"})
        decode_vin.assert_called_once_with("WBAPF71060A798127")
        self.assertEqual(payload["vin"], "WBAPF71060A798127")
        with self.assertRaisesRegex(
            PermissionError, "archive_card requires explicit user authority"
        ):
            executor.execute("archive_card", {"card_id": "card-1"})
        self.assertEqual(
            {"ok": True},
            executor.execute(
                "archive_card",
                {"card_id": "card-1", "confirmation": "explicit_user_authority"},
            ),
        )
        self.assertEqual(
            {"ok": True},
            executor.execute(
                "update_repair_order",
                {"card_id": "card-1", "repair_order": {"comment": "Техническая правка"}},
            ),
        )
        self.assertEqual(
            {"ok": True},
            executor.execute(
                "replace_repair_order_works",
                {"card_id": "card-1", "rows": [{"name": "Диагностика"}]},
            ),
        )
        self.assertEqual(
            {"ok": True},
            executor.execute("set_repair_order_status", {"card_id": "card-1", "status": "ready"}),
        )
        for tool_name, tool_args in (
            ("update_repair_order", {"card_id": "card-1", "repair_order": {"payments": []}}),
            (
                "replace_repair_order_works",
                {"card_id": "card-1", "rows": [{"name": "Диагностика", "price": "1000"}]},
            ),
            ("set_repair_order_status", {"card_id": "card-1", "status": "closed"}),
        ):
            with self.assertRaisesRegex(PermissionError, "requires explicit user authority"):
                executor.execute(tool_name, tool_args)

    def test_agent_tool_executor_exports_repair_order_pdf(self) -> None:
        class _FakeBoardApi:
            def __init__(self) -> None:
                self.payload: dict[str, object] | None = None

            def download_repair_order_print_pdf(self, **kwargs) -> dict[str, object]:
                self.payload = kwargs
                return {
                    "ok": True,
                    "data": {
                        "file_name": "repair-order-card-1.pdf",
                        "mime_type": "application/pdf",
                        "content_base64": "JVBERi0xLjQ=",
                        "size_bytes": 8,
                        "meta": {"documents": [{"id": "invoice"}]},
                    },
                }

        fake_api = _FakeBoardApi()
        executor = AgentToolExecutor(fake_api)

        self.assertIn(
            "download_repair_order_print_pdf",
            {definition.name for definition in executor.definitions},
        )
        result = executor.execute(
            "download_repair_order_print_pdf",
            {"card_id": "card-1", "selected_document_ids": ["invoice"]},
        )

        self.assertEqual(result["data"]["mime_type"], "application/pdf")
        self.assertEqual(
            fake_api.payload,
            {
                "card_id": "card-1",
                "selected_document_ids": ["invoice"],
                "selected_template_ids": None,
                "print_settings": None,
            },
        )

    def test_agent_tool_executor_normalizes_string_boolean_args(self) -> None:
        class _FakeBoardApi:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def get_board_content(self, **kwargs) -> dict[str, object]:
                self.calls.append(("get_board_content", kwargs))
                return {"ok": True}

            def get_board_events(self, **kwargs) -> dict[str, object]:
                self.calls.append(("get_board_events", kwargs))
                return {"ok": True}

            def get_gpt_wall(self, **kwargs) -> dict[str, object]:
                self.calls.append(("get_gpt_wall", kwargs))
                return {"ok": True}

            def search_cards(self, **kwargs) -> dict[str, object]:
                self.calls.append(("search_cards", kwargs))
                return {"ok": True}

            def get_card_context(self, card_id: str, **kwargs) -> dict[str, object]:
                self.calls.append(("get_card_context", {"card_id": card_id, **kwargs}))
                return {"ok": True}

        fake_api = _FakeBoardApi()
        executor = AgentToolExecutor(fake_api)

        executor.execute("get_board_content", {"include_archived": "false"})
        executor.execute("get_board_events", {"include_archived": "0"})
        executor.execute("get_gpt_wall", {"include_archived": "no"})
        executor.execute("search_cards", {"include_archived": "false"})
        executor.execute(
            "get_card_context",
            {"card_id": "card-1", "include_repair_order_text": "false"},
        )

        self.assertEqual(
            fake_api.calls,
            [
                (
                    "get_board_content",
                    {"include_archived": False, "view_mode": "agent"},
                ),
                ("get_board_events", {"event_limit": 100, "include_archived": False}),
                (
                    "get_gpt_wall",
                    {"include_archived": False, "event_limit": 20, "compact": True},
                ),
                (
                    "search_cards",
                    {
                        "query": None,
                        "include_archived": False,
                        "column": None,
                        "tag": None,
                        "indicator": None,
                        "status": None,
                        "limit": None,
                    },
                ),
                (
                    "get_card_context",
                    {
                        "card_id": "card-1",
                        "event_limit": 20,
                        "include_repair_order_text": False,
                    },
                ),
            ],
        )

    def test_prompt_exposes_full_tool_surface_and_marks_hard_actions(self) -> None:
        class _FakeBoardApi:
            def health(self) -> dict[str, object]:
                return {"ok": True}

            def review_board(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def list_columns(self) -> dict[str, object]:
                return {"ok": True}

            def get_board_snapshot(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def search_cards(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def get_card(self, card_id: str) -> dict[str, object]:
                return {"ok": True, "card_id": card_id}

            def get_card_context(self, card_id: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "card_id": card_id}

            def create_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def update_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def move_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def archive_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def restore_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def list_repair_orders(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def get_repair_order(self, card_id: str) -> dict[str, object]:
                return {"ok": True, "card_id": card_id}

            def update_repair_order(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def replace_repair_order_works(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def replace_repair_order_materials(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def set_repair_order_status(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def list_cashboxes(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def get_cashbox(self, cashbox_id: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "cashbox_id": cashbox_id}

            def create_cashbox(self, name: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "name": name}

            def delete_cashbox(self, cashbox_id: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "cashbox_id": cashbox_id}

            def create_cash_transaction(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

        executor = AgentToolExecutor(_FakeBoardApi())
        prompt = executor.describe_for_prompt(task_type="full_card_enrichment", context_kind="card")
        self.assertIn("update_card", prompt)
        self.assertIn("update_repair_order", prompt)
        self.assertIn("replace_repair_order_works", prompt)
        self.assertIn("replace_repair_order_materials", prompt)
        self.assertIn("decode_vin", prompt)
        self.assertIn("search_cards", prompt)
        self.assertIn("get_board_content", prompt)
        self.assertIn("get_board_events", prompt)
        self.assertIn("get_gpt_wall", prompt)
        self.assertIn("create_cashbox", prompt)
        self.assertIn("archive_card", prompt)
        self.assertIn("requires explicit user authority", prompt)
        self.assertNotIn("delete_column", prompt)

        system_prompt = build_default_system_prompt()
        self.assertIn("independent, practical director", system_prompt)
        self.assertIn("quote request", system_prompt)
        self.assertIn("Routes, scenarios, and source groups are hints", system_prompt)
        self.assertIn("native guard", system_prompt)
        self.assertIn("exactly one JSON object", system_prompt)
        self.assertLess(len(system_prompt), 5000)


if __name__ == "__main__":
    unittest.main()
