"""Synthetic effect tests: never signal a process or control real services."""

from __future__ import annotations

import copy
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from scripts import j1_forward_closure as closure
from scripts.j1_forward_io import Refusal

ATTEMPT = "7767b188-209a-49a3-8aac-854cf2d4336f"
FREEZE_PIDS = (538285, 538487, *range(700001, 700012))
TRANSPORT_PIDS = (538314, *range(800001, 800005))


def loaded(path):
    return f"{{ path={path} ; argv[]={path} ; ignore_errors=no ; status=0 }}"


def actor(pid):
    return {
        "pid": pid,
        "start_ticks": pid + 10000,
        "exe": "/root/codex",
        "exe_sha256": "a" * 64,
        "exe_identity": [1, pid],
        "argv_sha256": "b" * 64,
        "cgroup": "0::/user.slice/accepted-warm",
        "uid": 0,
        "mount_namespace": "mnt:[12345]",
    }


class FakeRunner:
    def __init__(self, *, freeze_pids=FREEZE_PIDS, transport_pids=TRANSPORT_PIDS):
        self.freeze_pids = freeze_pids
        self.transport_pids = transport_pids
        self.events = []
        self.saved = {}
        self.scopes = {}
        self.drains = {}
        self.barriers = {}
        self.actors = {
            pid: {**actor(pid), "stopped": False} for pid in (*freeze_pids, *transport_pids)
        }
        self.procs = {
            pid: {
                "start_ticks": row["start_ticks"],
                "ppid": 1,
                "state": "S",
                "cgroup": row["cgroup"],
                "suspect": True,
            }
            for pid, row in self.actors.items()
        }
        self.procs[os.getpid()] = {
            "start_ticks": 999,
            "ppid": 1,
            "state": "R",
            "cgroup": "0::/test",
            "suspect": True,
        }
        self.stopped = False
        self.fail_put = None
        self.fail_save = False
        self.fail_scope_save = False
        self.scope_captures = 0
        self.scope_error = None
        self.fail_signal = None
        self.commit_ready = False
        self.native_peer_live = False
        self.idle_valid = True
        self.idle_ref = {"path": "synthetic-idle", "sha256": "d" * 64}
        self.idle_observed_at = closure.datetime.now(closure.UTC).isoformat()
        self.fail_thaw = None
        self.extra_after_stop = False
        self.on_signal = None
        self.bad_loaded_hook = False
        self.scan_error = False
        self.boot = "boot-1"

    def boot_id(self):
        return self.boot

    def save(self, directory, value):
        self.events.append("save")
        if self.fail_save:
            raise OSError("durability failed")
        if str(directory) in self.saved:
            raise FileExistsError()
        self.saved[str(directory)] = copy.deepcopy(value)

    def load(self, directory):
        try:
            return copy.deepcopy(self.saved[str(directory)])
        except KeyError:
            raise FileNotFoundError() from None

    def control_bindings(self, owned):
        return "c" * 64

    def capture_scope(self, native, owned):
        self.scope_captures += 1
        return {
            "schema": "autostop.j1.crm-forward-process-scope.v1",
            "attempt_id": owned["attempt_id"],
            "attempt_dir": owned["attempt_dir"],
            "consumer_sha256": owned["consumer_sha256"],
            "native_sha256": closure.io.sha(closure.io.canonical(native)),
            "boot_id": self.boot,
            "original_actors": sorted(
                copy.deepcopy(native["warm_actors"]), key=lambda row: row["pid"]
            ),
            "freeze_actors": [actor(pid) for pid in self.freeze_pids],
            "transport_actors": [actor(pid) for pid in self.transport_pids],
            "observation": {
                "roles": {
                    str(pid): "daemon"
                    if pid == 538285
                    else "proxy"
                    if pid in self.freeze_pids[1:4]
                    else "helper"
                    for pid in (*self.freeze_pids, *self.transport_pids)
                }
            },
        }

    def save_scope(self, directory, value):
        self.events.append("save_scope")
        if self.fail_scope_save:
            raise OSError("scope durability failed")
        path = str(directory / "process-scope.json")
        if path in self.scopes:
            raise FileExistsError()
        self.scopes[path] = copy.deepcopy(value)
        return {"path": path, "sha256": closure.io.sha(closure.io.canonical(value))}

    def load_scope(self, reference):
        scope = copy.deepcopy(self.scopes[reference["path"]])
        closure.io.require(
            closure.io.sha(closure.io.canonical(scope)) == reference["sha256"],
            "reference_hash_mismatch",
        )
        return scope

    def scope_actors(self, scope):
        return copy.deepcopy((scope["freeze_actors"], scope["transport_actors"]))

    def verify_scope(self, scope, *, frozen):
        self.events.append(("scope_verify", frozen))
        if self.scope_error:
            raise Refusal(self.scope_error)
        writers, transports = self.scope_actors(scope)
        for expected in writers + transports:
            actual = self.actors[expected["pid"]]
            closure._same_actor(expected, actual, full=True)
            wanted = frozen if expected in writers else False
            closure.io.require(actual["stopped"] is wanted, "scope_actor_stop_state")
        return {
            "scope_sha256": closure.io.sha(closure.io.canonical(scope)),
            "frozen": frozen,
            "freeze_count": len(writers),
            "transport_count": len(transports),
        }

    def activation_scope(self, scope, *, released=False):
        self.events.append(("activation_verify", released))
        if self.scope_error:
            raise Refusal(self.scope_error)
        closure.io.require(self.native_peer_live, "activation_native_peer_missing")
        writers, transports = self.scope_actors(scope)
        for expected in writers + transports:
            actual = self.actors[expected["pid"]]
            closure._same_actor(expected, actual, full=True)
            wanted = expected in writers and expected["pid"] != 538285 and not released
            closure.io.require(actual["stopped"] is wanted, "scope_actor_stop_state")
        return {
            "scope_sha256": closure.io.sha(closure.io.canonical(scope)),
            "auxiliary_released": released,
            "native_control_peer": {"pid": 999000},
        }

    def staging_scope(self, scope):
        self.events.append("staging_verify")
        if self.scope_error:
            raise Refusal(self.scope_error)
        closure.io.require(not self.native_peer_live, "staging_native_peer_present")
        for expected in scope["freeze_actors"] + scope["transport_actors"]:
            actual = self.actors[expected["pid"]]
            closure._same_actor(expected, actual, full=True)
            wanted = expected["pid"] in self.freeze_pids[1:]
            closure.io.require(actual["stopped"] is wanted, "scope_actor_stop_state")
        return {
            "schema": "autostop.j1.crm-forward-process-scope-staging.v1",
            "scope_sha256": closure.io.sha(closure.io.canonical(scope)),
            "auxiliary_released": False,
            "native_control_peer": None,
            "topology_sha256": closure.io.sha(closure.io.canonical(scope["observation"])),
            "freeze_actors": [
                {
                    **row,
                    "stopped": row["pid"] != 538285,
                    "thread_states": ["S" if row["pid"] == 538285 else "T"],
                }
                for row in scope["freeze_actors"]
            ],
            "transport_actors": [
                {**row, "stopped": False, "thread_states": ["S"]}
                for row in scope["transport_actors"]
            ],
        }

    def idle_observer(self):
        self.events.append("idle_observer")
        self.idle_observed_at = closure.datetime.now(closure.UTC).isoformat()
        closure.io.require(
            not self.actors[538285]["stopped"]
            and all(self.actors[pid]["stopped"] for pid in self.freeze_pids[1:]),
            "idle_callback_order",
        )
        return copy.deepcopy(self.idle_ref)

    def idle_receipt(self, owned, reference):
        closure.io.require(
            self.idle_valid and reference == self.idle_ref, "ingress_idle_not_proven"
        )
        return {
            "metadata_only": True,
            "all_loaded_and_wake_idle": True,
            "active_turns": 0,
            "loaded_count": 7,
            "observed_at": self.idle_observed_at,
            "rpc_sha256": closure.host_policy.RPC_SHA,
            "wake_sha256": closure.host_policy.WAKE_SHA,
        }

    def admission_window(self, owned):
        return closure._utc("2026-01-01T00:00:00Z"), closure._utc("2099-01-01T00:00:00Z")

    def save_drain(self, owned, value):
        self.events.append("save_drain")
        path = str(Path(owned["attempt_dir"]) / "ingress-drain.json")
        if path in self.drains:
            raise FileExistsError()
        self.drains[path] = copy.deepcopy(value)
        return {"path": path, "sha256": closure.io.sha(closure.io.canonical(value))}

    def load_drain(self, owned):
        path = str(Path(owned["attempt_dir"]) / "ingress-drain.json")
        value = self.drains.get(path)
        if value is None:
            return None, None
        return copy.deepcopy(value), {
            "path": path,
            "sha256": closure.io.sha(closure.io.canonical(value)),
        }

    def committed(self, owned, commit_ref):
        self.events.append("commit_binding")
        closure.io.require(
            self.commit_ready and commit_ref == {"commit": "owned"}, "auxiliary_commit_head"
        )

    def read_barrier(self, path):
        return self.barriers.get(str(path))

    def put_barrier(self, path):
        self.events.append(("put", str(path)))
        if str(path) == self.fail_put:
            self.fail_put = None
            raise OSError("write failed")
        if str(path) in self.barriers:
            raise FileExistsError()
        self.barriers[str(path)] = closure.DENY_BYTES

    def remove_barrier(self, path):
        self.events.append(("remove", str(path)))
        if self.barriers[str(path)] != closure.DENY_BYTES:
            raise Refusal("barrier_not_owned")
        del self.barriers[str(path)]

    def reload(self, deadline):
        self.events.append("reload")

    def stop(self, deadline, *, ownership):
        self.events.append("stop")
        self.stopped = True
        if self.extra_after_stop:
            self.procs[888] = {
                "start_ticks": 8888,
                "ppid": 1,
                "state": "R",
                "cgroup": "0::/unrelated",
                "suspect": True,
            }

    def actor(self, expected, *, full=False):
        self.events.append(("actor", expected["pid"], full))
        return copy.deepcopy(self.actors[expected["pid"]])

    def signal_actor(self, expected, stop, *, deadline=None):
        if deadline is not None:
            closure._remaining(deadline)
        closure._same_actor(expected, self.actors[expected["pid"]], full=True)
        if (expected["pid"], stop) == self.fail_signal:
            self.fail_signal = None
            raise OSError("signal failed")
        if not stop and expected["pid"] == self.fail_thaw:
            self.fail_thaw = None
            raise OSError("thaw failed")
        self.events.append(("signal", expected["pid"], stop))
        self.actors[expected["pid"]]["stopped"] = stop
        self.procs[expected["pid"]]["state"] = "T" if stop else "S"
        if self.on_signal:
            self.on_signal(expected, stop)

    def processes(self):
        return copy.deepcopy(self.procs)

    def control_processes(self, owned, processes):
        return set()

    def source_roots(self):
        return [
            {"path": name, "present": True, "dev": 1, "ino": index}
            for index, name in enumerate(closure.WRITER_ROOTS)
        ]

    def units(self, deadline):
        self.events.append("units")
        result = {}
        for unit in closure.UNITS:
            path = str(closure.SYSTEMD / (unit + ".d") / closure.DROPIN)
            result[unit] = {
                "LoadState": "loaded",
                "NeedDaemonReload": "no",
                "ExecCondition": "path=/existing/guard ; path=/usr/bin/false"
                if path in self.barriers
                else "path=/existing/guard",
                "ActiveState": "inactive" if self.stopped else "active",
                "MainPID": "0" if self.stopped else "99",
                "Job": "",
                **{hook: "" for hook in closure.HOOKS},
            }
        if self.bad_loaded_hook:
            result[closure.UNITS[0]]["ExecStop"] = "path=/dangerous-hook"
        return result

    def containers(self, deadline):
        self.events.append("containers")
        return []

    def cache_views(self, deadline):
        self.events.append("cache_views")

    def descriptor_scan(self, processes, frozen, own):
        self.events.append("fds")
        self.last_descriptor_sets = (set(frozen), set(own))
        if self.scan_error:
            raise Refusal("unexpected_source_write_descriptor")
        return {"frozen_owned_write_fds": 2, "owned_sqlite_coordination_fds": 0}


