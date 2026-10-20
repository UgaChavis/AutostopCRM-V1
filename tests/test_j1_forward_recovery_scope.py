"""Synthetic recovery-scope tests; no production processes or signals."""

from __future__ import annotations

import copy
import os
import unittest
from unittest.mock import Mock, patch

from scripts import j1_forward_io as io
from scripts import j1_forward_process_scope as prior
from scripts import j1_forward_recovery_scope as scope

ATTEMPT = "7767b188-209a-49a3-8aac-854cf2d4336f"
DIRECTORY = "/var/lib/autostop-crm-forward/attempts/" + ATTEMPT


def actor(pid):
    return {
        "pid": pid,
        "start_ticks": prior.STARTS[pid],
        "exe": "/root/codex",
        "exe_sha256": prior._program_sha(pid),
        "exe_identity": [1, pid],
        "argv_sha256": prior.ARGV[prior._kind(pid)],
        "cgroup": prior.CGROUPS[pid],
        "uid": 996 if pid == 698406 else 0,
        "mount_namespace": "mnt:[123]",
    }


def fixture():
    actors = {str(pid): actor(pid) for pid in prior.FREEZE + prior.TRANSPORTS}
    original = [actor(pid) for pid in prior.ORIGINAL]
    native = {
        "schema": "autostop.j1.crm-forward-native-recovery.v2",
        "daemon_mode": "accepted_warm",
        "base_sha256": "e" * 64,
        "warm_identity_semantics": "historical_origin_plus_observed_continuation",
        "warm_actors": original,
        "historical_warm_actors": copy.deepcopy(original),
        "observed_warm_actors": [actor(pid) for pid in (538285, 538487)],
        "profiles": {
            prior.NATIVE_UNIT: {
                "interpreter": "/usr/bin/python3",
                "interpreter_realpath": "/usr/bin/python3.12",
                "interpreter_sha256": "c" * 64,
                "module": "autostop_manager.codex_wake",
                "arguments": [],
                "credentials": [0, 0, []],
            }
        },
    }
    stdio = [
        {
            "pid": pid,
            "fd": fd,
            "parent": prior.PARENTS[pid],
            "parent_fd": 10 + n * 3 + fd,
            "pipe_inode": 10000 + n * 3 + fd,
        }
        for n, pid in enumerate(sorted(prior.HELPERS + prior.PROXIES))
        for fd in (0, 1, 2)
    ]
    peer = {
        "actor": {"pid": 762195, "start_ticks": 11218504},
        "profile": scope.native_profile(native),
        "inode": 10,
        "daemon_inode": 11,
    }
    origin = {
        "schema": prior.SCHEMA,
        "profile_sha256": prior.PROFILE_SHA256,
        "attempt_id": scope.ORIGIN_ATTEMPT,
        "attempt_dir": scope.ORIGIN_DIRECTORY,
        "consumer_sha256": "a" * 64,
        "base_sha256": "e" * 64,
        "native_sha256": "b" * 64,
        "boot_id": "c2d6447f-ae85-43f9-bc17-a295094a06dd",
        "original_actors": original,
        "freeze_actors": [actor(pid) for pid in prior.FREEZE],
        "transport_actors": [actor(pid) for pid in prior.TRANSPORTS],
        "native_control_peer": peer,
        "observation": {
            "roles": {pid: prior._role(int(pid)) for pid in actors},
            "details": {
                pid: {
                    "ppid": prior.PARENTS[int(pid)],
                    "credentials": "fixed",
                    "capabilities": "fixed",
                    "namespaces": "fixed",
                }
                for pid in actors
            },
            "programs": {pid: {"sha256": row["exe_sha256"]} for pid, row in actors.items()},
            "entrypoints": {},
            "stdio": stdio,
            "proxy_peers": [],
            "daemon_listener": {},
            "confined_tunnel": {},
        },
    }
    observed = {
        "boot_id": origin["boot_id"],
        "actors": {str(pid): actor(pid) for pid in scope.FREEZE + scope.TRANSPORTS},
        "thread_states": {
            str(pid): ["T"] if pid in scope.FREEZE else ["S"]
            for pid in scope.FREEZE + scope.TRANSPORTS
        },
        "native_control_peer": copy.deepcopy(peer),
        "pending_ingress": True,
        "observation": scope._origin_projection(origin),
    }
    return native, origin, observed


