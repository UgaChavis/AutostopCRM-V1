"""Explicit cold plans over the existing immutable-pending transaction store.

Historical validation uses the package's captured native clock, never the
restoring machine's clock. It grants no freshness, native or dispatch authority.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_io as io
from scripts import j1_forward_recovery_state as recovery
from scripts import j1_forward_state as state

PLAN_SCHEMA = cold.PLAN_SCHEMA
BASELINE_SCHEMA = "autostop.j1.crm-manager-cold-forward-baseline.v1"
RECOVERY_SCHEMA = "autostop.j1.crm-manager-cold-forward-recovery.v1"
PLAN_FIELDS = state.PLAN_FIELDS | {
    "cold_authority_package_ref",
    "dispatch_epoch_ref",
    "candidate_manager_sha",
    "cold_crm_spec_ref",
}
# One existing archive-sized runtime graph, never a growing history cache.
MAX_HISTORICAL_BYTES = cold.MAX_RUNTIME_BYTES + 192 * 1024**2
MAX_HISTORICAL_REFERENCES = 10000


class _HistoricalValidation:
    """One public Store operation's pure graph, with fresh typed reads on reuse."""

    def __init__(self, store, plan_ref, plan):
        self.owner = (os.getpid(), threading.get_ident())
        self.plan_ref = dict(plan_ref)
        self.package_ref = dict(plan["cold_authority_package_ref"])
        package = io.document(store._read_reference(self.package_ref))
        cold.fields(package, cold.PACKAGE_FIELDS, "history_package_fields")
        binding = cold.fields(package["binding"], cold.BINDING_FIELDS, "history_binding_fields")
        self.clock = {"boot_id": binding["boot_id"], "monotonic_ns": binding["not_before_ns"]}
        self.memo = cold._CapturedPackages(self.clock)
        self.active = True
        self.store, self.current = store, None
        self.references, self.bytes = {}, 0

    def check(self, plan_ref=None):
        io.require(
            self.active
            and self.owner == (os.getpid(), threading.get_ident())
            and (plan_ref is None or plan_ref == self.plan_ref),
            "cold_history_operation_owner_or_plan",
        )

    def _read(self, reference, reader):
        self.check()
        cold.reference(reference)
        raw = reader(reference)
        io.require(type(raw) is bytes and io.sha(raw) == reference["sha256"], "cold_history_bytes")
        identity = (reference["sha256"], len(raw))
        previous = self.references.get(reference["path"])
        io.require(previous is None or previous == identity, "cold_history_reference_alias")
        if previous is None:
            io.require(
                len(self.references) < MAX_HISTORICAL_REFERENCES
                and self.bytes + len(raw) <= MAX_HISTORICAL_BYTES,
                "cold_history_operation_budget",
            )
            self.references[reference["path"]] = identity
            self.bytes += len(raw)
        return raw

    def read_reference(self, reference):
        io.require(self.current is not None, "cold_history_reader_inactive")
        return self._read(reference, self.current)

    def read_opaque_reference(self, reference):
        io.require(self.current is not None, "cold_history_reader_inactive")
        return self._read(reference, cold.opaque_reader(self.current))

    def read_runtime_capsule(self, reference):
        io.require(self.current is not None, "cold_history_reader_inactive")
        return self._read(reference, cold.opaque_reader(self.current, capsule=True))

    def validate(self, reference, read_reference, now_ns):
        self.check()
        io.require(
            reference == self.package_ref and now_ns == self.clock["monotonic_ns"],
            "cold_history_operation_binding",
        )
        io.require(self.current is None, "cold_history_reader_active")
        self.current = read_reference
        try:
            return self.memo.validate(
                reference,
                read_reference=self.read_reference,
                required_scope=None,
                now_ns=now_ns,
            )
        finally:
            self.current = None

    def recheck(self):
        self.check()
        self.validate(self.package_ref, self.store._read_reference, self.clock["monotonic_ns"])

    def close(self):
        self.active = False
        self.current = self.store = None
        self.memo._reader.seen.clear()
        self.memo._results.clear()
        self.memo = None
        self.references.clear()
        self.bytes = 0


def is_cold(plan):
    return type(plan) is dict and plan.get("schema") == PLAN_SCHEMA


