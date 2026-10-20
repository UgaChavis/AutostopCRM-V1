from __future__ import annotations

import copy
import os
import sys
import tempfile
import types
import unittest
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch

from scripts import cold_trace
from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_fixture as topology
from scripts import j1_forward_cold_kernel as kernel
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_cold_projection as projection
from scripts import j1_forward_cold_runtime_capsule as capsule
from scripts import j1_forward_cold_state as cold_state
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal
from tests import test_j1_forward_state as state_fixture


class ProductionVersionBoundaryTests(unittest.TestCase):
    def test_legacy_pending_and_kernel_deny_before_native_observation(self):
        provider = native.NativeProvider()
        with (
            patch.object(native.subprocess, "run") as command,
            patch.object(native.os, "pidfd_open", create=True) as pidfd,
        ):
            with self.assertRaisesRegex(io.Refusal, "production_pending_v2_required"):
                provider.verify_pending(
                    {"pending": {"schema": "autostop.j1.cold-pending-disposition.v1"}}
                )
            with self.assertRaisesRegex(io.Refusal, "production_kernel_v2_required"):
                provider.verify_kernel({"kernel": {"schema": "autostop.j1.cold-kernel.v1"}})
            command.assert_not_called()
            pidfd.assert_not_called()


@unittest.skipUnless(
    sys.platform.startswith("linux"), "Requires real Linux flock, AF_UNIX and SO_PEERCRED"
)
class ExecutableObservationTests(unittest.TestCase):
    def test_toolcache_fact_does_not_grant_source_custody(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "python"
            raw = b"a fact about bytes is not an accepted source"
            executable.write_bytes(raw)
            executable.chmod(0o775)
            executable.with_name("alias").hardlink_to(executable)
            # Hosted Python toolcaches need not be root-private source packages.
            self.assertEqual(native.executable_digest(executable), io.sha(raw))
            reference = {"path": str(executable), "sha256": io.sha(raw)}
            with patch.object(io, "protected_directory", return_value=executable.parent):
                with self.assertRaisesRegex(io.Refusal, "root_protected_regular_file"):
                    native.NativeProvider().read_opaque_reference(reference)

    def test_replaced_executable_during_measurement_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "python"
            executable.write_bytes(b"original")
            replacement = executable.with_name("replacement")
            replacement.write_bytes(b"original")
            actual_sha256 = native.hashlib.sha256

            class ReplacingDigest:
                def __init__(self):
                    self.digest = actual_sha256()

                def update(self, block):
                    self.digest.update(block)
                    replacement.replace(executable)

                def hexdigest(self):
                    return self.digest.hexdigest()

            with patch.object(native.hashlib, "sha256", ReplacingDigest):
                with self.assertRaisesRegex(io.Refusal, "native_executable_changed"):
                    native.executable_digest(executable)

    def test_actor_hashes_fresh_once_and_rechecks_the_executing_inode(self):
        expected = native.executable_digest(Path(os.readlink("/proc/self/exe")))
        with patch.object(
            native, "_executable_snapshot", wraps=native._executable_snapshot
        ) as read:
            measured = native.actor(os.getpid())
            self.assertEqual(read.call_count, 1)
            self.assertEqual(measured["pid"], os.getpid())
            self.assertEqual(measured["exe_ref"]["sha256"], expected)


class FingerprintProtocolTests(unittest.TestCase):
    def test_v2_records_preserve_raw_identity_and_cannot_become_positive_outcomes(self):
        binding = {key: str(uuid.uuid4()) for key in epochs.COMMON[:-1]}
        row = {
            "op_id": "effect/raw:id/unchanged",
            "fingerprint": {"kind": "observed_record_sha256", "sha256": "1" * 64},
            "outcome": "unknown",
            "retry": "forbidden",
            "evidence_refs": [],
        }
        pending = {"schema": "autostop.j1.cold-pending-disposition.v2", "requests": [row]}
        result = epochs.tombstones(binding, pending, str(uuid.uuid4()))
        self.assertEqual(result["schema"], "autostop.j1.cold-dispatch-tombstones.v2")
        self.assertEqual(result["rows"][0]["op_id"], row["op_id"])
        self.assertEqual(result["rows"][0]["fingerprint"], row["fingerprint"])
        self.assertNotIn("payload_sha256", result["rows"][0])
        for outcome in ("applied_readback", "not_applied_terminal"):
            row["outcome"] = outcome
            with self.assertRaisesRegex(io.Refusal, "not_positive_outcome"):
                epochs.tombstones(binding, pending, str(uuid.uuid4()))

    def test_saved_inventory_cannot_replace_the_independent_v2_catalog(self):
        provider = native.NativeProvider()
        binding = {"fixture": "binding"}
        checkpoint = {"path": "/private/source-checkpoint.json", "sha256": "1" * 64}
        old_crm = {"path": "/private/old-crm.json", "sha256": "2" * 64}
        request = {
            "op_id": "real-old-text-id",
            "fingerprint": {"kind": "observed_record_sha256", "sha256": "3" * 64},
        }
        inventory = {
            "schema": "autostop.j1.cold-pending-inventory.v2",
            "scope": "production_native",
            "binding": binding,
            "coverage": "source_defined_durable_catalogs_with_retired_unenumerated_ram",
            "payload_semantics": "observed_record_fingerprint_unknown_only",
            "source_checkpoint_ref": checkpoint,
            "old_crm_ingress_ref": old_crm,
            "rows": [{"request": request, "evidence_refs": [checkpoint]}],
        }
        row = {**request, "outcome": "unknown", "retry": "forbidden", "evidence_refs": [checkpoint]}
        validated = {
            "package": {"binding": binding},
            "pending": {
                "schema": "autostop.j1.cold-pending-disposition.v2",
                "inventory_ref": checkpoint,
                "requests": [row],
            },
        }
        measured = {
            "old_crm_ingress_ref": old_crm,
            "payload_semantics": inventory["payload_semantics"],
            "ram_disposition": "unenumerated_retired_without_replay",
            "rows": [{**request, "outcome": "unknown", "retry": "forbidden"}],
        }
        module = types.ModuleType("scripts.j1_forward_cold_catalog")
        calls = []

        def verify(reference, *, provider, binding):
            calls.append(reference)
            return measured

        module.verify_checkpoint = verify
        with (
            patch.dict(sys.modules, {module.__name__: module}),
            patch("scripts.j1_forward_cold_catalog", module, create=True),
            patch.object(provider, "read_reference", return_value=io.canonical(inventory)),
        ):
            provider.verify_pending(validated)
            self.assertEqual(calls, [checkpoint])
            measured["rows"] = []
            with self.assertRaisesRegex(io.Refusal, "inventory_incomplete"):
                provider.verify_pending(validated)


class KernelV2BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.provider = native.NativeProvider()
        self.binding = {"attempt_id": str(uuid.uuid4()), "generation": str(uuid.uuid4())}
        self.actor = {"pid": 12, "cgroup": "0::/private-kernel"}
        self.supervisor = {"pid": 11, "cgroup": "0::/private-kernel"}
        self.raw = {}

        def ref(name, value):
            raw = io.canonical(value)
            path = "/isolated/" + name + ".json"
            self.raw[path] = raw
            return {"path": path, "sha256": io.sha(raw)}

        source = native.INSTALL / "j1_forward_cold_kernel_supervisor.py"
        rpc = native.INSTALL / "j1_forward_cold_kernel_rpc.py"
        self.policy = {
            "source_ref": {
                "path": str(source),
                "sha256": io.sha(Path(native.__file__).with_name(source.name).read_bytes()),
            },
            "rpc_ref": {
                "path": str(rpc),
                "sha256": io.sha(Path(native.__file__).with_name(rpc.name).read_bytes()),
            },
            "interpreter_ref": {"path": "/accepted/physical-python", "sha256": "1" * 64},
        }
        self.policy["argv"] = [
            "/accepted/physical-python",
            "-I",
            "-S",
            "-B",
            str(source),
            "--attempt",
            self.binding["attempt_id"],
        ]
        self.unit = {
            "Type": "notify",
            "Restart": "no",
            "RuntimeMaxUSec": "infinity",
            "KillMode": "control-group",
            "NotifyAccess": "main",
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": "11",
            "ControlGroup": "/private-kernel",
            "DropInPaths": "",
            **{
                key: ""
                for key in (
                    "ExecCondition",
                    "ExecStartPre",
                    "ExecStartPost",
                    "ExecStop",
                    "ExecStopPost",
                    "ExecReload",
                )
            },
        }
        self.unit["ExecStart"] = (
            "{ path=/accepted/physical-python ; argv[]="
            + " ".join(self.policy["argv"])
            + " ; ignore_errors=no ; }"
        )
        self.policy["unit_ref"] = ref("unit", {"properties": copy.deepcopy(self.unit)})
        prep, empty = ref("preparation", {}), ref("owned-thread", {"thread_id": str(uuid.uuid4())})
        self.kernel = {
            "schema": "autostop.j1.cold-kernel.v2",
            "unit": native.kernel_unit(self.binding["generation"]),
            "actor": self.actor,
            "supervisor_actor": self.supervisor,
            "preparation_ref": prep,
            "empty_thread_ref": empty,
        }
        self.kernel["ready_ref"] = ref(
            "ready",
            {
                "schema": "autostop.j1.cold-kernel-ready.v2",
                "binding": self.binding,
                "phase": "ready",
                "actor": self.actor,
                "supervisor_actor": self.supervisor,
                "preparation_ref": prep,
                "empty_thread_ref": empty,
                "thread_id": io.document(self.raw[empty["path"]])["thread_id"],
            },
        )
        self.validated = {
            "kernel": self.kernel,
            "kernel_policy": self.policy,
            "package": {"binding": self.binding},
        }

    def observe(self, opened):
        with (
            patch.object(self.provider, "kernel_profile", return_value=self.policy),
            patch.object(
                self.provider, "systemd", side_effect=lambda unit: copy.deepcopy(self.unit)
            ),
            patch.object(
                self.provider, "read_reference", side_effect=lambda ref: self.raw[ref["path"]]
            ),
            patch.object(self.provider, "_verify_kernel_cache"),
            patch.object(self.provider, "_verify_kernel_libraries") as libraries,
            patch.object(
                self.provider,
                "_runtime_kernel_admitted",
                side_effect=lambda value: nullcontext(opened),
            ),
            patch.object(kernel, "verify_preparation") as prep,
            patch.object(kernel, "verify_runtime_identity") as running,
        ):
            self.provider.verify_kernel(self.validated)
            libraries.assert_called_once_with(self.validated)
            self.assertEqual(prep.call_count, int(not opened))
            self.assertEqual(running.call_count, int(opened))

    def test_prestart_and_postcommit_observations_use_different_checked_boundaries(self):
        self.observe(False)
        self.observe(True)

    def test_both_kernel_processes_use_the_existing_library_observer(self):
        from scripts import j1_forward_cold_lifecycle as lifecycle

        reference = {"path": "/isolated/kernel-policy.json", "sha256": "2" * 64}
        self.kernel["policy_ref"] = reference
        appserver = {"path": "/accepted/codex", "sha256": "3" * 64}
        self.validated["kernel_policy"]["appserver_ref"] = appserver
        self.kernel["actor"]["exe_ref"] = appserver
        with patch.object(lifecycle, "_loaded_libraries") as loaded:
            self.provider._verify_kernel_libraries(self.validated)
            self.assertEqual(
                [call.args for call in loaded.call_args_list],
                [
                    (11, {"policy_ref": reference}, self.provider),
                    (12, {"policy_ref": reference}, self.provider),
                ],
            )
            self.assertEqual(loaded.call_args_list[0].kwargs, {})
            self.assertEqual(loaded.call_args_list[1].kwargs, {"kernel_appserver_ref": appserver})
        with patch.object(
            lifecycle, "_loaded_libraries", side_effect=[None, io.Refusal("changed_child_library")]
        ):
            with self.assertRaisesRegex(io.Refusal, "changed_child_library"):
                self.provider._verify_kernel_libraries(self.validated)
        self.kernel["actor"]["exe_ref"] = {**appserver, "sha256": "4" * 64}
        with patch.object(lifecycle, "_loaded_libraries") as loaded:
            with self.assertRaisesRegex(io.Refusal, "mapped_appserver_binding"):
                self.provider._verify_kernel_libraries(self.validated)
            self.assertEqual(loaded.call_count, 1)

    def test_restart_timeout_wrong_pid_hook_and_stale_source_deny_before_delegate(self):
        for key, value in (
            ("Restart", "always"),
            ("RuntimeMaxUSec", "15min"),
            ("MainPID", "12"),
            ("ExecStartPre", "/unreviewed/helper"),
        ):
            with self.subTest(key=key):
                previous = self.unit[key]
                self.unit[key] = value
                with self.assertRaises(io.Refusal):
                    self.observe(False)
                self.unit[key] = previous
        self.policy["source_ref"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(io.Refusal, "reviewed_source"):
            self.observe(False)


@unittest.skipUnless(
    sys.platform.startswith("linux"), "Requires real Linux flock, AF_UNIX and SO_PEERCRED"
)
class KernelCommittedPhaseTests(unittest.TestCase):
    def setUp(self):
        fixture = state_fixture.ForwardStateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.binding = {key: str(uuid.uuid4()) for key in epochs.COMMON[:-1]}
        self.package = {"binding": self.binding}
        self.state = fixture.root / "cold/v1"
        path = self.state / "epochs" / self.binding["generation"] / "epoch.json"
        path.parent.mkdir(parents=True)
        self.package_ref = {
            "path": str(native.package_path(self.binding["attempt_id"])),
            "sha256": cold.digest(self.package),
        }
        self.epoch = {**epochs.common(self.binding), "package_ref": self.package_ref}
        path.write_bytes(io.canonical(self.epoch))
        self.epoch_ref = io.reference(path)
        lock = fixture.root / "dispatch.lock"
        lock.write_bytes(b"")
        info = lock.stat()
        self.fence = {
            "dispatch_lock_path": str(lock),
            "dispatch_lock_identity": {
                "device": info.st_dev,
                "inode": info.st_ino,
                "ctime_ns": info.st_ctime_ns,
            },
            "state_path": str(fixture.root / "dispatch-state.json"),
        }
        self.provider = native.NativeProvider()
        self.validated = {"package": self.package}

    def test_open_document_without_actual_head_denies_and_current_head_holds_shared_lock(self):
        fixture = self.fixture
        plan = fixture.plan(attempt=self.binding["attempt_id"])
        plan.update(
            schema=cold_state.PLAN_SCHEMA,
            scope=cold.PLAN_SCOPE,
            candidate_manager_sha="8" * 40,
            cold_authority_package_ref=self.package_ref,
            dispatch_epoch_ref=self.epoch_ref,
            cold_crm_spec_ref={"path": str(fixture.root / "spec.json"), "sha256": "9" * 64},
        )
        plan_ref = io.write_new(fixture.root / "plan.json", plan)
        store = cold_state.ColdStore(fixture.store_root, fixture.base)
        store.begin(plan)
        commit_ref = store.commit(plan, fixture.proofs())
        projection = {
            "schema": "autostop.j1.cold-dispatch-admission.v1",
            **epochs.common(self.binding),
            "source_refs": {"plan": plan_ref, "commit": commit_ref, "epoch": self.epoch_ref},
            "projected_refs": {},
        }
        projection_ref = io.write_new(fixture.root / "projection.json", projection)
        opened = {
            **epochs.closed_state(self.epoch),
            "state": "open",
            "plan_ref": plan_ref,
            "commit_ref": commit_ref,
            "admission_ref": projection_ref,
        }
        Path(self.fence["state_path"]).write_bytes(io.canonical(opened))
        entered, exited = [], []

        @contextmanager
        def historical_boundary(_store, reference):
            # This binding-only package isolates the native publication/lock
            # boundary. FixtureTypedHistoryTests validates the complete COLD
            # package, typed bytes and real historical memo below.
            self.assertEqual(reference, plan_ref)
            with self.assertRaises(BlockingIOError):
                os.close(epochs.locked_fence(self.fence, exclusive=True))
            entered.append(reference)
            try:
                yield
            finally:
                with self.assertRaises(BlockingIOError):
                    os.close(epochs.locked_fence(self.fence, exclusive=True))
                exited.append(reference)

        with (
            patch.object(native, "STATE", self.state),
            patch.object(epochs, "verify", return_value=self.fence),
            patch.object(cold_state.ColdStore, "historical_validation", historical_boundary),
        ):
            with self.provider._runtime_kernel_admitted(self.validated) as admitted:
                self.assertTrue(admitted)
                with self.assertRaises(BlockingIOError):
                    epochs.locked_fence(self.fence, exclusive=True)
            descriptor = epochs.locked_fence(self.fence, exclusive=True)
            try:
                with native.borrow_dispatch(self.provider, descriptor):
                    with self.provider._runtime_kernel_admitted(self.validated) as admitted:
                        self.assertTrue(admitted)
            finally:
                os.close(descriptor)
            before = store.head_path.read_bytes()
            store.head_path.write_bytes(
                io.canonical(
                    {
                        "schema": "autostop.j1.crm-forward-head.v1",
                        "sequence": 1,
                        "sha256": "0" * 64,
                    }
                )
            )
            with self.assertRaisesRegex(io.Refusal, "head_mismatch"):
                with self.provider._runtime_kernel_admitted(self.validated):
                    self.fail("OPEN cannot substitute for actual durable HEAD")
            store.head_path.write_bytes(before)
            with self.assertRaisesRegex(io.Refusal, "head_mismatch"):
                with self.provider._runtime_kernel_admitted(self.validated) as admitted:
                    self.assertTrue(admitted)
                    store.head_path.write_bytes(
                        io.canonical(
                            {
                                "schema": "autostop.j1.crm-forward-head.v1",
                                "sequence": 1,
                                "sha256": "0" * 64,
                            }
                        )
                    )
            store.head_path.write_bytes(before)
            descriptor = os.open(self.fence["dispatch_lock_path"], os.O_RDONLY)
            try:
                with self.assertRaisesRegex(io.Refusal, "not_owned"):
                    with native.borrow_dispatch(self.provider, descriptor):
                        self.fail("An open descriptor is not an owned exclusive lock")
            finally:
                os.close(descriptor)
        self.assertEqual(entered, [plan_ref] * 4)
        self.assertEqual(exited, entered)


@unittest.skipUnless(
    sys.platform.startswith("linux"), "Requires real Linux flock, AF_UNIX and SO_PEERCRED"
)
class FixtureTypedHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="typed-leaves-")
        self.addCleanup(temporary.cleanup)
        external = Path(temporary.name)

        class StorageFixture(rehearsal.TraceFixture):
            # Actual COLD history and v2 typed-byte validation, not a claim
            # that these inert ELF bytes are an accepted running appserver.
            def _pending(self, quarantine):
                reference = super()._pending(quarantine)
                document = io.document(self.provider.read_reference(reference))
                io.require(not document["requests"], "storage_fixture_empty_catalog")
                document["schema"] = "autostop.j1.cold-pending-disposition.v2"
                return self.publish("evidence/storage-pending-v2.json", document)

            def _produce_kernel(self, *arguments):
                source, policies, reference = super()._produce_kernel(*arguments)
                body = io.document(self.provider.read_reference(reference))
                roots = {name: str(external / name) for name in ("python", "venv")}
                for root in roots.values():
                    directory = Path(root) / "bin"
                    directory.mkdir(parents=True, mode=0o700)
                    leaf = directory / "python3.12"
                    leaf.write_bytes(b"\x7fELF inert private typed-reader fixture; never execute\n")
                    leaf.chmod(0o755)
                runtime = capsule.produce(
                    external / "runtime.tar.gz",
                    roots=roots,
                    binding=self.binding,
                    scope="native_fixture",
                )
                self.runtime_ref = self.publish("evidence/storage-runtime-index.json", runtime)
                self.runtime = runtime
                self.provider.external = {
                    **capsule.allowed_paths(runtime),
                    runtime["capsule_ref"]["path"]: runtime["capsule_ref"]["sha256"],
                }
                self.external_interpreter = self.provider.reference(
                    Path(roots["venv"]) / "bin/python3.12"
                )
                policy = io.document(self.provider.read_reference(body["policy_ref"]))
                rpc = self.publish("evidence/storage-rpc.py", b"# inert source fixture\n", raw=True)
                protocol = self.publish(
                    "evidence/storage-protocol.json",
                    self.doc(
                        "kernel-protocol-index",
                        files=sorted([policy["source_ref"], rpc], key=lambda row: row["path"]),
                    ),
                )
                policy.update(
                    schema="autostop.j1.cold-kernel-policy.v2",
                    interpreter_ref=self.external_interpreter,
                    runtime_index_ref=self.runtime_ref,
                    appserver_ref=self.external_interpreter,
                    appserver_argv=[self.external_interpreter["path"]],
                    appserver_environment_ref=policy["environment_ref"],
                    protocol_index_ref=protocol,
                    rpc_ref=rpc,
                )
                body.update(
                    schema="autostop.j1.cold-kernel.v2",
                    supervisor_actor=body["actor"],
                    policy_ref=self.publish("evidence/storage-kernel-policy-v2.json", policy),
                    preparation_ref=self.publish(
                        "evidence/storage-preparation.json", {"scope": "storage_only_fixture"}
                    ),
                    empty_thread_ref=self.publish(
                        "evidence/storage-empty-thread.json", {"scope": "storage_only_fixture"}
                    ),
                )
                return source, policies, self.publish("evidence/storage-kernel-v2.json", body)

        source_root = Path(__file__).resolve().parents[1]
        consumers = {
            str(path.relative_to(source_root)): path.read_bytes()
            for path in sorted((source_root / "scripts").glob("j1_forward_*.py"))
        }
        helper = "scripts/j1_manager_transition_source.py"
        consumers[helper] = (source_root / helper).read_bytes()
        # Actual non-root tests cannot read private common Git metadata. No
        # production Git/CI qualification is claimed by this storage fixture.
        with (
            patch.object(
                rehearsal,
                "checkout_identity",
                return_value={"revision": "1" * 40, "tree": "2" * 40},
            ),
            patch.object(rehearsal, "source_inventory", return_value=consumers),
        ):
            self.fixture = StorageFixture(candidates=True)
        self.addCleanup(self.fixture.close)
        # Kernel native truth has separate actual-kernel tests. Here the real
        # structural validator/State/crypto/flock and protected readers run.
        with patch.object(self.fixture.provider, "verify_kernel"):
            self.package_ref = self.fixture.produce()
        self.fixture.publish(
            "typed-fixture.json",
            {
                "schema": "autostop.j1.private-typed-cold-fixture.v1",
                "scope": "native_fixture",
                "binding": self.fixture.binding,
                "runtime_index_ref": self.fixture.runtime_ref,
                "external_refs": [self.fixture.runtime["capsule_ref"]],
            },
        )
        self.provider = self.fixture.provider = topology.FixtureProvider(self.fixture.root)
        custody = self.fixture.custody
        custody.__enter__()
        self.addCleanup(custody.__exit__, None, None, None)
        self.validated = cold.validate_package(
            self.package_ref, read_reference=self.provider.read_reference
        )
        self.assertEqual(self.validated["kernel"]["schema"], "autostop.j1.cold-kernel.v2")
        self.assertEqual(
            self.validated["pending"]["schema"], "autostop.j1.cold-pending-disposition.v2"
        )
        self.epoch = {
            "schema": epochs.SCHEMA,
            "scope": "native_fixture",
            **epochs.common(self.fixture.binding),
            "package_ref": self.package_ref,
            "runtime_bindings": self.fixture.runtime_bindings,
            "slots": {unit: lifecycle.slot_id(self.fixture.binding, unit) for unit in cold.ALLOWED},
            "journal_root": str(self.fixture.root / "execution/journals"),
        }
        epoch_ref = self.fixture.publish("execution/epoch.json", self.epoch)
        self.fence = epochs.verify(self.epoch, self.validated["package"], self.provider)
        signer = rehearsal.TemporaryCustodian(
            self.fixture.root / "signer", self.fixture.root / "reviewer/public.pem"
        )
        spec = self.fixture.publish("storage-spec.json", {"scope": "storage_only_fixture"})
        attempt = cold_trace.prepare_attempt(
            self.fixture, self.package_ref, epoch_ref, spec, signer
        )
        self.store = attempt["store"]
        self.assertEqual(attempt["envelope"]["schema"], cold_state.RECOVERY_SCHEMA)
        proofs = {
            name: signer.sign(
                name,
                attempt["plan_ref"],
                {"scope": "storage_only_fixture", "acceptance_claimed": False},
                attempt["directory"] / (name + "-proof.json"),
            )
            for name in rehearsal.state.PROOF_FIELDS
        }
        commit = self.store.commit_recovery(attempt["envelope"], proofs)
        descriptor = epochs.locked_fence(self.fence, exclusive=True)
        try:
            with patch.object(projection.native, "verify_package", return_value=self.validated):
                admission = projection.publish(
                    attempt["plan_ref"],
                    commit,
                    epoch_ref,
                    store=self.store,
                    provider=self.provider,
                    dispatch_fd=descriptor,
                )
            io.atomic_replace(
                self.fence["state_path"],
                {
                    **epochs.closed_state(self.epoch),
                    "state": "open",
                    "plan_ref": attempt["plan_ref"],
                    "commit_ref": commit,
                    "admission_ref": admission,
                },
            )
        finally:
            os.close(descriptor)

    def test_committed_fixture_head_uses_typed_external_readers_and_actual_shared_lock(self):
        leaf = self.fixture.external_interpreter
        capsule_ref = self.fixture.runtime["capsule_ref"]
        with self.assertRaisesRegex(io.Refusal, "fixture_reference_outside_root"):
            io.read_protected(leaf["path"])
        closed = []
        actual_close = cold_state._HistoricalValidation.close

        def close(operation):
            actual_close(operation)
            closed.append(operation)

        with (
            patch.object(
                self.provider, "read_opaque_reference", wraps=self.provider.read_opaque_reference
            ) as opaque,
            patch.object(
                self.provider, "read_runtime_capsule", wraps=self.provider.read_runtime_capsule
            ) as packed,
            patch.object(cold, "_decode_package", wraps=cold._decode_package) as decode,
            patch.object(cold_state._HistoricalValidation, "close", close),
        ):
            for expected in (1, 2):
                counts = opaque.call_count, packed.call_count
                with self.provider._runtime_kernel_admitted(self.validated) as admitted:
                    self.assertTrue(admitted)
                    with self.assertRaises(BlockingIOError):
                        os.close(epochs.locked_fence(self.fence, exclusive=True))
                # Entry/exit HEAD share one semantic decode, while a later
                # operation gets a fresh memo and actual typed source reads.
                self.assertEqual(decode.call_count, expected)
                self.assertGreater(opaque.call_count, counts[0])
                self.assertGreater(packed.call_count, counts[1])
                self.assertEqual(len(closed), expected)
                self.assertFalse(closed[-1].active)
                self.assertIsNone(closed[-1].memo)
                self.assertEqual(closed[-1].bytes, 0)
        self.assertGreaterEqual(sum(call.args == (leaf,) for call in opaque.call_args_list), 2)
        self.assertGreaterEqual(
            sum(call.args == (capsule_ref,) for call in packed.call_args_list), 2
        )

    def test_unlisted_writable_or_changed_typed_leaves_and_entry_exit_head_deny(self):
        unknown = Path(self.fixture.external_interpreter["path"]).with_name("unlisted")
        unknown.write_bytes(b"\x7fELF unlisted inert bytes")
        unknown.chmod(0o755)
        with self.assertRaisesRegex(io.Refusal, "fixture_reference_outside_root"):
            self.provider.read_opaque_reference(
                {"path": str(unknown), "sha256": io.sha(unknown.read_bytes())}
            )
        leaf = Path(self.fixture.external_interpreter["path"])
        leaf.chmod(0o777)
        try:
            actual_close = cold_state._HistoricalValidation.close
            with patch.object(
                cold_state._HistoricalValidation, "close", autospec=True, side_effect=actual_close
            ) as close:
                with self.assertRaisesRegex(io.Refusal, "fixture_external_protection"):
                    with self.provider._runtime_kernel_admitted(self.validated):
                        self.fail("Writable external runtime cannot admit committed history")
            close.assert_called_once()
            closed = close.call_args.args[0]
            self.assertFalse(closed.active)
            self.assertIsNone(closed.memo)
            self.assertEqual(closed.bytes, 0)
        finally:
            leaf.chmod(0o755)
        for path in (leaf, Path(self.fixture.runtime["capsule_ref"]["path"])):
            original = path.read_bytes()
            for at_exit in (False, True):
                with self.subTest(path=path.name, at_exit=at_exit):
                    try:
                        with self.assertRaises(io.Refusal):
                            if not at_exit:
                                path.write_bytes(original + b"changed")
                            with self.provider._runtime_kernel_admitted(self.validated) as admitted:
                                self.assertTrue(admitted)
                                path.write_bytes(original + b"changed")
                    finally:
                        path.write_bytes(original)
        original = self.store.head_path.read_bytes()
        for at_exit in (False, True):
            with self.subTest(head_at_exit=at_exit):
                try:
                    changed = {**io.document(original), "sha256": "f" * 64}
                    with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
                        if not at_exit:
                            io.atomic_replace(self.store.head_path, changed)
                        with self.provider._runtime_kernel_admitted(self.validated) as admitted:
                            self.assertTrue(admitted)
                            io.atomic_replace(self.store.head_path, changed)
                finally:
                    io.atomic_replace(self.store.head_path, io.document(original))
        with patch.object(cold, "_decode_package", wraps=cold._decode_package) as decode:
            with self.provider._runtime_kernel_admitted(self.validated) as admitted:
                self.assertTrue(admitted)
            self.assertEqual(decode.call_count, 1)


if __name__ == "__main__":
    unittest.main()