class RecoveryScopeTests(unittest.TestCase):
    def setUp(self):
        self.native, self.origin, self.observed = fixture()
        self.ref = {**scope.ORIGIN_SCOPE_REF, "sha256": io.sha(io.canonical(self.origin))}
        self.reference_patch = patch.object(scope, "ORIGIN_SCOPE_REF", self.ref)
        self.reference_patch.start()
        self.addCleanup(self.reference_patch.stop)
        self.read_patch = patch.object(scope, "_read_origin")
        self.read_mock = self.read_patch.start()
        self.addCleanup(self.read_patch.stop)
        self.observer = Mock()
        self.observer.snapshot.side_effect = lambda *_a, **_k: copy.deepcopy(self.observed)
        self.observer_patch = patch.object(scope, "OBSERVER", self.observer)
        self.observer_patch.start()
        self.addCleanup(self.observer_patch.stop)
        self.bound = self.capture()

    def capture(self):
        return scope.capture(
            self.origin,
            self.ref,
            self.native,
            attempt_id=ATTEMPT,
            attempt_dir=DIRECTORY,
            consumer_sha256="f" * 64,
        )

    def closed(self):
        self.observed["native_control_peer"] = None
        self.observed["pending_ingress"] = False

    def staging(self):
        self.closed()
        self.observed["thread_states"][str(scope.DAEMON)] = ["S"]

    def activation(self):
        self.staging()
        peer = copy.deepcopy(self.origin["native_control_peer"])
        peer["actor"]["pid"] = 2000000
        peer["actor"]["start_ticks"] += 1000
        self.observed["native_control_peer"] = peer

    def test_historical_three_are_not_claimed_live(self):
        self.assertEqual(len(self.bound["original_actors"]), 3)
        self.assertEqual([row["pid"] for row in self.bound["original_liveness"]], [538285, 538487])
        self.assertEqual(
            [row["pid"] for row in self.bound["retired_transports"]], list(scope.RETIRED)
        )
        self.assertEqual(tuple(map(len, scope.actor_sets(self.bound))), (13, 2))

    def test_preclose_is_frozen_ownership_not_quiet(self):
        result = scope.verify(self.bound, frozen=False)
        self.assertFalse(result["frozen"])
        self.assertEqual(result["phase"], "recovery_preclose")
        self.assertTrue(all(row["stopped"] for row in result["freeze_actors"]))

    def test_preclose_allows_exact_old_native_peer_or_absence(self):
        self.observed["native_control_peer"] = None
        scope.verify(self.bound, frozen=False)

    def test_preclose_refuses_new_native_peer(self):
        self.observed["native_control_peer"]["actor"]["pid"] += 1
        with self.assertRaisesRegex(io.Refusal, "preclose_native_peer_changed"):
            scope.verify(self.bound, frozen=False)

    def test_capture_refuses_unowned_running_writer(self):
        self.observed["thread_states"][str(scope.ORPHANS[0])] = ["S"]
        with self.assertRaisesRegex(io.Refusal, "actor_state"):
            self.capture()

    def test_capture_requires_explicit_historical_native_schema(self):
        self.native["schema"] = "autostop.j1.crm-forward-native.v1"
        with self.assertRaisesRegex(io.Refusal, "native_mode"):
            self.capture()

    def test_capture_refuses_invented_live_original_ssh(self):
        self.native["observed_warm_actors"] = copy.deepcopy(self.native["warm_actors"])
        with self.assertRaisesRegex(io.Refusal, "native_live_originals_changed"):
            self.capture()

    def test_pure_validation_never_calls_observer(self):
        self.observer.reset_mock()
        scope.validate_scope_shape(self.bound)
        self.observer.snapshot.assert_not_called()

    def test_scope_unknown_fields_refused(self):
        self.bound["trusted"] = True
        with self.assertRaisesRegex(io.Refusal, "scope_shape"):
            scope.actor_sets(self.bound)

    def test_old_scope_rewrite_refused_even_with_new_profile(self):
        self.bound["origin_scope"]["native_sha256"] = "0" * 64
        with self.assertRaisesRegex(io.Refusal, "origin_scope_hash"):
            scope.actor_sets(self.bound)

    def test_wrong_origin_reference_refused(self):
        self.bound["origin_scope_ref"]["path"] += ".copy"
        with self.assertRaisesRegex(io.Refusal, "origin_refs"):
            scope.actor_sets(self.bound)

    def test_same_attempt_replay_refused(self):
        self.bound["attempt_id"] = scope.ORIGIN_ATTEMPT
        self.bound["attempt_dir"] = scope.ORIGIN_DIRECTORY
        with self.assertRaisesRegex(io.Refusal, "attempt_binding"):
            scope.actor_sets(self.bound)

    def test_new_writer_is_not_adopted(self):
        self.bound["freeze_actors"][0]["pid"] += 1
        with self.assertRaisesRegex(io.Refusal, "actor_projection"):
            scope.actor_sets(self.bound)

    def test_unfrozen_retired_proxy_disposition_refused(self):
        self.bound["disposition"] = "all_released"
        with self.assertRaisesRegex(io.Refusal, "scope_shape"):
            scope.actor_sets(self.bound)

    def test_unrelated_topology_change_refused(self):
        self.bound["observation"]["details"][str(scope.HELPERS[0])]["ppid"] = 1
        with self.assertRaisesRegex(io.Refusal, "origin_topology_changed"):
            scope.actor_sets(self.bound)

    def test_fresh_executable_identity_change_refused(self):
        self.observed["actors"][str(scope.DAEMON)]["exe_identity"][1] += 1
        with self.assertRaisesRegex(io.Refusal, "actor_identity_changed"):
            scope.verify(self.bound, frozen=False)

    def test_fresh_credential_change_refused(self):
        self.observed["observation"]["details"][str(scope.ORPHANS[0])]["credentials"] = "changed"
        with self.assertRaisesRegex(io.Refusal, "topology_or_provenance_changed"):
            scope.verify(self.bound, frozen=False)

    def test_closed_requires_absent_native_peer(self):
        self.observed["pending_ingress"] = False
        with self.assertRaisesRegex(io.Refusal, "native_control_peer_present"):
            scope.verify(self.bound, frozen=True)

    def test_closed_refuses_queued_bytes(self):
        self.observed["native_control_peer"] = None
        with self.assertRaisesRegex(io.Refusal, "queued_proxy_ingress"):
            scope.verify(self.bound, frozen=True)

    def test_closed_readback_has_exact_actual_fifteen(self):
        self.closed()
        result = scope.verify(self.bound, frozen=True)
        self.assertTrue(result["frozen"])
        self.assertEqual(result["retired_transports_absent"], list(scope.RETIRED))
        self.assertEqual(
            tuple(map(len, (result["freeze_actors"], result["transport_actors"]))), (13, 2)
        )

    def test_staging_requires_daemon_live(self):
        self.closed()
        with self.assertRaisesRegex(io.Refusal, "actor_state"):
            scope.verify_staging(self.bound)

    def test_staging_refuses_pending_queue(self):
        self.staging()
        self.observed["pending_ingress"] = True
        with self.assertRaisesRegex(io.Refusal, "queued_proxy_ingress"):
            scope.verify_staging(self.bound)

    def test_staging_has_no_native_peer(self):
        self.staging()
        self.assertIsNone(scope.verify_staging(self.bound)["native_control_peer"])

    def test_activation_requires_admitted_native_profile(self):
        self.activation()
        self.observed["native_control_peer"]["profile"]["exe_sha256"] = "0" * 64
        with self.assertRaisesRegex(io.Refusal, "activation_native_peer_required"):
            scope.verify_activation(self.bound)

    def test_activation_keeps_all_auxiliary_stopped(self):
        self.activation()
        result = scope.verify_activation(self.bound)
        self.assertEqual(sum(row["stopped"] for row in result["freeze_actors"]), 12)

    def test_released_means_helpers_live_orphan_proxies_still_stopped(self):
        self.activation()
        for pid in scope.HELPERS:
            self.observed["thread_states"][str(pid)] = ["S"]
        result = scope.verify_released(self.bound)
        self.assertTrue(result["auxiliary_released"])
        self.assertEqual(
            [row["pid"] for row in result["freeze_actors"] if row["stopped"]], list(scope.ORPHANS)
        )

    def test_released_never_accepts_thawed_orphan(self):
        self.activation()
        for pid in scope.HELPERS + scope.ORPHANS:
            self.observed["thread_states"][str(pid)] = ["S"]
        with self.assertRaisesRegex(io.Refusal, "actor_state"):
            scope.verify_released(self.bound)

    def test_multi_thread_mixed_state_refused(self):
        self.observed["thread_states"][str(scope.DAEMON)] = ["T", "S"]
        with self.assertRaisesRegex(io.Refusal, "actor_state"):
            scope.verify(self.bound, frozen=False)

    def test_fixed_profile_hash_is_pure(self):
        self.assertEqual(scope.PROFILE_SHA256, io.sha(io.canonical(scope.PROFILE)))


