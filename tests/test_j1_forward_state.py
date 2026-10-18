"""Real file/lock tests for CRM generations, without any native service access."""

from __future__ import annotations

import copy
import os
import stat
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_io as io
from scripts import j1_forward_recovery_state as recovery
from scripts import j1_forward_state as state


class ForwardStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store_root = self.root / "state"
        self.base = "1" * 64
        self.store = state.Store(self.store_root, self.base)
        # Substitute only native ownership/permission and directory-fsync policy.
        # The protocol still uses real files, exclusive creation, atomic replace,
        # strict parsing, hash chains and actual OS locks on every platform.
        protected = patch.object(io, "protected_directory", self.physical_directory)
        protected.start()
        self.addCleanup(protected.stop)
        owner = patch.object(io, "ROOT_UID", getattr(os, "getuid", lambda: 0)())
        owner.start()
        self.addCleanup(owner.stop)
        if os.name == "nt":
            reader = patch.object(io, "read_protected", self.windows_read)
            reader.start()
            self.addCleanup(reader.stop)
            sync = patch.object(io, "fsync_directory")
            sync.start()
            self.addCleanup(sync.stop)

    @staticmethod
    def physical_directory(path, **_kwargs):
        path = Path(path)
        io.require(path.is_absolute() and ".." not in path.parts, "physical_test_path")
        for part in (path, *path.parents):
            info = part.lstat()
            io.require(stat.S_ISDIR(info.st_mode) and not part.is_symlink(), "physical_test_dir")
        return path

    @classmethod
    def windows_read(cls, path, limit=2 * 1024**2):
        path = Path(path)
        cls.physical_directory(path.parent)
        before = path.lstat()
        io.require(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and not path.is_symlink()
            and before.st_size <= limit,
            "physical_test_file",
        )
        raw = path.read_bytes()
        after = path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        io.require(
            len(raw) == before.st_size
            and all(getattr(before, key) == getattr(after, key) for key in fields),
            "physical_test_file_changed",
        )
        return raw

    def plan(self, *, sequence=1, predecessor=None, attempt=None):
        return {
            "schema": state.PLAN_SCHEMA,
            "scope": state.SCOPE,
            "host": "synthetic.example",
            "attempt_id": attempt or str(uuid.uuid4()),
            "sequence": sequence,
            "predecessor_sha256": predecessor or self.base,
            "base_sha256": self.base,
            "candidate": {
                "revision": "2" * 40,
                "tree": "3" * 40,
                "image": "sha256:" + "4" * 64,
                "consumer_sha256": "5" * 64,
            },
            "baseline_ref": {"path": str(self.root / "baseline.json"), "sha256": "6" * 64},
            "created_at": "2026-10-19T00:00:00Z",
            "deadline_utc": "2026-10-19T00:08:00+00:00",
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }

    def proofs(self):
        return {
            name: {"path": str(self.root / f"{name}.json"), "sha256": "7" * 64}
            for name in state.PROOF_FIELDS
        }

    def committed(self, *, finish=True):
        plan = self.plan()
        self.store.begin(plan)
        ref = self.store.commit(plan, self.proofs())
        if finish:
            self.store.finish(plan)
        return plan, ref

    def interrupt_head_publication(self, plan):
        self.store.begin(plan)
        with patch.object(io, "atomic_replace", side_effect=OSError("synthetic power loss")):
            with self.assertRaisesRegex(OSError, "synthetic power loss"):
                self.store.commit(plan, self.proofs())

    def test_first_read_has_base_identity_and_no_filesystem_effect(self):
        self.assertEqual(self.store.head(), {"sequence": 0, "sha256": self.base, "record": None})
        self.assertIsNone(self.store.pending())
        self.assertFalse(self.store_root.exists())

    def test_commit_advances_and_finish_preserves_entire_history(self):
        plan, first = self.committed()
        first_bytes = Path(first["path"]).read_bytes()
        second_plan = self.plan(sequence=2, predecessor=first["sha256"])
        self.store.begin(second_plan)
        second = self.store.commit(second_plan, self.proofs())
        self.assertEqual(self.store.pending(), second_plan)
        self.assertEqual(self.store.head()["sha256"], second["sha256"])
        self.store.finish(second_plan)
        self.assertIsNone(self.store.pending())
        self.assertEqual(Path(first["path"]).read_bytes(), first_bytes)
        self.assertEqual(self.store.head()["record"]["previous_sha256"], first["sha256"])
        self.assertEqual(self.store.head()["record"]["plan"]["candidate"], plan["candidate"])

    def test_only_matching_pending_and_exact_proofs_are_idempotent(self):
        plan = self.plan()
        pending_ref = self.store.begin(plan)
        self.assertEqual(self.store.begin(copy.deepcopy(plan)), pending_ref)
        committed = self.store.commit(plan, self.proofs())
        self.assertEqual(self.store.commit(plan, self.proofs()), committed)
        altered = self.proofs()
        altered["backup"]["sha256"] = "8" * 64
        with self.assertRaisesRegex(io.Refusal, "state_committed_proofs_mismatch"):
            self.store.commit(plan, altered)
        self.store.finish(plan)
        with self.assertRaisesRegex(io.Refusal, "state_pending_mismatch"):
            self.store.commit(plan, self.proofs())

    def test_two_concurrent_attempts_have_one_winner(self):
        barrier = threading.Barrier(2)
        plans = [self.plan(), self.plan()]

        def attempt(plan):
            barrier.wait(timeout=5)
            try:
                state.Store(self.store_root, self.base).begin(plan)
                return "won"
            except io.Refusal as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, plans))
        self.assertEqual(sorted(outcomes), ["state_pending_mismatch", "won"])
        self.assertIn(self.store.pending(), plans)
        self.assertEqual(self.store.head()["sequence"], 0)

    @unittest.skipUnless(os.name == "nt", "Windows byte-range lock initialization")
    def test_windows_concurrent_empty_lock_initializes_only_under_owned_lock(self):
        import msvcrt

        self.store_root.mkdir()
        lock_path = self.store_root / ".store.lock"
        lock_path.touch()
        lock_info = lock_path.stat()
        identity = (lock_info.st_dev, lock_info.st_ino)
        observed_empty = threading.Barrier(2)
        threads_seen, owners, writes = set(), set(), []
        guard = threading.Lock()
        original_fstat, original_write = os.fstat, os.write
        original_locking = msvcrt.locking

        def fstat(descriptor):
            info = original_fstat(descriptor)
            should_wait = False
            if (info.st_dev, info.st_ino) == identity and info.st_size == 0:
                with guard:
                    thread = threading.get_ident()
                    if thread not in threads_seen:
                        threads_seen.add(thread)
                        should_wait = True
                if should_wait:
                    observed_empty.wait(timeout=5)
            return info

        def locking(descriptor, mode, count):
            result = original_locking(descriptor, mode, count)
            with guard:
                if mode == msvcrt.LK_NBLCK:
                    owners.add(threading.get_ident())
                elif mode == msvcrt.LK_UNLCK:
                    owners.remove(threading.get_ident())
            return result

        def write(descriptor, raw):
            info = original_fstat(descriptor)
            if (info.st_dev, info.st_ino) == identity:
                with guard:
                    self.assertIn(threading.get_ident(), owners)
                    writes.append(raw)
            return original_write(descriptor, raw)

        def attempt(plan):
            try:
                state.Store(self.store_root, self.base).begin(plan)
                return "won"
            except io.Refusal as error:
                return str(error)

        with (
            patch.object(state.os, "fstat", side_effect=fstat),
            patch.object(state.os, "write", side_effect=write),
            patch.object(msvcrt, "locking", side_effect=locking),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            outcomes = list(pool.map(attempt, [self.plan(), self.plan()]))
        self.assertEqual(sorted(outcomes), ["state_pending_mismatch", "won"])
        self.assertEqual(writes, [b"\0"])
        self.assertEqual(lock_path.read_bytes(), b"\0")
        self.assertFalse(owners)

    @unittest.skipUnless(os.name == "nt", "Windows one-byte lock backing file")
    def test_windows_non_single_byte_lock_is_refused_without_rewrite(self):
        self.store_root.mkdir()
        lock_path = self.store_root / ".store.lock"
        lock_path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(io.Refusal, "state_lock_size_invalid"):
            self.store.begin(self.plan())
        self.assertEqual(lock_path.read_bytes(), b"corrupt")
        self.assertFalse(self.store.pending_path.exists())

    def test_replay_wrong_predecessor_and_reused_uuid_deny(self):
        plan, ref = self.committed()
        for candidate in (
            plan,
            self.plan(),
            self.plan(sequence=2, predecessor="f" * 64),
            self.plan(sequence=2, predecessor=ref["sha256"], attempt=plan["attempt_id"]),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(io.Refusal):
                    self.store.begin(candidate)
        self.assertIsNone(self.store.pending())
        self.assertEqual(self.store.head()["sha256"], ref["sha256"])

    def test_uuid_conflict_while_pending_cannot_change_candidate(self):
        plan = self.plan()
        self.store.begin(plan)
        changed = copy.deepcopy(plan)
        changed["candidate"]["image"] = "sha256:" + "a" * 64
        with self.assertRaisesRegex(io.Refusal, "state_pending_mismatch"):
            self.store.begin(changed)
        self.assertEqual(self.store.pending(), plan)

    def test_commit_rechecks_attempt_uniqueness_from_pending(self):
        first_plan, ref = self.committed()
        plan = self.plan(sequence=2, predecessor=ref["sha256"])
        self.store.begin(plan)
        plan["attempt_id"] = first_plan["attempt_id"]
        self.store.pending_path.write_bytes(io.canonical(plan))
        with self.assertRaisesRegex(io.Refusal, "state_attempt_replayed"):
            self.store.commit(plan, self.proofs())
        self.assertEqual(self.store.head()["sha256"], ref["sha256"])
        self.assertEqual(len(list(self.store.history.iterdir())), 1)

    def test_history_capacity_refuses_before_publishing_an_invalid_generation(self):
        _, ref = self.committed()
        plan = self.plan(sequence=2, predecessor=ref["sha256"])
        with patch.object(state, "MAX_HISTORY", 1):
            with self.assertRaisesRegex(io.Refusal, "state_history_limit"):
                self.store.begin(plan)
        self.store.begin(plan)
        with patch.object(state, "MAX_HISTORY", 1):
            with self.assertRaisesRegex(io.Refusal, "state_history_limit"):
                self.store.commit(plan, self.proofs())
        self.assertEqual(self.store.head()["sha256"], ref["sha256"])
        self.assertEqual(len(list(self.store.history.iterdir())), 1)

    def test_unknown_fields_types_invalid_identity_and_unsafe_flags_deny(self):
        mutations = [
            lambda p: p.update(unknown=True),
            lambda p: p.update(sequence=True),
            lambda p: p.update(old_writer_allowed=True),
            lambda p: p.update(candidate_may_have_written=False),
            lambda p: p.update(base_sha256="0" * 64),
            lambda p: p.update(attempt_id="not-a-uuid"),
            lambda p: p.update(deadline_utc="2026-10-18T00:00:00Z"),
            lambda p: p.update(created_at="2026-10-19T00:00:00"),
            lambda p: p["candidate"].update(image="latest"),
            lambda p: p["candidate"].update(command="arbitrary"),
            lambda p: p["baseline_ref"].update(path="../foreign.json"),
        ]
        for mutate in mutations:
            plan = self.plan()
            mutate(plan)
            with self.subTest(plan=plan):
                with self.assertRaises(io.Refusal):
                    self.store.begin(plan)
        self.assertFalse(self.store_root.exists())

    def test_proof_shapes_are_strict_but_references_are_not_executed_or_read(self):
        plan = self.plan()
        self.store.begin(plan)
        for proofs in (
            {},
            {**self.proofs(), "accepted": True},
            {**self.proofs(), "backup": {"path": "relative", "sha256": "7" * 64}},
        ):
            with self.assertRaises(io.Refusal):
                self.store.commit(plan, proofs)
        # None of the referenced evidence exists. Validation belongs to the
        # orchestrator; Store only records the already-reviewed references.
        self.store.commit(plan, self.proofs())

    def test_history_before_head_requires_explicit_exact_pending_recovery(self):
        plan = self.plan()
        self.interrupt_head_publication(plan)
        with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
            self.store.head()
        with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
            self.store.commit(plan, self.proofs())
        with self.assertRaisesRegex(io.Refusal, "state_pending_mismatch"):
            self.store.recover(self.plan())
        ref = self.store.recover(plan)
        self.assertEqual(self.store.head()["sha256"], ref["sha256"])
        self.assertEqual(self.store.recover(plan), ref)
        self.store.finish(plan)

    def test_second_generation_recovery_accepts_only_immediate_predecessor(self):
        _, first = self.committed()
        plan = self.plan(sequence=2, predecessor=first["sha256"])
        self.interrupt_head_publication(plan)
        first_head = self.store.head_path.read_bytes()
        self.store.head_path.unlink()
        with self.assertRaisesRegex(io.Refusal, "state_recovery_head_mismatch"):
            self.store.recover(plan)
        self.store.head_path.write_bytes(first_head)
        result = self.store.recover(plan)
        self.assertEqual(self.store.head()["sha256"], result["sha256"])

    def test_recovery_makes_record_and_directory_durable_before_head_publication(self):
        plan = self.plan()
        self.interrupt_head_publication(plan)
        events = []
        original_sync = os.fsync
        original_publish = self.store._publish_head

        def sync_file(descriptor):
            events.append("file")
            original_sync(descriptor)

        def sync_directory(path):
            self.assertIn(path, (self.store.root, self.store.history))
            events.append("root" if path == self.store.root else "history")

        def publish(record, ref):
            self.assertEqual(events, ["file", "root", "file", "history"])
            events.append("head")
            original_publish(record, ref)

        with (
            patch.object(state.os, "fsync", side_effect=sync_file),
            patch.object(state, "_sync_directory", side_effect=sync_directory),
            patch.object(self.store, "_publish_head", side_effect=publish),
        ):
            self.store.recover(plan)
        self.assertIn("head", events)

    def test_recovery_fsync_failure_keeps_head_unpublished(self):
        plan = self.plan()
        self.interrupt_head_publication(plan)
        with patch.object(state.os, "fsync", side_effect=OSError("synthetic fsync failure")):
            with self.assertRaises(OSError):
                self.store.recover(plan)
        self.assertFalse(self.store.head_path.exists())
        self.assertEqual(self.store.pending(), plan)

    def test_idempotent_begin_reestablishes_pending_durability(self):
        plan = self.plan()
        self.store.begin(plan)
        with patch.object(state.os, "fsync", side_effect=OSError("pending fsync failed")):
            with self.assertRaisesRegex(OSError, "pending fsync failed"):
                self.store.begin(plan)
        self.assertEqual(self.store.pending(), plan)

    def test_idempotent_commit_and_current_head_recovery_flush_every_pointer(self):
        plan, ref = self.committed(finish=False)
        original = self.store._durable
        for action in (
            lambda: self.store.commit(plan, self.proofs()),
            lambda: self.store.recover(plan),
        ):
            paths = []

            def durable(reference):
                paths.append(Path(reference["path"]))
                return original(reference)

            with patch.object(self.store, "_durable", side_effect=durable):
                self.assertEqual(action(), ref)
            self.assertEqual(
                paths, [self.store.pending_path, Path(ref["path"]), self.store.head_path]
            )

    def test_finish_does_not_remove_pending_before_head_is_durable(self):
        plan, _ = self.committed(finish=False)
        original = self.store._durable

        def refuse_head(reference):
            if Path(reference["path"]) == self.store.head_path:
                raise OSError("head fsync failed")
            return original(reference)

        with patch.object(self.store, "_durable", side_effect=refuse_head):
            with self.assertRaisesRegex(OSError, "head fsync failed"):
                self.store.finish(plan)
        self.assertEqual(self.store.pending(), plan)

    def test_recovery_rechecks_hash_after_file_and_directory_fsync(self):
        plan = self.plan()
        self.interrupt_head_publication(plan)
        history = next(self.store.history.iterdir())

        def change_after_sync(path):
            if path == self.store.history:
                history.write_bytes(history.read_bytes() + b" ")

        with patch.object(state, "_sync_directory", side_effect=change_after_sync):
            with self.assertRaisesRegex(io.Refusal, "state_durability_record_changed"):
                self.store.recover(plan)
        self.assertFalse(self.store.head_path.exists())

    def test_missing_regressed_and_wrong_head_with_history_refuse(self):
        _, first = self.committed()
        first_head = self.store.head_path.read_bytes()
        plan = self.plan(sequence=2, predecessor=first["sha256"])
        self.store.begin(plan)
        self.store.commit(plan, self.proofs())
        current_head = self.store.head_path.read_bytes()
        variants = [
            None,
            first_head,
            io.canonical({"schema": state.HEAD_SCHEMA, "sequence": 2, "sha256": "f" * 64}),
        ]
        for raw in variants:
            if self.store.head_path.exists():
                self.store.head_path.unlink()
            if raw is not None:
                self.store.head_path.write_bytes(raw)
            with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
                self.store.head()
        self.store.head_path.write_bytes(current_head)

    def test_finish_refuses_uncommitted_or_changed_attempt_and_keeps_pending(self):
        plan = self.plan()
        self.store.begin(plan)
        with self.assertRaisesRegex(io.Refusal, "state_finish_uncommitted"):
            self.store.finish(plan)
        self.store.commit(plan, self.proofs())
        altered = copy.deepcopy(plan)
        altered["candidate"]["tree"] = "f" * 40
        with self.assertRaisesRegex(io.Refusal, "state_pending_mismatch"):
            self.store.finish(altered)
        self.assertEqual(self.store.pending(), plan)

    def test_partial_commit_and_duplicate_json_refuse_without_cleanup(self):
        plan = self.plan()
        self.interrupt_head_publication(plan)
        history_path = next(self.store.history.iterdir())
        for raw in (b'{"schema":', b'{"schema":"first","schema":"second"}'):
            history_path.write_bytes(raw)
            with self.assertRaises(io.Refusal):
                self.store.recover(plan)
            self.assertEqual(self.store.pending(), plan)

    def test_history_change_gap_and_foreign_file_refuse(self):
        _, first = self.committed()
        file = Path(first["path"])
        original = file.read_bytes()
        record = io.document(original)
        record["plan"]["candidate"]["revision"] = "f" * 40
        file.write_bytes(io.canonical(record))
        with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
            self.store.head()
        file.write_bytes(original)
        destination = self.store.history / "00000000000000000002.json"
        file.rename(destination)
        with self.assertRaisesRegex(io.Refusal, "state_history_gap"):
            self.store.head()
        destination.rename(file)
        (self.store.history / "foreign").write_bytes(b"{}")
        with self.assertRaisesRegex(io.Refusal, "state_history_gap"):
            self.store.head()

    def test_recovery_does_not_accept_history_from_another_pending_plan(self):
        plan = self.plan()
        self.interrupt_head_publication(plan)
        other = self.plan()
        self.store.pending_path.write_bytes(io.canonical(other))
        with self.assertRaisesRegex(io.Refusal, "state_recovery_plan_mismatch"):
            self.store.recover(other)

    def test_hardlinked_history_is_refused(self):
        _, first = self.committed()
        linked = self.root / "outside.json"
        try:
            os.link(first["path"], linked)
        except OSError as error:
            self.skipTest(f"Native hardlinks unavailable: {error}")
        with self.assertRaises(io.Refusal):
            self.store.head()

    def test_confirm_finished_refuses_pending_without_durability_effects(self):
        plan, _ = self.committed(finish=False)
        with patch.object(self.store, "_durable") as durable:
            with self.assertRaisesRegex(io.Refusal, "state_terminal_pending_present"):
                self.store.confirm_finished(plan)
            durable.assert_not_called()
        self.assertEqual(self.store.pending(), plan)

    def test_confirm_finished_refuses_wrong_plan_wrong_head_and_empty_state(self):
        with self.assertRaisesRegex(io.Refusal, "state_terminal_head_mismatch"):
            self.store.confirm_finished(self.plan())
        self.assertFalse(self.store_root.exists())
        plan, _ = self.committed()
        other = copy.deepcopy(plan)
        other["candidate"]["revision"] = "f" * 40
        with self.assertRaisesRegex(io.Refusal, "state_terminal_head_mismatch"):
            self.store.confirm_finished(other)
        value = io.document(self.store.head_path.read_bytes())
        value["sha256"] = "f" * 64
        self.store.head_path.write_bytes(io.canonical(value))
        with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
            self.store.confirm_finished(plan)

    def test_confirm_finished_durability_order_history_head_then_pending_absence(self):
        plan, ref = self.committed()
        events = []
        original_durable = self.store._durable
        original_sync = state._sync_directory

        def durable(reference):
            events.append("history-file" if reference == ref else "head-file")
            return original_durable(reference)

        def sync(path):
            events.append("history-directory" if path == self.store.history else "root-directory")
            original_sync(path)

        with (
            patch.object(self.store, "_durable", side_effect=durable),
            patch.object(state, "_sync_directory", side_effect=sync),
        ):
            self.assertEqual(self.store.confirm_finished(plan), ref)
        self.assertEqual(
            events,
            ["history-file", "history-directory", "head-file", "root-directory", "root-directory"],
        )
        self.assertIsNone(self.store.pending())

    def test_confirm_finished_absence_fsync_failure_never_acknowledges(self):
        plan, ref = self.committed()
        roots = []
        original_sync = state._sync_directory

        def sync(path):
            if path == self.store.root:
                roots.append(path)
                if len(roots) == 2:
                    raise OSError("terminal absence fsync failed")
            original_sync(path)

        with patch.object(state, "_sync_directory", side_effect=sync):
            with self.assertRaisesRegex(OSError, "terminal absence fsync failed"):
                self.store.confirm_finished(plan)
        self.assertIsNone(self.store.pending())
        self.assertEqual(self.store.confirm_finished(plan), ref)

    def test_confirm_finished_file_fsync_failure_never_acknowledges(self):
        plan, ref = self.committed()
        with patch.object(state.os, "fsync", side_effect=OSError("terminal file fsync failed")):
            with self.assertRaisesRegex(OSError, "terminal file fsync failed"):
                self.store.confirm_finished(plan)
        self.assertEqual(self.store.head()["sha256"], ref["sha256"])
        self.assertIsNone(self.store.pending())

    def test_confirm_finished_refuses_pending_reappearance_during_barriers(self):
        plan, _ = self.committed()
        roots = []
        original_sync = state._sync_directory

        def sync(path):
            original_sync(path)
            if path == self.store.root:
                roots.append(path)
                if len(roots) == 2:
                    self.store.pending_path.write_bytes(io.canonical(plan))

        with patch.object(state, "_sync_directory", side_effect=sync):
            with self.assertRaisesRegex(io.Refusal, "state_terminal_pending_changed"):
                self.store.confirm_finished(plan)

    def test_confirm_finished_refuses_valid_head_advancement_during_barriers(self):
        plan, first = self.committed()
        next_plan = self.plan(sequence=2, predecessor=first["sha256"])
        roots = []
        original_sync = state._sync_directory

        def sync(path):
            original_sync(path)
            if path == self.store.root:
                roots.append(path)
                if len(roots) == 2:
                    record = {
                        "schema": state.COMMIT_SCHEMA,
                        "plan": next_plan,
                        "proofs": self.proofs(),
                        "previous_sha256": first["sha256"],
                        "sequence": 2,
                    }
                    ref = io.write_new(self.store.history / "00000000000000000002.json", record)
                    io.atomic_replace(
                        self.store.head_path,
                        {"schema": state.HEAD_SCHEMA, "sequence": 2, "sha256": ref["sha256"]},
                    )

        with patch.object(state, "_sync_directory", side_effect=sync):
            with self.assertRaisesRegex(io.Refusal, "state_terminal_head_changed"):
                self.store.confirm_finished(plan)

    def recovery_fixture(self):
        now = datetime.now(UTC)
        origin = self.plan()
        origin["created_at"] = (now - timedelta(days=2)).isoformat()
        origin["deadline_utc"] = (now - timedelta(days=1)).isoformat()
        plan = self.plan()
        plan["created_at"] = (now - timedelta(seconds=1)).isoformat()
        plan["deadline_utc"] = (now + timedelta(seconds=300)).isoformat()
        plan["candidate"]["revision"] = "a" * 40
        old, new = (self.root / "attempts" / value["attempt_id"] for value in (origin, plan))
        for directory in (old, new):
            directory.mkdir(parents=True)
        origin["baseline_ref"] = io.write_new(
            old / "baseline.json", {"synthetic": origin["attempt_id"]}
        )
        retained = self.root / "origins" / origin["attempt_id"]
        retained.mkdir(parents=True)
        io.write_new(retained / "pending.json", origin)
        envelope = {
            "schema": recovery.RECOVERY_SCHEMA,
            "recovery_id": plan["attempt_id"],
            "nonce": "a" * 64,
            "origin_pending_ref": self.store.begin(origin),
            "origin_plan_ref": io.write_new(old / "plan.json", origin),
            "origin_closure_ref": io.write_new(
                old / "closure-owned.json", {"synthetic": "old closure"}
            ),
            "origin_scope_ref": io.write_new(
                old / "process-scope.json", {"synthetic": "old scope"}
            ),
            "origin_failure_ref": io.write_new(old / "failure.json", {"synthetic": "old failure"}),
            "origin_bundle_ref": io.write_new(
                retained / "bundle.json", {"synthetic": "old bundle"}
            ),
            "deployment_lock_identity": [1, 123],
        }
        for name in ("recovery_code_ref", "ci_ref", "hold_ref"):
            envelope[name] = io.write_new(new / (name + ".json"), {"synthetic": name})
        plan["baseline_ref"] = io.write_new(
            new / "baseline.json",
            {"recovery_origin": {key: envelope[key] for key in recovery.ORIGIN_FIELDS}},
        )
        envelope["plan_ref"] = io.write_new(new / "plan.json", plan)
        proofs = {
            name: io.write_new(
                new / (name + "-new-proof.json"),
                {"synthetic_proof": name, "attempt_id": plan["attempt_id"]},
            )
            for name in state.PROOF_FIELDS
        }
        return origin, plan, envelope, proofs

    def test_recovery_begin_preserves_original_bytes_and_has_explicit_active_identity(self):
        origin, plan, envelope, _ = self.recovery_fixture()
        original = self.store.pending_path.read_bytes()
        self.assertEqual(
            self.store.active_plan(),
            {"plan": origin, "origin_plan_ref": None, "recovery_ref": None},
        )
        reference = self.store.begin_recovery(envelope)
        self.assertEqual(self.store.begin_recovery(copy.deepcopy(envelope)), reference)
        self.assertEqual(self.store.pending_path.read_bytes(), original)
        self.assertEqual(Path(self.store._archive_ref(envelope)["path"]).read_bytes(), original)
        self.assertEqual(
            self.store.active_plan(),
            {
                "plan": plan,
                "origin_plan_ref": envelope["origin_plan_ref"],
                "recovery_ref": reference,
            },
        )
        self.assertEqual(self.store.head()["sequence"], 0)

    def test_active_recovery_blocks_every_v1_mutating_or_terminal_api(self):
        origin, _, envelope, _ = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        for action in (
            lambda: self.store.begin(origin),
            lambda: self.store.commit(origin, self.proofs()),
            lambda: self.store.recover(origin),
            lambda: self.store.finish(origin),
            lambda: self.store.confirm_finished(origin),
        ):
            with (
                self.subTest(action=action),
                self.assertRaisesRegex(io.Refusal, "state_recovery_active"),
            ):
                action()
        self.assertEqual(self.store.pending(), origin)
        self.assertFalse(self.store.head_path.exists())

    def test_recovery_publication_interrupt_before_pointer_can_retry_exact_envelope(self):
        origin, _, envelope, _ = self.recovery_fixture()
        write = io.write_new

        def interrupted(path, value):
            if path == self.store.recovery_path:
                raise OSError("pointer publication interrupted")
            return write(path, value)

        with patch.object(io, "write_new", side_effect=interrupted), self.assertRaises(OSError):
            self.store.begin_recovery(envelope)
        self.assertFalse(self.store.recovery_path.exists())
        self.assertEqual(self.store.pending(), origin)
        archive = Path(self.store._archive_ref(envelope)["path"]).read_bytes()
        self.store.begin_recovery(envelope)
        self.assertEqual(Path(self.store._archive_ref(envelope)["path"]).read_bytes(), archive)

    def test_recovery_publication_unknown_after_pointer_is_idempotent(self):
        _, _, envelope, _ = self.recovery_fixture()
        write = io.write_new

        def interrupted(path, value):
            result = write(path, value)
            if path == self.store.recovery_path:
                raise OSError("pointer acknowledgment lost")
            return result

        with patch.object(io, "write_new", side_effect=interrupted), self.assertRaises(OSError):
            self.store.begin_recovery(envelope)
        self.assertEqual(self.store.begin_recovery(envelope), self.store._recovery_ref(envelope))

    def test_recovery_nonce_or_foreign_envelope_cannot_replace_active_owner(self):
        _, _, envelope, _ = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        changed = copy.deepcopy(envelope)
        changed["nonce"] = "b" * 64
        before = self.store.recovery_path.read_bytes()
        with self.assertRaisesRegex(io.Refusal, "recovery_baseline_origin_binding"):
            self.store.begin_recovery(changed)
        self.assertEqual(self.store.recovery_path.read_bytes(), before)

    def test_recovery_rejects_foreign_pending_and_nonidentical_original_bytes(self):
        origin, _, envelope, _ = self.recovery_fixture()
        raw = self.store.pending_path.read_bytes()
        self.store.pending_path.write_bytes(raw + b" ")
        with self.assertRaisesRegex(io.Refusal, "recovery_pending_bytes_changed"):
            self.store.begin_recovery(envelope)
        self.store.pending_path.write_bytes(
            io.canonical({**origin, "attempt_id": str(uuid.uuid4())})
        )
        with self.assertRaisesRegex(io.Refusal, "state_pending_mismatch"):
            self.store.begin_recovery(envelope)
        self.assertFalse(self.store.recovery_path.exists())

    def test_recovery_envelope_fields_nonce_lock_refs_and_fixed_paths_are_strict(self):
        _, _, envelope, _ = self.recovery_fixture()
        changes = (
            lambda e: e.update(extra=True),
            lambda e: e.update(schema="unknown"),
            lambda e: e.update(nonce="a" * 63),
            lambda e: e.update(deployment_lock_identity=[True, 1]),
            lambda e: e.update(recovery_id=str(uuid.uuid4())),
            lambda e: e["origin_pending_ref"].update(sha256="f" * 64),
            lambda e: e["origin_pending_ref"].update(path=str(self.root / "other-pending.json")),
            lambda e: e["ci_ref"].update(sha256="f" * 64),
        )
        for change in changes:
            altered = copy.deepcopy(envelope)
            change(altered)
            with self.subTest(change=change), self.assertRaises(io.Refusal):
                self.store.begin_recovery(altered)
        self.assertFalse(self.store.recovery_path.exists())

    def test_recovery_window_expired_future_or_oversized_is_refused_before_effects(self):
        _, plan, envelope, _ = self.recovery_fixture()
        now = datetime.now(UTC)
        for start, end in ((-100, -1), (100, 200), (-1, 601)):
            changed = copy.deepcopy(plan)
            changed["created_at"] = (now + timedelta(seconds=start)).isoformat()
            changed["deadline_utc"] = (now + timedelta(seconds=end)).isoformat()
            path = Path(envelope["plan_ref"]["path"])
            path.write_bytes(io.canonical(changed))
            envelope["plan_ref"] = io.reference(path)
            with (
                self.subTest(start=start, end=end),
                self.assertRaisesRegex(io.Refusal, "recovery_window"),
            ):
                self.store.begin_recovery(envelope)
        self.assertFalse(self.store.recovery_path.exists())

    def test_recovery_changed_base_host_sequence_or_predecessor_does_not_gain_authority(self):
        _, plan, envelope, _ = self.recovery_fixture()
        for key, value in (
            ("host", "foreign"),
            ("sequence", 2),
            ("base_sha256", "b" * 64),
            ("predecessor_sha256", "b" * 64),
        ):
            path = Path(envelope["plan_ref"]["path"])
            path.write_bytes(io.canonical({**plan, key: value}))
            envelope["plan_ref"] = io.reference(path)
            with self.subTest(key=key), self.assertRaises(io.Refusal):
                self.store.begin_recovery(envelope)
        self.assertFalse(self.store.recovery_path.exists())

    def test_recovery_commits_v2_then_consumes_pending_without_losing_origin_archive(self):
        origin, plan, envelope, proofs = self.recovery_fixture()
        original = self.store.pending_path.read_bytes()
        self.store.begin_recovery(envelope)
        committed = self.store.commit_recovery(envelope, proofs)
        record = self.store.head()["record"]
        self.assertEqual(record["schema"], recovery.COMMIT_SCHEMA)
        self.assertEqual(record["origin_plan"], origin)
        self.assertEqual(record["plan"], plan)
        self.assertEqual(self.store.pending_path.read_bytes(), original)
        self.assertEqual(self.store.commit_recovery(envelope, proofs), committed)
        self.assertEqual(self.store.finish_recovery(envelope), committed)
        self.assertIsNone(self.store.pending())
        self.assertIsNone(self.store.active_plan())
        self.assertEqual(self.store.head()["record"], record)
        self.assertEqual(Path(self.store._archive_ref(envelope)["path"]).read_bytes(), original)
        self.assertEqual(self.store.finish_recovery(envelope), committed)
        successor = self.plan(sequence=2, predecessor=committed["sha256"])
        self.store.begin(successor)
        self.store.commit(successor, self.proofs())
        self.assertEqual(len(self.store._records()), 2)

    def test_recovery_commit_requires_five_new_real_unique_evidence_files(self):
        _, _, envelope, proofs = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        variants = (
            {},
            {**proofs, "extra": envelope["origin_scope_ref"]},
            {**proofs, "closure": envelope["origin_closure_ref"]},
            {**proofs, "backup": proofs["closure"]},
            {**proofs, "backup": {**proofs["backup"], "sha256": "f" * 64}},
        )
        for altered in variants:
            with self.subTest(proofs=altered), self.assertRaises(io.Refusal):
                self.store.commit_recovery(envelope, altered)
        self.assertFalse(self.store.head_path.exists())
        Path(proofs["backup"]["path"]).unlink()
        with self.assertRaises(FileNotFoundError):
            self.store.commit_recovery(envelope, proofs)
        self.assertFalse(self.store.head_path.exists())

    def test_recovery_uncommitted_finish_never_removes_pending(self):
        origin, _, envelope, _ = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        with self.assertRaisesRegex(io.Refusal, "state_recovery_uncommitted"):
            self.store.finish_recovery(envelope)
        self.assertEqual(self.store.pending(), origin)
        self.assertTrue(self.store.recovery_path.exists())

    def test_recovery_history_before_head_requires_explicit_v2_repair(self):
        origin, _, envelope, proofs = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        with (
            patch.object(io, "atomic_replace", side_effect=OSError("lost HEAD")),
            self.assertRaises(OSError),
        ):
            self.store.commit_recovery(envelope, proofs)
        self.assertEqual(self.store.pending(), origin)
        with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
            self.store.head()
        with self.assertRaisesRegex(io.Refusal, "state_recovery_active"):
            self.store.recover(origin)
        reference = self.store.recover_recovery(envelope)
        self.assertEqual(self.store.head()["sha256"], reference["sha256"])
        self.assertEqual(self.store.recover_recovery(envelope), reference)
        self.store.finish_recovery(envelope)

    def test_recovery_durability_failure_keeps_original_pending_and_active_owner(self):
        origin, _, envelope, proofs = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        self.store.commit_recovery(envelope, proofs)
        original = self.store._durable

        def interrupted(reference):
            if reference["path"] == str(self.store.head_path):
                raise OSError("HEAD not durable")
            return original(reference)

        with (
            patch.object(self.store, "_durable", side_effect=interrupted),
            self.assertRaises(OSError),
        ):
            self.store.finish_recovery(envelope)
        self.assertEqual(self.store.pending(), origin)
        self.assertTrue(self.store.recovery_path.exists())

    def test_recovery_finish_interruption_after_pending_unlink_still_blocks_new_begin(self):
        _, plan, envelope, proofs = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        committed = self.store.commit_recovery(envelope, proofs)
        original = state._sync_directory

        def interrupted(path):
            if not self.store.pending_path.exists() and self.store.recovery_path.exists():
                raise OSError("pending absence not durable")
            original(path)

        with (
            patch.object(state, "_sync_directory", side_effect=interrupted),
            self.assertRaises(OSError),
        ):
            self.store.finish_recovery(envelope)
        self.assertTrue(self.store.recovery_path.exists())
        self.assertEqual(self.store.active_plan()["plan"], plan)
        with self.assertRaisesRegex(io.Refusal, "state_recovery_active"):
            self.store.begin(self.plan(sequence=2, predecessor=committed["sha256"]))
        self.assertEqual(self.store.finish_recovery(envelope), committed)

    def test_recovery_finish_lost_terminal_acknowledgment_reestablishes_absence(self):
        _, _, envelope, proofs = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        committed = self.store.commit_recovery(envelope, proofs)
        original = state._sync_directory

        def interrupted(path):
            if not self.store.pending_path.exists() and not self.store.recovery_path.exists():
                raise OSError("terminal acknowledgment lost")
            original(path)

        with (
            patch.object(state, "_sync_directory", side_effect=interrupted),
            self.assertRaises(OSError),
        ):
            self.store.finish_recovery(envelope)
        self.assertIsNone(self.store.pending())
        self.assertFalse(self.store.recovery_path.exists())
        self.assertEqual(self.store.finish_recovery(envelope), committed)

    def test_recovery_history_remembers_both_original_and_recovery_attempt_ids(self):
        origin, plan, envelope, proofs = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        committed = self.store.commit_recovery(envelope, proofs)
        self.store.finish_recovery(envelope)
        for attempt in (origin["attempt_id"], plan["attempt_id"]):
            with (
                self.subTest(attempt=attempt),
                self.assertRaisesRegex(io.Refusal, "state_attempt_replayed"),
            ):
                self.store.begin(
                    self.plan(sequence=2, predecessor=committed["sha256"], attempt=attempt)
                )

    def test_recovery_historical_archive_or_envelope_change_is_not_hidden_by_head(self):
        _, _, envelope, proofs = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        self.store.commit_recovery(envelope, proofs)
        self.store.finish_recovery(envelope)
        for path in (
            Path(self.store._archive_ref(envelope)["path"]),
            Path(self.store._recovery_ref(envelope)["path"]),
        ):
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(io.Refusal, "reference_hash_mismatch"),
            ):
                self.store.head()
            path.write_bytes(original)

    def test_recovery_committed_proofs_cannot_change_on_idempotent_commit(self):
        _, _, envelope, proofs = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        self.store.commit_recovery(envelope, proofs)
        path = Path(envelope["plan_ref"]["path"]).parent / "another-backup.json"
        changed = {**proofs, "backup": io.write_new(path, {"different": "proof"})}
        with self.assertRaisesRegex(io.Refusal, "state_committed_proofs_mismatch"):
            self.store.commit_recovery(envelope, changed)

    def test_recovery_new_baseline_must_bind_exact_eleven_origin_fields(self):
        _, plan, envelope, _ = self.recovery_fixture()
        baseline_path = Path(plan["baseline_ref"]["path"])
        original = io.document(baseline_path.read_bytes())
        self.assertEqual(len(original["recovery_origin"]), 11)
        for change in (
            lambda value: value["recovery_origin"].pop("origin_failure_ref"),
            lambda value: value["recovery_origin"].update(nonce="b" * 64),
            lambda value: value["recovery_origin"].update(extra="unbound"),
        ):
            altered = copy.deepcopy(original)
            change(altered)
            baseline_path.write_bytes(io.canonical(altered))
            plan["baseline_ref"] = io.reference(baseline_path)
            plan_path = Path(envelope["plan_ref"]["path"])
            plan_path.write_bytes(io.canonical(plan))
            envelope["plan_ref"] = io.reference(plan_path)
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(io.Refusal, "recovery_baseline_origin_binding"),
            ):
                self.store.begin_recovery(envelope)
        self.assertFalse(self.store.recovery_path.exists())

    def test_recovery_origin_bundle_and_pending_are_retained_at_exact_origin_root(self):
        origin, _, envelope, _ = self.recovery_fixture()
        retained = self.root / "origins" / origin["attempt_id"]
        raw = (retained / "pending.json").read_bytes()
        (retained / "pending.json").write_bytes(raw + b" ")
        with self.assertRaisesRegex(io.Refusal, "reference_hash_mismatch"):
            self.store.begin_recovery(envelope)
        (retained / "pending.json").write_bytes(raw)
        self.store.begin_recovery(envelope)
        self.assertEqual(self.store.pending_path.read_bytes(), raw)
        self.assertEqual(envelope["origin_bundle_ref"]["path"], str(retained / "bundle.json"))

    def test_recovery_deadline_is_rechecked_after_lock_wait(self):
        _, plan, envelope, _ = self.recovery_fixture()
        original = self.store._locked

        @contextmanager
        def delayed_lock(**kwargs):
            with original(**kwargs) as present, patch.object(recovery, "datetime") as clock:
                clock.now.return_value = datetime.fromisoformat(plan["deadline_utc"]) + timedelta(
                    seconds=1
                )
                yield present

        with (
            patch.object(self.store, "_locked", delayed_lock),
            self.assertRaisesRegex(io.Refusal, "recovery_window_expired_or_future"),
        ):
            self.store.begin_recovery(envelope)
        self.assertFalse(self.store.recovery_path.exists())
        self.assertFalse(self.store.recoveries.exists())

    def test_recovery_commit_after_deadline_cannot_publish_history(self):
        origin, plan, envelope, proofs = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        with patch.object(recovery, "datetime") as clock:
            clock.now.return_value = datetime.fromisoformat(plan["deadline_utc"]) + timedelta(
                seconds=1
            )
            with self.assertRaisesRegex(io.Refusal, "recovery_window_expired_or_future"):
                self.store.commit_recovery(envelope, proofs)
        self.assertEqual(self.store.pending(), origin)
        self.assertFalse(self.store.head_path.exists())
        self.assertFalse(self.store.history.exists())

    def test_recovery_finish_rechecks_foreign_pending_after_terminal_pointer_removal(self):
        _, _, envelope, proofs = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        self.store.commit_recovery(envelope, proofs)
        original = state._sync_directory

        def reappear(path):
            original(path)
            if not self.store.pending_path.exists() and not self.store.recovery_path.exists():
                io.write_new(self.store.pending_path, self.plan())

        with (
            patch.object(state, "_sync_directory", side_effect=reappear),
            self.assertRaisesRegex(io.Refusal, "state_terminal_pending_changed"),
        ):
            self.store.finish_recovery(envelope)
        self.assertTrue(self.store.pending_path.exists())

    def test_recovery_v2_history_rejects_unknown_fields_even_with_matching_head_hash(self):
        _, _, envelope, proofs = self.recovery_fixture()
        self.store.begin_recovery(envelope)
        reference = self.store.commit_recovery(envelope, proofs)
        path = Path(reference["path"])
        record = io.document(path.read_bytes())
        record["accepted"] = True
        path.write_bytes(io.canonical(record))
        io.atomic_replace(
            self.store.head_path,
            {"schema": state.HEAD_SCHEMA, "sequence": 1, "sha256": io.reference(path)["sha256"]},
        )
        with self.assertRaisesRegex(io.Refusal, "commit_fields"):
            self.store.head()


if __name__ == "__main__":
    unittest.main()
