"""Typed cold custodian over the existing delta/restore/Ed25519 implementation.

The seven new runtimes never inherit a warm-preservation claim. Signature
creation stays after independent data AND complete control-graph restore in
the existing Worker; only this explicit contract can use the cold remote CLI.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_cold_qualification as qualification
from scripts import j1_forward_cold_runtime as readonly_runtime
from scripts import j1_forward_cold_state as state
from scripts import j1_forward_io as io
from scripts import j1_forward_recovery_state as recovery
from scripts import j1_forward_worker as worker

REMOTE = "/opt/autostopcrm/scripts/j1_forward_cold_release.py"
NATIVE_SCHEMA = "autostop.j1.cold-native-observation.v1"
ACCEPTED_SCHEMA = "autostop.j1.cold-seven-runtime-observation.v1"


def native_observation(value, response, *, phase):
    package, epoch = response["evidence"]["package"], response["evidence"]["epoch"]
    binding, plan = package["binding"], response["plan"]
    contract.fields(
        value,
        {
            "schema",
            "scope",
            "ok",
            "phase",
            "attempt_id",
            "package_ref",
            "epoch_ref",
            "binding_sha256",
            "boot_id",
            "observed_monotonic_ns",
            "original_barriers_closed",
            "original_hold_retained",
            "old_writer_allowed",
            "candidate_running",
            "all_six_policy_refs",
            "retired_scope_sha256",
        },
        "worker_native_fields",
    )
    contract.need(
        value["schema"] == NATIVE_SCHEMA
        and value["scope"] == "production_native"
        and value["ok"] is True
        and value["phase"] == phase
        and value["attempt_id"] == plan["attempt_id"]
        and value["package_ref"] == plan["cold_authority_package_ref"]
        and value["epoch_ref"] == plan["dispatch_epoch_ref"]
        and value["binding_sha256"] == contract.digest(binding)
        and value["boot_id"] == binding["boot_id"] == epoch["boot_id"]
        and value["original_barriers_closed"] is True
        and value["original_hold_retained"] is True
        and value["old_writer_allowed"] is False
        and value["candidate_running"] is (phase == "activated")
        and value["all_six_policy_refs"] == package["policies"]
        and worker._hash(value["retired_scope_sha256"]),
        "worker_native_binding",
    )
    contract._binding(binding, now_ns=value["observed_monotonic_ns"])


def _baseline(response, attempt):
    plan, baseline = response["plan"], response["baseline"]
    worker._bound(baseline, response["baseline_ref"])
    expected = {
        "schema",
        "attempt_id",
        "native",
        "host",
        "previous_container",
        "source_consumers",
        "review_key_ref",
        "backup_profile_sha256",
        "deployment_lock_identity",
        "ci",
        "maintenance_parent",
        "controller_request_ref",
        "controller_launch_ref",
        "qualification_ref",
    }
    is_recovery = "recovery_origin" in baseline
    contract.need(
        set(baseline) == expected | ({"recovery_origin"} if is_recovery else set())
        and baseline["schema"] == state.BASELINE_SCHEMA
        and baseline["attempt_id"] == attempt
        and plan["baseline_ref"] == response["baseline_ref"]
        and baseline["native"]
        == {
            "cold_authority_package_ref": plan["cold_authority_package_ref"],
            "dispatch_epoch_ref": plan["dispatch_epoch_ref"],
        },
        "worker_baseline",
    )
    from scripts import j1_forward_marker

    j1_forward_marker.validate_parent(baseline["maintenance_parent"])
    worker._bound(response["qualification"], baseline["qualification_ref"])
    qualification.validate_document(
        response["qualification"],
        {key: plan["candidate"][key] for key in ("revision", "tree", "consumer_sha256")},
    )
    ci = baseline["ci"]
    contract.need(
        type(ci) is dict
        and set(ci) == {"run_id", "revision", "run_url", "jobs", "conclusion"}
        and type(ci["run_id"]) is int
        and ci["run_id"] > 0
        and ci["revision"] == plan["candidate"]["revision"]
        and ci["jobs"] == worker.CI_JOBS
        and ci["conclusion"] == "success"
        and ci["run_url"]
        == f"https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/{ci['run_id']}",
        "worker_ci",
    )
    qualified_quality = response["qualification"]["checks"]["quality"]
    contract.need(
        ci["run_id"] == qualified_quality["run_id"]
        and ci["run_url"] == qualified_quality["run_url"],
        "worker_qualified_quality_run",
    )
    contract.need(
        response["review_key_ref"] == baseline["review_key_ref"]
        and response["review_key_ref"]["path"] == worker.proofs.REVIEW_KEY_PATH,
        "worker_review_key",
    )
    worker._reference(response["review_key_ref"])
    for name in ("controller_request_ref", "controller_launch_ref"):
        worker._reference(baseline[name])
    if is_recovery:
        material = response["recovery"]
        contract.fields(
            material, {"envelope", "envelope_ref", "evidence"}, "worker_recovery_material"
        )
        envelope = material["envelope"]
        worker._bound(envelope, material["envelope_ref"])
        contract.fields(envelope, recovery.FIELDS, "worker_recovery_fields")
        contract.need(
            envelope["schema"] == state.RECOVERY_SCHEMA
            and envelope["recovery_id"] == attempt
            and envelope["plan_ref"] == response["plan_ref"]
            and material["envelope_ref"]["path"]
            == worker.ATTEMPTS.rsplit("/", 1)[0] + "/state/recoveries/" + attempt + ".json"
            and {name: envelope[name] for name in recovery.ORIGIN_FIELDS}
            == baseline["recovery_origin"],
            "worker_recovery_binding",
        )
        for name in recovery.REFERENCE_FIELDS:
            worker._reference(envelope[name])


def _accepted(document, response):
    plan, evidence = response["plan"], response["evidence"]
    package, epoch = evidence["package"], evidence["epoch"]
    contract.fields(
        document,
        {
            "schema",
            "attempt_id",
            "package_ref",
            "epoch_ref",
            "accepted",
            "observations",
            "crm_ref",
            "crm",
            "execution_acceptance_sha256",
        },
        "worker_accepted_fields",
    )
    contract.need(
        document["schema"] == ACCEPTED_SCHEMA
        and document["attempt_id"] == plan["attempt_id"]
        and document["package_ref"] == plan["cold_authority_package_ref"]
        and document["epoch_ref"] == plan["dispatch_epoch_ref"]
        and set(document["accepted"]) == set(document["observations"]) == set(contract.ALLOWED)
        and document["execution_acceptance_sha256"]
        == contract.digest(document["accepted"])
        == response["inspection"]["execution_acceptance_sha256"],
        "worker_accepted_binding",
    )
    identities, pids = set(), set()
    for unit in contract.ALLOWED:
        value, reference = document["observations"][unit], document["accepted"][unit]
        worker._bound(value, reference)
        contract.fields(
            value,
            {
                "schema",
                "scope",
                "package_ref",
                "epoch_ref",
                "logical_unit",
                "execution_unit",
                "policy_ref",
                "pid",
                "start_ticks",
                "invocation_id",
                "business_acceptance",
            },
            "worker_role_fields",
        )
        contract.need(
            value["schema"] == "autostop.j1.cold-slot-runtime-observation.v1"
            and value["scope"] == "production_native"
            and value["logical_unit"] == unit
            and value["execution_unit"]
            == "autostop-crm-forward-" + epoch["slots"][unit] + ".service"
            and value["package_ref"] == plan["cold_authority_package_ref"]
            and value["epoch_ref"] == plan["dispatch_epoch_ref"]
            and value["policy_ref"] == package["policies"][unit]
            and value["business_acceptance"] is False
            and type(value["pid"]) is int
            and value["pid"] > 0
            and type(value["start_ticks"]) is int
            and value["start_ticks"] > 0
            and re.fullmatch(r"[0-9a-f]{32}", value["invocation_id"]),
            "worker_role_binding",
        )
        identities.add((value["pid"], value["start_ticks"]))
        pids.add(value["pid"])
    contract.need(
        len(identities) == len(pids) == 6
        and document["crm_ref"] == response["inspection"]["crm_ref"]
        and document["crm"] == evidence["crm"],
        "worker_seven_distinct_runtimes",
    )
    crm = evidence["crm"]
    contract.need(
        crm["schema"] == "autostop.j1.cold-crm-runtime-acceptance.v1"
        and crm["role"] == "crm-embedded"
        and crm["epoch_ref"] == plan["dispatch_epoch_ref"]
        and crm["package_ref"] == plan["cold_authority_package_ref"]
        and crm["spec_ref"] == plan["cold_crm_spec_ref"]
        and crm["manager_source_revision"] == plan["candidate_manager_sha"]
        and crm["binding_ref"] == epoch["runtime_bindings"]["crm-embedded"]
        and type(crm["independent_observations"]) is int
        and crm["independent_observations"] == 2
        and crm["business_acceptance"] is False
        and crm["instance"]["image_id"] == plan["candidate"]["image"]
        and type(crm["instance"]["restart_count"]) is int
        and crm["instance"]["restart_count"] == 0
        and type(crm["instance"]["main_pid"]) is int
        and crm["instance"]["main_pid"] > 0
        and type(crm["instance"]["start_ticks"]) is int
        and crm["instance"]["start_ticks"] > 0
        and (
            "init_pid" not in crm["instance"]
            or (
                type(crm["instance"]["init_pid"]) is int
                and crm["instance"]["init_pid"] > 0
                and crm["instance"]["init_pid"] != crm["instance"]["main_pid"]
            )
        )
        and crm["instance"]["main_pid"] not in pids,
        "worker_embedded_runtime",
    )
    readonly_runtime.validate_report(
        evidence["runtime"],
        candidate={key: plan["candidate"][key] for key in ("image", "revision", "tree")},
        attempt=plan["attempt_id"],
        phase="closed",
    )


def validate_response(response, *, attempt, stage):
    worker._uuid(attempt)
    contract.need(stage in {"closed", "activated"}, "worker_stage")
    common = {
        "ok",
        "stage",
        "plan",
        "plan_ref",
        "baseline",
        "baseline_ref",
        "review_key_ref",
        "inspection",
        "inspection_ref",
        "evidence",
        "backup_context",
        "scope_profile_sha256",
        "qualification",
    }
    has_recovery = (
        type(response) is dict
        and type(response.get("baseline")) is dict
        and "recovery_origin" in response["baseline"]
    )
    contract.fields(
        response,
        common
        | ({"transport", "control_archive"} if stage == "closed" else set())
        | ({"recovery"} if has_recovery else set()),
        "worker_response_fields",
    )
    plan = response["plan"]
    worker._bound(plan, response["plan_ref"])
    state.validate_plan(plan, plan["base_sha256"])
    contract.need(
        response["ok"] is True
        and response["stage"] == stage
        and plan["attempt_id"] == attempt
        and plan["host"] == worker.NATIVE_HOST,
        "worker_response_binding",
    )
    prefix = worker.ATTEMPTS + "/" + attempt + "/"
    contract.need(
        response["plan_ref"]["path"] == prefix + "plan.json"
        and response["baseline_ref"]["path"] == prefix + "baseline.json",
        "worker_attempt_paths",
    )
    _baseline(response, attempt)
    inspection, evidence = response["inspection"], response["evidence"]
    worker._bound(inspection, response["inspection_ref"])
    fields = {
        "schema",
        "stage",
        "attempt_id",
        "plan_sha256",
        "baseline_sha256",
        "candidate",
        "package_ref",
        "epoch_ref",
        "native_ref",
        "original_barriers_closed",
        "original_hold_retained",
        "old_writer_allowed",
    }
    fields |= (
        {"writer_closure_confirmed", "backup_capture_ref", "backup_restore_ref"}
        if stage == "closed"
        else {
            "cold_guards_executed",
            "runtime_checks_passed",
            "execution_acceptance_sha256",
            "accepted_ref",
            "crm_ref",
            "runtime_ref",
        }
    )
    contract.fields(inspection, fields, "worker_inspection_fields")
    contract.need(
        inspection["schema"] == "autostop.j1.crm-manager-cold-forward-inspection.v1"
        and inspection["stage"] == stage
        and inspection["attempt_id"] == attempt
        and inspection["plan_sha256"] == response["plan_ref"]["sha256"]
        and inspection["baseline_sha256"] == response["baseline_ref"]["sha256"]
        and inspection["candidate"] == plan["candidate"]
        and inspection["package_ref"] == plan["cold_authority_package_ref"]
        and inspection["epoch_ref"] == plan["dispatch_epoch_ref"]
        and inspection["original_barriers_closed"] is True
        and inspection["original_hold_retained"] is True
        and inspection["old_writer_allowed"] is False,
        "worker_inspection_binding",
    )
    names = {"native", "package", "epoch"} | (
        {"backup_capture", "backup_restore"}
        if stage == "closed"
        else {"accepted", "crm", "runtime"}
    )
    contract.fields(evidence, names, "worker_evidence_fields")
    for name in names:
        reference = inspection[name + "_ref"]
        worker._bound(evidence[name], reference)
        contract.need(
            name in {"package", "epoch"} or reference["path"].startswith(prefix),
            "worker_evidence_path",
        )
    package, epoch = evidence["package"], evidence["epoch"]
    contract.fields(package, contract.PACKAGE_FIELDS, "worker_package_fields")
    binding = package["binding"]
    contract.need(
        package["schema"] == contract.SCHEMA
        and package["scope"] == epoch["scope"] == "production_native"
        and package["contract"] == contract.CONTRACT
        and binding["attempt_id"] == attempt
        and binding["candidate_crm_sha"] == plan["candidate"]["revision"]
        and binding["candidate_manager_sha"] == plan["candidate_manager_sha"]
        and epoch["package_ref"] == plan["cold_authority_package_ref"]
        and epoch["generation"] == binding["generation"]
        and set(package["policies"]) == set(contract.ALLOWED)
        and set(epoch["runtime_bindings"])
        == {
            "manager-mcp",
            "j1",
            "scheduler",
            "work-telegram",
            "personal-telegram",
            "wake",
            "crm-embedded",
        },
        "worker_package_epoch_binding",
    )
    native_observation(evidence["native"], response, phase=stage)
    profile = response["scope_profile_sha256"]
    contract.need(
        worker._hash(profile)
        and response["baseline"]["backup_profile_sha256"]
        == profile
        == worker.variant.backup_engine(plan).profile_sha256()
        and response["backup_context"]
        == {
            "attempt_id": attempt,
            "predecessor_sha256": plan["predecessor_sha256"],
            "candidate_crm_sha": plan["candidate"]["revision"],
            "candidate_manager_sha": plan["candidate_manager_sha"],
            "scope_profile_sha256": profile,
        },
        "worker_backup_context",
    )
    if stage == "closed":
        contract.need(inspection["writer_closure_confirmed"] is True, "worker_closure")
        transport, control = response["transport"], response["control_archive"]
        contract.fields(transport, {"path", "sha256", "manifest_sha256"}, "worker_transport")
        worker._reference({key: transport[key] for key in ("path", "sha256")})
        captured, restored = evidence["backup_capture"], evidence["backup_restore"]
        contract.need(
            worker._hash(transport["manifest_sha256"])
            and transport["path"].startswith(prefix)
            and transport["manifest_sha256"]
            == captured["manifest_sha256"]
            == captured["manifest"]["sha256"]
            and restored["manifest_sha256"] == captured["manifest_sha256"]
            and restored["provided_artifacts_restored_exactly"] is True
            and restored["source_evidence_unchanged"] is True,
            "worker_capture_restore_binding",
        )
        contract.fields(
            control, {"manifest", "manifest_sha256", "stage", "archive"}, "worker_control_archive"
        )
        location = "/var/lib/autostop-crm-forward-control-archives/" + attempt + "/closed"
        contract.need(
            control["stage"] == "closed"
            and worker._hash(control["manifest_sha256"])
            and control["manifest"]
            == {"path": location + "/manifest.json", "sha256": control["manifest_sha256"]}
            and control["archive"]["path"] == location + ".tar.gz",
            "worker_control_archive_binding",
        )
        worker._reference(control["archive"])
    else:
        contract.need(
            inspection["cold_guards_executed"] is True
            and inspection["runtime_checks_passed"] is True,
            "worker_activated_checks",
        )
        _accepted(evidence["accepted"], response)
    worker._remaining(plan)
    worker._source_consumers(response["baseline"], plan)
    return response


def capture_manifest(manifest, response):
    captured = response["evidence"]["backup_capture"]
    worker._bound(manifest, captured["manifest"])
    witnesses = manifest["external_closure_observations"]
    contract.need(
        type(witnesses) is list
        and len(witnesses) <= 200_002
        and len(witnesses) == 2 * len(manifest["artifacts"]) + 2,
        "worker_capture_fences",
    )
    payloads = [row for row in manifest["artifacts"] if "payload" in row]
    contract.need(
        len(payloads) == captured["files"]
        and sum(row["bytes"] for row in payloads) == captured["bytes"],
        "worker_capture_totals",
    )


def restored_capture_fences(response, data_manifest, package, restored):
    """Read opaque, independently restored fence bytes before any signature."""
    capture_manifest(data_manifest, response)
    raw = worker._physical(package / "manifest.json").read_bytes()
    contract.need(
        len(raw) <= worker.control_archive.MAX_FILE_BYTES
        and io.sha(raw) == response["control_archive"]["manifest_sha256"],
        "worker_restored_control_manifest",
    )
    manifest = io.document(raw)
    entries = {row["logical_name"]: row for row in manifest["artifacts"]}
    contract.need(len(entries) == len(manifest["artifacts"]), "worker_restored_control_inventory")
    root = PurePosixPath(worker.ATTEMPTS).parent
    cache, previous = {}, 0
    for witness in data_manifest["external_closure_observations"]:
        contract.fields(witness, {"attempt_id", "path", "sha256"}, "worker_capture_witness")
        reference = {key: witness[key] for key in ("path", "sha256")}
        worker._reference(reference)
        contract.need(
            witness["attempt_id"] == response["plan"]["attempt_id"]
            and reference["path"].startswith(worker.ATTEMPTS + "/" + witness["attempt_id"] + "/"),
            "worker_capture_witness_binding",
        )
        key = reference["path"]
        if key not in cache:
            name = "control/" + PurePosixPath(key).relative_to(root).as_posix()
            contract.need(name in entries, "worker_capture_fence_not_restored")
            entry = entries[name]
            contract.need(
                re.fullmatch(r"[0-9]{6}\.bin", entry["payload"]), "worker_capture_fence_payload"
            )
            path = worker._physical(restored / entry["payload"])
            contract.need(path.stat().st_size <= worker.MAX_JSON, "worker_capture_fence_size")
            body = path.read_bytes()
            contract.need(
                io.sha(body) == entry["sha256"] == reference["sha256"],
                "worker_capture_fence_changed",
            )
            cache[key] = io.document(body)
        body = cache[key]
        worker._bound(body, reference)
        native_observation(body, response, phase="closed")
        contract.need(body["observed_monotonic_ns"] >= previous, "worker_capture_fence_order")
        previous = body["observed_monotonic_ns"]


class ColdWorker(worker.Worker):
    CONTRACT = "cold"

    def _ssh(self, *args, **kwargs):
        command = super()._ssh(*args, **kwargs)
        contract.need(command.count(worker.REMOTE) == 1, "worker_fixed_remote")
        return [REMOTE if value == worker.REMOTE else value for value in command]

    def _capture_manifest(self, manifest, response):
        return capture_manifest(manifest, response)

    def _after_control_restore(self, response, data_manifest, package, restored):
        return restored_capture_fences(response, data_manifest, package, restored)

    def _activation_payloads(self, response, binding):
        plan = response["plan"]
        return {
            "native_acceptance": {
                **binding,
                "inspection_sha256": response["inspection_ref"]["sha256"],
                "cold_guards_executed": True,
                "execution_acceptance_sha256": response["inspection"][
                    "execution_acceptance_sha256"
                ],
            },
            "runtime_acceptance": {
                **binding,
                "inspection_sha256": response["inspection_ref"]["sha256"],
                "candidate_image": plan["candidate"]["image"],
                "candidate_revision": plan["candidate"]["revision"],
                "runtime_checks_passed": True,
            },
        }