class PipeOwnershipTests(unittest.TestCase):
    def setUp(self):
        _, self.origin, _ = fixture()
        self.processes = {
            pid: {"start_ticks": scope.STARTS[pid]} for pid in scope.FREEZE + scope.TRANSPORTS
        }
        self.tables = {pid: {} for pid in self.processes}
        for row in self.origin["observation"]["stdio"]:
            target = "pipe:[" + str(row["pipe_inode"]) + "]"
            self.tables[row["pid"]][row["fd"]] = target
            if row["pid"] in scope.HELPERS:
                self.tables[scope.DAEMON][row["parent_fd"]] = target
        self.observer = scope.LinuxObserver()
        self.observer._check = Mock()
        self.observer.descriptors = lambda pid: self.tables[pid]

    def run_stdio(self):
        def fdinfo(path):
            fd = int(path.name)
            return ("flags:\t" + oct(os.O_RDONLY if fd == 0 else os.O_WRONLY)[2:] + "\n").encode()

        with (
            patch.object(os, "O_ACCMODE", 3, create=True),
            patch.object(scope, "_read", side_effect=fdinfo),
            patch.object(
                scope, "_stat", side_effect=lambda pid: (self.processes[pid]["start_ticks"], 1, "T")
            ),
        ):
            return self.observer.stdio(self.processes, self.tables, self.origin)

    def test_orphan_pipes_have_exactly_one_owner_and_original_inode(self):
        helpers, orphans = self.run_stdio()
        self.assertEqual((len(helpers), len(orphans)), (27, 9))
        self.assertTrue(all(len(row["owners"]) == 1 for row in orphans))

    def test_pipe_replacement_refused(self):
        self.tables[scope.ORPHANS[0]][0] = "pipe:[99999999]"
        with self.assertRaisesRegex(io.Refusal, "stdio_endpoint_changed"):
            self.run_stdio()

    def test_external_process_with_old_pipe_is_not_adopted(self):
        self.processes[999] = {"start_ticks": 1}
        self.tables[999] = {0: self.tables[scope.ORPHANS[0]][0]}
        with self.assertRaisesRegex(io.Refusal, "orphan_pipe_external_owner"):
            self.run_stdio()

    def test_disappearing_first_fd_cannot_hide_second_unknown_pipe_owner(self):
        self.processes[999] = {"start_ticks": 1}
        self.tables[999] = {
            10: self.tables[scope.ORPHANS[0]][0],
            11: self.tables[scope.ORPHANS[0]][1],
        }

        def fdinfo(path):
            if path.parts[-3] == "999" and path.name == "10":
                raise FileNotFoundError
            return ("flags:\t" + ("0" if path.name == "0" else "1") + "\n").encode()

        with (
            patch.object(os, "O_ACCMODE", 3, create=True),
            patch.object(scope, "_read", side_effect=fdinfo),
            patch.object(
                scope, "_stat", side_effect=lambda pid: (self.processes[pid]["start_ticks"], 1, "T")
            ),
            self.assertRaisesRegex(io.Refusal, "orphan_pipe_owner_unstable"),
        ):
            self.observer.stdio(self.processes, self.tables, self.origin)

    def test_helper_pipe_must_retain_exact_daemon_endpoint(self):
        helper = next(
            row for row in self.origin["observation"]["stdio"] if row["pid"] in scope.HELPERS
        )
        self.tables[scope.DAEMON].pop(helper["parent_fd"])
        with self.assertRaisesRegex(io.Refusal, "helper_stdio_parent_changed"):
            self.run_stdio()


