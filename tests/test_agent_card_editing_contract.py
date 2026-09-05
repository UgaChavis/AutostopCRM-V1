from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.runner import AgentRunner
from minimal_kanban.agent.tools import AgentToolExecutor


class _FakeBoardApi:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def update_card(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(dict(kwargs))
        return {"ok": True, "data": {"card": {"id": kwargs["card_id"]}}}


class AgentCardEditingContractTests(unittest.TestCase):
    def test_agent_update_card_preserves_exact_description_and_write_contract_args(self) -> None:
        board_api = _FakeBoardApi()
        executor = AgentToolExecutor(  # type: ignore[arg-type]
            board_api,
            actor_name="Codex MCP QA",
        )

        executor.execute(
            "update_card",
            {
                "card_id": "card-1",
                "description": "  **Важно:**  два  пробела  ",
                "expected_updated_at": "2026-06-08T10:00:00+07:00",
                "response_mode": "compact",
            },
        )

        self.assertEqual(
            "  **Важно:**  два  пробела  ",
            board_api.calls[0]["description"],
        )
        self.assertEqual(
            "2026-06-08T10:00:00+07:00",
            board_api.calls[0]["expected_updated_at"],
        )
        self.assertEqual("compact", board_api.calls[0]["response_mode"])

    def test_agent_value_verification_preserves_exact_description_whitespace(
        self,
    ) -> None:
        runner = object.__new__(AgentRunner)

        self.assertIs(runner._values_equal("  A  B  ", "  A  B  "), True)
        self.assertIs(runner._values_equal("A B", "A  B"), False)