class RecoveryRunner(FakeRunner):
    def __init__(self):
        super().__init__(
            freeze_pids=closure.recovery_scope.FREEZE,
            transport_pids=closure.recovery_scope.TRANSPORTS,
        )
        self.pending_ingress = True
        self.native_peer_live = True
        for pid in self.freeze_pids:
            self.actors[pid]["stopped"] = True
            self.procs[pid]["state"] = "T"

    def capture_scope(self, native, owned):
        result = super().capture_scope(native, owned)
        result["schema"] = closure.recovery_scope.SCHEMA
        return result

    def verify_scope(self, scope, *, frozen):
        result = super().verify_scope(scope, frozen=True)
        self.events[-1] = ("scope_verify", frozen)
        if frozen:
            closure.io.require(not self.pending_ingress, "queued_proxy_ingress")
            closure.io.require(not self.native_peer_live, "native_control_peer_present")
        return {**result, "frozen": frozen}

    def stop(self, deadline):
        super().stop(deadline)
        self.native_peer_live = False

    def idle_observer(self):
        result = super().idle_observer()
        self.pending_ingress = False
        return result

    def staging_scope(self, scope):
        result = super().staging_scope(scope)
        closure.io.require(not self.pending_ingress, "queued_proxy_ingress")
        return {
            **result,
            "schema": "autostop.j1.crm-forward-process-scope-staging.v2",
            "retired_transports_absent": list(closure.recovery_scope.RETIRED),
            "disposition": closure.recovery_scope.DISPOSITION,
            "original_liveness": [
                row
                for row in result["freeze_actors"]
                if row["pid"] in closure.recovery_scope.ORIGINAL
            ],
            "retained_frozen": list(closure.recovery_scope.ORPHANS),
        }

    def activation_scope(self, scope, *, released=False):
        self.events.append(("activation_verify", released))
        if self.scope_error:
            raise Refusal(self.scope_error)
        closure.io.require(self.native_peer_live, "activation_native_peer_missing")
        closure.io.require(not self.pending_ingress, "queued_proxy_ingress")
        for expected in scope["freeze_actors"] + scope["transport_actors"]:
            actual = self.actors[expected["pid"]]
            closure._same_actor(expected, actual, full=True)
            wanted = expected["pid"] in closure.recovery_scope.ORPHANS or (
                not released and expected["pid"] in self.freeze_pids[1:]
            )
            closure.io.require(actual["stopped"] is wanted, "scope_actor_stop_state")
        return {
            "auxiliary_released": released,
            "retained_frozen": list(closure.recovery_scope.ORPHANS),
        }


class RecoveryClosureTests(unittest.TestCase):
    def setUp(self):
        self.runner = RecoveryRunner()
        runner_patch = patch.object(closure, "RUNNER", self.runner)
        runner_patch.start()
        self.addCleanup(runner_patch.stop)
        closure._CACHE.clear()
        self.directory = Path.cwd() / ATTEMPT
        native = {"warm_actors": [actor(pid) for pid in sorted(closure.ACCEPTED_PIDS)]}
        self.owned = {
            "schema": closure.SCHEMA,
            "attempt_id": ATTEMPT,
            "attempt_dir": str(self.directory),
            "boot_id": self.runner.boot_id(),
            "dropins": closure._paths(),
            "bytes_sha256": closure.io.sha(closure.DENY_BYTES),
            "warm_actors": native["warm_actors"],
            "baseline_sha256": closure.io.sha(closure.io.canonical(native)),
            "source_roots": self.runner.source_roots(),
            "consumer_sha256": "c" * 64,
        }
        scope = self.runner.capture_scope(native, self.owned)
        self.owned["freeze_actors"] = scope["freeze_actors"]
        self.owned["process_scope_ref"] = self.runner.save_scope(self.directory, scope)
        self.runner.save(self.directory, self.owned)
        for entry in self.owned["dropins"]:
            self.runner.barriers[entry["path"]] = closure.DENY_BYTES
        self.runner.events.clear()

    def close(self, *, idle_observer=None):
        return closure.close_recovery(
            self.owned,
            deadline=time.monotonic() + 30,
            idle_observer=idle_observer or self.runner.idle_observer,
        )

    def assert_frozen(self):
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in self.runner.freeze_pids))
        self.assertFalse(
            any(self.runner.actors[pid]["stopped"] for pid in self.runner.transport_pids)
        )
        self.assertFalse(
            any(
                event == ("signal", pid, False)
                for event in self.runner.events
                for pid in self.runner.freeze_pids[1:]
            )
        )

    def test_recovery_checks_full_fence_then_daemon_only_fresh_drain(self):
        original = copy.deepcopy(self.owned["warm_actors"])
        self.assertNotIn(538314, self.runner.actors)
        self.assertEqual(self.close(), self.owned)
        events = self.runner.events
        for earlier, later in (
            ("units", "stop"),
            ("stop", "fds"),
            ("fds", ("signal", 538285, False)),
            (("signal", 538285, False), "idle_observer"),
            ("idle_observer", "staging_verify"),
            ("staging_verify", "save_drain"),
            ("save_drain", ("signal", 538285, True)),
        ):
            self.assertLess(events.index(earlier), events.index(later))
        self.assertEqual(
            [event for event in events if isinstance(event, tuple) and event[0] == "signal"],
            [("signal", 538285, False), ("signal", 538285, True)],
        )
        self.assertEqual(self.owned["warm_actors"], original)
        self.assert_frozen()
        observed = closure.observe_closed(self.owned, force_full=True)
        self.assertIsNotNone(observed["ingress_drain_ref"])
        self.assertEqual(len(observed["freeze_actors"]), 13)
        self.assertEqual(len(observed["transport_actors"]), 2)
        self.assertEqual(observed["full_scan_count"], 2)

    def test_predrain_is_not_cached_or_accepted_as_quiet(self):
        self.runner.stop(time.monotonic() + 30)
        closure._recovery_preclose(self.owned, time.monotonic() + 30)
        self.assertEqual(closure._CACHE, {})
        self.assertEqual(self.runner.drains, {})
        self.assertFalse(
            any(isinstance(event, tuple) and event[0] == "signal" for event in self.runner.events)
        )
        with self.assertRaisesRegex(Refusal, "ingress_drain_missing"):
            closure.observe_closed(self.owned, force_full=True)
        with self.assertRaisesRegex(Refusal, "queued_proxy_ingress"):
            closure._observe(self.owned, force_full=True)

    def test_loaded_fence_failure_never_stops_units_or_resumes_daemon(self):
        self.runner.bad_loaded_hook = True
        with self.assertRaisesRegex(Refusal, "loaded_barrier_invalid"):
            self.close()
        self.assertNotIn("stop", self.runner.events)
        self.assertNotIn(("signal", 538285, False), self.runner.events)
        self.assert_frozen()

    def test_full_source_descriptor_failure_prevents_daemon_resume(self):
        self.runner.scan_error = True
        with self.assertRaisesRegex(Refusal, "unexpected_source_write_descriptor"):
            self.close()
        self.assertNotIn(("signal", 538285, False), self.runner.events)
        self.assert_frozen()

    def test_failures_after_resume_refreeze_all_owned_writers(self):
        for phase in ("idle", "staging", "save", "final"):
            with self.subTest(phase=phase):
                self.setUp()
                if phase == "idle":
                    self.runner.idle_valid = False
                elif phase == "staging":
                    self.runner.staging_scope = Mock(side_effect=Refusal("bad_staging"))
                elif phase == "save":
                    self.runner.save_drain = Mock(side_effect=OSError("write failed"))
                else:

                    def after_stop(expected, stop):
                        if expected["pid"] == 538285 and stop:
                            self.runner.scope_error = "changed_final_scope"

                    self.runner.on_signal = after_stop
                with self.assertRaises((Refusal, OSError)):
                    self.close()
                self.assertIn(("signal", 538285, False), self.runner.events)
                self.assert_frozen()

    def test_expired_identity_check_cannot_resume_daemon(self):
        with patch.object(closure, "_recovery_preclose", side_effect=Refusal("closure_deadline")):
            with self.assertRaisesRegex(Refusal, "closure_deadline"):
                self.close()
        self.assertNotIn(("signal", 538285, False), self.runner.events)
        self.assert_frozen()

    def test_idle_receipt_cannot_replace_zero_queue_staging_proof(self):
        with self.assertRaisesRegex(Refusal, "queued_proxy_ingress"):
            self.close(idle_observer=lambda: self.runner.idle_ref)
        self.assertNotIn("save_drain", self.runner.events)
        self.assert_frozen()

    def test_existing_drain_is_not_replayed_to_resume_recovery(self):
        self.close()
        self.runner.events.clear()
        with self.assertRaisesRegex(Refusal, "recovery_drain_already_exists"):
            self.close()
        self.assertNotIn(("signal", 538285, False), self.runner.events)
        self.assert_frozen()

    def test_release_retains_exact_orphan_proxies_and_live_transports(self):
        self.close()
        closure.reopen_native(self.owned)
        self.runner.native_peer_live = True
        self.runner.commit_ready = True
        self.runner.events.clear()
        result = closure.release_auxiliary(self.owned, {"commit": "owned"})
        self.assertEqual(result["disposition"], closure.recovery_scope.DISPOSITION)
        self.assertEqual(result["retained_frozen"], list(closure.recovery_scope.ORPHANS))
        continued = {
            event[1]
            for event in self.runner.events
            if isinstance(event, tuple) and event[0] == "signal" and event[2] is False
        }
        self.assertEqual(continued, set(closure.recovery_scope.HELPERS))
        for row in result["freeze_actors"]:
            self.assertEqual(row["stopped"], row["pid"] in closure.recovery_scope.ORPHANS)
        self.assertTrue(all(not row["stopped"] for row in result["transport_actors"]))

    def test_partial_helper_release_failure_refreezes_and_never_releases_orphans(self):
        self.close()
        closure.reopen_native(self.owned)
        self.runner.native_peer_live = True
        self.runner.commit_ready = True
        self.runner.fail_thaw = closure.recovery_scope.HELPERS[0]
        self.runner.events.clear()
        with self.assertRaisesRegex(OSError, "thaw failed"):
            closure.release_auxiliary(self.owned, {"commit": "owned"})
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in self.runner.freeze_pids))
        for pid in closure.recovery_scope.ORPHANS:
            self.assertNotIn(("signal", pid, False), self.runner.events)

    def test_recovery_drain_cannot_claim_live_retired_authority(self):
        self.close()
        path = str(self.directory / "ingress-drain.json")
        self.runner.drains[path]["staging_observation"]["original_liveness"].append(
            {**actor(538314), "stopped": False, "thread_states": ["S"]}
        )
        with self.assertRaisesRegex(Refusal, "ingress_recovery_disposition"):
            closure.observe_closed(self.owned)


