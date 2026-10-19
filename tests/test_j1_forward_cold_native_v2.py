from __future__ import annotations

import copy
import os
import sys
import tempfile
import types
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_kernel as kernel
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_cold_state as cold_state
from scripts import j1_forward_io as io
from tests import test_j1_forward_state as state_fixture


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
            patch.object(
                self.provider,
                "_runtime_kernel_admitted",
                side_effect=lambda value: nullcontext(opened),
            ),
            patch.object(kernel, "verify_preparation") as prep,
            patch.object(kernel, "verify_runtime_identity") as running,
        ):
            self.provider.verify_kernel(self.validated)
            self.assertEqual(prep.call_count, int(not opened))
            self.assertEqual(running.call_count, int(opened))

    def test_prestart_and_postcommit_observations_use_different_checked_boundaries(self):
        self.observe(False)
        self.observe(True)

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
        with (
            patch.object(native, "STATE", self.state),
            patch.object(epochs, "verify", return_value=self.fence),
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


if __name__ == "__main__":
    unittest.main()
