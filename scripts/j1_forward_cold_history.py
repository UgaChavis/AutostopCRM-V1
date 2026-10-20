"""Pure semantic coverage of the preserved predecessor; no native authority reuse."""

from __future__ import annotations

from pathlib import Path

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_io as io

ORIGIN = "236d5cef-6c87-4bf1-a977-a36e70bdf90b"
DIRECTORY = "/var/lib/autostop-crm-forward/attempts/" + ORIGIN
ORIGIN_REFS = {
    "plan_ref": {
        "path": DIRECTORY + "/plan.json",
        "sha256": "a872a54ee8b9ddd600d52e2efcd5922e29e5dd714a78bacac62feaed307bfd57",
    },
    "scope_ref": {
        "path": DIRECTORY + "/process-scope.json",
        "sha256": "c494e945625a86a484559eb9bad1f536f8480b8de014d54dd4cb091ba2db222a",
    },
    "closure_ref": {
        "path": DIRECTORY + "/closure-owned.json",
        "sha256": "a67442c32ba0e022f3daf266980f2cc5ee41391392e4bacfa1ac4190e3bf1b7a",
    },
}
ANCHOR_FIELDS = {
    "schema",
    "scope",
    "origin_attempt_id",
    "plan_ref",
    "baseline_ref",
    "scope_ref",
    "closure_ref",
    "original_hold_ref",
}


def inventory(scope, *, fixture=False):
    """Derive coverage from preserved topology, never a caller's retirement list."""
    if fixture:
        contract.fields(
            scope,
            {"schema", "attempt_id", "boot_id", "actors", "endpoints", "pipe_inodes"},
            "fixture_preserved_scope",
        )
        contract.need(
            scope["schema"] == "autostop.j1.cold-fixture-preserved-scope.v1",
            "fixture_preserved_schema",
        )
        return {key: scope[key] for key in ("actors", "endpoints", "pipe_inodes")}
    from scripts import j1_forward_process_scope as prior

    prior.validate_scope_shape(scope)
    actors = {row["pid"]: row for row in scope["freeze_actors"] + scope["transport_actors"]}
    peer = scope["native_control_peer"]
    if peer is not None:
        row = peer["actor"]
        contract.need(
            row["pid"] not in actors or actors[row["pid"]] == row, "history_native_peer_alias"
        )
        actors[row["pid"]] = row
    topology = scope["observation"]
    listener = topology["daemon_listener"]
    endpoints = [{"inode": listener["inode"], "path_sha256": listener["path_sha256"]}]
    for row in topology["proxy_peers"]:
        endpoints.extend(
            {"inode": row[key], "path_sha256": listener["path_sha256"]}
            for key in ("inode", "daemon_inode")
        )
    if peer is not None:
        endpoints.extend(
            {"inode": peer[key], "path_sha256": listener["path_sha256"]}
            for key in ("inode", "daemon_inode")
        )
    endpoints = [dict(row) for row in {tuple(sorted(row.items())) for row in endpoints}]
    return {
        "actors": [actors[pid] for pid in sorted(actors)],
        "endpoints": sorted(endpoints, key=lambda row: row["inode"]),
        "pipe_inodes": sorted({row["pipe_inode"] for row in topology["stdio"]}),
    }


