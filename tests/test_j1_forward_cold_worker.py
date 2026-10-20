"""Synthetic protocol documents; no native truth, SSH or signing is supplied."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_state as state
from scripts import j1_forward_cold_telegram as telegram
from scripts import j1_forward_cold_worker as cold
from scripts import j1_forward_io as io
from scripts import j1_forward_worker as worker
from tests import test_j1_forward_cold_capture as capture_tests
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
            "deadline_ns": time.monotonic_ns() + 590 * 10**9,
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
        self.telegram_policy = {
            "schema": telegram.FRONTIER_SCHEMA,
            "scope": "production_native",
            "binding": binding,
            "source_revision": binding["candidate_manager_sha"],
            "roles": list(telegram.ROLES),
            "cut": telegram.FRONTIER_CUT,
            "account_self_sha256": {
                role: letter * 64 for role, letter in zip(telegram.ROLES, "ab")
            },
        }
        self.telegram_policy_ref = self.fixture.ref("telegram-policy.json", self.telegram_policy)
        inputs = {
            "schema": "autostop.j1.cold-producer-inputs.v1",
            "scope": "production_native",
            "binding": binding,
            "telegram_frontier_policy_ref": self.telegram_policy_ref,
        }
        inputs_ref = self.fixture.ref("producer-inputs.json", inputs)
        source = {"schema": "autostop.j1.cold-source-package.v2", "producer_inputs_ref": inputs_ref}
        source_ref = self.fixture.ref("source-package.json", source)
        package["source_package_ref"] = source_ref
        self.telegram_source = {
            "source_package_ref": source_ref,
            "source_package": source,
            "producer_inputs_ref": inputs_ref,
            "producer_inputs": inputs,
        }
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
            old["backup_capture"]["capture_interval_ref"] = self.fixture.ref(
                "cold-capture-completed.json", {"synthetic": "capture-interval"}
            )
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
            "schema": "autostop.j1.cold-crm-runtime-acceptance.v2",
            "role": "crm-embedded",
            "epoch_ref": plan["dispatch_epoch_ref"],
            "package_ref": plan["cold_authority_package_ref"],
            "spec_ref": plan["cold_crm_spec_ref"],
            "manager_source_revision": plan["candidate_manager_sha"],
            "binding_ref": epoch["runtime_bindings"]["crm-embedded"],
            "http_ingress_ref": {
                "path": "/etc/autostop-manager-cold/"
                + epoch["epoch_id"]
                + "/crm-embedded/http-ingress.json",
                "sha256": "e" * 64,
            },
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
            "telegram_frontiers": self.telegram_observation(package["binding"], observations),
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

    def telegram_observation(self, binding, observations):
        actors = {
            role: {key: observations[unit][key] for key in telegram.ACTOR_FIELDS}
            for role, unit in telegram.UNITS.items()
        }
        observed = time.monotonic_ns()
        rows = {}
        for role, actor in actors.items():
            process = {
                **actor,
                "boot_id": binding["boot_id"],
                "source_revision": binding["candidate_manager_sha"],
                "started_monotonic_ns": binding["not_before_ns"] + 1,
            }
            cut_path, quarantine_path = telegram._paths(binding, role, actor)
            cut = {
                "schema": telegram.CUT_SCHEMA,
                **{key: binding[key] for key in ("attempt_id", "boot_id", "generation")},
                "epoch_id": binding["generation"],
                "source_revision": binding["candidate_manager_sha"],
                "role": role,
                "main_pid": actor["pid"],
                "start_ticks": actor["start_ticks"],
                "observed_monotonic_ns": observed,
                "account_self_sha256": self.telegram_policy["account_self_sha256"][role],
                "pts": 1,
                "qts": 0,
                "seq": 1,
                "supported_updates": "private_account_global_pts_only",
                "historical_replay": "denied",
                "observation_only": True,
                "release_authority": False,
                "quarantine_path": quarantine_path,
            }
            rows[role] = {
                "process_before": process,
                "process_after": process,
                "cut": cut,
                "cut_path": cut_path,
                "cut_sha256": contract.digest(cut),
                "quarantine_path": quarantine_path,
                "quarantine_sha256": "9" * 64,
                "quarantine": {
                    "status": "clear",
                    "record_count": 0,
                    "overflow_count": 0,
                    "overflow_chain_sha256": "0" * 64,
                    "complete_observation_log": True,
                    "historical_inventory_complete": False,
                    "replay": "forbidden",
                    "record_fingerprints_sha256": contract.digest([]),
                },
            }
        body = {
            "schema": telegram.SCHEMA,
            "scope": "production_native",
            "binding": binding,
            "policy_ref": self.telegram_policy_ref,
            "policy": self.telegram_policy,
            "actors_by_role": actors,
            "observed_monotonic_ns": observed,
            "roles": rows,
            "historical_inventory_complete": False,
            "native_acceptance": False,
            "release_authority": False,
        }
        reference = self.fixture.ref("telegram-observation.json", body)
        return {
            "reference": reference,
            "digest": reference["sha256"],
            "document": body,
            "source": self.telegram_source,
        }

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

    def test_closed_production_response_requires_attempt_bound_completed_interval(self):
        for failure in ("missing", "other_attempt"):
            response = self.response()
            captured = response["evidence"]["backup_capture"]
            if failure == "missing":
                captured.pop("capture_interval_ref")
            else:
                captured["capture_interval_ref"]["path"] = worker.ATTEMPTS + "/other/completed.json"
            self.rebind(response)
            with self.subTest(failure=failure), self.assertRaises(io.Refusal):
                cold.validate_response(response, attempt=self.fixture.attempt, stage="closed")

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

    def test_hash_valid_frontier_with_unbound_account_or_policy_cannot_be_signed(self):
        for failure in ("missing", "wrong_account", "unbound_policy", "overflow"):
            response = self.response("activated")
            accepted = response["evidence"]["accepted"]
            frontier = accepted["telegram_frontiers"]
            body = frontier["document"]
            row = body["roles"][telegram.ROLES[0]]
            if failure == "missing":
                del accepted["telegram_frontiers"]
            elif failure == "wrong_account":
                row["cut"]["account_self_sha256"] = "f" * 64
                row["cut_sha256"] = contract.digest(row["cut"])
            elif failure == "unbound_policy":
                body["policy"] = {
                    **body["policy"],
                    "account_self_sha256": dict.fromkeys(telegram.ROLES, "f" * 64),
                }
                body["policy_ref"] = self.fixture.ref("other-policy.json", body["policy"])
            else:
                row["quarantine"]["overflow_count"] = 1
            frontier["reference"] = self.fixture.ref("changed-frontiers.json", body)
            frontier["digest"] = frontier["reference"]["sha256"]
            self.rebind(response)
            with self.subTest(failure=failure), self.assertRaises(io.Refusal):
                cold.validate_response(response, attempt=self.fixture.attempt, stage="activated")

    def test_ready_seam_rechecks_both_receipts_with_explicit_native_io_doubles(self):
        response = self.response("activated")
        evidence, package = response["evidence"], response["evidence"]["package"]
        accepted = evidence["accepted"]
        frontier = accepted["telegram_frontiers"]
        sources = frontier["source"]
        documents = {
            package["source_package_ref"]["path"]: sources["source_package"],
            sources["producer_inputs_ref"]["path"]: sources["producer_inputs"],
            self.telegram_policy_ref["path"]: self.telegram_policy,
            **{
                ref["path"]: accepted["observations"][unit]
                for unit, ref in accepted["accepted"].items()
            },
        }
        instance = object.__new__(lifecycle.Controller)
        instance.root = self.fixture.root / "telegram-ready"
        instance.root.mkdir(mode=0o700)
        instance.provider = SimpleNamespace(
            scope="production_native",
            boot=lambda: package["binding"]["boot_id"],
            read_reference=lambda ref: io.canonical(documents[ref["path"]]),
        )
        instance.package_ref = response["plan"]["cold_authority_package_ref"]
        instance.epoch_ref = response["plan"]["dispatch_epoch_ref"]
        instance.verify = Mock(return_value=({"package": package}, {}))
        instance.context = lambda unit: (
            None,
            {"ticket": unit, "state": {"execution_possible": True}},
            {},
        )
        instance.backend = SimpleNamespace(
            observe=lambda unit, _context, **_kw: {
                k: accepted["observations"][unit][k]
                for k in ("pid", "start_ticks", "invocation_id")
            }
        )
        raw = {}
        for row in frontier["document"]["roles"].values():
            raw[row["cut_path"]] = io.canonical(row["cut"])
            quarantine = {k: v for k, v in row["cut"].items() if k != "quarantine_path"}
            quarantine.update(
                schema=telegram.QUARANTINE_SCHEMA,
                status="clear",
                records=[],
                overflow_count=0,
                overflow_chain_sha256="0" * 64,
                complete_observation_log=True,
                historical_inventory_complete=False,
                replay="forbidden",
            )
            raw[row["quarantine_path"]] = io.canonical(quarantine)
        observe = telegram.observe
        publications = {}

        def publish(path, body, _root):
            path.write_bytes(io.canonical(body))
            # The protocol is POSIX even when its private bytes live on a
            # Windows drive. Never pass that native path to cold.reference.
            logical = str(PurePosixPath(self.fixture.prefix) / "telegram-ready" / path.name)
            publications[logical] = path
            return {"path": logical, "sha256": contract.digest(body)}

        with (
            patch.object(
                telegram,
                "observe",
                side_effect=lambda *a, **kw: observe(*a, **kw, read_bytes=raw.__getitem__),
            ),
            patch.object(lifecycle, "_publish", side_effect=publish),
            patch.object(lifecycle.os, "sysconf", return_value=100, create=True),
        ):
            result = instance.telegram_frontiers(accepted["accepted"])
        self.assertEqual(result["source"], sources)
        self.assertEqual(instance.verify.call_count, 2)
        self.assertEqual(
            io.sha(publications[result["reference"]["path"]].read_bytes()), result["digest"]
        )
        self.assertEqual(set(result["document"]["roles"]), set(telegram.ROLES))

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
        response["evidence"]["backup_capture"].pop("capture_interval_ref")
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

    def test_interval_chain_is_read_from_independent_restore_before_acceptance(self):
        manifest, response, read = capture_tests.RestoredIntervalTests().fixture()
        attempt = response["plan"]["attempt_id"]
        raw = {}
        original = response["evidence"]["backup_capture"]["capture_interval_ref"]

        def relocate(reference):
            path = worker.ATTEMPTS + "/" + attempt + "/" + Path(reference["path"]).name
            body = io.document(read(reference))
            for key in ("native_ref", "entry_ref"):
                if key in body:
                    body[key] = relocate(body[key])
            raw[path] = io.canonical(body)
            return {"path": path, "sha256": io.sha(raw[path])}

        witnesses = manifest["external_closure_observations"]
        for witness in witnesses:
            witness.update(relocate({key: witness[key] for key in ("path", "sha256")}))
        complete = io.document(read(original))
        complete["witnesses_sha256"] = contract.digest(witnesses)
        complete["entry_ref"] = relocate(complete["entry_ref"])
        complete["native_ref"] = relocate(complete["native_ref"])
        path = worker.ATTEMPTS + "/" + attempt + "/completion.json"
        raw[path] = io.canonical(complete)
        response["evidence"]["backup_capture"] = {
            "capture_interval_ref": {"path": path, "sha256": io.sha(raw[path])},
            "manifest": {"path": "/private/manifest.json", "sha256": contract.digest(manifest)},
            "files": 0,
            "bytes": 0,
        }
        with tempfile.TemporaryDirectory() as temporary:
            package, restored = Path(temporary) / "package", Path(temporary) / "restored"
            package.mkdir()
            restored.mkdir()
            artifacts = []
            for index, (name, body) in enumerate(sorted(raw.items())):
                payload = f"{index:06d}.bin"
                (restored / payload).write_bytes(body)
                artifacts.append(
                    {
                        "logical_name": "control/"
                        + PurePosixPath(name)
                        .relative_to(PurePosixPath(worker.ATTEMPTS).parent)
                        .as_posix(),
                        "payload": payload,
                        "bytes": len(body),
                        "sha256": io.sha(body),
                    }
                )
            control = io.canonical({"artifacts": artifacts})
            (package / "manifest.json").write_bytes(control)
            response["control_archive"] = {"manifest_sha256": io.sha(control)}
            result = cold.restored_capture_fences(response, manifest, package, restored)
            self.assertEqual(result["boundary_count"], 4)
            self.assertFalse(result["native_acceptance"])
            completed = next(
                row for row in artifacts if row["logical_name"].endswith("/completion.json")
            )
            (restored / completed["payload"]).unlink()
            with self.assertRaises(FileNotFoundError):
                cold.restored_capture_fences(response, manifest, package, restored)


if __name__ == "__main__":
    unittest.main()