def validate_plan(plan, base):
    io.require(type(plan) is dict and set(plan) == PLAN_FIELDS, "cold_plan_fields")
    io.require(
        plan["schema"] == PLAN_SCHEMA and plan["scope"] == cold.PLAN_SCOPE, "cold_plan_scope"
    )
    projection = {key: plan[key] for key in state.PLAN_FIELDS}
    projection.update(schema=state.PLAN_SCHEMA, scope=state.SCOPE)
    state.validate_plan(projection, base)
    for key in ("cold_authority_package_ref", "dispatch_epoch_ref", "cold_crm_spec_ref"):
        state._reference(plan[key])
    io.require(
        type(plan["candidate_manager_sha"]) is str
        and cold.SHA1.fullmatch(plan["candidate_manager_sha"]),
        "cold_plan_manager_revision",
    )


def historical_package(reference, *, read_reference):
    raw = read_reference(reference)
    io.require(io.sha(raw) == reference["sha256"], "cold_history_package_changed")
    package = io.document(raw)
    io.require(
        type(package) is dict and type(package.get("binding")) is dict, "cold_history_package"
    )
    # This is a captured-clock structural check, not a lease-renewal operation.
    operation = getattr(getattr(read_reference, "__self__", None), "_historical_validation", None)
    if operation is not None:
        operation.check()
        if reference == operation.package_ref:
            return operation.validate(
                reference, read_reference, package["binding"].get("not_before_ns")
            )
    return cold.validate_package(
        reference,
        read_reference=read_reference,
        now_ns=package["binding"].get("not_before_ns"),
    )


def _origin_references(baseline, *, read_reference):
    io.require(baseline.get("schema") == BASELINE_SCHEMA, "cold_recovery_baseline_schema")
    origin = baseline["recovery_origin"]
    io.require(set(origin) == recovery.ORIGIN_FIELDS, "cold_recovery_origin_fields")
    for name in recovery.REFERENCE_FIELDS - {"plan_ref", "origin_pending_ref"}:
        state._reference(origin[name])
        read_reference(origin[name])


def _origin_binding(baseline, value):
    origin = baseline["recovery_origin"]
    package, anchor = value["package"], value["history"]["anchor"]
    io.require(package["binding"]["attempt_id"] == baseline["attempt_id"], "cold_recovery_attempt")
    for name, old in (
        ("origin_plan_ref", "plan_ref"),
        ("origin_scope_ref", "scope_ref"),
        ("origin_closure_ref", "closure_ref"),
    ):
        io.require(origin[name] == anchor[old], "cold_recovery_preserved_anchor")
    io.require(
        origin["origin_pending_ref"]["sha256"] == anchor["plan_ref"]["sha256"]
        and origin["hold_ref"]["sha256"] == anchor["original_hold_ref"]["sha256"]
        and origin["deployment_lock_identity"] == package["binding"]["deployment_lock_identity"],
        "cold_recovery_pending_hold_lock",
    )
    return value


def validate_origin(baseline, *, read_reference):
    _origin_references(baseline, read_reference=read_reference)
    value = historical_package(
        baseline["native"]["cold_authority_package_ref"], read_reference=read_reference
    )
    return _origin_binding(baseline, value)


def envelope(store, document):
    """Reuse durable Store mechanics with a separate cold semantic boundary."""
    io.require(type(document) is dict and set(document) == recovery.FIELDS, "cold_recovery_fields")
    if document.get("schema") != RECOVERY_SCHEMA:
        return recovery.RecoveryStateMixin._envelope(store, document)
    origin, plan = recovery.RecoveryStateMixin._envelope(
        store, {**document, "schema": recovery.RECOVERY_SCHEMA}
    )
    io.require(is_cold(plan), "cold_recovery_plan_required")
    baseline = io.document(store._read_reference(plan["baseline_ref"]))
    io.require(
        baseline["native"]["cold_authority_package_ref"] == plan["cold_authority_package_ref"]
        and baseline["native"]["dispatch_epoch_ref"] == plan["dispatch_epoch_ref"],
        "cold_recovery_native_refs",
    )
    value = validate_origin(baseline, read_reference=store._read_reference)
    binding = value["package"]["binding"]
    io.require(
        binding["candidate_crm_sha"] == plan["candidate"]["revision"]
        and binding["candidate_manager_sha"] == plan["candidate_manager_sha"]
        and value["history"]["anchor"]["plan_ref"] == document["origin_plan_ref"],
        "cold_recovery_candidate_anchor",
    )
    return origin, plan


