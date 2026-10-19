"""Synthetic authority/producer boundaries only; never call native host APIs."""

from __future__ import annotations

import copy
import time
import unittest
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_backup as old_backup
from scripts import j1_forward_io as io
from scripts import j1_forward_mixed_authority as mixed
from scripts import j1_forward_mixed_backup as mixed_backup
from scripts import j1_forward_state as state
from scripts import j1_manager_successor_guard_v2 as guard
from scripts import j1_manager_successor_native_v2 as native
from scripts import j1_manager_successor_transition as transition
from tests import test_j1_forward_state as state_tests
from tests import test_j1_manager_successor_guard as guard_tests


class MixedPlanTests(unittest.TestCase):
    def setUp(self):
        self.fixture = state_tests.ForwardStateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = self.fixture.plan()
        self.plan.update(
            schema=mixed.PLAN_SCHEMA,
            scope=mixed.SCOPE,
            manager_transition_ref={
                "path": str(self.fixture.root / "transition.json"),
                "sha256": "a" * 64,
            },
            candidate_manager_sha=guard.REVISION,
        )

    def test_explicit_mixed_plan_cannot_enter_the_original_v1_validator(self):
        mixed.validate_plan(self.plan, self.fixture.base)
        with self.assertRaises(io.Refusal):
            state.validate_plan(self.plan, self.fixture.base)

    def test_wrong_manager_scope_or_extra_field_refuses(self):
        for field, value in (
            ("candidate_manager_sha", "0" * 40),
            ("scope", state.SCOPE),
            ("unexpected", True),
        ):
            plan = {**self.plan, field: value}
            with self.subTest(field=field), self.assertRaises(io.Refusal):
                mixed.validate_plan(plan, self.fixture.base)

    def test_real_state_transaction_preserves_original_schema_in_history_and_blocks_downgrade(self):
        old, first = self.fixture.committed()
        plan = {**self.plan, "sequence": 2, "predecessor_sha256": first["sha256"]}
        store = mixed.MixedStore(self.fixture.store_root, self.fixture.base)
        store.begin(plan)
        committed = store.commit(plan, self.fixture.proofs())
        store.finish(plan)
        self.assertEqual(store.head()["sha256"], committed["sha256"])
        self.assertEqual(store._records()[0][0]["plan"], old)
        with self.assertRaises(io.Refusal):
            store.begin(self.fixture.plan(sequence=3, predecessor=committed["sha256"]))
        with self.assertRaises(io.Refusal):
            self.fixture.store.head()

    def test_separate_backup_namespace_preserves_the_entire_v1_profile(self):
        original_profile, original_roots = old_backup.PROFILE, old_backup.ROOTS
        engine = mixed_backup.engine(guard.SOURCE_ROOT)
        self.assertEqual(old_backup.PROFILE, original_profile)
        self.assertEqual(old_backup.ROOTS, original_roots)
        self.assertEqual(engine.PROFILE, mixed_backup.PROFILE)
        self.assertEqual(engine.ROOTS[: len(original_roots)], original_roots)
        self.assertEqual(
            set(engine.ROOTS[len(original_roots) :]),
            {*mixed_backup.EXTRA_ROOTS, ("mcp_successor_source_v2", guard.SOURCE_ROOT)},
        )
        self.assertNotEqual(engine.profile_sha256(), old_backup.profile_sha256())

    def test_backup_source_is_exact_and_cannot_be_a_selector_or_traversal(self):
        for path in (guard.SELECTOR, guard.SOURCE_ROOT + "/../source", "/tmp/source"):
            with self.subTest(path=path), self.assertRaises(old_backup.BackupRefusal):
                mixed_backup.engine(path)

    def test_mixed_transport_and_restore_share_the_typed_profile_without_mutating_v1(self):
        from scripts import j1_forward_transport as old_transport

        previous = old_transport.PROFILE_SHA256
        transport = mixed_backup.transport_engine(guard.SOURCE_ROOT)
        self.assertEqual(
            transport.PROFILE_SHA256, mixed_backup.engine(guard.SOURCE_ROOT).profile_sha256()
        )
        self.assertIs(transport.core(), mixed_backup.engine(guard.SOURCE_ROOT))
        self.assertEqual(old_transport.PROFILE_SHA256, previous)


