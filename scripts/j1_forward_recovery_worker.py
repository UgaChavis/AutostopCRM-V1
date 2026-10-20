"""Independent workstation validation of explicit failed-attempt recovery evidence."""

from __future__ import annotations

import copy
import re
from pathlib import Path

from scripts import j1_forward_io as io
from scripts import j1_forward_state as state


def validate_response(response):
    """Verify an explicit successor transaction without reinterpreting old liveness."""
    from scripts import j1_forward_process_scope as prior
    from scripts import j1_forward_recovery_scope as policy
    from scripts import j1_forward_recovery_state as recovery_state
    from scripts.j1_forward_worker import ATTEMPTS, _bound, _hash, _reference, _utc, require

    recovery, baseline, plan = (response[key] for key in ("recovery", "baseline", "plan"))
    require(
        type(recovery) is dict and set(recovery) == {"envelope", "envelope_ref", "evidence"},
        "worker_recovery_response",
    )
    envelope, documents = recovery["envelope"], recovery["evidence"]
    _bound(envelope, recovery["envelope_ref"])
    require(
        type(envelope) is dict
        and set(envelope) == recovery_state.FIELDS
        and envelope["schema"] == recovery_state.RECOVERY_SCHEMA
        and envelope["recovery_id"] == plan["attempt_id"]
        and _hash(envelope["nonce"])
        and envelope["plan_ref"] == response["plan_ref"]
        and recovery["envelope_ref"]["path"]
        == ATTEMPTS.rsplit("/", 1)[0] + "/state/recoveries/" + plan["attempt_id"] + ".json",
        "worker_recovery_envelope",
    )
    reference_names = recovery_state.REFERENCE_FIELDS - {"plan_ref"}
    require(
        type(documents) is dict
        and set(documents)
        == {name.removesuffix("_ref") for name in reference_names}
        | {"origin_baseline", "origin_failure", "hold_status"},
        "worker_recovery_documents",
    )
    for name in reference_names:
        _bound(documents[name.removesuffix("_ref")], envelope[name])
    origin, scope = documents["origin_plan"], response["evidence"]["process_scope"]
    _reference(origin.get("baseline_ref"))
    shape = copy.deepcopy(origin)
    shape["baseline_ref"]["path"] = str(Path(__file__).absolute())
    state.validate_plan(shape, plan["base_sha256"])
    old_prefix, prefix = (
        ATTEMPTS + "/" + origin["attempt_id"] + "/",
        ATTEMPTS + "/" + plan["attempt_id"] + "/",
    )
    require(
        origin["attempt_id"] == policy.ORIGIN_ATTEMPT != plan["attempt_id"]
        and all(
            plan[key] == origin[key]
            for key in ("base_sha256", "host", "sequence", "predecessor_sha256")
        )
        and documents["origin_pending"] == origin
        and envelope["origin_pending_ref"]["path"]
        == ATTEMPTS.rsplit("/", 1)[0] + "/state/pending.json"
        and envelope["origin_pending_ref"]["sha256"] == envelope["origin_plan_ref"]["sha256"]
        and envelope["origin_plan_ref"] == policy.ORIGIN_REFS["plan"]
        and envelope["origin_closure_ref"] == policy.ORIGIN_REFS["closure"]
        and envelope["origin_failure_ref"] == policy.ORIGIN_REFS["failure"]
        and envelope["origin_scope_ref"] == policy.ORIGIN_SCOPE_REF
        and origin["baseline_ref"]["path"] == old_prefix + "baseline.json"
        and _utc(origin["created_at"]) <= _utc(plan["created_at"])
        and 0 < (_utc(plan["deadline_utc"]) - _utc(plan["created_at"])).total_seconds() <= 600,
        "worker_recovery_origin_binding",
    )
    identity = envelope["deployment_lock_identity"]
    require(
        type(identity) is list
        and len(identity) == 2
        and all(type(value) is int and 0 <= value < 2**64 for value in identity)
        and identity[1] > 0
        and identity == baseline["deployment_lock_identity"],
        "worker_recovery_lock_identity",
    )
    expected_origin = {
        key: envelope[key]
        for key in (
            "nonce",
            "origin_pending_ref",
            "origin_plan_ref",
            "origin_closure_ref",
            "origin_scope_ref",
            "origin_bundle_ref",
            "recovery_code_ref",
            "ci_ref",
            "hold_ref",
            "deployment_lock_identity",
        )
    }
    expected_origin["origin_failure_ref"] = policy.ORIGIN_REFS["failure"]
    require(baseline["recovery_origin"] == expected_origin, "worker_recovery_baseline_origin")
    _bound(documents["origin_baseline"], origin["baseline_ref"])
    require(
        identity == documents["origin_baseline"].get("deployment_lock_identity"),
        "worker_recovery_original_lock_identity",
    )
    _bound(documents["origin_failure"], expected_origin["origin_failure_ref"])
    require(
        documents["origin_failure"].get("plan_ref") == envelope["origin_plan_ref"]
        and documents["origin_failure"].get("ok") is False
        and documents["origin_failure"].get("production_restore_attempted") is False,
        "worker_recovery_failure_origin",
    )
    policy.validate_scope_shape(scope)
    require(
        scope["origin_scope"] == documents["origin_scope"]
        and scope["origin_scope_ref"] == envelope["origin_scope_ref"]
        and scope["recovery_origin_refs"] == policy.ORIGIN_REFS,
        "worker_recovery_scope_origin",
    )
    old_native = documents["origin_baseline"].get("native")
    require(
        type(old_native) is dict
        and documents["origin_baseline"].get("schema") == "autostop.j1.crm-forward-baseline.v1"
        and documents["origin_baseline"].get("attempt_id") == origin["attempt_id"]
        and old_native.get("base_sha256")
        == baseline["native"].get("base_sha256")
        == plan["base_sha256"]
        and documents["origin_scope"]["native_sha256"] == io.sha(io.canonical(old_native))
        and documents["origin_scope"]["consumer_sha256"] == origin["candidate"]["consumer_sha256"]
        and baseline["native"]["historical_warm_actors"] == old_native["warm_actors"],
        "worker_recovery_native_origin",
    )
    old_closure = documents["origin_closure"]
    require(
        old_closure.get("attempt_id") == origin["attempt_id"]
        and old_closure.get("attempt_dir") == old_prefix.rstrip("/")
        and old_closure.get("process_scope_ref") == envelope["origin_scope_ref"]
        and old_closure.get("consumer_sha256") == origin["candidate"]["consumer_sha256"]
        and old_closure.get("baseline_sha256") == documents["origin_scope"]["native_sha256"]
        and old_closure.get("freeze_actors") == documents["origin_scope"]["freeze_actors"]
        and old_closure.get("warm_actors") == old_native["warm_actors"],
        "worker_recovery_closure_origin",
    )
    bundle, code = documents["origin_bundle"], documents["recovery_code"]
    bundle_prefix = ATTEMPTS.rsplit("/", 1)[0] + "/origins/" + origin["attempt_id"] + "/"
    require(
        set(bundle)
        == {
            "schema",
            "origin_attempt_id",
            "origin_plan_ref",
            "origin_baseline_ref",
            "source_revision",
            "source_tree",
            "source_consumers",
            "consumer_sha256",
            "files",
        }
        and bundle["schema"] == "autostop.j1.crm-forward-origin-bundle.v2"
        and envelope["origin_bundle_ref"]["path"] == bundle_prefix + "bundle.json"
        and bundle["origin_attempt_id"] == origin["attempt_id"]
        and bundle["origin_plan_ref"] == envelope["origin_plan_ref"]
        and bundle["origin_baseline_ref"] == origin["baseline_ref"]
        and bundle["source_revision"] == origin["candidate"]["revision"]
        and bundle["source_tree"] == origin["candidate"]["tree"]
        and bundle["consumer_sha256"] == origin["candidate"]["consumer_sha256"]
        and bundle["source_consumers"] == documents["origin_baseline"]["source_consumers"]
        and io.sha(io.canonical(bundle["source_consumers"])) == bundle["consumer_sha256"]
        and type(bundle["files"]) is dict
        and set(bundle["files"]) == set(bundle["source_consumers"]),
        "worker_recovery_origin_bundle",
    )
    for name, digest in bundle["source_consumers"].items():
        require(
            re.fullmatch(r"scripts/j1_forward_[a-z_]+\.py", name) is not None
            and _hash(digest)
            and bundle["files"][name]
            == {"path": bundle_prefix + "consumers/" + name, "sha256": digest},
            "worker_recovery_origin_source",
        )
    require(
        set(code) == {"schema", "revision", "tree", "consumer_sha256", "source_consumers"}
        and code["schema"] == "autostop.j1.crm-forward-recovery-code.v2"
        and all(
            code[key] == plan["candidate"][key] for key in ("revision", "tree", "consumer_sha256")
        )
        and code["source_consumers"] == baseline["source_consumers"]
        and documents["ci"] == baseline["ci"]
        and all(
            envelope[key]["path"].startswith(prefix)
            for key in ("recovery_code_ref", "ci_ref", "hold_ref")
        ),
        "worker_recovery_current_code",
    )
    hold = documents["hold"]
    require(
        set(hold)
        == {
            "schema",
            "origin_attempt_id",
            "attempt_hash",
            "reason",
            "enabled",
            "scheduler_counts",
            "status_ref",
        }
        and hold["schema"] == "autostop.j1.crm-forward-recovery-hold.v2"
        and hold["origin_attempt_id"] == origin["attempt_id"]
        and hold["attempt_hash"] == io.sha(("release-attempt:" + origin["attempt_id"]).encode())
        and hold["reason"] == "release"
        and hold["enabled"] is True,
        "worker_recovery_hold_ownership",
    )
    _bound(documents["hold_status"], hold["status_ref"])
    require(hold["status_ref"]["path"].startswith(prefix), "worker_recovery_hold_path")
    from scripts import j1_forward_host as host_policy

    data = host_policy.manager_data(documents["hold_status"])
    counts, jobs = hold["scheduler_counts"], data.get("jobs")
    require(
        type(counts) is dict
        and set(counts) == {"active_leases", "outbox_sending", "outbox_total"}
        and all(type(value) is int and value >= 0 for value in counts.values())
        and counts["active_leases"] == counts["outbox_sending"] == 0
        and type(jobs) is list
        and all(
            type(job) is dict
            and "lease_until" in job
            and (job["lease_until"] is None or _utc(job["lease_until"]) <= _utc(plan["created_at"]))
            for job in jobs
        )
        and type(data.get("global_hold")) is dict
        and all(data["global_hold"].get(key) == hold[key] for key in ("attempt_hash", "reason"))
        and data["global_hold"].get("enabled") is True,
        "worker_recovery_hold_status",
    )
    outbox = data.get("readiness", {}).get("execution_packet", {}).get("outbox", {})
    require(
        type(outbox) is dict
        and type(outbox.get("by_status")) is dict
        and type(outbox["by_status"].get("sending", 0)) is int
        and outbox["by_status"].get("sending", 0) == 0
        and type(outbox.get("total_count")) is int
        and outbox["total_count"] == counts["outbox_total"],
        "worker_recovery_hold_outbox",
    )
    native = response["evidence"]["native"]
    require(
        all(
            native.get(key) == baseline["native"].get(key)
            for key in (
                "schema",
                "warm_identity_semantics",
                "historical_warm_actors",
                "recovery_origin",
            )
        )
        and native.get("recovery_origin") == expected_origin
        and type(native.get("observed_warm_actors")) is list
        and len(native["observed_warm_actors"]) == 2
        and all(
            type(row) is dict and set(prior.ACTOR_FIELDS) <= set(row)
            for row in native["observed_warm_actors"]
        )
        and sorted(
            [
                {key: row[key] for key in prior.ACTOR_FIELDS}
                for row in native["observed_warm_actors"]
            ],
            key=lambda row: row["pid"],
        )
        == scope["original_liveness"],
        "worker_recovery_native_evidence",
    )
