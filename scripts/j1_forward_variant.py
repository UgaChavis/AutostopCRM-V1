"""Explicit protocol selection shared by controller, native stop and worker."""

from __future__ import annotations

import os
import time
from pathlib import Path

from scripts import j1_forward_authority as original
from scripts import j1_forward_backup as backup
from scripts import j1_forward_io as io
from scripts import j1_forward_state as state

MIXED_PLAN = "autostop.j1.crm-manager-mcp-forward-plan.v1"
MIXED_BASELINE = "autostop.j1.crm-manager-mcp-forward-baseline.v1"
MIXED_INSPECTION = "autostop.j1.crm-manager-mcp-forward-inspection.v1"
SUPPLEMENTAL = frozenset(
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


def is_mixed(plan):
    return type(plan) is dict and plan.get("schema") == MIXED_PLAN


def validate_plan(plan, base):
    if is_mixed(plan):
        from scripts.j1_forward_mixed_authority import validate_plan as validate

        return validate(plan, base)
    return state.validate_plan(plan, base)


def baseline_schema(plan):
    return MIXED_BASELINE if is_mixed(plan) else "autostop.j1.crm-forward-baseline.v1"


def inspection_schema(plan):
    return MIXED_INSPECTION if is_mixed(plan) else "autostop.j1.crm-forward-inspection.v1"


def guard_acceptance_field(plan):
    return "mixed_guards_executed" if is_mixed(plan) else "original_guards_executed"


def store(root, plan):
    if is_mixed(plan):
        from scripts.j1_forward_mixed_authority import MixedStore

        return MixedStore(root, plan["base_sha256"])
    return state.Store(root, plan["base_sha256"])


def backup_engine(plan):
    if is_mixed(plan):
        from scripts import j1_forward_mixed_backup

        return j1_forward_mixed_backup.engine(
            "/opt/autostop-manager-mcp-releases/" + plan["candidate_manager_sha"] + "/source"
        )
    return backup


def transport_engine(plan):
    if is_mixed(plan):
        from scripts import j1_forward_mixed_backup

        return j1_forward_mixed_backup.transport_engine(
            "/opt/autostop-manager-mcp-releases/" + plan["candidate_manager_sha"] + "/source"
        )
    from scripts import j1_forward_transport

    return j1_forward_transport


def transition(plan):
    from scripts import j1_forward_mixed_authority

    return j1_forward_mixed_authority.load_transition(plan["manager_transition_ref"])


def installation_phase(plan):
    """Classify only exact staged or installed bytes; never infer a permit."""
    from scripts import j1_manager_successor_guard_v2 as guard

    receipt, _binding = transition(plan)
    dropin, selector = Path(guard.DROPIN), Path(guard.SELECTOR)
    if os.path.lexists(selector):
        io.require(
            selector.is_symlink() and os.readlink(selector) == guard.SOURCE_ROOT,
            "mixed_selector_foreign",
        )
    if not os.path.lexists(dropin):
        io.require(
            not any(
                os.path.lexists(path)
                for path in (guard.STATE, guard.ADMISSION, Path(guard.STATE_DIR) / "proofs")
            ),
            "mixed_partial_authority_requires_review",
        )
        return "staged"
    io.require(
        io.read_protected(dropin) == guard.render_dropin(receipt["binding_ref"]["sha256"])
        and selector.is_symlink(),
        "mixed_installation_changed",
    )
    return "installed"


def original_native(plan):
    receipt, _binding = transition(plan)
    return io.document(io.referenced(receipt["predecessor_ref"]))


def verify_native(plan, baseline, *, running=False, owned_closure=None, preclose=False):
    if not is_mixed(plan):
        options = {"preclose": True} if preclose else {}
        return original.verify_preserved(
            baseline["native"], running=running, owned_closure=owned_closure, **options
        )
    io.require(not preclose, "mixed_historical_recovery_not_admitted")
    if installation_phase(plan) == "staged":
        before = original.verify_preserved(
            original_native(plan), running=running, owned_closure=owned_closure
        )
        from scripts import j1_manager_successor_guard_v2 as guard
        from scripts import j1_manager_successor_native_v2 as native

        _receipt, binding = transition(plan)
        deadline = time.monotonic() + 60
        adapter = native.NativeIO(deadline)
        reader = guard.Reader(adapter, deadline)
        capsule, index, _old, policy, *_refs = guard._source_authority(binding, reader)
        runtime = guard._profile(binding, capsule, policy, reader)
        adapter.verify_staged_source(binding["profile"], index)
        adapter.verify_runtime(binding["profile"], runtime)
        reader.recheck()
        return {
            **before,
            "schema": "autostop.j1.crm-manager-mcp-preinstall-observation.v1",
            "baseline_sha256": baseline["native"]["sha256"],
            "predecessor_baseline_sha256": before["baseline_sha256"],
            "manager_transition_ref": plan["manager_transition_ref"],
            "phase": "staged",
            "candidate_installed": False,
            "native_acceptance": False,
        }
    from scripts import j1_forward_mixed_authority

    return j1_forward_mixed_authority.verify_preserved(
        baseline["native"], running=running, owned_closure=owned_closure
    )


def stop_native(plan, baseline):
    if is_mixed(plan) and installation_phase(plan) == "staged":
        return original_native(plan)
    return baseline["native"]


def permanent_denied(plan):
    return (
        frozenset(original.ALL_UNITS) - frozenset(original.NATIVE_UNITS)
        if is_mixed(plan)
        else frozenset()
    )


def consumer_name(name, *, mixed=False):
    import re

    return bool(
        type(name) is str
        and (
            re.fullmatch(r"scripts/j1_forward_[a-z_]+\.py", name) or mixed and name in SUPPLEMENTAL
        )
    )
