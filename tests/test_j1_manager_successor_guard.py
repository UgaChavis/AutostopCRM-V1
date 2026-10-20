"""Synthetic protected authority only. These fixtures are not native permits."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import socket
import stat
import subprocess
import time
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from scripts import j1_manager_successor_guard as guard

ATTEMPT = "a4c6259b-e39b-4dc9-97d9-70b2eaf80e73"
BOOT = "c98894a5-d7e9-475c-b43e-a04e876df623"


class Dispatched(BaseException):
    """Synthetic exec never starts a process."""


class SyntheticAuthority:
    """All bytes and pins here are invented; scoped patches cannot become production pins."""

    def __init__(self):
        self.raw, self.calls, self.markers = {}, [], set()
        self.locked = False
        self.fail = None
        self.on_read = None
        self.observed = {
            "host": guard.HOST,
            "boot_id": BOOT,
            "cgroup": "/system.slice/" + guard.UNIT,
            "uid": 0,
            "gid": 0,
            "groups": [],
            "cwd": "/old/state",
            "environment": {"PYTHONPATH": guard.SELECTOR, "SYNTHETIC_SECRET": "never-return"},
        }
        self.pins = {path: self.add(path, b"synthetic authority")["sha256"] for path in guard.PINS}
        self.bindings, profiles = {}, {}
        for number, unit in enumerate(sorted(guard.UNITS), 1):
            root, selector = f"/old/source/{number}", f"/old/current/{number}"
            env = {**self.observed["environment"], "PYTHONPATH": selector}
            runtime = self.add(f"/old/runtime/{number}.json", {"schema": "synthetic-old-runtime"})
            profile = {
                "source_root": root,
                "source_identity": [1, number],
                "selector": selector,
                "manifest_sha256": "a" * 64,
                "module": "autostop_manager.mcp_server",
                "arguments": [],
                "access": "writer",
                "cwd": "/old/state",
                "cwd_identity": [2, 3],
                "interpreter": "/old/python",
                "interpreter_realpath": "/old/python3.12",
                "interpreter_identity": [2, 4],
                "interpreter_sha256": "b" * 64,
                "runtime_files": [runtime],
                "runtime_receipt": runtime["path"],
                "startup_locations": [{"path": root, "kind": "directory", "files": {}}],
                "stable_environment_sha256": guard._legacy_hash(env),
                "environment_files": [],
                "unit_files": [],
                "guard_dropin": f"/etc/systemd/system/{unit}.d/99-j1-candidate-guard.conf",
                "dropin_directories": [],
                "credentials": [0, 0, []],
                "ordinary_database": {},
            }
            self.bindings[unit] = profile
            request = self.add(f"/synthetic/{unit}/request", {"attempt_id": ATTEMPT, "unit": unit})
            observed = self.add(
                f"/synthetic/{unit}/input",
                {
                    "schema": "autostop.j1.input-observation.v1",
                    "scope": "production_quiet_window",
                    "attempt_id": ATTEMPT,
                    "unit": unit,
                    "request_sha256": request["sha256"],
                    "stable_input_environment": env,
                    "stable_environment_sha256": guard._legacy_hash(env),
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
            profiles[unit] = {
                "input": observed,
                "request": request,
                "unit_context_ref": self.add(f"/synthetic/{unit}/context", context),
                "unit_context": context,
            }
        self.policy = {"revision": guard.OLD_REVISION, "bindings": self.bindings}
        policy_ref = self.pin(guard.POLICY, self.policy)
        index_ref = self.pin(guard.OLD_INDEX, {"revision": guard.OLD_REVISION, "files": []})
        old_state = self.pin(
            guard.OLD_STATE,
            {
                "revision": guard.OLD_REVISION,
                "old_writer_allowed": False,
                "candidate_may_have_written": True,
            },
        )
        native_proof = self.add("/synthetic/cache-native", b"synthetic native cache evidence")
        cache_policy = self.add(
            "/etc/autostop-j1-cache-view/policy.json",
            {
                "schema": "autostop.j1.cache-view-policy.v1",
                "host": guard.HOST,
                "database_identity": [3, 4],
                "cache_identity": [3, 5],
                "native_proof_path": native_proof["path"],
                "native_proof_sha256": native_proof["sha256"],
            },
        )
        cache_state = self.add(
            "/synthetic/cache-state", {"boot_id": BOOT, "policy_sha256": cache_policy["sha256"]}
        )
        actor = {
            "pid": 91,
            "start_ticks": 48,
            "argv_sha256": "e" * 64,
            "cgroup": "/warm",
            "exe": "/old/codex",
            "mount_namespace": "mnt:[42]",
        }
        warm_ref = self.add(
            "/synthetic/warm",
            {
                "actual_cache_read_only": True,
                "host_cache_read_only": True,
                "actors": [actor],
                "boot_id": BOOT,
                "cache_view_state": cache_state,
            },
        )
        lifecycle = self.add(
            "/synthetic/lifecycle",
            {"daemon_mode": "accepted_warm", "daemon_identity_receipt": warm_ref},
        )
        closure = self.add(
            "/synthetic/prep/closure-acceptance.json",
            {"daemon_mode": "accepted_warm", "lifecycle": lifecycle},
        )
        prep = self.add("/synthetic/prep/preparation.json", {"synthetic": True})
        handoff = self.add("/synthetic/prep/handoff.json", {"synthetic": True})
        intent = self.add(
            "/synthetic/intent",
            {
                "preparation_path": prep["path"],
                "preparation_sha256": prep["sha256"],
                "closure_sha256": closure["sha256"],
            },
        )
        admitted = self.add("/synthetic/admitted", {"handoff_sha256": handoff["sha256"]})
        quiet = self.add("/synthetic/quiet", {"synthetic": True})
        activation = self.add("/synthetic/activation", {"evidence": {"quiet_window": quiet}})
        forward = {
            "attempt_id": ATTEMPT,
            "profiles": profiles,
            "original_duty_intent": intent,
            "original_guard": self.ref(guard.OLD_GUARD),
            "original_duty_controller": self.ref(guard.OLD_DUTY),
            "policy": policy_ref,
            "armed": old_state,
            "index": index_ref,
            "admitted": admitted,
            "activation": activation,
        }
        self.pin(guard.FORWARD, forward)
        authority = {path: self.ref(path) for path in self.pins}
        for ref in (
            intent,
            admitted,
            activation,
            prep,
            handoff,
            closure,
            lifecycle,
            warm_ref,
            cache_state,
            cache_policy,
        ):
            authority[ref["path"]] = ref
        self.baseline = {
            "schema": "autostop.crm.j1-forward-native-baseline.v1",
            "manager_revision": guard.OLD_REVISION,
            "daemon_mode": "accepted_warm",
            "authority": sorted(authority.values(), key=lambda r: r["path"]),
            "profiles": {
                unit: {"binding_sha256": guard.sha(guard.canonical(p))}
                for unit, p in self.bindings.items()
            },
            "warm_actors": [{**actor, "uid": 0}],
            "cache": {
                "policy": cache_policy,
                "native_proof": native_proof,
                "state": cache_state,
                "database_identity": [3, 4],
                "parent_identity": [3, 5],
                "pid1": {"mode": "ro"},
                "self": {"mode": "ro"},
            },
        }
        self.seal_baseline()
        refs = dict(authority)
        for ref in (quiet, native_proof):
            refs[ref["path"]] = ref
        for profile in profiles.values():
            for key in ("input", "request", "unit_context_ref"):
                refs[profile[key]["path"]] = profile[key]
        files = []
        for path, raw in (
            ("autostop_manager/__init__.py", b""),
            ("autostop_manager/mcp_server.py", b"synthetic"),
        ):
            files.append(
                {
                    "path": path,
                    "mode": "100644",
                    "git_oid": hashlib.sha1(
                        b"blob " + str(len(raw)).encode() + b"\0" + raw
                    ).hexdigest(),
                    "size": len(raw),
                    "sha256": guard.sha(raw),
                }
            )
        self.tree = guard.git_tree(files)
        self.index = {
            "schema": "autostop.j1.manager-git-source.v1",
            "revision": guard.REVISION,
            "tree": self.tree,
            "files": files,
        }
        self.add(guard.INDEX, self.index)
        old = self.bindings[guard.UNIT]
        self.capsule = {
            "schema": "autostop.j1.manager-mcp-source-capsule.v1",
            "contract": guard.CONTRACT,
            "attempt_id": ATTEMPT,
            "controller": {},
            "candidate": {
                "revision": guard.REVISION,
                "tree": self.tree,
                "source_index_sha256": guard.sha(guard.canonical(self.index)),
                "ci": {},
            },
            "predecessor": {
                "baseline_sha256": self.ref(guard.BASELINE)["sha256"],
                "authority_base_sha256": self.baseline["base_sha256"],
                "references": sorted(refs.values(), key=lambda r: r["path"]),
            },
            "target": {
                "unit": guard.UNIT,
                "before_binding_sha256": guard.sha(guard.canonical(old)),
                "manager_revision": guard.REVISION,
                "source_root": guard.SOURCE_ROOT,
                "selector": guard.SELECTOR,
                "manifest_sha256": "f" * 64,
                "cwd": old["cwd"],
                "guard": guard.GUARD,
                "dropin": guard.DROPIN,
                "environment": {
                    "before_sha256": old["stable_environment_sha256"],
                    "after_sha256": guard._legacy_hash(self.observed["environment"]),
                    "PYTHONPATH": {"before": old["selector"], "after": guard.SELECTOR},
                    "launch_pythonpath": guard.SOURCE_ROOT,
                },
            },
            "preserved": {
                "profiles": {
                    u: guard.sha(guard.canonical(p))
                    for u, p in self.bindings.items()
                    if u != guard.UNIT
                },
                "warm_actors_sha256": guard.sha(guard.canonical(self.baseline["warm_actors"])),
                "cache_sha256": guard.sha(guard.canonical(self.baseline["cache"])),
                "duty_commit": {},
                "denied_units": [],
                "source_contract_files": {},
                "runtime_observation_sha256": "a" * 64,
            },
            "impact": {},
            "required_later": [],
            "native_acceptance": False,
            "activation_authorized": False,
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        self.binding = {
            "schema": "autostop.j1.manager-mcp-native-binding.v1",
            "contract": guard.CONTRACT,
            "attempt_id": ATTEMPT,
            "host": guard.HOST,
            "unit": guard.UNIT,
            "guard": self.add(guard.GUARD, b"synthetic guard"),
            "native": self.add(guard.NATIVE, b"class NativeIO: pass\n"),
            "source_index": self.ref(guard.INDEX),
            "profile": copy.deepcopy(old),
        }
        self.store_facts = {
            "manager_revision": guard.OLD_REVISION,
            "mode": "legacy_unsupported",
            "stop_permitted": True,
            "store_workers_drained": None,
            "worker_drain_supported": False,
        }
        self.reseal()

    def add(self, path, value):
        self.raw[path] = value if type(value) is bytes else guard.canonical(value)
        return self.ref(path)

    def ref(self, path):
        return {"path": path, "sha256": guard.sha(self.raw[path])}

    def pin(self, path, value):
        ref = self.add(path, value)
        self.pins[path] = ref["sha256"]
        return ref

    def doc(self, path):
        return guard.document(self.raw[path])

    def seal_baseline(self):
        self.baseline["base_sha256"] = guard.sha(
            guard.canonical(
                {k: self.baseline[k] for k in ("manager_revision", "daemon_mode", "authority")}
            )
        )
        self.baseline["sha256"] = guard.sha(
            guard.canonical({k: v for k, v in self.baseline.items() if k != "sha256"})
        )
        self.add(guard.BASELINE, self.baseline)

    def reseal(self):
        """Simulate a future issuer resealing new bytes; frozen old pins remain unchanged."""
        self.seal_baseline()
        self.capsule["predecessor"].update(
            baseline_sha256=self.ref(guard.BASELINE)["sha256"],
            authority_base_sha256=self.baseline["base_sha256"],
        )
        self.capsule["preserved"]["warm_actors_sha256"] = guard.sha(
            guard.canonical(self.baseline["warm_actors"])
        )
        self.capsule["preserved"]["cache_sha256"] = guard.sha(
            guard.canonical(self.baseline["cache"])
        )
        self.capsule["sha256"] = guard.sha(
            guard.canonical({k: v for k, v in self.capsule.items() if k != "sha256"})
        )
        self.binding["capsule"] = self.add(guard.CAPSULE, self.capsule)
        self.binding["predecessor_baseline"] = self.ref(guard.BASELINE)
        self.binding["source_index"] = self.add(guard.INDEX, self.index)
        old, profile = self.bindings[guard.UNIT], self.binding["profile"]
        profile.update(
            manager_revision=guard.REVISION,
            source_root=guard.SOURCE_ROOT,
            source_identity=[6, 7],
            selector=guard.SELECTOR,
            manifest_sha256=self.capsule["target"]["manifest_sha256"],
            stable_environment_sha256=self.capsule["target"]["environment"]["after_sha256"],
            runtime_receipt=guard.RUNTIME,
            startup_locations=[{**r, "path": guard.SOURCE_ROOT} for r in old["startup_locations"]],
        )
        receipt = {
            "schema": "autostop.j1.manager-mcp-runtime-acceptance.v1",
            "scope": guard.CONTRACT,
            "revision": guard.REVISION,
            "source_root": guard.SOURCE_ROOT,
            "source_identity": [6, 7],
            "manager_origin": guard.SOURCE_ROOT + "/autostop_manager/__init__.py",
            "manifest_sha256": profile["manifest_sha256"],
            "interpreter": profile["interpreter"],
            "interpreter_sha256": profile["interpreter_sha256"],
            "stable_input_environment_sha256": profile["stable_environment_sha256"],
            "startup_paths": [guard.SOURCE_ROOT],
            "complete_startup_path_enumerated": True,
            "startup_hooks_reviewed": True,
            "dependency_runtime_sealed": True,
            "application_imported": False,
            "source_capsule_sha256": self.ref(guard.CAPSULE)["sha256"],
            "predecessor_runtime_sha256": self.ref(old["runtime_receipt"])["sha256"],
        }
        profile["runtime_files"] = [self.add(guard.RUNTIME, receipt)]
        self.publish_permit()

    def publish_permit(self):
        self.binding_hash = self.add(guard.BINDING, self.binding)["sha256"]
        context = {
            "contract": guard.CONTRACT,
            "attempt_id": ATTEMPT,
            "host": guard.HOST,
            "boot_id": BOOT,
            "unit": guard.UNIT,
            "binding_sha256": self.binding_hash,
            "guard_sha256": self.binding["guard"]["sha256"],
            "native_sha256": self.binding["native"]["sha256"],
            "source_capsule_sha256": self.binding["capsule"]["sha256"],
            "predecessor_baseline_sha256": self.binding["predecessor_baseline"]["sha256"],
        }
        evidence = self.add(
            "/synthetic/future-issuer-evidence", b"synthetic only; no live acceptance"
        )
        proofs = {}
        for kind, facts in guard.PROOF_FACTS.items():
            proofs[kind] = self.add(
                guard.STATE_DIR + "/proofs/" + kind + ".json",
                {
                    "schema": f"autostop.j1.manager-mcp-prestart-{kind}.v1",
                    **context,
                    "facts": self.store_facts if kind == "store_stop" else facts,
                    "evidence": [evidence],
                    "native_acceptance": False,
                    "old_writer_allowed": False,
                },
            )
        admission = self.add(
            guard.ADMISSION,
            {
                "schema": "autostop.j1.manager-mcp-prestart-admission.v1",
                **context,
                "proofs": proofs,
                "prestart_authorized": True,
                "native_acceptance": False,
                "candidate_may_have_written": True,
                "old_writer_allowed": False,
            },
        )
        self.add(
            guard.STATE,
            {
                "schema": "autostop.j1.manager-mcp-successor-state.v1",
                **context,
                "admission": admission,
                "candidate_may_have_written": True,
                "old_writer_allowed": False,
            },
        )

    def edit_proof(self, kind, change):
        admission, state = self.doc(guard.ADMISSION), self.doc(guard.STATE)
        ref = admission["proofs"][kind]
        doc = self.doc(ref["path"])
        change(doc)
        admission["proofs"][kind] = self.add(ref["path"], doc)
        state["admission"] = self.add(guard.ADMISSION, admission)
        self.add(guard.STATE, state)

    @contextmanager
    def patches(self):
        with patch.object(guard, "PINS", self.pins), patch.object(guard, "TREE", self.tree):
            yield

    @contextmanager
    def lock(self):
        if self.locked:
            raise guard.Refusal("synthetic_lock_busy")
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def read(self, path, limit=guard.LIMIT):
        if self.on_read:
            self.on_read(path)
        return self.raw[path]

    def exists(self, path):
        return path in self.markers

    def observation(self):
        return copy.deepcopy(self.observed)

    def verify(self, name):
        if not self.locked:
            raise AssertionError("authorization escaped lifecycle lock")
        self.calls.append(name)
        if self.fail == name:
            raise guard.Refusal("synthetic_native_drift")

    def verify_preserved_sources(self, *_args):
        self.verify("preserved_sources")

    def verify_preserved_authority(self, *_args):
        self.verify("preserved_authority")

    def verify_source(self, *_args):
        self.verify("source")

    def verify_runtime(self, *_args):
        self.verify("runtime")

    def verify_units(self, *_args, **_kwargs):
        self.verify("units")

    def verify_cache(self, *_args):
        self.verify("cache")

    def verify_warm(self, *_args):
        self.verify("warm")

    def dispatch(self, plan):
        self.verify("dispatch")
        self.plan = plan
        raise Dispatched

    def run(self, stage="check", **kwargs):
        with self.patches():
            return guard.run_guard(stage, self.binding_hash, io=self, **kwargs)


class SuccessorGuardTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(
            socket, "socket", side_effect=AssertionError("no network in core tests")
        ).start()
        patch.object(
            subprocess, "Popen", side_effect=AssertionError("no subprocess in core tests")
        ).start()
        patch.object(
            guard.os, "execve", side_effect=AssertionError("no real dispatch in core tests")
        ).start()
        self.fixture = SyntheticAuthority()

    def deny(self, fixture=None, pattern=None, **kwargs):
        with self.assertRaisesRegex(guard.Refusal, pattern or ".+"):
            (fixture or self.fixture).run(**kwargs)
        self.assertFalse((fixture or self.fixture).locked)

    def test_check_is_only_condition_and_never_returns_environment(self):
        result = self.fixture.run()
        self.assertEqual(result["decision"], "ALLOW_MCP_SUCCESSOR_CONDITION")
        self.assertIs(result["native_acceptance"], False)
        self.assertNotIn("never-return", json.dumps(result))
        self.assertEqual(
            self.fixture.calls,
            [
                "preserved_sources",
                "preserved_authority",
                "source",
                "runtime",
                "units",
                "cache",
                "warm",
            ],
        )

    def test_launch_preserves_cwd_and_holds_lock_through_dispatch(self):
        with self.assertRaises(Dispatched):
            self.fixture.run("launch")
        self.assertFalse(self.fixture.locked)
        self.assertEqual(self.fixture.plan["cwd"], "/old/state")
        self.assertEqual(self.fixture.plan["environment"]["PYTHONPATH"], guard.SOURCE_ROOT)
        self.assertEqual(
            self.fixture.plan["argv"], ["/old/python", "-P", "-m", "autostop_manager.mcp_server"]
        )

    def test_source_capsule_without_separate_admission_denies(self):
        del self.fixture.raw[guard.ADMISSION]
        self.deny(pattern="native_authority_unavailable")

    def test_first_start_and_same_generation_restart_do_not_need_poststart_acceptance(self):
        self.assertTrue(self.fixture.run()["ok"])
        self.assertTrue(self.fixture.run()["ok"])
        self.assertIs(self.fixture.doc(guard.ADMISSION)["native_acceptance"], False)

    def test_reboot_or_wrong_cgroup_or_credentials_deny(self):
        for key, value in (
            ("boot_id", ATTEMPT),
            ("cgroup", "/system.slice/other.service"),
            ("uid", 1),
            ("gid", True),
            ("groups", [1]),
            ("cwd", "/different"),
        ):
            with self.subTest(key=key):
                fixture = SyntheticAuthority()
                fixture.observed[key] = value
                self.deny(fixture)

    def test_closed_pending_unknown_operation_and_expired_budget(self):
        for path in (
            guard.STATE_DIR + "/closed.json",
            guard.STATE_DIR + "/arming-pending.json",
            "/var/lib/autostop-crm-j1-duty/commit-pending.json",
        ):
            self.fixture.markers = {path}
            self.deny(pattern="pending_or_closed")
        self.fixture.markers.clear()
        self.deny(stage="arm", pattern="unknown_operation")
        self.deny(deadline=time.monotonic() - 1, pattern="guard_deadline")

    def test_each_native_layer_can_refuse_even_with_all_proof_flags_true(self):
        for name in (
            "preserved_sources",
            "preserved_authority",
            "source",
            "runtime",
            "units",
            "cache",
            "warm",
        ):
            with self.subTest(layer=name):
                self.fixture.fail = name
                self.deny(pattern="synthetic_native_drift")

    def test_resealed_source_blob_cannot_keep_reviewed_tree_label(self):
        record = self.fixture.index["files"][1]
        raw = b"coordinated replacement"
        record.update(
            size=len(raw),
            sha256=guard.sha(raw),
            git_oid=hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
        )
        self.fixture.capsule["candidate"]["source_index_sha256"] = guard.sha(
            guard.canonical(self.fixture.index)
        )
        self.fixture.reseal()
        self.deny(pattern="source_tree_mismatch")

    def test_resealed_extra_environment_is_not_a_pythonpath_only_delta(self):
        self.fixture.observed["environment"]["LD_PRELOAD"] = "/synthetic/evil.so"
        self.fixture.capsule["target"]["environment"]["after_sha256"] = guard._legacy_hash(
            self.fixture.observed["environment"]
        )
        self.fixture.reseal()
        self.deny(pattern="source_environment_delta")

    def test_resealed_warm_actor_and_removed_authority_cannot_select_new_predecessor(self):
        for change in (
            lambda f: f.baseline["warm_actors"][0].update(pid=92),
            lambda f: f.baseline["warm_actors"][0].update(start_ticks=49),
            lambda f: f.baseline["authority"].pop(),
            lambda f: f.capsule["predecessor"]["references"].pop(),
        ):
            fixture = SyntheticAuthority()
            change(fixture)
            fixture.reseal()
            self.deny(fixture)

    def test_unknown_and_malformed_proofs_cannot_be_self_resealed(self):
        changes = (
            lambda d: d.update(unexpected=True),
            lambda d: d.update(native_acceptance=True),
            lambda d: d.update(boot_id=ATTEMPT),
            lambda d: d.update(evidence=[]),
            lambda d: d["facts"].update(all_writers_closed=1),
            lambda d: d.update(schema="generic-proof"),
        )
        for change in changes:
            fixture = SyntheticAuthority()
            fixture.edit_proof("closure", change)
            self.deny(fixture)

    def test_store_proof_modes_have_honest_legacy_and_modern_semantics(self):
        for facts in (
            {
                "manager_revision": guard.REVISION,
                "mode": "store_workers_drained",
                "stop_permitted": True,
                "store_workers_drained": True,
                "worker_drain_supported": True,
            },
            {
                "manager_revision": guard.OLD_REVISION,
                "mode": "no_active_process",
                "stop_permitted": True,
                "store_workers_drained": None,
                "worker_drain_supported": False,
            },
        ):
            self.fixture.store_facts = facts
            self.fixture.publish_permit()
            self.assertTrue(self.fixture.run()["ok"])
        for change in (
            {"manager_revision": guard.REVISION, "mode": "legacy_unsupported"},
            {"mode": "unknown"},
            {"store_workers_drained": True, "mode": "no_active_process"},
            {"stop_permitted": 1},
        ):
            fixture = SyntheticAuthority()
            fixture.store_facts.update(change)
            fixture.publish_permit()
            self.deny(fixture)

    def test_binding_profile_cannot_change_cwd_or_dependencies(self):
        for key, value in (
            ("cwd", "/new/cwd"),
            ("interpreter", "/new/python"),
            ("arguments", ["--unsafe"]),
            ("credentials", [0, 0, [1]]),
        ):
            fixture = SyntheticAuthority()
            fixture.binding["profile"][key] = value
            fixture.publish_permit()
            self.deny(fixture, "profile_delta")

    def test_read_recheck_rejects_mid_authorization_change(self):
        count = 0

        def mutate(path):
            nonlocal count
            if path == guard.BINDING:
                count += 1
                if count == 2:
                    self.fixture.raw[path] += b" "

        self.fixture.on_read = mutate
        self.deny(pattern="reference_changed")

    def test_final_context_and_marker_checks_happen_after_io(self):
        original = self.fixture.verify_warm

        def close(*args):
            original(*args)
            self.fixture.markers.add(guard.STATE_DIR + "/closed.json")

        self.fixture.verify_warm = close
        self.deny(pattern="pending_or_closed")

    def test_dispatcher_return_is_never_success(self):
        self.deny(
            stage="launch", dispatcher=lambda plan: None, pattern="exec_returned_without_dispatch"
        )

    def test_volatile_environment_is_bounded_and_stable_environment_stays_exact(self):
        self.fixture.observed["environment"].update(
            INVOCATION_ID="a" * 32, JOURNAL_STREAM="1:2", SYSTEMD_EXEC_PID="32"
        )
        self.assertTrue(self.fixture.run()["ok"])
        self.fixture.observed["environment"]["SYSTEMD_EXEC_PID"] = "0"
        self.deny(pattern="volatile_environment")

    def test_pin_before_sidecar_execution_and_no_import_fallback(self):
        with self.fixture.patches():
            module = guard.load_native(self.fixture.binding_hash, read=self.fixture.read)
            self.assertEqual(module.__file__, guard.NATIVE)
            self.assertTrue(hasattr(module, "NativeIO"))
            self.fixture.raw[guard.NATIVE] = b"raise AssertionError('UNVERIFIED CODE EXECUTED')"
            with self.assertRaisesRegex(guard.Refusal, "bootstrap_native_hash"):
                guard.load_native(self.fixture.binding_hash, read=self.fixture.read)

    def test_bootstrap_rechecks_files_after_verified_helper_load(self):
        reads = 0

        def read(path):
            nonlocal reads
            if path == guard.BINDING:
                reads += 1
                if reads > 1:
                    return self.fixture.raw[path] + b" "
            return self.fixture.raw[path]

        with self.fixture.patches(), self.assertRaisesRegex(guard.Refusal, "bootstrap_changed"):
            guard.load_native(self.fixture.binding_hash, read=read)

    def test_cli_refuses_uninstalled_or_nonisolated_invocation(self):
        with (
            patch.object(guard.sys, "platform", "win32"),
            patch.object(guard.sys, "stderr", io.StringIO()) as output,
        ):
            self.assertEqual(
                guard.main(["check", "--binding-sha256", "a" * 64, "--unit", guard.UNIT]), 255
            )
        self.assertIn("installed_native_entry_required", output.getvalue())

    def test_installed_cli_watchdog_covers_bootstrap_and_is_cleared_on_all_outcomes(self):
        flags = types.SimpleNamespace(isolated=1, no_site=1, optimize=0)
        argv = ["check", "--binding-sha256", self.fixture.binding_hash, "--unit", guard.UNIT]
        for failure in (None, guard.Refusal("guard_deadline"), RuntimeError("secret error text")):
            with self.subTest(failure=type(failure).__name__):
                with (
                    self.fixture.patches(),
                    patch.object(guard, "__file__", guard.GUARD),
                    patch.object(guard.sys, "platform", "linux"),
                    patch.object(guard.sys, "flags", flags),
                    patch.object(guard.sys, "dont_write_bytecode", True),
                    patch.object(guard.os, "geteuid", return_value=0, create=True),
                    patch.object(guard.socket, "gethostname", return_value=guard.HOST),
                    patch.object(guard.signal, "SIGALRM", 14, create=True),
                    patch.object(guard.signal, "ITIMER_REAL", 0, create=True),
                    patch.object(guard.signal, "signal", return_value=17) as handler,
                    patch.object(guard.signal, "setitimer", create=True) as timer,
                    patch.object(guard.sys, "stdout", io.StringIO()) as stdout,
                    patch.object(guard.sys, "stderr", io.StringIO()) as stderr,
                ):

                    def load(_digest):
                        self.assertEqual(timer.call_args.args, (0, 15))
                        if failure:
                            raise failure
                        return types.SimpleNamespace(NativeIO=lambda deadline: self.fixture)

                    with patch.object(guard, "load_native", side_effect=load):
                        self.assertEqual(guard.main(argv), 0 if failure is None else 255)
                    self.assertEqual(timer.call_args.args, (0, 0))
                    self.assertEqual(handler.call_args.args, (14, 17))
                    self.assertNotIn("secret error text", stderr.getvalue())
                    self.assertNotIn("never-return", stdout.getvalue())
        with self.assertRaisesRegex(guard.Refusal, "guard_deadline"):
            guard._alarm(14, None)

    def test_bootstrap_reader_checks_root_metadata_aliases_and_read_races(self):
        def metadata(mode=stat.S_IFREG | 0o600, **overrides):
            values = dict(
                st_mode=mode,
                st_uid=0,
                st_nlink=1,
                st_size=3,
                st_dev=1,
                st_ino=2,
                st_mtime_ns=3,
                st_ctime_ns=4,
            )
            return types.SimpleNamespace(**{**values, **overrides})

        parent = types.SimpleNamespace(lstat=lambda: metadata(stat.S_IFDIR | 0o755))
        for before, final, error in (
            (metadata(), metadata(), None),
            (metadata(st_uid=1), metadata(), "bootstrap_file"),
            (metadata(st_nlink=2), metadata(), "bootstrap_file"),
            (metadata(stat.S_IFLNK | 0o777), metadata(), "bootstrap_file"),
            (metadata(), metadata(st_ino=99), "bootstrap_file_changed"),
        ):
            path = types.SimpleNamespace(parents=[parent])
            sequence = iter((before, final))
            path.lstat = lambda: next(sequence)
            with (
                patch.object(guard, "Path", return_value=path),
                patch.object(guard.os, "O_NOFOLLOW", 0, create=True),
                patch.object(guard.os, "O_CLOEXEC", 0, create=True),
                patch.object(guard.os, "open", return_value=9),
                patch.object(guard.os, "fstat", return_value=before),
                patch.object(guard.os, "fdopen", return_value=io.BytesIO(b"abc")),
                patch.object(guard.os, "close") as close,
            ):
                if error:
                    with self.assertRaisesRegex(guard.Refusal, error):
                        guard.protected_read("/protected/file")
                else:
                    self.assertEqual(guard.protected_read("/protected/file"), b"abc")
                    close.assert_called_once_with(9)
        unsafe = types.SimpleNamespace(
            parents=[types.SimpleNamespace(lstat=lambda: metadata(stat.S_IFDIR | 0o777))]
        )
        with (
            patch.object(guard, "Path", return_value=unsafe),
            self.assertRaisesRegex(guard.Refusal, "bootstrap_directory"),
        ):
            guard.protected_read("/protected/file")

    def test_json_and_reference_boundaries_reject_ambiguous_input(self):
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1e999}', b"[]", b"\xff", b"{"):
            with self.assertRaises(guard.Refusal):
                guard.document(raw)
        for path in ("relative", "//host/file", "/a/../b", "/a//b", "/a\\b", "/a\n"):
            with self.assertRaises(guard.Refusal):
                guard._reference({"path": path, "sha256": "a" * 64})

    def test_source_inventory_disallows_duplicate_parent_conflicts_and_non_git_modes(self):
        entry = copy.deepcopy(self.fixture.index["files"][0])
        for files in (
            [entry, entry],
            [{**entry, "path": "a"}, {**entry, "path": "a/b"}],
            [{**entry, "mode": "120000"}],
            [{**entry, "size": True}],
            [{**entry, "path": "../x"}],
        ):
            with self.assertRaises(guard.Refusal):
                guard.git_tree(files)

    def test_dropin_is_exactly_mcp_and_orders_before_release_closure(self):
        text = guard.render_dropin("a" * 64).decode()
        self.assertIn("ExecCondition=\nExecCondition=+", text)
        self.assertIn(" -I -S -B " + guard.GUARD, text)
        self.assertNotIn("WorkingDirectory", text)
        self.assertLess(guard.DROPIN.rsplit("/", 1)[1], "zz-j1-forward-closure.conf")


if __name__ == "__main__":
    unittest.main()
