"""Private cold trace assembly around the actual durable recovery controller.

The source/CI receipt below describes this fixture only. It deliberately does
not assert production branch CI eligibility or production application fitness.
Native effectors are restricted to the supplied owned private topology.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal


def _capture_data(fixture, engine, output, context, observer, *, timeout, diagnostic_interval=None):
    """Keep actual capture unchanged and report private inventory drift on refusal."""
    io.require(fixture.provider.scope == "native_fixture", "trace_private_provider_required")
    original, initial = engine.build_profile, None

    def profile(*args, **kwargs):
        nonlocal initial
        result = original(*args, **kwargs)
        if initial is None:
            initial = result
        return result

    engine.build_profile = profile
    try:
        arguments = {"timeout": timeout}
        if engine.COLD_VERSION == 4:
            arguments["diagnostic_interval"] = diagnostic_interval
        return engine.capture(output, context, observer, **arguments)
    except engine.BackupRefusal as error:
        if str(error) in {"inventory_changed", "final_inventory_changed"}:
            changed = []
            for item in initial or ():
                io.require(item.source.is_relative_to(fixture.root), "trace_private_inventory")
                try:
                    actual = tuple(engine.version(item.source.lstat()))
                except FileNotFoundError:
                    actual = ()
                if actual != item.source_version:
                    changed.append(
                        {
                            "component": item.component,
                            "logical_name": item.logical_name,
                            "expected_version": item.source_version,
                            "actual_version": actual,
                        }
                    )
                    if len(changed) == 16:
                        break
            print("PRIVATE_BACKUP_DRIFT " + io.canonical(changed).decode().strip(), flush=True)
        raise
    finally:
        engine.build_profile = original


def private_profile(fixture, engine, business_roots):
    """Materialize all real v3 root types using only artificial private data."""
    aliases = dict(business_roots)
    package = io.document(fixture.provider.read_reference(fixture.package_ref))
    aliases.update(
        cold_source=str(fixture.root / "source"),
        cold_config=str(fixture.root / "execution/roles"),
        cold_state=str(fixture.root / "execution/roles"),
        cold_controller_code=str(fixture.root / "source/scripts"),
        cold_controller_state=str(fixture.root / "execution"),
    )
    from scripts import j1_forward_cold_authority as authority

    for index, unit in enumerate(authority.ALLOWED):
        policy = io.document(fixture.provider.read_reference(package["policies"][unit]))
        effective = io.document(fixture.provider.read_reference(policy["unit_ref"]))
        aliases["unit_cold_" + str(index)] = effective["files"][0]["path"]
    materialized = fixture._mkdir("profile-materialized")
    for name, source in engine.ROOTS:
        if name in aliases:
            continue
        path = materialized / name / Path(source).name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if source in engine.SELECTORS:
            target = path.parent / "sealed-release"
            target.mkdir(mode=0o700)
            for filename, raw in (
                ("REVISION", (fixture.source["revision"] + "\n").encode()),
                ("MANIFEST.files0", b"synthetic-private-source-dependency\0"),
                (
                    "MANIFEST.sha256",
                    (io.sha(b"synthetic-private-source-dependency\0") + "\n").encode(),
                ),
            ):
                fixture._copy(target / filename, raw)
            path.symlink_to(target, target_is_directory=True)
        elif engine.expected_root_kind(name, source) == "file":
            if name.startswith("unit_") and source.endswith(".service"):
                unit = Path(source).name
                raw = (fixture.root / "barriers" / unit).read_bytes()
            else:
                raw = b"ARTIFICIAL_FIXTURE_ONLY=1\n"
            fixture._copy(path, raw)
        else:
            path.mkdir(mode=0o700)
            fixture._copy(path / "artificial.fixture", b"no production account or data\n")
        aliases[name] = str(path)
    io.require(set(aliases) == {name for name, _ in engine.ROOTS}, "trace_full_cold_root_inventory")
    return tuple((name, aliases[name]) for name, _ in engine.ROOTS)


def prepare_attempt(fixture, package_ref, epoch_ref, crm_spec_ref, signer, *, engine=None):
    """Keep the original pending intact and begin a separate cold recovery."""
    from scripts import j1_forward_cold_authority as contract
    from scripts import j1_forward_cold_state as cold_state

    engine = rehearsal.backup if engine is None else engine

    io.require(fixture.provider.scope == "native_fixture", "trace_private_provider_required")
    io.require(
        fixture.binding["candidate_crm_sha"] == fixture.source["revision"],
        "trace_checkout_candidate_binding",
    )
    io.require(
        Path(crm_spec_ref["path"]).is_relative_to(fixture.root),
        "trace_private_crm_spec_required",
    )
    io.referenced(crm_spec_ref)
    directory = fixture.control / "attempts" / fixture.binding["attempt_id"]
    consumer_root = directory / "consumers"
    for name, raw in fixture.source_bytes.items():
        fixture._copy(consumer_root / name, raw)
    source = fixture._write(
        directory / "recovery-source.json",
        {
            "schema": "autostop.j1.private-rehearsal-source.v1",
            "source_revision": fixture.source["revision"],
            "source_tree": fixture.source["tree"],
            "source_consumers": fixture.consumers,
            "consumer_sha256": io.sha(io.canonical(fixture.consumers)),
        },
    )
    ci = fixture._write(
        directory / "fixture-ci-boundary.json",
        {
            "schema": "autostop.j1.private-rehearsal-ci-boundary.v1",
            "source_revision": fixture.source["revision"],
            "scope": "private_fixture_source_exact",
            "hosted_production_branch_ci_verified": False,
        },
    )
    origin = {**fixture.origin, "recovery_code_ref": source, "ci_ref": ci}
    baseline = {
        "schema": cold_state.BASELINE_SCHEMA,
        "attempt_id": fixture.binding["attempt_id"],
        "native": {
            "base_sha256": fixture.base_sha256,
            "desired_inbound": False,
            "scope": "native_fixture",
            "cold_authority_package_ref": package_ref,
            "dispatch_epoch_ref": epoch_ref,
        },
        "source_consumers": fixture.consumers,
        "deployment_lock_identity": fixture.binding["deployment_lock_identity"],
        "backup_profile_sha256": engine.profile_sha256(),
        "review_key_ref": io.reference(signer.public),
        "controller_request_ref": fixture.parent.request_ref,
        "controller_launch_ref": fixture.parent.manifest_ref,
        "recovery_origin": origin,
        "host": {},
        "previous_container": {"image": "synthetic-original", "running": False},
    }
    baseline_ref = fixture._write(directory / "baseline.json", baseline)
    plan = fixture._plan(fixture.binding["attempt_id"], created=datetime.now(UTC))
    plan.update(
        schema=contract.PLAN_SCHEMA,
        scope=contract.PLAN_SCOPE,
        baseline_ref=baseline_ref,
        cold_authority_package_ref=package_ref,
        dispatch_epoch_ref=epoch_ref,
        cold_crm_spec_ref=crm_spec_ref,
        candidate_manager_sha=fixture.binding["candidate_manager_sha"],
    )
    plan_ref = fixture._write(directory / "plan.json", plan)
    envelope = {
        "schema": cold_state.RECOVERY_SCHEMA,
        "recovery_id": plan["attempt_id"],
        "plan_ref": plan_ref,
        **origin,
    }

    class PrivateColdStore(cold_state.ColdStore):
        def _read_reference(self, reference):
            if Path(reference["path"]).is_relative_to(fixture.root):
                return super()._read_reference(reference)
            # The provider admits only its captured exact interpreter ref
            # outside this private root. It cannot read arbitrary host files.
            return fixture.provider.read_reference(reference)

        def read_opaque_reference(self, reference):
            return fixture.provider.read_opaque_reference(reference)

        def read_runtime_capsule(self, reference):
            return fixture.provider.read_runtime_capsule(reference)

    store = PrivateColdStore(fixture.control / "state", fixture.base_sha256)
    reference = store.begin_recovery(envelope)
    io.require(store.pending() != plan, "trace_original_pending_replaced")
    return {
        "store": store,
        "envelope": envelope,
        "recovery_ref": reference,
        "plan": plan,
        "plan_ref": plan_ref,
        "baseline": baseline,
        "directory": directory,
        "consumer_root": consumer_root,
        "engine": engine,
    }


def capture_closed(fixture, attempt, controller, *, codec=5):
    """Run the actual bounded capture protocol, then its typed control archive."""
    from scripts import j1_forward_archive as archive
    from scripts import j1_forward_cold_backup as backup
    from scripts import j1_forward_cold_native as native
    from scripts import j1_forward_cold_release as coordinator
    from scripts import j1_forward_release as forward

    directory, plan, plan_ref = attempt["directory"], attempt["plan"], attempt["plan_ref"]
    package = io.document(fixture.provider.read_reference(fixture.package_ref))
    scope_ref = fixture._write(
        directory / "process-scope.json",
        {
            "schema": "autostop.j1.private-cold-closed-scope.v1",
            "attempt_id": plan["attempt_id"],
            "package_ref": fixture.package_ref,
            "retired_scope_ref": fixture.origin["origin_scope_ref"],
            "epoch_ref": plan["dispatch_epoch_ref"],
        },
    )
    closure_ref = fixture._write(
        directory / "closure-owned.json",
        {
            "attempt_id": plan["attempt_id"],
            "process_scope_ref": scope_ref,
            "baseline_sha256": io.sha(io.canonical(attempt["baseline"]["native"])),
            "consumer_sha256": plan["candidate"]["consumer_sha256"],
            "package_closure_ref": package["closure_ref"],
            "package_hold_ref": package["hold_ref"],
        },
    )
    fixture._write(
        directory / "ingress-drain.json",
        {
            "schema": "autostop.j1.private-cold-ingress-disposition.v1",
            "attempt_id": plan["attempt_id"],
            "retirement_ref": package["retirement_ref"],
            "quarantine_ref": package["quarantine_ref"],
            "pending_ref": package["pending_ref"],
            "old_requests_replayed": False,
        },
    )
    inspection = fixture._write(
        directory / "inspection-closed.json",
        {
            "stage": "closed",
            "scope": "native_fixture",
            "plan_sha256": plan_ref["sha256"],
            "baseline_sha256": plan["baseline_ref"]["sha256"],
            "package_ref": fixture.package_ref,
            "production_acceptance": False,
        },
    )
    engine = attempt["engine"]
    context = {
        "attempt_id": plan["attempt_id"],
        "predecessor_sha256": plan["predecessor_sha256"],
        "candidate_crm_sha": plan["candidate"]["revision"],
        "candidate_manager_sha": plan["candidate_manager_sha"],
        "scope_profile_sha256": engine.profile_sha256(),
    }
    deadline = time.monotonic() + min(300, forward.remaining(plan))

    def gate():
        forward.remaining(plan)
        io.require(time.monotonic() < deadline, "cold_capture_deadline")

    data_started = time.perf_counter()
    with backup.CaptureInterval(controller, plan_ref, directory, gate=gate) as interval:
        captured = _capture_data(
            fixture,
            engine,
            directory / "backup",
            context,
            interval.observe,
            timeout=min(deadline - time.monotonic(), forward.remaining(plan)),
            diagnostic_interval=interval,
        )
    print(
        "PRIVATE_COLD_CAPTURE_TIMING "
        + io.canonical(
            {
                "kind": "data_interval",
                "wall_seconds": time.perf_counter() - data_started,
                "limit_seconds": 300,
            }
        )
        .decode()
        .strip(),
        flush=True,
    )
    captured = {**captured, "capture_interval_ref": interval.completion_ref}
    backup_ref = fixture._write(directory / "backup-evidence.json", captured)

    def observer():
        forward.remaining(plan)
        return fixture._write(
            directory / ("archive-native-" + str(time.monotonic_ns()) + ".json"),
            coordinator._native_observation(plan, controller, "closed", _observation=observation),
        )

    control_started = time.perf_counter()
    control_deadline = time.monotonic() + min(60, forward.remaining(plan))
    with native.PackageRequalification(
        controller.package_ref,
        provider=fixture.provider,
        required_scope=fixture.provider.scope,
        max_calls=3,
    ) as observation:
        archived = archive.capture(
            fixture.control,
            plan_ref,
            captured["manifest"],
            fixture.root / "control-closed-package",
            stage="closed",
            observer=observer,
            codec=codec,
            consumer_roots={plan["candidate"]["consumer_sha256"]: attempt["consumer_root"]},
            authority_reader=fixture.provider.read_reference,
            authority_file_reader=fixture.custody.read,
            timeout=min(control_deadline - time.monotonic(), forward.remaining(plan)),
        )
    forward.remaining(plan)
    io.require(time.monotonic() < control_deadline, "cold_control_capture_deadline")
    print(
        "PRIVATE_COLD_CAPTURE_TIMING "
        + io.canonical(
            {
                "kind": "control_archive",
                "wall_seconds": time.perf_counter() - control_started,
                "limit_seconds": 60,
            }
        )
        .decode()
        .strip(),
        flush=True,
    )
    fixture._write(directory / "control-closed.json", archived)
    return {
        "captured": captured,
        "backup_ref": backup_ref,
        "archived": archived,
        "closed_inspection_ref": inspection,
        "closure_ref": closure_ref,
        "context": context,
    }


def bind_verified_inspection(fixture, attempt, signer, kind, proof_ref, inspection_ref):
    """Preserve the actual measured body after verifying its signed digest.

    This does not create an observation. The inspection already contains the
    real fixture observer/acceptance references; the binding makes those bytes
    reachable from the control graph rather than retaining only a digest.
    """
    from scripts import j1_forward_variant as variant

    payload = rehearsal.signatures.verify_proof(
        proof_ref,
        kind,
        attempt["plan_ref"],
        io.reference(signer.public),
        plan_validator=variant.validate_plan,
    )
    field = "closed_inspection_sha256" if kind == "offhost_restore" else "inspection_sha256"
    inspection = io.document(fixture.provider.read_reference(inspection_ref))
    io.require(
        payload.get(field) == inspection_ref["sha256"]
        and inspection.get("plan_sha256") == attempt["plan_ref"]["sha256"]
        and inspection.get("baseline_sha256") == attempt["plan"]["baseline_ref"]["sha256"]
        and inspection.get("stage") == payload.get("phase"),
        "trace_signed_inspection_binding",
    )
    return fixture._write(
        attempt["directory"] / ("proof-binding-" + kind + ".json"),
        {
            "plan_ref": attempt["plan_ref"],
            "proof_ref": proof_ref,
            "inspection_ref": inspection_ref,
        },
    )


def measured_receipt(fixture, started, *, stages):
    """Report process high water marks and bytes, without private evidence data."""
    import resource

    return {
        "schema": "autostop.j1.private-release-rehearsal-measurements.v1",
        "scope": "native_fixture",
        "source_revision": fixture.source["revision"],
        "source_tree": fixture.source["tree"],
        "wall_seconds": time.perf_counter() - started,
        "runner_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "children_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "private_regular_bytes": rehearsal.measure_private_bytes(fixture.root),
        "stages": stages,
        "same_host_private_custodian": True,
        "production_restore_performed": False,
        "production_release_accepted": False,
        "hosted_production_branch_ci_verified": False,
    }


def write_report(output, value):
    output = Path(output)
    io.protected_directory(output.parent)
    return io.write_new(output, value)
