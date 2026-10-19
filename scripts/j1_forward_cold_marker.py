"""Retain an exact old maintenance marker or own a new cold marker explicitly.

No old marker is rewritten/adopted. Removal is a separate opening operation,
allowed only against the committed cold lineage and fresh cold acceptance.
"""

from __future__ import annotations

from pathlib import Path

from scripts import j1_forward_io as io
from scripts import j1_forward_marker as marker
from scripts import j1_forward_release as release

SCHEMA = "autostop.j1.cold-maintenance-retention.v1"


def retain(directory, plan_ref, baseline, *, controller, store):
    plan = io.document(io.referenced(plan_ref))
    origin = baseline["recovery_origin"]
    old_plan_ref = origin["origin_plan_ref"]
    old_plan = io.document(io.referenced(old_plan_ref))
    old_baseline = io.document(io.referenced(old_plan["baseline_ref"]))
    old_directory = Path(old_plan_ref["path"]).parent
    controller.verify()
    active = store.active_plan()
    io.require(active is not None and active["plan"] == plan, "cold_marker_active_plan")
    with marker._directory(baseline["maintenance_parent"]) as (descriptor, _pin, _refresh):
        present = marker._entry(descriptor) is not None
    old_pointer = None
    if present:
        record = release.marker_record(old_directory, old_plan_ref, old_baseline)
        payload = release.marker_value(old_plan_ref, old_plan)
        identity = marker.require_owned(
            record["parent"], payload, expected_identity=record["identity"]
        )
        parent = record["parent"]
        old_pointer = io.reference(old_directory / "marker-owned.json")
        retained = True
    else:
        parent, payload = baseline["maintenance_parent"], release.marker_value(plan_ref, plan)

        def authorize():
            controller.verify()
            io.require(store.active_plan()["plan"] == plan, "cold_marker_active_plan")

        identity = marker.ensure(parent, payload, authorize=authorize)
        retained = False
    return io.write_new(
        Path(directory) / "cold-marker-owned.json",
        {
            "schema": SCHEMA,
            "attempt_id": plan["attempt_id"],
            "plan_ref": plan_ref,
            "origin_plan_ref": old_plan_ref,
            "parent": parent,
            "payload": payload,
            "identity": identity,
            "old_bytes_retained": retained,
            "original_ownership_ref": old_pointer,
        },
    )


def require(reference, plan_ref):
    record = io.document(io.referenced(reference))
    io.require(
        set(record)
        == {
            "schema",
            "attempt_id",
            "plan_ref",
            "origin_plan_ref",
            "parent",
            "payload",
            "identity",
            "old_bytes_retained",
            "original_ownership_ref",
        }
        and record["schema"] == SCHEMA
        and record["plan_ref"] == plan_ref
        and type(record["old_bytes_retained"]) is bool,
        "cold_marker_record",
    )
    plan = io.document(io.referenced(plan_ref))
    baseline = io.document(io.referenced(plan["baseline_ref"]))
    io.require(
        record["attempt_id"] == plan["attempt_id"]
        and record["origin_plan_ref"] == baseline["recovery_origin"]["origin_plan_ref"],
        "cold_marker_origin_binding",
    )
    if record["old_bytes_retained"]:
        io.require(record["original_ownership_ref"] is not None, "cold_marker_old_ownership")
        io.referenced(record["original_ownership_ref"])
        old_plan = io.document(io.referenced(record["origin_plan_ref"]))
        old_baseline = io.document(io.referenced(old_plan["baseline_ref"]))
        previous = release.marker_record(
            Path(record["origin_plan_ref"]["path"]).parent, record["origin_plan_ref"], old_baseline
        )
        io.require(
            record["original_ownership_ref"]["path"]
            == str(Path(record["origin_plan_ref"]["path"]).parent / "marker-owned.json")
            and previous["parent"] == record["parent"]
            and previous["identity"] == record["identity"]
            and record["payload"] == release.marker_value(record["origin_plan_ref"], old_plan),
            "cold_marker_preserved_owner_changed",
        )
    else:
        io.require(record["original_ownership_ref"] is None, "cold_marker_foreign_ownership")
        io.require(
            record["parent"] == baseline["maintenance_parent"]
            and record["payload"] == release.marker_value(plan_ref, plan),
            "cold_marker_new_owner_changed",
        )
    marker.require_owned(record["parent"], record["payload"], expected_identity=record["identity"])
    return record


def remove(reference, plan_ref, commit_ref, *, controller, store, http_cut=None):
    record = require(reference, plan_ref)

    def authorize():
        # Removing the gateway marker opens a second ingress. It is never an
        # expiry-safe cleanup or permission to thaw an original native unit.
        controller.verify()
        controller.admitted()
        ready = controller.ready()
        if getattr(getattr(controller, "provider", None), "scope", None) == "production_native":
            from scripts import j1_forward_cold_runtime as runtime

            io.require(callable(http_cut), "cold_marker_http_cut_required")
            plan = io.document(io.referenced(plan_ref))
            spec = io.document(io.referenced(plan["cold_crm_spec_ref"]))
            candidate = {key: plan["candidate"][key] for key in ("image", "revision", "tree")}
            runtime.validate_http_cut(
                http_cut(),
                candidate=candidate,
                attempt=plan["attempt_id"],
                config_ref=spec["http_ingress_ref"],
            )
        head = store.head()
        io.require(
            head["sha256"] == commit_ref["sha256"]
            and head["record"]["plan"] == io.document(io.referenced(plan_ref))
            and ready["ok"] is True,
            "cold_marker_committed_acceptance",
        )

    marker.remove_owned(
        record["parent"],
        record["payload"],
        expected_identity=record["identity"],
        authorize=authorize,
    )
