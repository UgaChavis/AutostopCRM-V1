"""Versioned CRM-only successor to an already accepted warm J1 activation.

All authority remains in the original native guards. This consumer preserves
that authority, drains its existing writers, and records a separate monotonic
CRM generation. It never rewrites an armed receipt or restores production data.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import j1_forward_authority as authority
from scripts import j1_forward_backup as backup
from scripts import j1_forward_closure as closure
from scripts import j1_forward_io as io
from scripts import j1_forward_marker as marker
from scripts import j1_forward_proofs as signatures
from scripts import j1_forward_recovery as recovery
from scripts import j1_forward_transport as transport
from scripts.j1_forward_host import CRM, DOCKER, Host, check_container_preserved, work_counts
from scripts.j1_forward_monitor import ClosureMonitor
from scripts.j1_forward_state import PLAN_SCHEMA, SCOPE, Store, validate_plan

ROOT = Path("/var/lib/autostop-crm-forward")
ATTEMPTS = ROOT / "attempts"
STATE = ROOT / "state"
LOCK = CRM / ".autostop-deploy.lock"
MARKER = CRM / "data/.agent-gateway-maintenance"
KEY = Path(signatures.REVIEW_KEY_PATH)
CACHE_INVENTORY = ROOT / "transport-cache-inventory.json"
MAX_WINDOW = 600
SCHEMA = "autostop.j1.crm-forward-baseline.v1"
INSPECTION = "autostop.j1.crm-forward-inspection.v1"


def utc():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def attempt_directory(attempt):
    io.require(type(attempt) is str and str(uuid.UUID(attempt)) == attempt, "attempt_uuid_required")
    directory = ATTEMPTS / attempt
    io.protected_directory(directory, private=True)
    return directory


def source_consumers():
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(CRM), "ls-files", "scripts/j1_forward_*.py"],
        capture_output=True,
        check=True,
        timeout=10,
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
    )
    paths = result.stdout.decode().splitlines()
    io.require(
        8 <= len(paths) <= 32 and "scripts/j1_forward_release.py" in paths,
        "published_consumer_set_required",
    )
    io.require(
        all(re.fullmatch(r"scripts/j1_forward_[a-z_]+\.py", path) for path in paths),
        "consumer_path_invalid",
    )
    return {path: io.reference(CRM / path)["sha256"] for path in sorted(paths)}


def load_attempt(attempt, *, verify_published=True):
    directory = attempt_directory(attempt)
    plan_ref = io.reference(directory / "plan.json")
    plan = io.document(io.referenced(plan_ref))
    validate_plan(plan, plan["base_sha256"])
    baseline = io.document(io.referenced(plan["baseline_ref"]))
    io.require(
        baseline.get("schema") == SCHEMA
        and baseline["attempt_id"] == attempt
        and baseline["native"]["base_sha256"] == plan["base_sha256"]
        and plan["host"] == socket.gethostname(),
        "baseline_binding_invalid",
    )
    consumers = source_consumers()
    io.require(
        baseline["source_consumers"] == consumers
        and io.sha(io.canonical(consumers)) == plan["candidate"]["consumer_sha256"],
        "consumer_source_changed",
    )
    io.require(io.reference(KEY) == baseline["review_key_ref"], "review_key_changed")
    host_class = recovery.RecoveryHost if "recovery_origin" in baseline else Host
    host = host_class(baseline["native"], directory, host_baseline=baseline["host"])
    if verify_published:
        io.require(
            host.source() == {key: plan["candidate"][key] for key in ("revision", "tree")},
            "published_candidate_changed",
        )
    return directory, plan_ref, plan, baseline, host


def remaining(plan):
    value = (
        datetime.fromisoformat(plan["deadline_utc"].replace("Z", "+00:00")) - datetime.now(UTC)
    ).total_seconds()
    created = datetime.fromisoformat(plan["created_at"].replace("Z", "+00:00"))
    duration = (
        datetime.fromisoformat(plan["deadline_utc"].replace("Z", "+00:00")) - created
    ).total_seconds()
    io.require(0 < value <= duration <= MAX_WINDOW, "forward_window_expired_or_invalid")
    return value


@contextmanager
def deployment_lock(expected=None, *, on_failure=None):
    import fcntl

    io.protected_directory(LOCK.parent)
    before = LOCK.lstat()
    io.require(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == 0
        and before.st_nlink == 1
        and not before.st_mode & 0o022,
        "original_deployment_lock_required",
    )
    identity = [before.st_dev, before.st_ino]
    io.require(expected is None or expected == identity, "deployment_lock_inode_changed")
    descriptor = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(descriptor)
        io.require([opened.st_dev, opened.st_ino] == identity, "deployment_lock_replaced")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        io.require([LOCK.stat().st_dev, LOCK.stat().st_ino] == identity, "deployment_lock_replaced")
        try:
            yield
        except BaseException:
            if on_failure is not None:
                on_failure()
            raise
    finally:
        os.close(descriptor)


def context(plan):
    return {
        "attempt_id": plan["attempt_id"],
        "predecessor_sha256": plan["predecessor_sha256"],
        "candidate_crm_sha": plan["candidate"]["revision"],
        "candidate_manager_sha": "89af29082e0a6f72c5d85ae448acb6d4deec5d94",
        "scope_profile_sha256": backup.profile_sha256(),
    }


def save(directory, label, value):
    return io.write_new(directory / (label + "-" + uuid.uuid4().hex + ".json"), value)


def progress(directory, phase, **values):
    return io.atomic_replace(
        directory / "progress.json", {"phase": phase, "observed_at": utc(), **values}
    )


def verify_ci(run_id, source):
    io.require(type(run_id) is int and run_id > 0, "quality_run_id_required")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(suffix):
        request = urllib.request.Request(
            "https://api.github.com/repos/UgaChavis/AutostopCRM-V1/actions/runs/"
            + str(run_id)
            + suffix,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "AutoStop-CRM-forward-release",
            },
        )
        with opener.open(request, timeout=20) as response:
            io.require(
                response.status == 200 and response.url == request.full_url, "quality_api_response"
            )
            raw = response.read(2 * 1024**2 + 1)
        io.require(len(raw) <= 2 * 1024**2, "quality_response_size")
        return io.document(raw)

    run = get("")
    io.require(
        run.get("id") == run_id
        and run.get("head_sha") == source["revision"]
        and run.get("head_branch") == "autostopcrm-v1"
        and run.get("event") == "push"
        and run.get("path") == ".github/workflows/quality.yml"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success",
        "exact_merged_quality_run_required",
    )
    jobs = get("/jobs?filter=latest&per_page=100")
    names = {"docker-runtime-assets", "static", "unit", "browser-performance", "quality"}
    io.require(
        jobs.get("total_count") == 5
        and type(jobs.get("jobs")) is list
        and {item.get("name") for item in jobs["jobs"]} == names
        and all(
            item.get("status") == "completed"
            and item.get("conclusion") == "success"
            and item.get("head_sha") == source["revision"]
            for item in jobs["jobs"]
        ),
        "five_successful_quality_jobs_required",
    )
    return {
        "run_id": run_id,
        "revision": source["revision"],
        "run_url": run["html_url"],
        "jobs": sorted(names),
        "conclusion": "success",
    }


def prepare(image, ci_run_id):
    io.require(os.geteuid() == 0, "native_root_required")
    for path in (ROOT, ATTEMPTS, STATE):
        if not path.exists():
            path.mkdir(mode=0o700)
        io.protected_directory(path, private=True)
    io.require(not MARKER.exists(), "preexisting_maintenance_marker")
    with deployment_lock():
        io.require(not os.path.lexists(STATE / "active-recovery.json"), "state_recovery_active")
        maintenance_parent = marker.prepare()
        native = authority.snapshot_native(full_runtime=True)
        store = Store(STATE, native["base_sha256"])
        io.require(store.active_plan() is None, "pending_forward_requires_recovery")
        head = store.head()
        attempt = str(uuid.uuid4())
        directory = ATTEMPTS / attempt
        directory.mkdir(mode=0o700)
        host = Host(native, directory)
        source = host.source()
        ci = verify_ci(ci_run_id, source)
        candidate = host.image(image)
        io.require(candidate == {**source, "image": image}, "candidate_image_source_mismatch")
        current = host.container()
        expected = (
            head["record"]["plan"]["candidate"]
            if head["record"]
            else {"revision": native["legacy_crm_revision"], "image": current["image"]}
        )
        io.require(
            current["revision"] == expected["revision"]
            and current["image"] == expected["image"]
            and current["health"] == "healthy"
            and current["running"] is True
            and current["revision"] != source["revision"],
            "current_generation_mismatch",
        )
        host_baseline = host.capture_baseline()
        consumers = source_consumers()
        baseline = {
            "schema": SCHEMA,
            "attempt_id": attempt,
            "native": native,
            "host": host_baseline,
            "previous_container": current,
            "source_consumers": consumers,
            "review_key_ref": io.reference(KEY),
            "backup_profile_sha256": backup.profile_sha256(),
            "deployment_lock_identity": [LOCK.stat().st_dev, LOCK.stat().st_ino],
            "maintenance_parent": maintenance_parent,
            "ci": ci,
        }
        baseline_ref = io.write_new(directory / "baseline.json", baseline)
        now = datetime.now(UTC)
        plan = {
            "schema": PLAN_SCHEMA,
            "scope": SCOPE,
            "host": socket.gethostname(),
            "attempt_id": attempt,
            "sequence": head["sequence"] + 1,
            "predecessor_sha256": head["sha256"],
            "base_sha256": native["base_sha256"],
            "candidate": {**candidate, "consumer_sha256": io.sha(io.canonical(consumers))},
            "baseline_ref": baseline_ref,
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "deadline_utc": (now + timedelta(seconds=MAX_WINDOW))
            .isoformat()
            .replace("+00:00", "Z"),
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        validate_plan(plan, native["base_sha256"])
        ref = io.write_new(directory / "plan.json", plan)
        progress(directory, "prepared", plan_ref=ref)
        return {
            "ok": True,
            "attempt_id": attempt,
            "plan_ref": ref,
            "deadline_utc": plan["deadline_utc"],
        }


def marker_value(plan_ref, plan):
    return {
        "schema": "autostop.j1.crm-forward-maintenance.v1",
        "attempt_id": plan["attempt_id"],
        "plan_sha256": plan_ref["sha256"],
    }


def marker_parent(baseline):
    pin = baseline.get("maintenance_parent")
    io.require(type(pin) is dict, "maintenance_parent_pin_required")
    return pin


def store_for(plan, baseline=None):
    baseline = (
        baseline if baseline is not None else io.document(io.referenced(plan["baseline_ref"]))
    )
    return (
        recovery.RecoveryStore(plan, baseline)
        if "recovery_origin" in baseline
        else Store(STATE, plan["base_sha256"])
    )


def marker_record(directory, plan_ref, baseline):
    io.require((directory / "marker-owned.json").exists(), "marker_ownership_required")
    pointer = io.document(io.read_protected(directory / "marker-owned.json"))
    io.require(
        type(pointer) is dict
        and set(pointer) == {"plan_ref", "record"}
        and pointer["plan_ref"] == plan_ref,
        "marker_ownership_pointer_changed",
    )
    raw = io.referenced(pointer["record"])
    path = Path(pointer["record"]["path"])
    io.require(
        path.parent == directory and re.fullmatch(r"marker-identity-[0-9a-f]{32}\.json", path.name),
        "marker_ownership_path_changed",
    )
    record = io.document(raw)
    io.require(
        type(record) is dict
        and set(record) == {"plan_ref", "parent", "identity"}
        and record["plan_ref"] == plan_ref
        and io.canonical(record["parent"]) == io.canonical(marker_parent(baseline)),
        "marker_ownership_binding_changed",
    )
    return record


def authorize_marker(plan, baseline):
    store = store_for(plan, baseline)
    pending = store.pending()
    if pending is not None:
        io.require(io.canonical(pending) == io.canonical(plan), "marker_pending_changed")
    else:
        store.confirm_finished(plan)


def ensure_marker(directory, plan_ref, plan, baseline):
    pin = marker_parent(baseline)
    previous = (
        marker_record(directory, plan_ref, baseline)
        if (directory / "marker-owned.json").exists()
        else None
    )
    if previous is not None and os.path.lexists(MARKER):
        marker.require_owned(
            pin, marker_value(plan_ref, plan), expected_identity=previous["identity"]
        )
    identity = marker.ensure(
        pin, marker_value(plan_ref, plan), authorize=lambda: authorize_marker(plan, baseline)
    )
    if previous is None or io.canonical(previous["identity"]) != io.canonical(identity):
        ref = save(
            directory,
            "marker-identity",
            {"plan_ref": plan_ref, "parent": pin, "identity": identity},
        )
        io.atomic_replace(directory / "marker-owned.json", {"plan_ref": plan_ref, "record": ref})
    return require_marker(plan_ref, plan, baseline)


def require_marker(plan_ref, plan, baseline):
    pin = marker_parent(baseline)
    record = marker_record(Path(plan_ref["path"]).parent, plan_ref, baseline)
    return marker.require_owned(
        pin, marker_value(plan_ref, plan), expected_identity=record["identity"]
    )


def remove_marker(plan_ref, plan, baseline, commit_ref):
    identity = require_marker(plan_ref, plan, baseline)

    def authorize():
        store = store_for(plan, baseline)
        pending = store.pending()
        io.require(
            pending is None or io.canonical(pending) == io.canonical(plan), "marker_pending_changed"
        )
        head = store.head()
        io.require(
            head["record"] is not None
            and head["record"]["plan"] == plan
            and head["sha256"] == commit_ref["sha256"]
            and head["sequence"] == plan["sequence"],
            "marker_committed_head_required",
        )
        io.confirm_durable(commit_ref)
        io.confirm_durable(io.reference(store.head_path))
        io.require(store.head() == head, "marker_committed_head_changed")
        if pending is None:
            io.require(store.confirm_finished(plan) == commit_ref, "marker_committed_head_changed")

    marker.remove_owned(
        marker_parent(baseline),
        marker_value(plan_ref, plan),
        expected_identity=identity,
        authorize=authorize,
    )


def guard_starts(host, started_at):
    result = {}
    for unit, state in host.native["desired_units"].items():
        if not state["active"]:
            continue
        raw = host.run(
            [
                "/usr/bin/systemctl",
                "show",
                unit,
                "--no-pager",
                "-p",
                "InvocationID",
                "-p",
                "ExecCondition",
                "-p",
                "ExecStartPre",
                "-p",
                "ActiveEnterTimestampMonotonic",
                "-p",
                "MainPID",
            ],
            label="guard-starts",
        )
        value = dict(line.split("=", 1) for line in raw.decode().splitlines() if "=" in line)
        io.require(
            re.fullmatch(r"[0-9a-f]{32}", value.get("InvocationID", ""))
            and int(value.get("MainPID", "0")) > 0
            and int(value.get("ActiveEnterTimestampMonotonic", "0")) >= started_at,
            "fresh_native_invocation_required",
        )
        checks = value.get("ExecCondition", "") + value.get("ExecStartPre", "")
        records = re.findall(r"\{([^{}]*)\}", checks)
        guard = [item for item in records if "autostop-j1-candidate-guard/guard.py" in item]
        io.require(
            len(guard) == 1
            and re.search(r"(?:^|;\s*)code=exited(?:\s*;|\s*$)", guard[0])
            and re.search(r"(?:^|;\s*)status=0(?:\s*;|\s*$)", guard[0]),
            "original_guard_success_required",
        )
        result[unit] = {
            "invocation_id": value["InvocationID"],
            "main_pid": int(value["MainPID"]),
            "active_enter_monotonic_us": int(value["ActiveEnterTimestampMonotonic"]),
            "guard_execution_sha256": io.sha(checks.encode()),
        }
    io.require(len(result) == 6, "six_native_guard_starts_required")
    return result


def projection(ownership):
    return {"dropins": ownership["dropins"], "bytes_sha256": ownership["bytes_sha256"]}


def validate_payload(kind, payload, directory, plan_ref, plan, baseline):
    io.require(
        type(payload) is dict
        and payload.get("plan_sha256") == plan_ref["sha256"]
        and payload.get("baseline_sha256") == plan["baseline_ref"]["sha256"],
        "proof_payload_binding",
    )
    if kind == "offhost_restore":
        captured = io.document(io.read_protected(directory / "backup-evidence.json"))
        io.require(
            payload.get("phase") == "closed"
            and payload.get("actual_restore") is True
            and payload.get("manifest_sha256") == captured["manifest_sha256"]
            and payload.get("profile_sha256") == baseline["backup_profile_sha256"],
            "offhost_restore_required",
        )
        inspection_hash = payload.get("closed_inspection_sha256")
    else:
        io.require(payload.get("phase") == "activated", "activated_proof_required")
        inspection_hash = payload.get("inspection_sha256")
        if kind == "native_acceptance":
            io.require(
                payload.get("native_preserved") is True
                and payload.get("original_guards_executed") is True,
                "native_acceptance_required",
            )
        else:
            io.require(
                payload.get("runtime_checks_passed") is True
                and payload.get("candidate_image") == plan["candidate"]["image"]
                and payload.get("candidate_revision") == plan["candidate"]["revision"],
                "runtime_acceptance_required",
            )
    io.require(
        type(inspection_hash) is str and re.fullmatch(r"[0-9a-f]{64}", inspection_hash),
        "independent_inspection_required",
    )
    matches = [
        path
        for path in directory.glob("inspection-*.json")
        if io.reference(path)["sha256"] == inspection_hash
    ]
    io.require(len(matches) == 1, "inspection_reference_unknown")
    inspected = io.document(io.read_protected(matches[0]))
    io.require(
        inspected["stage"] == payload["phase"]
        and inspected["plan_sha256"] == plan_ref["sha256"]
        and inspected["baseline_sha256"] == plan["baseline_ref"]["sha256"],
        "inspection_binding_invalid",
    )


def receive_proof(attempt, kind, raw):
    directory, plan_ref, plan, baseline, _ = load_attempt(attempt)
    remaining(plan)
    io.require(kind in signatures.KINDS and 0 < len(raw) <= 2 * 1024**2, "proof_input_invalid")
    value = io.document(raw)
    incoming = save(directory, "incoming-proof", value)
    payload = signatures.verify_proof(incoming, kind, plan_ref, baseline["review_key_ref"])
    validate_payload(kind, payload, directory, plan_ref, plan, baseline)
    final = io.write_new(directory / (kind + ".json"), value)
    return {"ok": True, "proof": final}


def await_proof(directory, kind, plan_ref, plan, baseline, observer):
    path = directory / (kind + ".json")
    while not path.exists():
        remaining(plan)
        observer()
        time.sleep(0.3)
    ref = io.reference(path)
    payload = signatures.verify_proof(ref, kind, plan_ref, baseline["review_key_ref"])
    validate_payload(kind, payload, directory, plan_ref, plan, baseline)
    observer()
    return ref


def inspect(attempt, stage):
    if stage == "barriers":
        directory = attempt_directory(attempt)
        ownership = io.document(io.read_protected(directory / "closure-owned.json"))
        io.require(
            type(ownership) is dict
            and ownership.get("attempt_id") == attempt
            and ownership.get("attempt_dir") == str(directory),
            "barrier_attempt_binding",
        )
        observation = closure.inspect_barriers(ownership)
        return {"ok": True, "stage": stage, "observation": observation}
    io.require(stage in {"closed", "activated"}, "inspection_stage_invalid")
    directory, plan_ref, plan, baseline, host = load_attempt(attempt)
    host.deadline = time.monotonic() + remaining(plan)
    require_marker(plan_ref, plan, baseline)
    ownership = io.document(io.read_protected(directory / "closure-owned.json"))
    evidence = {}
    evidence["process_scope"] = io.document(io.referenced(ownership["process_scope_ref"]))
    drain_ref = io.reference(directory / "ingress-drain.json")
    evidence["ingress_drain"] = io.document(io.referenced(drain_ref))
    idle_ref = evidence["ingress_drain"]["idle_observation_ref"]
    evidence["ingress_idle"] = io.document(io.referenced(idle_ref))
    inspection = {
        "schema": INSPECTION,
        "stage": stage,
        "attempt_id": attempt,
        "plan_sha256": plan_ref["sha256"],
        "baseline_sha256": plan["baseline_ref"]["sha256"],
        "candidate": plan["candidate"],
        "native_preserved": True,
        "process_scope_ref": ownership["process_scope_ref"],
        "ingress_drain_ref": drain_ref,
        "ingress_idle_ref": idle_ref,
    }
    state = io.document(io.read_protected(directory / "progress.json"))
    if stage == "closed":
        io.require(state["phase"] == "awaiting-offhost-restore", "closed_inspection_phase")
        for name, path in (
            ("closure_ownership", directory / "closure-owned.json"),
            ("backup_capture", directory / "backup-evidence.json"),
            ("capture_monitor", directory / "capture-monitor.json"),
        ):
            inspection[name + "_ref"] = io.reference(path)
            evidence[name] = io.document(io.referenced(inspection[name + "_ref"]))
        captured_monitor = evidence["capture_monitor"]
        for name, record in (
            ("capture_initial_full", captured_monitor["initial_full"]),
            ("capture_terminal_full", captured_monitor["terminal_full"]),
            ("capture_first_sample", captured_monitor["first_sample"]),
            ("capture_last_sample", captured_monitor["last_sample"]),
        ):
            inspection[name + "_ref"] = record["reference"]
            evidence[name] = io.document(io.referenced(record["reference"]))
        evidence["closure"] = closure.observe_closed(ownership, force_full=True)
        evidence["native"] = authority.verify_preserved(
            baseline["native"], running=False, owned_closure=projection(ownership)
        )
        inspection.update(
            writer_closure_confirmed=True,
            closure_ref=save(directory, "inspected-closure", evidence["closure"]),
        )
    else:
        io.require(
            state["phase"] == "awaiting-native-runtime-acceptance", "activated_inspection_phase"
        )
        evidence["native"] = authority.verify_preserved(baseline["native"], running=True)
        evidence["guard_starts"] = guard_starts(host, state["native_start_monotonic_us"])
        evidence["activation_cohort"] = closure.observe_activation(ownership)
        counts = work_counts(host.job_status())
        io.require(counts["enabled"] is False, "work_must_remain_paused")
        evidence["runtime"] = host.probes(plan["candidate"], attempt, held=True)
        evidence["container"] = host.container()
        check_container_preserved(
            baseline["previous_container"], evidence["container"], plan["candidate"]
        )
        inspection.update(
            original_guards_executed=True,
            runtime_checks_passed=True,
            runtime_ref=save(directory, "inspected-runtime", evidence["runtime"]),
            container_ref=save(directory, "inspected-container", evidence["container"]),
            guard_starts_ref=save(directory, "inspected-guards", evidence["guard_starts"]),
            activation_cohort_ref=save(
                directory, "inspected-activation-cohort", evidence["activation_cohort"]
            ),
        )
    inspection["native_ref"] = save(directory, "inspected-native", evidence["native"])
    ref = save(directory, "inspection", inspection)
    result = {
        "ok": True,
        "stage": stage,
        "plan": plan,
        "plan_ref": plan_ref,
        "baseline": baseline,
        "baseline_ref": plan["baseline_ref"],
        "review_key_ref": baseline["review_key_ref"],
        "inspection": inspection,
        "inspection_ref": ref,
        "evidence": evidence,
        "backup_context": context(plan),
        "scope_profile_sha256": backup.profile_sha256(),
    }
    if stage == "closed":
        result["transport"] = io.document(io.read_protected(directory / "transport.json"))
    if "recovery_origin" in baseline:
        result["recovery"] = recovery.response_evidence(plan, baseline)
    remaining(plan)
    return result


@contextmanager
def cleanup_signals():
    handlers = {}
    if os.name == "posix":
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            handlers[signum] = signal.signal(signum, signal.SIG_IGN)
    try:
        yield
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


def record_failure(directory, plan_ref, plan, baseline, host, ownership, error):
    with cleanup_signals():
        return _record_failure(directory, plan_ref, plan, baseline, host, ownership, error)


def _record_failure(directory, plan_ref, plan, baseline, host, ownership, error):
    """Caller holds the original lock through restoration and its readback."""
    errors = []
    try:
        ensure_marker(directory, plan_ref, plan, baseline)
    except Exception:
        errors.append("maintenance_marker_restore_failed")
    try:
        host.deadline = time.monotonic() + 30
        host.manager("hold", plan["attempt_id"])
    except Exception:
        errors.append("scheduler_hold_restore_failed")
    if ownership is None and (directory / "closure-owned.json").exists():
        ownership = io.document(io.read_protected(directory / "closure-owned.json"))
    try:
        closed = closure.fail_closed(ownership) if ownership is not None else None
    except Exception:
        closed = None
        errors.append("owned_closure_restore_failed")
    if closed is None:
        errors.append("closure_readback_unavailable")
    failure = {
        "ok": False,
        "error_type": type(error).__name__,
        "error": str(error) if isinstance(error, io.Refusal) else "forward_operation_failed",
        "plan_ref": plan_ref,
        "closure": closed,
        "errors": errors,
        "old_writer_allowed": False,
        "production_restore_attempted": False,
    }
    failure_ref = save(directory, "failure", failure)
    if not (directory / "failure.json").exists():
        io.write_new(directory / "failure.json", failure)
    proven = (
        closed is not None
        and closed.get("observation") is not None
        and not closed.get("errors")
        and not errors
    )
    progress(
        directory, "failed-closed" if proven else "failure-closure-incomplete", failure=failure_ref
    )
    return failure


def apply(attempt):
    directory, plan_ref, plan, baseline, host = load_attempt(attempt)
    store = store_for(plan, baseline)
    io.require(store.pending() is None, "pending_forward_requires_recovery")
    io.require(
        not (directory / "failure.json").exists(),
        "failed_attempt_requires_reviewed_forward_recovery",
    )
    ownership = None
    mutation_owned = False
    failure_recorded = False
    monitor = None

    def locked_failure():
        nonlocal failure_recorded
        if mutation_owned:
            with cleanup_signals():
                if monitor is not None:
                    if not monitor.cancel():
                        # A reader may still own closure's full-scan lock. End
                        # only this consumer; the fixed ExecStopPost reconciles
                        # the durable pending attempt after all threads die.
                        # Never mutate the closure concurrently with readers.
                        failure_recorded = True
                        try:
                            progress(directory, "failure-closure-incomplete", readers_stopped=False)
                        finally:
                            os._exit(2)
                record_failure(
                    directory, plan_ref, plan, baseline, host, ownership, sys.exception()
                )
            failure_recorded = True

    try:
        with deployment_lock(baseline["deployment_lock_identity"], on_failure=locked_failure):
            host.deadline = time.monotonic() + remaining(plan)
            recovering = "recovery_origin" in baseline
            if recovering:
                ownership = recovery.prepared_ownership(directory, baseline)
                authority.verify_preserved(
                    baseline["native"],
                    running=True,
                    owned_closure=projection(ownership),
                    preclose=True,
                )
            else:
                authority.verify_preserved(baseline["native"], running=True)
            current = host.container()
            io.require(current == baseline["previous_container"], "baseline_container_changed")
            io.require(not MARKER.exists(), "preexisting_maintenance_marker")
            io.require(
                io.canonical(marker.prepare()) == io.canonical(marker_parent(baseline)),
                "maintenance_parent_changed",
            )
            if not recovering:
                ownership = closure.prepare_closure(baseline["native"], directory)
            try:
                store.begin(plan)
            except BaseException:
                # Publication may have completed before an fsync/readback
                # failed. Only an exact owned pending permits cleanup effects;
                # a competing transaction must never grant writer ownership.
                try:
                    mutation_owned = store.pending() == plan
                except Exception:
                    mutation_owned = False
                raise
            mutation_owned = True
            ensure_marker(directory, plan_ref, plan, baseline)
            progress(directory, "draining")
            save(directory, "drain", host.begin_drain(attempt, baseline["host"]))

            def idle_observer():
                return save(
                    directory,
                    "ingress-idle",
                    {"observed_at": utc(), **host.observe_idle_appserver()},
                )

            ownership = (
                closure.close_recovery(
                    ownership, deadline=host.deadline, idle_observer=idle_observer
                )
                if recovering
                else closure.close_writers(
                    baseline["native"],
                    directory,
                    deadline=host.deadline,
                    idle_observer=idle_observer,
                )
            )

            def marker_check():
                remaining(plan)
                require_marker(plan_ref, plan, baseline)

            monitor = ClosureMonitor(
                lambda: save(
                    directory,
                    "closed-observation",
                    closure.observe_closed(ownership, force_full=True),
                ),
                lambda: closure.sample_closed(ownership),
                marker_check,
                {
                    "attempt_id": attempt,
                    "boot_id": ownership["boot_id"],
                    "closure_ref": io.reference(directory / "closure-owned.json"),
                    "process_scope_ref": ownership["process_scope_ref"],
                    "ingress_drain_ref": io.reference(directory / "ingress-drain.json"),
                    "plan_ref": plan_ref,
                    "consumer_sha256": plan["candidate"]["consumer_sha256"],
                },
                deadline=host.deadline,
            )
            monitor.start()
            witness = monitor.fence
            monitor.begin_capture()
            captured = backup.capture(
                directory / "backup", context(plan), witness, timeout=min(remaining(plan), 3600)
            )
            captured_monitor = monitor.finish_capture()
            captured["capture_monitor_ref"] = io.write_new(
                directory / "capture-monitor.json", captured_monitor
            )
            captured["terminal_full_ref"] = captured_monitor["terminal_full"]["reference"]
            witness()
            io.write_new(directory / "backup-evidence.json", captured)
            known = io.reference(CACHE_INVENTORY) if CACHE_INVENTORY.exists() else None
            packed = transport.bundle_delta(
                directory / "backup",
                captured["manifest_sha256"],
                known,
                directory / "delta.tar.gz",
                timeout=min(remaining(plan), 3600),
            )
            packed_ref = packed["archive"]
            io.write_new(
                directory / "transport.json",
                {**packed_ref, "manifest_sha256": captured["manifest_sha256"]},
            )
            witness()
            progress(
                directory, "awaiting-offhost-restore", manifest_sha256=captured["manifest_sha256"]
            )
            restored = await_proof(directory, "offhost_restore", plan_ref, plan, baseline, witness)
            authority.verify_preserved(
                baseline["native"], running=False, owned_closure=projection(ownership)
            )
            witness()
            monitor.stop()
            remaining(plan)
            require_marker(plan_ref, plan, baseline)
            progress(directory, "activating-crm")
            # The accepted quiet interval ends here. Reopen only the owned
            # barrier before introducing the candidate's first writable mount.
            closure.reopen_native(ownership, deadline=host.deadline)
            host.replace_crm(plan["candidate"])
            native_start = int(time.monotonic() * 1_000_000)
            host.start_native()
            authority.verify_preserved(baseline["native"], running=True)
            guard_starts(host, native_start)
            closure.observe_activation(ownership)
            io.require(
                work_counts(host.job_status())["enabled"] is False,
                "work_reopened_before_acceptance",
            )
            progress(
                directory,
                "awaiting-native-runtime-acceptance",
                native_start_monotonic_us=native_start,
            )

            def still_held():
                remaining(plan)
                require_marker(plan_ref, plan, baseline)
                closure.observe_activation(ownership)
                io.require(
                    work_counts(host.job_status())["enabled"] is False,
                    "work_reopened_before_commit",
                )
                remaining(plan)

            native = await_proof(
                directory, "native_acceptance", plan_ref, plan, baseline, still_held
            )
            runtime = await_proof(
                directory, "runtime_acceptance", plan_ref, plan, baseline, still_held
            )
            host.validate_manager(host.manager("status"), attempt, held=True)
            authority.verify_preserved(baseline["native"], running=True)
            check_container_preserved(
                baseline["previous_container"], host.container(), plan["candidate"]
            )
            still_held()
            proofs = {
                "closure": io.reference(directory / "closure-owned.json"),
                "backup": io.reference(directory / "backup-evidence.json"),
                "offhost_restore": restored,
                "native_acceptance": native,
                "runtime_acceptance": runtime,
            }
            commit = store.commit(plan, proofs)
            # This durable transaction MUST precede removal of the old marker.
            io.require(store.head()["record"]["plan"] == plan, "durable_head_readback_failed")
            progress(directory, "committed-opening", commit=commit)
            auxiliary_release = save(
                directory, "auxiliary-release", closure.release_auxiliary(ownership, commit)
            )
            remove_marker(plan_ref, plan, baseline, commit)
        # Original duty restoration acquires the same lock, so release it only
        # after HEAD is durable and marker removal has been synchronized.
        host.deadline = time.monotonic() + 180
        host.manager("release-hold", attempt)
        host.restore_duty()
        io.require(
            work_counts(host.job_status())["enabled"] is baseline["native"]["desired_inbound"],
            "desired_duty_not_restored",
        )
        final = host.probes(plan["candidate"], attempt, held=False)
        authority.verify_preserved(baseline["native"], running=True)
        check_container_preserved(
            baseline["previous_container"], host.container(), plan["candidate"]
        )
        released_cohort = (
            save(directory, "released-cohort", closure.observe_released(ownership))
            if recovering
            else None
        )
        completed = io.write_new(
            directory / "completion.json",
            {
                "ok": True,
                "plan_ref": plan_ref,
                "commit": commit,
                "actual_container": host.container(),
                "checks": final,
                "auxiliary_release_ref": auxiliary_release,
                **({"released_cohort_ref": released_cohort} if recovering else {}),
                "completed_at": utc(),
            },
        )
        store.finish(plan)
        progress(directory, "complete", completion=completed)
        return {"ok": True, "completion": completed, "attempt_id": attempt}
    except BaseException as exc:
        if not mutation_owned:
            raise
        if not failure_recorded:
            with deployment_lock(baseline["deployment_lock_identity"]):
                pending = store.pending()
                io.require(
                    pending == plan or (pending is None and store.head()["record"]["plan"] == plan),
                    "failure_pending_changed",
                )
                record_failure(directory, plan_ref, plan, baseline, host, ownership, exc)
        raise


def export_delta(attempt):
    directory, _, plan, _, _ = load_attempt(attempt)
    remaining(plan)
    ref = io.document(io.read_protected(directory / "transport.json"))
    path = directory / "delta.tar.gz"
    io.require(ref["path"] == str(path), "transport_path_invalid")
    info = path.lstat()
    io.require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == 0
        and info.st_nlink == 1
        and not info.st_mode & 0o077
        and info.st_size <= transport.MAX_ENVELOPE,
        "transport_file_invalid",
    )
    # The receiver re-hashes the complete archive before parsing any member.
    with path.open("rb") as stream:
        while block := stream.read(1024**2):
            remaining(plan)
            sys.stdout.buffer.write(block)


def build():
    """Build only the published commit, before maintenance or writer effects."""
    io.require(not MARKER.exists(), "build_requires_open_installation")
    if not ROOT.exists():
        ROOT.mkdir(mode=0o700)
    io.protected_directory(ROOT, private=True)
    host = Host({}, ROOT)
    with deployment_lock():
        io.require(not os.path.lexists(STATE / "active-recovery.json"), "state_recovery_active")
        source = host.source()
        scope = backup.inventory()
        import shutil

        needed = (
            sum(component["bytes"] for component in scope["components"].values()) * 2 + 4 * 1024**3
        )
        io.require(shutil.disk_usage(CRM).free >= needed, "build_and_backup_disk_headroom_required")
        logfile = ROOT / ("build-" + source["revision"] + "-" + uuid.uuid4().hex + ".log")
        tag = "autostopcrm-forward:" + source["revision"]
        with logfile.open("xb") as log:
            archive = subprocess.Popen(
                ["/usr/bin/git", "-C", str(CRM), "archive", "--format=tar", source["revision"]],
                stdout=subprocess.PIPE,
                stderr=log,
                env={"PATH": "/usr/bin:/bin"},
            )
            try:
                result = subprocess.run(
                    DOCKER
                    + [
                        "build",
                        "--label",
                        "org.opencontainers.image.revision=" + source["revision"],
                        "--label",
                        "org.autostop.crm.tree=" + source["tree"],
                        "--tag",
                        tag,
                        "-",
                    ],
                    stdin=archive.stdout,
                    stdout=log,
                    stderr=log,
                    timeout=1800,
                    check=False,
                    env={"PATH": "/usr/bin:/bin", "LANG": "C"},
                )
                archive.stdout.close()
                io.require(
                    archive.wait(timeout=15) == result.returncode == 0, "commit_image_build_failed"
                )
                log.flush()
                os.fsync(log.fileno())
            finally:
                if archive.poll() is None:
                    archive.kill()
                    archive.wait()
        io.require(host.source() == source, "source_changed_during_build")
        candidate = host.image(tag)
        io.require(
            {key: candidate[key] for key in ("revision", "tree")} == source, "built_image_mismatch"
        )
        return {"ok": True, "candidate": candidate, "build_log": str(logfile)}


def settle(attempt):
    """ExecStopPost safety net, including SIGKILL of the main consumer."""
    directory, plan_ref, plan, baseline, host = load_attempt(attempt, verify_published=False)
    store = store_for(plan, baseline)

    def owned_closure():
        ownership = io.document(io.read_protected(directory / "closure-owned.json"))
        io.require(
            ownership.get("schema") == closure.SCHEMA
            and ownership.get("attempt_id") == attempt
            and ownership.get("attempt_dir") == str(directory)
            and ownership.get("baseline_sha256") == io.sha(io.canonical(baseline["native"])),
            "stop_post_closure_binding_changed",
        )
        return ownership

    def refence(error):
        ownership = owned_closure()
        failure = record_failure(directory, plan_ref, plan, baseline, host, ownership, error)
        closed = failure.get("closure")
        proven = (
            closed is not None
            and closed.get("observation") is not None
            and not closed.get("errors")
            and not failure["errors"]
        )
        return {"ok": False, "failure_closed": proven}

    # A complete receipt is published before pending is cleared. It cannot
    # authorize recovery of an interrupted transition, even if HEAD committed.
    # Check pending first: a killed history publication may make HEAD unreadable.
    with cleanup_signals(), deployment_lock(baseline["deployment_lock_identity"]):
        try:
            pending = store.pending()
        except Exception as exc:
            io.require(MARKER.exists(), "stop_post_owned_marker_required")
            require_marker(plan_ref, plan, baseline)
            return refence(exc)
        if pending is not None:
            io.require(pending == plan, "stop_post_pending_changed")
            return refence(io.Refusal("consumer_terminated_with_pending_transition"))

        owned_marker = False
        if MARKER.exists():
            require_marker(plan_ref, plan, baseline)
            owned_marker = True
        try:
            head = store.head()
            same_head = head["record"] is not None and head["record"]["plan"] == plan
        except Exception as exc:
            # A damaged HEAD is not authority to act on another generation.
            # The exact protected marker still identifies this writer attempt.
            if owned_marker:
                return refence(exc)
            raise
        if not same_head:
            io.require(
                head.get("sequence") == plan["sequence"] - 1
                and head.get("sha256") == plan["predecessor_sha256"],
                "stop_post_head_changed",
            )
        if owned_marker:
            return refence(io.Refusal("consumer_terminated_with_owned_maintenance"))
        if not same_head:
            # Before begin there are no writer effects. Historical receipts
            # must never authorize stopping a newer committed generation.
            io.require(
                not (directory / "completion.json").exists()
                and not (directory / "failure.json").exists(),
                "stop_post_head_changed",
            )
            state = io.document(io.read_protected(directory / "progress.json"))
            io.require(
                state.get("phase") == "prepared" and state.get("plan_ref") == plan_ref,
                "stop_post_unowned_incomplete_state",
            )
            if (directory / "closure-owned.json").exists():
                ownership = owned_closure()
                io.require(
                    all(not Path(item["path"]).exists() for item in ownership["dropins"]),
                    "stop_post_unowned_writer_barriers_present",
                )
            return {"ok": True, "writer_mutation_not_begun": True}

        # Admission and its failure restoration retain the same original lock.
        # No failure below may leave a previously completed but now invalid
        # generation running, nor delete pending as a substitute for admission.
        try:
            io.require(not (directory / "failure.json").exists(), "completion_has_later_failure")
            completion_ref = io.reference(directory / "completion.json")
            completed = io.document(io.referenced(completion_ref))
            state_ref = io.reference(directory / "progress.json")
            state = io.document(io.referenced(state_ref))
            io.require(
                completed.get("ok") is True
                and completed.get("plan_ref") == plan_ref
                and state.get("phase") == "complete"
                and state.get("completion") == completion_ref,
                "completion_binding_invalid",
            )
            ownership = owned_closure()
            io.require(
                all(not Path(item["path"]).exists() for item in ownership["dropins"]),
                "completed_writer_barriers_present",
            )
            commit = store.confirm_finished(plan)
            io.require(completed.get("commit") == commit, "completion_commit_binding_invalid")
            io.confirm_durable(completion_ref)
            io.confirm_durable(state_ref)
            host.deadline = time.monotonic() + 90
            authority.verify_preserved(baseline["native"], running=True)
            check_container_preserved(
                baseline["previous_container"], host.container(), plan["candidate"]
            )
            io.require(
                work_counts(host.job_status())["enabled"] is baseline["native"]["desired_inbound"],
                "completed_duty_changed",
            )
            host.validate_manager(host.manager("status"), attempt, held=False)
            if "recovery_origin" in baseline:
                for key, label in (
                    ("auxiliary_release_ref", "auxiliary-release"),
                    ("released_cohort_ref", "released-cohort"),
                ):
                    reference = completed.get(key)
                    io.require(
                        type(reference) is dict
                        and Path(reference.get("path", "")).parent == directory
                        and re.fullmatch(
                            label + r"-[0-9a-f]{32}\.json", Path(reference["path"]).name
                        ),
                        "completed_cohort_reference_required",
                    )
                    cohort = io.document(io.referenced(reference))
                    io.require(
                        cohort.get("attempt_id") == attempt
                        and cohort.get("process_scope_ref") == ownership["process_scope_ref"],
                        "completed_cohort_binding_changed",
                    )
                closure.observe_released(ownership)
            return {"ok": True, "completed": True}
        except BaseException as exc:
            return refence(exc)


def launch(attempt):
    """Keep the transition alive across SSH loss and fence on service death."""
    directory, plan_ref, plan, baseline, host = load_attempt(attempt)
    remaining(plan)
    unit = "autostop-crm-forward-" + attempt
    command = [
        "/usr/bin/systemd-run",
        "--quiet",
        "--wait",
        "--collect",
        "--unit=" + unit,
        "--property=Type=exec",
        "--property=RuntimeMaxSec=900",
        "--property=TimeoutStopSec=120",
        "--property=KillSignal=SIGTERM",
        "--property=ExecStopPost=/usr/bin/python3 -I -S -B /opt/autostopcrm/scripts/j1_forward_release.py settle --attempt "
        + attempt,
        "--setenv=LD_LIBRARY_PATH=/opt/autostop-sqlite/3.51.3/lib",
        "/usr/bin/python3",
        "-I",
        "-S",
        "-B",
        str(CRM / "scripts/j1_forward_release.py"),
        "run",
        "--attempt",
        attempt,
    ]
    host.run(command, label="forward-service", timeout=1100)
    with deployment_lock(baseline["deployment_lock_identity"]):
        io.require(
            not (directory / "failure.json").exists() and not MARKER.exists(),
            "completed_generation_failure_present",
        )
        completion_ref = io.reference(directory / "completion.json")
        completion = io.document(io.referenced(completion_ref))
        state_ref = io.reference(directory / "progress.json")
        state = io.document(io.referenced(state_ref))
        io.require(
            completion.get("ok") is True
            and completion.get("plan_ref") == plan_ref
            and state.get("phase") == "complete"
            and state.get("completion") == completion_ref,
            "completed_generation_readback_required",
        )
        commit = store_for(plan, baseline).confirm_finished(plan)
        io.require(completion.get("commit") == commit, "completed_generation_commit_changed")
        io.confirm_durable(completion_ref)
        io.confirm_durable(state_ref)
    return {
        "ok": True,
        "attempt_id": attempt,
        "completion": completion_ref,
    }


def interrupted(signum, _frame):
    raise io.Refusal("forward_signal_" + str(signum))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--image", required=True)
    prepare_parser.add_argument("--ci-run-id", type=int, required=True)
    recovery_parser = sub.add_parser("prepare-recovery")
    recovery_parser.add_argument("--image", required=True)
    recovery_parser.add_argument("--ci-run-id", type=int, required=True)
    recovery_parser.add_argument("--origin-attempt", required=True)
    origin_parser = sub.add_parser("seal-origin")
    origin_parser.add_argument("--attempt", required=True)
    sub.add_parser("build")
    for operation in ("apply", "run", "settle", "inspect", "receive-proof", "export-delta"):
        child = sub.add_parser(operation)
        child.add_argument("--attempt", required=True)
        if operation == "inspect":
            child.add_argument(
                "--stage", choices=("closed", "activated", "barriers"), required=True
            )
        if operation == "receive-proof":
            child.add_argument("--kind", choices=sorted(signatures.KINDS), required=True)
    args = parser.parse_args()
    try:
        io.require(os.name == "posix" and os.geteuid() == 0, "linux_native_root_required")
        if args.command == "prepare":
            result = prepare(args.image, args.ci_run_id)
        elif args.command == "prepare-recovery":
            result = recovery.prepare(args.image, args.ci_run_id, args.origin_attempt)
        elif args.command == "seal-origin":
            result = recovery.seal_origin(args.attempt)
        elif args.command == "build":
            result = build()
        elif args.command == "apply":
            result = launch(args.attempt)
        elif args.command == "run":
            cgroup = Path("/proc/self/cgroup").read_text()
            io.require(
                "/autostop-crm-forward-" + args.attempt + ".service" in cgroup,
                "owned_systemd_consumer_required",
            )
            for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                signal.signal(signum, interrupted)
            result = apply(args.attempt)
        elif args.command == "settle":
            result = settle(args.attempt)
        elif args.command == "inspect":
            result = inspect(args.attempt, args.stage)
        elif args.command == "receive-proof":
            result = receive_proof(args.attempt, args.kind, sys.stdin.buffer.read(2 * 1024**2 + 1))
        else:
            export_delta(args.attempt)
            return 0
        sys.stdout.buffer.write(io.canonical(result))
        return 0 if result.get("ok") is True else 2
    except Exception as exc:
        error = str(exc) if isinstance(exc, io.Refusal) else type(exc).__name__
        sys.stderr.buffer.write(io.canonical({"ok": False, "error": error}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