class ColdStore(state.Store):
    def _history_owner(self):
        operation = getattr(self, "_historical_validation", None)
        if operation is not None:
            operation.check()

    @contextmanager
    def _locked(self, *, create=False):
        self._history_owner()
        with super()._locked(create=create) as present:
            yield present

    @contextmanager
    def historical_validation(self, plan_ref):
        """Share only immutable historical semantics inside one public call.

        Mutable State/HEAD/proofs retain their actual reads and fsync barriers.
        No current native observation receives this memo or a historical clock.
        """
        self._history_owner()
        operation = getattr(self, "_historical_validation", None)
        if operation is not None:
            operation.check(plan_ref)
            yield
            return
        plan = io.document(self._read_reference(plan_ref))
        self._validate_plan(plan)
        if not is_cold(plan):
            yield
            return
        operation = _HistoricalValidation(self, plan_ref, plan)
        self._historical_validation = operation
        try:
            try:
                yield
            except BaseException:
                try:
                    operation.recheck()
                except BaseException:
                    pass  # Preserve the original refusal; it cannot return success.
                raise
            else:
                operation.recheck()
        finally:
            operation.close()
            self.__dict__.pop("_historical_validation", None)

    def _read_reference(self, reference):
        cold.reference(reference)
        raw = io.read_protected(reference["path"], cold.MAX_SOURCE_BYTES)
        io.require(io.sha(raw) == reference["sha256"], "cold_state_reference_hash")
        return raw

    def read_opaque_reference(self, reference):
        cold.reference(reference)
        raw = io.read_protected(reference["path"], cold.MAX_RUNTIME_BYTES)
        io.require(io.sha(raw) == reference["sha256"], "cold_state_reference_hash")
        return cold.runtime_bytes(reference, raw)

    def read_runtime_capsule(self, reference):
        cold.reference(reference)
        raw = io.read_protected(reference["path"], 192 * 1024**2)
        io.require(io.sha(raw) == reference["sha256"], "cold_state_reference_hash")
        return cold.runtime_bytes(reference, raw, capsule=True)

    def _mutation(self, method, *args):
        from scripts import j1_forward_cold_projection

        with j1_forward_cold_projection.close_before_mutation(self):
            return method(*args)

    def begin(self, plan):
        return self._mutation(super().begin, plan)

    def commit(self, plan, proofs):
        return self._mutation(super().commit, plan, proofs)

    def recover(self, plan):
        return self._mutation(super().recover, plan)

    def begin_recovery(self, envelope):
        return self._mutation(super().begin_recovery, envelope)

    @staticmethod
    def _recovery_plan_ref(envelope):
        io.require(
            type(envelope) is dict and set(envelope) == recovery.FIELDS,
            "cold_recovery_fields",
        )
        return envelope["plan_ref"]

    def _historical_recovery(self, method, envelope, *args):
        with self.historical_validation(self._recovery_plan_ref(envelope)):
            return method(envelope, *args)

    def commit_recovery(self, envelope, proofs):
        return self._mutation(self._historical_recovery, super().commit_recovery, envelope, proofs)

    def recover_recovery(self, envelope):
        return self._mutation(self._historical_recovery, super().recover_recovery, envelope)

    def finish_recovery(self, envelope):
        # Finishing consumes pending pointers, never mutates HEAD or closes OPEN.
        with self.historical_validation(self._recovery_plan_ref(envelope)):
            return super().finish_recovery(envelope)

    def _validate_plan(self, plan):
        from scripts import j1_forward_variant

        return j1_forward_variant.validate_plan(plan, self.base_sha256)

    def _envelope(self, document):
        self._history_owner()
        return envelope(self, document)

    def _records(self):
        records, cold_seen = super()._records(), False
        for record, _reference in records:
            current = is_cold(record["plan"])
            io.require(not cold_seen or current, "cold_authority_downgrade")
            cold_seen |= current
        return records
