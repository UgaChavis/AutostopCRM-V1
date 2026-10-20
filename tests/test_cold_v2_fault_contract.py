"""Actual recovery-storage crash only, without native acceptance or backup."""

from __future__ import annotations

import os
import sys
import unittest

from scripts import j1_forward_cold_state as transactions
from scripts import j1_forward_io as io
from tests import test_j1_forward_state as state_tests
from tests.cold_v2_trace import _crash_after_history


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
)
class RecoveryCrashContractTests(unittest.TestCase):
    def setUp(self):
        self.substrate = state_tests.ForwardStateTests()
        self.substrate.setUp()
        self.addCleanup(self.substrate.doCleanups)
        # Tiny retained v2 storage envelope exercises the real recovery mixin
        # through ColdStore. No native/cryptographic acceptance is asserted.
        self.store = self.substrate.store = transactions.ColdStore(
            self.substrate.store_root, self.substrate.base
        )

    def test_real_history_crash_reopens_and_recovers_same_recovery_transaction(self):
        _, plan, envelope, proofs = self.substrate.recovery_fixture()
        self.store.begin_recovery(envelope)
        pending = self.store.pending_path.read_bytes()
        _crash_after_history(self.store, plan, lambda: self.store.commit_recovery(envelope, proofs))
        reopened = transactions.ColdStore(self.store.root, self.substrate.base)
        self.assertEqual(reopened.pending_path.read_bytes(), pending)
        with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
            reopened.head()
        committed = reopened.recover_recovery(envelope)
        self.assertEqual(reopened.commit_recovery(envelope, proofs), committed)
        self.assertEqual(len(list(reopened.history.iterdir())), 1)
        self.assertEqual(reopened.head()["sha256"], committed["sha256"])

    def test_missing_history_boundary_is_a_failure(self):
        plan = self.substrate.plan()
        with self.assertRaisesRegex(io.Refusal, "private_recovery_crash_missed"):
            _crash_after_history(self.store, plan, lambda: None)
        self.assertFalse(self.store.history.exists())


if __name__ == "__main__":
    unittest.main()
