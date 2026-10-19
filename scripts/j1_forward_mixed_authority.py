"""Explicit warm authority for one successor MCP and five original profiles.

No old observer schema is reinterpreted. The original accepted warm cohort is
required in full; historical recovery observations or cold replacements cannot
substitute for that native authority. This module never grants a native permit.
"""

from __future__ import annotations

import time
from pathlib import Path

from scripts import j1_forward_authority as original
from scripts import j1_forward_io as io
from scripts import j1_forward_state as state
from scripts import j1_manager_successor_guard_v2 as successor
from scripts import j1_manager_successor_native_v2 as native

PLAN_SCHEMA = "autostop.j1.crm-manager-mcp-forward-plan.v1"
SCOPE = "crm_and_manager_mcp_preserved_warm"
SCHEMA = "autostop.j1.crm-manager-mcp-native-baseline.v2"
INSTALL_SCHEMA = "autostop.j1.manager-mcp-installation.v2"
PLAN_FIELDS = state.PLAN_FIELDS | {"manager_transition_ref", "candidate_manager_sha"}
INSTALL_FIELDS = {
    "schema",
    "contract",
    "attempt_id",
    "host",
    "manager_revision",
    "binding_ref",
    "source_inputs_ref",
    "source_package_sha256",
    "predecessor_ref",
    "native_acceptance",
    "activation_authorized",
    "candidate_may_have_written",
    "old_writer_allowed",
    "controller_files",
}
CONTROLLER_FILES = tuple(
    "scripts/" + name + ".py"
    for name in (
        "j1_manager_transition",
        "j1_manager_transition_source",
        "j1_manager_successor_guard",
        "j1_manager_successor_native",
        "j1_manager_successor_guard_v2",
        "j1_manager_successor_native_v2",
        "j1_manager_successor_transition",
    )
)


def consumer_inventory(source_root=None):
    root = Path(__file__).resolve().parents[1] if source_root is None else Path(source_root)
    return {name: io.sha(io.read_protected(root / name)) for name in CONTROLLER_FILES}


def validate_plan(plan, base_sha256):
    io.require(type(plan) is dict and set(plan) == PLAN_FIELDS, "mixed_plan_fields")
    io.require(plan["schema"] == PLAN_SCHEMA and plan["scope"] == SCOPE, "mixed_plan_scope")
    projection = {key: plan[key] for key in state.PLAN_FIELDS}
    projection.update(schema=state.PLAN_SCHEMA, scope=state.SCOPE)
    state.validate_plan(projection, base_sha256)
    state._reference(plan["manager_transition_ref"])
    io.require(plan["candidate_manager_sha"] == successor.REVISION, "mixed_manager_revision")


def validate_any_plan(plan, base_sha256):
    if type(plan) is dict and plan.get("schema") == PLAN_SCHEMA:
        return validate_plan(plan, base_sha256)
    return state.validate_plan(plan, base_sha256)


class MixedStore(state.Store):
    """The existing transaction protocol with an explicit plan discriminator."""

    def _validate_plan(self, plan):
        return validate_any_plan(plan, self.base_sha256)

    def _records(self):
        records, transitioned = super()._records(), False
        for record, _ref in records:
            mixed = record["plan"]["schema"] == PLAN_SCHEMA
            io.require(not transitioned or mixed, "mixed_manager_downgrade_forbidden")
            transitioned |= mixed
        return records

    def begin(self, plan):
        validate_plan(plan, self.base_sha256)
        return super().begin(plan)

    def commit(self, plan, proofs):
        validate_plan(plan, self.base_sha256)
        return super().commit(plan, proofs)


def load_transition(reference, *, read_reference=None):
    read_reference = io.referenced if read_reference is None else read_reference
    state._reference(reference)
    receipt = io.document(read_reference(reference))
    io.require(set(receipt) == INSTALL_FIELDS, "mixed_installation_fields")
    io.require(
        receipt["schema"] == INSTALL_SCHEMA
        and receipt["contract"] == successor.CONTRACT
        and receipt["host"] == successor.HOST
        and receipt["manager_revision"] == successor.REVISION
        and receipt["native_acceptance"] is False
        and receipt["activation_authorized"] is False
        and receipt["candidate_may_have_written"] is True
        and receipt["old_writer_allowed"] is False
        and state._hash(receipt["source_package_sha256"]),
        "mixed_installation_contract",
    )
    for field in ("binding_ref", "source_inputs_ref", "predecessor_ref"):
        state._reference(receipt[field])
        read_reference(receipt[field])
    io.require(
        receipt["controller_files"] == consumer_inventory(), "mixed_controller_bytes_changed"
    )
    io.require(receipt["binding_ref"]["path"] == successor.BINDING, "mixed_binding_path")
    binding = io.document(read_reference(receipt["binding_ref"]))
    successor._binding_shape(binding)
    io.require(
        binding["attempt_id"] == receipt["attempt_id"]
        and binding["predecessor_baseline"]
        == {"path": successor.BASELINE, "sha256": receipt["predecessor_ref"]["sha256"]},
        "mixed_installation_binding",
    )
    return receipt, binding


