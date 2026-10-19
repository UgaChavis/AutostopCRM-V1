"""Finite Windows custodian: inspect, independently restore, then attest.

There is no sign-payload operation. The signing key is used only after the fixed
native inspection and, for closed admission, an actual disposable local restore.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import math
import os
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).absolute().parent.parent))

from scripts import j1_forward_archive as control_archive
from scripts import j1_forward_io as io
from scripts import j1_forward_proofs as proofs
from scripts import j1_forward_variant as variant

HOST = "root@crm.autostopcrm.ru"
NATIVE_HOST = "vps26457.mnogoweb.in"
REMOTE = "/opt/autostopcrm/scripts/j1_forward_release.py"
ATTEMPTS = "/var/lib/autostop-crm-forward/attempts"
MANAGER_REVISION = "89af29082e0a6f72c5d85ae448acb6d4deec5d94"
INSPECTION_SCHEMA = "autostop.j1.crm-forward-inspection.v1"
MAX_JSON = 2 * 1024**2
MAX_DELTA = 16 * 1024**3 + 256 * 1024**2
MIN_SQLITE = (3, 51, 3)
MONITOR_LIMITS = {
    "sample_interval_seconds": 0.2,
    "maximum_sample_age_seconds": 1.0,
    "full_refresh_seconds": 0.5,
    "maximum_full_start_gap_seconds": 1.0,
    "maximum_full_scan_seconds": 30.0,
}
CAPTURE_EVIDENCE = {
    "closure_ownership",
    "backup_capture",
    "capture_monitor",
    "capture_initial_full",
    "capture_terminal_full",
    "capture_first_sample",
    "capture_last_sample",
}
CI_JOBS = sorted(["docker-runtime-assets", "static", "unit", "browser-performance", "quality"])
NATIVE_UNITS = frozenset(
    {
        "autostop-j1.service",
        "autostop-manager-mcp.service",
        "autostop-manager-scheduler.service",
        "autostop-work-telegram.service",
        "autostop-telegram.service",
        "autostop-codex-wake.service",
    }
)
WRITE_RIGHTS = 2 | 4 | 16 | 64 | 256 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000
SYSTEM_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544"})
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
ACL_SCRIPT = r"""
$ErrorActionPreference='Stop'
$p=$env:AUTOSTOP_J1_WORKER_ACL_PATH
$a=Get-Acl -LiteralPath $p
$rules=@($a.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | ForEach-Object {
  @{sid=$_.IdentityReference.Value;allow=($_.AccessControlType -eq 'Allow');rights=[int64]$_.FileSystemRights;
    inherit_only=(($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0)}
})
@{current=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
owner=$a.GetOwner([Security.Principal.SecurityIdentifier]).Value;rules=$rules} | ConvertTo-Json -Depth 4 -Compress
"""


def require(condition, code):
    io.require(condition, code)


def _utc(value):
    require(type(value) is str and len(value) <= 40, "worker_time_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise io.Refusal("worker_time_invalid") from None
    require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), "worker_time_invalid")
    return parsed


def _uuid(value):
    try:
        require(type(value) is str and str(uuid.UUID(value)) == value, "worker_attempt_invalid")
    except (ValueError, AttributeError):
        raise io.Refusal("worker_attempt_invalid") from None
    return value


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _reference(value):
    require(type(value) is dict and set(value) == {"path", "sha256"}, "worker_reference_invalid")
    path = value["path"]
    require(
        type(path) is str
        and 0 < len(path) <= 4096
        and "\\" not in path
        and "\0" not in path
        and PurePosixPath(path).is_absolute()
        and ".." not in PurePosixPath(path).parts
        and PurePosixPath(path).as_posix() == path
        and _hash(value["sha256"]),
        "worker_reference_invalid",
    )
    return value


def _bound(document, reference):
    _reference(reference)
    require(
        type(document) is dict and io.sha(io.canonical(document)) == reference["sha256"],
        "worker_document_hash_mismatch",
    )


def _physical(path, *, directory=False, single_link=True):
    path = Path(path)
    require(
        path.is_absolute() and ".." not in path.parts and not str(path).startswith("\\\\"),
        "worker_absolute_local_path_required",
    )
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        require(
            not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
            "worker_filesystem_link_refused",
        )
        require(
            stat.S_ISDIR(info.st_mode)
            if part != path or directory
            else stat.S_ISREG(info.st_mode) and (not single_link or info.st_nlink == 1),
            "worker_physical_path_required",
        )
    return path


def _acl(path):
    require(os.name == "nt", "worker_windows_custody_required")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    environment = {
        **os.environ,
        "AUTOSTOP_J1_WORKER_ACL_PATH": str(path),
        "PSModulePath": str(powershell.parent / "Modules"),
    }
    try:
        result = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", ACL_SCRIPT],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise io.Refusal("worker_acl_inspection_failed") from None
    require(
        result.returncode == 0 and len(result.stdout) <= 128 * 1024, "worker_acl_inspection_failed"
    )
    return io.document(result.stdout)


def _check_acl(value, *, private):
    require(
        type(value) is dict
        and set(value) == {"current", "owner", "rules"}
        and type(value["current"]) is str
        and value["current"].startswith("S-1-")
        and type(value["rules"]) is list
        and value["rules"],
        "worker_acl_invalid",
    )
    custodians = {*SYSTEM_SIDS, value["current"]}
    if not private:
        custodians.add(TRUSTED_INSTALLER)
    require(value["owner"] in custodians, "worker_acl_owner_invalid")
    allowed = False
    for rule in value["rules"]:
        require(
            type(rule) is dict
            and set(rule) == {"sid", "allow", "rights", "inherit_only"}
            and type(rule["sid"]) is str
            and type(rule["allow"]) is bool
            and type(rule["rights"]) is int,
            "worker_acl_rule_invalid",
        )
        require(type(rule["inherit_only"]) is bool, "worker_acl_rule_invalid")
        if not private and rule["inherit_only"]:
            continue
        if rule["allow"]:
            # OWNER RIGHTS denotes this object's already-validated owner,
            # including Python's Windows mode=0700 directory ACL.
            principal = value["owner"] if rule["sid"] == "S-1-3-4" else rule["sid"]
            if principal in custodians:
                allowed = True
            else:
                require(
                    not private and not rule["rights"] & WRITE_RIGHTS, "worker_acl_other_principal"
                )
    require(allowed, "worker_acl_access_missing")


def custody(path, *, directory=False):
    path = _physical(path, directory=directory)
    _check_acl(_acl(path), private=True)
    return path


def program(path):
    # Windows servicing hard-links system executables into WinSxS. Their ACL
    # protects the inode through every name; custody data still requires one link.
    path = _physical(path, single_link=False)
    require(path.suffix.lower() == ".exe", "worker_executable_required")
    for item in (path, path.parent):
        _check_acl(_acl(item), private=False)
    return path


def _new_file(path, raw):
    require(not os.path.lexists(path), "worker_output_exists")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return {"path": str(path), "sha256": io.sha(raw)}


def _remaining(plan, maximum=3600):
    remaining = (_utc(plan["deadline_utc"]) - datetime.now(UTC)).total_seconds()
    require(remaining > 0, "worker_plan_expired")
    return min(maximum, remaining)


def _source_consumers(baseline, plan):
    expected = baseline.get("source_consumers")
    require(type(expected) is dict and expected, "worker_consumers_missing")
    root = Path(__file__).absolute().parent.parent
    paths = sorted((root / "scripts").glob("j1_forward_*.py"))
    if variant.is_mixed(plan):
        paths += [root / name for name in sorted(variant.SUPPLEMENTAL)]
    actual = {}
    for path in paths:
        _physical(path)
        require(path.stat().st_size <= MAX_JSON, "worker_consumer_size")
        actual["scripts/" + path.name] = io.sha(path.read_bytes())
    require(
        actual == expected
        and io.sha(io.canonical(expected)) == plan["candidate"]["consumer_sha256"],
        "worker_consumer_hash_mismatch",
    )


def _process_scope_evidence(scope, reference, observed, plan, native, *, starts=None):
    """Bind closure or staged activation to the exact prepared process births."""
    from scripts import j1_forward_process_scope as process_scope

    recovery = type(scope) is dict and scope.get("schema") != process_scope.SCHEMA
    policy = process_scope
    if recovery:
        from scripts import j1_forward_recovery_scope as policy

    fields = set(process_scope.ACTOR_FIELDS)
    require(
        type(scope) is dict
        and set(scope) == policy.SCOPE_FIELDS
        and scope["schema"] == policy.SCHEMA
        and scope["profile_sha256"] == policy.PROFILE_SHA256
        and scope["attempt_id"] == plan["attempt_id"]
        and scope["attempt_dir"] == ATTEMPTS + "/" + plan["attempt_id"]
        and reference["path"] == scope["attempt_dir"] + "/process-scope.json"
        and scope["base_sha256"] == plan["base_sha256"]
        and scope["native_sha256"] == io.sha(io.canonical(native))
        and scope["consumer_sha256"] == plan["candidate"]["consumer_sha256"]
        and type(scope["observation"]) is dict,
        "worker_process_scope_binding",
    )
    _uuid(scope["boot_id"])
    policy.validate_scope_shape(scope)
    require(
        type(native.get("warm_actors")) is list
        and len(native["warm_actors"]) == 3
        and all(type(row) is dict and fields <= set(row) for row in native["warm_actors"])
        and scope["original_actors"]
        == sorted(
            [{key: row[key] for key in fields} for row in native["warm_actors"]],
            key=lambda row: row["pid"],
        ),
        "worker_original_actors_changed",
    )
    if recovery:
        require(
            native.get("schema") == "autostop.j1.crm-forward-native-recovery.v2"
            and native.get("warm_identity_semantics")
            == "historical_origin_plus_observed_continuation"
            and native.get("historical_warm_actors") == native["warm_actors"],
            "worker_recovery_historical_native",
        )
        observed_native = native.get("observed_warm_actors")
        require(
            type(observed_native) is list
            and len(observed_native) == 2
            and all(type(row) is dict and fields <= set(row) for row in observed_native)
            and sorted(
                [{key: row[key] for key in fields} for row in observed_native],
                key=lambda row: row["pid"],
            )
            == scope["original_liveness"],
            "worker_recovery_native_liveness",
        )

    def actors(rows, *, measured=False, stopped=False):
        require(type(rows) is list and 0 < len(rows) <= 128, "worker_actor_cohort_required")
        result = {}
        for row in rows:
            require(
                type(row) is dict
                and set(row) == fields | ({"stopped", "thread_states"} if measured else set())
                and type(row.get("pid")) is int
                and row["pid"] > 1
                and type(row.get("start_ticks")) is int
                and row["start_ticks"] > 0
                and type(row.get("uid")) is int
                and row["uid"] >= 0
                and _hash(row.get("exe_sha256"))
                and _hash(row.get("argv_sha256"))
                and type(row.get("exe")) is str
                and row["exe"].startswith("/")
                and type(row.get("exe_identity")) is list
                and len(row["exe_identity"]) == 2
                and all(type(value) is int and value >= 0 for value in row["exe_identity"])
                and type(row.get("cgroup")) is str
                and row["cgroup"].startswith("0::/")
                and type(row.get("mount_namespace")) is str
                and re.fullmatch(r"mnt:\[[0-9]+\]", row["mount_namespace"])
                and row["pid"] not in result,
                "worker_actor_identity_invalid",
            )
            if measured:
                states = row["thread_states"]
                expected_stop = stopped(row["pid"]) if callable(stopped) else stopped
                require(
                    row["stopped"] is expected_stop
                    and type(states) is list
                    and 0 < len(states) <= 8192
                    and all(
                        type(value) is str
                        and (value == "T" if expected_stop else value in {"R", "S", "D", "I"})
                        for value in states
                    ),
                    "worker_actor_threads_not_proven",
                )
            result[row["pid"]] = {key: row[key] for key in fields}
        return result

    frozen = actors(scope["freeze_actors"])
    transports = actors(scope["transport_actors"])
    originals = actors(scope["original_actors"])
    require(
        set(originals) == {538285, 538314, 538487}
        and [row["pid"] for row in scope["freeze_actors"]] == list(policy.FREEZE)
        and [row["pid"] for row in scope["transport_actors"]] == list(policy.TRANSPORTS)
        and all(
            row["start_ticks"] == process_scope.STARTS[pid]
            for pid, row in (frozen | transports).items()
        )
        and not set(frozen) & set(transports)
        and {538285, 538487} <= set(frozen)
        and ((538314 not in transports) if recovery else (538314 in transports))
        and scope["freeze_actors"][0]["pid"] == 538285
        and all(row["uid"] == 0 for row in frozen.values()),
        "worker_actor_partition_invalid",
    )
    roles = scope["observation"].get("roles")
    require(
        type(roles) is dict
        and set(roles) == {str(pid) for pid in frozen | transports}
        and roles.get("538285") == "daemon"
        and (("538314" not in roles) if recovery else roles.get("538314") == "ssh_original")
        and roles.get("538487") == "proxy"
        and all(
            roles[str(pid)] in {"daemon", "helper_mcp", "helper_code", "proxy"} for pid in frozen
        )
        and all(
            roles[str(pid)]
            in {"ssh_original", "ssh_proxy_parent", "ssh_tunnel_monitor", "ssh_tunnel"}
            for pid in transports
        ),
        "worker_actor_roles_invalid",
    )
    activated = starts is not None
    require(
        observed.get("schema")
        == (
            "autostop.j1.crm-forward-activation-observation.v2"
            if activated
            else "autostop.j1.crm-forward-closure-observation.v2"
        )
        and observed.get("process_scope_ref") == reference
        and observed.get("scope_sha256") == reference["sha256"],
        "worker_closure_scope_changed",
    )
    sample = observed.get("process_scope_observation")
    require(
        type(sample) is dict
        and set(sample)
        == {
            "schema",
            "scope_sha256",
            "freeze_actors",
            "transport_actors",
            "native_control_peer",
            "topology_sha256",
        }
        | ({"auxiliary_released"} if activated else {"frozen"})
        | (
            {"retired_transports_absent", "disposition", "original_liveness"}
            | ({"retained_frozen"} if activated else {"phase"})
            if recovery
            else set()
        )
        and sample["schema"]
        == (
            "autostop.j1.crm-forward-process-scope-activation." + ("v2" if recovery else "v1")
            if activated
            else policy.OBSERVATION_SCHEMA
        )
        and sample["scope_sha256"] == reference["sha256"]
        and (sample["auxiliary_released"] is False if activated else sample["frozen"] is True)
        and sample["topology_sha256"] == io.sha(io.canonical(scope["observation"]))
        and actors(
            sample["freeze_actors"],
            measured=True,
            stopped=(lambda pid: pid != 538285) if activated else True,
        )
        == frozen
        and actors(sample["transport_actors"], measured=True, stopped=False) == transports,
        "worker_cohort_not_proven",
    )
    if recovery:
        require(
            sample["retired_transports_absent"] == list(policy.RETIRED)
            and sample["disposition"] == policy.DISPOSITION
            and sample["original_liveness"]
            == [row for row in sample["freeze_actors"] if row["pid"] in policy.ORIGINAL]
            and (
                sample["retained_frozen"] == list(policy.ORPHANS)
                if activated
                else sample["phase"] == "closed"
            ),
            "worker_recovery_observed_disposition",
        )
    peer = sample["native_control_peer"]
    if activated:
        profile = process_scope.native_profile(native)
        require(
            type(peer) is dict
            and type(scope["native_control_peer"]) is dict
            and peer.get("profile") == profile == scope["native_control_peer"].get("profile")
            and type(peer.get("actor")) is dict,
            "worker_native_peer_profile",
        )
        actor = peer["actor"]
        actors([actor])
        require(
            actor["pid"] == starts["autostop-codex-wake.service"]["main_pid"]
            and actor["exe"] == profile["exe"]
            and actor["exe_sha256"] == profile["exe_sha256"]
            and actor["argv_sha256"] == profile["argv_sha256"]
            and actor["uid"] == profile["credentials"][0]
            and actor["cgroup"] == "0::/system.slice/autostop-codex-wake.service",
            "worker_native_peer_guard_binding",
        )
    else:
        require(peer is None, "worker_closed_native_peer_present")
    for key, members, stopped in (
        ("freeze_actors", frozen, True),
        ("transport_actors", transports, False),
    ):
        expected = [
            {
                "pid": row["pid"],
                "start_ticks": row["start_ticks"],
                "stopped": stopped and (not activated or row["pid"] != 538285),
            }
            for row in scope[key]
        ]
        require(
            observed.get(key) == expected and len(expected) == len(members),
            "worker_closure_actor_projection",
        )


def _ingress_evidence(evidence, inspection, plan):
    """A failed emergency fence cannot stand in for a normal drained admission."""
    from scripts import j1_forward_host as host_policy

    drain, idle, scope = (
        evidence[key] for key in ("ingress_drain", "ingress_idle", "process_scope")
    )
    reference = inspection["process_scope_ref"]
    prefix = ATTEMPTS + "/" + plan["attempt_id"] + "/"
    require(
        set(drain)
        == {
            "schema",
            "attempt_id",
            "process_scope_ref",
            "idle_observation_ref",
            "staging_observation",
            "observed_at",
        }
        and drain["schema"] == "autostop.j1.crm-forward-ingress-drain.v1"
        and drain["attempt_id"] == plan["attempt_id"]
        and drain["process_scope_ref"] == reference
        and inspection["ingress_drain_ref"]["path"] == prefix + "ingress-drain.json"
        and drain["idle_observation_ref"] == inspection["ingress_idle_ref"]
        and re.fullmatch(
            re.escape(prefix) + r"ingress-idle-[0-9a-f]{32}\.json",
            inspection["ingress_idle_ref"]["path"],
        ),
        "worker_ingress_drain_binding",
    )
    require(
        set(idle)
        == {
            "observed_at",
            "loaded_count",
            "active_turns",
            "all_loaded_and_wake_idle",
            "metadata_only",
            "rpc_sha256",
            "wake_sha256",
        }
        and type(idle["loaded_count"]) is int
        and idle["loaded_count"] >= 0
        and type(idle["active_turns"]) is int
        and idle["active_turns"] == 0
        and idle["all_loaded_and_wake_idle"] is True
        and idle["metadata_only"] is True
        and idle["rpc_sha256"] == host_policy.RPC_SHA
        and idle["wake_sha256"] == host_policy.WAKE_SHA
        and _utc(plan["created_at"])
        <= _utc(idle["observed_at"])
        <= _utc(drain["observed_at"])
        <= _utc(plan["deadline_utc"]),
        "worker_ingress_idle_not_proven",
    )
    staged = drain["staging_observation"]
    require(type(staged) is dict, "worker_ingress_staging_invalid")
    closed = evidence.get("closure")
    require(
        (closed or evidence["activation_cohort"]).get("ingress_drain_ref")
        == inspection["ingress_drain_ref"],
        "worker_ingress_closure_binding",
    )
    expected = {
        "schema": "autostop.j1.crm-forward-process-scope-staging."
        + ("v2" if "origin_scope_ref" in scope else "v1"),
        "scope_sha256": reference["sha256"],
        "auxiliary_released": False,
        "native_control_peer": None,
        "topology_sha256": io.sha(io.canonical(scope["observation"])),
    }
    if "origin_scope_ref" in scope:
        from scripts import j1_forward_recovery_scope as policy

        expected.update(
            retired_transports_absent=list(policy.RETIRED),
            disposition=policy.DISPOSITION,
            original_liveness=[
                row for row in staged.get("freeze_actors", []) if row["pid"] in policy.ORIGINAL
            ],
            retained_frozen=list(policy.ORPHANS),
        )
    require(
        set(staged) == set(expected) | {"freeze_actors", "transport_actors"}
        and all(staged[key] == value for key, value in expected.items()),
        "worker_ingress_staging_invalid",
    )
    for key in ("freeze_actors", "transport_actors"):
        rows = staged[key]
        require(type(rows) is list and len(rows) == len(scope[key]), "worker_ingress_actor_count")
        for actual, original in zip(rows, scope[key], strict=True):
            stopped = key == "freeze_actors" and original["pid"] != 538285
            require(
                type(actual) is dict
                and set(actual) == set(original) | {"stopped", "thread_states"}
                and all(actual.get(name) == value for name, value in original.items())
                and actual["stopped"] is stopped
                and type(actual["thread_states"]) is list
                and 0 < len(actual["thread_states"]) <= 8192
                and all(
                    value == "T" if stopped else value in {"R", "S", "D", "I"}
                    for value in actual["thread_states"]
                ),
                "worker_ingress_actor_state",
            )


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _capture_monitor_evidence(response):
    """Validate source-bound monitoring receipts using only the remote clock.

    Intermediate sample/fence maxima are reviewed-monitor attestations. Only
    supplied endpoint documents and the full-record timing ledger are checked
    here; this does not reobserve historical processes from the workstation.
    """
    from scripts import j1_forward_backup as backup_policy
    from scripts import j1_forward_closure as closure_policy

    plan, inspection, evidence = (response[name] for name in ("plan", "inspection", "evidence"))
    prefix = ATTEMPTS + "/" + plan["attempt_id"] + "/"
    scope = evidence["process_scope"]
    owned, monitor, captured = (
        evidence[name] for name in ("closure_ownership", "capture_monitor", "backup_capture")
    )
    for name, filename in (
        ("closure_ownership", "closure-owned.json"),
        ("capture_monitor", "capture-monitor.json"),
        ("backup_capture", "backup-evidence.json"),
    ):
        require(inspection[name + "_ref"]["path"] == prefix + filename, "worker_capture_path")
    require(
        set(owned)
        == {
            "schema",
            "attempt_id",
            "attempt_dir",
            "boot_id",
            "dropins",
            "bytes_sha256",
            "warm_actors",
            "baseline_sha256",
            "source_roots",
            "consumer_sha256",
            "freeze_actors",
            "process_scope_ref",
        }
        | (
            {"manager_transition_ref", "permanent_denied_units"}
            if variant.is_mixed(plan)
            else set()
        )
        and owned["schema"]
        == (closure_policy.MIXED_SCHEMA if variant.is_mixed(plan) else closure_policy.SCHEMA)
        and owned["attempt_id"] == plan["attempt_id"]
        and owned["attempt_dir"] == prefix.rstrip("/")
        and owned["boot_id"] == scope["boot_id"]
        and owned["baseline_sha256"] == scope["native_sha256"]
        and owned["consumer_sha256"] == plan["candidate"]["consumer_sha256"]
        and owned["process_scope_ref"] == inspection["process_scope_ref"]
        and io.canonical(owned["freeze_actors"]) == io.canonical(scope["freeze_actors"])
        and io.canonical(owned["warm_actors"])
        == io.canonical(response["baseline"]["native"]["warm_actors"])
        and owned["bytes_sha256"] == io.sha(closure_policy.DENY_BYTES)
        and owned["dropins"]
        == [
            {
                "unit": unit,
                "path": "/etc/systemd/system/" + unit + ".d/" + closure_policy.DROPIN,
                "sha256": io.sha(closure_policy.DENY_BYTES),
            }
            for unit in closure_policy.UNITS
        ],
        "worker_capture_ownership",
    )
    if variant.is_mixed(plan):
        require(
            owned["manager_transition_ref"] == plan["manager_transition_ref"]
            and owned["permanent_denied_units"] == sorted(set(closure_policy.UNITS) - NATIVE_UNITS),
            "worker_mixed_closure_binding",
        )
    roots = owned["source_roots"]
    expected_roots = closure_policy.writer_roots(owned)
    require(
        type(roots) is list and len(roots) == len(expected_roots),
        "worker_capture_roots",
    )
    for row, path in zip(roots, expected_roots, strict=True):
        require(
            type(row) is dict
            and type(row.get("present")) is bool
            and row.get("path") == path
            and set(row) == {"path", "present"} | ({"dev", "ino"} if row["present"] else set())
            and (
                not row["present"]
                or all(type(row[k]) is int and row[k] >= 0 for k in ("dev", "ino"))
            ),
            "worker_capture_roots",
        )
    require(
        set(monitor)
        == {
            "schema",
            "bindings",
            "generation",
            "process_id",
            "limits",
            "started_monotonic",
            "capture_started_monotonic",
            "capture_finished_monotonic",
            "capture_finished_after_full_sequence",
            "capture_last_fence_full_sequence",
            "observed_monotonic",
            "initial_full",
            "terminal_full",
            "full_observations",
            "first_sample",
            "last_sample",
            "sample_count",
            "fence_count",
            "maximum_sample_start_gap_seconds",
            "maximum_full_start_gap_seconds",
            "maximum_fence_sample_age_seconds",
            "maximum_fence_full_age_seconds",
        }
        and monitor["schema"] == "autostop.j1.crm-forward-capture-monitor.v1"
        and type(monitor["process_id"]) is int
        and monitor["process_id"] > 1
        and monitor["process_id"]
        not in {row["pid"] for row in (*scope["freeze_actors"], *scope["transport_actors"])}
        and monitor["bindings"]
        == {
            "attempt_id": plan["attempt_id"],
            "boot_id": scope["boot_id"],
            "closure_ref": inspection["closure_ownership_ref"],
            "process_scope_ref": inspection["process_scope_ref"],
            "ingress_drain_ref": inspection["ingress_drain_ref"],
            "plan_ref": response["plan_ref"],
            "consumer_sha256": plan["candidate"]["consumer_sha256"],
        }
        and type(monitor["limits"]) is dict
        and set(monitor["limits"]) == set(MONITOR_LIMITS)
        and all(
            _number(value) and value == MONITOR_LIMITS[key]
            for key, value in monitor["limits"].items()
        ),
        "worker_capture_monitor_binding",
    )
    _uuid(monitor["generation"])
    times = (
        "started_monotonic",
        "capture_started_monotonic",
        "capture_finished_monotonic",
        "observed_monotonic",
    )
    metrics = (
        "maximum_sample_start_gap_seconds",
        "maximum_full_start_gap_seconds",
        "maximum_fence_sample_age_seconds",
        "maximum_fence_full_age_seconds",
    )
    require(
        all(_number(monitor[key]) for key in (*times, *metrics)), "worker_capture_monitor_numbers"
    )
    started, capture_start, capture_end, observed = (monitor[key] for key in times)
    window = (_utc(plan["deadline_utc"]) - _utc(plan["created_at"])).total_seconds()
    require(
        started <= capture_start <= capture_end <= observed
        and observed - started <= min(600, window)
        and type(monitor["sample_count"]) is int
        and 1 <= monitor["sample_count"] <= 3002
        and type(monitor["fence_count"]) is int
        and 1 <= monitor["fence_count"] <= 1_000_000,
        "worker_capture_monitor_interval",
    )

    def record(row, *, full):
        require(
            type(row) is dict
            and set(row)
            == {"started_monotonic", "completed_monotonic", "reference"}
            | ({"sequence", "full_scan_count"} if full else set())
            and _number(row["started_monotonic"])
            and _number(row["completed_monotonic"])
            and started <= row["started_monotonic"] <= row["completed_monotonic"] <= observed
            and row["completed_monotonic"] - row["started_monotonic"] <= (30 if full else 1),
            "worker_capture_record",
        )
        _reference(row["reference"])
        require(
            re.fullmatch(
                re.escape(prefix)
                + (r"closed-observation-" if full else r"closed-sample-")
                + r"[0-9a-f]{32}\.json",
                row["reference"]["path"],
            ),
            "worker_capture_record_path",
        )
        if full:
            require(
                type(row["sequence"]) is int
                and row["sequence"] > 0
                and type(row["full_scan_count"]) is int
                and row["full_scan_count"] > 0,
                "worker_capture_record_count",
            )

    records = monitor["full_observations"]
    require(type(records) is list and 2 <= len(records) <= 1202, "worker_capture_full_count")
    gaps, paths, hashes = [], set(), set()
    for index, row in enumerate(records):
        record(row, full=True)
        ref = row["reference"]
        require(
            row["sequence"] == index + 1
            and ref["path"] not in paths
            and ref["sha256"] not in hashes,
            "worker_capture_full_replay",
        )
        paths.add(ref["path"])
        hashes.add(ref["sha256"])
        if index:
            previous = records[index - 1]
            gap = row["started_monotonic"] - previous["completed_monotonic"]
            require(
                0 <= gap <= 1 and row["full_scan_count"] == previous["full_scan_count"] + 1,
                "worker_capture_full_gap",
            )
            gaps.append(gap)
    initial, terminal = records[0], records[-1]
    boundary = monitor["capture_finished_after_full_sequence"]
    issued = monitor["capture_last_fence_full_sequence"]
    require(
        type(boundary) is int
        and 1 <= boundary < len(records)
        and records[boundary - 1]["started_monotonic"] <= capture_end
        and records[boundary]["started_monotonic"] >= capture_end
        and terminal["sequence"] > boundary,
        "worker_capture_terminal_sequence",
    )
    require(
        type(issued) is int
        and 1 <= issued <= boundary
        and records[issued - 1]["completed_monotonic"] <= capture_end,
        "worker_capture_issued_sequence",
    )
    require(
        io.canonical(monitor["initial_full"]) == io.canonical(initial)
        and io.canonical(monitor["terminal_full"]) == io.canonical(terminal)
        and initial["completed_monotonic"]
        <= capture_start
        <= capture_end
        <= terminal["started_monotonic"]
        and max(gaps) <= monitor["maximum_full_start_gap_seconds"] <= 1
        and observed - terminal["completed_monotonic"] <= 31
        and monitor["maximum_fence_sample_age_seconds"] <= 1
        and monitor["maximum_fence_full_age_seconds"] <= 31,
        "worker_capture_terminal_order",
    )
    first, last = monitor["first_sample"], monitor["last_sample"]
    for row in (first, last):
        record(row, full=False)
    require(
        initial["completed_monotonic"]
        <= first["started_monotonic"]
        <= first["completed_monotonic"]
        <= capture_start
        and first["completed_monotonic"] <= last["completed_monotonic"]
        and 0 <= observed - last["started_monotonic"] <= 1
        and first["started_monotonic"] <= last["started_monotonic"]
        and monitor["maximum_sample_start_gap_seconds"] <= 1,
        "worker_capture_sample_order",
    )
    if monitor["sample_count"] == 1:
        require(
            first == last and monitor["maximum_sample_start_gap_seconds"] == 0,
            "worker_capture_sample_count",
        )
    else:
        span = last["started_monotonic"] - first["started_monotonic"]
        require(
            first["reference"]["path"] != last["reference"]["path"]
            and first["reference"]["sha256"] != last["reference"]["sha256"]
            and 0
            < span / (monitor["sample_count"] - 1)
            <= monitor["maximum_sample_start_gap_seconds"]
            <= span,
            "worker_capture_sample_count",
        )
    for name, row in (("capture_initial_full", initial), ("capture_terminal_full", terminal)):
        require(inspection[name + "_ref"] == row["reference"], "worker_capture_full_reference")
        full = evidence[name]
        require(
            set(full)
            == {
                "schema",
                "attempt_id",
                "boot_id",
                "full_scan_at",
                "full_scan_count",
                "units_stopped",
                "other_container_count",
                "process_scope_ref",
                "scope_sha256",
                "freeze_actors",
                "transport_actors",
                "process_scope_observation",
                "ingress_drain_ref",
                "frozen_owned_write_fds",
                "owned_sqlite_coordination_fds",
                "sampled_at",
                "full_scan_this_sample",
                "full_scan_age_seconds",
            }
            and full["attempt_id"] == plan["attempt_id"]
            and full["boot_id"] == scope["boot_id"]
            and full["ingress_drain_ref"] == inspection["ingress_drain_ref"]
            and full["full_scan_this_sample"] is True
            and type(full["full_scan_count"]) is int
            and full["full_scan_count"] == row["full_scan_count"]
            and full["units_stopped"] == list(closure_policy.UNITS)
            and all(
                type(full[key]) is int and 0 <= full[key] <= 16_000_000
                for key in (
                    "other_container_count",
                    "frozen_owned_write_fds",
                    "owned_sqlite_coordination_fds",
                )
            )
            and all(
                _number(full[key])
                for key in ("full_scan_at", "sampled_at", "full_scan_age_seconds")
            )
            and 0 <= full["full_scan_age_seconds"] <= 30
            and _utc(plan["created_at"]).timestamp()
            <= full["full_scan_at"]
            <= full["sampled_at"]
            <= _utc(plan["deadline_utc"]).timestamp(),
            "worker_capture_full_evidence",
        )
        _process_scope_evidence(
            scope, inspection["process_scope_ref"], full, plan, response["baseline"]["native"]
        )
        for key, stopped in (("freeze_actors", True), ("transport_actors", False)):
            expected = [
                {"pid": actor["pid"], "start_ticks": actor["start_ticks"], "stopped": stopped}
                for actor in scope[key]
            ]
            require(io.canonical(full[key]) == io.canonical(expected), "worker_capture_full_actors")
    for name, row in (("capture_first_sample", first), ("capture_last_sample", last)):
        require(inspection[name + "_ref"] == row["reference"], "worker_capture_sample_reference")
        sample = evidence[name]
        require(
            set(sample)
            == {
                "schema",
                "attempt_id",
                "boot_id",
                "process_id",
                "closure_sha256",
                "process_scope_ref",
                "scope_sha256",
                "ingress_drain_ref",
                "freeze_actors",
                "transport_actors",
                "sampled_at",
                "sampled_started_monotonic",
                "sampled_monotonic",
            }
            and sample["schema"] == "autostop.j1.crm-forward-closure-sample.v1"
            and sample["attempt_id"] == plan["attempt_id"]
            and sample["boot_id"] == scope["boot_id"]
            and type(sample["process_id"]) is int
            and sample["process_id"] == monitor["process_id"]
            and sample["closure_sha256"] == inspection["closure_ownership_ref"]["sha256"]
            and sample["process_scope_ref"] == inspection["process_scope_ref"]
            and sample["scope_sha256"] == inspection["process_scope_ref"]["sha256"]
            and sample["ingress_drain_ref"] == inspection["ingress_drain_ref"]
            and all(
                _number(sample[key])
                for key in ("sampled_at", "sampled_started_monotonic", "sampled_monotonic")
            )
            and sample["sampled_started_monotonic"] == row["started_monotonic"]
            and sample["sampled_monotonic"] == row["completed_monotonic"]
            and _utc(plan["created_at"]).timestamp()
            <= sample["sampled_at"]
            <= _utc(plan["deadline_utc"]).timestamp(),
            "worker_capture_sample_evidence",
        )
        for key, stopped in (("freeze_actors", True), ("transport_actors", False)):
            expected = [
                {"pid": actor["pid"], "start_ticks": actor["start_ticks"], "stopped": stopped}
                for actor in scope[key]
            ]
            require(
                io.canonical(sample[key]) == io.canonical(expected), "worker_capture_sample_actors"
            )
    require(
        evidence["capture_first_sample"]["sampled_at"]
        <= evidence["capture_last_sample"]["sampled_at"],
        "worker_capture_sample_clock",
    )
    require(
        set(captured)
        == {
            "manifest",
            "manifest_sha256",
            "files",
            "bytes",
            "capture_monitor_ref",
            "terminal_full_ref",
        }
        | set(backup_policy.FALSE_CLAIMS)
        and all(captured[key] is False for key in backup_policy.FALSE_CLAIMS)
        and type(captured["files"]) is int
        and 0 < captured["files"] <= backup_policy.MAX_FILES
        and type(captured["bytes"]) is int
        and 0 <= captured["bytes"] <= backup_policy.MAX_TOTAL_BYTES
        and captured["capture_monitor_ref"] == inspection["capture_monitor_ref"]
        and captured["terminal_full_ref"] == inspection["capture_terminal_full_ref"]
        and captured["manifest_sha256"] == response["transport"]["manifest_sha256"],
        "worker_backup_capture_binding",
    )
    _reference(captured["manifest"])
    require(
        captured["manifest"]
        == {"path": prefix + "backup/manifest.json", "sha256": captured["manifest_sha256"]},
        "worker_backup_manifest_binding",
    )


def _capture_manifest_evidence(manifest, response):
    """Cross-bind the restored manifest's actual fence references to its monitor."""
    captured = response["evidence"]["backup_capture"]
    monitor = response["evidence"]["capture_monitor"]
    _bound(manifest, captured["manifest"])
    witnesses = manifest.get("external_closure_observations")
    require(
        type(witnesses) is list and len(witnesses) == monitor["fence_count"],
        "worker_manifest_witness_count",
    )
    allowed = {
        (row["reference"]["path"], row["reference"]["sha256"]): row["sequence"]
        for row in monitor["full_observations"]
        if row["completed_monotonic"] <= monitor["capture_finished_monotonic"]
        and row["sequence"] <= monitor["capture_last_fence_full_sequence"]
    }
    previous = 0
    for witness in witnesses:
        require(
            type(witness) is dict
            and set(witness) == {"attempt_id", "path", "sha256"}
            and witness["attempt_id"] == response["plan"]["attempt_id"],
            "worker_manifest_witness_binding",
        )
        _reference({key: witness[key] for key in ("path", "sha256")})
        sequence = allowed.get((witness["path"], witness["sha256"]), 0)
        require(sequence > 0 and sequence >= previous, "worker_manifest_witness_order")
        previous = sequence
    require(previous == monitor["capture_last_fence_full_sequence"], "worker_manifest_last_witness")
    artifacts = manifest.get("artifacts")
    require(type(artifacts) is list, "worker_manifest_artifacts")
    require(len(witnesses) == 2 * len(artifacts) + 2, "worker_manifest_fence_coverage")
    payloads = []
    for row in artifacts:
        require(type(row) is dict, "worker_manifest_artifacts")
        if "payload" in row:
            require(
                type(row.get("bytes")) is int and row["bytes"] >= 0,
                "worker_manifest_artifact_bytes",
            )
            payloads.append(row)
    require(
        len(payloads) == captured["files"]
        and sum(row["bytes"] for row in payloads) == captured["bytes"],
        "worker_manifest_capture_totals",
    )


def validate_response(response, *, attempt, stage):
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
    }
    recovery = (
        type(response) is dict
        and type(response.get("baseline")) is dict
        and "recovery_origin" in response["baseline"]
    )
    require(
        type(response) is dict
        and set(response)
        == common
        | ({"transport", "control_archive"} if stage == "closed" else set())
        | ({"recovery"} if recovery else set())
        and response["ok"] is True
        and response["stage"] == stage,
        "worker_inspect_response_invalid",
    )
    plan = response["plan"]
    _bound(plan, response["plan_ref"])
    require(
        type(plan.get("base_sha256")) is str and _hash(plan["base_sha256"]), "worker_plan_invalid"
    )
    _reference(plan.get("baseline_ref"))
    # Native POSIX reference syntax was checked above. The shared validator also
    # checks platform-local path syntax, so supply a local path for that one test.
    shape = copy.deepcopy(plan)
    shape["baseline_ref"]["path"] = str(Path(__file__).absolute())
    if variant.is_mixed(plan):
        _reference(plan.get("manager_transition_ref"))
        shape["manager_transition_ref"]["path"] = str(Path(__file__).absolute())
    variant.validate_plan(shape, plan["base_sha256"])
    require(plan["attempt_id"] == attempt and plan["host"] == NATIVE_HOST, "worker_plan_binding")
    require(plan["baseline_ref"] == response["baseline_ref"], "worker_baseline_binding")
    _bound(response["baseline"], response["baseline_ref"])
    baseline = response["baseline"]
    require(
        set(baseline)
        == {
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
        }
        | ({"recovery_origin"} if recovery else set())
        and baseline["schema"] == variant.baseline_schema(plan)
        and baseline["attempt_id"] == attempt
        and baseline["native"]["base_sha256"] == plan["base_sha256"],
        "worker_baseline_invalid",
    )
    from scripts import j1_forward_marker as marker_policy

    marker_policy.validate_parent(baseline["maintenance_parent"])
    ci = baseline["ci"]
    require(
        type(ci) is dict
        and set(ci) == {"run_id", "revision", "run_url", "jobs", "conclusion"}
        and type(ci["run_id"]) is int
        and ci["run_id"] > 0
        and ci["revision"] == plan["candidate"]["revision"]
        and ci["run_url"]
        == f"https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/{ci['run_id']}"
        and ci["jobs"] == CI_JOBS
        and ci["conclusion"] == "success",
        "worker_ci_invalid",
    )
    prefix = ATTEMPTS + "/" + attempt + "/"
    require(
        response["plan_ref"]["path"] == prefix + "plan.json"
        and response["baseline_ref"]["path"] == prefix + "baseline.json"
        and response["inspection_ref"]["path"].startswith(prefix),
        "worker_attempt_path_invalid",
    )
    _reference(response["review_key_ref"])
    require(
        response["review_key_ref"]["path"] == proofs.REVIEW_KEY_PATH
        and baseline["review_key_ref"] == response["review_key_ref"],
        "worker_review_key_binding",
    )
    _bound(response["inspection"], response["inspection_ref"])
    inspection = response["inspection"]
    fields = {
        "schema",
        "stage",
        "attempt_id",
        "plan_sha256",
        "baseline_sha256",
        "candidate",
        "native_preserved",
        "native_ref",
        "process_scope_ref",
        "ingress_drain_ref",
        "ingress_idle_ref",
    }
    fields |= (
        {"writer_closure_confirmed", "closure_ref"} | {name + "_ref" for name in CAPTURE_EVIDENCE}
        if stage == "closed"
        else {
            variant.guard_acceptance_field(plan),
            "runtime_checks_passed",
            "runtime_ref",
            "container_ref",
            "guard_starts_ref",
            "activation_cohort_ref",
        }
    )
    require(
        set(inspection) == fields
        and inspection["schema"] == variant.inspection_schema(plan)
        and inspection["stage"] == stage
        and inspection["attempt_id"] == attempt
        and inspection["plan_sha256"] == response["plan_ref"]["sha256"]
        and inspection["baseline_sha256"] == response["baseline_ref"]["sha256"]
        and inspection["candidate"] == plan["candidate"]
        and inspection["native_preserved"] is True,
        "worker_inspection_binding",
    )
    evidence = response["evidence"]
    expected_evidence = {"native", "process_scope", "ingress_drain", "ingress_idle"} | (
        {"closure"} | CAPTURE_EVIDENCE
        if stage == "closed"
        else {"runtime", "container", "guard_starts", "activation_cohort"}
    )
    require(type(evidence) is dict and set(evidence) == expected_evidence, "worker_evidence_fields")
    for name in expected_evidence:
        _bound(evidence[name], inspection[name + "_ref"])
        require(
            inspection[name + "_ref"]["path"].startswith(prefix), "worker_evidence_path_invalid"
        )
    if recovery:
        from scripts import j1_forward_recovery_worker

        j1_forward_recovery_worker.validate_response(response)
    else:
        require(
            evidence["process_scope"].get("schema") == "autostop.j1.crm-forward-process-scope.v1"
            and baseline["native"].get("schema") != "autostop.j1.crm-forward-native-recovery.v2",
            "worker_unbound_recovery_scope",
        )
    native = evidence["native"]
    if variant.is_mixed(plan) and stage == "closed":
        require(
            native.get("schema") == "autostop.j1.crm-manager-mcp-preinstall-observation.v1"
            and native.get("phase") == "staged"
            and native.get("candidate_installed") is False
            and native.get("native_acceptance") is False
            and native.get("manager_transition_ref") == plan["manager_transition_ref"]
            and native.get("predecessor_baseline_sha256")
            == baseline["native"]["predecessor_sha256"],
            "worker_mixed_preinstall_evidence",
        )
    require(
        native.get("ok") is True
        and native.get("baseline_sha256") == baseline["native"]["sha256"]
        and native.get("running") is (stage == "activated")
        and native.get("closed") is (stage == "closed"),
        "worker_native_evidence_invalid",
    )
    if stage == "closed":
        require(inspection["writer_closure_confirmed"] is True, "worker_closure_missing")
        _process_scope_evidence(
            evidence["process_scope"],
            inspection["process_scope_ref"],
            evidence["closure"],
            plan,
            baseline["native"],
        )
        transport = response["transport"]
        require(
            type(transport) is dict and set(transport) == {"path", "sha256", "manifest_sha256"},
            "worker_transport_invalid",
        )
        _reference({key: transport[key] for key in ("path", "sha256")})
        require(_hash(transport["manifest_sha256"]), "worker_manifest_hash_invalid")
        require(transport["path"].startswith(prefix), "worker_transport_path_invalid")
    else:
        require(
            inspection[variant.guard_acceptance_field(plan)] is True
            and inspection["runtime_checks_passed"] is True,
            "worker_activated_checks_missing",
        )
        starts = evidence["guard_starts"]
        require(set(starts) == NATIVE_UNITS, "worker_native_guard_set")
        for record in starts.values():
            require(
                type(record) is dict
                and set(record)
                == {
                    "invocation_id",
                    "main_pid",
                    "active_enter_monotonic_us",
                    "guard_execution_sha256",
                }
                and type(record["invocation_id"]) is str
                and re.fullmatch(r"[0-9a-f]{32}", record["invocation_id"])
                and type(record["main_pid"]) is int
                and record["main_pid"] > 0
                and type(record["active_enter_monotonic_us"]) is int
                and record["active_enter_monotonic_us"] > 0
                and _hash(record["guard_execution_sha256"]),
                "worker_native_guard_record",
            )
        _process_scope_evidence(
            evidence["process_scope"],
            inspection["process_scope_ref"],
            evidence["activation_cohort"],
            plan,
            baseline["native"],
            starts=starts,
        )
        container = evidence["container"]
        require(
            container.get("image") == plan["candidate"]["image"]
            and container.get("revision") == plan["candidate"]["revision"]
            and container.get("running") is True
            and container.get("health") == "healthy",
            "worker_runtime_container_invalid",
        )
        runtime = evidence["runtime"]
        require(
            all(
                type(runtime.get(name)) is dict and runtime[name].get("ok") is True
                for name in ("runtime", "gateway", "oauth", "performance", "feed")
            )
            and runtime.get("actual_crm_revision") == plan["candidate"]["revision"],
            "worker_runtime_evidence_invalid",
        )
    _ingress_evidence(evidence, inspection, plan)
    if stage == "closed":
        control = response["control_archive"]
        require(
            type(control) is dict
            and set(control) == {"manifest", "manifest_sha256", "stage", "archive"}
            and control["stage"] == "closed"
            and _hash(control["manifest_sha256"]),
            "worker_control_archive_required",
        )
        _reference(control["manifest"])
        _reference(control["archive"])
        control_prefix = "/var/lib/autostop-crm-forward-control-archives/" + attempt + "/closed"
        require(
            control["manifest"]
            == {"path": control_prefix + "/manifest.json", "sha256": control["manifest_sha256"]}
            and control["archive"]["path"] == control_prefix + ".tar.gz",
            "worker_control_archive_binding",
        )
        _capture_monitor_evidence(response)
    context = response["backup_context"]
    expected_context = {
        "attempt_id": attempt,
        "predecessor_sha256": plan["predecessor_sha256"],
        "candidate_crm_sha": plan["candidate"]["revision"],
        "candidate_manager_sha": plan.get("candidate_manager_sha", MANAGER_REVISION),
        "scope_profile_sha256": response["scope_profile_sha256"],
    }
    require(
        _hash(response["scope_profile_sha256"])
        and context == expected_context
        and baseline["backup_profile_sha256"]
        == response["scope_profile_sha256"]
        == variant.backup_engine(plan).profile_sha256(),
        "worker_backup_context_invalid",
    )
    _remaining(plan)
    _source_consumers(response["baseline"], plan)
    return response


