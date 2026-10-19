"""Prepared MCP successor guard. No installer, admission issuer or ARM operation.

Production entry requires a separately issued, protected pre-start authority.
Source capsules never grant execution. This module imports only the standard
library; its sole executable sidecar is verified as bytes before execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import stat
import sys
import time
import types
import uuid
from pathlib import Path, PurePosixPath

HOST = "vps26457.mnogoweb.in"
UNIT = "autostop-manager-mcp.service"
REVISION = "2b8ba701a7058aaf79478ef38cb21532d95f2a72"
TREE = "32fc40d8a5ab4548219a99c11b242b06eaf042bf"
OLD_REVISION = "89af29082e0a6f72c5d85ae448acb6d4deec5d94"
CONTRACT = "manager_mcp_only_preserved_warm_v1"
INSTALL = "/usr/local/libexec/autostop-manager-mcp-successor/v1"
CONFIG = "/etc/autostop-manager-mcp-successor/v1"
STATE_DIR = "/var/lib/autostop-manager-mcp-successor/v1"
GUARD = INSTALL + "/guard.py"
NATIVE = INSTALL + "/native.py"
BINDING = CONFIG + "/binding.json"
CAPSULE = CONFIG + "/source-capsule.json"
INDEX = CONFIG + "/source-index.json"
BASELINE = CONFIG + "/predecessor-baseline.json"
RUNTIME = CONFIG + "/runtime-acceptance.json"
STATE = STATE_DIR + "/armed.json"
ADMISSION = STATE_DIR + "/admission.json"
SOURCE_ROOT = f"/opt/autostop-manager-mcp-releases/{REVISION}/source"
SELECTOR = "/opt/autostop-manager-mcp-releases/current"
DROPIN = f"/etc/systemd/system/{UNIT}.d/zz-j1-authority-mcp.conf"
POLICY = "/etc/autostop-j1-candidate-guard/policy.json"
FORWARD = "/etc/autostop-j1-candidate-guard/forward21.json"
OLD_STATE = "/var/lib/autostop-j1-candidate-guard/armed.json"
OLD_INDEX = "/usr/local/libexec/autostop-j1-candidate-guard/source-index.json"
PUBLIC_GUARD = "/usr/local/libexec/autostop-j1-candidate-guard/guard.py"
PUBLIC_DUTY = "/usr/local/libexec/autostop-crm-j1-duty/restore.py"
OLD_GUARD = "/usr/local/libexec/autostop-j1-candidate-guard/guard.original15-d5275167987434df.py"
OLD_DUTY = "/usr/local/libexec/autostop-crm-j1-duty/restore.original15-c7256cebae7c9dfc.py"
PINS = {
    POLICY: "54c764ca99d2c398da6659502c0a744ef3d131936aaff60a3ea95f56758206af",
    FORWARD: "432e097d7dfd50ab795fe508c3e84317cc9c80596bb4fbc8432decd7944d33ee",
    OLD_STATE: "815552f89e334dfd92d68ced03e7bc312df3352a38cccea8bcc1ed11ef3a3002",
    OLD_INDEX: "1cfd4aff5ba303e00c12f8b70d9408c77de1c08313586acd49cb96a20a524e3c",
    PUBLIC_GUARD: "c5de603f10ba2cbe6c72249c9f38e662a5e812b9d6f84442277eaea4147c2975",
    PUBLIC_DUTY: "48cc13f80e3e3cb217a8068bf3a93882259e3b308a613ce3be436bcb5609708d",
    OLD_GUARD: "d5275167987434dffab4f81f0671c381c7a32f69e281fd8d4f8644715f3427c6",
    OLD_DUTY: "c7256cebae7c9dfc4eee37701a462a4d128a66b937d1beabf94c76b6578d62f4",
    "/var/lib/autostop-crm-j1-duty/committed.json": "a4ba90f7a418d37a2b9bffa4e4555b63f3e4d7961e7ff4d29a59fcf7d8667d85",
}
UNITS = {
    UNIT,
    "autostop-j1.service",
    "autostop-manager-scheduler.service",
    "autostop-work-telegram.service",
    "autostop-telegram.service",
    "autostop-codex-wake.service",
}
PROFILE_FIELDS = set(
    "source_root source_identity selector manifest_sha256 module arguments access cwd cwd_identity interpreter interpreter_realpath interpreter_identity interpreter_sha256 runtime_files runtime_receipt startup_locations stable_environment_sha256 environment_files unit_files guard_dropin dropin_directories credentials ordinary_database".split()
)
BINDING_FIELDS = set(
    "schema contract attempt_id host unit guard native capsule source_index predecessor_baseline profile".split()
)
CAPSULE_FIELDS = set(
    "schema contract attempt_id controller candidate predecessor target preserved impact required_later native_acceptance activation_authorized candidate_may_have_written old_writer_allowed sha256".split()
)
RUNTIME_FIELDS = set(
    "schema scope revision source_root source_identity manager_origin manifest_sha256 interpreter interpreter_sha256 stable_input_environment_sha256 startup_paths complete_startup_path_enumerated startup_hooks_reviewed dependency_runtime_sealed application_imported source_capsule_sha256 predecessor_runtime_sha256".split()
)
VOLATILE = {
    "INVOCATION_ID": r"[0-9a-f]{32}",
    "JOURNAL_STREAM": r"[0-9]+:[0-9]+",
    "SYSTEMD_EXEC_PID": r"[1-9][0-9]*",
}
PROOF_FACTS = {
    "source_verification": {
        "full_git_index_verified": True,
        "source_capsule_rederived": True,
        "application_imported": False,
    },
    "runtime": {
        "native_paths_inspected": True,
        "startup_hooks_reviewed": True,
        "dependency_runtime_sealed": True,
        "application_imported": False,
    },
    "preserved_authority": {
        "old_source_inventory_exact": True,
        "other_five_bindings_unchanged": True,
        "warm_identity_retained": True,
        "bytecode_ignored": False,
    },
    "closure": {
        "owned_closure": True,
        "all_writers_closed": True,
        "old_main_pid_gone": True,
        "candidate_started": False,
    },
    "backup": {"snapshot_verified": True, "captured_after_closure": True},
    "offhost_restore": {"offhost_verified": True, "disposable_restore_verified": True},
    "store_stop": None,
}
HEX = re.compile(r"[0-9a-f]{64}\Z")
LIMIT = 32 * 1024**2


class Refusal(ValueError):
    """Bounded, non-secret denial code."""


def require(value, code):
    if not value:
        raise Refusal(code)


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _fields(value, names, code):
    require(type(value) is dict and set(value) == set(names), code)
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _constant(_value):
    raise Refusal("nonfinite_json")


def document(raw):
    require(type(raw) is bytes and len(raw) <= LIMIT, "document_bounds")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, Refusal):
            raise
        raise Refusal("invalid_json") from exc
    require(type(value) is dict, "document_object")
    # Also rejects JSON floats overflowing to infinity, which parse_constant cannot see.
    try:
        canonical(value)
    except (ValueError, RecursionError) as exc:
        raise Refusal("invalid_json") from exc
    return value


def _hash(value):
    require(type(value) is str and HEX.fullmatch(value), "digest_invalid")
    return value


def _path(value):
    require(
        type(value) is str
        and value.startswith("/")
        and not value.startswith("//")
        and str(PurePosixPath(value)) == value
        and ".." not in PurePosixPath(value).parts
        and "\\" not in value
        and all(32 <= ord(c) != 127 for c in value),
        "path_invalid",
    )
    return value


def _reference(value, path=None):
    _fields(value, {"path", "sha256"}, "reference_fields")
    _path(value["path"])
    _hash(value["sha256"])
    require(path is None or value["path"] == path, "reference_path")
    return value


def _identity(value):
    require(
        type(value) is list and len(value) == 2 and all(type(v) is int and v >= 0 for v in value),
        "physical_identity",
    )


def _uuid(value):
    require(type(value) is str and str(uuid.UUID(value)) == value, "uuid_invalid")


class Reader:
    def __init__(self, io, deadline):
        self.io, self.deadline, self.seen = io, deadline, {}

    def raw(self, ref):
        _reference(ref)
        require(time.monotonic() < self.deadline, "guard_deadline")
        require(self.seen.get(ref["path"], ref) == ref, "reference_conflict")
        raw = self.io.read(ref["path"], LIMIT)
        require(
            type(raw) is bytes and len(raw) <= LIMIT and sha(raw) == ref["sha256"],
            "reference_changed",
        )
        self.seen[ref["path"]] = dict(ref)
        return raw

    def doc(self, ref):
        return document(self.raw(ref))

    def recheck(self):
        for ref in list(self.seen.values()):
            self.raw(ref)


def render_dropin(binding_sha256):
    _hash(binding_sha256)
    command = f"/usr/bin/python3 -I -S -B {GUARD}"
    arguments = f" --binding-sha256 {binding_sha256} --unit {UNIT}\n"
    return (
        f"[Service]\nEnvironment=PYTHONPATH={SELECTOR}\nExecCondition=\n"
        f"ExecCondition=+{command} check{arguments}ExecStart=\n"
        f"ExecStart={command} launch{arguments}"
    ).encode()


def _binding_shape(binding):
    _fields(binding, BINDING_FIELDS, "binding_fields")
    require(
        binding["schema"] == "autostop.j1.manager-mcp-native-binding.v1"
        and binding["contract"] == CONTRACT
        and binding["host"] == HOST
        and binding["unit"] == UNIT,
        "binding_scope",
    )
    _uuid(binding["attempt_id"])
    for key, path in (
        ("guard", GUARD),
        ("native", NATIVE),
        ("capsule", CAPSULE),
        ("source_index", INDEX),
        ("predecessor_baseline", BASELINE),
    ):
        _reference(binding[key], path)
    _fields(binding["profile"], PROFILE_FIELDS | {"manager_revision"}, "profile_fields")


def git_tree(files):
    """Bind the complete native blob inventory to the reviewed immutable tree."""
    require(type(files) is list and files, "source_files")
    root = {}
    for record in files:
        _fields(record, {"path", "mode", "git_oid", "size", "sha256"}, "source_entry_fields")
        path = record["path"]
        require(
            type(path) is str
            and not path.startswith("/")
            and "\\" not in path
            and str(PurePosixPath(path)) == path
            and ".." not in PurePosixPath(path).parts
            and path not in {".", "REVISION", "MANIFEST.sha256", "MANIFEST.files0"}
            and all(32 <= ord(c) != 127 for c in path),
            "source_path",
        )
        require(
            record["mode"] in {"100644", "100755"}
            and type(record["git_oid"]) is str
            and re.fullmatch(r"[0-9a-f]{40}", record["git_oid"])
            and type(record["size"]) is int
            and 0 <= record["size"] <= LIMIT,
            "source_entry",
        )
        _hash(record["sha256"])
        node = root
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            require(type(node) is dict, "source_path_conflict")
        require(parts[-1] not in node, "source_duplicate")
        node[parts[-1]] = (record["mode"], record["git_oid"])

    def tree(node):
        data = b""
        for name, value in sorted(
            node.items(),
            key=lambda pair: (pair[0] + ("/" if type(pair[1]) is dict else "")).encode(),
        ):
            mode, oid = ("40000", tree(value)) if type(value) is dict else value
            data += mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(oid)
        return hashlib.sha1(b"tree " + str(len(data)).encode() + b"\0" + data).hexdigest()

    return tree(root)


def _legacy_hash(value):
    return sha(
        (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    )


def _accepted_environments(forward, policy, reader):
    _fields(forward["profiles"], UNITS, "accepted_profiles")
    environments = {}
    for unit, profile in forward["profiles"].items():
        _fields(
            profile,
            {"input", "request", "unit_context_ref", "unit_context"},
            "accepted_profile_fields",
        )
        request, observed = reader.doc(profile["request"]), reader.doc(profile["input"])
        require(
            observed.get("schema") == "autostop.j1.input-observation.v1"
            and observed.get("scope") == "production_quiet_window"
            and request.get("attempt_id") == observed.get("attempt_id") == forward["attempt_id"]
            and request.get("unit") == observed.get("unit") == unit
            and observed.get("request_sha256") == profile["request"]["sha256"],
            "accepted_input_binding",
        )
        env, binding = observed.get("stable_input_environment"), policy["bindings"][unit]
        require(
            type(env) is dict and all(type(k) is str and type(v) is str for k, v in env.items()),
            "accepted_environment",
        )
        require(
            _legacy_hash(env)
            == observed.get("stable_environment_sha256")
            == binding["stable_environment_sha256"]
            and env.get("PYTHONPATH") == binding["selector"],
            "accepted_environment_hash",
        )
        context = reader.doc(profile["unit_context_ref"])
        _fields(
            context,
            "Id Names LoadState Type NotifyAccess FragmentPath SourcePath NeedDaemonReload".split(),
            "accepted_unit_fields",
        )
        require(
            context == profile["unit_context"]
            and all(type(v) is str for v in context.values())
            and context["Id"] == context["Names"] == unit
            and context["LoadState"] == "loaded"
            and context["FragmentPath"] == "/etc/systemd/system/" + unit
            and context["SourcePath"] == ""
            and context["NeedDaemonReload"] == "no",
            "accepted_unit_context",
        )
        environments[unit] = env
    return environments


def _predecessor_chain(forward, baseline, reader, references):
    """Recover authority edges from frozen bytes, never from self-selected baseline refs."""
    expected = {path: {"path": path, "sha256": digest} for path, digest in PINS.items()}
    for name in (
        "original_guard",
        "original_duty_controller",
        "original_duty_intent",
        "policy",
        "armed",
        "index",
        "admitted",
        "activation",
    ):
        ref = _reference(forward[name])
        expected[ref["path"]] = ref
    intent, admitted = reader.doc(forward["original_duty_intent"]), reader.doc(forward["admitted"])
    activation = reader.doc(forward["activation"])
    prep = {"path": intent["preparation_path"], "sha256": intent["preparation_sha256"]}
    folder = str(PurePosixPath(prep["path"]).parent)
    closure_ref = {"path": folder + "/closure-acceptance.json", "sha256": intent["closure_sha256"]}
    handoff = {"path": folder + "/handoff.json", "sha256": admitted["handoff_sha256"]}
    reader.doc(prep)
    reader.doc(handoff)
    closure = reader.doc(closure_ref)
    lifecycle = reader.doc(closure["lifecycle"])
    warm = reader.doc(lifecycle["daemon_identity_receipt"])
    require(
        closure.get("daemon_mode") == lifecycle.get("daemon_mode") == "accepted_warm"
        and warm.get("actual_cache_read_only") is True
        and warm.get("host_cache_read_only") is True,
        "accepted_warm_contract",
    )
    state = reader.doc(warm["cache_view_state"])
    _uuid(warm["boot_id"])
    require(state.get("boot_id") == warm["boot_id"], "accepted_cache_boot")
    for ref in (
        prep,
        handoff,
        closure_ref,
        closure["lifecycle"],
        lifecycle["daemon_identity_receipt"],
        warm["cache_view_state"],
    ):
        expected[ref["path"]] = ref
    cache = baseline["cache"]
    _fields(
        cache,
        "policy native_proof database_identity parent_identity pid1 self state".split(),
        "baseline_cache_fields",
    )
    policy_ref = _reference(cache["policy"], "/etc/autostop-j1-cache-view/policy.json")
    require(
        policy_ref["sha256"] == state["policy_sha256"]
        and cache["state"] == warm["cache_view_state"],
        "baseline_cache_authority",
    )
    cache_policy = reader.doc(policy_ref)
    require(
        cache_policy.get("schema") == "autostop.j1.cache-view-policy.v1"
        and cache_policy.get("host") == HOST
        and cache["database_identity"] == cache_policy["database_identity"]
        and cache["parent_identity"] == cache_policy["cache_identity"]
        and cache["native_proof"]
        == {
            "path": cache_policy["native_proof_path"],
            "sha256": cache_policy["native_proof_sha256"],
        }
        and cache["pid1"].get("mode") == cache["self"].get("mode") == "ro",
        "baseline_cache_identity",
    )
    expected[policy_ref["path"]] = policy_ref
    require(
        baseline["authority"] == sorted(expected.values(), key=lambda r: r["path"])
        and baseline["base_sha256"]
        == sha(
            canonical({k: baseline[k] for k in ("manager_revision", "daemon_mode", "authority")})
        ),
        "baseline_authority_inventory",
    )
    accepted, observed = warm["actors"], baseline["warm_actors"]
    require(
        type(accepted) is list
        and type(observed) is list
        and accepted
        and len(accepted) == len(observed),
        "baseline_warm_inventory",
    )
    accepted_by_pid = {actor["pid"]: actor for actor in accepted}
    require(
        len(accepted_by_pid) == len(accepted)
        and {actor["pid"] for actor in observed} == set(accepted_by_pid),
        "baseline_warm_inventory",
    )
    for actor in observed:
        require(
            type(actor["pid"]) is int
            and actor["pid"] > 0
            and type(actor["uid"]) is int
            and actor["uid"] == 0
            and all(
                actor[key] == accepted_by_pid[actor["pid"]][key]
                for key in ("pid", "start_ticks", "argv_sha256", "cgroup", "exe", "mount_namespace")
            ),
            "baseline_warm_identity",
        )
    # These extra reads are source-compiler evidence, not the observer authority list.
    quiet = activation["evidence"]["quiet_window"]
    reader.doc(quiet)
    reader.raw(cache["native_proof"])
    all_refs = dict(expected)
    for ref in (quiet, cache["native_proof"]):
        all_refs[ref["path"]] = ref
    for profile in forward["profiles"].values():
        for key in ("input", "request", "unit_context_ref"):
            ref = profile[key]
            all_refs[ref["path"]] = ref
    require(references == all_refs, "source_predecessor_inventory")
    return warm


def _source_authority(binding, reader):
    capsule = reader.doc(binding["capsule"])
    _fields(capsule, CAPSULE_FIELDS, "source_capsule_fields")
    require(
        capsule["schema"] == "autostop.j1.manager-mcp-source-capsule.v1"
        and capsule["contract"] == CONTRACT
        and capsule["attempt_id"] == binding["attempt_id"]
        and capsule["native_acceptance"] is False
        and capsule["activation_authorized"] is False
        and capsule["candidate_may_have_written"] is True
        and capsule["old_writer_allowed"] is False
        and capsule["sha256"]
        == sha(canonical({k: v for k, v in capsule.items() if k != "sha256"})),
        "source_capsule_contract",
    )
    _fields(
        capsule["candidate"], {"revision", "tree", "source_index_sha256", "ci"}, "candidate_fields"
    )
    require(
        capsule["candidate"]["revision"] == REVISION and capsule["candidate"]["tree"] == TREE,
        "fixed_candidate",
    )
    index = reader.doc(binding["source_index"])
    _fields(index, {"schema", "revision", "tree", "files"}, "source_index_fields")
    require(
        index["schema"] == "autostop.j1.manager-git-source.v1"
        and index["revision"] == REVISION
        and index["tree"] == TREE
        and sha(canonical(index)) == capsule["candidate"]["source_index_sha256"],
        "source_index_binding",
    )
    require(git_tree(index["files"]) == TREE, "source_tree_mismatch")
    _fields(
        capsule["predecessor"],
        {"baseline_sha256", "authority_base_sha256", "references"},
        "predecessor_fields",
    )
    require(
        binding["predecessor_baseline"]["sha256"] == capsule["predecessor"]["baseline_sha256"],
        "baseline_binding",
    )
    baseline = reader.doc(binding["predecessor_baseline"])
    require(
        baseline.get("schema") == "autostop.crm.j1-forward-native-baseline.v1"
        and baseline.get("manager_revision") == OLD_REVISION
        and baseline.get("daemon_mode") == "accepted_warm"
        and baseline.get("sha256")
        == sha(canonical({k: v for k, v in baseline.items() if k != "sha256"}))
        and baseline.get("base_sha256") == capsule["predecessor"]["authority_base_sha256"],
        "baseline_contract",
    )
    refs = capsule["predecessor"]["references"]
    require(
        type(refs) is list and len(refs) == len({_reference(r)["path"] for r in refs}),
        "predecessor_references",
    )
    references = {ref["path"]: ref for ref in refs}
    for path, digest in PINS.items():
        require(references.get(path) == {"path": path, "sha256": digest}, "old_authority_pin")
    for ref in refs:
        reader.raw(ref)
    policy = reader.doc(references[POLICY])
    forward = reader.doc(references[FORWARD])
    old_state = reader.doc(references[OLD_STATE])
    old_index = reader.doc(references[OLD_INDEX])
    require(
        policy.get("revision")
        == old_index.get("revision")
        == old_state.get("revision")
        == OLD_REVISION
        and set(policy["bindings"]) == UNITS
        and old_state.get("old_writer_allowed") is False
        and old_state.get("candidate_may_have_written") is True,
        "old_authority_context",
    )
    _fields(
        capsule["preserved"],
        {
            "profiles",
            "warm_actors_sha256",
            "cache_sha256",
            "duty_commit",
            "denied_units",
            "source_contract_files",
            "runtime_observation_sha256",
        },
        "preserved_fields",
    )
    preserved = {
        unit: sha(canonical(profile))
        for unit, profile in policy["bindings"].items()
        if unit != UNIT
    }
    require(
        capsule["preserved"]["profiles"] == preserved
        and capsule["preserved"]["warm_actors_sha256"] == sha(canonical(baseline["warm_actors"]))
        and capsule["preserved"]["cache_sha256"] == sha(canonical(baseline["cache"])),
        "preserved_binding",
    )
    for unit, profile in policy["bindings"].items():
        require(
            baseline["profiles"][unit]["binding_sha256"] == sha(canonical(profile)),
            "baseline_profile",
        )
    environments = _accepted_environments(forward, policy, reader)
    require(
        capsule["target"]["environment"]["after_sha256"]
        == _legacy_hash({**environments[UNIT], "PYTHONPATH": SELECTOR}),
        "source_environment_delta",
    )
    _predecessor_chain(forward, baseline, reader, references)
    return capsule, index, baseline, policy, forward, old_index, references


def _profile(binding, capsule, policy, reader):
    old, current = policy["bindings"][UNIT], binding["profile"]
    _fields(old, PROFILE_FIELDS, "old_profile_fields")
    target = capsule["target"]
    _fields(
        target,
        {
            "unit",
            "before_binding_sha256",
            "manager_revision",
            "source_root",
            "selector",
            "manifest_sha256",
            "cwd",
            "guard",
            "dropin",
            "environment",
        },
        "target_fields",
    )
    _fields(
        target["environment"],
        {"before_sha256", "after_sha256", "PYTHONPATH", "launch_pythonpath"},
        "environment_delta_fields",
    )
    require(
        target["unit"] == UNIT
        and target["before_binding_sha256"] == sha(canonical(old))
        and target["manager_revision"] == REVISION
        and target["source_root"] == SOURCE_ROOT
        and target["selector"] == SELECTOR
        and target["guard"] == GUARD
        and target["dropin"] == DROPIN
        and target["cwd"] == old["cwd"]
        and target["environment"]["before_sha256"] == old["stable_environment_sha256"]
        and target["environment"]["PYTHONPATH"] == {"before": old["selector"], "after": SELECTOR}
        and target["environment"]["launch_pythonpath"] == SOURCE_ROOT,
        "target_delta",
    )
    _identity(current["source_identity"])
    runtime_refs = [r for r in current["runtime_files"] if r["path"] == RUNTIME]
    old_runtime_refs = [r for r in old["runtime_files"] if r["path"] == old["runtime_receipt"]]
    require(len(runtime_refs) == len(old_runtime_refs) == 1, "runtime_receipt_reference")
    runtime_ref, old_runtime_ref = runtime_refs[0], old_runtime_refs[0]
    _reference(runtime_ref, RUNTIME)
    expected = {
        **old,
        "manager_revision": REVISION,
        "source_root": SOURCE_ROOT,
        "source_identity": current["source_identity"],
        "selector": SELECTOR,
        "manifest_sha256": target["manifest_sha256"],
        "stable_environment_sha256": target["environment"]["after_sha256"],
        "runtime_receipt": RUNTIME,
        "runtime_files": [runtime_ref if r == old_runtime_ref else r for r in old["runtime_files"]],
        "startup_locations": [
            {**r, "path": SOURCE_ROOT} if r["path"] == old["source_root"] else r
            for r in old["startup_locations"]
        ],
    }
    require(
        current == expected
        and current["module"] == "autostop_manager.mcp_server"
        and current["arguments"] == []
        and current["access"] == "writer"
        and current["credentials"] == [0, 0, []]
        and sum(r["path"] == old["source_root"] for r in old["startup_locations"]) == 1,
        "profile_delta",
    )
    reader.raw(old_runtime_ref)
    receipt = reader.doc(runtime_ref)
    expected_receipt = {
        "schema": "autostop.j1.manager-mcp-runtime-acceptance.v1",
        "scope": CONTRACT,
        "revision": REVISION,
        "source_root": SOURCE_ROOT,
        "source_identity": current["source_identity"],
        "manager_origin": SOURCE_ROOT + "/autostop_manager/__init__.py",
        "manifest_sha256": current["manifest_sha256"],
        "interpreter": current["interpreter"],
        "interpreter_sha256": current["interpreter_sha256"],
        "stable_input_environment_sha256": current["stable_environment_sha256"],
        "startup_paths": [r["path"] for r in current["startup_locations"]],
        "complete_startup_path_enumerated": True,
        "startup_hooks_reviewed": True,
        "dependency_runtime_sealed": True,
        "application_imported": False,
        "source_capsule_sha256": binding["capsule"]["sha256"],
        "predecessor_runtime_sha256": old_runtime_ref["sha256"],
    }
    require(canonical(receipt) == canonical(expected_receipt), "runtime_receipt_contract")
    return receipt


def _observation(observed, profile):
    _fields(
        observed,
        {"host", "boot_id", "cgroup", "uid", "gid", "groups", "cwd", "environment"},
        "observation_fields",
    )
    _uuid(observed["boot_id"])
    require(
        observed["host"] == HOST
        and observed["cgroup"] == "/system.slice/" + UNIT
        and type(observed["uid"]) is int
        and type(observed["gid"]) is int
        and type(observed["groups"]) is list
        and all(type(n) is int for n in observed["groups"])
        and [observed["uid"], observed["gid"], sorted(observed["groups"])] == profile["credentials"]
        and observed["cwd"] == profile["cwd"],
        "native_process_context",
    )
    environment = observed["environment"]
    require(
        type(environment) is dict
        and all(type(k) is str and type(v) is str for k, v in environment.items()),
        "environment_fields",
    )
    stable = dict(environment)
    for key, pattern in VOLATILE.items():
        if key in stable:
            require(re.fullmatch(pattern, stable.pop(key)) is not None, "volatile_environment")
    digest = sha(
        (json.dumps(stable, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    )
    require(
        digest == profile["stable_environment_sha256"] and stable.get("PYTHONPATH") == SELECTOR,
        "environment_changed",
    )


def _deny_markers(io):
    for path in (
        STATE_DIR + "/arming-pending.json",
        STATE_DIR + "/closed.json",
        "/var/lib/autostop-j1-candidate-guard/arming-pending.json",
        "/var/lib/autostop-crm-j1-duty/commit-pending.json",
    ):
        require(not io.exists(path), "pending_or_closed")


def _permit(binding, binding_sha256, observed, reader):
    raw = reader.io.read(STATE, 65536)
    state = document(raw)
    context = {
        "contract": CONTRACT,
        "attempt_id": binding["attempt_id"],
        "host": HOST,
        "boot_id": observed["boot_id"],
        "unit": UNIT,
        "binding_sha256": binding_sha256,
        "guard_sha256": binding["guard"]["sha256"],
        "native_sha256": binding["native"]["sha256"],
        "source_capsule_sha256": binding["capsule"]["sha256"],
        "predecessor_baseline_sha256": binding["predecessor_baseline"]["sha256"],
    }
    _fields(
        state,
        {"schema", *context, "admission", "candidate_may_have_written", "old_writer_allowed"},
        "state_fields",
    )
    require(
        state["schema"] == "autostop.j1.manager-mcp-successor-state.v1"
        and all(type(state[k]) is type(v) and state[k] == v for k, v in context.items())
        and state["candidate_may_have_written"] is True
        and state["old_writer_allowed"] is False,
        "state_context",
    )
    admission = reader.doc(_reference(state["admission"], ADMISSION))
    _fields(
        admission,
        {
            "schema",
            *context,
            "proofs",
            "prestart_authorized",
            "native_acceptance",
            "candidate_may_have_written",
            "old_writer_allowed",
        },
        "admission_fields",
    )
    require(
        admission["schema"] == "autostop.j1.manager-mcp-prestart-admission.v1"
        and all(type(admission[k]) is type(v) and admission[k] == v for k, v in context.items())
        and admission["prestart_authorized"] is True
        and admission["native_acceptance"] is False
        and admission["candidate_may_have_written"] is True
        and admission["old_writer_allowed"] is False,
        "admission_context",
    )
    _fields(admission["proofs"], PROOF_FACTS, "proof_inventory")
    for kind, facts in PROOF_FACTS.items():
        proof = reader.doc(
            _reference(admission["proofs"][kind], STATE_DIR + "/proofs/" + kind + ".json")
        )
        _fields(
            proof,
            {"schema", *context, "facts", "evidence", "native_acceptance", "old_writer_allowed"},
            "proof_fields",
        )
        require(
            proof["schema"] == f"autostop.j1.manager-mcp-prestart-{kind}.v1"
            and all(type(proof[k]) is type(v) and proof[k] == v for k, v in context.items())
            and proof["native_acceptance"] is False
            and proof["old_writer_allowed"] is False,
            "proof_context",
        )
        if kind == "store_stop":
            _store_facts(proof["facts"])
        else:
            require(canonical(proof["facts"]) == canonical(facts), "proof_required_facts")
        evidence = proof["evidence"]
        require(
            type(evidence) is list
            and evidence
            and len(evidence) == len({_reference(ref)["path"] for ref in evidence}),
            "proof_evidence",
        )
        for ref in evidence:
            reader.raw(ref)
    reader.raw({"path": STATE, "sha256": sha(raw)})
    return context


def _store_facts(value):
    _fields(
        value,
        {
            "manager_revision",
            "mode",
            "stop_permitted",
            "store_workers_drained",
            "worker_drain_supported",
        },
        "store_proof_fields",
    )
    require(
        value["manager_revision"] in {OLD_REVISION, REVISION} and value["stop_permitted"] is True,
        "store_proof_revision",
    )
    if value["mode"] == "legacy_unsupported":
        require(
            value["manager_revision"] == OLD_REVISION
            and value["worker_drain_supported"] is False
            and value["store_workers_drained"] is None,
            "legacy_store_proof",
        )
    elif value["mode"] == "store_workers_drained":
        require(
            value["manager_revision"] == REVISION
            and value["worker_drain_supported"] is True
            and value["store_workers_drained"] is True,
            "modern_store_proof",
        )
    elif value["mode"] == "no_active_process":
        require(
            value["store_workers_drained"] is None
            and type(value["worker_drain_supported"]) is bool,
            "inactive_store_proof",
        )
    else:
        raise Refusal("unknown_store_proof")


def authorize_locked(stage, binding_sha256, *, io, deadline):
    """Caller must hold the adapter lifecycle lock through check or dispatch."""
    require(stage in {"check", "launch"}, "unknown_operation")
    reader = Reader(io, deadline)
    binding = reader.doc({"path": BINDING, "sha256": _hash(binding_sha256)})
    _binding_shape(binding)
    reader.raw(binding["guard"])
    reader.raw(binding["native"])
    _deny_markers(io)
    observed = io.observation()
    capsule, index, baseline, policy, forward, old_index, refs = _source_authority(binding, reader)
    receipt = _profile(binding, capsule, policy, reader)
    profile = binding["profile"]
    _observation(observed, profile)
    _permit(binding, binding_sha256, observed, reader)
    context = forward["profiles"][UNIT]["unit_context"]
    require(
        context.get("Type") == "simple" and context.get("NotifyAccess") == "none", "mcp_unit_type"
    )
    # Every preserved source is checked, including the old cwd and both Telegram roots.
    # A protected grant cannot turn existing bytecode/source drift into acceptance.
    io.verify_preserved_sources(policy, old_index)
    io.verify_preserved_authority(policy, forward)
    io.verify_source(profile, index)
    io.verify_runtime(profile, receipt)
    old_refs = {
        PUBLIC_GUARD: {"path": OLD_GUARD, "sha256": PINS[OLD_GUARD]},
        PUBLIC_DUTY: {"path": OLD_DUTY, "sha256": PINS[OLD_DUTY]},
    }
    io.verify_units(
        profile,
        binding_sha256=binding_sha256,
        old_policy_sha256=PINS[POLICY],
        old_refs=old_refs,
        dropin_bytes=render_dropin(binding_sha256),
    )
    io.verify_cache(profile)
    # The receipt boot is part of the accepted warm chain, not a caller-selected boot.
    warm_refs = [ref for ref in refs.values() if ref["path"] == baseline["cache"]["state"]["path"]]
    require(len(warm_refs) == 1, "warm_cache_reference")
    cache_state = reader.doc(warm_refs[0])
    require(cache_state.get("boot_id") == observed["boot_id"], "warm_boot_changed")
    io.verify_warm(
        {
            "boot_id": observed["boot_id"],
            "actors": baseline["warm_actors"],
            "cache": baseline["cache"],
        }
    )
    reader.recheck()
    _deny_markers(io)
    _observation(io.observation(), profile)
    require(time.monotonic() < deadline, "guard_deadline")
    environment = {**observed["environment"], "PYTHONPATH": SOURCE_ROOT}
    return {
        "executable": profile["interpreter"],
        "argv": [profile["interpreter"], "-P", "-m", "autostop_manager.mcp_server"],
        "environment": environment,
        "cwd": profile["cwd"],
        "unit": UNIT,
        "revision": REVISION,
        "candidate_may_have_written": True,
        "old_writer_allowed": False,
    }


def run_guard(stage, binding_sha256, *, io, dispatcher=None, deadline=None):
    deadline = time.monotonic() + 15 if deadline is None else deadline
    try:
        with io.lock():
            plan = authorize_locked(stage, binding_sha256, io=io, deadline=deadline)
            if stage == "check":
                return {
                    "ok": True,
                    "decision": "ALLOW_MCP_SUCCESSOR_CONDITION",
                    "unit": UNIT,
                    "revision": REVISION,
                    "native_acceptance": False,
                }
            (io.dispatch if dispatcher is None else dispatcher)(plan)
            raise Refusal("exec_returned_without_dispatch")
    except (KeyError, TypeError, AttributeError, OSError, OverflowError, RecursionError) as exc:
        raise Refusal("native_authority_unavailable") from exc
    except ValueError as exc:
        if isinstance(exc, Refusal):
            raise
        raise Refusal("native_authority_invalid") from exc


def protected_read(path, limit=LIMIT):
    """Minimal bootstrap reader, before any executable sidecar can be loaded."""
    path = Path(_path(path))
    for parent in reversed(path.parents):
        info = parent.lstat()
        require(
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            "bootstrap_directory",
        )
    before = path.lstat()
    require(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == 0
        and before.st_nlink == 1
        and not before.st_mode & 0o7022
        and before.st_size <= limit,
        "bootstrap_file",
    )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after, final = os.fstat(fd), path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        require(
            len(raw) == before.st_size <= limit
            and all(
                getattr(before, k) == getattr(opened, k) == getattr(after, k) == getattr(final, k)
                for k in fields
            ),
            "bootstrap_file_changed",
        )
        return raw
    finally:
        os.close(fd)


def load_native(binding_sha256, *, read=protected_read):
    """Hash BEFORE compile/exec. No sys.path import or fallback helper lookup."""
    raw_binding = read(BINDING)
    require(sha(raw_binding) == _hash(binding_sha256), "bootstrap_binding_hash")
    binding = document(raw_binding)
    _binding_shape(binding)
    require(sha(read(GUARD)) == binding["guard"]["sha256"], "bootstrap_guard_hash")
    raw = read(NATIVE)
    require(sha(raw) == binding["native"]["sha256"], "bootstrap_native_hash")
    module = types.ModuleType("_pinned_manager_successor_native")
    module.__file__ = NATIVE
    exec(compile(raw, NATIVE, "exec"), module.__dict__)
    require(
        read(BINDING) == raw_binding
        and read(NATIVE) == raw
        and sha(read(GUARD)) == binding["guard"]["sha256"],
        "bootstrap_changed",
    )
    return module


def _alarm(_signum, _frame):
    raise Refusal("guard_deadline")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("operation", choices=("check", "launch"))
    parser.add_argument("--binding-sha256", required=True)
    parser.add_argument("--unit", required=True, choices=(UNIT,))
    args = parser.parse_args(argv)
    previous_handler = None
    try:
        require(
            sys.platform == "linux"
            and sys.flags.isolated
            and sys.flags.no_site
            and sys.dont_write_bytecode
            and not sys.flags.optimize
            and Path(__file__) == Path(GUARD)
            and os.geteuid() == 0
            and socket.gethostname() == HOST,
            "installed_native_entry_required",
        )
        deadline = time.monotonic() + 15
        previous_handler = signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, 15)
        native = load_native(args.binding_sha256)
        result = run_guard(
            args.operation, args.binding_sha256, io=native.NativeIO(deadline), deadline=deadline
        )
        print(canonical(result).decode(), end="")
        return 0
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, Refusal) and re.fullmatch(r"[a-z_]{1,80}", str(exc))
            else "native_authority_unknown"
        )
        print(
            canonical(
                {"ok": False, "decision": "DENY", "error_code": reason, "native_acceptance": False}
            ).decode(),
            end="",
            file=sys.stderr,
        )
        return 255
    finally:
        if previous_handler is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
