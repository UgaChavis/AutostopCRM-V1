"""Explicit cold plans over the existing immutable-pending transaction store.

Historical validation uses the package's captured native clock, never the
restoring machine's clock. It grants no freshness, native or dispatch authority.
"""

from __future__ import annotations

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
    return cold.validate_package(
        reference,
        read_reference=read_reference,
        now_ns=package["binding"].get("not_before_ns"),
    )


def validate_origin(baseline, *, read_reference):
    io.require(baseline.get("schema") == BASELINE_SCHEMA, "cold_recovery_baseline_schema")
    origin, native = baseline["recovery_origin"], baseline["native"]
    io.require(set(origin) == recovery.ORIGIN_FIELDS, "cold_recovery_origin_fields")
    for name in recovery.REFERENCE_FIELDS - {"plan_ref", "origin_pending_ref"}:
        state._reference(origin[name])
        read_reference(origin[name])
    value = historical_package(native["cold_authority_package_ref"], read_reference=read_reference)
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

    def commit_recovery(self, envelope, proofs):
        return self._mutation(super().commit_recovery, envelope, proofs)

    def recover_recovery(self, envelope):
        return self._mutation(super().recover_recovery, envelope)

    def _validate_plan(self, plan):
        from scripts import j1_forward_variant

        return j1_forward_variant.validate_plan(plan, self.base_sha256)

    def _envelope(self, document):
        return envelope(self, document)

    def _records(self):
        records, cold_seen = super()._records(), False
        for record, _reference in records:
            current = is_cold(record["plan"])
            io.require(not cold_seen or current, "cold_authority_downgrade")
            cold_seen |= current
        return records
