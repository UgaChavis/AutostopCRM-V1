"""Synthetic protocol documents; no native truth, SSH or signing is supplied."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_cold_state as state
from scripts import j1_forward_cold_worker as cold
from scripts import j1_forward_io as io
from scripts import j1_forward_worker as worker
from tests import test_j1_forward_cold_qualification as qualification_tests
from tests import test_j1_forward_cold_runtime as runtime_tests
from tests import test_j1_forward_worker as warm_tests


class ColdWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = warm_tests.WorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def response(self, stage="closed"):
        response = self.fixture.response(stage)
        plan, baseline = response["plan"], response["baseline"]
        generation = "00000000-0000-4000-8000-000000000007"
        boot = "00000000-0000-4000-8000-000000000008"
        epoch_path = "/var/lib/autostop-crm-forward/cold/v1/epochs/" + generation + "/epoch.json"
        binding = {
            "attempt_id": plan["attempt_id"],
            "boot_id": boot,
            "generation": generation,
            "consumer_sha256": "9" * 64,
            "candidate_crm_sha": plan["candidate"]["revision"],
            "candidate_manager_sha": "a" * 40,
            "deployment_lock_identity": [1, 2],
            "not_before_ns": time.monotonic_ns() - 10**9,
            "deadline_ns": time.monotonic_ns() + 600 * 10**9,
        }
        package = {
            name: self.fixture.ref(name + ".json", {"synthetic": name})
            for name in contract.PACKAGE_FIELDS
        }
        package.update(
            schema=contract.SCHEMA,
            scope="production_native",
            contract=contract.CONTRACT,
            binding=binding,
            policies={
                unit: self.fixture.ref(unit + "-policy.json", {"synthetic": unit})
                for unit in contract.ALLOWED
            },
            permanent_denied_units=list(contract.DENIED),
        )
        package_ref = {
            "path": "/etc/autostop-crm-forward/cold/v1/authority.json",
            "sha256": contract.digest(package),
        }
        roles = {
            "manager-mcp",
            "j1",
            "scheduler",
            "work-telegram",
            "personal-telegram",
            "wake",
            "crm-embedded",
        }
        epoch = {
            "schema": "autostop.j1.cold-dispatch-epoch.v1",
            "scope": "production_native",
            **{key: binding[key] for key in ("attempt_id", "boot_id", "generation")},
            "epoch_id": generation,
            "package_ref": package_ref,
            "journal_root": epoch_path.rsplit("/", 1)[0] + "/journals",
            "runtime_bindings": {
                role: {
                    "path": f"/etc/autostop-manager-cold/{generation}/{role}/binding.json",
                    "sha256": "e" * 64,
                }
                for role in roles
            },
            "slots": {
                unit: f"00000000-0000-4000-8000-{index:012d}"
                for index, unit in enumerate(contract.ALLOWED, 1)
            },
        }
        epoch_ref = {"path": epoch_path, "sha256": contract.digest(epoch)}
        plan.update(
            schema=state.PLAN_SCHEMA,
            scope=contract.PLAN_SCOPE,
            cold_authority_package_ref=package_ref,
            dispatch_epoch_ref=epoch_ref,
            candidate_manager_sha=binding["candidate_manager_sha"],
            cold_crm_spec_ref=self.fixture.ref("cold-crm-spec.json", {"synthetic": True}),
        )
        response["qualification"] = qualification_tests.document(
            {key: plan["candidate"][key] for key in ("revision", "tree", "consumer_sha256")}
        )
        baseline["ci"].update(run_id=101, run_url=cold.qualification.RUN_URL + "101")
        baseline.update(
            schema=state.BASELINE_SCHEMA,
            native={"cold_authority_package_ref": package_ref, "dispatch_epoch_ref": epoch_ref},
            controller_request_ref=self.fixture.ref("controller-request.json", {"synthetic": True}),
            controller_launch_ref=self.fixture.ref("controller-launch.json", {"synthetic": True}),
            qualification_ref=self.fixture.ref("qualification.json", response["qualification"]),
        )
        profile = worker.variant.backup_engine(plan).profile_sha256()
        baseline["backup_profile_sha256"] = response["scope_profile_sha256"] = profile
        response["backup_context"].update(
            candidate_manager_sha=binding["candidate_manager_sha"], scope_profile_sha256=profile
        )
        response["baseline_ref"] = plan["baseline_ref"] = self.fixture.ref(
            "baseline.json", baseline
        )
        response["plan_ref"] = self.fixture.ref("plan.json", plan)
        native = {
            "schema": cold.NATIVE_SCHEMA,
            "scope": "production_native",
            "ok": True,
            "phase": stage,
            "attempt_id": plan["attempt_id"],
            "package_ref": package_ref,
            "epoch_ref": epoch_ref,
            "binding_sha256": contract.digest(binding),
            "boot_id": boot,
            "observed_monotonic_ns": time.monotonic_ns(),
            "original_barriers_closed": True,
            "original_hold_retained": True,
            "old_writer_allowed": False,
            "candidate_running": stage == "activated",
            "all_six_policy_refs": package["policies"],
            "retired_scope_sha256": "b" * 64,
        }
        old = response["evidence"]
        evidence = {"native": native, "package": package, "epoch": epoch}
        inspection = {
            "schema": "autostop.j1.crm-manager-cold-forward-inspection.v1",
            "stage": stage,
            "attempt_id": plan["attempt_id"],
            "plan_sha256": response["plan_ref"]["sha256"],
            "baseline_sha256": response["baseline_ref"]["sha256"],
            "candidate": plan["candidate"],
            "package_ref": package_ref,
            "epoch_ref": epoch_ref,
            "native_ref": self.fixture.ref("cold-native.json", native),
            "original_barriers_closed": True,
            "original_hold_retained": True,
            "old_writer_allowed": False,
        }
        if stage == "closed":
            evidence.update(
                backup_capture=old["backup_capture"],
                backup_restore={
                    "manifest_sha256": old["backup_capture"]["manifest_sha256"],
                    "provided_artifacts_restored_exactly": True,
                    "source_evidence_unchanged": True,
                },
            )
            inspection.update(
                writer_closure_confirmed=True,
                backup_capture_ref=self.fixture.ref(
                    "backup-evidence.json", evidence["backup_capture"]
                ),
                backup_restore_ref=self.fixture.ref(
                    "backup-restore.json", evidence["backup_restore"]
                ),
            )
        else:
            self.activation(evidence, inspection, response)
        response["evidence"], response["inspection"] = evidence, inspection
        response["inspection_ref"] = self.fixture.ref("inspection-cold.json", inspection)
        return response

    def activation(self, evidence, inspection, response):
        plan, epoch, package = response["plan"], evidence["epoch"], evidence["package"]
        observations = {
            unit: {
                "schema": "autostop.j1.cold-slot-runtime-observation.v1",
                "scope": "production_native",
                "package_ref": plan["cold_authority_package_ref"],
                "epoch_ref": plan["dispatch_epoch_ref"],
                "logical_unit": unit,
                "execution_unit": "autostop-crm-forward-" + epoch["slots"][unit] + ".service",
                "policy_ref": package["policies"][unit],
                "pid": index + 100,
                "start_ticks": 200,
                "invocation_id": f"{index:032x}",
                "business_acceptance": False,
            }
            for index, unit in enumerate(contract.ALLOWED, 1)
        }
        refs = {
            unit: self.fixture.ref("accepted-" + unit + ".json", body)
            for unit, body in observations.items()
        }
        crm = {
            "schema": "autostop.j1.cold-crm-runtime-acceptance.v1",
            "role": "crm-embedded",
            "epoch_ref": plan["dispatch_epoch_ref"],
            "package_ref": plan["cold_authority_package_ref"],
            "spec_ref": plan["cold_crm_spec_ref"],
            "manager_source_revision": plan["candidate_manager_sha"],
            "binding_ref": epoch["runtime_bindings"]["crm-embedded"],
            "independent_observations": 2,
            "business_acceptance": False,
            "instance": {
                "main_pid": 2000,
                "start_ticks": 200,
                "image_id": plan["candidate"]["image"],
                "restart_count": 0,
            },
        }
        crm_ref = self.fixture.ref("crm-accepted.json", crm)
        accepted = {
            "schema": cold.ACCEPTED_SCHEMA,
            "attempt_id": plan["attempt_id"],
            "package_ref": plan["cold_authority_package_ref"],
            "epoch_ref": plan["dispatch_epoch_ref"],
            "accepted": refs,
            "observations": observations,
            "crm_ref": crm_ref,
            "crm": crm,
            "execution_acceptance_sha256": contract.digest(refs),
        }
        runtime = runtime_tests.report(
            {key: plan["candidate"][key] for key in ("image", "revision", "tree")},
            plan["attempt_id"],
        )
        evidence.update(accepted=accepted, crm=crm, runtime=runtime)
        inspection.update(
            cold_guards_executed=True,
            runtime_checks_passed=True,
            execution_acceptance_sha256=contract.digest(refs),
            accepted_ref=self.fixture.ref("accepted.json", accepted),
            crm_ref=crm_ref,
            runtime_ref=self.fixture.ref("runtime.json", runtime),
        )

    def rebind(self, response):
        for name in response["evidence"]:
            if name not in {"package", "epoch"}:
                path = response["inspection"][name + "_ref"]["path"]
                response["inspection"][name + "_ref"] = {
                    "path": path,
                    "sha256": contract.digest(response["evidence"][name]),
                }
        response["inspection_ref"] = self.fixture.ref(
            "inspection-cold.json", response["inspection"]
        )

    def test_closed_and_all_seven_activation_documents_use_explicit_cold_contract(self):
        for stage in ("closed", "activated"):
            response = self.response(stage)
            self.assertIs(
                response,
                worker.validate_response(response, attempt=self.fixture.attempt, stage=stage),
            )

    def test_false_fence_wrong_phase_and_native_fixture_scope_are_rejected(self):
        for field, value in (
            ("original_barriers_closed", False),
            ("original_hold_retained", False),
            ("old_writer_allowed", True),
            ("scope", "native_fixture"),
            ("phase", "activated"),
        ):
            response = self.response()
            response["evidence"]["native"][field] = value
            self.rebind(response)
            with self.subTest(field=field), self.assertRaises(io.Refusal):
                cold.validate_response(response, attempt=self.fixture.attempt, stage="closed")

    def test_duplicate_stale_or_missing_runtime_cannot_satisfy_seven_acceptances(self):
        for failure in ("duplicate", "stale", "missing"):
            response = self.response("activated")
            accepted = response["evidence"]["accepted"]
            first, second = contract.ALLOWED[:2]
            if failure == "missing":
                del accepted["observations"][first]
            else:
                row = accepted["observations"][first]
                if failure == "duplicate":
                    row["pid"] = accepted["observations"][second]["pid"]
                else:
                    row["scope"] = "native_fixture"
                accepted["accepted"][first] = self.fixture.ref("changed-role.json", row)
                accepted["execution_acceptance_sha256"] = contract.digest(accepted["accepted"])
                response["inspection"]["execution_acceptance_sha256"] = accepted[
                    "execution_acceptance_sha256"
                ]
            self.rebind(response)
            with self.subTest(failure=failure), self.assertRaises(io.Refusal):
                cold.validate_response(response, attempt=self.fixture.attempt, stage="activated")

    def test_cold_signed_payload_cannot_claim_preserved_old_native_runtime(self):
        response = self.response("activated")
        instance = object.__new__(cold.ColdWorker)
        payload = instance._activation_payloads(response, {"phase": "activated"})[
            "native_acceptance"
        ]
        self.assertTrue(payload["cold_guards_executed"])
        self.assertNotIn("native_preserved", payload)
        instance.ssh, instance.ssh_key = Path("ssh.exe"), Path("private")
        arguments = instance._ssh("inspect", self.fixture.attempt, stage="closed")
        self.assertIn(cold.REMOTE, arguments)
        self.assertNotIn(worker.REMOTE, arguments)

    def test_seven_runtime_identities_require_distinct_positive_integer_pids(self):
        for failure in (
            "native_pid_reused",
            "crm_native_pid_reused",
            "crm_negative_pid",
            "crm_boolean_pid",
            "crm_zero_ticks",
            "crm_boolean_ticks",
            "crm_boolean_restart_count",
            "crm_float_observation_count",
            "crm_same_init_pid",
            "crm_boolean_init_pid",
        ):
            response = self.response("activated")
            accepted = response["evidence"]["accepted"]
            first, second = contract.ALLOWED[:2]
            crm = response["evidence"]["crm"]
            if failure == "native_pid_reused":
                row = accepted["observations"][first]
                row["pid"] = accepted["observations"][second]["pid"]
                row["start_ticks"] += 1
                accepted["accepted"][first] = self.fixture.ref("changed-role.json", row)
                accepted["execution_acceptance_sha256"] = contract.digest(accepted["accepted"])
                response["inspection"]["execution_acceptance_sha256"] = accepted[
                    "execution_acceptance_sha256"
                ]
            elif failure == "crm_float_observation_count":
                crm["independent_observations"] = 2.0
            else:
                field, value = {
                    "crm_native_pid_reused": ("main_pid", accepted["observations"][first]["pid"]),
                    "crm_negative_pid": ("main_pid", -1),
                    "crm_boolean_pid": ("main_pid", True),
                    "crm_zero_ticks": ("start_ticks", 0),
                    "crm_boolean_ticks": ("start_ticks", True),
                    "crm_boolean_restart_count": ("restart_count", False),
                    "crm_same_init_pid": ("init_pid", crm["instance"]["main_pid"]),
                    "crm_boolean_init_pid": ("init_pid", True),
                }[failure]
                crm["instance"][field] = value
            reference = self.fixture.ref("changed-crm.json", crm)
            accepted["crm_ref"] = response["inspection"]["crm_ref"] = reference
            self.rebind(response)
            with self.subTest(failure=failure), self.assertRaises(io.Refusal):
                cold.validate_response(response, attempt=self.fixture.attempt, stage="activated")

    def test_data_only_restore_cannot_skip_missing_or_changed_control_fence(self):
        response = self.response()
        body = response["evidence"]["native"]
        reference = self.fixture.ref("capture-native.json", body)
        manifest = {
            "artifacts": [],
            "external_closure_observations": [{"attempt_id": self.fixture.attempt, **reference}]
            * 2,
        }
        captured = response["evidence"]["backup_capture"]
        captured.update(
            manifest=self.fixture.ref("backup/manifest.json", manifest), files=0, bytes=0
        )
        with tempfile.TemporaryDirectory() as temporary:
            package, restored = Path(temporary) / "package", Path(temporary) / "restored"
            package.mkdir()
            restored.mkdir()
            control_manifest = {"artifacts": []}
            control_raw = io.canonical(control_manifest)
            (package / "manifest.json").write_bytes(control_raw)
            response["control_archive"]["manifest_sha256"] = io.sha(control_raw)
            with self.assertRaisesRegex(io.Refusal, "fence_not_restored"):
                cold.restored_capture_fences(response, manifest, package, restored)
            name = "control/attempts/" + self.fixture.attempt + "/capture-native.json"
            control_manifest["artifacts"] = [
                {"logical_name": name, "payload": "000000.bin", "sha256": reference["sha256"]}
            ]
            control_raw = io.canonical(control_manifest)
            (package / "manifest.json").write_bytes(control_raw)
            response["control_archive"]["manifest_sha256"] = io.sha(control_raw)
            (restored / "000000.bin").write_bytes(io.canonical(body))
            cold.restored_capture_fences(response, manifest, package, restored)
            (restored / "000000.bin").write_bytes(b"changed")
            with self.assertRaisesRegex(io.Refusal, "fence_changed"):
                cold.restored_capture_fences(response, manifest, package, restored)


if __name__ == "__main__":
    unittest.main()
