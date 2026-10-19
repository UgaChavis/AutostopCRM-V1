"""Synthetic authority graphs only: no native acceptance or real predecessor export."""

from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_state as state
from scripts import j1_manager_transition as transition
from scripts.j1_forward_io import Refusal, canonical, sha

ATTEMPT = "a4c6259b-e39b-4dc9-97d9-70b2eaf80e73"


def ci(revision, *, manager):
    repo = "UgaChavis/AutostopManager" if manager else "UgaChavis/AutostopCRM-V1"
    return {
        "repository": repo,
        "revision": revision,
        "run_id": 123,
        "run_attempt": 1,
        "head_branch": "synthetic",
        "event": "push",
        "run_url": f"https://github.com/{repo}/actions/runs/123",
        "jobs": ["quality"]
        if manager
        else ["browser-performance", "docker-runtime-assets", "quality", "static", "unit"],
        "conclusion": "success",
        "evidence_kind": "cached_offline_consistency",
        "fresh_github_verified": False,
        "native_acceptance": False,
        "activation_authorized": False,
    }


class TransitionFixture:
    """In-memory predecessor with scoped test-only pin replacement, never a live fixture."""

    def __init__(self, *, cross_pin=None, warm_ro=True, env_secret="private-value"):
        self.raw = {}
        self.proposal = copy.deepcopy(transition.PROPOSAL)
        self.pins = {}
        self.old_refs = copy.deepcopy(transition.OLD_REFS)

        def raw(path, value):
            encoded = value if type(value) is bytes else canonical(value)
            self.raw[path] = encoded
            return {"path": path, "sha256": sha(encoded)}

        self.add = raw
        for path in transition.PINS:
            if path not in {transition.CAPSULE_PATH, transition.COMMIT_PATH}:
                self.pins[path] = raw(path, ("synthetic " + path).encode())["sha256"]
        for name in ("original_guard", "original_duty_controller"):
            path = self.old_refs[name]["path"]
            self.old_refs[name] = raw(path, self.raw.get(path, ("synthetic " + path).encode()))
        index = {
            "revision": transition.OLD_REVISION,
            "tree": transition.OLD_TREE,
            "files": [
                {"path": f"fixture/{n}.txt", "bytes": 0, "sha256": sha(b"")}
                for n in range(603 - len(transition.PRESERVED_SOURCE_FILES))
            ]
            + [
                {"path": path, "bytes": 0, "sha256": sha(b"")}
                for path in transition.PRESERVED_SOURCE_FILES
            ],
        }
        self.old_refs["index"] = raw(self.old_refs["index"]["path"], index)
        profiles, bindings = {}, {}
        for number, (unit, (module, args, access)) in enumerate(transition.PROFILES.items(), 1):
            root = "/old/" + unit
            selector = root + "-current"
            env = {"PYTHONPATH": selector, "SYNTHETIC_SECRET": env_secret}
            binding = {
                "source_root": root,
                "source_identity": [1, number],
                "selector": selector,
                "manifest_sha256": "1" * 64,
                "module": module,
                "arguments": args,
                "access": access,
                "cwd": "/old/state/" + unit,
                "cwd_identity": [2, number],
                "interpreter": "/old/runtime/python3",
                "interpreter_realpath": "/old/runtime/python3.12",
                "interpreter_identity": [3, 1],
                "interpreter_sha256": "2" * 64,
                "runtime_files": [],
                "runtime_receipt": "/old/runtime/receipt.json",
                "startup_locations": [],
                "stable_environment_sha256": transition._legacy_hash(env),
                "environment_files": [],
                "unit_files": [
                    {
                        "path": transition.PUBLIC_GUARD,
                        "sha256": self.old_refs["original_guard"]["sha256"],
                    },
                    {
                        "path": transition.PUBLIC_DUTY,
                        "sha256": self.old_refs["original_duty_controller"]["sha256"],
                    },
                ],
                "guard_dropin": f"/etc/systemd/system/{unit}.d/99-j1-candidate-guard.conf",
                "dropin_directories": [{"path": f"/etc/systemd/system/{unit}.d", "files": {}}],
                "credentials": {"uid": 0, "gid": 0},
                "ordinary_database": "denied",
            }
            if cross_pin == "file" and unit == "autostop-j1.service":
                binding["unit_files"].append({"path": self.proposal["guard"], "sha256": "d" * 64})
            if cross_pin == "directory" and unit == "autostop-j1.service":
                binding["dropin_directories"].append(
                    {"path": f"/etc/systemd/system/{transition.TARGET}.d", "files": {}}
                )
            if cross_pin == "ancestor" and unit == "autostop-j1.service":
                binding["unit_files"].append(
                    {"path": "/opt/autostop-manager-mcp-releases", "sha256": "d" * 64}
                )
            if cross_pin == "runtime" and unit == "autostop-j1.service":
                binding["runtime_files"].append(
                    {"path": self.proposal["guard"], "sha256": "d" * 64}
                )
            if cross_pin == "environment_absent" and unit == "autostop-j1.service":
                binding["environment_files"].append(
                    {"path": self.proposal["selector"], "sha256": None}
                )
            if cross_pin == "startup_absent" and unit == "autostop-j1.service":
                binding["startup_locations"].append(
                    {"path": self.proposal["source_root"], "kind": "absent"}
                )
            bindings[unit] = binding
            request = raw(
                f"/proofs/{unit}/request.json", {"attempt_id": transition.OLD_ATTEMPT, "unit": unit}
            )
            observed = raw(
                f"/proofs/{unit}/input.json",
                {
                    "schema": "autostop.j1.input-observation.v1",
                    "scope": transition.SCOPE,
                    "attempt_id": transition.OLD_ATTEMPT,
                    "unit": unit,
                    "request_sha256": request["sha256"],
                    "stable_input_environment": env,
                    "stable_environment_sha256": binding["stable_environment_sha256"],
                },
            )
            context = {
                "Id": unit,
                "Names": unit,
                "LoadState": "loaded",
                "Type": "simple",
                "NotifyAccess": "none",
                "FragmentPath": "/etc/systemd/system/" + unit,
                "SourcePath": "",
                "NeedDaemonReload": "no",
            }
            context_ref = raw(f"/proofs/{unit}/context.json", context)
            profiles[unit] = {
                "input": observed,
                "request": request,
                "unit_context_ref": context_ref,
                "unit_context": context,
            }
        self.policy = {
            "schema": "autostop.j1.candidate-policy.v2",
            "revision": transition.OLD_REVISION,
            "tree": transition.OLD_TREE,
            "index_sha256": self.old_refs["index"]["sha256"],
            "guard_sha256": self.old_refs["original_guard"]["sha256"],
            "host": transition.HOST,
            "attempt_id": transition.OLD_ATTEMPT,
            "bindings": bindings,
        }
        self.old_refs["policy"] = raw(self.old_refs["policy"]["path"], self.policy)
        quiet = raw("/proofs/quiet.json", {"schema": "synthetic-quiet"})
        self.old_refs["armed"] = raw(
            self.old_refs["armed"]["path"],
            {
                "schema": "autostop.j1.candidate-state.v2",
                "scope": transition.SCOPE,
                "attempt_id": transition.OLD_ATTEMPT,
                "revision": transition.OLD_REVISION,
                "policy_sha256": self.old_refs["policy"]["sha256"],
                "guard_sha256": self.old_refs["original_guard"]["sha256"],
                "index_sha256": self.old_refs["index"]["sha256"],
                "candidate_may_have_written": True,
                "old_writer_allowed": False,
                "prerequisite_sha256": quiet["sha256"],
            },
        )
        cache_state = raw("/proofs/cache-state.json", {"synthetic": True})
        warm_actor = {
            "pid": 53,
            "start_ticks": 100,
            "argv_sha256": "a" * 64,
            "cgroup": "synthetic",
            "exe": "/old/codex",
            "mount_namespace": "mnt:[42]",
        }
        warm = raw(
            "/proofs/warm.json",
            {
                "actual_cache_read_only": warm_ro,
                "host_cache_read_only": True,
                "cache_view_state": cache_state,
                "actors": [warm_actor],
            },
        )
        lifecycle = raw(
            "/proofs/lifecycle.json",
            {"daemon_mode": "accepted_warm", "daemon_identity_receipt": warm},
        )
        closure = raw(
            transition.PREP + "/closure-acceptance.json",
            {"daemon_mode": "accepted_warm", "lifecycle": lifecycle},
        )
        preparation = {
            "schema": "autostop.crm.j1-release-preparation.v1",
            "attempt_id": transition.OLD_ATTEMPT,
            "crm": {"revision": "e" * 40},
            "manager": {"revision": transition.OLD_REVISION},
            "sources": {"manager": {"path": "/old/manager"}},
        }
        prep_ref = raw(transition.PREP + "/preparation.json", preparation)
        self.prep_sha = prep_ref["sha256"]
        intent = {
            "schema": "autostop.crm.j1-work-duty-intent.v1",
            "scope": transition.SCOPE,
            "attempt_id": transition.OLD_ATTEMPT,
            "preparation_path": prep_ref["path"],
            "preparation_sha256": self.prep_sha,
            "closure_sha256": closure["sha256"],
            "crm_revision": "e" * 40,
            "manager_revision": transition.OLD_REVISION,
            "inbound_enabled": True,
            "maintenance_marker": "/opt/autostopcrm/data/.agent-gateway-maintenance",
            "deploy_lock": "/opt/autostopcrm/.autostop-deploy.lock",
            "controller_sha256": self.old_refs["original_duty_controller"]["sha256"],
        }
        self.old_refs["original_duty_intent"] = raw(
            self.old_refs["original_duty_intent"]["path"], intent
        )
        handoff_files = [{"path": "/old/kept-file", "sha256": "d" * 64}]
        if cross_pin == "handoff":
            handoff_files.append({"path": self.proposal["selector"], "sha256": "d" * 64})
        handoff = {
            "schema": "autostop.crm.j1-activation-handoff.v1",
            "scope": transition.SCOPE,
            "attempt_id": transition.OLD_ATTEMPT,
            "host": transition.HOST,
            "preparation_sha256": self.prep_sha,
            "closure_sha256": closure["sha256"],
            **{key: preparation[key] for key in ("crm", "manager", "sources")},
            "work_duty_controller_sha256": intent["controller_sha256"],
            "work_duty_intent": self.old_refs["original_duty_intent"],
            "files": handoff_files,
        }
        handoff_ref = raw(transition.PREP + "/handoff.json", handoff)
        self.old_refs["activation"] = raw(
            self.old_refs["activation"]["path"],
            {
                "schema": "autostop.crm.j1-activation-acceptance.v1",
                "scope": transition.SCOPE,
                "attempt_id": transition.OLD_ATTEMPT,
                "handoff_sha256": handoff_ref["sha256"],
                "evidence": {
                    "policy": self.old_refs["policy"],
                    "state": self.old_refs["armed"],
                    "quiet_window": quiet,
                },
            },
        )
        self.old_refs["admitted"] = raw(
            self.old_refs["admitted"]["path"],
            {
                "schema": "autostop.crm.j1-admission-receipt.v1",
                "scope": transition.SCOPE,
                "attempt_id": transition.OLD_ATTEMPT,
                "preparation_sha256": self.prep_sha,
                "handoff_sha256": handoff_ref["sha256"],
                "acceptance_sha256": self.old_refs["activation"]["sha256"],
            },
        )
        commit = {
            "schema": "autostop.crm.j1-work-duty-committed.v1",
            "scope": transition.SCOPE,
            "attempt_id": transition.OLD_ATTEMPT,
            "intent_sha256": self.old_refs["original_duty_intent"]["sha256"],
            "preparation_sha256": self.prep_sha,
            "handoff_sha256": handoff_ref["sha256"],
            "acceptance_sha256": self.old_refs["activation"]["sha256"],
            "policy_sha256": self.old_refs["policy"]["sha256"],
            "crm_revision": "e" * 40,
            "inbound_enabled": True,
        }
        commit_ref = raw(transition.COMMIT_PATH, commit)
        self.pins[transition.COMMIT_PATH] = commit_ref["sha256"]
        capsule = {
            "schema": "autostop.j1.original15-forward-capsule.v2",
            "contract": "two-public-pins-relocation-and-condition-notify-only.v2",
            "attempt_id": transition.OLD_ATTEMPT,
            "preparation_sha256": self.prep_sha,
            "revision": transition.OLD_REVISION,
            "wrapper": {
                "path": transition.PUBLIC_GUARD,
                "sha256": self.pins[transition.PUBLIC_GUARD],
            },
            "duty_wrapper": {
                "path": transition.PUBLIC_DUTY,
                "sha256": self.pins[transition.PUBLIC_DUTY],
            },
            **self.old_refs,
            "profiles": profiles,
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
            "runtime_acceptance": False,
        }
        capsule_ref = raw(transition.CAPSULE_PATH, capsule)
        self.pins[transition.CAPSULE_PATH] = capsule_ref["sha256"]
        native_proof = raw("/proofs/native-cache.json", {"synthetic": True})
        cache_policy = raw(
            transition.CACHE_POLICY,
            {
                "schema": "autostop.j1.cache-view-policy.v1",
                "host": transition.HOST,
                "database_identity": [5, 1],
                "cache_identity": [5, 2],
                "native_proof_path": native_proof["path"],
                "native_proof_sha256": native_proof["sha256"],
            },
        )
        authority = {path: {"path": path, "sha256": digest} for path, digest in self.pins.items()}
        for ref in [
            *self.old_refs.values(),
            prep_ref,
            handoff_ref,
            closure,
            lifecycle,
            warm,
            cache_state,
            cache_policy,
        ]:
            authority[ref["path"]] = ref
        base = {
            "manager_revision": transition.OLD_REVISION,
            "daemon_mode": "accepted_warm",
            "authority": sorted(authority.values(), key=lambda ref: ref["path"]),
        }
        baseline = {
            "schema": "autostop.crm.j1-forward-native-baseline.v1",
            **base,
            "base_sha256": sha(canonical(base)),
            "profiles": {
                unit: {
                    **{key: binding[key] for key in transition.PROFILE_FIELDS},
                    "binding_sha256": sha(canonical(binding)),
                }
                for unit, binding in bindings.items()
            },
            "manager_root": bindings["autostop-j1.service"]["source_root"],
            "work_root": bindings["autostop-work-telegram.service"]["source_root"],
            "legacy_crm_revision": "e" * 40,
            "deployment_lock": intent["deploy_lock"],
            "deployment_lock_identity": [4, 1],
            "maintenance_marker": intent["maintenance_marker"],
            "desired_inbound": True,
            "cache": {
                "policy": cache_policy,
                "native_proof": native_proof,
                "database_identity": [5, 1],
                "parent_identity": [5, 2],
                "pid1": {"mode": "ro"},
                "self": {"mode": "ro"},
                "state": cache_state,
            },
            "warm_actors": [{**warm_actor, "uid": 0}],
            "runtime_inventories": [],
            "desired_units": {
                unit: {"active": unit in transition.PROFILES, "enabled": True}
                for unit in [*transition.PROFILES, *transition.DENIED]
            },
            "libraries": {unit: [] for unit in transition.PROFILES},
            "cache_mounts": {unit: {} for unit in transition.PROFILES},
        }
        baseline["sha256"] = sha(canonical(baseline))
        self.predecessor = {
            "schema": "autostop.j1.manager-predecessor.v1",
            "baseline": baseline,
            "baseline_sha256": sha(canonical(baseline)),
            "documents": {
                "policy": self.old_refs["policy"],
                "forward_capsule": capsule_ref,
                "intent": self.old_refs["original_duty_intent"],
                "handoff": handoff_ref,
                "armed": self.old_refs["armed"],
                "source_index": self.old_refs["index"],
                "admitted": self.old_refs["admitted"],
                "activation": self.old_refs["activation"],
                "duty_commit": commit_ref,
            },
        }
        blob = b"# synthetic candidate\n"
        files = [
            {
                "path": "autostop_manager/mcp_server.py",
                "mode": "100644",
                "git_oid": hashlib.sha1(
                    b"blob " + str(len(blob)).encode() + b"\0" + blob
                ).hexdigest(),
                "size": len(blob),
                "sha256": sha(blob),
            }
        ]
        files += [
            {
                "path": path,
                "mode": "100644",
                "git_oid": hashlib.sha1(b"blob 0\0").hexdigest(),
                "size": 0,
                "sha256": sha(b""),
            }
            for path in transition.PRESERVED_SOURCE_FILES
        ]
        files.sort(key=lambda record: record["path"])
        self.tree = transition.git_tree(files)
        self.candidate = {
            "source": {
                "schema": "autostop.j1.manager-git-source.v1",
                "revision": transition.REVISION,
                "tree": self.tree,
                "files": files,
            },
            "ci": ci(transition.REVISION, manager=True),
        }
        self.controller = {
            "revision": "c" * 40,
            "source_sha256": "d" * 64,
            "ci": ci("c" * 40, manager=False),
        }

    def patches(self):
        stack = ExitStack()
        for name, value in (
            ("PINS", self.pins),
            ("OLD_REFS", self.old_refs),
            ("PREP_SHA", self.prep_sha),
            ("TREE", self.tree),
        ):
            stack.enter_context(patch.object(transition, name, value))
        return stack

    def read(self, ref):
        return self.raw[ref["path"]]

    def compile(self):
        return transition.compile_capsule(
            self.predecessor,
            self.candidate,
            self.proposal,
            attempt_id=ATTEMPT,
            controller=self.controller,
            read=self.read,
        )

    def reseal_baseline(self):
        baseline = self.predecessor["baseline"]
        baseline["sha256"] = sha(
            canonical({key: value for key, value in baseline.items() if key != "sha256"})
        )
        self.predecessor["baseline_sha256"] = sha(canonical(baseline))


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = TransitionFixture()
        self.enterContext(self.fixture.patches())

    def verify(self, capsule):
        f = self.fixture
        return transition.verify_capsule(
            capsule, f.predecessor, f.candidate, f.proposal, controller=f.controller, read=f.read
        )

    def test_source_capsule_preserves_five_profiles_and_cwd_without_secrets_or_permission(self):
        capsule = self.fixture.compile()
        self.assertEqual(self.verify(capsule), capsule)
        self.assertEqual(
            capsule["target"]["cwd"], self.fixture.policy["bindings"][transition.TARGET]["cwd"]
        )
        self.assertEqual(
            set(capsule["preserved"]["profiles"]), set(transition.PROFILES) - {transition.TARGET}
        )
        self.assertFalse(capsule["native_acceptance"])
        self.assertFalse(capsule["activation_authorized"])
        self.assertFalse(capsule["old_writer_allowed"])
        self.assertTrue(capsule["candidate_may_have_written"])
        self.assertNotIn("private-value", canonical(capsule).decode())
        self.assertNotIn("SYNTHETIC_SECRET", canonical(capsule).decode())
        self.assertEqual(capsule["impact"]["affected_profiles"], [transition.TARGET])
        self.assertEqual(capsule["impact"]["old_files_rewritten"], [])

    def test_recomputed_self_hash_cannot_forge_scope_flags_target_or_preserved_proof(self):
        original = self.fixture.compile()
        mutations = [
            lambda c: c.update(activation_authorized=True),
            lambda c: c.update(native_acceptance=True),
            lambda c: c.update(old_writer_allowed=True),
            lambda c: c.update(candidate_may_have_written=False),
            lambda c: c["target"].update(cwd="/new/cwd"),
            lambda c: c["target"].update(unit="autostop-j1.service"),
            lambda c: c["preserved"].update(warm_actors_sha256="f" * 64),
            lambda c: c.update(extra="unknown"),
            lambda c: c["impact"].update(affected_profiles=list(transition.PROFILES)),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                changed = copy.deepcopy(original)
                mutate(changed)
                changed["sha256"] = sha(
                    canonical({k: v for k, v in changed.items() if k != "sha256"})
                )
                with self.assertRaisesRegex(Refusal, "capsule_rederivation"):
                    self.verify(changed)

    def test_unknown_input_fields_are_not_silently_discarded(self):
        for value in (
            self.fixture.predecessor,
            self.fixture.candidate,
            self.fixture.candidate["source"],
            self.fixture.controller,
            self.fixture.proposal,
            self.fixture.candidate["ci"],
        ):
            with self.subTest(fields=list(value)):
                value["unknown"] = True
                with self.assertRaises(Refusal):
                    self.fixture.compile()
                del value["unknown"]

    def test_self_consistent_baseline_hash_cannot_change_other_profile(self):
        self.fixture.predecessor["baseline"]["profiles"]["autostop-j1.service"]["source_root"] = (
            "/forged"
        )
        self.fixture.reseal_baseline()
        with self.assertRaisesRegex(Refusal, "profile_changed"):
            self.fixture.compile()

    def test_self_consistent_baseline_hash_cannot_replace_warm_actor_or_cache_mode(self):
        for key in ("warm", "cache"):
            fixture = TransitionFixture()
            if key == "warm":
                fixture.predecessor["baseline"]["warm_actors"][0]["start_ticks"] += 1
            else:
                fixture.predecessor["baseline"]["cache"]["self"]["mode"] = "rw"
            fixture.reseal_baseline()
            with fixture.patches(), self.assertRaises(Refusal):
                fixture.compile()

    def test_export_hash_does_not_replace_actual_raw_bytes(self):
        for missing in (False, True):
            path = self.fixture.predecessor["documents"]["policy"]["path"]
            raw = self.fixture.raw[path]
            if missing:
                del self.fixture.raw[path]
            else:
                self.fixture.raw[path] = raw + b" "
            with self.assertRaisesRegex(Refusal, "required_bytes_missing|raw_hash"):
                self.fixture.compile()
            self.fixture.raw[path] = raw

    def test_read_callback_hash_is_independently_verified(self):
        f = self.fixture
        with self.assertRaisesRegex(Refusal, "raw_hash"):
            transition.compile_capsule(
                f.predecessor,
                f.candidate,
                f.proposal,
                attempt_id=ATTEMPT,
                controller=f.controller,
                read=lambda _ref: b"{}",
            )

    def test_cross_pins_and_preserved_directory_inventory_refuse_mcp_only_scope(self):
        for kind in (
            "file",
            "directory",
            "ancestor",
            "handoff",
            "runtime",
            "environment_absent",
            "startup_absent",
        ):
            fixture = TransitionFixture(cross_pin=kind)
            with (
                self.subTest(kind=kind),
                fixture.patches(),
                self.assertRaisesRegex(Refusal, "cross_pin_collision|cross_profile_directory"),
            ):
                fixture.compile()

    def test_resealed_baseline_cannot_omit_actual_old_authority_edge(self):
        baseline = self.fixture.predecessor["baseline"]
        baseline["authority"] = [
            ref
            for ref in baseline["authority"]
            if ref["path"] != self.fixture.old_refs["policy"]["path"]
        ]
        baseline["base_sha256"] = sha(
            canonical(
                {key: baseline[key] for key in ("manager_revision", "daemon_mode", "authority")}
            )
        )
        self.fixture.reseal_baseline()
        with self.assertRaisesRegex(Refusal, "authority_inventory"):
            self.fixture.compile()

    def test_false_warm_readonly_receipt_refuses_even_with_consistent_test_pins(self):
        fixture = TransitionFixture(warm_ro=False)
        with fixture.patches(), self.assertRaisesRegex(Refusal, "warm_cache"):
            fixture.compile()

    def test_candidate_full_tree_binds_file_names_modes_and_object_ids(self):
        original = copy.deepcopy(self.fixture.candidate["source"]["files"])
        for key, value in (("path", "elsewhere.py"), ("mode", "100755"), ("git_oid", "0" * 40)):
            self.fixture.candidate["source"]["files"] = copy.deepcopy(original)
            self.fixture.candidate["source"]["files"][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(Refusal, "full_git_tree"):
                self.fixture.compile()

    def test_candidate_revision_is_reviewed_exact_commit(self):
        self.fixture.candidate["source"]["revision"] = "a" * 40
        with self.assertRaisesRegex(Refusal, "fixed_candidate"):
            self.fixture.compile()

    def test_dependency_and_surface_metadata_must_match_immutable_old_index(self):
        for key, value in (("sha256", "e" * 64), ("size", 42)):
            original = copy.deepcopy(self.fixture.candidate["source"]["files"])
            record = next(
                item
                for item in self.fixture.candidate["source"]["files"]
                if item["path"] == "pyproject.toml"
            )
            record[key] = value
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(Refusal, "dependency_or_mcp_surface_changed"),
            ):
                self.fixture.compile()
            self.fixture.candidate["source"]["files"] = original

    def test_ci_must_match_exact_revision_jobs_and_offline_honesty(self):
        original = copy.deepcopy(self.fixture.candidate["ci"])
        for key, value in (
            ("revision", "f" * 40),
            ("jobs", ["unit"]),
            ("run_id", True),
            ("fresh_github_verified", True),
            ("native_acceptance", True),
            ("event", "schedule"),
            ("run_url", "https://example.test/123"),
        ):
            self.fixture.candidate["ci"] = {**original, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(Refusal, "ci_contract"):
                self.fixture.compile()

    def test_returned_capsule_cannot_mutate_inputs(self):
        before = copy.deepcopy(self.fixture.controller)
        capsule = self.fixture.compile()
        capsule["controller"]["ci"]["jobs"].append("extra")
        self.assertEqual(self.fixture.controller, before)

    def test_fixed_dropin_is_after_guard_and_before_both_closure_fences(self):
        dropin = self.fixture.proposal["dropin"].rsplit("/", 1)[-1]
        self.assertLess("99-j1-candidate-guard.conf", dropin)
        self.assertLess(dropin, "zz-j1-forward-closed.conf")
        self.assertLess(dropin, "zz-j1-release-closed.conf")
        for path in (
            "/etc/systemd/system/service.d/new.conf",
            "/etc/systemd/system/autostop-manager-mcp.service.d/zzzz.conf",
        ):
            self.fixture.proposal["dropin"] = path
            with self.assertRaisesRegex(Refusal, "fixed_proposal"):
                self.fixture.compile()

    def test_strict_json_rejects_duplicate_authority_keys_before_any_semantics(self):
        raw = b'{"schema":1,"schema":2}'
        reader = transition._Reader(lambda _ref: raw)
        with self.assertRaisesRegex(Refusal, "duplicate_json_key"):
            reader.doc({"path": "/synthetic/doc", "sha256": sha(raw)})

    def test_legacy_valid_plan_still_refuses_new_scope(self):
        base = "1" * 64
        plan = {
            "schema": state.PLAN_SCHEMA,
            "scope": state.SCOPE,
            "host": "synthetic.example",
            "attempt_id": ATTEMPT,
            "sequence": 1,
            "predecessor_sha256": base,
            "base_sha256": base,
            "candidate": {
                "revision": "2" * 40,
                "tree": "3" * 40,
                "image": "sha256:" + "4" * 64,
                "consumer_sha256": "5" * 64,
            },
            "baseline_ref": {
                "path": str(Path(tempfile.gettempdir()).resolve() / "synthetic-baseline.json"),
                "sha256": "6" * 64,
            },
            "created_at": "2026-10-19T00:00:00Z",
            "deadline_utc": "2026-10-19T00:08:00+00:00",
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        state.validate_plan(plan, base)
        plan["scope"] = transition.CONTRACT
        with self.assertRaisesRegex(Refusal, "plan_scope"):
            state.validate_plan(plan, base)


if __name__ == "__main__":
    unittest.main()