def load(anchor_ref, *, read_reference, fixture=False):
    reader = contract.Reader(read_reference)
    anchor = reader.doc(anchor_ref)
    contract.fields(anchor, ANCHOR_FIELDS, "history_anchor_fields")
    contract.need(
        anchor["schema"] == "autostop.j1.cold-predecessor-anchor.v1"
        and anchor["scope"] == ("native_fixture" if fixture else "production_native"),
        "history_anchor_schema",
    )
    if not fixture:
        contract.need(
            anchor["origin_attempt_id"] == ORIGIN
            and all(anchor[key] == ref for key, ref in ORIGIN_REFS.items()),
            "history_fixed_origin",
        )
    documents = {
        key: reader.doc(anchor[key])
        for key in ("plan_ref", "baseline_ref", "scope_ref", "closure_ref", "original_hold_ref")
    }
    plan, baseline, scope, closure, hold = (
        documents[key]
        for key in ("plan_ref", "baseline_ref", "scope_ref", "closure_ref", "original_hold_ref")
    )
    contract.need(
        plan["attempt_id"] == anchor["origin_attempt_id"]
        and plan["baseline_ref"] == anchor["baseline_ref"]
        and scope["attempt_id"] == plan["attempt_id"]
        and closure["attempt_id"] == plan["attempt_id"]
        and closure["process_scope_ref"] == anchor["scope_ref"]
        and baseline["attempt_id"] == plan["attempt_id"],
        "history_chain_binding",
    )
    if not fixture:
        from scripts import j1_forward_state as state

        state.validate_plan(plan, plan["base_sha256"])
        contract.need(
            baseline["schema"] == "autostop.j1.crm-forward-baseline.v1"
            and anchor["baseline_ref"]["path"] == DIRECTORY + "/baseline.json"
            and scope["native_sha256"] == contract.digest(baseline["native"])
            and scope["consumer_sha256"] == plan["candidate"]["consumer_sha256"]
            and closure["baseline_sha256"] == scope["native_sha256"]
            and closure["consumer_sha256"] == scope["consumer_sha256"],
            "history_preserved_native_binding",
        )
    contract.fields(
        hold,
        {
            "schema",
            "origin_attempt_id",
            "attempt_hash",
            "reason",
            "enabled",
            "scheduler_counts",
            "status_ref",
        },
        "history_original_hold_fields",
    )
    expected_hash = io.sha(("release-attempt:" + anchor["origin_attempt_id"]).encode())
    contract.need(
        hold["schema"] == "autostop.j1.crm-forward-recovery-hold.v2"
        and hold["origin_attempt_id"] == anchor["origin_attempt_id"]
        and hold["enabled"] is True
        and hold["reason"] == "release"
        and hold["attempt_hash"] == expected_hash
        and hold["scheduler_counts"]["active_leases"] == 0
        and hold["scheduler_counts"]["outbox_sending"] == 0,
        "history_original_hold",
    )
    reader.raw(hold["status_ref"])
    result = inventory(scope, fixture=fixture)
    reader.recheck()
    return {"anchor": anchor, "scope": scope, "inventory": result, "original_hold": hold}


def check_coverage(validated, history):
    """Reject a missing old actor and an unrelated empty cgroup alike."""
    origin, retired, quarantine = (validated[key] for key in ("origin", "retirement", "quarantine"))
    inventory = history["inventory"]
    contract.need(
        origin["predecessor_scope_ref"] == history["anchor"]["scope_ref"]
        and origin["actors"] == retired["actors"] == inventory["actors"],
        "history_actor_coverage",
    )
    contract.need(
        quarantine["endpoints"] == inventory["endpoints"]
        and quarantine["pipe_inodes"] == inventory["pipe_inodes"],
        "history_descriptor_coverage",
    )
    expected = sorted(
        {"/sys/fs/cgroup" + row["cgroup"].removeprefix("0::") for row in inventory["actors"]}
    )
    return expected


def check_containment(document, validated, history):
    contract.fields(
        document,
        {"schema", "scope", "binding", "predecessor_scope_ref", "actors_sha256", "cgroup_roots"},
        "containment_fields",
    )
    expected = check_coverage(validated, history)
    contract.need(
        document["schema"] == "autostop.j1.cold-closed-population.v1"
        and document["scope"] == validated["package"]["scope"]
        and document["binding"] == validated["package"]["binding"]
        and document["predecessor_scope_ref"] == history["anchor"]["scope_ref"]
        and document["actors_sha256"] == contract.digest(history["inventory"]["actors"])
        and document["cgroup_roots"] == expected,
        "containment_history_coverage",
    )
    return [Path(path) for path in expected]
