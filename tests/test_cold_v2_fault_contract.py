"""Actual recovery-storage crash only, without native acceptance or backup."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import j1_forward_cold_state as transactions
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal
from tests import test_j1_forward_state as state_tests
from tests.cold_v2_trace import _crash_after_history


def _isolated_check(case, root):
    substrate = state_tests.ForwardStateTests()
    substrate.root = root = Path(root)
    substrate.store_root, substrate.base = root / "state", "1" * 64
    store = substrate.store = transactions.ColdStore(substrate.store_root, substrate.base)
    with rehearsal.PrivateFilesystem(root):
        if case == "recover":
            _, plan, envelope, proofs = substrate.recovery_fixture()
            store.begin_recovery(envelope)
            pending = store.pending_path.read_bytes()
            _crash_after_history(store, plan, lambda: store.commit_recovery(envelope, proofs))
            reopened = transactions.ColdStore(store.root, substrate.base)
            try:
                reopened.head()
            except io.Refusal as error:
                io.require(str(error) == "state_head_mismatch", "wrong_crash_boundary")
            else:
                raise AssertionError("history must precede HEAD after real child death")
            committed = reopened.recover_recovery(envelope)
            io.require(
                reopened.commit_recovery(envelope, proofs) == committed,
                "recovered_commit_not_idempotent",
            )
            receipt = {
                "history_crash_exit": 77,
                "head_mismatch_before_recover": True,
                "commit": committed,
                "recovery_ref": reopened._recovery_ref(envelope),
                "old_pending_sha256": io.sha(pending),
            }
        else:
            io.require(case == "miss", "unknown_private_case")
            try:
                _crash_after_history(store, substrate.plan(), lambda: None)
            except io.Refusal as error:
                io.require(str(error) == "private_recovery_crash_missed", "wrong_missed_boundary")
            else:
                raise AssertionError("a missed write must fail")
            receipt = {"missed_boundary_denied": True}
        io.write_new(root / "result.json", receipt)


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
)
class RecoveryCrashContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="isolated-cold-recovery-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = transactions.ColdStore(self.root / "state", "1" * 64)

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
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-8192:])
        with rehearsal.PrivateFilesystem(self.root):
            return io.document(io.read_protected(self.root / "result.json"))

    def test_real_history_crash_reopens_and_recovers_same_recovery_transaction(self):
        receipt = self.isolated("recover")
        self.assertEqual(receipt["history_crash_exit"], 77)
        self.assertTrue(receipt["head_mismatch_before_recover"])
        with rehearsal.PrivateFilesystem(self.root):
            envelope = io.document(io.referenced(receipt["recovery_ref"]))
            self.assertEqual(self.store.recover_recovery(envelope), receipt["commit"])
            self.assertEqual(len(list(self.store.history.iterdir())), 1)
            self.assertEqual(self.store.head()["sha256"], receipt["commit"]["sha256"])
            self.assertEqual(
                io.sha(self.store.pending_path.read_bytes()), receipt["old_pending_sha256"]
            )

    def test_missing_history_boundary_is_a_failure(self):
        self.assertTrue(self.isolated("miss")["missed_boundary_denied"])
        self.assertFalse(self.store.history.exists())


if __name__ == "__main__":
    unittest.main()
