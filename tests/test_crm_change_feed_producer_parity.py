from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.crm_change_feed_producer_parity import (  # noqa: E402
    MANIFEST_PATH,
    REQUIRED_CHANGE_TYPES,
    REQUIRED_ENTITY_DOMAINS,
    build_producer_inventory,
)


class CrmChangeFeedProducerParityTests(unittest.TestCase):
    def manifest_copy(self) -> dict:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def build_with_manifest(self, manifest: dict) -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "producer-manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            return build_producer_inventory(manifest_path=path)

    def test_all_write_routes_have_handler_gateway_producer_and_test_evidence(self) -> None:
        result = build_producer_inventory()

        self.assertEqual([], result["issues"])
        self.assertTrue(result["summary"]["producer_complete"])
        self.assertEqual(0, result["summary"]["gaps"])
        self.assertEqual(94, result["summary"]["write_actions"])
        self.assertEqual(71, result["summary"]["executor_contract_only"])
        self.assertEqual(71, result["summary"]["executor_contract_resolved"])
        self.assertEqual(56, result["summary"]["canonical_route_feed_readback"])
        self.assertEqual(15, result["summary"]["reasoned_route_contract_exemptions"])
        self.assertTrue(result["summary"]["canonical_contract_complete"])
        self.assertEqual(len(REQUIRED_ENTITY_DOMAINS), result["summary"]["entity_domains"])
        self.assertEqual(sorted(REQUIRED_CHANGE_TYPES), result["summary"]["change_types"])
        for row in result["matrix"]:
            self.assertEqual("covered", row["status"], row)
            if row["producer_kind"] != "privacy_exemption":
                self.assertTrue(row["gateway"], row)
            self.assertTrue(row["route_handler_test_evidence"], row)
            self.assertTrue(row["producer_test_evidence"], row)
            self.assertTrue(row["producer_kind"], row)
            if row["readback_class"] == "executor_contract_only":
                self.assertTrue(row["canonical_route_contract"], row)

    def test_removing_one_route_creates_a_machine_visible_gap(self) -> None:
        manifest = self.manifest_copy()
        state_group = next(
            group
            for group in manifest["producer_groups"]
            if group["name"] == "json_state_commit_projection"
        )
        state_group["routes"].remove("/api/create_card")

        result = self.build_with_manifest(manifest)

        self.assertFalse(result["summary"]["producer_complete"])
        self.assertIn(
            "producer_route_uncovered",
            {issue["code"] for issue in result["issues"]},
        )

    def test_exemption_cannot_expand_beyond_reviewed_allowlist(self) -> None:
        manifest = self.manifest_copy()
        state_group = next(
            group
            for group in manifest["producer_groups"]
            if group["name"] == "json_state_commit_projection"
        )
        privacy_group = next(
            group for group in manifest["producer_groups"] if group["kind"] == "privacy_exemption"
        )
        state_group["routes"].remove("/api/create_card")
        privacy_group["routes"].append("/api/create_card")
        privacy_group["route_evidence"]["/api/create_card"] = {
            "source": "src/minimal_kanban/services/card_service.py",
            "pattern": "def create_card(self, payload: dict)",
        }

        result = self.build_with_manifest(manifest)

        self.assertIn(
            "producer_exemption_not_allowlisted",
            {issue["code"] for issue in result["issues"]},
        )

    def test_missing_exact_producer_test_pattern_fails_closed(self) -> None:
        manifest = self.manifest_copy()
        state_group = next(
            group
            for group in manifest["producer_groups"]
            if group["name"] == "json_state_commit_projection"
        )
        state_group["test_evidence"][0]["pattern"] = "missing_producer_contract_test"

        result = self.build_with_manifest(manifest)

        self.assertIn(
            "producer_test_evidence_missing",
            {issue["code"] for issue in result["issues"]},
        )


if __name__ == "__main__":
    unittest.main()