class VersionedConsumerTests(unittest.TestCase):
    def test_v2_reuses_a_separate_sealed_namespace_and_never_changes_v1(self):
        from scripts import j1_manager_successor_guard as original

        base = guard.load_guard()
        self.assertEqual(original.CONTRACT, "manager_mcp_only_preserved_warm_v1")
        self.assertEqual(base.CONTRACT, guard.CONTRACT)
        self.assertNotEqual(base.GUARD, original.GUARD)
        self.assertNotEqual(base.STATE_DIR, original.STATE_DIR)
        self.assertEqual(base.PINS, original.PINS)

    def test_changed_base_bytes_are_rejected_before_compilation(self):
        for loader in (guard.load_guard, native.load_native):
            with (
                self.subTest(loader=loader.__name__),
                self.assertRaisesRegex(ValueError, "base_.*changed"),
            ):
                loader(
                    read=lambda _path: b"raise AssertionError('must never compile')",
                    source_mode=False,
                )

    def test_native_overlay_adds_only_the_two_exact_denies_and_keeps_original_policy(self):
        base = native.load_native()
        seen, raw = [], {}
        for unit in base.DENIED_UNITS:
            raw[str(base.SYSTEMD / (unit + ".d") / native.DENY_NAME)] = native.DENY_BYTES
        policy = {"bindings": {"synthetic": {"unit_files": []}}}

        def verify(_self, value, _forward):
            seen.append(value)

        with (
            patch.object(native, "load_native", return_value=base),
            patch.object(base.NativeIO, "verify_preserved_authority", verify),
            patch.object(base.NativeIO, "read", side_effect=lambda path, *_: raw[path]),
        ):
            adapter = native.NativeIO(time.monotonic() + 10)
            adapter.verify_preserved_authority(policy, {})
            self.assertEqual(policy["bindings"]["synthetic"]["unit_files"], [])
            refs = seen[0]["bindings"]["synthetic"]["unit_files"]
            self.assertEqual({ref["path"] for ref in refs}, set(raw))
            raw[next(iter(raw))] = b"changed"
            with self.assertRaisesRegex(ValueError, "permanent_deny_changed"):
                adapter.verify_preserved_authority(policy, {})

    def test_loaded_records_require_exact_executable_no_ignored_error_or_lost_record(self):
        command = "/usr/bin/python3 -I -S -B /fixed/guard.py check"
        raw = f"{{ path=/usr/bin/python3 ; argv[]={command} ; ignore_errors=no ; }}"
        native.validate_command_records(raw, [command])
        for altered in (
            raw.replace("path=/usr/bin/python3", "path=/usr/bin/true"),
            raw.replace("ignore_errors=no", "ignore_errors=yes"),
            raw + raw,
            "garbage " + raw,
            raw.replace("ignore_errors=no", "ignore_errors=no ; ignore_errors=no"),
        ):
            with self.subTest(raw=altered), self.assertRaises(ValueError):
                native.validate_command_records(altered, [command])

    def test_mixed_loaded_mcp_requires_its_versioned_guard_plus_owned_false_only_when_closed(self):
        digest = "a" * 64
        prefix = f"/usr/bin/python3 -I -S -B {guard.GUARD}"
        args = f" --binding-sha256 {digest} --unit {guard.UNIT}"

        def record(command):
            return f"{{ path={command.split(' ', 1)[0]} ; argv[]={command} ; ignore_errors=no ; }}"

        context = {"Id": guard.UNIT, "NeedDaemonReload": "no"}
        authority = {"capsule": {"profiles": {guard.UNIT: {"unit_context": context}}}}
        fields = {
            **context,
            "ExecCondition": record(prefix + " check" + args),
            "ExecStart": record(prefix + " launch" + args),
            "ActiveState": "inactive",
            "SubState": "dead",
            "MainPID": "0",
        }
        mixed._mcp_loaded(fields, digest, authority, closed=False)
        with self.assertRaises(ValueError):
            mixed._mcp_loaded(fields, digest, authority, closed=True)
        fields["ExecCondition"] += " " + record("/usr/bin/false")
        mixed._mcp_loaded(fields, digest, authority, closed=True)
        with self.assertRaises(ValueError):
            mixed._mcp_loaded(fields, digest, authority, closed=False)
        fields["MainPID"] = "123"
        with self.assertRaisesRegex(ValueError, "still_running"):
            mixed._mcp_loaded(fields, digest, authority, closed=True)

    def test_original_native_baseline_is_never_relabelled_as_mixed(self):
        with patch.object(mixed, "observe") as observer:
            with self.assertRaisesRegex(io.Refusal, "mixed_baseline_schema"):
                mixed.verify_preserved({"schema": mixed.original.SCHEMA})
            observer.assert_not_called()


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.base = guard.load_guard()
        self.controller_files = {name: "9" * 64 for name in mixed.CONTROLLER_FILES}
        self.stack.enter_context(
            patch.object(mixed, "consumer_inventory", return_value=self.controller_files)
        )
        self.stack.enter_context(patch.object(guard_tests, "guard", self.base))
        self.fixture = guard_tests.SyntheticAuthority()
        self.fixture.baseline.update(
            runtime_inventories=[],
            deployment_lock="/synthetic/deploy.lock",
            deployment_lock_identity=[1, 2],
            maintenance_marker="/synthetic/marker",
            desired_inbound=False,
            manager_root="/old/manager",
            work_root="/old/work",
            legacy_crm_revision="a" * 40,
            desired_units={
                unit: {"active": unit in guard.UNITS, "enabled": True}
                for unit in mixed.original.ALL_UNITS
            },
            libraries={},
            cache_mounts={},
        )
        self.fixture.reseal()
        self.stack.enter_context(self.fixture.patches())
        self.stack.enter_context(patch.object(io, "referenced", self.referenced))
        self.stack.enter_context(
            patch.object(io, "read_protected", lambda path, *_: self.fixture.raw[str(path)])
        )
        self.binding_ref = self.fixture.ref(guard.BINDING)
        source_inputs = self.fixture.add(str(transition.SOURCE_INPUTS), {"synthetic": True})
        self.transition_ref = self.fixture.add(
            str(transition.INSTALL_RECEIPT),
            {
                "schema": mixed.INSTALL_SCHEMA,
                "contract": guard.CONTRACT,
                "attempt_id": guard_tests.ATTEMPT,
                "host": guard.HOST,
                "manager_revision": guard.REVISION,
                "binding_ref": self.binding_ref,
                "source_inputs_ref": source_inputs,
                "source_package_sha256": "a" * 64,
                "predecessor_ref": self.fixture.ref(guard.BASELINE),
                "native_acceptance": False,
                "activation_authorized": False,
                "candidate_may_have_written": True,
                "old_writer_allowed": False,
                "controller_files": self.controller_files,
            },
        )
        # The synthetic fixture's earlier permit is not the producer's output.
        for path in (
            guard.STATE,
            guard.ADMISSION,
            *(guard.STATE_DIR + "/proofs/" + kind + ".json" for kind in guard.PROOF_FACTS),
        ):
            self.fixture.raw.pop(path, None)
        baseline_ref = self.fixture.add(
            "/synthetic/attempt/baseline.json",
            {
                "review_key_ref": {"path": "/synthetic/public-key", "sha256": "b" * 64},
                "backup_profile_sha256": "c" * 64,
            },
        )
        now = datetime.now(UTC)
        self.plan = {
            "schema": mixed.PLAN_SCHEMA,
            "scope": mixed.SCOPE,
            "host": guard.HOST,
            "attempt_id": guard_tests.ATTEMPT,
            "sequence": 1,
            "predecessor_sha256": "d" * 64,
            "base_sha256": "d" * 64,
            "candidate": {
                "revision": "e" * 40,
                "tree": "f" * 40,
                "image": "sha256:" + "1" * 64,
                "consumer_sha256": "2" * 64,
            },
            "baseline_ref": baseline_ref,
            "created_at": (now - timedelta(seconds=1)).isoformat(),
            "deadline_utc": (now + timedelta(seconds=300)).isoformat(),
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
            "manager_transition_ref": self.transition_ref,
            "candidate_manager_sha": guard.REVISION,
        }
        self.plan_ref = self.fixture.add("/synthetic/attempt/plan.json", self.plan)
        self.ownership = {
            "attempt_id": self.plan["attempt_id"],
            "attempt_dir": "/synthetic/attempt",
            "boot_id": guard_tests.BOOT,
            "consumer_sha256": "2" * 64,
            "baseline_sha256": "3" * 64,
            "dropins": [],
            "bytes_sha256": "4" * 64,
            "process_scope_ref": {"path": "/synthetic/scope.json", "sha256": "5" * 64},
        }
        self.backup = self.fixture.add(
            "/synthetic/attempt/backup-evidence.json", {"manifest_sha256": "6" * 64}
        )
        self.fixture.add("/synthetic/attempt/control-closed.json", {"manifest_sha256": "7" * 64})
        self.offhost = self.fixture.add(
            "/synthetic/attempt/offhost_restore.json", {"synthetic_signed": True}
        )
        self.payload = {
            "plan_sha256": self.plan_ref["sha256"],
            "baseline_sha256": baseline_ref["sha256"],
            "actual_restore": True,
            "phase": "closed",
            "manifest_sha256": "6" * 64,
            "profile_sha256": "c" * 64,
            "control_graph_restored": True,
            "control_archive_manifest_sha256": "7" * 64,
            "control_restore_receipt_sha256": "8" * 64,
        }
        self.drain = self.fixture.add(
            "/synthetic/attempt/store-drain.json",
            {
                "attempt_id": self.plan["attempt_id"],
                "baseline_ref": baseline_ref,
                **{key: self.ownership[key] for key in ("consumer_sha256", "process_scope_ref")},
                "native_baseline_sha256": self.ownership["baseline_sha256"],
                "result": {
                    "ok": True,
                    "identity": {"revision": guard.OLD_REVISION},
                    "mode": "legacy_unsupported",
                    "stop_permitted": True,
                    "worker_drain_supported": False,
                    "store_workers_drained": None,
                },
            },
        )
        self.stack.enter_context(
            patch.object(
                transition.proofs,
                "verify_proof",
                side_effect=lambda *_a, **_k: copy.deepcopy(self.payload),
            )
        )
        self.stack.enter_context(
            patch.object(
                mixed,
                "observe",
                return_value={"immutable": {"profiles": {unit: {} for unit in guard.UNITS}}},
            )
        )
        self.backend = self.Backend(self)

    def referenced(self, ref):
        raw = self.fixture.raw[ref["path"]]
        io.require(io.sha(raw) == ref["sha256"], "synthetic_reference_changed")
        return raw

    class Backend:
        def __init__(self, test):
            self.test, self.events, self.held = test, [], False
            self.preserve_error = None
            self.publish_error = None

        @contextmanager
        def locked(self, *_args, **_kwargs):
            self.held = True
            self.events.append("lock")
            try:
                yield
            finally:
                self.held = False

        def publish(self, path, raw, **_kwargs):
            self.test.assertTrue(self.held)
            path = str(path)
            self.events.append(path)
            if self.publish_error == path:
                raise OSError("synthetic publication interruption")
            previous = self.test.fixture.raw.get(path)
            io.require(previous is None or previous == raw, "synthetic_existing_changed")
            return self.test.fixture.add(path, raw)

        def read_optional(self, path):
            return self.test.fixture.raw.get(str(path))

        def selector(self):
            self.test.assertTrue(self.held)
            self.events.append("selector")

        def reload(self, _deadline):
            self.test.assertTrue(self.held)
            self.events.append("reload")

        def closure(self, ownership):
            self.events.append("closure")
            return {key: ownership[key] for key in ("attempt_id", "boot_id", "process_scope_ref")}

        def preserve(self, *_args, **kwargs):
            self.events.append("preserve-running" if kwargs.get("running") else "preserve")
            if self.preserve_error:
                raise self.preserve_error

    def issue(self):
        return transition.issue_locked(
            self.transition_ref,
            self.plan_ref,
            self.ownership,
            self.backup,
            self.offhost,
            self.drain,
            deployment_fd=99,
            deadline=time.monotonic() + 30,
            backend=self.backend,
        )

    def test_issuer_publishes_seven_evidence_wrappers_admission_and_armed_last(self):
        result = self.issue()
        self.assertEqual(result, self.fixture.ref(guard.STATE))
        self.assertEqual(self.backend.events[-1], guard.STATE)
        self.assertLess(self.backend.events.index("closure"), self.backend.events.index("selector"))
        self.assertLess(
            self.backend.events.index("reload"), self.backend.events.index(guard.ADMISSION)
        )
        self.assertEqual(set(self.fixture.doc(guard.ADMISSION)["proofs"]), set(guard.PROOF_FACTS))
        self.assertFalse(self.fixture.doc(guard.ADMISSION)["native_acceptance"])
        self.assertFalse(self.fixture.doc(guard.STATE)["old_writer_allowed"])
        reader = self.base.Reader(self.fixture, time.monotonic() + 10)
        self.base._permit(
            self.fixture.binding, self.binding_ref["sha256"], {"boot_id": guard_tests.BOOT}, reader
        )

    def test_lost_reply_retry_keeps_the_same_immutable_authority_bytes(self):
        first = self.issue()
        previous = copy.deepcopy(self.fixture.raw)
        self.assertEqual(self.issue(), first)
        self.assertEqual(self.fixture.raw, previous)

    def test_missing_control_restore_or_different_archive_refuses_before_effects(self):
        for field, value in (
            ("control_graph_restored", False),
            ("control_archive_manifest_sha256", "0" * 64),
            ("control_restore_receipt_sha256", None),
            ("actual_restore", False),
        ):
            original = self.payload[field]
            self.payload[field] = value
            with self.subTest(field=field), self.assertRaises(io.Refusal):
                self.issue()
            self.assertEqual(self.backend.events, [])
            self.payload[field] = original

    def test_changed_store_scope_refuses_before_selector_or_arm(self):
        drain = self.fixture.doc(self.drain["path"])
        drain["process_scope_ref"]["sha256"] = "0" * 64
        self.drain = self.fixture.add(self.drain["path"], drain)
        with self.assertRaisesRegex(io.Refusal, "store_receipt_binding"):
            self.issue()
        self.assertEqual(self.backend.events, [])

    def test_wrong_attempt_cannot_issue_even_with_authenticated_offhost_fixture(self):
        self.ownership["attempt_id"] = "00000000-0000-4000-8000-000000000000"
        with self.assertRaisesRegex(io.Refusal, "plan_binding"):
            self.issue()
        self.assertEqual(self.backend.events, [])

    def test_interrupted_wrapper_publication_leaves_no_armed_state(self):
        self.backend.publish_error = guard.ADMISSION
        with self.assertRaises(OSError):
            self.issue()
        self.assertNotIn(guard.STATE, self.fixture.raw)

    def test_native_compatibility_stop_precedes_source_verify_and_staging(self):
        self.backend.preserve_error = io.Refusal("native_warm_actor_changed")
        with patch.object(transition.source, "verify") as verifier:
            with self.assertRaisesRegex(io.Refusal, "native_warm_actor_changed"):
                transition.stage_locked(
                    {"path": "/synthetic/inputs", "sha256": "0" * 64},
                    "/synthetic/package",
                    self.fixture.ref(guard.BASELINE),
                    self.ownership,
                    deployment_fd=99,
                    deadline=time.monotonic() + 30,
                    backend=self.backend,
                )
            verifier.assert_not_called()
        self.assertEqual(self.backend.events, ["preserve"])

    def test_inert_staging_derives_a_guard_consumable_binding_without_selector_dropin_or_arm(self):
        capsule = copy.deepcopy(self.fixture.capsule)
        capsule["contract"] = "manager_mcp_only_preserved_warm_v1"
        capsule["target"]["guard"] = "/usr/local/libexec/autostop-manager-mcp-successor/v1/guard.py"
        capsule["sha256"] = io.sha(
            io.canonical({key: value for key, value in capsule.items() if key != "sha256"})
        )
        index = self.fixture.index
        metadata = {
            "REVISION": (guard.REVISION + "\n").encode(),
            "MANIFEST.sha256": b"synthetic manifest",
            "MANIFEST.files0": b"synthetic inventory",
        }
        contents = {
            "autostop_manager/__init__.py": b"",
            "autostop_manager/mcp_server.py": b"synthetic",
            **metadata,
        }
        package = {
            "/synthetic/package/capsule.json": io.canonical(capsule),
            "/synthetic/package/source-index.json": io.canonical(index),
            "/synthetic/package/controller-source-index.json": b'{"synthetic":true}\n',
            **{"/synthetic/package/source/" + name: raw for name, raw in contents.items()},
        }
        inputs = self.fixture.add("/synthetic/source-inputs.json", {"synthetic": True})
        predecessor = self.fixture.ref(guard.BASELINE)
        for path in (
            guard.GUARD,
            guard.NATIVE,
            guard.BINDING,
            guard.CAPSULE,
            guard.INDEX,
            guard.RUNTIME,
            str(transition.INSTALL_RECEIPT),
        ):
            self.fixture.raw.pop(path, None)

        def read(path, *_args):
            return (
                self.fixture.raw[str(path)]
                if str(path) in self.fixture.raw
                else Path(path).read_bytes()
            )

        with (
            patch.object(transition.source, "verify") as verifier,
            patch.object(
                transition.source, "stable_read", side_effect=lambda path, *_: package[str(path)]
            ),
            patch.object(transition.authority, "_identity", return_value=[1, 9]),
            patch.object(io, "read_protected", side_effect=read),
        ):
            staged = transition.stage_locked(
                inputs,
                "/synthetic/package",
                predecessor,
                self.ownership,
                deployment_fd=99,
                deadline=time.monotonic() + 30,
                backend=self.backend,
            )
            self.backend.events.clear()
            self.assertEqual(
                transition.stage_locked(
                    inputs,
                    "/synthetic/package",
                    predecessor,
                    None,
                    deployment_fd=99,
                    deadline=time.monotonic() + 30,
                    backend=self.backend,
                    mode="inert_before_prepare",
                ),
                staged,
            )
        self.assertEqual(verifier.call_count, 2)
        verifier.assert_called_with(inputs["path"], inputs["sha256"], "/synthetic/package")
        self.assertNotIn("closure", self.backend.events)
        self.assertEqual(self.backend.events.count("preserve-running"), 3)
        receipt, binding = mixed.load_transition(staged)
        self.assertFalse(receipt["activation_authorized"])
        self.assertNotIn("selector", self.backend.events)
        self.assertNotIn(guard.DROPIN, self.fixture.raw)
        self.assertNotIn(guard.STATE, self.fixture.raw)
        reader = self.base.Reader(self.fixture, time.monotonic() + 10)
        derived, _index, _old, policy, *_rest = self.base._source_authority(binding, reader)
        runtime = self.base._profile(binding, derived, policy, reader)
        self.assertEqual(runtime["source_identity"], [1, 9])
        self.assertFalse(runtime["application_imported"])

    def test_static_target_projection_keeps_predecessor_immutable_and_is_not_acceptance(self):
        before = copy.deepcopy(self.fixture.baseline)
        projected = mixed.project_baseline(self.transition_ref, before)
        self.assertEqual(before, self.fixture.baseline)
        self.assertEqual(projected["schema"], mixed.SCHEMA)
        self.assertEqual(projected["profiles"][guard.UNIT]["manager_revision"], guard.REVISION)
        self.assertEqual(projected["warm_actors"], before["warm_actors"])
        self.assertEqual(projected["desired_units"], before["desired_units"])
        changed = {**before, "desired_inbound": True}
        with self.assertRaisesRegex(io.Refusal, "predecessor_digest"):
            mixed.project_baseline(self.transition_ref, changed)


if __name__ == "__main__":
    unittest.main()