class ScopeDispatchTests(unittest.TestCase):
    def test_exact_schema_routes_every_backend_to_same_module(self):
        runner = closure.LinuxRunner()
        for module in (closure.process_scope, closure.recovery_scope):
            with self.subTest(schema=module.SCHEMA):
                scope = {"schema": module.SCHEMA}
                with (
                    patch.object(module, "actor_sets", return_value=([], [])) as actors,
                    patch.object(module, "verify", return_value="verified") as verify,
                    patch.object(module, "verify_staging", return_value="staged") as stage,
                    patch.object(module, "verify_activation", return_value="active") as active,
                    patch.object(module, "verify_released", return_value="released") as released,
                    patch.object(
                        closure.io, "referenced", return_value=closure.io.canonical(scope)
                    ),
                ):
                    self.assertEqual(runner.load_scope({}), scope)
                    self.assertEqual(runner.scope_actors(scope), ([], []))
                    self.assertEqual(runner.verify_scope(scope, frozen=False), "verified")
                    self.assertEqual(runner.staging_scope(scope), "staged")
                    self.assertEqual(runner.activation_scope(scope), "active")
                    self.assertEqual(runner.activation_scope(scope, released=True), "released")
                    self.assertEqual(actors.call_count, 2)
                    verify.assert_called_once_with(scope, frozen=False)
                    stage.assert_called_once_with(scope)
                    active.assert_called_once_with(scope)
                    released.assert_called_once_with(scope)

    def test_unknown_schema_never_falls_back_to_existing_scope(self):
        runner = closure.LinuxRunner()
        scope = {"schema": closure.recovery_scope.SCHEMA + ".other"}
        for call in (
            runner.scope_actors,
            lambda value: runner.verify_scope(value, frozen=True),
            runner.staging_scope,
            runner.activation_scope,
        ):
            with self.subTest(call=call), self.assertRaisesRegex(Refusal, "closure_scope_schema"):
                call(scope)


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.runner = FakeRunner()
        self.mock_runner = patch.object(closure, "RUNNER", self.runner)
        self.mock_runner.start()
        self.addCleanup(self.mock_runner.stop)
        closure._CACHE.clear()
        self.directory = Path.cwd() / ATTEMPT
        self.baseline = {
            "attempt_id": "old-authority-attempt",
            "warm_actors": [actor(pid) for pid in sorted(closure.ACCEPTED_PIDS)],
        }

    def close(self, *, idle_observer=None):
        return closure.close_writers(
            self.baseline,
            self.directory,
            deadline=time.monotonic() + 30,
            idle_observer=idle_observer or self.runner.idle_observer,
        )

    def owned(self):
        return self.runner.saved[str(self.directory)]

    def test_prepare_saves_ownership_without_runtime_effects(self):
        result = closure.prepare_closure(self.baseline, self.directory)
        self.assertEqual(result, self.owned())
        self.assertEqual(result["attempt_id"], ATTEMPT)
        self.assertEqual(self.runner.barriers, {})
        self.assertFalse(self.runner.stopped)
        self.assertFalse(any(row["stopped"] for row in self.runner.actors.values()))
        self.assertEqual(
            [event for event in self.runner.events if not isinstance(event, tuple)],
            ["save_scope", "save"],
        )
        self.assertTrue(
            all(
                event[0] in {"actor", "scope_verify"}
                for event in self.runner.events
                if isinstance(event, tuple)
            )
        )

    def test_prepared_ownership_fences_early_drain_failure(self):
        owned = closure.prepare_closure(self.baseline, self.directory)
        result = closure.fail_closed(owned)
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["observation"])
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(self.runner.stopped)
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS))

    def test_close_reuses_prepared_record_without_republication(self):
        prepared = closure.prepare_closure(self.baseline, self.directory)
        closed = self.close()
        self.assertEqual(prepared, closed)
        self.assertEqual(self.runner.events.count("save"), 1)
        self.assertEqual(self.runner.scope_captures, 1)
        self.assertEqual(self.runner.events.count("save_scope"), 1)
        self.assertEqual(len(self.runner.barriers), 8)

    def test_v2_freezes_ingress_before_idle_then_daemon_and_preserves_live_transports(self):
        prepared = closure.prepare_closure(self.baseline, self.directory)
        closed = self.close()
        self.assertEqual(closed["schema"], "autostop.j1.crm-forward-closure.v2")
        self.assertEqual(closed["warm_actors"], self.baseline["warm_actors"])
        self.assertEqual(prepared, closed)
        effects = [
            event
            for event in self.runner.events
            if event in ("save_scope", "save", "stop")
            or isinstance(event, tuple)
            and event[0] in {"put", "signal"}
        ]
        self.assertEqual(effects[:2], ["save_scope", "save"])
        self.assertEqual(
            [event[1] for event in effects if isinstance(event, tuple) and event[0] == "signal"],
            [*FREEZE_PIDS[1:], 538285],
        )
        idle_index = self.runner.events.index("idle_observer")
        daemon_index = self.runner.events.index(("signal", 538285, True))
        self.assertLess(idle_index, self.runner.events.index("staging_verify"))
        self.assertLess(self.runner.events.index("save_drain"), daemon_index)
        self.assertTrue(
            all(
                self.runner.events.index(("signal", pid, True)) < idle_index
                for pid in FREEZE_PIDS[1:]
            )
        )
        self.assertTrue(all(not self.runner.actors[pid]["stopped"] for pid in TRANSPORT_PIDS))
        self.assertEqual(self.runner.last_descriptor_sets[0], set(FREEZE_PIDS))
        self.assertFalse(set(TRANSPORT_PIDS) & self.runner.last_descriptor_sets[1])
        observation = closure.observe_closed(closed)
        self.assertEqual(observation["scope_sha256"], closed["process_scope_ref"]["sha256"])
        self.assertEqual({row["pid"] for row in observation["freeze_actors"]}, set(FREEZE_PIDS))
        self.assertEqual(
            {row["pid"] for row in observation["transport_actors"]}, set(TRANSPORT_PIDS)
        )

    def test_scope_durability_failure_cannot_install_fence_or_publish_ownership(self):
        self.runner.fail_scope_save = True
        with self.assertRaisesRegex(OSError, "scope durability"):
            self.close()
        self.assertEqual(self.runner.saved, {})
        self.assertEqual(self.runner.barriers, {})
        self.assertFalse(self.runner.stopped)

    def test_missing_idle_callback_refuses_before_any_ownership_or_effect(self):
        with self.assertRaises(TypeError):
            closure.close_writers(self.baseline, self.directory, deadline=time.monotonic() + 30)
        with self.assertRaisesRegex(Refusal, "ingress_idle_observer_required"):
            closure.close_writers(
                self.baseline, self.directory, deadline=time.monotonic() + 30, idle_observer=None
            )
        self.assertEqual(self.runner.events, [])
        self.assertEqual(self.runner.saved, {})

    def test_idle_callback_failure_refences_daemon_first_and_never_authorizes_reopen(self):
        def refuse_idle():
            self.runner.idle_observer()
            self.runner.events.append("idle_failure")
            raise Refusal("application_still_active")

        with self.assertRaisesRegex(Refusal, "application_still_active"):
            self.close(idle_observer=refuse_idle)
        self.assertEqual(self.runner.events.count("idle_observer"), 1)
        failure_index = self.runner.events.index("idle_failure")
        first_signal = next(
            event
            for event in self.runner.events[failure_index:]
            if isinstance(event, tuple) and event[0] == "signal"
        )
        self.assertEqual(first_signal, ("signal", 538285, True))
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS))
        self.assertEqual(self.runner.drains, {})
        for action in (closure.observe_closed, closure.reopen_native):
            with self.assertRaisesRegex(Refusal, "ingress_drain_missing"):
                action(self.owned())
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertFalse(
            any(
                isinstance(event, tuple) and event[0] == "signal" and not event[2]
                for event in self.runner.events
            )
        )

    def test_kernel_queue_failure_after_idle_never_publishes_admission(self):
        def idle_then_queued():
            ref = self.runner.idle_observer()
            self.runner.scope_error = "queued_proxy_ingress"
            return ref

        with self.assertRaisesRegex(Refusal, "queued_proxy_ingress"):
            self.close(idle_observer=idle_then_queued)
        self.assertEqual(self.runner.drains, {})
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS))

    def test_admission_rejects_inexact_states_identity_fields_and_time_even_when_cached(self):
        owned = self.close()
        path = next(iter(self.runner.drains))
        pristine = copy.deepcopy(self.runner.drains[path])
        mutations = (
            lambda value: value.update(extra=True),
            lambda value: value.update(observed_at="2025-01-01T00:00:00Z"),
            lambda value: value.update(observed_at="2099-01-02T00:00:00Z"),
            lambda value: value["staging_observation"].update(extra=True),
            lambda value: value["staging_observation"]["freeze_actors"].pop(),
            lambda value: value["staging_observation"]["freeze_actors"][1].update(stopped=False),
            lambda value: value["staging_observation"]["freeze_actors"][1].update(
                thread_states=["T", "S"]
            ),
            lambda value: value["staging_observation"]["transport_actors"][0].update(stopped=True),
            lambda value: value["staging_observation"]["transport_actors"][0].update(start_ticks=1),
            lambda value: value["staging_observation"].update(native_control_peer={"pid": 99}),
        )
        for index, mutate in enumerate(mutations):
            value = copy.deepcopy(pristine)
            mutate(value)
            self.runner.drains[path] = value
            with self.subTest(index=index), self.assertRaises(Refusal):
                closure.observe_closed(owned)
        self.runner.drains[path] = pristine
        self.runner.idle_observed_at = "2099-01-01T00:00:00Z"
        with self.assertRaisesRegex(Refusal, "ingress_admission_window"):
            closure.observe_closed(owned)

    def test_pre_stopped_derived_writer_is_not_adopted(self):
        self.runner.actors[FREEZE_PIDS[-1]]["stopped"] = True
        with self.assertRaisesRegex(Refusal, "scope_actor_stop_state"):
            self.close()
        self.assertEqual(self.runner.saved, {})
        self.assertEqual(self.runner.scopes, {})
        self.assertFalse(
            any(event[0] == "signal" for event in self.runner.events if isinstance(event, tuple))
        )

    def test_mid_freeze_failure_refences_exact_stored_writers_without_transport_signal(self):
        self.runner.fail_signal = (FREEZE_PIDS[5], True)
        with self.assertRaisesRegex(OSError, "signal failed"):
            self.close()
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS))
        self.assertTrue(all(not self.runner.actors[pid]["stopped"] for pid in TRANSPORT_PIDS))
        signaled = [
            event[1]
            for event in self.runner.events
            if isinstance(event, tuple) and event[0] == "signal"
        ]
        failure_stop = [index for index, event in enumerate(self.runner.events) if event == "stop"][
            -1
        ]
        self.assertEqual(
            next(
                event[1]
                for event in self.runner.events[failure_stop:]
                if isinstance(event, tuple) and event[0] == "signal"
            ),
            538285,
        )
        self.assertEqual(set(signaled), set(FREEZE_PIDS))
        self.assertEqual(self.runner.scope_captures, 1)

    def test_same_identity_foreign_peer_refusal_cannot_be_cached_or_recaptured(self):
        owned = self.close()
        self.runner.scope_error = "scope_socket_peer_changed"
        with self.assertRaisesRegex(Refusal, "scope_socket_peer_changed"):
            closure.observe_closed(owned, force_full=True)
        result = closure.fail_closed(owned)
        self.assertIn("closure_readback_failed", result["errors"])
        self.assertIsNone(result["observation"])
        self.assertEqual(self.runner.scope_captures, 1)
        self.assertEqual(len(self.runner.barriers), 8)

    def test_cached_sample_refuses_stopped_transport_without_signaling_it(self):
        owned = self.close()
        self.runner.actors[538314]["stopped"] = True
        with self.assertRaisesRegex(Refusal, "scope_transport_stopped"):
            closure.observe_closed(owned)
        self.assertFalse(
            any(
                event[:2] == ("signal", 538314)
                for event in self.runner.events
                if isinstance(event, tuple)
            )
        )

    def test_foreign_scope_binding_refused_before_effects_even_with_matching_reference_hash(self):
        owned = closure.prepare_closure(self.baseline, self.directory)
        reference = owned["process_scope_ref"]
        scope = self.runner.scopes[reference["path"]]
        scope["consumer_sha256"] = "d" * 64
        reference["sha256"] = closure.io.sha(closure.io.canonical(scope))
        self.runner.saved[str(self.directory)] = copy.deepcopy(owned)
        with self.assertRaisesRegex(Refusal, "closure_scope_binding"):
            closure.fail_closed(owned)
        self.assertEqual(self.runner.barriers, {})

    def test_prepare_never_overwrites_existing_ownership(self):
        prepared = closure.prepare_closure(self.baseline, self.directory)
        with self.assertRaises(FileExistsError):
            closure.prepare_closure(self.baseline, self.directory)
        self.assertEqual(self.owned(), prepared)
        self.assertEqual(self.runner.barriers, {})

    def test_stale_or_foreign_prepared_record_is_refused_before_effects(self):
        prepared = closure.prepare_closure(self.baseline, self.directory)
        mutations = {
            "boot_id": "different-boot",
            "attempt_id": "b921389d-490c-4608-852b-b5b9918798e0",
            "attempt_dir": str(self.directory.parent / "other"),
            "baseline_sha256": "c" * 64,
            "source_roots": [],
            "warm_actors": [actor(pid + 1) for pid in sorted(closure.ACCEPTED_PIDS)],
        }
        for key, value in mutations.items():
            with self.subTest(field=key):
                self.runner.saved[str(self.directory)] = {**copy.deepcopy(prepared), key: value}
                with self.assertRaisesRegex(Refusal, "prepared_closure_changed"):
                    self.close()
                self.assertEqual(self.runner.barriers, {})
                self.assertFalse(self.runner.stopped)

    def test_prepared_actor_reuse_is_rechecked_before_installation(self):
        closure.prepare_closure(self.baseline, self.directory)
        self.runner.actors[538314]["start_ticks"] += 1
        with self.assertRaisesRegex(Refusal, "warm_actor_changed"):
            self.close()
        self.assertEqual(self.runner.barriers, {})
        self.assertFalse(self.runner.stopped)

    def test_barrier_appearing_after_prepare_is_not_adopted(self):
        closure.prepare_closure(self.baseline, self.directory)
        path = closure._paths()[0]["path"]
        self.runner.barriers[path] = b"foreign-owner"
        with self.assertRaisesRegex(Refusal, "barrier_already_exists"):
            self.close()
        self.assertEqual(self.runner.barriers, {path: b"foreign-owner"})
        self.assertFalse(self.runner.stopped)

    def test_close_persists_before_fixed_effects_and_preserves_warm_identity(self):
        result = self.close()
        self.assertEqual(result["attempt_id"], ATTEMPT)
        self.assertEqual(result["warm_actors"], self.baseline["warm_actors"])
        effect = [
            event
            for event in self.runner.events
            if event == "save"
            or event == "stop"
            or isinstance(event, tuple)
            and event[0] in {"put", "signal"}
        ]
        self.assertEqual(effect[0], "save")
        self.assertEqual(sum(item[0] == "put" for item in effect if isinstance(item, tuple)), 8)
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS))
        self.assertNotIn("start", self.runner.events)

    def test_failed_ownership_durability_produces_no_runtime_effect(self):
        self.runner.fail_save = True
        with self.assertRaises(OSError):
            self.close()
        self.assertEqual(self.runner.barriers, {})
        self.assertNotIn("stop", self.runner.events)

    def test_preexisting_equal_barrier_is_not_claimed(self):
        self.runner.barriers[closure._paths()[0]["path"]] = closure.DENY_BYTES
        with self.assertRaisesRegex(Refusal, "barrier_already_exists"):
            self.close()
        self.assertEqual(self.runner.saved, {})

    def test_partial_install_failure_restores_all_owned_barriers_without_thaw(self):
        self.runner.fail_put = closure._paths()[3]["path"]
        with self.assertRaises(OSError):
            self.close()
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(self.runner.stopped)
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS))
        self.assertFalse(
            any(
                e[0] == "signal" and e[2] is False
                for e in self.runner.events
                if isinstance(e, tuple)
            )
        )

    def test_reused_pid_refused_before_ownership(self):
        self.runner.actors[538314]["start_ticks"] += 1
        with self.assertRaisesRegex(Refusal, "warm_actor_changed"):
            self.close()
        self.assertEqual(self.runner.saved, {})

    def test_unknown_warm_descendant_is_never_signaled(self):
        self.runner.procs[777] = {
            "start_ticks": 100,
            "ppid": 538285,
            "state": "R",
            "cgroup": "0::/accepted",
            "suspect": True,
        }
        with self.assertRaisesRegex(Refusal, "unknown_warm_descendant"):
            self.close()
        self.assertEqual(self.runner.saved, {})
        self.assertFalse(any(e[0] == "signal" for e in self.runner.events if isinstance(e, tuple)))

    def test_unknown_writer_after_unit_stop_cannot_be_reported_closed(self):
        self.runner.extra_after_stop = True
        with self.assertRaisesRegex(Refusal, "unexpected_runtime_actor"):
            self.close()
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertFalse(
            any(
                e[0] == "signal" and e[1] == 888 for e in self.runner.events if isinstance(e, tuple)
            )
        )

    def test_descendant_fork_during_freeze_remains_unowned_and_refuses(self):
        def fork(expected, stop):
            if stop:
                self.runner.procs[777] = {
                    "start_ticks": 100,
                    "ppid": 538285,
                    "state": "R",
                    "cgroup": "0::/accepted",
                    "suspect": True,
                }

        self.runner.on_signal = fork
        with self.assertRaisesRegex(Refusal, "unknown_warm_descendant"):
            self.close()
        self.assertFalse(
            any(
                e[0] == "signal" and e[1] == 777 for e in self.runner.events if isinstance(e, tuple)
            )
        )

    def test_loaded_mutating_stop_hook_prevents_stop_command(self):
        self.runner.bad_loaded_hook = True
        with self.assertRaisesRegex(Refusal, "loaded_barrier_invalid"):
            self.close()
        self.assertNotIn("stop", self.runner.events)

    def test_fd_writer_failure_retains_fence(self):
        self.runner.scan_error = True
        with self.assertRaisesRegex(Refusal, "unexpected_source_write_descriptor"):
            self.close()
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS))

    def test_reopen_removes_only_owned_files_and_thaws_only_daemon_without_start(self):
        result = self.close()
        self.runner.barriers["/etc/unrelated.conf"] = b"unrelated"
        closure.reopen_native(result)
        self.assertEqual(self.runner.barriers, {"/etc/unrelated.conf": b"unrelated"})
        self.assertFalse(self.runner.actors[538285]["stopped"])
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS[1:]))
        self.assertTrue(all(not self.runner.actors[pid]["stopped"] for pid in TRANSPORT_PIDS))
        self.assertNotIn("start", self.runner.events)
        self.assertEqual(
            [
                event[1]
                for event in self.runner.events
                if isinstance(event, tuple) and event[0] == "signal" and not event[2]
            ],
            [538285],
        )

    def test_thaw_failure_reinstalls_barriers_and_refreezes_prior_thaw(self):
        result = self.close()
        self.runner.fail_thaw = 538285
        with self.assertRaises(OSError):
            closure.reopen_native(result)
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS))

    def test_reopen_cannot_outlive_plan_while_full_check_completes(self):
        owned = self.close()
        original_observe = closure._observe

        with patch.object(closure.time, "monotonic", return_value=100) as clock:

            def expire_after_observation(*args, **kwargs):
                observed = original_observe(*args, **kwargs)
                clock.return_value = 101.01
                return observed

            with (
                patch.object(closure, "_observe", side_effect=expire_after_observation),
                self.assertRaises(Refusal),
            ):
                closure.reopen_native(owned, deadline=101)
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS))

    def test_activation_requires_guarded_native_peer_and_twelve_owned_auxiliaries_frozen(self):
        owned = self.close()
        closure.reopen_native(owned)
        with self.assertRaisesRegex(Refusal, "activation_native_peer_missing"):
            closure.observe_activation(owned)
        self.runner.native_peer_live = True
        observed = closure.observe_activation(owned)
        self.assertEqual(observed["schema"], "autostop.j1.crm-forward-activation-observation.v2")
        self.assertEqual(observed["freeze_actors"][0]["stopped"], False)
        self.assertTrue(all(row["stopped"] for row in observed["freeze_actors"][1:]))
        self.runner.actors[FREEZE_PIDS[-1]]["stopped"] = False
        with self.assertRaisesRegex(Refusal, "scope_actor_stop_state"):
            closure.observe_activation(owned)

    def test_auxiliary_release_requires_commit_and_never_signals_daemon_or_transport(self):
        owned = self.close()
        closure.reopen_native(owned)
        self.runner.native_peer_live = True
        with self.assertRaisesRegex(Refusal, "auxiliary_commit_head"):
            closure.release_auxiliary(owned, {"commit": "owned"})
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS[1:]))
        self.runner.commit_ready = True
        before = len(self.runner.events)
        observed = closure.release_auxiliary(owned, {"commit": "owned"})
        signals = [
            event[1]
            for event in self.runner.events[before:]
            if isinstance(event, tuple) and event[0] == "signal"
        ]
        self.assertEqual(signals, list(reversed(FREEZE_PIDS[1:])))
        self.assertFalse(any(row["stopped"] for row in self.runner.actors.values()))
        self.assertTrue(observed["process_scope_observation"]["auxiliary_released"])

    def test_partial_auxiliary_release_failure_refreezes_full_recorded_cohort(self):
        owned = self.close()
        closure.reopen_native(owned)
        self.runner.native_peer_live = self.runner.commit_ready = True
        self.runner.fail_thaw = FREEZE_PIDS[-3]
        with self.assertRaisesRegex(OSError, "thaw failed"):
            closure.release_auxiliary(owned, {"commit": "owned"})
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in FREEZE_PIDS))
        self.assertTrue(all(not self.runner.actors[pid]["stopped"] for pid in TRANSPORT_PIDS))
        self.assertEqual(self.runner.scope_captures, 1)

    def test_foreign_barrier_bytes_never_overwritten(self):
        result = self.close()
        path = result["dropins"][0]["path"]
        self.runner.barriers[path] = b"foreign"
        response = closure.fail_closed(result)
        self.assertIn("barrier_restore_failed", response["errors"])
        self.assertEqual(self.runner.barriers[path], b"foreign")
        self.assertIsNone(response["observation"])

    def test_forged_receipt_cannot_select_file_or_pid(self):
        result = self.close()
        forged = copy.deepcopy(result)
        forged["dropins"][0]["path"] = "/etc/unrelated.conf"
        with self.assertRaisesRegex(Refusal, "closure_fixed_barriers"):
            closure.reopen_native(forged)
        forged = copy.deepcopy(result)
        forged["warm_actors"][0]["start_ticks"] += 1
        with self.assertRaisesRegex(Refusal, "closure_ownership_changed"):
            closure.fail_closed(forged)

    def test_lightweight_checks_detect_thaw_even_with_full_scan_cached(self):
        result = self.close()
        before = self.runner.events.count("fds")
        observed = closure.observe_closed(result)
        self.assertFalse(observed["full_scan_this_sample"])
        self.assertEqual(self.runner.events.count("fds"), before)
        self.runner.actors[538487]["stopped"] = False
        with self.assertRaisesRegex(Refusal, "warm_actor_not_frozen"):
            closure.observe_closed(result)

    def test_force_full_refreshes_heavy_scan_and_reports_count(self):
        result = self.close()
        observed = closure.observe_closed(result, force_full=True)
        self.assertEqual(observed["full_scan_count"], 2)
        self.assertTrue(observed["full_scan_this_sample"])
        self.assertNotIn("accepted", observed)

    def test_full_observation_scopes_only_its_actor_hash_pass_and_keeps_both_scope_scans(self):
        owned = self.close()
        actual_actor = self.runner.actor
        contexts = []

        def actor_in_pass(expected, *, full=False):
            contexts.append((full, getattr(closure._HASH_PASS, "cache", None)))
            return actual_actor(expected, full=full)

        with (
            patch.object(self.runner, "actor", side_effect=actor_in_pass),
            patch.object(self.runner, "verify_scope", wraps=self.runner.verify_scope) as scope,
            patch.object(self.runner, "descriptor_scan", wraps=self.runner.descriptor_scan) as fds,
        ):
            closure.observe_closed(owned, force_full=True)
        self.assertEqual(scope.call_count, 2)
        self.assertEqual(fds.call_count, 1)
        full_contexts = [context for full, context in contexts if full]
        self.assertEqual(len(full_contexts), 18)
        self.assertTrue(all(context is full_contexts[0] for context in full_contexts))
        self.assertIsNotNone(full_contexts[0])
        self.assertTrue(all(context is None for full, context in contexts if not full))
        self.assertFalse(hasattr(closure._HASH_PASS, "cache"))

    def test_activation_hashes_share_one_fresh_pass_without_cross_pass_reuse(self):
        owned = self.close()
        closure.reopen_native(owned)
        actual_actor = self.runner.actor
        contexts = []

        def actor_in_pass(expected, *, full=False):
            self.assertTrue(full)
            contexts.append(getattr(closure._HASH_PASS, "cache", None))
            return actual_actor(expected, full=full)

        with patch.object(self.runner, "actor", side_effect=actor_in_pass):
            first = closure._activation_actors(owned, released=False)
            second = closure._activation_actors(owned, released=False)
        self.assertEqual(first, second)
        self.assertEqual(len(contexts), 36)
        self.assertIsNotNone(contexts[0])
        self.assertTrue(all(context is contexts[0] for context in contexts[:18]))
        self.assertTrue(all(context is contexts[18] for context in contexts[18:]))
        self.assertIsNot(contexts[0], contexts[18])
        self.assertFalse(hasattr(closure._HASH_PASS, "cache"))

    def test_sample_is_fresh_and_distinct_without_cache_or_full_observer(self):
        owned = self.close()
        previous_cache = copy.deepcopy(closure._CACHE)
        self.runner.events.clear()
        before = time.monotonic()
        with (
            patch.object(closure, "_CACHE", Mock()),
            patch.object(self.runner, "verify_scope", side_effect=AssertionError("full scan")),
            patch.object(self.runner, "units", side_effect=AssertionError("unit scan")),
            patch.object(closure, "_executable_hash_pass", side_effect=AssertionError("hash pass")),
        ):
            observed = closure.sample_closed(owned)
        self.assertEqual(closure._CACHE, previous_cache)
        self.assertEqual(observed["schema"], "autostop.j1.crm-forward-closure-sample.v1")
        self.assertEqual(observed["process_id"], os.getpid())
        self.assertEqual(observed["closure_sha256"], closure.io.sha(closure.io.canonical(owned)))
        self.assertEqual(observed["process_scope_ref"], owned["process_scope_ref"])
        self.assertIsNotNone(observed["ingress_drain_ref"])
        self.assertEqual(len(observed["freeze_actors"]), 13)
        self.assertEqual(len(observed["transport_actors"]), 5)
        self.assertTrue(all(row["stopped"] for row in observed["freeze_actors"]))
        self.assertTrue(all(not row["stopped"] for row in observed["transport_actors"]))
        self.assertLessEqual(before, observed["sampled_started_monotonic"])
        self.assertLessEqual(observed["sampled_started_monotonic"], observed["sampled_monotonic"])
        self.assertLessEqual(observed["sampled_monotonic"], time.monotonic())
        self.assertFalse(any(key.startswith("full_scan") for key in observed))
        self.assertNotIn("process_scope_observation", observed)
        self.assertNotIn("accepted", observed)
        self.assertEqual(
            self.runner.events,
            [("actor", pid, False) for pid in (*FREEZE_PIDS, *TRANSPORT_PIDS)],
        )

    def test_sample_does_not_wait_for_full_scan_lock(self):
        owned = self.close()
        finished, errors = threading.Event(), []

        def sample():
            try:
                closure.sample_closed(owned)
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        thread = threading.Thread(target=sample)
        with closure._LOCK:
            thread.start()
            completed_while_locked = finished.wait(2)
        thread.join(5)
        self.assertTrue(completed_while_locked)
        self.assertEqual(errors, [])
        self.assertFalse(thread.is_alive())

    def test_sample_refuses_actor_drift_and_missing_admission(self):
        owned = self.close()
        changes = (
            (538285, {"stopped": False}, "warm_actor_not_frozen"),
            (538314, {"stopped": True}, "scope_transport_stopped"),
            (538487, {"start_ticks": 1}, "warm_actor_changed"),
            (538487, {"argv_sha256": "f" * 64}, "warm_actor_changed"),
            (538487, {"mount_namespace": "mnt:[other]"}, "warm_actor_changed"),
        )
        for pid, updates, error in changes:
            pristine = self.runner.actors[pid].copy()
            self.runner.actors[pid].update(updates)
            with self.subTest(updates=updates), self.assertRaisesRegex(Refusal, error):
                closure.sample_closed(owned)
            self.runner.actors[pid] = pristine
        self.runner.drains.clear()
        with self.assertRaisesRegex(Refusal, "ingress_drain_missing"):
            closure.sample_closed(owned)

    def test_sample_refuses_ownership_boot_and_scope_drift(self):
        owned = self.close()
        with patch.object(self.runner, "load", return_value={**owned, "boot_id": "other"}):
            with self.assertRaisesRegex(Refusal, "closure_ownership_changed"):
                closure.sample_closed(owned)
        self.runner.boot = "other"
        with self.assertRaisesRegex(Refusal, "closure_boot_changed"):
            closure.sample_closed(owned)
        self.runner.boot = owned["boot_id"]
        with patch.object(self.runner, "load_scope", return_value={}):
            with self.assertRaisesRegex(Refusal, "closure_scope_schema"):
                closure.sample_closed(owned)

    def test_sample_rechecks_barriers_after_actor_reads(self):
        owned = self.close()
        actual_actor = self.runner.actor

        def remove_barrier(expected, *, full=False):
            result = actual_actor(expected, full=full)
            self.runner.barriers.pop(owned["dropins"][0]["path"], None)
            return result

        with patch.object(self.runner, "actor", side_effect=remove_barrier):
            with self.assertRaisesRegex(Refusal, "barrier_changed"):
                closure.sample_closed(owned)

    def test_boot_change_blocks_old_pid_ownership(self):
        result = self.close()
        self.runner.boot = "boot-2"
        with self.assertRaisesRegex(Refusal, "closure_boot_changed"):
            closure.fail_closed(result)

    def test_concurrent_observers_share_one_scan_without_skipping_identity_checks(self):
        result = self.close()
        errors = []

        def inspect():
            try:
                closure.observe_closed(result)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=inspect) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(self.runner.events.count("fds"), 1)
        self.assertFalse(any(thread.is_alive() for thread in threads))


class ExecutableHashPassTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "executable"
        self.source.write_bytes(b"same executable bytes")
        self.path = Mock(spec=Path)
        self.path.open.side_effect = self.source.open
        self.info = SimpleNamespace(
            **{field: getattr(self.source.stat(), field) for field in closure._EXE_METADATA}
        )
        self.path.stat.return_value = self.info
        patches = (
            patch.object(closure.os, "fstat", return_value=self.info),
            patch.object(closure.os, "readlink", return_value="/fixed/executable"),
            patch.object(closure, "_stat", return_value=(100, 1, "T")),
            patch.object(closure.hashlib, "sha256", wraps=closure.hashlib.sha256),
        )
        self.fd_stat, self.readlink, self.birth, self.hash_calls = [p.start() for p in patches]
        for item in patches:
            self.addCleanup(item.stop)

    def digest(self, info=None):
        return closure._executable_sha(
            self.path,
            info or self.info,
            exe="/fixed/executable",
            pid=123,
            birth=100,
        )

    def test_pass_hashes_each_distinct_binary_once_and_next_pass_rehashes(self):
        with closure._executable_hash_pass():
            first = self.digest()
            self.assertEqual(self.digest(), first)
            other = SimpleNamespace(**vars(self.info))
            other.st_ino += 1
            self.fd_stat.return_value = other
            self.path.stat.return_value = other
            self.assertEqual(self.digest(other), first)
            self.assertEqual(self.hash_calls.call_count, 2)
        self.assertFalse(hasattr(closure._HASH_PASS, "cache"))
        with closure._executable_hash_pass():
            self.assertEqual(self.digest(other), first)
        self.assertEqual(self.hash_calls.call_count, 3)
        self.assertEqual(self.fd_stat.call_count, 8)
        self.assertEqual(self.path.stat.call_count, 4)
        self.assertEqual(self.readlink.call_count, 4)
        self.assertEqual(self.birth.call_count, 4)

    def test_no_implicit_cache_outside_a_pass(self):
        self.digest()
        self.digest()
        self.assertEqual(self.hash_calls.call_count, 2)
        self.assertFalse(hasattr(closure._HASH_PASS, "cache"))

    def test_every_metadata_field_invalidates_pass_hash(self):
        for field in closure._EXE_METADATA:
            with self.subTest(field=field), closure._executable_hash_pass():
                self.fd_stat.return_value = self.info
                self.path.stat.return_value = self.info
                self.hash_calls.reset_mock()
                self.digest()
                changed = SimpleNamespace(**vars(self.info))
                setattr(changed, field, getattr(changed, field) + 1)
                self.fd_stat.return_value = changed
                self.path.stat.return_value = changed
                self.digest(changed)
                self.assertEqual(self.hash_calls.call_count, 2)

    def test_cache_hit_refuses_changed_open_descriptor_or_path(self):
        changed = SimpleNamespace(**vars(self.info))
        changed.st_ctime_ns += 1
        for boundary in ("open", "after", "path", "link", "birth"):
            with self.subTest(boundary=boundary), closure._executable_hash_pass():
                self.fd_stat.side_effect = None
                self.path.stat.return_value = self.info
                self.readlink.return_value = "/fixed/executable"
                self.birth.return_value = (100, 1, "T")
                self.hash_calls.reset_mock()
                self.digest()
                if boundary == "open":
                    self.fd_stat.side_effect = [changed]
                elif boundary == "after":
                    self.fd_stat.side_effect = [self.info, changed]
                elif boundary == "path":
                    self.path.stat.return_value = changed
                elif boundary == "link":
                    self.readlink.return_value = "/replaced/executable"
                else:
                    self.birth.return_value = (101, 1, "T")
                with self.assertRaisesRegex(Refusal, "actor_executable_changed|actor_reused"):
                    self.digest()
                self.assertEqual(self.hash_calls.call_count, 1)

    def test_failed_hash_verification_is_not_cached_and_scope_cleans_up(self):
        changed = SimpleNamespace(**vars(self.info))
        changed.st_size += 1
        with self.assertRaisesRegex(Refusal, "actor_executable_changed"):
            with closure._executable_hash_pass():
                self.fd_stat.side_effect = [self.info, changed]
                try:
                    self.digest()
                finally:
                    self.assertEqual(closure._HASH_PASS.cache, {})
        self.assertFalse(hasattr(closure._HASH_PASS, "cache"))
        self.fd_stat.side_effect = None
        with closure._executable_hash_pass():
            self.digest()
        self.assertEqual(self.hash_calls.call_count, 2)

    def test_hash_passes_are_thread_local(self):
        barrier = threading.Barrier(2)
        caches, errors = [], []

        def read():
            try:
                with closure._executable_hash_pass():
                    self.digest()
                    caches.append(closure._HASH_PASS.cache)
                    barrier.wait(timeout=3)
                    self.digest()
                if hasattr(closure._HASH_PASS, "cache"):
                    raise AssertionError("thread cache survived pass")
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=read) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertIsNot(caches[0], caches[1])
        self.assertEqual(self.hash_calls.call_count, 2)