class Worker:
    def __init__(self, *, ssh_key, reviewer_key, openssl, cache):
        require(os.name == "nt", "worker_windows_custody_required")
        program(Path(sys.executable))
        self.ssh_key = custody(ssh_key)
        self.reviewer_key = custody(reviewer_key)
        custody(self.reviewer_key.parent, directory=True)
        self.cache = custody(cache, directory=True)
        custody(self.cache.parent, directory=True)
        self.openssl = program(openssl)
        self.ssh = program(Path(os.environ["SystemRoot"]) / "System32/OpenSSH/ssh.exe")

    def _ssh(self, operation, attempt, *, stage=None, kind=None):
        require(
            operation in {"inspect", "export-delta", "export-control", "receive-proof"},
            "worker_remote_operation",
        )
        _uuid(attempt)
        remote = ["/usr/bin/python3", "-B", REMOTE, operation, "--attempt", attempt]
        if stage is not None:
            require(
                (operation == "inspect" and stage in {"closed", "activated"})
                or (operation == "export-control" and stage in {"closed", "terminal"}),
                "worker_remote_stage",
            )
            remote += ["--stage", stage]
        if kind is not None:
            require(operation == "receive-proof" and kind in proofs.KINDS, "worker_remote_kind")
            remote += ["--kind", kind]
        return [
            str(self.ssh),
            "-i",
            str(self.ssh_key),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=12",
            HOST,
            *remote,
        ]

    def _download(self, argv, destination, *, limit, timeout, input_bytes=None):
        """Drain bounded stdout to a private exclusive file, including on Windows."""
        require(not os.path.lexists(destination), "worker_output_exists")
        require(input_bytes is None or len(input_bytes) <= MAX_JSON, "worker_input_limit")
        errors, count, digest = [], [0], hashlib.sha256()
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            try:
                child = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
            except OSError:
                raise io.Refusal("worker_remote_unavailable") from None

            def drain():
                try:
                    while chunk := child.stdout.read(1024**2):
                        count[0] += len(chunk)
                        require(count[0] <= limit, "worker_remote_output_limit")
                        digest.update(chunk)
                        target.write(chunk)
                except (OSError, ValueError) as error:
                    errors.append(error)
                    child.kill()

            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            writer = None
            if input_bytes is not None:

                def feed():
                    try:
                        child.stdin.write(input_bytes)
                    except OSError as error:
                        errors.append(error)
                        child.kill()
                    finally:
                        try:
                            child.stdin.close()
                        except OSError:
                            pass

                writer = threading.Thread(target=feed, daemon=True)
                writer.start()
            try:
                code = child.wait(timeout=timeout)
            except (OSError, subprocess.SubprocessError):
                child.kill()
                child.wait(timeout=5)
                raise io.Refusal("worker_remote_failed") from None
            finally:
                reader.join(timeout=5)
                if writer is not None:
                    writer.join(timeout=5)
                child.stdout.close()
            require(
                not reader.is_alive()
                and (writer is None or not writer.is_alive())
                and not errors
                and code == 0,
                "worker_remote_failed",
            )
            target.flush()
            os.fsync(target.fileno())
        custody(destination)
        return {"path": str(destination), "sha256": digest.hexdigest()}

    def _crypto(self, arguments, *, timeout):
        try:
            result = subprocess.run(
                [str(self.openssl), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                env={"LANG": "C", "LC_ALL": "C", "OPENSSL_CONF": os.devnull},
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise io.Refusal("worker_crypto_failed") from None
        require(result.returncode == 0, "worker_crypto_failed")

    def _sign(self, response, kind, payload, directory):
        plan = response["plan"]
        custody(self.reviewer_key)
        envelope = {
            "schema": proofs.SCHEMA,
            "kind": kind,
            "attempt_id": plan["attempt_id"],
            "plan_sha256": response["plan_ref"]["sha256"],
            "observed_at": datetime.now(UTC).isoformat(),
            "key_sha256": response["review_key_ref"]["sha256"],
            "payload": payload,
        }
        body_path, signature_path = (
            directory / (kind + "-body.json"),
            directory / (kind + "-signature.bin"),
        )
        _new_file(body_path, io.canonical(envelope))
        require(not os.path.lexists(signature_path), "worker_output_exists")
        self._crypto(
            [
                "pkeyutl",
                "-sign",
                "-inkey",
                str(self.reviewer_key),
                "-rawin",
                "-in",
                str(body_path),
                "-out",
                str(signature_path),
            ],
            timeout=_remaining(plan, 10),
        )
        custody(signature_path)
        signature = signature_path.read_bytes()
        require(len(signature) == 64, "worker_signature_invalid")
        self._crypto(
            [
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(directory / "reviewer-public.pem"),
                "-rawin",
                "-in",
                str(body_path),
                "-sigfile",
                str(signature_path),
            ],
            timeout=_remaining(plan, 10),
        )
        envelope["signature_base64"] = base64.b64encode(signature).decode("ascii")
        raw = io.canonical(envelope)
        _new_file(directory / (kind + ".json"), raw)
        _remaining(plan)
        return raw

    def run(self, attempt, stage):
        _uuid(attempt)
        require(stage in {"closed", "activated"}, "worker_stage_invalid")
        root = self.cache.parent / ("attempt-" + attempt)
        if not root.exists():
            root.mkdir(mode=0o700)
        custody(root, directory=True)
        directory = root / (stage + "-" + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        custody(directory, directory=True)
        inspect_path = directory / "inspection-response.json"
        self._download(
            self._ssh("inspect", attempt, stage=stage), inspect_path, limit=MAX_JSON, timeout=120
        )
        response = validate_response(
            io.document(inspect_path.read_bytes()), attempt=attempt, stage=stage
        )
        plan = response["plan"]
        public = directory / "reviewer-public.pem"
        self._crypto(
            ["pkey", "-in", str(self.reviewer_key), "-pubout", "-out", str(public)],
            timeout=_remaining(plan, 10),
        )
        custody(public)
        public_bytes = public.read_bytes()
        proofs._ed25519_public_key(public_bytes)
        require(
            io.sha(public_bytes) == response["review_key_ref"]["sha256"],
            "worker_signer_key_mismatch",
        )
        binding = {
            "phase": stage,
            "plan_sha256": response["plan_ref"]["sha256"],
            "baseline_sha256": response["baseline_ref"]["sha256"],
        }
        if stage == "closed":
            transport = variant.transport_engine(plan)
            backup = transport.core()
            require(
                sqlite3.sqlite_version_info >= MIN_SQLITE
                and backup.sqlite3.sqlite_version_info >= MIN_SQLITE,
                "worker_sqlite_runtime_too_old",
            )
            require(
                response["scope_profile_sha256"]
                == transport.PROFILE_SHA256
                == backup.profile_sha256(),
                "worker_profile_mismatch",
            )
            archive = directory / "delta.tar.gz"
            archive_ref = self._download(
                self._ssh("export-delta", attempt),
                archive,
                limit=MAX_DELTA,
                timeout=_remaining(plan),
            )
            require(
                archive_ref["sha256"] == response["transport"]["sha256"],
                "worker_archive_hash_mismatch",
            )
            manifest_sha = response["transport"]["manifest_sha256"]
            assembled = directory / "assembled"
            transport.assemble_delta(
                archive,
                archive_ref["sha256"],
                manifest_sha,
                self.cache,
                assembled,
                timeout=_remaining(plan),
            )
            manifest_path = _physical(assembled / "manifest.json")
            require(manifest_path.stat().st_size <= 64 * 1024**2, "worker_manifest_size")
            _capture_manifest_evidence(io.document(manifest_path.read_bytes()), response)
            restored = backup.verify_and_restore(
                assembled,
                manifest_sha,
                directory / "restored",
                response["backup_context"],
                timeout=_remaining(plan),
            )
            receipt = restored["receipt"]
            receipt_path = _physical(receipt["path"])
            require(
                receipt_path == directory / "restored/verification.json"
                and io.sha(receipt_path.read_bytes()) == receipt["sha256"]
                and restored["provided_artifacts_restored_exactly"] is True
                and restored["source_evidence_unchanged"] is True,
                "worker_restore_receipt_invalid",
            )
            control = response["control_archive"]
            envelope = directory / "control.tar.gz"
            envelope_ref = self._download(
                self._ssh("export-control", attempt, stage="closed"),
                envelope,
                limit=control_archive.MAX_TOTAL_BYTES + control_archive.MAX_FILE_BYTES,
                timeout=_remaining(plan),
            )
            require(
                envelope_ref["sha256"] == control["archive"]["sha256"],
                "worker_control_envelope_changed",
            )
            package = directory / "control-package"
            control_archive.unbundle(
                envelope, envelope_ref["sha256"], package, timeout=min(_remaining(plan), 60)
            )
            control_restored = control_archive.verify_and_restore(
                package,
                control["manifest_sha256"],
                directory / "control-restored",
                plan_ref=response["plan_ref"],
                backup_manifest_ref=response["evidence"]["backup_capture"]["manifest"],
                stage="closed",
                timeout=min(_remaining(plan), 60),
                custody=custody,
            )
            control_receipt = custody(directory / "control-restored/restore.json")
            control_raw = control_receipt.read_bytes()
            require(
                io.document(control_raw) == control_restored
                and control_restored["actual_disposable_restore"] is True
                and control_restored["semantic_graph_verified"] is True
                and all(control_restored[name] is False for name in control_archive.CLAIMS),
                "worker_control_restore_receipt_invalid",
            )
            payloads = {
                "offhost_restore": {
                    **binding,
                    "manifest_sha256": manifest_sha,
                    "restore_receipt_sha256": receipt["sha256"],
                    "closed_inspection_sha256": response["inspection_ref"]["sha256"],
                    "profile_sha256": response["scope_profile_sha256"],
                    "actual_restore": True,
                    "control_archive_manifest_sha256": control["manifest_sha256"],
                    "control_graph_restored": True,
                    "control_restore_receipt_sha256": io.sha(control_raw),
                }
            }
        else:
            payloads = {
                "native_acceptance": {
                    **binding,
                    "inspection_sha256": response["inspection_ref"]["sha256"],
                    "native_preserved": True,
                    variant.guard_acceptance_field(plan): True,
                },
                "runtime_acceptance": {
                    **binding,
                    "inspection_sha256": response["inspection_ref"]["sha256"],
                    "candidate_image": plan["candidate"]["image"],
                    "candidate_revision": plan["candidate"]["revision"],
                    "runtime_checks_passed": True,
                },
            }
        delivered = {}
        for kind, payload in payloads.items():
            raw = self._sign(response, kind, payload, directory)
            output = directory / (kind + "-delivery.json")
            self._download(
                self._ssh("receive-proof", attempt, kind=kind),
                output,
                limit=MAX_JSON,
                timeout=_remaining(plan, 60),
                input_bytes=raw,
            )
            acknowledgement = io.document(output.read_bytes())
            require(
                type(acknowledgement) is dict
                and set(acknowledgement) == {"ok", "proof"}
                and acknowledgement["ok"] is True,
                "worker_delivery_refused",
            )
            _reference(acknowledgement["proof"])
            require(
                acknowledgement["proof"]
                == {"path": ATTEMPTS + "/" + attempt + "/" + kind + ".json", "sha256": io.sha(raw)},
                "worker_delivery_reference_mismatch",
            )
            delivered[kind] = io.sha(raw)
        return {
            "ok": True,
            "attempt_id": attempt,
            "stage": stage,
            "proof_sha256": delivered,
            "inspection_sha256": response["inspection_ref"]["sha256"],
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--stage", choices=("closed", "activated"), required=True)
    for name in ("ssh-key", "reviewer-key", "openssl", "cache"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--restore-python", type=Path)
    args = parser.parse_args(argv)
    try:
        require(os.name == "nt", "worker_windows_custody_required")
        _uuid(args.attempt)
        if args.restore_python is not None and args.restore_python != Path(sys.executable):
            interpreter = program(args.restore_python)
            arguments = list(sys.argv[1:] if argv is None else argv)
            index = arguments.index("--restore-python")
            del arguments[index : index + 2]
            result = subprocess.run(
                [str(interpreter), "-I", "-S", "-B", str(Path(__file__).absolute()), *arguments],
                timeout=7200,
                check=False,
            )
            return result.returncode
        worker = Worker(
            ssh_key=args.ssh_key,
            reviewer_key=args.reviewer_key,
            openssl=args.openssl,
            cache=args.cache,
        )
        print(io.canonical(worker.run(args.attempt, args.stage)).decode("utf-8"), end="")
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        print('{"ok":false,"error":"worker_operation_refused"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
