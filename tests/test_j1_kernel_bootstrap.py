"""Versioned consumer checks. Synthetic facts never constitute native acceptance."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_kernel_bootstrap as kernel
from scripts import j1_release_boundary as boundary
from scripts import j1_work_duty as duty
from tests import test_j1_work_duty as duty_tests


class KernelFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.root = Path(temporary.name).resolve()
        self.addCleanup(temporary.cleanup)
        # Host ownership only is substituted. Actual bytes, bounded reads,
        # identities, reference hashes and strict JSON parsing remain real.
        owner = patch.object(kernel, "protected")
        owner.start()
        self.addCleanup(owner.stop)
        self.preparation = {
            "attempt_id": "a5f8daac-e7e0-471d-8c46-31b030184b8c",
            "crm": {"revision": "b" * 40, "tree": "c" * 40},
        }
        self.base_policy, self.base_state = "e" * 64, "f" * 64
        self.common = {
            "daemon_mode": kernel.MODE,
            "attempt_id": self.preparation["attempt_id"],
            "crm_revision": self.preparation["crm"]["revision"],
            "crm_tree": self.preparation["crm"]["tree"],
            "manager_revision": kernel.MANAGER,
            "guard_v2_sha256": kernel.GUARD_SHA,
            "consumer_contract_sha256": kernel.CONTRACT_SHA,
            "bootstrap_package_manifest_sha256": "1" * 64,
            "cache_view_package_manifest_sha256": "2" * 64,
        }
        self.primary = self.put(
            "primary", {"fixture": "synthetic capability; no production observation"}
        )
        self.bootstrap_manifest = self.put(
            "bootstrap-manifest",
            {"schema": "autostop.admin-prepared-package.v1", "files": [], "synthetic": True},
        )
        self.cache_manifest = self.put(
            "cache-manifest", {"synthetic": True, "component": "cache-view"}
        )
        self.common["bootstrap_package_manifest_sha256"] = self.bootstrap_manifest["sha256"]
        self.common["cache_view_package_manifest_sha256"] = self.cache_manifest["sha256"]
        self.instruction = self.put(
            "instruction", {"instruction_revision": boundary.INSTRUCTION_REVISION}
        )
        self.eligible = {
            **self.common,
            "schema": "autostop.j1.kernel-bootstrap-eligibility.v1",
            "baseline_ref": self.primary,
            "instruction_ref": self.instruction,
            "history_recovery_ref": self.primary,
            "isolated_native_capability_refs": [self.primary],
            "coordinator": "synthetic-test-only",
        }
        self.eligible_ref = self.put("eligibility", self.eligible)
        self.daemon = {
            "pid": 1280,
            "start_ticks": 321,
            "argv_sha256": "3" * 64,
            "environment_sha256": "4" * 64,
            "mount_namespace": "mnt:[123]",
        }
        self.ready = {
            "schema": "autostop.j1.kernel-bootstrap-state.v1",
            "policy_sha256": "5" * 64,
            "boot_id": "f2e24821-7214-4100-a935-0946a635c8c9",
            "generation": "4e677b6c-9263-40c1-85be-bf29bf73cd7a",
            "phase": "ready",
            "daemon": self.daemon,
            "previous_generation": None,
            "terminal_exit_code": None,
        }
        self.closure = self.put(
            "closure",
            {
                "accepted": True,
                "closed_writer_classes": kernel.WRITER_CLASSES,
                "files": [self.primary],
            },
        )
        self.native = {
            **self.common,
            "schema": "autostop.j1.kernel-bootstrap-native-acceptance.v1",
            "eligibility_ref": self.eligible_ref,
            "bootstrap_policy_sha256": "5" * 64,
            "base_policy_sha256": self.base_policy,
            "base_armed_state_sha256": self.base_state,
            "boot_id": self.ready["boot_id"],
            "generation": self.ready["generation"],
            "ready_state_ref": self.put("initial-ready-snapshot", self.ready),
            "daemon": self.daemon,
            "preservation_ref": self.primary,
            "global_writer_closure_ref": self.closure,
            "primary_native_evidence_refs": [
                self.primary,
                self.bootstrap_manifest,
                self.cache_manifest,
            ],
            "reviewer": "synthetic-test-only",
            "accepted": True,
        }
        self.wrapper = {
            "schema": "autostop.j1.kernel-bootstrap-preservation.v1",
            "daemon_mode": kernel.MODE,
            "attempt_id": self.preparation["attempt_id"],
            "eligibility_ref": self.eligible_ref,
            "native_acceptance_ref": self.put("native", self.native),
            "start_or_restart_performed": True,
            "preserved_thread_ref": self.primary,
            "preserved_instruction_ref": self.instruction,
            "preserved_history_ref": self.primary,
        }

    def put(self, name, value):
        path = self.root / (name + ".json")
        path.write_bytes((json.dumps(value, sort_keys=True) + "\n").encode())
        return {"path": str(path), "sha256": kernel.digest(path.read_bytes())}

    def validate(self):
        return kernel.native_acceptance(
            self.put("preservation", self.wrapper),
            self.eligible_ref,
            self.preparation,
            self.root,
            self.base_policy,
            self.base_state,
        )


class KernelProofTests(KernelFixture):
    def test_implementation_matches_exact_normative_schema_and_phase_split(self):
        path = Path(__file__).resolve().parents[1] / "docs/j1_kernel_release_interface_v1.json"
        self.assertEqual(kernel.DECISION_SHA, kernel.digest(path.read_bytes()))
        contract = kernel.decode(path.read_bytes())
        self.assertEqual(kernel.MODE, contract["daemon_mode"])
        self.assertEqual(kernel.ELIGIBILITY_FIELDS, set(contract["eligibility"]["required_fields"]))
        self.assertEqual(
            kernel.NATIVE_FIELDS, set(contract["initial_native_acceptance"]["required_fields"])
        )
        self.assertEqual(
            kernel.PRESERVATION_FIELDS,
            set(contract["initial_native_acceptance"]["bootstrap_preservation_fields"]),
        )
        self.assertIs(
            True, contract["phase_definition"]["no_six_dispatch_before_post_arm_native_acceptance"]
        )
        self.assertIs(False, contract["native_or_production_ready"])

    def test_leaf_native_log_is_bounded_hash_checked_without_json_requirement(self):
        path = self.root / "actual-native.log"
        path.write_bytes(b"Native evidence log fixture; not a production PASS.\n")
        ref = {"path": str(path), "sha256": kernel.digest(path.read_bytes())}
        self.assertEqual([path.read_bytes()], kernel.references([ref], self.root))

    def test_exact_cold_receipt_is_distinct_from_warm(self):
        self.assertEqual(self.native, self.validate())
        self.assertIs(True, self.wrapper["start_or_restart_performed"])
        self.wrapper["start_or_restart_performed"] = False
        with self.assertRaisesRegex(kernel.KernelError, "actual_cold_start"):
            self.validate()

    def test_static_approval_or_ready_cannot_replace_native_acceptance(self):
        for value in (
            {"schema": "autostop.j1.kernel-bootstrap-static-approval.v1", "static_approved": True},
            self.ready,
            {**self.native, "accepted": False},
        ):
            with self.subTest(schema=value["schema"]):
                self.wrapper["native_acceptance_ref"] = self.put("native", value)
                with self.assertRaises(kernel.KernelError):
                    self.validate()

    def test_missing_unknown_and_future_eligibility_fields_deny(self):
        for name in (
            *kernel.ELIGIBILITY_FIELDS,
            "future_bootstrap_READY",
            "future_armed_state_sha256",
        ):
            value = copy.deepcopy(self.eligible)
            if name in value:
                del value[name]
            else:
                value[name] = True
            with self.subTest(field=name), self.assertRaises(kernel.KernelError):
                kernel.eligibility(self.put("bad-eligibility", value), self.preparation, self.root)

    def test_native_context_and_bool_as_pid_deny(self):
        for name, value in (
            ("attempt_id", "other"),
            ("crm_tree", "d" * 40),
            ("daemon_mode", "accepted_warm"),
            ("base_armed_state_sha256", "0" * 64),
            ("reviewer", ""),
            ("daemon", {**self.daemon, "pid": True}),
        ):
            with self.subTest(field=name):
                changed = {**self.native, name: value}
                self.wrapper["native_acceptance_ref"] = self.put("native", changed)
                with self.assertRaises(kernel.KernelError):
                    self.validate()

    def test_global_closure_cannot_be_inferred_from_ready(self):
        for closure in (
            {"accepted": True, "closed_writer_classes": ["native_units"], "files": [self.primary]},
            {
                "accepted": False,
                "closed_writer_classes": kernel.WRITER_CLASSES,
                "files": [self.primary],
            },
            {"accepted": True, "closed_writer_classes": kernel.WRITER_CLASSES, "files": []},
        ):
            self.native["global_writer_closure_ref"] = self.put("closure", closure)
            self.wrapper["native_acceptance_ref"] = self.put("native", self.native)
            with self.assertRaises(kernel.KernelError):
                self.validate()

    def test_initial_ready_pending_or_other_generation_deny(self):
        for name, value in (
            ("phase", "pending"),
            ("generation", "62df2263-63e8-4386-8f0c-f6e93d677f92"),
        ):
            changed = {**self.ready, name: value}
            self.native["ready_state_ref"] = self.put("initial-ready-snapshot", changed)
            self.wrapper["native_acceptance_ref"] = self.put("native", self.native)
            with self.assertRaisesRegex(kernel.KernelError, "initial_ready_snapshot_mismatch"):
                self.validate()

    def test_reference_outside_attempt_and_changed_bytes_deny(self):
        with self.assertRaisesRegex(kernel.KernelError, "outside_attempt"):
            kernel.reference(self.primary, self.root / "other")
        Path(self.primary["path"]).write_bytes(b"{}")
        with self.assertRaisesRegex(kernel.KernelError, "hash_mismatch"):
            kernel.reference(self.primary, self.root)

    def test_bounded_duplicate_and_nonfinite_json_deny(self):
        path = self.root / "invalid.json"
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b" " * (kernel.LIMIT + 1)):
            path.write_bytes(raw)
            with self.subTest(raw_length=len(raw)), self.assertRaises(kernel.KernelError):
                kernel.reference({"path": str(path), "sha256": kernel.digest(raw)}, self.root)

    def test_code_drift_denies_before_import(self):
        install = self.root / "installed"
        install.mkdir()
        entries = []
        for name in ("bootstrap.py", "rpc.py", "render.py"):
            raw = b"# synthetic package identity; never a production approval\n"
            (install / name).write_bytes(raw)
            entries.append({"path": name, "bytes": len(raw), "sha256": kernel.digest(raw)})
        manifest_ref = self.put(
            "package-manifest", {"schema": "autostop.admin-prepared-package.v1", "files": entries}
        )
        native = {
            **self.native,
            "bootstrap_package_manifest_sha256": manifest_ref["sha256"],
            "primary_native_evidence_refs": [manifest_ref],
        }
        (install / "bootstrap.py").write_bytes(b"raise AssertionError('must never execute')\n")
        with patch.object(kernel, "INSTALL", install), patch.object(kernel, "load_module") as load:
            with self.assertRaisesRegex(kernel.KernelError, "bootstrap_code_changed"):
                kernel.producer(native, self.root)
            load.assert_not_called()

    def test_fixed_gate_orders_dependencies_and_never_embeds_receipt_commands(self):
        raw = kernel.gate_bytes("a" * 64).decode()
        self.assertIn(
            "Requires=autostop-j1-cache-view.service\nWants=autostop-codex-start.service", raw
        )
        self.assertIn("After=autostop-j1-cache-view.service autostop-codex-start.service", raw)
        self.assertIn("ExecCondition=+/usr/bin/python3 -I -S", raw)
        self.assertIn("BindsTo=autostop-codex-start.service", raw)
        self.assertIn(
            "Requires=\nRequires=",
            kernel.gate_bytes("a" * 64, unit="autostop-codex-wake.service").decode(),
        )
        self.assertIn(
            "BindsTo=autostop-codex-start.service", kernel.recovery_bytes("a" * 64).decode()
        )
        with self.assertRaises(kernel.KernelError):
            kernel.gate_bytes("x; touch /tmp/unowned")


class KernelCurrentObservationTests(KernelFixture):
    def setUp(self):
        super().setUp()
        self.current = copy.deepcopy(self.ready)
        self.unit = b"Type=notify\nActiveState=active\nSubState=running\nMainPID=1279\n"
        self.module = SimpleNamespace(
            Deny=type("SyntheticDeny", (ValueError,), {}),
            read_state=Mock(
                side_effect=lambda _: (
                    copy.deepcopy(self.current),
                    json.dumps(self.current).encode(),
                )
            ),
            actual_unit=Mock(),
            fixed_command=Mock(side_effect=lambda *a, **kw: self.unit),
            cache_check=Mock(),
            ready_daemon=Mock(side_effect=lambda *a, **kw: (self.current["daemon"], 99)),
            base_arm=Mock(),
        )
        self.boot = self.root / "boot-id"
        self.boot.write_text(self.current["boot_id"])
        patches = [
            patch.object(kernel, "producer", return_value=self.module),
            patch.object(kernel, "policy_inputs", return_value=({}, {})),
            patch.object(kernel, "cache_view", return_value=b"synthetic current cache view"),
            patch.object(kernel, "main_process", return_value=(1279, 320, "mnt:[123]")),
            patch.object(kernel, "BOOT_ID", self.boot),
            patch.object(kernel.sys, "platform", "linux"),
            patch.object(kernel.os, "geteuid", return_value=0, create=True),
            patch.object(kernel.os, "close"),
            patch.object(kernel.select, "select", return_value=([], [], [])),
            patch.object(kernel, "read_file", return_value=b"policy"),
        ]
        self.native["bootstrap_policy_sha256"] = kernel.digest(b"policy")
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def observe(self, initial=True):
        return kernel.observe(self.native, self.preparation, self.root, initial=initial)

    def test_current_ready_requires_loaded_notify_main_and_rpc_before_second_state_read(self):
        self.assertEqual(self.current, self.observe())
        self.assertEqual(2, self.module.read_state.call_count)
        self.assertEqual(2, self.module.actual_unit.call_count)
        self.module.ready_daemon.assert_called_once()
        self.module.cache_check.assert_called_once_with({}, Path("/proc/1/root"))

    def test_stale_boot_or_pending_ready_always_deny(self):
        for change in ({"boot_id": "f2e24821-7214-4100-a935-0946a635c8c8"}, {"phase": "pending"}):
            self.current = {**self.ready, **change}
            with self.subTest(change=change), self.assertRaises(kernel.KernelError):
                self.observe(initial=False)

    def test_later_new_generation_is_observed_without_overwriting_initial_receipt(self):
        initial_bytes = json.dumps(self.native, sort_keys=True)
        self.current.update(
            generation="62df2263-63e8-4386-8f0c-f6e93d677f92", daemon={**self.daemon, "pid": 9999}
        )
        with self.assertRaisesRegex(kernel.KernelError, "initial_native_generation_changed"):
            self.observe(initial=True)
        self.assertEqual(9999, self.observe(initial=False)["daemon"]["pid"])
        self.assertEqual(initial_bytes, json.dumps(self.native, sort_keys=True))

    def test_active_exited_zero_pid_and_unexpected_type_deny_before_rpc(self):
        for raw in (
            self.unit.replace(b"running", b"exited"),
            self.unit.replace(b"1279", b"0"),
            self.unit.replace(b"notify", b"simple"),
        ):
            self.unit = raw
            with self.assertRaisesRegex(kernel.KernelError, "bootstrap_notify_running_required"):
                self.observe()
        self.module.ready_daemon.assert_not_called()

    def test_ready_race_and_daemon_identity_drift_deny(self):
        raw = json.dumps(self.current).encode()
        self.module.read_state.side_effect = [(self.current, raw), (self.current, raw + b" ")]
        with self.assertRaisesRegex(kernel.KernelError, "ready_generation_raced"):
            self.observe()
        self.module.read_state.side_effect = lambda _: (self.current, raw)
        self.module.ready_daemon.side_effect = None
        self.module.ready_daemon.return_value = ({**self.daemon, "start_ticks": 322}, 99)
        with self.assertRaisesRegex(kernel.KernelError, "current_daemon_changed"):
            self.observe()


class KernelDutyBindingTests(KernelFixture):
    def test_fixed_helper_is_hash_bound_before_import_and_uses_fresh_mode_after_commit(self):
        helper = self.root / "kernel.py"
        helper.write_bytes(
            b"# Synthetic fixture module; loader substituted, never a native proof.\n"
        )
        record = {"path": str(helper), "sha256": kernel.digest(helper.read_bytes())}
        handoff = {"files": [record], "pre_effect_lifecycle": self.primary}
        binding = {"unit_files": [record]}
        lifecycle_ref = self.put("lifecycle", {"synthetic": True})
        lifecycle = {
            "daemon_mode": kernel.MODE,
            "pre_effect_acceptance": self.primary,
            "bootstrap_preservation_receipt": self.primary,
            "writer_admission_closure_receipt": self.closure,
        }
        facade = SimpleNamespace(
            KernelError=kernel.KernelError,
            reference=Mock(
                side_effect=[
                    {
                        "daemon_mode": kernel.MODE,
                        "bootstrap_preservation_receipt": self.eligible_ref,
                    },
                    lifecycle,
                ]
                * 2
            ),
            native_acceptance=Mock(return_value={"global_writer_closure_ref": self.closure}),
            observe=Mock(),
        )
        specification = SimpleNamespace(loader=Mock())
        with (
            patch.object(duty, "KERNEL_CONSUMER", helper),
            patch.object(duty, "owner"),
            patch.object(
                duty.importlib.util, "spec_from_file_location", return_value=specification
            ),
            patch.object(duty.importlib.util, "module_from_spec", return_value=facade),
            patch.object(duty, "exists", side_effect=[False, True]),
        ):
            refs = {
                "lifecycle": lifecycle_ref,
                "policy": {"sha256": self.base_policy},
                "state": {"sha256": self.base_state},
            }
            duty.kernel_ready(self.preparation, self.root, handoff, {}, refs, binding)
            duty.kernel_ready(self.preparation, self.root, handoff, {}, refs, binding)
            self.assertEqual(
                [True, False], [call.kwargs["initial"] for call in facade.observe.call_args_list]
            )
        helper.write_bytes(b"raise AssertionError('never import drifted helper')\n")
        with (
            patch.object(duty, "KERNEL_CONSUMER", helper),
            patch.object(duty, "owner"),
            patch.object(duty.importlib.util, "spec_from_file_location") as load,
        ):
            with self.assertRaisesRegex(duty.DutyError, "bound_file_changed"):
                duty.kernel_ready(self.preparation, self.root, handoff, {}, refs, binding)
            load.assert_not_called()


class KernelRecoveryTests(duty_tests.WorkDutyFixture):
    def setUp(self):
        super().setUp()
        self.seed_commit()
        self.docs["closure-acceptance"]["daemon_mode"] = kernel.MODE
        self.flush("closure-acceptance")
        self.docs["intent"]["closure_sha256"] = self.refs["closure-acceptance"]["sha256"]
        self.flush("intent")
        self.intent_hash = self.refs["intent"]["sha256"]
        duty.DROPIN.write_bytes(duty.dropin_bytes(self.intent_hash))
        self.docs["policy"]["bindings"][duty.WORK_UNIT]["unit_files"][2]["sha256"] = duty.digest(
            duty.DROPIN.read_bytes()
        )
        self.docs["handoff"]["closure_sha256"] = self.refs["closure-acceptance"]["sha256"]
        self.docs["handoff"]["desired_native"] = {unit: True for unit in duty.NATIVE_UNITS}
        self.docs["committed"]["intent_sha256"] = self.intent_hash
        self.rebind_authorization()
        # Native observation has its own contract tests and external acceptance.
        # All release files, commit bindings and real locks are retained here.
        observation = patch.object(duty, "kernel_ready")
        self.current = observation.start()
        self.addCleanup(observation.stop)
        commands = patch.object(duty.subprocess, "run")
        self.commands = commands.start()
        self.addCleanup(commands.stop)

        @contextmanager
        def lock(_path, _deadline):
            yield

        locking = patch.object(duty, "shared_lock", side_effect=lock)
        locking.start()
        self.addCleanup(locking.stop)

    def test_recovery_requests_only_exact_six_without_waiting_or_duty_mutation(self):
        with patch.object(duty, "local_request") as rpc:
            self.assertEqual("native_start_requested", duty.recover(self.intent_hash))
        rpc.assert_not_called()
        self.current.assert_called_once()
        argv = self.commands.call_args.args[0]
        self.assertEqual(
            ["/usr/bin/systemctl", "--no-block", "start", *sorted(duty.NATIVE_UNITS)], argv
        )
        self.assertGreater(self.commands.call_args.kwargs["timeout"], 0)
        self.assertEqual(1, self.commands.call_count)

    def test_uncommitted_release_never_recovers_after_reboot(self):
        duty.COMMIT.chmod(0o600)
        duty.COMMIT.unlink()
        self.assertEqual("closed_uncommitted", duty.recover(self.intent_hash))
        self.commands.assert_not_called()
        self.current.assert_not_called()

    def test_unknown_ready_or_pending_or_maintenance_never_starts_profiles(self):
        self.current.side_effect = duty.DutyError("current_ready_required")
        with self.assertRaisesRegex(duty.DutyError, "current_ready_required"):
            duty.recover(self.intent_hash)
        self.current.side_effect = None
        self.marker.write_bytes(b"maintenance")
        self.assertEqual("closed_maintenance", duty.recover(self.intent_hash))
        self.marker.unlink()
        duty.PENDING.write_bytes(b"unknown")
        with self.assertRaisesRegex(duty.DutyError, "commit_pending"):
            duty.recover(self.intent_hash)
        self.commands.assert_not_called()

    def test_malformed_target_map_cannot_inject_an_extra_unit(self):
        self.docs["handoff"]["desired_native"]["foreign.service"] = True
        self.rebind_authorization()
        with self.assertRaisesRegex(duty.DutyError, "native_targets_invalid"):
            duty.recover(self.intent_hash)
        self.commands.assert_not_called()


@unittest.skipUnless(
    duty_tests.NATIVE_LINUX, "Requires actual Linux flock and Unix peer credentials"
)
class KernelDutyRpcTests(duty_tests.WorkDutyNativeTests):
    """Real Work RPC; substitute only the separately native-gated kernel observer."""

    def setUp(self):
        super().setUp()
        fixture_expected = duty.expected_commit

        def verify(intent, intent_hash):
            self.dynamic_readiness()
            return fixture_expected(intent, intent_hash)

        self.dynamic_readiness = Mock()
        check = patch.object(duty, "expected_commit", side_effect=verify)
        check.start()
        self.addCleanup(check.stop)

    def test_current_boot_failure_never_sends_duty_rpc(self):
        duty.commit(self.intent_hash)
        self.dynamic_readiness.side_effect = duty.DutyError("current_boot_required")
        with self.bridge(True) as requests:
            self.deny_restore("current_boot_required")
        self.assertEqual([], requests)

    def test_current_ready_rechecked_after_deploy_lock_before_mutation(self):
        duty.commit(self.intent_hash)
        self.dynamic_readiness.side_effect = [None, duty.DutyError("ready_generation_raced")]
        with self.bridge(True) as requests:
            self.deny_restore("ready_generation_raced")
        self.assertEqual([], requests)

    def test_fresh_generation_allows_original_duty_rpc_without_new_commit(self):
        duty.commit(self.intent_hash)
        before = duty.COMMIT.read_bytes()
        with self.bridge(True) as requests:
            self.assertEqual("restored", duty.restore(self.intent_hash))
        self.assertEqual(["status", "duty_set", "status"], [item["operation"] for item in requests])
        self.assertEqual(before, duty.COMMIT.read_bytes())
