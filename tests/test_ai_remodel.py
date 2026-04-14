from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.remodel import (
    AiActorMode,
    AiEntryExposureState,
    AiRolloutState,
    AiScenarioId,
    AiScopeKind,
    AiTriggerKind,
    get_ai_effective_mode,
    get_ai_backend_reuse_map,
    get_ai_entry_exposure_map,
    get_ai_entry_surface_map,
    get_ai_feature_flags,
    get_ai_legacy_deactivation_map,
    get_ai_legacy_entry_point_map,
    get_ai_remodel_status_payload,
    get_ai_scenario_map,
)


class AiRemodelTests(unittest.TestCase):
    def test_default_ai_remodel_flags_keep_legacy_enabled_and_new_paths_disabled(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            flags = get_ai_feature_flags()
            self.assertTrue(flags.legacy_ux_enabled)
            self.assertFalse(flags.ai_chat_enabled)
            self.assertFalse(flags.full_card_enrichment_enabled)
            self.assertFalse(flags.board_control_enabled)

    def test_status_payload_exposes_canonical_new_scenarios(self) -> None:
        payload = get_ai_remodel_status_payload()
        self.assertEqual(payload["phase"], "module_1_3_entry_deactivation")
        self.assertEqual(
            sorted(payload["scenario_registry"].keys()),
            sorted(item.value for item in AiScenarioId),
        )
        ai_chat = payload["scenario_registry"]["ai_chat"]
        self.assertEqual(ai_chat["trigger_kind"], AiTriggerKind.USER_INVOKED.value)
        self.assertEqual(ai_chat["actor_mode"], AiActorMode.INTERACTIVE.value)
        self.assertEqual(ai_chat["scope_kind"], AiScopeKind.WORKSPACE.value)
        self.assertIn("internet_lookup", ai_chat["context_sources"])
        self.assertIn("not_background", ai_chat["boundaries"])
        self.assertEqual(payload["feature_flags"]["legacy_ux_enabled"], True)
        self.assertEqual(payload["feature_flags"]["ai_chat_enabled"], False)
        self.assertIn("board_dock_button", payload["legacy_entry_points"])
        self.assertIn("reuse_as_is", payload["backend_reuse"])
        self.assertEqual(payload["effective_mode"]["primary_interactive_path"], "legacy_agent_modal_manual_tasks")
        self.assertIn("ai_chat", payload["effective_mode"]["hidden"])
        self.assertIn("board_control", payload["effective_mode"]["background_only"])
        self.assertIn("board_dock_button", payload["entry_surface_registry"])
        self.assertIn("board_dock_button", payload["legacy_deactivation_map"])
        self.assertEqual(payload["entry_exposure"]["board_dock_button"]["exposure_state"], AiEntryExposureState.LEGACY_ONLY.value)
        self.assertEqual(payload["entry_exposure"]["card_agent_button"]["exposure_state"], AiEntryExposureState.LEGACY_ONLY.value)
        self.assertEqual(payload["entry_exposure"]["agent_enqueue_task_api"]["exposure_state"], AiEntryExposureState.ACTIVE.value)
        self.assertEqual(payload["entry_exposure"]["agent_tasks_modal"]["exposure_state"], AiEntryExposureState.LEGACY_ONLY.value)

    def test_canonical_registry_maps_have_three_scenarios(self) -> None:
        scenario_map = get_ai_scenario_map()
        self.assertEqual(set(scenario_map.keys()), {item.value for item in AiScenarioId})
        self.assertEqual(scenario_map["full_card_enrichment"]["scope_kind"], AiScopeKind.CARD.value)
        self.assertEqual(scenario_map["board_control"]["trigger_kind"], AiTriggerKind.SCHEDULED.value)
        self.assertEqual(scenario_map["ai_chat"]["rollout_state"], AiRolloutState.HIDDEN.value)

    def test_legacy_and_reuse_maps_are_present(self) -> None:
        legacy_map = get_ai_legacy_entry_point_map()
        reuse_map = get_ai_backend_reuse_map()
        self.assertIn("card_agent_button", legacy_map)
        self.assertIn("runner", reuse_map["reuse_with_adaptation"])
        self.assertIn("agent_modal_ux", reuse_map["legacy_only"])

    def test_effective_mode_resolver_is_conservative_by_default(self) -> None:
        payload = get_ai_effective_mode()
        self.assertTrue(payload["legacy_ux_enabled"])
        self.assertIn("ai_chat", payload["hidden"])
        self.assertIn("full_card_enrichment", payload["hidden"])
        self.assertIn("board_control", payload["hidden"])
        self.assertEqual(payload["primary_interactive_path"], "legacy_agent_modal_manual_tasks")
        self.assertIn("entry_exposure", payload)
        self.assertEqual(payload["entry_exposure"]["board_dock_button"]["exposure_state"], AiEntryExposureState.LEGACY_ONLY.value)
        self.assertEqual(payload["entry_exposure"]["future_ai_chat_window"]["exposure_state"], AiEntryExposureState.HIDDEN.value)

    def test_entry_surface_registry_and_deactivation_map_are_available(self) -> None:
        surface_map = get_ai_entry_surface_map()
        deactivation_map = get_ai_legacy_deactivation_map()
        exposure_map = get_ai_entry_exposure_map()
        self.assertIn("agent_manual_prompt", surface_map)
        self.assertIn("agent_tasks_modal", deactivation_map)
        self.assertIn("card_autofill_toggle", exposure_map)
        self.assertEqual(exposure_map["future_board_control_toggle"]["exposure_state"], AiEntryExposureState.HIDDEN.value)
        self.assertEqual(exposure_map["card_autofill_toggle"]["exposure_state"], AiEntryExposureState.LEGACY_ONLY.value)


if __name__ == "__main__":
    unittest.main()