class LinuxBackendTests(unittest.TestCase):
    def test_idle_receipt_requires_exact_pinned_metadata_and_plan_window(self):
        directory = Path.cwd() / ATTEMPT
        ref = {"path": str(directory / ("ingress-idle-" + "a" * 32 + ".json")), "sha256": "b" * 64}
        value = {
            "observed_at": "2026-10-19T00:02:00Z",
            "loaded_count": 7,
            "active_turns": 0,
            "all_loaded_and_wake_idle": True,
            "metadata_only": True,
            "rpc_sha256": closure.host_policy.RPC_SHA,
            "wake_sha256": closure.host_policy.WAKE_SHA,
        }
        runner = closure.LinuxRunner()
        runner.admission_window = Mock(
            return_value=(
                closure._utc("2026-10-19T00:00:00Z"),
                closure._utc("2026-10-19T00:10:00Z"),
            )
        )
        with patch.object(
            closure.io, "referenced", side_effect=lambda _ref: closure.io.canonical(value)
        ):
            self.assertEqual(runner.idle_receipt({"attempt_dir": str(directory)}, ref), value)
            for key, bad in (
                ("metadata_only", False),
                ("all_loaded_and_wake_idle", False),
                ("loaded_count", True),
                ("loaded_count", -1),
                ("active_turns", False),
                ("active_turns", 1),
                ("rpc_sha256", "c" * 64),
                ("wake_sha256", "c" * 64),
                ("observed_at", "2026-10-19T00:11:00Z"),
                ("observed_at", "2026-10-19T00:02:00+01:00"),
            ):
                original = value[key]
                value[key] = bad
                with self.subTest(key=key, bad=bad), self.assertRaises(Refusal):
                    runner.idle_receipt({"attempt_dir": str(directory)}, ref)
                value[key] = original
            value["pending_rpc_count"] = 0
            with self.assertRaisesRegex(Refusal, "ingress_idle_not_proven"):
                runner.idle_receipt({"attempt_dir": str(directory)}, ref)

    def units_fixture(
        self,
        *,
        missing=closure.HOOKS,
        values=None,
        trigger=None,
        trigger_raw=b"ActiveState=inactive\nJob=\n",
        typed=None,
        jobs=None,
        extra=b"",
    ):
        runner = closure.LinuxRunner()
        fields = {
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "SubState": "dead",
            "NeedDaemonReload": "no",
            "MainPID": "0",
            "ExecCondition": loaded("/original/guard") + " " + loaded("/usr/bin/false"),
            **dict.fromkeys(closure.HOOKS, ""),
            "TriggeredBy": trigger or "",
            "Job": "",
        }
        fields.update(values or {})
        raw = (
            "".join(
                key + "=" + value + "\n" for key, value in fields.items() if key not in missing
            ).encode()
            + extra
        )

        def command(argv, deadline):
            if tuple(argv) == closure.JOB_QUERY:
                return (
                    jobs
                    if jobs is not None
                    else closure.io.canonical({"type": "a(usssoo)", "data": [[]]})
                )
            if argv[0] == "/usr/bin/busctl":
                if typed is not None:
                    if isinstance(typed, BaseException):
                        raise typed
                    return typed
                return b"".join(
                    closure.io.canonical({"type": "a(sasbttttuii)", "data": []}) for _ in argv[7:]
                )
            if argv[2] == trigger:
                return trigger_raw
            return raw

        runner.command = Mock(side_effect=command)
        return runner

    def test_all_eight_units_require_typed_proof_for_omitted_empty_hooks(self):
        runner = self.units_fixture()
        units = runner.units(time.monotonic() + 30)
        self.assertEqual(set(units), set(closure.UNITS))
        self.assertTrue(all(set(closure.HOOKS) <= set(fields) for fields in units.values()))
        self.assertTrue(
            all(fields["Job"] == fields["TriggeredBy"] == "" for fields in units.values())
        )
        calls = [call.args[0] for call in runner.command.call_args_list]
        typed = [
            command
            for command in calls
            if command[0] == "/usr/bin/busctl" and command[3] == "get-property"
        ]
        self.assertEqual(len(typed), 8)
        self.assertEqual(
            {command[5] for command in typed},
            {closure._unit_object_path(unit) for unit in closure.UNITS},
        )
        self.assertTrue(
            all(tuple(command[7:]) == tuple(sorted(closure.HOOKS)) for command in typed)
        )
        self.assertTrue(all("--all" in command for command in calls if command[1] == "show"))
        closure._unit_policy(units)

    def test_explicit_empty_hooks_need_no_typed_fallback(self):
        runner = self.units_fixture(missing=())
        closure._unit_policy(runner.units(time.monotonic() + 30))
        self.assertFalse(
            any(
                call.args[0][0] == "/usr/bin/busctl" and call.args[0][3] == "get-property"
                for call in runner.command.call_args_list
            )
        )

    def test_repeated_loaded_conditions_preserve_guard_then_deny(self):
        guard, deny = loaded("/original/guard"), loaded("/usr/bin/false")
        runner = self.units_fixture(
            values={"ExecCondition": guard}, extra=("ExecCondition=" + deny + "\n").encode()
        )
        units = runner.units(time.monotonic() + 30)
        self.assertTrue(all(row["ExecCondition"] == guard + " " + deny for row in units.values()))
        closure._unit_policy(units)

    def test_loaded_guard_and_owned_deny_use_two_systemctl_condition_rows(self):
        guard = (
            "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I -S "
            "/usr/local/libexec/autostop-j1-candidate-guard/guard.py check "
            "--policy-sha256 " + "a" * 64 + " --unit fixture.service ; ignore_errors=no ; "
            "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }"
        )
        deny = (
            "{ path=/usr/bin/false ; argv[]=/usr/bin/false ; ignore_errors=no ; "
            "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }"
        )
        runner = self.units_fixture(
            values={"ExecCondition": guard, "ActiveState": "active", "MainPID": "123"},
            extra=("ExecCondition=" + deny + "\n").encode(),
        )
        units = runner.units(time.monotonic() + 30)
        self.assertEqual(set(units), set(closure.UNITS))
        self.assertTrue(all(row["ExecCondition"] == guard + " " + deny for row in units.values()))
        self.assertTrue(all(not row[hook] for row in units.values() for hook in closure.HOOKS))
        self.assertEqual(
            sum(
                call.args[0][0] == "/usr/bin/busctl" and call.args[0][3] == "get-property"
                for call in runner.command.call_args_list
            ),
            8,
        )
        # This is the loaded-policy admission before stopping currently active units.
        closure._unit_policy(units, stopped=False)

    def test_repeated_conditions_preserve_order_with_the_shared_strict_command_parser(self):
        second, first = loaded("second"), loaded("first")
        raw = f"ExecCondition={second}\nExecCondition={first}\n".encode()
        self.assertEqual(
            closure._show_fields(raw, ("ExecCondition",)),
            {"ExecCondition": second + " " + first},
        )

    def test_condition_array_does_not_allow_duplicate_scalars_or_stop_hooks(self):
        for field in (
            "LoadState",
            "ActiveState",
            "SubState",
            "NeedDaemonReload",
            "MainPID",
            "Job",
            "TriggeredBy",
            *closure.HOOKS,
        ):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(Refusal, "unit_status_incomplete"),
            ):
                self.units_fixture(missing=(), extra=(field + "=\n").encode()).units(
                    time.monotonic() + 30
                )

    def test_nonempty_original_hook_and_guard_are_preserved_and_not_accepted_closed(self):
        condition = loaded("/original/guard") + " " + loaded("/usr/bin/false")
        hook = loaded("/original/duty-hook")
        runner = self.units_fixture(
            missing=tuple(hook for hook in closure.HOOKS if hook != "ExecStartPost"),
            values={"ExecStartPost": hook, "ExecCondition": condition},
        )
        units = runner.units(time.monotonic() + 30)
        self.assertTrue(
            all(
                row["ExecStartPost"] == hook and row["ExecCondition"] == condition
                for row in units.values()
            )
        )
        with self.assertRaisesRegex(Refusal, "loaded_barrier_invalid"):
            closure._unit_policy(units)

    def test_missing_scalar_job_trigger_or_guard_never_defaulted(self):
        for name in (
            "LoadState",
            "ActiveState",
            "SubState",
            "NeedDaemonReload",
            "MainPID",
            "ExecCondition",
            "Job",
            "TriggeredBy",
        ):
            with self.subTest(name=name):
                runner = self.units_fixture(missing=(*closure.HOOKS, name))
                with self.assertRaisesRegex(Refusal, "unit_status_incomplete"):
                    runner.units(time.monotonic() + 30)
                self.assertEqual(runner.command.call_count, 1)

    def test_typed_empty_proof_rejects_nonempty_wrong_type_missing_rows_and_read_failure(self):
        cases = [
            (
                closure.io.canonical({"type": "a(sasbttttuii)", "data": [["/mutable/hook"]]}),
                "unit_empty_hook_not_proven",
            ),
            (closure.io.canonical({"type": "as", "data": []}), "unit_empty_hook_not_proven"),
            (
                closure.io.canonical({"type": "a(sasbttttuii)", "data": [], "extra": True}),
                "unit_empty_hook_not_proven",
            ),
            (b"", "unit_empty_hooks_incomplete"),
            (Refusal("closure_command_failed"), "closure_command_failed"),
        ]
        for typed, error in cases:
            with self.subTest(error=error, typed=typed):
                runner = self.units_fixture(missing=("ExecStop",), typed=typed)
                with self.assertRaisesRegex(Refusal, error):
                    runner.units(time.monotonic() + 30)

    def test_duplicate_and_malformed_systemd_fields_are_refused(self):
        for extra in (
            b"Job=unsafe\n",
            b"malformed\n",
            b"Unexpected=\n",
            b"ExecCondition=\n",
            b"ExecStop=\n",
            b"ExecCondition=path=/unsafe\n",
        ):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(Refusal, "unit_status_incomplete"):
                    self.units_fixture(missing=(), extra=extra).units(time.monotonic() + 30)

    def test_trigger_requires_explicit_empty_job_and_inactive_state(self):
        for raw, error in (
            (b"ActiveState=inactive\n", "unit_status_incomplete"),
            (b"Job=\n", "unit_status_incomplete"),
            (b"ActiveState=active\nJob=\n", "trigger_active"),
            (b"ActiveState=inactive\nJob=0\n", "trigger_active"),
            (b"ActiveState=inactive\nJob=123/start\n", "trigger_active"),
        ):
            with self.subTest(raw=raw), self.assertRaisesRegex(Refusal, error):
                self.units_fixture(trigger="owned.timer", trigger_raw=raw).units(
                    time.monotonic() + 30
                )
        runner = self.units_fixture(trigger="owned.timer")
        self.assertEqual(len(runner.units(time.monotonic() + 30)), 8)
        self.assertTrue(
            all(
                "--all" in call.args[0]
                for call in runner.command.call_args_list
                if call.args[0][1] == "show"
            )
        )

    def test_job_zero_is_not_silently_treated_as_empty_and_native_queue_still_refuses(self):
        units = self.units_fixture(values={"Job": "0"}).units(time.monotonic() + 30)
        with self.assertRaisesRegex(Refusal, "unit_not_stopped"):
            closure._unit_policy(units)
        with self.assertRaisesRegex(Refusal, "native_job_queued"):
            self.units_fixture(
                jobs=closure.io.canonical(
                    {
                        "type": "a(usssoo)",
                        "data": [
                            [
                                [
                                    1,
                                    closure.UNITS[0],
                                    "start",
                                    "waiting",
                                    "/org/freedesktop/systemd1/job/1",
                                    closure._unit_object_path(closure.UNITS[0]),
                                ]
                            ]
                        ],
                    }
                )
            ).units(time.monotonic() + 30)

    def test_job_list_requires_typed_complete_rows_and_preserves_unrelated_jobs(self):
        unrelated = [
            1,
            "other.service",
            "start",
            "waiting",
            "/org/freedesktop/systemd1/job/1",
            "/org/freedesktop/systemd1/unit/other_2eservice",
        ]
        raw = closure.io.canonical({"type": "a(usssoo)", "data": [[unrelated]]})
        self.assertEqual(len(self.units_fixture(jobs=raw).units(time.monotonic() + 30)), 8)
        for value in (
            {"type": "as", "data": [[]]},
            {"type": "a(usssoo)", "data": []},
            {"type": "a(usssoo)", "data": [[unrelated[:-1]]]},
            {"type": "a(usssoo)", "data": [[[False, *unrelated[1:]]]]},
        ):
            with self.subTest(value=value), self.assertRaises(Refusal):
                self.units_fixture(jobs=closure.io.canonical(value)).units(time.monotonic() + 30)

    def test_readonly_child_forms_include_exact_all_and_typed_hooks_only(self):
        show = ("/usr/bin/systemctl", "show", closure.UNITS[0], "--no-pager")
        for flags in ((), ("--all",)):
            self.assertEqual(
                closure._readonly_control_child((*show, *flags, "--property=ActiveState,Job")),
                "/usr/bin/systemctl",
            )
        typed = tuple(closure._empty_hooks_command(closure.UNITS[0], ("ExecStartPre", "ExecStop")))
        self.assertEqual(closure._readonly_control_child(typed), "/usr/bin/busctl")
        self.assertEqual(closure._readonly_control_child(closure.JOB_QUERY), "/usr/bin/busctl")
        for command in (
            (*show, "--all", "--extra", "--property=Job"),
            (*typed[:-2], "Environment"),
            (*typed[:-2], "ExecStop", "ExecStop"),
            (*typed[:3], "set-property", *typed[4:]),
            (*typed[:5], "/org/freedesktop/systemd1/unit/other", *typed[6:]),
            (*closure.JOB_QUERY[:-1], "StartUnit"),
            (*closure.JOB_QUERY, "unexpected"),
        ):
            with self.subTest(command=command):
                self.assertIsNone(closure._readonly_control_child(command))

    def test_transient_workers_require_explicit_bytecode_suppression(self):
        allowed = closure._forward_commands(ATTEMPT)
        prefix = ("/usr/bin/python3", "-I", "-S")
        for operation in ("run", "settle"):
            tail = (closure.FORWARD_SCRIPT, operation, "--attempt", ATTEMPT)
            with self.subTest(operation=operation):
                self.assertNotIn((*prefix, *tail), allowed)
                self.assertEqual(allowed[(*prefix, "-B", *tail)], operation)
        for flags in ((), ("-B",)):
            self.assertEqual(
                allowed[(*prefix, *flags, closure.FORWARD_SCRIPT, "apply", "--attempt", ATTEMPT)],
                "apply",
            )

    def test_current_release_launcher_matches_admitted_fixed_waiter(self):
        from scripts import j1_forward_release as release

        host = Mock()
        host.run.side_effect = RuntimeError("captured-before-effects")
        with (
            patch.object(release, "load_attempt", return_value=(None, None, {}, None, host)),
            patch.object(release, "remaining", return_value=600),
            patch.object(release, "CRM", PurePosixPath("/opt/autostopcrm")),
        ):
            with self.assertRaisesRegex(RuntimeError, "captured-before-effects"):
                release.launch(ATTEMPT)
        self.assertEqual(
            tuple(host.run.call_args.args[0]), closure._systemd_launch_command(ATTEMPT)
        )

    def test_current_worker_commands_match_admitted_control_forms(self):
        from scripts import j1_forward_worker as worker

        instance = object.__new__(worker.Worker)
        instance.ssh = Path("ssh.exe")
        instance.ssh_key = Path("private-key")
        allowed = closure._forward_commands(ATTEMPT)
        for operation, options in (
            ("inspect", {"stage": "closed"}),
            ("inspect", {"stage": "activated"}),
            ("export-delta", {}),
            ("receive-proof", {"kind": "offhost_restore"}),
            ("receive-proof", {"kind": "native_acceptance"}),
            ("receive-proof", {"kind": "runtime_acceptance"}),
        ):
            with self.subTest(operation=operation, options=options):
                command = instance._ssh(operation, ATTEMPT, **options)
                remote = tuple(command[command.index(worker.HOST) + 1 :])
                self.assertEqual(allowed.get(remote), operation)

    def test_control_source_and_baseline_are_bound_before_admission(self):
        directory = closure.ATTEMPTS / ATTEMPT
        native = {"native": "accepted"}
        consumers = {
            "scripts/j1_forward_" + name + ".py": "a" * 64
            for name in ("release", "closure", "io", "host", "state", "worker", "backup", "proofs")
        }
        baseline = {"native": native, "attempt_id": ATTEMPT, "source_consumers": consumers}
        plan = {
            "attempt_id": ATTEMPT,
            "baseline_ref": {"path": str(directory / "baseline.json"), "sha256": "b" * 64},
            "candidate": {"consumer_sha256": closure.io.sha(closure.io.canonical(consumers))},
        }
        owned = {
            "attempt_id": ATTEMPT,
            "attempt_dir": str(directory),
            "baseline_sha256": closure.io.sha(closure.io.canonical(native)),
        }
        with (
            patch.object(closure.io, "read_protected", return_value=closure.io.canonical(plan)),
            patch.object(closure.io, "referenced", return_value=closure.io.canonical(baseline)),
            patch.object(closure.io, "reference", return_value={"sha256": "a" * 64}) as refs,
        ):
            self.assertEqual(
                closure.LinuxRunner().control_bindings(owned),
                plan["candidate"]["consumer_sha256"],
            )
            self.assertEqual(refs.call_count, len(consumers))
            with self.assertRaisesRegex(Refusal, "control_consumer_binding"):
                closure.LinuxRunner().control_bindings({**owned, "consumer_sha256": "e" * 64})
            refs.return_value = {"sha256": "c" * 64}
            with self.assertRaisesRegex(Refusal, "control_consumer_changed"):
                closure.LinuxRunner().control_bindings(owned)
            with self.assertRaisesRegex(Refusal, "control_baseline_binding"):
                closure.LinuxRunner().control_bindings({**owned, "baseline_sha256": "d" * 64})

    def test_auxiliary_commit_gate_binds_fixed_history_plan_closure_and_durable_head(self):
        from tests.test_j1_forward_state import ForwardStateTests

        fixture = ForwardStateTests()
        fixture.root, fixture.base = Path.cwd(), "1" * 64
        plan = fixture.plan(attempt=ATTEMPT)
        directory = closure.ATTEMPTS / ATTEMPT
        root = closure.ATTEMPTS.parent / "state"
        ref = {"path": str(root / "history" / "00000000000000000001.json"), "sha256": "a" * 64}
        ownership_ref = {"path": str(directory / "closure-owned.json"), "sha256": "b" * 64}
        record = {"plan": plan, "proofs": {"closure": ownership_ref}}
        head = {"sequence": 1, "sha256": ref["sha256"], "record": record}
        owned = {
            "attempt_id": ATTEMPT,
            "attempt_dir": str(directory),
            "consumer_sha256": plan["candidate"]["consumer_sha256"],
            "process_scope_ref": {
                "path": str(directory / "process-scope.json"),
                "sha256": "c" * 64,
            },
        }
        runner = closure.LinuxRunner()
        runner.control_bindings = Mock()
        runner.load_scope = Mock(return_value={"base_sha256": fixture.base})
        store = Mock()
        store.head.return_value = head
        head_ref = {"path": str(root / "HEAD.json"), "sha256": "f" * 64}
        with (
            patch.object(closure.io, "read_protected", return_value=closure.io.canonical(plan)),
            patch.object(
                closure.io, "referenced", side_effect=lambda _ref: closure.io.canonical(record)
            ),
            patch.object(
                closure.io,
                "reference",
                side_effect=lambda path: head_ref if path == root / "HEAD.json" else ownership_ref,
            ),
            patch.object(closure.io, "confirm_durable") as durable,
            patch.object(closure.forward_state, "Store", return_value=store),
        ):
            runner.committed(owned, ref)
            self.assertEqual(store.head.call_count, 2)
            self.assertEqual(durable.call_args_list[0].args, (ref,))
            self.assertEqual(durable.call_args_list[1].args, (head_ref,))
            runner.control_bindings.assert_called_once_with(owned)
            for bad_ref, bad_owned, error in (
                ({**ref, "path": str(root / "foreign.json")}, owned, "auxiliary_commit_reference"),
                (ref, {**owned, "attempt_id": "foreign"}, "auxiliary_commit_binding"),
                (ref, {**owned, "consumer_sha256": "d" * 64}, "auxiliary_commit_binding"),
            ):
                with self.subTest(error=error), self.assertRaisesRegex(Refusal, error):
                    runner.committed(bad_owned, bad_ref)
            record["proofs"]["closure"] = {**ownership_ref, "sha256": "d" * 64}
            with self.assertRaisesRegex(Refusal, "auxiliary_commit_binding"):
                runner.committed(owned, ref)
            record["proofs"]["closure"] = ownership_ref
            store.head.side_effect = [head, {**head, "sha256": "d" * 64}]
            with self.assertRaisesRegex(Refusal, "auxiliary_commit_head"):
                runner.committed(owned, ref)

    def test_proc_argv_preserves_empty_argument_boundaries(self):
        self.assertEqual(closure._argv(b"python\0arg\0\0"), ("python", "arg", ""))
        self.assertEqual(closure._argv(b"python\0arg\0"), ("python", "arg"))
        self.assertEqual(closure._argv(b""), ())

    def control_fixture(self):
        runner = closure.LinuxRunner()
        runner.control_bindings = Mock()
        runner.control_identity = Mock(return_value=True)
        commands = closure._forward_commands(ATTEMPT)
        argv = {operation: args for args, operation in commands.items()}

        def process(args, parent=1, group="0::/ssh.scope"):
            return {
                "argv": args,
                "ppid": parent,
                "state": "S",
                "start_ticks": 100,
                "cgroup": group,
                "suspect": True,
            }

        return runner, argv, process

    def test_concurrent_exact_main_and_inspection_are_authenticated(self):
        runner, argv, process = self.control_fixture()
        rows = {101: process(argv["run"]), 102: process(argv["inspect"])}
        result = runner.control_processes({"attempt_id": ATTEMPT}, rows)
        self.assertEqual(result, {101, 102})
        runner.control_bindings.assert_called_once()
        self.assertEqual(runner.control_identity.call_count, 2)
        self.assertEqual(
            runner.control_identity.call_args_list[0].kwargs,
            {"unit": "autostop-crm-forward-" + ATTEMPT + ".service"},
        )
        self.assertEqual(runner.control_identity.call_args_list[1].kwargs, {"unit": None})

    def test_changed_attempt_operation_or_extra_argument_never_admitted(self):
        runner, argv, process = self.control_fixture()
        wrong_attempt = tuple(
            "b921389d-490c-4608-852b-b5b9918798e0" if x == ATTEMPT else x for x in argv["inspect"]
        )
        wrong_role = tuple("arbitrary-write" if x == "inspect" else x for x in argv["inspect"])
        rows = {
            101: process(wrong_attempt),
            102: process(wrong_role),
            103: process((*argv["inspect"], "--force")),
            104: process(("/tmp/python", *argv["inspect"][1:])),
        }
        self.assertEqual(runner.control_processes({"attempt_id": ATTEMPT}, rows), set())
        runner.control_bindings.assert_not_called()
        runner.control_identity.assert_not_called()

    def test_consumer_binding_failure_cannot_admit_process(self):
        runner, argv, process = self.control_fixture()
        runner.control_bindings.side_effect = Refusal("control_consumer_changed")
        with self.assertRaisesRegex(Refusal, "control_consumer_changed"):
            runner.control_processes({"attempt_id": ATTEMPT}, {101: process(argv["inspect"])})
        runner.control_identity.assert_not_called()

    def test_systemd_waiter_requires_exact_command_and_authenticated_apply_parent(self):
        runner, argv, process = self.control_fixture()
        launch = closure._systemd_launch_command(ATTEMPT)
        rows = {
            101: process(argv["apply"]),
            102: process(launch, parent=101),
            103: process(launch, parent=999),
            104: process((*launch, "--extra"), parent=101),
        }
        self.assertEqual(runner.control_processes({"attempt_id": ATTEMPT}, rows), {101, 102})
        self.assertEqual(runner.control_identity.call_args_list[-1].args[2], "/usr/bin/systemd-run")

    def test_shell_wrapper_must_be_literal_and_actual_authenticated_parent(self):
        runner, argv, process = self.control_fixture()
        command = " ".join(argv["inspect"])
        rows = {
            101: process(argv["inspect"], parent=102),
            102: process(("bash", "-c", command)),
            103: process(("bash", "-c", command)),
            104: process(("bash", "-c", command + "; true")),
        }
        self.assertEqual(runner.control_processes({"attempt_id": ATTEMPT}, rows), {101, 102})
        self.assertEqual(runner.control_identity.call_args_list[-1].args[2], "/bin/bash")

    def test_only_readonly_fixed_command_children_are_admitted(self):
        runner, argv, process = self.control_fixture()
        git = ("/usr/bin/git", "-C", closure.CRM.as_posix())
        rows = {
            101: process(argv["inspect"]),
            102: process((*git, "status", "--porcelain"), parent=101),
            103: process((*git, "reset", "--hard"), parent=101),
            104: process((*git, "status", "--porcelain"), parent=999),
            105: process(
                (
                    "/usr/bin/systemctl",
                    "show",
                    closure.UNITS[0],
                    "--no-pager",
                    "--property=ActiveState,Job",
                ),
                parent=101,
            ),
            106: process(("/usr/bin/systemctl", "start", closure.UNITS[0]), parent=101),
        }
        self.assertEqual(runner.control_processes({"attempt_id": ATTEMPT}, rows), {101, 102, 105})

    def test_fixed_bus_metadata_children_require_parent_and_actual_busctl_identity(self):
        runner, argv, process = self.control_fixture()
        hooks = tuple(closure._empty_hooks_command(closure.UNITS[0], ("ExecStop",)))
        rows = {
            101: process(argv["inspect"]),
            102: process(hooks, parent=101),
            103: process(closure.JOB_QUERY, parent=101),
            104: process(hooks, parent=999),
            105: process((*closure.JOB_QUERY[:-1], "StartUnit"), parent=101),
        }
        self.assertEqual(runner.control_processes({"attempt_id": ATTEMPT}, rows), {101, 102, 103})
        self.assertEqual(
            [(call.args[0], call.args[2]) for call in runner.control_identity.call_args_list],
            [(101, "/usr/bin/python3"), (102, "/usr/bin/busctl"), (103, "/usr/bin/busctl")],
        )
        runner.control_identity.side_effect = [True, Refusal("control_executable_changed")]
        with self.assertRaisesRegex(Refusal, "control_executable_changed"):
            runner.control_processes({"attempt_id": ATTEMPT}, rows)

    def test_control_admission_is_not_a_write_descriptor_or_native_cgroup_exemption(self):
        runner = FakeRunner()
        runner.procs[777] = {
            "start_ticks": 100,
            "ppid": 1,
            "state": "S",
            "cgroup": "0::/ssh.scope",
            "suspect": True,
        }
        runner.control_processes = Mock(return_value={777})
        with patch.object(closure, "RUNNER", runner):
            owned = closure.prepare_closure(
                {"warm_actors": [actor(pid) for pid in sorted(closure.ACCEPTED_PIDS)]},
                Path.cwd() / ATTEMPT,
            )
            frozen, own = closure._process_policy(owned, runner.procs)
            self.assertNotIn(777, own)
            self.assertNotIn(777, frozen)
            runner.procs[777]["cgroup"] = "0::/system.slice/" + closure.UNITS[0]
            with self.assertRaisesRegex(Refusal, "native_cgroup_not_empty"):
                closure._process_policy(owned, runner.procs)
        with self.assertRaisesRegex(Refusal, "unexpected_source_write_descriptor"):
            self.descriptor_fixture("2", target="/opt/autostopcrm/data/state.sqlite3-shm")

    def control_identity_fixture(
        self, *, uid=b"0 0 0 0", inode=9, final_birth=100, cgroup="0::/system.slice/owned.service"
    ):
        proc, root, program, trusted, hostroot = (MagicMock() for _ in range(5))
        nodes = {
            name: MagicMock() for name in ("cmdline", "status", "exe", "ns/mnt", "root", "cgroup")
        }
        global_namespace = MagicMock()
        proc.__truediv__.return_value = root
        root.__truediv__.side_effect = nodes.__getitem__
        program.resolve.return_value = trusted
        trusted.__str__.return_value = "/usr/bin/python3.12"
        trusted.stat.return_value = SimpleNamespace(st_dev=1, st_ino=9)
        nodes["exe"].stat.return_value = SimpleNamespace(st_dev=1, st_ino=inode)
        hostroot.stat.return_value = nodes["root"].stat.return_value = SimpleNamespace(
            st_dev=1, st_ino=1
        )
        argv = (
            "/usr/bin/python3",
            "-I",
            "-S",
            "-B",
            closure.FORWARD_SCRIPT,
            "run",
            "--attempt",
            ATTEMPT,
        )
        raw = {
            nodes["cmdline"]: b"\0".join(x.encode() for x in argv) + b"\0",
            nodes["status"]: b"Uid:\t" + uid + b"\n",
            nodes["exe"]: b"protected interpreter",
            nodes["cgroup"]: cgroup.encode(),
        }
        paths = {
            "/proc": proc,
            "/usr/bin/python3": program,
            "/": hostroot,
            "/proc/self/ns/mnt": global_namespace,
        }
        links = {
            nodes["exe"]: "/usr/bin/python3.12",
            nodes["ns/mnt"]: "mnt:[1]",
            global_namespace: "mnt:[1]",
        }
        row = {"start_ticks": 100, "ppid": 1, "argv": argv, "cgroup": cgroup}
        with (
            patch.object(closure, "Path", side_effect=paths.__getitem__),
            patch.object(closure, "_stat", side_effect=[(100, 1, "S"), (final_birth, 1, "S")]),
            patch.object(closure, "_proc_bytes", side_effect=lambda path, *args: raw[path]),
            patch.object(closure.io, "read_protected", return_value=b"protected interpreter"),
            patch.object(closure.os, "readlink", side_effect=links.__getitem__),
        ):
            return closure.LinuxRunner.control_identity(
                123, row, "/usr/bin/python3", unit="owned.service"
            )

    def test_control_identity_checks_root_program_pid_birth_and_service(self):
        self.assertTrue(self.control_identity_fixture())
        for changes, error in (
            ({"uid": b"0 1000 0 0"}, "control_root_required"),
            ({"inode": 10}, "control_executable_changed"),
            ({"final_birth": 101}, "control_pid_changed"),
            ({"cgroup": "0::/system.slice/other.service"}, "control_service_required"),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(Refusal, error):
                self.control_identity_fixture(**changes)

    def test_container_stop_is_attempted_even_if_unit_stop_fails(self):
        runner = closure.LinuxRunner()
        runner.command = Mock(side_effect=[Refusal("unit-stop"), b""])
        ownership = {"fixture": "already-drained"}
        deadline = time.monotonic() + 10
        with (
            patch.object(runner, "_store_stop") as store_stop,
            self.assertRaisesRegex(Refusal, "writer_stop_failed"),
        ):
            runner.stop(deadline, ownership=ownership)
        store_stop.assert_called_once_with(ownership, deadline)
        first, second = [call.args[0] for call in runner.command.call_args_list]
        self.assertEqual(first, ["/usr/bin/systemctl", "stop", *closure.UNITS])
        self.assertEqual(second[:3], closure.DOCKER)
        self.assertEqual(second[-2:], ["stop", "autostopcrm"])
        self.assertNotIn("start", second)

    def test_pidfd_is_opened_before_identity_recheck_and_reused_pid_not_signaled(self):
        runner = closure.LinuxRunner()
        expected = actor(538285)
        runner.actor = Mock(return_value={**expected, "start_ticks": 999, "stopped": False})
        with (
            patch.object(closure.os, "pidfd_open", return_value=42, create=True) as opened,
            patch.object(closure.signal, "pidfd_send_signal", create=True) as sent,
            patch.object(closure.os, "close") as closed,
        ):
            with self.assertRaisesRegex(Refusal, "warm_actor_changed"):
                runner.signal_actor(expected, True)
        opened.assert_called_once_with(538285, 0)
        sent.assert_not_called()
        closed.assert_called_once_with(42)

    def test_slow_identity_hash_cannot_signal_continue_after_plan_deadline(self):
        runner = closure.LinuxRunner()
        expected = actor(538285)
        with (
            patch.object(closure.time, "monotonic", return_value=100) as clock,
            patch.object(closure.os, "pidfd_open", return_value=42, create=True),
            patch.object(closure.signal, "pidfd_send_signal", create=True) as sent,
            patch.object(closure.os, "close") as closed,
        ):

            def delayed_actor(_expected, *, full=False):
                self.assertTrue(full)
                clock.return_value = 101.01
                return {**expected, "stopped": True}

            runner.actor = delayed_actor
            with self.assertRaises(Refusal):
                runner.signal_actor(expected, False, deadline=101)
        sent.assert_not_called()
        closed.assert_called_once_with(42)

    def test_container_inspection_excludes_environment_and_rejects_parent_mount_writer(self):
        runner = closure.LinuxRunner()
        identity = "a" * 64
        runner.command = Mock(
            side_effect=[
                identity.encode() + b"\n",
                closure.io.canonical(
                    {
                        "Id": identity,
                        "Name": "/another-project",
                        "Service": "other",
                        "Mounts": [{"Source": "/opt", "RW": True}],
                    }
                ),
            ]
        )
        with self.assertRaisesRegex(Refusal, "alternative_container_writer"):
            runner.containers(time.monotonic() + 10)
        inspection = runner.command.call_args_list[1].args[0]
        self.assertIn("--format", inspection)
        self.assertNotIn("Env", inspection[inspection.index("--format") + 1])

    def test_readonly_mount_from_another_project_is_not_stopped(self):
        runner = closure.LinuxRunner()
        identity = "a" * 64
        runner.command = Mock(
            side_effect=[
                identity.encode() + b"\n",
                closure.io.canonical(
                    {
                        "Id": identity,
                        "Name": "/other",
                        "Service": "other",
                        "Mounts": [{"Source": "/opt/autostopcrm/data", "RW": False}],
                    }
                ),
            ]
        )
        self.assertEqual(runner.containers(time.monotonic() + 10), [{"id": identity}])
        self.assertFalse(any("stop" in call.args[0] for call in runner.command.call_args_list))

    def descriptor_fixture(
        self, flags, *, target="/opt/autostopcrm/data/state.json", frozen=(), own=(), alias=False
    ):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "123"
            (root / "fd").mkdir(parents=True)
            (root / "fdinfo").mkdir()
            descriptor = root / "fd/5"
            descriptor.write_bytes(b"synthetic")
            (root / "fdinfo/5").write_text("flags:\t" + flags + "\n")
            (root / "maps").write_bytes(b"")
            metadata = descriptor.stat()
            runner = closure.LinuxRunner()
            runner.source_inodes = Mock(
                return_value={(metadata.st_dev, metadata.st_ino)} if alias else set()
            )
            original_path = Path

            def redirected(value):
                return base if str(value) == "/proc" else original_path(value)

            with (
                patch.object(closure, "Path", side_effect=redirected),
                patch.object(closure.os, "readlink", return_value=target),
                patch.object(closure.os, "O_ACCMODE", 3, create=True),
                patch.object(closure, "_stat", return_value=(100, 1, "S")),
            ):
                return runner.descriptor_scan({123: {"start_ticks": 100}}, set(frozen), set(own))

    def test_readonly_source_descriptor_is_permitted(self):
        result = self.descriptor_fixture("0")
        self.assertEqual(result["frozen_owned_write_fds"], 0)

    def test_primary_source_write_descriptor_is_rejected_even_for_backup_process(self):
        with self.assertRaisesRegex(Refusal, "unexpected_source_write_descriptor"):
            self.descriptor_fixture("2", own={123})

    def test_unknown_hardlink_alias_writer_is_detected_by_inode(self):
        with self.assertRaisesRegex(Refusal, "unexpected_source_write_descriptor"):
            self.descriptor_fixture("2", target="/tmp/not-the-source-name", alias=True)

    def test_exact_frozen_actor_descriptor_is_recorded_not_called_active(self):
        result = self.descriptor_fixture("2", frozen={123})
        self.assertEqual(result["frozen_owned_write_fds"], 1)

    def test_only_real_sqlite_auxiliary_can_be_coordination_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory) / "synthetic.sqlite3"
            with patch.object(closure, "_under", return_value=True):
                primary.write_bytes(b"not a database")
                self.assertFalse(closure.LinuxRunner.coordination_path(str(primary) + "-shm"))
                primary.write_bytes(b"SQLite format 3\0")
                self.assertTrue(closure.LinuxRunner.coordination_path(str(primary) + "-shm"))
                self.assertFalse(closure.LinuxRunner.coordination_path(str(primary) + "-wal"))

    def test_source_inventory_does_not_hide_walk_errors(self):
        def incomplete(path, *, followlinks, onerror):
            onerror(PermissionError("synthetic"))
            return []

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(closure, "WRITER_ROOTS", (directory,)),
            patch.object(closure.os, "walk", side_effect=incomplete),
        ):
            with self.assertRaisesRegex(Refusal, "source_inventory_incomplete"):
                closure.LinuxRunner.source_inodes()


if __name__ == "__main__":
    unittest.main()