class KernelBoundaryTests(unittest.TestCase):
    def process_rows(self):
        return {
            pid: {"start_ticks": scope.STARTS[pid], "ppid": scope.PARENTS[pid]}
            for pid in scope.FREEZE + scope.TRANSPORTS
        }

    def test_retired_ssh_reappearing_is_never_adopted(self):
        rows = self.process_rows()
        rows[scope.RETIRED[0]] = {"start_ticks": 9999999, "ppid": 1}
        observer = scope.LinuxObserver()
        observer.processes = lambda: rows
        with self.assertRaisesRegex(io.Refusal, "retired_transport_present"):
            observer.snapshot(origin={})

    def test_missing_old_writer_refuses_before_any_replacement(self):
        rows = self.process_rows()
        rows.pop(scope.ORPHANS[0])
        observer = scope.LinuxObserver()
        observer.processes = lambda: rows
        with self.assertRaisesRegex(io.Refusal, "actor_missing"):
            observer.snapshot(origin={})

    def test_extra_daemon_child_refused_before_program_reads(self):
        rows = self.process_rows()
        rows[2222222] = {"start_ticks": 9999999, "ppid": scope.DAEMON}
        observer = scope.LinuxObserver()
        observer.processes = lambda: rows
        with self.assertRaisesRegex(io.Refusal, "descendants_changed"):
            observer.snapshot(origin={})

    def test_origin_receipts_are_read_and_bound_without_mutation(self):
        _native, origin, _observed = fixture()
        ref = {**scope.ORIGIN_SCOPE_REF, "sha256": io.sha(io.canonical(origin))}
        docs = {
            ref["path"]: origin,
            scope.ORIGIN_REFS["plan"]["path"]: {"attempt_id": scope.ORIGIN_ATTEMPT},
            scope.ORIGIN_REFS["closure"]["path"]: {"process_scope_ref": ref},
            scope.ORIGIN_REFS["failure"]["path"]: {"plan_ref": scope.ORIGIN_REFS["plan"]},
        }
        with (
            patch.object(scope, "ORIGIN_SCOPE_REF", ref),
            patch.object(
                io, "referenced", side_effect=lambda item: io.canonical(docs[item["path"]])
            ) as read,
        ):
            scope._read_origin(origin, ref, scope.ORIGIN_REFS)
            self.assertEqual(read.call_count, 4)
            docs[scope.ORIGIN_REFS["failure"]["path"]]["plan_ref"] = ref
            with self.assertRaisesRegex(io.Refusal, "origin_document_binding"):
                scope._read_origin(origin, ref, scope.ORIGIN_REFS)

    def test_socket_accepted_after_final_ss_is_refused(self):
        initial = {pid: {} for pid in scope.FREEZE + scope.TRANSPORTS}
        initial[scope.DAEMON][31] = "socket:[123]"
        final = copy.deepcopy(initial)
        final[scope.DAEMON][32] = "socket:[456]"
        observer = scope.LinuxObserver()
        observer.descriptors = lambda pid: final[pid]
        with self.assertRaisesRegex(io.Refusal, "daemon_socket_set_changed"):
            observer.finish_stdio({}, initial, {}, [], [], [])

    def test_socket_accepted_during_final_pipe_scan_is_refused(self):
        initial = {pid: {} for pid in scope.FREEZE + scope.TRANSPORTS}
        initial[scope.DAEMON][31] = "socket:[123]"
        final = copy.deepcopy(initial)
        observer = scope.LinuxObserver()
        observer.descriptors = lambda pid: final[pid]

        def pipe_scan(*_args):
            final[scope.DAEMON][32] = "socket:[456]"
            return [], []

        observer.stdio = pipe_scan
        with self.assertRaisesRegex(io.Refusal, "daemon_socket_set_changed"):
            observer.finish_stdio({}, initial, {}, [], [], [])

    def test_connection_queued_during_final_pipe_scan_is_refused(self):
        initial = {pid: {} for pid in scope.FREEZE + scope.TRANSPORTS}
        initial[scope.DAEMON][31] = "socket:[123]"
        before = b"u_str LISTEN 0 4096 /root/private.sock 123 * 0\n"
        after = b"u_str LISTEN 1 4096 /root/private.sock 123 * 0\n"
        observer = scope.LinuxObserver()
        observer.descriptors = lambda pid: initial[pid]
        observer.stdio = lambda *_args: ([], [])
        observer.command = lambda _args: after
        with self.assertRaisesRegex(io.Refusal, "socket_topology_changed_during_scan"):
            observer.finish_stdio({}, initial, {}, [], [], scope._parse_ss(before))


if __name__ == "__main__":
    unittest.main()
