"""Actual recovery-storage crash only, without native acceptance or backup."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import cold_trace as trace
from scripts import j1_forward_cold_authority as authority
from scripts import j1_forward_cold_fixture as cold_fixture
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_state as transactions
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal
from tests.cold_v2_trace import _crash_after_history


def _retire_preserving_bytes(fixture, root):
    # Real owned-PID/FD cleanup runs unchanged. The outer test owns the private
    # directory and removes it after reopening the exact durable ColdStore.
    def retain(path):
        io.require(
            Path(path) == fixture.root and fixture.root.parent == root,
            "private_fault_cleanup_scope",
        )

    with patch.object(cold_fixture.shutil, "rmtree", side_effect=retain):
        fixture.close()


def _private_store(receipt):
    fixture_root = Path(receipt["fixture_root"])
    with patch.object(cold_fixture.tempfile, "gettempdir", return_value=str(fixture_root.parent)):
        provider = cold_fixture.FixtureProvider(fixture_root)

    class PrivateColdStore(transactions.ColdStore):
        def _read_reference(self, reference):
            return provider.read_reference(reference)

        def read_opaque_reference(self, reference):
            return provider.read_opaque_reference(reference)

    return PrivateColdStore(Path(receipt["state_root"]), receipt["base_sha256"]), provider


def _recover(attempt, signer):
    store, plan, envelope = (attempt[name] for name in ("store", "plan", "envelope"))
    pending = store.pending_path.read_bytes()
    proofs = {
        name: signer.sign(
            name,
            attempt["plan_ref"],
            {"scope": "storage_only_fixture", "acceptance_claimed": False},
            attempt["directory"] / (name + "-proof.json"),
        )
        for name in rehearsal.state.PROOF_FIELDS
    }
    _crash_after_history(store, plan, lambda: store.commit_recovery(envelope, proofs))
    try:
        store.head()
    except io.Refusal as error:
        io.require(str(error) == "state_head_mismatch", "wrong_crash_boundary")
    else:
        raise AssertionError("history must precede HEAD after real child death")
    committed = store.recover_recovery(envelope)
    io.require(store.pending_path.read_bytes() == pending, "old_pending_changed")
    return {
        "history_crash_exit": 77,
        "head_mismatch_before_recover": True,
        "commit": committed,
        "proofs": proofs,
        "changed_backup_ref": signer.sign(
            "backup",
            attempt["plan_ref"],
            {"scope": "different_storage_fixture"},
            attempt["directory"] / "changed-backup-proof.json",
        ),
        "old_pending_sha256": io.sha(pending),
    }


def _inherited_scope_denied(store):
    io.require(threading.active_count() == 1, "private_owner_fork_requires_one_thread")
    child = os.fork()
    if child == 0:
        try:
            store.head()
        except io.Refusal as error:
            os._exit(0 if str(error) == "cold_history_operation_owner_or_plan" else 78)
        os._exit(79)
    descriptor, reaped = os.pidfd_open(child), False
    try:
        io.require(bool(select.select([descriptor], [], [], 15)[0]), "private_owner_fork_timeout")
        waited, status = os.waitpid(child, 0)
        reaped = True
        io.require(
            waited == child and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0,
            "private_inherited_scope_not_denied",
        )
    finally:
        if not reaped:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(child, 0)
        os.close(descriptor)


def _finish(store, provider, receipt):
    controller = lifecycle.Controller(
        receipt["package_ref"],
        receipt["epoch_ref"],
        provider=provider,
        backend=lifecycle.FixtureBackend(SimpleNamespace(provider=provider)),
    )
    proofs = receipt["proofs"]
    # This is storage-only completed-stage setup, not an activation permit or
    # native acceptance. finish itself never starts a role or opens dispatch.
    io.write_new(
        controller.root / "release-admission.json",
        {
            "schema": "autostop.j1.cold-slot-release-admission.v1",
            "package_ref": receipt["package_ref"],
            "epoch_ref": receipt["epoch_ref"],
            "plan_ref": receipt["plan_ref"],
            "backup_ref": proofs["backup"],
            "offhost_ref": proofs["offhost_restore"],
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        },
    )
    io.write_new(controller.root / "commit.json", {"commit_ref": receipt["commit"]})
    io.write_new(controller.root / "opened.json", {"commit_ref": receipt["commit"]})
    finished = controller.finish(store=store)
    io.require(controller.finish(store=store) == finished, "finish_not_idempotent")
    io.require(store.pending() is None and store._active_recovery() is None, "unfinished_state")
    return finished


def _isolated_check(case, root):
    root = Path(root)
    source = Path(__file__).resolve().parents[1]
    consumers = {
        str(path.relative_to(source)): path.read_bytes()
        for path in sorted((source / "scripts").glob("j1_forward_*.py"))
    }
    helper = "scripts/j1_manager_transition_source.py"
    consumers[helper] = (source / helper).read_bytes()
    # Storage tests do not need private Git metadata or claim qualified source.
    # The actual script bytes remain bound into this explicitly fixture origin.
    with (
        patch.object(
            rehearsal, "checkout_identity", return_value={"revision": "1" * 40, "tree": "2" * 40}
        ),
        patch.object(rehearsal, "source_inventory", return_value=consumers),
    ):
        fixture = rehearsal.TraceFixture(candidates=True)
    try:
        package = fixture.produce()
        with fixture.custody:
            epoch = lifecycle.provision_epoch(
                package, fixture.runtime_bindings, provider=fixture.provider
            )
            spec = fixture._write(fixture.root / "crm-spec.json", {"scope": "storage_only"})
            signer = rehearsal.TemporaryCustodian(
                fixture.root / "signer", fixture.root / "reviewer/public.pem"
            )
            begin_counts = None
            if case == "begin":
                pending_before = fixture.store.pending_path.read_bytes()
                head_path = fixture.store.head_path
                head_before = head_path.read_bytes() if head_path.exists() else None
                with patch.object(
                    authority, "_decode_package", wraps=authority._decode_package
                ) as decode:
                    attempt = trace.prepare_attempt(fixture, package, epoch, spec, signer)
                    begin_counts = [decode.call_count]
                    io.require(
                        not hasattr(attempt["store"], "_historical_validation"),
                        "private_begin_retained_scope",
                    )
                    decode.reset_mock()
                    repeated = attempt["store"].begin_recovery(attempt["envelope"])
                    begin_counts.append(decode.call_count)
                io.require(repeated == attempt["recovery_ref"], "private_begin_not_idempotent")
                io.require(
                    fixture.store.pending_path.read_bytes() == pending_before
                    and (head_path.read_bytes() if head_path.exists() else None) == head_before,
                    "private_begin_changed_origin",
                )
                io.require(
                    not hasattr(attempt["store"], "_historical_validation"),
                    "private_repeat_begin_retained_scope",
                )
            else:
                attempt = trace.prepare_attempt(fixture, package, epoch, spec, signer)
            io.require(
                attempt["envelope"]["schema"] == transactions.RECOVERY_SCHEMA
                and attempt["plan"]["schema"] == transactions.PLAN_SCHEMA,
                "cold_fault_schema_required",
            )
            receipt = {
                "fixture_root": str(fixture.root),
                "state_root": str(attempt["store"].root),
                "base_sha256": fixture.base_sha256,
                "recovery_ref": attempt["recovery_ref"],
                "package_ref": package,
                "epoch_ref": epoch,
                "plan_ref": attempt["plan_ref"],
            }
            if case == "begin":
                receipt["begin_decode_counts"] = begin_counts
                receipt["original_pending_sha256"] = io.sha(pending_before)
                receipt["head_unchanged"] = True
                receipt["actual_uid"] = os.geteuid()
            elif case == "recover":
                receipt.update(_recover(attempt, signer))
            elif case == "owner":
                with attempt["store"].historical_validation(attempt["plan_ref"]):
                    attempt["store"]._envelope(attempt["envelope"])
                    _inherited_scope_denied(attempt["store"])
                receipt["inherited_scope_denied"] = True
            else:
                io.require(case in {"miss", "prepare"}, "unknown_private_case")
                if case == "miss":
                    try:
                        _crash_after_history(attempt["store"], attempt["plan"], lambda: None)
                    except io.Refusal as error:
                        io.require(
                            str(error) == "private_recovery_crash_missed", "wrong_missed_boundary"
                        )
                    else:
                        raise AssertionError("a missed write must fail")
                    receipt["missed_boundary_denied"] = True
    finally:
        _retire_preserving_bytes(fixture, root)
    with rehearsal.PrivateFilesystem(root):
        io.write_new(root / "result.json", receipt)


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
)
class _ColdFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cf-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def isolated(self, case):
        command = (
            "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
            "from tests.test_cold_v2_fault_contract import _isolated_check; "
            "_isolated_check(sys.argv[2], Path(sys.argv[3]))"
        )
        result = subprocess.run(
            [
                str(Path(sys.executable).resolve()),
                "-I",
                "-S",
                "-B",
                "-c",
                command,
                str(Path(__file__).resolve().parents[1]),
                case,
                str(self.root),
            ],
            capture_output=True,
            timeout=20,
            check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "TZ": "UTC",
                "TMPDIR": str(self.root),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-8192:])
        with rehearsal.PrivateFilesystem(self.root):
            return io.document(io.read_protected(self.root / "result.json"))


class RecoveryCrashContractTests(_ColdFixtureTests):
    def test_real_history_crash_reopens_and_recovers_same_recovery_transaction(self):
        receipt = self.isolated("recover")
        self.assertEqual(receipt["history_crash_exit"], 77)
        self.assertTrue(receipt["head_mismatch_before_recover"])
        with rehearsal.PrivateFilesystem(self.root):
            store, provider = _private_store(receipt)
            envelope = io.document(io.referenced(receipt["recovery_ref"]))
            self.assertEqual(envelope["schema"], transactions.RECOVERY_SCHEMA)
            self.assertEqual(store.head()["record"]["plan"]["schema"], transactions.PLAN_SCHEMA)
            self.assertEqual(store.commit_recovery(envelope, receipt["proofs"]), receipt["commit"])
            with self.assertRaisesRegex(io.Refusal, "state_committed_proofs_mismatch"):
                store.commit_recovery(
                    envelope, {**receipt["proofs"], "backup": receipt["changed_backup_ref"]}
                )
            self.assertEqual(io.sha(store.pending_path.read_bytes()), receipt["old_pending_sha256"])
            self.assertTrue(io.document(io.referenced(_finish(store, provider, receipt)))["ok"])
            self.assertEqual(len(list(store.history.iterdir())), 1)
            self.assertEqual(store.head()["sha256"], receipt["commit"]["sha256"])
            self.assertIsNone(store.pending())
            self.assertIsNone(store._active_recovery())
            with self.assertRaisesRegex(io.Refusal, "state_pending_mismatch"):
                store.begin_recovery(envelope)
            self.assertEqual(store.head()["sha256"], receipt["commit"]["sha256"])
            self.assertEqual(
                io.sha(io.referenced(store._archive_ref(envelope))), receipt["old_pending_sha256"]
            )
            for unit in authority.UNITS:
                self.assertEqual(
                    (Path(receipt["fixture_root"]) / "barriers" / unit).read_bytes(),
                    authority.DENY_BYTES,
                )

    def test_missing_history_boundary_is_a_failure(self):
        receipt = self.isolated("miss")
        self.assertTrue(receipt["missed_boundary_denied"])
        with rehearsal.PrivateFilesystem(self.root):
            store, _provider = _private_store(receipt)
            self.assertFalse(store.history.exists())
            self.assertEqual(store.active_plan()["plan"]["schema"], transactions.PLAN_SCHEMA)
            self.assertEqual(store._active_recovery()["schema"], transactions.RECOVERY_SCHEMA)
            self.assertFalse(store.pending()["old_writer_allowed"])


class HistoricalValidationTests(_ColdFixtureTests):
    def test_each_public_begin_decodes_once_and_preserves_original_state(self):
        receipt = self.isolated("begin")
        self.assertEqual(receipt["begin_decode_counts"], [1, 1])
        self.assertTrue(receipt["head_unchanged"])
        with rehearsal.PrivateFilesystem(self.root):
            store, _provider = _private_store(receipt)
            self.assertEqual(
                io.sha(store.pending_path.read_bytes()), receipt["original_pending_sha256"]
            )
            self.assertFalse(hasattr(store, "_historical_validation"))

    def test_each_public_finish_decodes_once_and_preserves_open_dispatch(self):
        receipt = self.isolated("recover")
        with rehearsal.PrivateFilesystem(self.root):
            store, provider = _private_store(receipt)
            dispatch = Path(receipt["fixture_root"]) / "execution/dispatch/state.json"
            opened = {
                **io.document(io.read_protected(dispatch)),
                "state": "open",
                "commit_ref": receipt["commit"],
                "plan_ref": receipt["plan_ref"],
                "admission_ref": receipt["plan_ref"],
            }
            io.atomic_replace(dispatch, opened)
            before = dispatch.read_bytes()
            with patch.object(
                authority, "_decode_package", wraps=authority._decode_package
            ) as decode:
                _finish(store, provider, receipt)  # Actual first and idempotent Controller.finish.
                self.assertEqual(decode.call_count, 2)
            self.assertEqual(dispatch.read_bytes(), before)
            self.assertIsNone(store.pending())
            self.assertFalse(hasattr(store, "_historical_validation"))

    def test_same_call_reuses_semantics_but_rereads_and_next_call_is_fresh(self):
        receipt = self.isolated("prepare")
        with rehearsal.PrivateFilesystem(self.root):
            store, _provider = _private_store(receipt)
            envelope = io.document(io.referenced(receipt["recovery_ref"]))
            with patch.object(
                authority, "_decode_package", wraps=authority._decode_package
            ) as decode:
                with store.historical_validation(receipt["plan_ref"]):
                    operation = store._historical_validation
                    with patch.object(
                        operation.memo._reader, "recheck", wraps=operation.memo._reader.recheck
                    ) as recheck:
                        store._envelope(envelope)
                        store._envelope(envelope)
                        self.assertGreaterEqual(recheck.call_count, 3)
                    self.assertEqual(decode.call_count, 1)
                    self.assertLessEqual(operation.bytes, transactions.MAX_HISTORICAL_BYTES)
                self.assertFalse(hasattr(store, "_historical_validation"))
                self.assertFalse(operation.active)
                self.assertIsNone(operation.memo)
                self.assertEqual(operation.bytes, 0)
                with self.assertRaisesRegex(io.Refusal, "cold_history_operation_owner_or_plan"):
                    operation.recheck()
                with store.historical_validation(receipt["plan_ref"]):
                    store._envelope(envelope)
                self.assertEqual(decode.call_count, 2)

    def test_source_tamper_on_reuse_or_exit_is_denied_and_scope_is_discarded(self):
        receipt = self.isolated("prepare")
        with rehearsal.PrivateFilesystem(self.root):
            store, _provider = _private_store(receipt)
            envelope = io.document(io.referenced(receipt["recovery_ref"]))
            source = Path(receipt["fixture_root"]) / "source/worker.py"
            original = source.read_bytes()
            for at_exit in (False, True):
                with self.subTest(at_exit=at_exit):
                    try:
                        with self.assertRaises(io.Refusal):
                            with store.historical_validation(receipt["plan_ref"]):
                                store._envelope(envelope)
                                source.write_bytes(original + b"\n# late source drift\n")
                                if not at_exit:
                                    store._envelope(envelope)
                        self.assertFalse(hasattr(store, "_historical_validation"))
                    finally:
                        source.write_bytes(original)
            with store.historical_validation(receipt["plan_ref"]):
                store._envelope(envelope)

    def test_mutable_head_pending_and_proofs_are_never_cached(self):
        receipt = self.isolated("recover")
        with rehearsal.PrivateFilesystem(self.root):
            store, _provider = _private_store(receipt)
            envelope = io.document(io.referenced(receipt["recovery_ref"]))
            with store.historical_validation(receipt["plan_ref"]):
                store._envelope(envelope)
                original = store.head_path.read_bytes()
                try:
                    io.atomic_replace(
                        store.head_path,
                        {
                            "schema": "autostop.j1.crm-forward-head.v1",
                            "sequence": io.document(original)["sequence"],
                            "sha256": "f" * 64,
                        },
                    )
                    with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
                        store.head()
                finally:
                    io.atomic_replace(store.head_path, io.document(original))
                original = store.pending_path.read_bytes()
                try:
                    changed = io.document(original)
                    changed["candidate"]["tree"] = "e" * 40
                    io.atomic_replace(store.pending_path, changed)
                    with self.assertRaisesRegex(io.Refusal, "state_pending_mismatch"):
                        store._matching_recovery(envelope)
                finally:
                    io.atomic_replace(store.pending_path, io.document(original))
                proof = Path(receipt["proofs"]["backup"]["path"])
                original = proof.read_bytes()
                try:
                    proof.write_bytes(original + b"\n")
                    with self.assertRaises(io.Refusal):
                        store._committed_recovery(envelope)
                finally:
                    proof.write_bytes(original)
            self.assertEqual(store.head()["sha256"], receipt["commit"]["sha256"])

    def test_other_thread_and_inherited_pid_cannot_use_active_scope(self):
        receipt = self.isolated("owner")
        self.assertTrue(receipt["inherited_scope_denied"])
        with rehearsal.PrivateFilesystem(self.root):
            store, _provider = _private_store(receipt)
            with store.historical_validation(receipt["plan_ref"]):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    with self.assertRaisesRegex(io.Refusal, "cold_history_operation_owner_or_plan"):
                        executor.submit(store.head).result(timeout=5)
            self.assertFalse(hasattr(store, "_historical_validation"))

    def test_operation_byte_bound_and_exception_never_leave_reusable_scope(self):
        receipt = self.isolated("prepare")
        with rehearsal.PrivateFilesystem(self.root):
            store, _provider = _private_store(receipt)
            envelope = io.document(io.referenced(receipt["recovery_ref"]))
            with patch.object(transactions, "MAX_HISTORICAL_BYTES", 1):
                with self.assertRaisesRegex(io.Refusal, "cold_history_operation_budget"):
                    with store.historical_validation(receipt["plan_ref"]):
                        store._envelope(envelope)
            self.assertFalse(hasattr(store, "_historical_validation"))
            with self.assertRaisesRegex(io.Refusal, "original_test_refusal"):
                with store.historical_validation(receipt["plan_ref"]):
                    store._envelope(envelope)
                    raise io.Refusal("original_test_refusal")
            self.assertFalse(hasattr(store, "_historical_validation"))
            with store.historical_validation(receipt["plan_ref"]):
                store._envelope(envelope)


if __name__ == "__main__":
    unittest.main()
