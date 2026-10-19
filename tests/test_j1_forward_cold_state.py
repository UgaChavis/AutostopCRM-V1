from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

from scripts import j1_forward_cold_state as cold
from scripts import j1_forward_io as io
from scripts import j1_forward_recovery_state as recovery
from scripts import j1_forward_release_rehearsal as rehearsal
from scripts import j1_forward_variant as variant


@unittest.skipUnless(
    sys.platform.startswith("linux"), "Requires real Linux flock, AF_UNIX and SO_PEERCRED"
)
class ColdRecoveryStateTests(unittest.TestCase):
    def test_exact_cold_origin_retains_pending_and_permanent_old_writer_ban(self):
        with rehearsal.TraceFixture() as fixture:
            package = fixture.produce()
            with fixture.custody:
                pending = fixture.store.pending_path.read_bytes()
                directory = fixture.control / "attempts" / fixture.binding["attempt_id"]
                origin = {
                    **fixture.origin,
                    "recovery_code_ref": fixture._write(
                        directory / "recovery-code.json", {"scope": "native_fixture"}
                    ),
                    "ci_ref": fixture._write(directory / "ci.json", {"scope": "native_fixture"}),
                }
                epoch = fixture._write(directory / "epoch-ref.json", {"scope": "native_fixture"})
                spec = fixture._write(directory / "crm-spec.json", {"scope": "native_fixture"})
                baseline = fixture._write(
                    directory / "baseline.json",
                    {
                        "schema": cold.BASELINE_SCHEMA,
                        "attempt_id": fixture.binding["attempt_id"],
                        "native": {
                            "cold_authority_package_ref": package,
                            "dispatch_epoch_ref": epoch,
                        },
                        "recovery_origin": origin,
                    },
                )
                plan = fixture._plan(fixture.binding["attempt_id"])
                plan.update(
                    schema=cold.PLAN_SCHEMA,
                    scope="crm_and_manager_all_profiles_cold",
                    candidate_manager_sha=fixture.binding["candidate_manager_sha"],
                    cold_authority_package_ref=package,
                    dispatch_epoch_ref=epoch,
                    cold_crm_spec_ref=spec,
                    baseline_ref=baseline,
                )
                plan["candidate"]["revision"] = fixture.binding["candidate_crm_sha"]
                plan_ref = fixture._write(directory / "plan.json", plan)
                envelope = {
                    "schema": cold.RECOVERY_SCHEMA,
                    "recovery_id": plan["attempt_id"],
                    **origin,
                    "plan_ref": plan_ref,
                }

                class PrivateStore(cold.ColdStore):
                    def _read_reference(self, reference):
                        if Path(reference["path"]).is_relative_to(fixture.control):
                            return super()._read_reference(reference)
                        return fixture.provider.read_reference(reference)

                    def read_opaque_reference(self, reference):
                        return fixture.provider.read_opaque_reference(reference)

                store = PrivateStore(fixture.control / "state", fixture.base_sha256)
                variant.validate_plan(plan, fixture.base_sha256)
                for changed in (
                    {**plan, "old_writer_allowed": True},
                    {key: value for key, value in plan.items() if key != "cold_crm_spec_ref"},
                ):
                    with self.assertRaises(io.Refusal):
                        variant.validate_plan(changed, fixture.base_sha256)
                reference = store.begin_recovery(envelope)
                self.assertEqual(store.active_plan()["plan"], plan)
                self.assertEqual(store.pending_path.read_bytes(), pending)
                self.assertEqual(io.document(io.referenced(reference)), envelope)
                self.assertEqual(store.begin_recovery(envelope), reference)
                self.assertFalse(store.active_plan()["plan"]["old_writer_allowed"])
                wrong = copy.deepcopy(envelope)
                wrong["origin_scope_ref"]["sha256"] = "0" * 64
                with self.assertRaises(io.Refusal):
                    store._envelope(wrong)
                self.assertEqual(store.pending_path.read_bytes(), pending)
                self.assertEqual(envelope["schema"], cold.RECOVERY_SCHEMA)
                self.assertNotEqual(envelope["schema"], recovery.RECOVERY_SCHEMA)


if __name__ == "__main__":
    unittest.main()