def _mcp_loaded(fields, binding_sha256, authority, *, closed):
    prefix = f"/usr/bin/python3 -I -S -B {successor.GUARD}"
    arguments = f" --binding-sha256 {binding_sha256} --unit {successor.UNIT}"
    conditions = [prefix + " check" + arguments]
    if closed:
        conditions.append("/usr/bin/false")
    native.validate_command_records(fields["ExecCondition"], conditions)
    native.validate_command_records(fields["ExecStart"], [prefix + " launch" + arguments])
    io.require(
        original._commands(fields, "ExecCondition") == conditions
        and original._commands(fields, "ExecStart") == [prefix + " launch" + arguments],
        "mixed_loaded_mcp_guard",
    )
    io.require(
        all(
            not fields.get(key)
            for key in ("ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "ExecReload")
        ),
        "mixed_loaded_mcp_hooks",
    )
    context = authority["capsule"]["profiles"][successor.UNIT]["unit_context"]
    io.require({key: fields.get(key) for key in context} == context, "mixed_mcp_context")
    if closed:
        io.require(
            fields.get("ActiveState") == "inactive"
            and fields.get("SubState") == "dead"
            and fields.get("MainPID") == "0",
            "mixed_mcp_still_running",
        )


def _mcp_unit_files(profile, binding_sha256, authority, *, closed, reader):
    policy_hash = authority["capsule"]["policy"]["sha256"]
    guard = authority["guard"]
    relocated = authority["wrapper"].relocated_binding(profile, authority["capsule"])
    for reference in relocated["unit_files"]:
        io.require(
            io.sha(io.read_protected(reference["path"])) == reference["sha256"],
            "mixed_preserved_unit_file",
        )
    own = Path(profile["guard_dropin"])
    io.require(
        own == original.SYSTEMD / (successor.UNIT + ".d") / "99-j1-candidate-guard.conf"
        and io.read_protected(own) == guard.unit_dropin(successor.UNIT, policy_hash).encode(),
        "mixed_original_mcp_guard",
    )
    reader.raw(
        {"path": successor.DROPIN, "sha256": io.sha(successor.render_dropin(binding_sha256))}
    )
    io.require(
        any(record["path"] == str(own.parent) for record in profile["dropin_directories"]),
        "mixed_mcp_dropin_inventory",
    )
    for record in profile["dropin_directories"]:
        folder = Path(record["path"])
        if record["files"] is None:
            io.require(original._absent(folder), "mixed_unknown_dropin_directory")
            continue
        guard.directory(folder)
        expected = set(record["files"])
        if folder == own.parent:
            expected |= {own.name, Path(successor.DROPIN).name}
            if closed:
                expected.add(original.DENY_NAME)
        io.require(
            {path.name for path in folder.iterdir() if path.suffix == ".conf"} == expected,
            "mixed_unknown_mcp_dropin",
        )
        for name, digest in record["files"].items():
            io.require(
                io.sha(io.read_protected(folder / name)) == digest, "mixed_mcp_dropin_changed"
            )


def observe(transition_ref, *, running=False, owned_closure=None, full_runtime=False):
    """Actual pinned native reads; never execute guard authorize/check/launch."""
    started, deadline = time.monotonic(), time.monotonic() + (180 if full_runtime else 60)
    receipt, binding = load_transition(transition_ref)
    authority = original._load_authority(deadline)
    native_io = native.NativeIO(deadline)
    reader = successor.Reader(native_io, deadline)
    reader.raw(receipt["binding_ref"])
    for field, module in (("guard", successor), ("native", native)):
        io.require(
            binding[field]["sha256"] == io.sha(io.read_protected(Path(module.__file__))),
            "mixed_consumer_code_mismatch",
        )
        reader.raw(binding[field])
    capsule, index, predecessor, policy, _forward, old_index, _refs = successor._source_authority(
        binding, reader
    )
    runtime = successor._profile(binding, capsule, policy, reader)
    native_io.verify_preserved_sources(policy, old_index)
    native_io.verify_source(binding["profile"], index)
    native_io.verify_runtime(binding["profile"], runtime)
    native_io.verify_cache(binding["profile"])
    owned = original._closure_record(owned_closure)
    io.require(not (running and owned), "mixed_running_and_closed_conflict")
    # In this version the two denied roles retain the exact canonical barrier
    # during both closure and activation. Only six working roles may reopen.
    original._unsupported_units(authority, True)
    profiles, units, processes = {}, {}, {}
    for unit in original.ALL_UNITS:
        fields = original._systemd(unit, deadline)
        if unit == successor.UNIT:
            _mcp_loaded(fields, receipt["binding_ref"]["sha256"], authority, closed=bool(owned))
        else:
            original._loaded(
                unit, fields, authority, bool(owned) or unit not in original.NATIVE_UNITS
            )
        io.require(fields["UnitFileState"] in {"enabled", "disabled"}, "mixed_enablement_unknown")
        units[unit] = {
            "enabled": fields["UnitFileState"] == "enabled",
            "active": fields["ActiveState"] == "active",
        }
        if unit not in original.NATIVE_UNITS:
            continue
        if unit == successor.UNIT:
            profile = binding["profile"]
            _mcp_unit_files(
                profile,
                receipt["binding_ref"]["sha256"],
                authority,
                closed=bool(owned),
                reader=reader,
            )
        else:
            profile = policy["bindings"][unit]
            old = authority["guard"]
            old.verify_source(profile, authority["index"])
            old.verify_runtime(profile, original.SCOPE)
            environment = authority["wrapper"].accepted_input(authority["capsule"], policy, unit)
            old.verify_environment(environment, profile)
            relocated = authority["wrapper"].relocated_binding(profile, authority["capsule"])
            if owned:
                original._units_with_closure(
                    old, relocated, unit, authority["capsule"]["policy"]["sha256"]
                )
            else:
                old.verify_units(relocated, unit, authority["capsule"]["policy"]["sha256"])
        profiles[unit] = {**profile, "binding_sha256": io.sha(io.canonical(profile))}
        if running:
            processes[unit] = original._running(unit, profile, fields, authority["guard"], deadline)
    warm = original._warm_actors(authority, deadline)
    io.require(warm == predecessor["warm_actors"], "mixed_original_warm_cohort_changed")
    cache = original._cache(authority)
    io.require(cache == predecessor["cache"], "mixed_cache_changed")
    reader.recheck()
    io.referenced(transition_ref)
    original._expired(deadline)
    immutable = {
        "schema": SCHEMA,
        "scope": SCOPE,
        "manager_revision": successor.REVISION,
        "daemon_mode": "accepted_original_warm",
        "manager_transition_ref": transition_ref,
        "predecessor_sha256": predecessor["sha256"],
        "base_sha256": predecessor["base_sha256"],
        "binding_sha256": receipt["binding_ref"]["sha256"],
        "profiles": profiles,
        "warm_actors": warm,
        "cache": cache,
        "runtime_inventories": original._runtime_inventory(authority, full_runtime, deadline),
        "deployment_lock": predecessor["deployment_lock"],
        "deployment_lock_identity": original._identity(Path(predecessor["deployment_lock"])),
        "maintenance_marker": predecessor["maintenance_marker"],
        "desired_inbound": predecessor["desired_inbound"],
        "manager_root": predecessor["manager_root"],
        "work_root": predecessor["work_root"],
        "legacy_crm_revision": predecessor["legacy_crm_revision"],
    }
    io.require(
        immutable["deployment_lock_identity"] == predecessor["deployment_lock_identity"],
        "mixed_deployment_lock_changed",
    )
    return {
        "immutable": immutable,
        "units": units,
        "processes": processes,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def snapshot_native(transition_ref):
    observed = observe(transition_ref, running=True, full_runtime=True)
    value = {
        **observed["immutable"],
        "desired_units": observed["units"],
        "libraries": {unit: row["libraries"] for unit, row in observed["processes"].items()},
        "cache_mounts": {unit: row["cache_mount"] for unit, row in observed["processes"].items()},
    }
    return {**value, "sha256": io.sha(io.canonical(value))}


def prestart_baseline(transition_ref, *, owned_closure):
    """Derive static targets under closure; post-start acceptance stays required."""
    receipt, _binding = load_transition(transition_ref)
    predecessor = io.document(io.referenced(receipt["predecessor_ref"]))
    observed = observe(
        transition_ref, running=False, owned_closure=owned_closure, full_runtime=True
    )
    value = {
        **observed["immutable"],
        **{key: predecessor[key] for key in ("desired_units", "libraries", "cache_mounts")},
    }
    return {**value, "sha256": io.sha(io.canonical(value))}


def verify_preserved(baseline, *, running=False, owned_closure=None):
    io.require(type(baseline) is dict and baseline.get("schema") == SCHEMA, "mixed_baseline_schema")
    unsigned = {key: value for key, value in baseline.items() if key != "sha256"}
    io.require(io.sha(io.canonical(unsigned)) == baseline.get("sha256"), "mixed_baseline_digest")
    observed = observe(
        baseline["manager_transition_ref"], running=running, owned_closure=owned_closure
    )
    expected = {
        key: value
        for key, value in unsigned.items()
        if key not in {"desired_units", "libraries", "cache_mounts"}
    }
    io.require(observed["immutable"] == expected, "mixed_immutable_inputs_changed")
    io.require(
        {unit: row["enabled"] for unit, row in observed["units"].items()}
        == {unit: row["enabled"] for unit, row in baseline["desired_units"].items()},
        "mixed_enablement_changed",
    )
    if running:
        io.require(observed["units"] == baseline["desired_units"], "mixed_desired_state_changed")
        for name, field in (("libraries", "libraries"), ("cache_mounts", "cache_mount")):
            io.require(
                {unit: row[field] for unit, row in observed["processes"].items()} == baseline[name],
                "mixed_runtime_changed",
            )
    return {
        "ok": True,
        "baseline_sha256": baseline["sha256"],
        "running": running,
        "closed": owned_closure is not None,
        "processes": observed["processes"],
        "elapsed_ms": observed["elapsed_ms"],
    }
