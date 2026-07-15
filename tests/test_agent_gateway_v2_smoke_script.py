from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_agent_gateway_v2.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("check_agent_gateway_v2", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("check_agent_gateway_v2.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AgentGatewayV2SmokeScriptTests(unittest.TestCase):
    def test_expected_surface_is_exactly_24_tools(self) -> None:
        module = load_script_module()

        self.assertEqual(24, len(module.EXPECTED_TOOL_NAMES))
        self.assertFalse(module.EXPECTED_TOOL_NAMES & module.FORBIDDEN_LEGACY_TOOL_NAMES)

    def test_exhaustive_flag_is_explicit(self) -> None:
        module = load_script_module()

        standard = module.build_parser().parse_args([])
        exhaustive = module.build_parser().parse_args(["--exhaustive"])

        self.assertFalse(standard.exhaustive)
        self.assertTrue(exhaustive.exhaustive)

    def test_state_version_requires_integer_summary_value(self) -> None:
        module = load_script_module()

        self.assertEqual(7, module._state_version({"summary": {"state_version": 7}}))
        with self.assertRaisesRegex(RuntimeError, "state_version"):
            module._state_version({"summary": {}})

    def test_exhaustive_inventory_contract_includes_required_card_target(self) -> None:
        module = load_script_module()

        arguments = module._safe_inventory_contract_arguments("case-id")

        self.assertEqual("write_off", arguments["planned_changes"]["movement_type"])
        self.assertEqual("synthetic-card-target", arguments["planned_changes"]["card_id"])
        self.assertEqual("gateway-v2-contract-case-id", arguments["idempotency_key"])
        self.assertTrue(arguments["dry_run"])


if __name__ == "__main__":
    unittest.main()
