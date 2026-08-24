from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.control import AgentControlService  # noqa: E402
from minimal_kanban.agent.remodel import (  # noqa: E402
    AiFeatureFlags,
    get_ai_effective_mode,
    get_ai_feature_flags,
    get_ai_remodel_status_payload,
)
from minimal_kanban.agent.storage import AgentStorage  # noqa: E402

RETIRED_UI_ENTRY_IDS = {
    "agent_manual_prompt",
    "agent_status_surface",
    "agent_tasks_modal",
    "board_dock_button",
    "card_agent_button",
    "card_autofill_toggle",
    "quick_prompts",
}
RETIRED_LEGACY_ENTRY_POINT_IDS = {
    "agent_tasks_modal",
    "board_dock_button",
    "card_agent_button",
    "card_autofill_toggle",
    "quick_prompts",
}


class AiRemodelRegistryTests(unittest.TestCase):
    def test_retired_ui_entries_only_remain_as_compatibility_tombstones(self) -> None:
        payload = get_ai_remodel_status_payload()

        for key in ("entry_surface_registry", "entry_exposure"):
            self.assertFalse(RETIRED_UI_ENTRY_IDS.intersection(payload[key]), key)

        self.assertEqual(
            RETIRED_UI_ENTRY_IDS,
            RETIRED_UI_ENTRY_IDS.intersection(payload["legacy_deactivation_map"]),
        )
        for entry_id in RETIRED_UI_ENTRY_IDS:
            tombstone = payload["legacy_deactivation_map"][entry_id]
            self.assertEqual("retired_unreachable", tombstone["legacy_status"])
            self.assertIn(tombstone["deactivation_policy"], {"gate", "later_hide"})

        self.assertEqual(
            RETIRED_LEGACY_ENTRY_POINT_IDS,
            set(payload["legacy_entry_points"]),
        )
        self.assertTrue(
            all(
                entry["role"] == "retired_unreachable"
                for entry in payload["legacy_entry_points"].values()
            )
        )

    def test_card_enrichment_is_the_active_interactive_entry(self) -> None:
        payload = get_ai_remodel_status_payload()
        entry = payload["entry_surface_registry"]["future_card_enrichment_trigger"]

        self.assertEqual(
            payload["effective_mode"]["primary_interactive_path"], "full_card_enrichment"
        )
        self.assertEqual(payload["scenario_registry"]["full_card_enrichment"]["stage"], "active")
        self.assertEqual(entry["location"], "web_assets.cardAgentButton -> runFullCardEnrichment")
        self.assertEqual(entry["legacy_status"], "active_replacement")
        self.assertEqual(entry["surface_kind"], "ui")
        self.assertEqual(
            payload["entry_exposure"]["future_card_enrichment_trigger"]["exposure_state"],
            "active",
        )

    def test_backend_compatibility_surfaces_remain_active(self) -> None:
        payload = get_ai_remodel_status_payload()

        for entry_id in (
            "agent_enqueue_task_api",
            "agent_scheduled_tasks_api",
            "card_created_auto_trigger",
            "set_card_ai_autofill_api",
        ):
            self.assertIn(entry_id, payload["entry_surface_registry"])
            self.assertEqual(payload["entry_exposure"][entry_id]["exposure_state"], "active")

        self.assertEqual(set(payload["legacy_entry_points"]), RETIRED_LEGACY_ENTRY_POINT_IDS)
        self.assertEqual(set(payload["backend_legacy_only"]), {"autofill_bridge"})

    def test_retired_legacy_flag_cannot_restore_the_removed_modal(self) -> None:
        with patch.dict(os.environ, {"MINIMAL_KANBAN_AI_LEGACY_UX_ENABLED": "1"}):
            self.assertFalse(get_ai_feature_flags().legacy_ux_enabled)

        mode = get_ai_effective_mode(
            AiFeatureFlags(
                legacy_ux_enabled=True,
                ai_chat_enabled=False,
                full_card_enrichment_enabled=False,
                board_control_enabled=False,
            )
        )
        self.assertFalse(mode["legacy_ux_enabled"])
        self.assertFalse(mode["mode_config"]["legacy_ux_enabled"])
        self.assertEqual(mode["primary_interactive_path"], "none")
        self.assertEqual(
            mode["entry_exposure"]["future_card_enrichment_trigger"]["exposure_state"],
            "hidden",
        )
        self.assertFalse(RETIRED_UI_ENTRY_IDS.intersection(mode["entry_exposure"]))

    def test_agent_status_contract_exposes_retired_tombstones_without_reactivating_them(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir) / "agent")
            control = AgentControlService(storage)

            payload = control.agent_status()["ai_remodel"]

        self.assertEqual(
            RETIRED_UI_ENTRY_IDS,
            RETIRED_UI_ENTRY_IDS.intersection(payload["legacy_deactivation_map"]),
        )
        self.assertFalse(RETIRED_UI_ENTRY_IDS.intersection(payload["entry_exposure"]))
        self.assertFalse(payload["legacy_ux_enabled"])
        self.assertFalse(payload["effective_mode"]["legacy_ux_enabled"])


if __name__ == "__main__":
    unittest.main()
