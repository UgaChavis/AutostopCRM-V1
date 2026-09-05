from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.contracts import EvidenceResult, PlanResult, VerifyResult  # noqa: E402
from minimal_kanban.agent.runner import AgentRunner  # noqa: E402


class AgentRunnerSerializationTests(unittest.TestCase):
    def test_values_equal_sanitizes_non_finite_numbers(self) -> None:
        runner = object.__new__(AgentRunner)

        self.assertTrue(runner._values_equal({"score": math.nan}, {"score": None}))
        self.assertTrue(runner._values_equal([math.inf, -math.inf], [None, None]))

    def test_user_task_message_sanitizes_context_metadata(self) -> None:
        runner = object.__new__(AgentRunner)

        message = runner._build_user_task_message(
            {"id": "task-1", "mode": "manual", "source": "test", "task_text": "Проверь"},
            {"context": {"score": math.nan, "items": [math.inf]}},
            task_type="manual",
        )

        self.assertNotIn("NaN", message)
        self.assertNotIn("Infinity", message)
        self.assertIn('"score": null', message)
        self.assertIn('"items": [\n    null\n  ]', message)

    def test_runtime_prompt_override_replaces_the_default_prompt(self) -> None:
        class _Storage:
            @staticmethod
            def read_prompt_text() -> str:
                return "Local autonomy prompt"

            @staticmethod
            def read_memory_text() -> str:
                return ""

        class _Tools:
            @staticmethod
            def describe_for_prompt(**kwargs) -> str:  # noqa: ANN003
                return "- get_store_context"

        runner = object.__new__(AgentRunner)
        runner._storage = _Storage()  # type: ignore[attr-defined]
        runner._tools = _Tools()  # type: ignore[attr-defined]
        prompt = runner._build_decision_loop_system_prompt(
            task_type="general",
            context_kind="board",
            plan=PlanResult(
                scenario_id="freeform_manual",
                scenario_chain=["freeform_manual"],
                execution_mode="model_loop",
                needs_external_tools=False,
            ),
            evidence=EvidenceResult(context_kind="board"),
        )

        self.assertIn("Local autonomy prompt", prompt)
        self.assertIn("get_store_context", prompt)
        self.assertNotIn("You are the AUTOSTOP CRM operations agent.", prompt)

    def test_low_risk_card_patch_needs_no_forced_readback(self) -> None:
        class _Policy:
            @staticmethod
            def filter_patch(plan, patch):  # noqa: ANN001
                return patch

        class _Tools:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def execute(self, name: str, args: dict[str, object]) -> dict[str, object]:
                self.calls.append((name, dict(args)))
                return {"ok": True}

        runner = object.__new__(AgentRunner)
        runner._policy = _Policy()  # type: ignore[attr-defined]
        tools = _Tools()
        runner._tools = tools  # type: ignore[attr-defined]
        runner._read_verification_state = lambda card_id: (_ for _ in ()).throw(  # type: ignore[attr-defined]
            AssertionError(f"unexpected reread for {card_id}")
        )

        args, result, patch, verify = runner._execute_card_update(
            args={
                "card_id": "card-1",
                "title": "Уточнённый заголовок",
                "expected_updated_at": "2026-09-05T00:00:00Z",
            },
            plan=object(),
            cleanup_card_id="",
        )

        self.assertEqual(
            tools.calls,
            [
                (
                    "update_card",
                    {
                        "card_id": "card-1",
                        "title": "Уточнённый заголовок",
                        "expected_updated_at": "2026-09-05T00:00:00Z",
                    },
                )
            ],
        )
        self.assertEqual(args, tools.calls[0][1])
        self.assertTrue(result["ok"])
        self.assertEqual(patch.card_patch, {"title": "Уточнённый заголовок"})
        self.assertTrue(verify.applied_ok)

    def test_repair_order_write_forwards_native_confirmation(self) -> None:
        class _Policy:
            @staticmethod
            def filter_patch(plan, patch):  # noqa: ANN001
                return patch

        class _Tools:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def execute(self, name: str, args: dict[str, object]) -> dict[str, object]:
                self.calls.append((name, dict(args)))
                return {"ok": True}

        runner = object.__new__(AgentRunner)
        runner._policy = _Policy()  # type: ignore[attr-defined]
        tools = _Tools()
        runner._tools = tools  # type: ignore[attr-defined]
        runner._read_verification_state = lambda card_id: {"repair_order": {"client": "Иван"}}  # type: ignore[attr-defined]
        runner._verify_repair_order_write = lambda **kwargs: VerifyResult(applied_ok=True)  # type: ignore[attr-defined]

        runner._execute_repair_order_update(
            args={
                "card_id": "card-1",
                "repair_order": {"comment": "Согласовано"},
                "confirmation": "explicit_user_authority",
            },
            plan=object(),
            cleanup_card_id="",
        )

        self.assertEqual(
            tools.calls,
            [
                (
                    "update_repair_order",
                    {
                        "card_id": "card-1",
                        "repair_order": {"comment": "Согласовано"},
                        "confirmation": "explicit_user_authority",
                    },
                )
            ],
        )

    def test_only_a_real_card_scope_preloads_crm_context(self) -> None:
        runner = object.__new__(AgentRunner)

        self.assertFalse(
            runner._should_preload_context(
                task_type="vin_decode",
                metadata={},
                context_kind="board",
            )
        )
        self.assertTrue(
            runner._should_preload_context(
                task_type="general",
                metadata={"context": {"kind": "card", "card_id": "card-1"}},
                context_kind="card",
            )
        )

    def test_current_card_scope_reuses_preloaded_context(self) -> None:
        runner = object.__new__(AgentRunner)

        message = runner._build_user_task_message(
            {"id": "task-1", "mode": "manual", "source": "test", "task_text": "Проверь"},
            {
                "context": {"kind": "card", "card_id": "card-1"},
                "scope": {"type": "current_card", "card_id": "card-1"},
            },
            task_type="manual",
            preloaded_context={
                "data": {"card": {"id": "card-1", "title": "Известная карточка"}, "events": []}
            },
        )

        self.assertIn("Известная карточка", message)
        self.assertNotIn('"error"', message)

    def test_card_context_hints_cover_present_quote_signals(self) -> None:
        runner = object.__new__(AgentRunner)

        plan = runner._build_card_autofill_plan(
            {
                "vin": "WVWZZZ1JZXW000001",
                "part_queries": ["тормозные колодки"],
                "maintenance_needed": False,
                "dtc_codes": [],
                "symptom_query": "стук на холодную",
            }
        )

        self.assertEqual(
            [item["name"] for item in plan["scenarios"]],
            ["vin_enrichment", "parts_lookup", "fault_research"],
        )
        self.assertEqual(plan["skipped"], [])


if __name__ == "__main__":
    unittest.main()
