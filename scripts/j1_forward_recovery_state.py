"""Explicit recovery transactions; historical pending bytes remain immutable.

The caller validates native ownership, signed acceptance and actual runtime
effects. This store verifies their immutable reference bytes and transaction
bindings; a stored reference alone never establishes substantive acceptance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from scripts import j1_forward_io as io

RECOVERY_SCHEMA = "autostop.j1.crm-forward-recovery.v2"
COMMIT_SCHEMA = "autostop.j1.crm-forward-commit.v2"
REFERENCE_FIELDS = frozenset(
    {
        "origin_pending_ref",
        "origin_plan_ref",
        "origin_closure_ref",
        "origin_scope_ref",
        "origin_failure_ref",
        "origin_bundle_ref",
        "plan_ref",
        "recovery_code_ref",
        "ci_ref",
        "hold_ref",
    }
)
FIELDS = REFERENCE_FIELDS | {
    "schema",
    "recovery_id",
    "nonce",
    "deployment_lock_identity",
}
ORIGIN_FIELDS = (REFERENCE_FIELDS - {"plan_ref"}) | {"nonce", "deployment_lock_identity"}
MAX_WINDOW = 600


def validate_envelope(envelope, base_sha256):
    """Read retained evidence, never the possibly consumed live pending file."""
    from scripts import j1_forward_state as state

    io.require(type(envelope) is dict and set(envelope) == FIELDS, "recovery_fields")
    io.require(envelope["schema"] == RECOVERY_SCHEMA, "recovery_schema")
    io.require(state._hash(envelope["nonce"]), "recovery_nonce")
    identity = envelope["deployment_lock_identity"]
    io.require(
        type(identity) is list
        and len(identity) == 2
        and all(type(value) is int and 0 <= value < 2**64 for value in identity)
        and identity[1] > 0,
        "recovery_lock_identity",
    )
    documents = {}
    for name in REFERENCE_FIELDS:
        state._reference(envelope[name])
        if name != "origin_pending_ref":
            documents[name] = io.referenced(envelope[name])
    origin, plan = (io.document(documents[name]) for name in ("origin_plan_ref", "plan_ref"))
    for value in (origin, plan):
        state.validate_plan(value, base_sha256)
    io.require(
        envelope["recovery_id"] == plan["attempt_id"] != origin["attempt_id"]
        and all(
            plan[key] == origin[key]
            for key in ("base_sha256", "host", "sequence", "predecessor_sha256")
        )
        and envelope["origin_pending_ref"]["sha256"] == envelope["origin_plan_ref"]["sha256"]
        and io.canonical(origin) == documents["origin_plan_ref"]
        and io.canonical(plan) == documents["plan_ref"],
        "recovery_plan_binding",
    )
    start, end = state._timestamp(plan["created_at"]), state._timestamp(plan["deadline_utc"])
    io.require(
        state._timestamp(origin["created_at"]) <= start
        and 0 < (end - start).total_seconds() <= MAX_WINDOW,
        "recovery_window_invalid",
    )
    io.referenced(origin["baseline_ref"])
    baseline = io.document(io.referenced(plan["baseline_ref"]))
    io.require(
        type(baseline) is dict
        and baseline.get("recovery_origin") == {key: envelope[key] for key in ORIGIN_FIELDS},
        "recovery_baseline_origin_binding",
    )
    return origin, plan


def _live(plan):
    from scripts import j1_forward_state as state

    io.require(
        state._timestamp(plan["created_at"])
        <= datetime.now(UTC)
        < state._timestamp(plan["deadline_utc"]),
        "recovery_window_expired_or_future",
    )


class RecoveryStateMixin:
    """Methods share Store's real filesystem lock and append-only HEAD chain."""

    @property
    def recoveries(self):
        return self.root / "recoveries"

    @property
    def recovery_path(self):
        return self.root / "active-recovery.json"

    def _no_recovery(self):
        from scripts.j1_forward_state import _exists

        io.require(not _exists(self.recovery_path), "state_recovery_active")

    def _envelope(self, envelope):
        origin, plan = validate_envelope(envelope, self.base_sha256)
        attempts = self.root.parent / "attempts"
        old = attempts / origin["attempt_id"]
        new = attempts / plan["attempt_id"]
        retained = self.root.parent / "origins" / origin["attempt_id"]
        paths = {
            "origin_pending_ref": self.pending_path,
            "origin_plan_ref": old / "plan.json",
            "origin_closure_ref": old / "closure-owned.json",
            "origin_scope_ref": old / "process-scope.json",
            "origin_failure_ref": old / "failure.json",
            "origin_bundle_ref": retained / "bundle.json",
            "plan_ref": new / "plan.json",
        }
        io.require(
            all(envelope[key]["path"] == str(path) for key, path in paths.items())
            and origin["baseline_ref"]["path"] == str(old / "baseline.json")
            and plan["baseline_ref"]["path"] == str(new / "baseline.json"),
            "recovery_fixed_paths",
        )
        io.referenced(
            {
                "path": str(retained / "pending.json"),
                "sha256": envelope["origin_pending_ref"]["sha256"],
            }
        )
        for name in ("recovery_code_ref", "ci_ref", "hold_ref"):
            io.require(Path(envelope[name]["path"]).is_relative_to(new), "recovery_evidence_path")
        return origin, plan

    def _recovery_ref(self, envelope):
        return {
            "path": str(self.recoveries / (envelope["recovery_id"] + ".json")),
            "sha256": io.sha(io.canonical(envelope)),
        }

    def _archive_ref(self, envelope):
        return {
            "path": str(self.recoveries / (envelope["recovery_id"] + ".pending.json")),
            "sha256": envelope["origin_pending_ref"]["sha256"],
        }

    def _retained_recovery(self, envelope):
        origin, plan = self._envelope(envelope)
        io.require(
            io.document(io.referenced(self._recovery_ref(envelope))) == envelope,
            "recovery_record_changed",
        )
        io.require(
            io.document(io.referenced(self._archive_ref(envelope))) == origin,
            "recovery_pending_archive_changed",
        )
        return origin, plan

    def _active_recovery(self):
        from scripts.j1_forward_state import _exists, _reference

        if not _exists(self.recovery_path):
            return None
        reference = io.document(io.read_protected(self.recovery_path))
        _reference(reference)
        path = Path(reference["path"])
        io.require(
            path.parent == self.recoveries and path.suffix == ".json", "recovery_pointer_path"
        )
        envelope = io.document(io.referenced(reference))
        self._envelope(envelope)
        io.require(self._recovery_ref(envelope) == reference, "recovery_pointer_binding")
        self._retained_recovery(envelope)
        return envelope

    def _matching_recovery(self, envelope, *, pending=True):
        io.require(self._active_recovery() == envelope, "state_recovery_mismatch")
        origin, plan = self._retained_recovery(envelope)
        if pending:
            self._matching_pending(origin)
            io.require(
                io.reference(self.pending_path) == envelope["origin_pending_ref"],
                "recovery_pending_bytes_changed",
            )
        return origin, plan

    def _recovery_record(self, record):
        envelope = io.document(io.referenced(record["recovery_ref"]))
        origin, plan = self._retained_recovery(envelope)
        io.require(
            record["recovery_ref"] == self._recovery_ref(envelope)
            and record["origin_plan"] == origin
            and record["plan"] == plan,
            "recovery_commit_binding",
        )
        return origin["attempt_id"]

    def active_plan(self):
        """Return effective pending identity, without granting liveness/acceptance."""
        with self._locked() as present:
            if not present:
                return None
            envelope = self._active_recovery()
            if envelope is None:
                plan = self._pending()
                return (
                    None
                    if plan is None
                    else {"plan": plan, "origin_plan_ref": None, "recovery_ref": None}
                )
            origin, plan = self._matching_recovery(envelope, pending=False)
            pending = self._pending()
            if pending is None:
                self._committed_recovery(envelope)
            else:
                io.require(
                    pending == origin
                    and io.reference(self.pending_path) == envelope["origin_pending_ref"],
                    "recovery_pending_bytes_changed",
                )
            return {
                "plan": plan,
                "origin_plan_ref": envelope["origin_plan_ref"],
                "recovery_ref": self._recovery_ref(envelope),
            }

    def begin_recovery(self, envelope):
        from scripts.j1_forward_state import MAX_HISTORY, _exists, _sync_directory, record_attempts

        origin, plan = self._envelope(envelope)
        _live(plan)
        with self._locked():
            _live(plan)
            self._matching_pending(origin)
            io.require(
                io.reference(self.pending_path) == envelope["origin_pending_ref"],
                "recovery_pending_bytes_changed",
            )
            head, records = self._snapshot()
            io.require(
                len(records) < MAX_HISTORY
                and plan["sequence"] == head["sequence"] + 1
                and plan["predecessor_sha256"] == head["sha256"]
                and not {origin["attempt_id"], plan["attempt_id"]} & record_attempts(records),
                "recovery_predecessor_or_replay",
            )
            active = self._active_recovery()
            io.require(active is None or active == envelope, "state_recovery_mismatch")
            if not _exists(self.recoveries):
                self.recoveries.mkdir(mode=0o700)
                _sync_directory(self.root)
            self._durable(envelope["origin_pending_ref"])
            for reference, value in (
                (self._archive_ref(envelope), origin),
                (self._recovery_ref(envelope), envelope),
            ):
                if not _exists(Path(reference["path"])):
                    io.write_new(Path(reference["path"]), value)
                self._durable(reference)
            if active is None:
                _live(plan)
                io.write_new(self.recovery_path, self._recovery_ref(envelope))
            self._durable(io.reference(self.recovery_path))
            self._matching_recovery(envelope)
            _live(plan)
            return self._recovery_ref(envelope)

    def _fresh_recovery_proofs(self, envelope, proofs):
        from scripts.j1_forward_state import _proofs

        _proofs(proofs)
        directory = Path(envelope["plan_ref"]["path"]).parent
        excluded = {envelope[key]["sha256"] for key in REFERENCE_FIELDS}
        io.require(
            len({ref["path"] for ref in proofs.values()}) == len(proofs)
            and len({ref["sha256"] for ref in proofs.values()}) == len(proofs)
            and all(
                Path(ref["path"]).is_relative_to(directory) and ref["sha256"] not in excluded
                for ref in proofs.values()
            ),
            "recovery_new_proofs_required",
        )
        for reference in proofs.values():
            self._durable(reference)

    def commit_recovery(self, envelope, proofs):
        from scripts.j1_forward_state import MAX_HISTORY, _exists, _sync_directory, record_attempts

        with self._locked():
            origin, plan = self._matching_recovery(envelope)
            head, records = self._snapshot()
            self._fresh_recovery_proofs(envelope, proofs)
            if head["record"] is not None and head["record"]["plan"] == plan:
                io.require(
                    head["record"].get("recovery_ref") == self._recovery_ref(envelope)
                    and head["record"]["proofs"] == proofs,
                    "state_committed_proofs_mismatch",
                )
                return self._committed_recovery(envelope)
            _live(plan)
            io.require(
                len(records) < MAX_HISTORY
                and plan["sequence"] == head["sequence"] + 1
                and plan["predecessor_sha256"] == head["sha256"]
                and not {origin["attempt_id"], plan["attempt_id"]} & record_attempts(records),
                "recovery_predecessor_or_replay",
            )
            for reference in (
                envelope["origin_pending_ref"],
                self._archive_ref(envelope),
                self._recovery_ref(envelope),
                io.reference(self.recovery_path),
            ):
                self._durable(reference)
            if not _exists(self.history):
                self.history.mkdir(mode=0o700)
                _sync_directory(self.root)
            record = {
                "schema": COMMIT_SCHEMA,
                "plan": plan,
                "origin_plan": origin,
                "recovery_ref": self._recovery_ref(envelope),
                "proofs": proofs,
                "previous_sha256": head["sha256"],
                "sequence": plan["sequence"],
            }
            _live(plan)
            reference = io.write_new(self.history / f"{plan['sequence']:020d}.json", record)
            self._publish_head(record, reference)
            return self._committed_recovery(envelope)

    def _committed_recovery(self, envelope):
        origin, plan = self._retained_recovery(envelope)
        head, records = self._snapshot()
        record = head["record"]
        io.require(
            record is not None
            and record["schema"] == COMMIT_SCHEMA
            and record["plan"] == plan
            and record["origin_plan"] == origin
            and record["recovery_ref"] == self._recovery_ref(envelope),
            "state_recovery_uncommitted",
        )
        self._fresh_recovery_proofs(envelope, record["proofs"])
        for reference in (
            self._archive_ref(envelope),
            self._recovery_ref(envelope),
            records[-1][1],
            io.reference(self.head_path),
        ):
            self._durable(reference)
        io.require(self._snapshot()[0] == head, "state_recovery_head_changed")
        return records[-1][1]

    def recover_recovery(self, envelope):
        """Repair only an already published v2 commit; never invent acceptance."""
        with self._locked():
            origin, plan = self._matching_recovery(envelope)
            records = self._records()
            io.require(bool(records), "state_recovery_commit_missing")
            record, reference = records[-1]
            io.require(
                record["schema"] == COMMIT_SCHEMA
                and record["plan"] == plan
                and record["origin_plan"] == origin
                and record["recovery_ref"] == self._recovery_ref(envelope),
                "state_recovery_plan_mismatch",
            )
            current = {"sequence": record["sequence"], "sha256": reference["sha256"]}
            previous = {"sequence": record["sequence"] - 1, "sha256": record["previous_sha256"]}
            io.require(self._head_record() in (current, previous), "state_recovery_head_mismatch")
            self._fresh_recovery_proofs(envelope, record["proofs"])
            for ref in (
                envelope["origin_pending_ref"],
                self._archive_ref(envelope),
                self._recovery_ref(envelope),
                io.reference(self.recovery_path),
                reference,
            ):
                self._durable(ref)
            self._publish_head(record, reference)
            return self._committed_recovery(envelope)

    def finish_recovery(self, envelope):
        """Consume old pending last; interrupted cleanup is exact and retryable."""
        from scripts.j1_forward_state import _exists, _sync_directory

        with self._locked():
            active = self._active_recovery()
            io.require(active is None or active == envelope, "state_recovery_mismatch")
            reference = self._committed_recovery(envelope)
            if _exists(self.pending_path):
                io.require(active == envelope, "state_recovery_mismatch")
                self._matching_recovery(envelope)
                self._durable(envelope["origin_pending_ref"])
                self.pending_path.unlink()
            _sync_directory(self.root)
            io.require(self._pending() is None, "state_terminal_pending_changed")
            if active is not None:
                self.recovery_path.unlink()
            _sync_directory(self.root)
            self._no_recovery()
            io.require(self._pending() is None, "state_terminal_pending_changed")
            io.require(
                self._committed_recovery(envelope) == reference, "state_recovery_head_changed"
            )
            self._no_recovery()
            io.require(self._pending() is None, "state_terminal_pending_changed")
            return reference
