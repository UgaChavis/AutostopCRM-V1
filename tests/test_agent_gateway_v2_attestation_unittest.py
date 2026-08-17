from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTEST_STYLE_TESTS = ROOT / "tests" / "test_agent_gateway_v2_attestation_script.py"
_TMP_PATH_CASES = {
    "test_state_is_stop_the_line_and_contains_no_business_payload",
    "test_cleanup_orchestrator_persists_terminal_verified_state",
}
_CASE_NAMES = (
    "test_manifest_covers_exact_public_and_crm_operation_contracts",
    "test_runtime_evidence_never_serializes_request_or_response_payloads",
    "test_entity_mapping_prefers_exact_id_over_relationship_reference",
    "test_state_is_stop_the_line_and_contains_no_business_payload",
    "test_parser_requires_one_explicit_campaign_action",
    "test_completed_pending_cleanup_is_successful_but_not_verified",
    "test_safe_summary_omits_cleanup_call_evidence",
    "test_cleanup_helpers_require_exact_one_ruble_effect",
    "test_cleanup_orchestrator_persists_terminal_verified_state",
    "test_employee_snapshot_matches_backend_active_then_name_order",
    "test_error_codes_are_allowlisted_before_persistence",
    "test_expected_call_failure_carries_safe_evidence",
    "test_read_operation_retry_uses_new_attempt_key_and_proves_deduplication",
    "test_board_write_executor_scopes_apply_and_archives_fixture",
    "test_inventory_save_executor_proves_conflict_replay_and_exact_reread",
    "test_document_delete_executor_proves_conflict_replay_and_absence",
    "test_frozen_manifest_check_stops_on_public_schema_drift",
)


def _load_cases_module():
    spec = importlib.util.spec_from_file_location("gateway_attestation_cases", PYTEST_STYLE_TESTS)
    if spec is None or spec.loader is None:
        raise AssertionError("Gateway attestation cases must be importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(sys.platform == "linux", "attestation runner is verified on Linux CI")
class GatewayAttestationLinuxTests(unittest.TestCase):
    pass


def _make_case(case_name: str):
    def case(self) -> None:
        module = _load_cases_module()
        test_function = getattr(module, case_name)
        if case_name in _TMP_PATH_CASES:
            with tempfile.TemporaryDirectory() as directory:
                test_function(Path(directory))
            return
        test_function()

    case.__name__ = case_name
    return case


for _case_name in _CASE_NAMES:
    setattr(GatewayAttestationLinuxTests, _case_name, _make_case(_case_name))
