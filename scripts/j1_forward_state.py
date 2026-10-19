"""Append-only CRM successor history; native J1 authority is never rewritten.

The caller owns the existing deployment lock and validates the substantive
acceptance proofs. This module serializes its own filesystem transactions and
only consumes records at fixed paths beneath its explicitly selected root.
"""

from __future__ import annotations

import os
import re
import stat
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from scripts import j1_forward_io as io
from scripts import j1_forward_recovery_state as recovery_state

PLAN_SCHEMA = "autostop.j1.crm-forward-plan.v1"
COMMIT_SCHEMA = "autostop.j1.crm-forward-commit.v1"
HEAD_SCHEMA = "autostop.j1.crm-forward-head.v1"
SCOPE = "crm_only_native_preserved"
PLAN_FIELDS = frozenset(
    {
        "schema",
        "scope",
        "host",
        "attempt_id",
        "sequence",
        "predecessor_sha256",
        "base_sha256",
        "candidate",
        "baseline_ref",
        "created_at",
        "deadline_utc",
        "candidate_may_have_written",
        "old_writer_allowed",
    }
)
PROOF_FIELDS = frozenset(
    {"closure", "backup", "offhost_restore", "native_acceptance", "runtime_acceptance"}
)
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HISTORY_NAME = re.compile(r"([0-9]{20})\.json\Z")
MAX_SEQUENCE = 2**63 - 1
MAX_HISTORY = 10000
LOCK_SECONDS = 10.0


def _hash(value):
    return type(value) is str and HEX64.fullmatch(value) is not None


def _sequence(value):
    return type(value) is int and 0 < value <= MAX_SEQUENCE


def _reference(value):
    io.require(type(value) is dict and set(value) == {"path", "sha256"}, "reference_fields")
    path = value["path"]
    io.require(
        type(path) is str
        and 0 < len(path) <= 4096
        and "\0" not in path
        and (Path(path).is_absolute() or PurePosixPath(path).is_absolute())
        and ".." not in Path(path).parts
        and ".." not in PurePosixPath(path).parts
        and _hash(value["sha256"]),
        "reference_invalid",
    )


def _timestamp(value):
    io.require(type(value) is str and len(value) <= 40, "timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        io.require(False, "timestamp_invalid")
    io.require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), "utc_required")
    return parsed


def validate_plan(plan, base_sha256):
    io.require(type(plan) is dict and set(plan) == PLAN_FIELDS, "plan_fields")
    io.require(plan["schema"] == PLAN_SCHEMA and plan["scope"] == SCOPE, "plan_scope")
    io.require(
        type(plan["host"]) is str
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}", plan["host"]),
        "plan_host",
    )
    try:
        attempt = uuid.UUID(plan["attempt_id"]) if type(plan["attempt_id"]) is str else None
    except ValueError:
        attempt = None
    io.require(attempt is not None and str(attempt) == plan["attempt_id"], "plan_attempt")
    io.require(_sequence(plan["sequence"]), "plan_sequence")
    io.require(_hash(plan["predecessor_sha256"]), "plan_predecessor")
    io.require(plan["base_sha256"] == base_sha256, "plan_base")
    io.require(
        plan["candidate_may_have_written"] is True and plan["old_writer_allowed"] is False,
        "plan_writer_fence",
    )
    candidate = plan["candidate"]
    io.require(
        type(candidate) is dict
        and set(candidate) == {"revision", "tree", "image", "consumer_sha256"},
        "candidate_fields",
    )
    io.require(
        all(
            type(candidate[key]) is str and HEX40.fullmatch(candidate[key])
            for key in ("revision", "tree")
        )
        and type(candidate["image"]) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", candidate["image"])
        and _hash(candidate["consumer_sha256"]),
        "candidate_identity",
    )
    _reference(plan["baseline_ref"])
    io.require(_timestamp(plan["created_at"]) < _timestamp(plan["deadline_utc"]), "plan_deadline")


