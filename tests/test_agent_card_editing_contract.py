from __future__ import annotations

import sys
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


def test_agent_update_card_preserves_exact_description_and_write_contract_args() -> None:
    board_api = _FakeBoardApi()
    executor = AgentToolExecutor(board_api, actor_name="Codex MCP QA")  # type: ignore[arg-type]

    executor.execute(
        "update_card",
        {
            "card_id": "card-1",
            "description": "  **Важно:**  два  пробела  ",
            "expected_updated_at": "2026-06-08T10:00:00+07:00",
            "response_mode": "compact",
        },
    )

    assert board_api.calls[0]["description"] == "  **Важно:**  два  пробела  "
    assert board_api.calls[0]["expected_updated_at"] == "2026-06-08T10:00:00+07:00"
    assert board_api.calls[0]["response_mode"] == "compact"


def test_agent_description_patch_verification_is_exact_not_whitespace_insensitive() -> None:
    runner = object.__new__(AgentRunner)

    assert runner._description_patch_applied("  A  B  ", "  A  B  ") is True
    assert runner._description_patch_applied("A B", "A  B") is False
