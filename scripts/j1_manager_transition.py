"""Pure, source-only MCP authority proposal; never an activation permission.

The old six-profile authority remains immutable.  Readers supply exact exported
bytes under their original logical paths; no old Python authority is executed.
Only the IO consumer establishes Git blob provenance and protected-file access.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import PurePosixPath

from scripts.j1_forward_io import Refusal, canonical, document, require, sha

SCHEMA = "autostop.j1.manager-mcp-source-capsule.v1"
CONTRACT = "manager_mcp_only_preserved_warm_v1"
TARGET = "autostop-manager-mcp.service"
REVISION = "bfbe58823a11f84fef23fef291f690559d989af0"
TREE = "703692676ca782948421154ec184b9cb2bc07a85"
OLD_REVISION = "89af29082e0a6f72c5d85ae448acb6d4deec5d94"
OLD_TREE = "c16c03e349d793bea742c0288d397eceb650682e"
OLD_ATTEMPT = "d80bb431-9a85-49ce-a383-f56031511d1c"
PREP = "/opt/autostop-work-telegram-releases/preparation-20261017-98e30a50146b-recovery15"
PREP_SHA = "a1ba900e15f7048fe1e96fabfb0071937df7fbb43c1ffd6649b561f02ed8e685"
HOST = "vps26457.mnogoweb.in"
SCOPE = "production_quiet_window"
PUBLIC_GUARD = "/usr/local/libexec/autostop-j1-candidate-guard/guard.py"
PUBLIC_DUTY = "/usr/local/libexec/autostop-crm-j1-duty/restore.py"
CAPSULE_PATH = "/etc/autostop-j1-candidate-guard/forward21.json"
COMMIT_PATH = "/var/lib/autostop-crm-j1-duty/committed.json"
CACHE_POLICY = "/etc/autostop-j1-cache-view/policy.json"
PROFILES = {
    "autostop-j1.service": ("autostop_manager.j1_research", ["worker"], "writer"),
    TARGET: ("autostop_manager.mcp_server", [], "writer"),
    "autostop-manager-scheduler.service": (
        "autostop_manager.automation_daemon",
        ["daemon"],
        "writer",
    ),
    "autostop-work-telegram.service": (
        "autostop_manager.telegram_bridge",
        ["--account", "work", "daemon"],
        "client",
    ),
    "autostop-telegram.service": (
        "autostop_manager.telegram_bridge",
        ["--account", "personal", "daemon"],
        "client",
    ),
    "autostop-codex-wake.service": ("autostop_manager.telegram_wake", ["daemon"], "writer"),
}
DENIED = ["autostop-codex-start.service", "autostop-j1-browser.service"]
PRESERVED_SOURCE_FILES = (
    "autostop_manager/mcp_contract.py",
    "deploy/systemd/autostop-manager-mcp.service",
    "deploy/telegram/build-requirements-py312-linux-x86_64.lock",
    "deploy/telegram/pyaes-source-py312-linux-x86_64.lock",
    "deploy/telegram/pyaes-wheel-py312-linux-x86_64.lock",
    "deploy/telegram/requirements-py312-linux-x86_64.lock",
    "pyproject.toml",
)
PINS = {
    PUBLIC_GUARD: "c5de603f10ba2cbe6c72249c9f38e662a5e812b9d6f84442277eaea4147c2975",
    "/usr/local/libexec/autostop-j1-candidate-guard/guard.original15-d5275167987434df.py": "d5275167987434dffab4f81f0671c381c7a32f69e281fd8d4f8644715f3427c6",
    CAPSULE_PATH: "432e097d7dfd50ab795fe508c3e84317cc9c80596bb4fbc8432decd7944d33ee",
    PUBLIC_DUTY: "48cc13f80e3e3cb217a8068bf3a93882259e3b308a613ce3be436bcb5609708d",
    COMMIT_PATH: "a4ba90f7a418d37a2b9bffa4e4555b63f3e4d7961e7ff4d29a59fcf7d8667d85",
}
OLD_REFS = {
    "original_guard": {
        "path": "/usr/local/libexec/autostop-j1-candidate-guard/guard.original15-d5275167987434df.py",
        "sha256": PINS[
            "/usr/local/libexec/autostop-j1-candidate-guard/guard.original15-d5275167987434df.py"
        ],
    },
    "original_duty_controller": {
        "path": "/usr/local/libexec/autostop-crm-j1-duty/restore.original15-c7256cebae7c9dfc.py",
        "sha256": "c7256cebae7c9dfc4eee37701a462a4d128a66b937d1beabf94c76b6578d62f4",
    },
    "original_duty_intent": {
        "path": "/etc/autostop-crm-j1-duty/intent.json",
        "sha256": "29354903e662fe471135fa2d5f284dd1ba95f1dffc2efb756685357b295ed5f4",
    },
    "policy": {
        "path": "/etc/autostop-j1-candidate-guard/policy.json",
        "sha256": "54c764ca99d2c398da6659502c0a744ef3d131936aaff60a3ea95f56758206af",
    },
    "armed": {
        "path": "/var/lib/autostop-j1-candidate-guard/armed.json",
        "sha256": "815552f89e334dfd92d68ced03e7bc312df3352a38cccea8bcc1ed11ef3a3002",
    },
    "index": {
        "path": "/usr/local/libexec/autostop-j1-candidate-guard/source-index.json",
        "sha256": "1cfd4aff5ba303e00c12f8b70d9408c77de1c08313586acd49cb96a20a524e3c",
    },
    "admitted": {
        "path": PREP + "/admitted.json",
        "sha256": "a84cb133dcbce4be55b616d482789350e0058e64557bf71e60fb72ae0e4815a7",
    },
    "activation": {
        "path": PREP + "/activation-acceptance.json",
        "sha256": "b3acd57341a835cb3f0537d8d55afb0a9a1444c38520ea86feca5be4f2f7fb77",
    },
}
PROPOSAL = {
    "schema": "autostop.j1.manager-mcp-proposal.v1",
    "target": TARGET,
    "source_root": f"/opt/autostop-manager-mcp-releases/{REVISION}/source",
    "selector": "/opt/autostop-manager-mcp-releases/current",
    "guard": "/usr/local/libexec/autostop-manager-mcp-successor/v1/guard.py",
    "dropin": f"/etc/systemd/system/{TARGET}.d/zz-j1-authority-mcp.conf",
}
BINDING_FIELDS = set(
    "source_root source_identity selector manifest_sha256 module arguments access cwd cwd_identity interpreter interpreter_realpath interpreter_identity interpreter_sha256 runtime_files runtime_receipt startup_locations stable_environment_sha256 environment_files unit_files guard_dropin dropin_directories credentials ordinary_database".split()
)
PROFILE_FIELDS = BINDING_FIELDS - set(
    "runtime_files runtime_receipt startup_locations environment_files unit_files guard_dropin dropin_directories".split()
)
BASE_FIELDS = set(
    "schema manager_revision daemon_mode authority base_sha256 profiles manager_root work_root legacy_crm_revision deployment_lock deployment_lock_identity maintenance_marker desired_inbound cache warm_actors runtime_inventories desired_units libraries cache_mounts sha256".split()
)
DOCUMENT_FIELDS = set(
    "policy forward_capsule intent handoff armed source_index admitted activation duty_commit".split()
)
HEX = re.compile(r"[0-9a-f]{64}\Z")
GIT = re.compile(r"[0-9a-f]{40}\Z")


def _fields(value, fields, code):
    require(type(value) is dict and set(value) == set(fields), code)
    return value


def _digest(value):
    require(type(value) is str and HEX.fullmatch(value), "transition_digest")
    return value


def _path(value):
    require(
        type(value) is str
        and value.startswith("/")
        and "\\" not in value
        and str(PurePosixPath(value)) == value
        and ".." not in PurePosixPath(value).parts
        and not any(ord(char) < 32 for char in value),
        "transition_path",
    )
    return value


def _ref(value):
    _fields(value, {"path", "sha256"}, "transition_reference_fields")
    _path(value["path"])
    _digest(value["sha256"])
    return value


def _refs(values):
    require(type(values) is list, "transition_reference_list")
    result = {_ref(item)["path"]: item for item in values}
    require(len(result) == len(values), "transition_duplicate_reference")
    return result


class _Reader:
    def __init__(self, read):
        self.read = read
        self.seen = {}

    def raw(self, ref):
        _ref(ref)
        require(self.seen.get(ref["path"], ref) == ref, "transition_reference_conflict")
        try:
            raw = self.read(dict(ref))
        except (OSError, KeyError) as exc:
            raise Refusal("transition_required_bytes_missing") from exc
        require(type(raw) is bytes and len(raw) <= 32 * 1024**2, "transition_raw_bounds")
        require(sha(raw) == ref["sha256"], "transition_raw_hash")
        self.seen[ref["path"]] = dict(ref)
        return raw

    def doc(self, ref):
        value = document(self.raw(ref))
        require(type(value) is dict, "transition_document_object")
        return value


def _legacy_hash(value):
    return sha(
        (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    )


def _context(value, schema):
    require(
        value.get("schema") == schema
        and value.get("attempt_id") == OLD_ATTEMPT
        and value.get("scope") == SCOPE,
        "transition_predecessor_context",
    )


def _accepted(reader, capsule, policy):
    environments = {}
    _fields(capsule["profiles"], PROFILES, "transition_accepted_profiles")
    for unit, profile in capsule["profiles"].items():
        _fields(
            profile,
            {"input", "request", "unit_context_ref", "unit_context"},
            "transition_accepted_fields",
        )
        request = reader.doc(profile["request"])
        observed = reader.doc(profile["input"])
        _context(observed, "autostop.j1.input-observation.v1")
        require(
            request.get("attempt_id") == OLD_ATTEMPT
            and request.get("unit") == unit
            and observed.get("unit") == unit
            and observed.get("request_sha256") == profile["request"]["sha256"],
            "transition_accepted_request",
        )
        env = observed.get("stable_input_environment")
        require(
            type(env) is dict and all(type(k) is str and type(v) is str for k, v in env.items()),
            "transition_environment_fields",
        )
        binding = policy["bindings"][unit]
        require(
            _legacy_hash(env)
            == observed.get("stable_environment_sha256")
            == binding["stable_environment_sha256"]
            and env.get("PYTHONPATH") == binding["selector"],
            "transition_environment_binding",
        )
        context = reader.doc(profile["unit_context_ref"])
        _fields(
            context,
            "Id Names LoadState Type NotifyAccess FragmentPath SourcePath NeedDaemonReload".split(),
            "transition_unit_context_fields",
        )
        require(
            context == profile["unit_context"]
            and all(type(v) is str for v in context.values())
            and context["Id"] == context["Names"] == unit
            and context["LoadState"] == "loaded"
            and context["FragmentPath"] == "/etc/systemd/system/" + unit
            and context["SourcePath"] == ""
            and context["NeedDaemonReload"] == "no",
            "transition_unit_context",
        )
        environments[unit] = env
    return environments


def _bridge(reader, docs, capsule):
    intent, admitted, armed = (docs[key] for key in ("intent", "admitted", "armed"))
    _fields(
        intent,
        "schema scope attempt_id preparation_path preparation_sha256 closure_sha256 crm_revision manager_revision inbound_enabled maintenance_marker deploy_lock controller_sha256".split(),
        "transition_intent_fields",
    )
    _context(intent, "autostop.crm.j1-work-duty-intent.v1")
    require(
        intent["manager_revision"] == OLD_REVISION
        and intent["preparation_path"] == PREP + "/preparation.json"
        and intent["preparation_sha256"] == PREP_SHA
        and type(intent["inbound_enabled"]) is bool
        and intent["controller_sha256"] == OLD_REFS["original_duty_controller"]["sha256"],
        "transition_intent_binding",
    )
    _fields(
        admitted,
        "schema scope attempt_id preparation_sha256 handoff_sha256 acceptance_sha256".split(),
        "transition_admitted_fields",
    )
    _context(admitted, "autostop.crm.j1-admission-receipt.v1")
    _context(armed, "autostop.j1.candidate-state.v2")
    require(
        armed.get("old_writer_allowed") is False
        and armed.get("candidate_may_have_written") is True
        and armed.get("revision") == OLD_REVISION
        and armed.get("policy_sha256") == capsule["policy"]["sha256"]
        and armed.get("guard_sha256") == OLD_REFS["original_guard"]["sha256"]
        and armed.get("index_sha256") == capsule["index"]["sha256"]
        and admitted["preparation_sha256"] == PREP_SHA
        and admitted["acceptance_sha256"] == capsule["activation"]["sha256"],
        "transition_arm_admission",
    )
    preparation = reader.doc({"path": intent["preparation_path"], "sha256": PREP_SHA})
    require(
        preparation.get("schema") == "autostop.crm.j1-release-preparation.v1"
        and preparation.get("attempt_id") == OLD_ATTEMPT,
        "transition_preparation",
    )
    handoff, activation = docs["handoff"], docs["activation"]
    _context(handoff, "autostop.crm.j1-activation-handoff.v1")
    _context(activation, "autostop.crm.j1-activation-acceptance.v1")
    require(
        handoff.get("host") == HOST
        and handoff.get("preparation_sha256") == PREP_SHA
        and handoff.get("closure_sha256") == intent["closure_sha256"]
        and all(handoff.get(key) == preparation.get(key) for key in ("crm", "manager", "sources"))
        and handoff.get("work_duty_controller_sha256") == intent["controller_sha256"]
        and handoff.get("work_duty_intent") == OLD_REFS["original_duty_intent"]
        and activation.get("handoff_sha256") == admitted["handoff_sha256"],
        "transition_handoff_bridge",
    )
    require(
        handoff["manager"].get("revision") == OLD_REVISION
        and handoff["crm"].get("revision") == intent["crm_revision"],
        "transition_handoff_revisions",
    )
    evidence = activation.get("evidence")
    require(
        type(evidence) is dict
        and evidence.get("policy") == capsule["policy"]
        and evidence.get("state") == capsule["armed"],
        "transition_activation_bridge",
    )
    require(
        _ref(evidence["quiet_window"])["sha256"] == armed.get("prerequisite_sha256"),
        "transition_quiet_bridge",
    )
    reader.doc(evidence["quiet_window"])
    expected_commit = {
        "schema": "autostop.crm.j1-work-duty-committed.v1",
        "scope": SCOPE,
        "attempt_id": OLD_ATTEMPT,
        "intent_sha256": OLD_REFS["original_duty_intent"]["sha256"],
        "preparation_sha256": PREP_SHA,
        "handoff_sha256": admitted["handoff_sha256"],
        "acceptance_sha256": admitted["acceptance_sha256"],
        "policy_sha256": capsule["policy"]["sha256"],
        "crm_revision": intent["crm_revision"],
        "inbound_enabled": intent["inbound_enabled"],
    }
    require(docs["duty_commit"] == expected_commit, "transition_duty_commit")
    closure = reader.doc(
        {"path": PREP + "/closure-acceptance.json", "sha256": intent["closure_sha256"]}
    )
    lifecycle = reader.doc(closure["lifecycle"])
    require(
        closure.get("daemon_mode") == lifecycle.get("daemon_mode") == "accepted_warm",
        "transition_warm_mode",
    )
    warm = reader.doc(lifecycle["daemon_identity_receipt"])
    require(
        warm.get("actual_cache_read_only") is True and warm.get("host_cache_read_only") is True,
        "transition_warm_cache",
    )
    reader.doc(warm["cache_view_state"])
    chain_refs = [
        {"path": intent["preparation_path"], "sha256": PREP_SHA},
        {"path": PREP + "/handoff.json", "sha256": admitted["handoff_sha256"]},
        {"path": PREP + "/closure-acceptance.json", "sha256": intent["closure_sha256"]},
        closure["lifecycle"],
        lifecycle["daemon_identity_receipt"],
        warm["cache_view_state"],
    ]
    return warm, chain_refs


def _baseline(value, policy, reader, warm, chain_refs):
    _fields(value, BASE_FIELDS, "transition_baseline_fields")
    require(
        value["schema"] == "autostop.crm.j1-forward-native-baseline.v1"
        and value["manager_revision"] == OLD_REVISION
        and value["daemon_mode"] == "accepted_warm",
        "transition_baseline_scope",
    )
    require(
        sha(canonical({k: v for k, v in value.items() if k != "sha256"})) == value["sha256"],
        "transition_baseline_digest",
    )
    base = {key: value[key] for key in ("manager_revision", "daemon_mode", "authority")}
    require(sha(canonical(base)) == value["base_sha256"], "transition_baseline_authority_digest")
    refs = _refs(value["authority"])
    for ref in refs.values():
        reader.raw(ref)
    for path, digest in PINS.items():
        require(refs.get(path) == {"path": path, "sha256": digest}, "transition_predecessor_pin")
    expected_refs = {path: {"path": path, "sha256": digest} for path, digest in PINS.items()}
    for ref in [*OLD_REFS.values(), *chain_refs]:
        expected_refs[ref["path"]] = ref
    require(CACHE_POLICY in refs, "transition_cache_policy_reference")
    expected_refs[CACHE_POLICY] = refs[CACHE_POLICY]
    require(
        refs == expected_refs
        and value["authority"] == sorted(refs.values(), key=lambda item: item["path"]),
        "transition_authority_inventory",
    )
    _fields(value["profiles"], PROFILES, "transition_baseline_profiles")
    for unit, binding in policy["bindings"].items():
        expected = {key: binding[key] for key in PROFILE_FIELDS}
        expected["binding_sha256"] = sha(canonical(binding))
        require(value["profiles"][unit] == expected, "transition_profile_changed")
    require(
        value["manager_root"] == policy["bindings"]["autostop-j1.service"]["source_root"]
        and value["work_root"]
        == policy["bindings"]["autostop-work-telegram.service"]["source_root"],
        "transition_source_groups",
    )
    require(
        type(value["warm_actors"]) is list
        and value["warm_actors"]
        and type(warm.get("actors")) is list,
        "transition_warm_actors",
    )
    observed = {item["pid"]: item for item in value["warm_actors"]}
    accepted = {item["pid"]: item for item in warm["actors"]}
    require(
        len(observed) == len(value["warm_actors"]) == len(accepted) == len(warm["actors"])
        and set(observed) == set(accepted),
        "transition_warm_actor_set",
    )
    for pid, actor in observed.items():
        require(
            type(pid) is int
            and pid > 0
            and actor.get("uid") == 0
            and all(
                actor.get(key) == accepted[pid].get(key)
                for key in ("pid", "start_ticks", "argv_sha256", "cgroup", "exe", "mount_namespace")
            ),
            "transition_warm_actor_changed",
        )
    cache = value["cache"]
    _fields(
        cache,
        "policy native_proof database_identity parent_identity pid1 self state".split(),
        "transition_cache_fields",
    )
    require(
        cache["policy"] == refs.get(CACHE_POLICY) and cache["state"] == warm["cache_view_state"],
        "transition_cache_refs",
    )
    cache_policy = reader.doc(cache["policy"])
    reader.raw(cache["native_proof"])
    require(
        cache_policy.get("schema") == "autostop.j1.cache-view-policy.v1"
        and cache_policy.get("host") == HOST
        and cache["database_identity"] == cache_policy.get("database_identity")
        and cache["parent_identity"] == cache_policy.get("cache_identity")
        and cache["native_proof"]
        == {
            "path": cache_policy.get("native_proof_path"),
            "sha256": cache_policy.get("native_proof_sha256"),
        }
        and cache["pid1"].get("mode") == cache["self"].get("mode") == "ro",
        "transition_cache_preserved",
    )
    _fields(value["desired_units"], {*PROFILES, *DENIED}, "transition_desired_units")
    for unit, state in value["desired_units"].items():
        _fields(state, {"active", "enabled"}, "transition_unit_state_fields")
        require(
            all(type(v) is bool for v in state.values())
            and (state["active"] if unit in PROFILES else not state["active"]),
            "transition_unit_state",
        )
    for key in ("libraries", "cache_mounts"):
        _fields(value[key], PROFILES, "transition_runtime_profiles")


def _overlap(first, second):
    a, b = PurePosixPath(first), PurePosixPath(second)
    return a == b or a in b.parents or b in a.parents


def _impact(policy, handoff, authority, proposal):
    paths = [proposal[key] for key in ("source_root", "selector", "guard", "dropin")]
    require(
        all(not _overlap(a, b) for n, a in enumerate(paths) for b in paths[n + 1 :]),
        "transition_proposal_collision",
    )
    preserved = list(_refs(handoff["files"])) + list(authority)
    target_directory = str(PurePosixPath(proposal["dropin"]).parent)
    for unit, binding in policy["bindings"].items():
        preserved += list(_refs(binding["unit_files"]))
        preserved += list(_refs(binding["runtime_files"]))
        preserved += [
            binding[key]
            for key in (
                "source_root",
                "selector",
                "cwd",
                "interpreter",
                "interpreter_realpath",
                "runtime_receipt",
            )
        ]
        require(
            type(binding["environment_files"]) is list
            and type(binding["startup_locations"]) is list,
            "transition_dependency_inventory",
        )
        for record in binding["environment_files"]:
            _fields(record, {"path", "sha256"}, "transition_environment_file_fields")
            preserved.append(_path(record["path"]))
            if record["sha256"] is not None:
                _digest(record["sha256"])
        for record in binding["startup_locations"]:
            require(
                type(record) is dict and record.get("kind") in {"absent", "zip", "directory"},
                "transition_startup_location",
            )
            # These complete directories include absent import hooks too.
            preserved.append(_path(record["path"]))
        require(type(binding["dropin_directories"]) is list, "transition_dropin_directories")
        directories = set()
        for record in binding["dropin_directories"]:
            _fields(record, {"path", "files"}, "transition_dropin_fields")
            folder = _path(record["path"])
            require(folder not in directories, "transition_duplicate_directory")
            directories.add(folder)
            files = record["files"]
            require(files is None or type(files) is dict, "transition_dropin_files")
            if files is not None:
                for name, digest in files.items():
                    require(
                        type(name) is str
                        and PurePosixPath(name).name == name
                        and name.endswith(".conf"),
                        "transition_dropin_name",
                    )
                    _digest(digest)
                    preserved.append(folder + "/" + name)
            if _overlap(folder, proposal["dropin"]):
                require(
                    unit == TARGET and folder == target_directory and files is not None,
                    "transition_cross_profile_directory",
                )
        require(
            binding["guard_dropin"] == f"/etc/systemd/system/{unit}.d/99-j1-candidate-guard.conf"
            and str(PurePosixPath(binding["guard_dropin"]).parent) in directories,
            "transition_old_guard_dropin",
        )
    require(
        all(not _overlap(new, _path(old)) for new in paths for old in preserved),
        "transition_cross_pin_collision",
    )
    name = PurePosixPath(proposal["dropin"]).name
    require(
        "99-j1-candidate-guard.conf" < name < "zz-j1-forward-closed.conf"
        and name < "zz-j1-release-closed.conf",
        "transition_closure_order",
    )
    return {"affected_profiles": [TARGET], "new_paths": sorted(paths), "old_files_rewritten": []}


def validate_predecessor(predecessor, proposal, *, read):
    """Validate exact predecessor bytes and return internal (possibly private) data."""
    _fields(
        predecessor,
        {"schema", "baseline", "baseline_sha256", "documents"},
        "transition_predecessor_fields",
    )
    require(
        predecessor["schema"] == "autostop.j1.manager-predecessor.v1",
        "transition_predecessor_schema",
    )
    require(proposal == PROPOSAL and type(proposal) is dict, "transition_fixed_proposal")
    baseline, references = predecessor["baseline"], predecessor["documents"]
    require(sha(canonical(baseline)) == predecessor["baseline_sha256"], "transition_export_digest")
    _fields(references, DOCUMENT_FIELDS, "transition_document_fields")
    reader = _Reader(read)
    expected = {
        "forward_capsule": {"path": CAPSULE_PATH, "sha256": PINS[CAPSULE_PATH]},
        "duty_commit": {"path": COMMIT_PATH, "sha256": PINS[COMMIT_PATH]},
        "intent": OLD_REFS["original_duty_intent"],
        "source_index": OLD_REFS["index"],
        **{key: OLD_REFS[key] for key in ("policy", "armed", "admitted", "activation")},
    }
    require(
        all(references.get(key) == ref for key, ref in expected.items()),
        "transition_exact_documents",
    )
    docs = {key: reader.doc(ref) for key, ref in references.items()}
    capsule = docs["forward_capsule"]
    _fields(
        capsule,
        {
            "schema",
            "contract",
            "attempt_id",
            "preparation_sha256",
            "revision",
            "wrapper",
            "duty_wrapper",
            *OLD_REFS,
            "profiles",
            "candidate_may_have_written",
            "old_writer_allowed",
            "runtime_acceptance",
        },
        "transition_old_capsule_fields",
    )
    require(
        capsule["schema"] == "autostop.j1.original15-forward-capsule.v2"
        and capsule["contract"] == "two-public-pins-relocation-and-condition-notify-only.v2"
        and capsule["attempt_id"] == OLD_ATTEMPT
        and capsule["preparation_sha256"] == PREP_SHA
        and capsule["revision"] == OLD_REVISION
        and capsule["candidate_may_have_written"] is True
        and capsule["old_writer_allowed"] is False
        and capsule["runtime_acceptance"] is False
        and all(capsule[key] == ref for key, ref in OLD_REFS.items()),
        "transition_old_capsule_contract",
    )
    for key, path in (("wrapper", PUBLIC_GUARD), ("duty_wrapper", PUBLIC_DUTY)):
        require(capsule[key] == {"path": path, "sha256": PINS[path]}, "transition_old_wrapper")
        reader.raw(capsule[key])
    for key in OLD_REFS:
        reader.raw(capsule[key])
    require(
        references["handoff"]
        == {"path": PREP + "/handoff.json", "sha256": docs["admitted"]["handoff_sha256"]},
        "transition_handoff_reference",
    )
    policy = docs["policy"]
    _fields(
        policy,
        "schema revision tree index_sha256 guard_sha256 host attempt_id bindings".split(),
        "transition_policy_fields",
    )
    require(
        policy["schema"] == "autostop.j1.candidate-policy.v2"
        and policy["revision"] == OLD_REVISION
        and policy["tree"] == OLD_TREE
        and policy["index_sha256"] == OLD_REFS["index"]["sha256"]
        and policy["guard_sha256"] == OLD_REFS["original_guard"]["sha256"]
        and policy["host"] == HOST
        and policy["attempt_id"] == OLD_ATTEMPT,
        "transition_policy_contract",
    )
    _fields(policy["bindings"], PROFILES, "transition_policy_profiles")
    for unit, binding in policy["bindings"].items():
        _fields(binding, BINDING_FIELDS, "transition_binding_fields")
        require(
            (binding["module"], binding["arguments"], binding["access"]) == PROFILES[unit],
            "transition_entrypoint",
        )
        records = _refs(binding["unit_files"])
        require(
            records.get(PUBLIC_GUARD)
            == {"path": PUBLIC_GUARD, "sha256": OLD_REFS["original_guard"]["sha256"]}
            and records.get(PUBLIC_DUTY)
            == {"path": PUBLIC_DUTY, "sha256": OLD_REFS["original_duty_controller"]["sha256"]},
            "transition_two_pin_relocation",
        )
    index = docs["source_index"]
    require(
        index.get("revision") == OLD_REVISION
        and index.get("tree") == OLD_TREE
        and type(index.get("files")) is list
        and len(index["files"]) == 603,
        "transition_old_source_index",
    )
    environments = _accepted(reader, capsule, policy)
    warm, chain_refs = _bridge(reader, docs, capsule)
    _baseline(baseline, policy, reader, warm, chain_refs)
    require(
        baseline["legacy_crm_revision"] == docs["intent"]["crm_revision"]
        and baseline["desired_inbound"] is docs["intent"]["inbound_enabled"]
        and baseline["deployment_lock"]
        == docs["intent"]["deploy_lock"]
        == "/opt/autostopcrm/.autostop-deploy.lock"
        and baseline["maintenance_marker"]
        == docs["intent"]["maintenance_marker"]
        == "/opt/autostopcrm/data/.agent-gateway-maintenance",
        "transition_baseline_duty",
    )
    impact = _impact(policy, docs["handoff"], _refs(baseline["authority"]), proposal)
    return {
        "policy": policy,
        "source_index": index,
        "environment": environments[TARGET],
        "impact": impact,
        "read_references": sorted(reader.seen.values(), key=lambda ref: ref["path"]),
    }


def git_tree(files):
    """Reconstruct the full Git tree: missing/extra/moved metadata cannot pass."""
    root = {}
    for record in files:
        _fields(
            record, {"path", "mode", "git_oid", "size", "sha256"}, "transition_git_entry_fields"
        )
        path = record["path"]
        require(
            type(path) is str
            and not path.startswith("/")
            and "\\" not in path
            and str(PurePosixPath(path)) == path
            and ".." not in PurePosixPath(path).parts
            and path not in {".", "REVISION", "MANIFEST.sha256", "MANIFEST.files0"}
            and not any(ord(char) < 32 for char in path),
            "transition_git_path",
        )
        require(
            record["mode"] in {"100644", "100755"}
            and type(record["git_oid"]) is str
            and GIT.fullmatch(record["git_oid"])
            and type(record["size"]) is int
            and 0 <= record["size"] <= 32 * 1024**2,
            "transition_git_entry",
        )
        _digest(record["sha256"])
        node = root
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            require(type(node) is dict, "transition_git_path_conflict")
        require(parts[-1] not in node, "transition_git_duplicate")
        node[parts[-1]] = (record["mode"], record["git_oid"])

    def tree(node):
        data = b""
        for name, value in sorted(
            node.items(),
            key=lambda item: (item[0] + ("/" if type(item[1]) is dict else "")).encode(),
        ):
            mode, oid = ("40000", tree(value)) if type(value) is dict else value
            data += mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(oid)
        return hashlib.sha1(b"tree " + str(len(data)).encode() + b"\0" + data).hexdigest()

    return tree(root)


def _ci(value, revision, *, manager):
    _fields(
        value,
        "repository revision run_id run_attempt head_branch event jobs run_url conclusion evidence_kind fresh_github_verified native_acceptance activation_authorized".split(),
        "transition_ci_fields",
    )
    repo = "UgaChavis/AutostopManager" if manager else "UgaChavis/AutostopCRM-V1"
    jobs = (
        ["quality"]
        if manager
        else ["browser-performance", "docker-runtime-assets", "quality", "static", "unit"]
    )
    require(
        value["repository"] == repo
        and value["revision"] == revision
        and value["jobs"] == jobs
        and value["conclusion"] == "success"
        and value["evidence_kind"] == "cached_offline_consistency"
        and value["fresh_github_verified"] is False
        and value["native_acceptance"] is False
        and value["activation_authorized"] is False
        and type(value["run_id"]) is int
        and value["run_id"] > 0
        and type(value["run_attempt"]) is int
        and value["run_attempt"] > 0
        and type(value["head_branch"]) is str
        and value["head_branch"]
        and value["event"] in {"push", "pull_request", "workflow_dispatch"}
        and value["run_url"] == f"https://github.com/{repo}/actions/runs/{value['run_id']}",
        "transition_ci_contract",
    )


def _candidate(candidate, controller):
    _fields(candidate, {"source", "ci"}, "transition_candidate_fields")
    source = candidate["source"]
    _fields(source, {"schema", "revision", "tree", "files"}, "transition_source_fields")
    require(
        source["schema"] == "autostop.j1.manager-git-source.v1"
        and source["revision"] == REVISION
        and source["tree"] == TREE
        and type(source["files"]) is list
        and source["files"],
        "transition_fixed_candidate",
    )
    require(
        git_tree(source["files"]) == TREE
        and [r["path"] for r in source["files"]] == sorted(r["path"] for r in source["files"]),
        "transition_full_git_tree",
    )
    require(
        "autostop_manager/mcp_server.py" in {r["path"] for r in source["files"]},
        "transition_candidate_entrypoint",
    )
    _fields(controller, {"revision", "source_sha256", "ci"}, "transition_controller_fields")
    require(
        type(controller["revision"]) is str and GIT.fullmatch(controller["revision"]),
        "transition_controller_revision",
    )
    _digest(controller["source_sha256"])
    _ci(candidate["ci"], REVISION, manager=True)
    _ci(controller["ci"], controller["revision"], manager=False)
    return source


def compile_capsule(predecessor, candidate, proposal, *, attempt_id, controller, read):
    """Compile reviewable source intent. No file writes, commands or live probes."""
    try:
        require(
            type(attempt_id) is str and str(uuid.UUID(attempt_id)) == attempt_id,
            "transition_attempt_id",
        )
        source = _candidate(candidate, controller)
        checked = validate_predecessor(predecessor, proposal, read=read)
        policy, env = checked["policy"], checked["environment"]
        binding, baseline = policy["bindings"][TARGET], predecessor["baseline"]
        after_env = {**env, "PYTHONPATH": proposal["selector"]}
        hashes = {record["path"]: record["sha256"] for record in source["files"]}
        old_files = {record["path"]: record for record in checked["source_index"]["files"]}
        new_files = {record["path"]: record for record in source["files"]}
        require(
            len(old_files) == len(checked["source_index"]["files"])
            and all(
                path in old_files
                and path in new_files
                and old_files[path]["sha256"] == new_files[path]["sha256"]
                and old_files[path]["bytes"] == new_files[path]["size"]
                for path in PRESERVED_SOURCE_FILES
            ),
            "transition_dependency_or_mcp_surface_changed",
        )
        hashes["REVISION"] = sha((REVISION + "\n").encode())
        manifest = "".join(hashes[path] + "  ./" + path + "\n" for path in sorted(hashes)).encode()
        result = {
            "schema": SCHEMA,
            "contract": CONTRACT,
            "attempt_id": attempt_id,
            "controller": controller,
            "candidate": {
                "revision": REVISION,
                "tree": TREE,
                "source_index_sha256": sha(canonical(source)),
                "ci": candidate["ci"],
            },
            "predecessor": {
                "baseline_sha256": predecessor["baseline_sha256"],
                "authority_base_sha256": baseline["base_sha256"],
                "references": checked["read_references"],
            },
            "target": {
                "unit": TARGET,
                "before_binding_sha256": sha(canonical(binding)),
                "manager_revision": REVISION,
                "source_root": proposal["source_root"],
                "selector": proposal["selector"],
                "manifest_sha256": sha(manifest),
                "cwd": binding["cwd"],
                "guard": proposal["guard"],
                "dropin": proposal["dropin"],
                "environment": {
                    "before_sha256": binding["stable_environment_sha256"],
                    "after_sha256": _legacy_hash(after_env),
                    "PYTHONPATH": {"before": binding["selector"], "after": proposal["selector"]},
                    "launch_pythonpath": proposal["source_root"],
                },
            },
            "preserved": {
                "profiles": {
                    unit: sha(canonical(value))
                    for unit, value in sorted(policy["bindings"].items())
                    if unit != TARGET
                },
                "warm_actors_sha256": sha(canonical(baseline["warm_actors"])),
                "cache_sha256": sha(canonical(baseline["cache"])),
                "duty_commit": predecessor["documents"]["duty_commit"],
                "denied_units": DENIED,
                "source_contract_files": {path: hashes[path] for path in PRESERVED_SOURCE_FILES},
                "runtime_observation_sha256": sha(
                    canonical(
                        {
                            key: baseline[key]
                            for key in (
                                "runtime_inventories",
                                "libraries",
                                "cache_mounts",
                                "desired_units",
                            )
                        }
                    )
                ),
            },
            "impact": checked["impact"],
            "required_later": [
                "reviewed_successor_guard",
                "native_runtime_binding",
                "fresh_preserved_authority",
                "owned_quiet_window",
                "store_drain",
                "native_acceptance",
            ],
            "native_acceptance": False,
            "activation_authorized": False,
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        # Detach returned data from caller-owned mutable dictionaries.
        return document(canonical({**result, "sha256": sha(canonical(result))}))
    except (KeyError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise Refusal("transition_input_shape") from exc
    except ValueError as exc:
        if isinstance(exc, Refusal):
            raise
        raise Refusal("transition_input_value") from exc


def verify_capsule(capsule, predecessor, candidate, proposal, *, controller, read):
    """Recompile from actual inputs; a recomputed capsule self-hash is insufficient."""
    require(type(capsule) is dict, "transition_capsule_object")
    expected = compile_capsule(
        predecessor,
        candidate,
        proposal,
        attempt_id=capsule.get("attempt_id"),
        controller=controller,
        read=read,
    )
    require(
        type(capsule.get("sha256")) is str and canonical(capsule) == canonical(expected),
        "transition_capsule_rederivation",
    )
    return expected