def _proofs(proofs):
    io.require(type(proofs) is dict and set(proofs) == PROOF_FIELDS, "proof_fields")
    for value in proofs.values():
        _reference(value)


def _directory(path):
    io.protected_directory(path)


def _exists(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _sync_directory(path):
    if os.name == "nt":
        return  # Directory fsync is a native Linux publication guarantee.
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def record_attempts(records):
    return {
        plan["attempt_id"]
        for record, _ in records
        for plan in (record["plan"], *([record["origin_plan"]] if "origin_plan" in record else []))
    }


class Store(recovery_state.RecoveryStateMixin):
    def __init__(self, root, base_sha256):
        self.root = Path(root)
        io.require(self.root.is_absolute() and ".." not in self.root.parts, "state_root_absolute")
        io.require(_hash(base_sha256), "state_base_invalid")
        self.base_sha256 = base_sha256
        self.history = self.root / "history"
        self.head_path = self.root / "HEAD.json"
        self.pending_path = self.root / "pending.json"

    @contextmanager
    def _locked(self, *, create=False):
        if not _exists(self.root):
            if not create:
                yield False
                return
            _directory(self.root.parent)
            self.root.mkdir(mode=0o700, exist_ok=True)
            _sync_directory(self.root.parent)
        _directory(self.root)
        path = self.root / ".store.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        acquired = False
        try:
            info = os.fstat(fd)
            io.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "state_lock_invalid")
            io.require(not path.is_symlink(), "state_lock_invalid")
            if os.name != "nt":
                io.require(
                    info.st_uid == io.ROOT_UID and not info.st_mode & 0o022,
                    "state_lock_unprotected",
                )
                import fcntl
            else:
                import msvcrt
            deadline = time.monotonic() + LOCK_SECONDS
            while True:
                try:
                    if os.name == "nt":
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, PermissionError):
                    io.require(time.monotonic() < deadline, "state_lock_timeout")
                    time.sleep(0.01)
            current = path.lstat()
            io.require(
                (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino),
                "state_lock_replaced",
            )
            if os.name == "nt":
                # Windows permits locking a range beyond EOF. Acquire that
                # range before initializing it: another opener can have seen
                # size zero before this holder wrote its byte and took the lock.
                size = os.fstat(fd).st_size
                io.require(size in (0, 1), "state_lock_size_invalid")
                if size == 0:
                    os.lseek(fd, 0, os.SEEK_SET)
                    io.require(os.write(fd, b"\0") == 1, "state_lock_initialization_failed")
                    os.fsync(fd)
                io.require(os.fstat(fd).st_size == 1, "state_lock_size_invalid")
            yield True
        finally:
            if acquired:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(self, path):
        raw = io.read_protected(path)
        return io.document(raw), {"path": str(path), "sha256": io.sha(raw)}

    def _contains(self, path):
        return _exists(path)

    def _logical_path(self, value):
        return Path(value)

    def _validate_plan(self, plan):
        return validate_plan(plan, self.base_sha256)

    def _reference(self, path):
        return io.reference(path)

    def _read_reference(self, reference):
        return io.referenced(reference)

    def _history_paths(self):
        if not self._contains(self.history):
            return []
        _directory(self.history)
        return sorted(self.history.iterdir())

    def _records(self):
        paths = self._history_paths()
        io.require(len(paths) <= MAX_HISTORY, "state_history_limit")
        previous = self.base_sha256
        attempts = set()
        records = []
        for sequence, path in enumerate(paths, 1):
            match = HISTORY_NAME.fullmatch(path.name)
            io.require(match is not None and int(match[1]) == sequence, "state_history_gap")
            record, ref = self._read(path)
            fields = {"schema", "plan", "proofs", "previous_sha256", "sequence"}
            is_recovery = (
                type(record) is dict and record.get("schema") == recovery_state.COMMIT_SCHEMA
            )
            if is_recovery:
                fields |= {"origin_plan", "recovery_ref"}
            io.require(
                type(record) is dict and set(record) == fields,
                "commit_fields",
            )
            io.require(record["schema"] == COMMIT_SCHEMA or is_recovery, "commit_schema")
            self._validate_plan(record["plan"])
            _proofs(record["proofs"])
            io.require(
                type(record["sequence"]) is int
                and record["sequence"] == sequence
                and record["plan"]["sequence"] == sequence
                and record["previous_sha256"] == previous
                and record["plan"]["predecessor_sha256"] == previous,
                "state_history_chain",
            )
            attempt = record["plan"]["attempt_id"]
            current_attempts = {attempt}
            if is_recovery:
                current_attempts.add(self._recovery_record(record))
            io.require(not current_attempts & attempts, "state_attempt_replayed")
            attempts.update(current_attempts)
            records.append((record, ref))
            previous = ref["sha256"]
        return records

    def _head_record(self):
        if not self._contains(self.head_path):
            return {"sequence": 0, "sha256": self.base_sha256}
        value, _ = self._read(self.head_path)
        io.require(
            type(value) is dict
            and set(value) == {"schema", "sequence", "sha256"}
            and value["schema"] == HEAD_SCHEMA
            and _sequence(value["sequence"])
            and _hash(value["sha256"]),
            "state_head_invalid",
        )
        return {"sequence": value["sequence"], "sha256": value["sha256"]}

    def _snapshot(self):
        records = self._records()
        head = self._head_record()
        expected = records[-1][1]["sha256"] if records else self.base_sha256
        io.require(head == {"sequence": len(records), "sha256": expected}, "state_head_mismatch")
        return {**head, "record": records[-1][0] if records else None}, records

    def head(self):
        with self._locked() as present:
            if not present:
                return {"sequence": 0, "sha256": self.base_sha256, "record": None}
            return self._snapshot()[0]

    def _pending(self):
        if not self._contains(self.pending_path):
            return None
        plan, _ = self._read(self.pending_path)
        self._validate_plan(plan)
        return plan

    def pending(self):
        with self._locked() as present:
            return self._pending() if present else None

    def _matching_pending(self, plan):
        pending = self._pending()
        io.require(
            pending is not None and io.canonical(pending) == io.canonical(plan),
            "state_pending_mismatch",
        )

    def _durable(self, ref):
        """Flush an exact checked inode and directory, then verify the bytes."""
        path = Path(ref["path"])
        before = path.lstat()
        io.require(io.reference(path) == ref, "state_durability_record_changed")
        access = os.O_RDWR if os.name == "nt" else os.O_RDONLY
        descriptor = os.open(path, access | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            io.require(
                (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                "state_durability_record_changed",
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _sync_directory(path.parent)
        after = path.lstat()
        io.require(
            (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
            and io.reference(path) == ref,
            "state_durability_record_changed",
        )
        return ref

    def begin(self, plan):
        self._validate_plan(plan)
        with self._locked(create=True):
            self._no_recovery()
            head, records = self._snapshot()
            pending = self._pending()
            if pending is not None:
                self._matching_pending(plan)
                io.require(
                    (head["record"] is not None and head["record"]["plan"] == plan)
                    or (
                        plan["sequence"] == head["sequence"] + 1
                        and plan["predecessor_sha256"] == head["sha256"]
                    ),
                    "state_pending_predecessor",
                )
                return self._durable(io.reference(self.pending_path))
            io.require(
                plan["attempt_id"] not in record_attempts(records),
                "state_attempt_replayed",
            )
            io.require(len(records) < MAX_HISTORY, "state_history_limit")
            io.require(
                plan["sequence"] == head["sequence"] + 1
                and plan["predecessor_sha256"] == head["sha256"],
                "state_predecessor",
            )
            return io.write_new(self.pending_path, plan)

    def _publish_head(self, record, ref):
        io.atomic_replace(
            self.head_path,
            {
                "schema": HEAD_SCHEMA,
                "sequence": record["sequence"],
                "sha256": ref["sha256"],
            },
        )

    def commit(self, plan, proofs):
        self._validate_plan(plan)
        _proofs(proofs)
        with self._locked(create=True):
            self._no_recovery()
            self._matching_pending(plan)
            head, records = self._snapshot()
            if head["record"] is not None and head["record"]["plan"] == plan:
                io.require(head["record"]["proofs"] == proofs, "state_committed_proofs_mismatch")
                self._durable(io.reference(self.pending_path))
                self._durable(records[-1][1])
                self._durable(io.reference(self.head_path))
                self._snapshot()
                return records[-1][1]
            io.require(len(records) < MAX_HISTORY, "state_history_limit")
            io.require(
                plan["attempt_id"] not in record_attempts(records),
                "state_attempt_replayed",
            )
            io.require(
                plan["sequence"] == head["sequence"] + 1
                and plan["predecessor_sha256"] == head["sha256"],
                "state_predecessor",
            )
            self._durable(io.reference(self.pending_path))
            if not _exists(self.history):
                self.history.mkdir(mode=0o700)
                _sync_directory(self.root)
            record = {
                "schema": COMMIT_SCHEMA,
                "plan": plan,
                "proofs": proofs,
                "previous_sha256": head["sha256"],
                "sequence": plan["sequence"],
            }
            ref = io.write_new(self.history / f"{plan['sequence']:020d}.json", record)
            self._publish_head(record, ref)
            return ref

    def recover(self, plan):
        """Finish only one already-published commit belonging to exact pending."""
        self._validate_plan(plan)
        with self._locked():
            self._no_recovery()
            self._matching_pending(plan)
            records = self._records()
            io.require(bool(records), "state_recovery_commit_missing")
            record, ref = records[-1]
            io.require(
                record["schema"] == COMMIT_SCHEMA and record["plan"] == plan,
                "state_recovery_plan_mismatch",
            )
            head = self._head_record()
            current = {"sequence": record["sequence"], "sha256": ref["sha256"]}
            predecessor = {"sequence": record["sequence"] - 1, "sha256": record["previous_sha256"]}
            io.require(head in (current, predecessor), "state_recovery_head_mismatch")
            # A previous process may have left complete bytes in page cache
            # without completing file or directory durability barriers.
            self._durable(io.reference(self.pending_path))
            self._durable(ref)
            if head == predecessor:
                self._publish_head(record, ref)
            else:
                self._durable(io.reference(self.head_path))
            self._snapshot()
            return ref

    def finish(self, plan):
        self._validate_plan(plan)
        with self._locked():
            self._no_recovery()
            self._matching_pending(plan)
            head, records = self._snapshot()
            io.require(
                head["record"] is not None
                and head["record"]["schema"] == COMMIT_SCHEMA
                and head["record"]["plan"] == plan,
                "state_finish_uncommitted",
            )
            self._durable(records[-1][1])
            self._durable(io.reference(self.head_path))
            self._snapshot()
            self.pending_path.unlink()
            _sync_directory(self.root)

    def confirm_finished(self, plan):
        """Confirm terminal durability after an interrupted acknowledgement."""
        self._validate_plan(plan)
        with self._locked():
            self._no_recovery()
            io.require(self._pending() is None, "state_terminal_pending_present")
            head, records = self._snapshot()
            io.require(
                head["record"] is not None
                and head["record"]["schema"] == COMMIT_SCHEMA
                and head["record"]["plan"] == plan,
                "state_terminal_head_mismatch",
            )
            self._durable(records[-1][1])
            self._durable(io.reference(self.head_path))
            _sync_directory(self.root)
            io.require(self._pending() is None, "state_terminal_pending_changed")
            confirmed, _ = self._snapshot()
            io.require(confirmed == head, "state_terminal_head_changed")
            return records[-1][1]
