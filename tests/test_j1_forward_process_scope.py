"""Synthetic cohort tests; no production reads, signals, or service effects."""

from __future__ import annotations

import copy
import errno
import os
import stat
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_io as io
from scripts import j1_forward_process_scope as scope

ATTEMPT = "7767b188-209a-49a3-8aac-854cf2d4336f"
DIRECTORY = "/var/lib/autostop-crm-forward/attempts/" + ATTEMPT


def actor(pid):
    return {
        "pid": pid,
        "start_ticks": scope.STARTS[pid],
        "exe": "/root/codex",
        "exe_sha256": scope._program_sha(pid),
        "exe_identity": [1, pid],
        "argv_sha256": scope.ARGV[scope._kind(pid)],
        "cgroup": scope.CGROUPS[pid],
        "uid": 996 if pid == 698406 else 0,
        "mount_namespace": "mnt:[123]",
    }


def fixture():
    actors = {str(pid): actor(pid) for pid in scope.FREEZE + scope.TRANSPORTS}
    profile = {
        "exe": "/usr/bin/python3.12",
        "exe_sha256": "c" * 64,
        "argv_sha256": "d" * 64,
        "credentials": [0, 0, []],
    }
    peer = {
        "actor": {"pid": 762195, "start_ticks": 11218504},
        "profile": profile,
        "inode": 10,
        "daemon_inode": 11,
    }
    observed = {
        "boot_id": "c2d6447f-ae85-43f9-bc17-a295094a06dd",
        "actors": actors,
        "thread_states": {str(pid): ["S", "S"] for pid in scope.FREEZE + scope.TRANSPORTS},
        "native_control_peer": peer,
        "pending_ingress": False,
        "observation": {
            "roles": {
                str(pid): scope._role(pid) for pid in sorted(scope.FREEZE + scope.TRANSPORTS)
            },
            "details": {},
            "programs": {},
            "entrypoints": {},
            "stdio": [],
            "proxy_peers": [],
            "daemon_listener": {},
            "confined_tunnel": {},
        },
    }
    native = {
        "daemon_mode": "accepted_warm",
        "base_sha256": "e" * 64,
        "warm_actors": [actor(pid) for pid in scope.ORIGINAL],
        "profiles": {
            scope.NATIVE_UNIT: {
                "interpreter": "/usr/bin/python3",
                "interpreter_realpath": "/usr/bin/python3.12",
                "interpreter_sha256": "c" * 64,
                "module": "autostop_manager.codex_wake",
                "arguments": [],
                "credentials": [0, 0, []],
            }
        },
    }
    return native, observed


class ProcessScopeTests(unittest.TestCase):
    def setUp(self):
        self.native, self.observed = fixture()
        self.observer = Mock()
        self.observer.snapshot.side_effect = lambda _profile=None: copy.deepcopy(self.observed)
        self.replace = patch.object(scope, "OBSERVER", self.observer)
        self.replace.start()
        self.addCleanup(self.replace.stop)
        self.bound = scope.capture(
            self.native, attempt_id=ATTEMPT, attempt_dir=DIRECTORY, consumer_sha256="f" * 64
        )

    def frozen(self):
        self.observed["native_control_peer"] = None
        for pid in scope.FREEZE:
            self.observed["thread_states"][str(pid)] = ["T", "T"]

    def activation(self):
        for pid in scope.FREEZE:
            self.observed["thread_states"][str(pid)] = ["S"] if pid == scope.DAEMON else ["T"]
        self.observed["native_control_peer"]["actor"]["pid"] = 1000001
        self.observed["native_control_peer"]["actor"]["start_ticks"] += 50000

    def test_capture_keeps_original_three_separate_and_daemon_first(self):
        self.assertEqual(
            [row["pid"] for row in self.bound["original_actors"]], list(scope.ORIGINAL)
        )
        writers, transports = scope.actor_sets(self.bound)
        self.assertEqual([row["pid"] for row in writers], list(scope.FREEZE))
        self.assertEqual([row["pid"] for row in transports], list(scope.TRANSPORTS))
        self.assertEqual((len(writers), len(transports)), (13, 5))
        self.assertEqual(writers[0]["pid"], scope.DAEMON)

    def test_pure_shape_validation_never_observes_host(self):
        self.observer.reset_mock()
        scope.validate_scope_shape(self.bound)
        self.observer.snapshot.assert_not_called()

    def test_current_profile_is_pure_and_hash_bound(self):
        self.assertEqual(scope.PROFILE_SHA256, io.sha(io.canonical(scope.PROFILE)))

    def test_unknown_scope_fields_refused(self):
        self.bound["trusted"] = True
        with self.assertRaisesRegex(io.Refusal, "scope_shape"):
            scope.actor_sets(self.bound)

    def test_changed_profile_refused(self):
        self.bound["profile_sha256"] = "0" * 64
        with self.assertRaisesRegex(io.Refusal, "scope_shape"):
            scope.actor_sets(self.bound)

    def test_static_shape_refuses_wrong_role_even_without_host_read(self):
        self.bound["freeze_actors"][-1]["argv_sha256"] = "0" * 64
        self.observer.reset_mock()
        with self.assertRaisesRegex(io.Refusal, "actor_shape"):
            scope.actor_sets(self.bound)
        self.observer.snapshot.assert_not_called()

    def test_different_attempt_directory_refused(self):
        self.bound["attempt_dir"] = DIRECTORY + "/../elsewhere"
        with self.assertRaisesRegex(io.Refusal, "attempt_binding"):
            scope.actor_sets(self.bound)

    def test_uuid_alias_refused(self):
        self.bound["attempt_id"] = ATTEMPT.upper()
        with self.assertRaisesRegex(io.Refusal, "attempt_binding"):
            scope.actor_sets(self.bound)

    def test_native_authority_replacement_refused_at_capture(self):
        self.native["warm_actors"][0]["argv_sha256"] = "0" * 64
        with self.assertRaisesRegex(io.Refusal, "original_actor_changed"):
            scope.capture(
                self.native, attempt_id=ATTEMPT, attempt_dir=DIRECTORY, consumer_sha256="f" * 64
            )

    def test_native_peer_required_at_capture(self):
        self.observed["native_control_peer"] = None
        with self.assertRaisesRegex(io.Refusal, "capture_native_peer_missing"):
            scope.capture(
                self.native, attempt_id=ATTEMPT, attempt_dir=DIRECTORY, consumer_sha256="f" * 64
            )

    def test_preexisting_stop_never_becomes_owned(self):
        self.observed["thread_states"][str(scope.FREEZE[-1])] = ["T"]
        with self.assertRaisesRegex(io.Refusal, "unexpected_stopped_actor"):
            scope.capture(
                self.native, attempt_id=ATTEMPT, attempt_dir=DIRECTORY, consumer_sha256="f" * 64
            )

    def test_complete_frozen_evidence(self):
        self.frozen()
        result = scope.verify(self.bound, frozen=True)
        self.assertEqual(result["scope_sha256"], io.sha(io.canonical(self.bound)))
        self.assertTrue(
            all(
                row["stopped"] and set(row["thread_states"]) == {"T"}
                for row in result["freeze_actors"]
            )
        )
        self.assertTrue(all(not row["stopped"] for row in result["transport_actors"]))
        self.assertIsNone(result["native_control_peer"])

    def test_one_running_thread_refuses_closure(self):
        self.frozen()
        self.observed["thread_states"][str(scope.DAEMON)] = ["T", "R"]
        with self.assertRaisesRegex(io.Refusal, "writer_not_stopped"):
            scope.verify(self.bound, frozen=True)

    def test_queued_proxy_request_refuses_frozen_acceptance(self):
        self.frozen()
        self.observed["pending_ingress"] = True
        with self.assertRaisesRegex(io.Refusal, "queued_proxy_ingress"):
            scope.verify(self.bound, frozen=True)

    def test_queued_proxy_request_refuses_daemon_only_activation(self):
        self.activation()
        self.observed["pending_ingress"] = True
        with self.assertRaisesRegex(io.Refusal, "queued_proxy_ingress"):
            scope.verify_activation(self.bound)

    def test_no_thread_evidence_refused(self):
        self.frozen()
        self.observed["thread_states"][str(scope.DAEMON)] = []
        with self.assertRaisesRegex(io.Refusal, "thread_states"):
            scope.verify(self.bound, frozen=True)

    def test_transport_must_remain_live(self):
        self.frozen()
        self.observed["thread_states"]["698406"] = ["T"]
        with self.assertRaisesRegex(io.Refusal, "unexpected_stopped_actor"):
            scope.verify(self.bound, frozen=True)

    def test_native_peer_cannot_remain_while_closed(self):
        for pid in scope.FREEZE:
            self.observed["thread_states"][str(pid)] = ["T"]
        with self.assertRaisesRegex(io.Refusal, "native_control_peer_changed"):
            scope.verify(self.bound, frozen=True)

    def test_before_restart_original_native_peer_can_disappear(self):
        self.observed["native_control_peer"] = None
        scope.verify(self.bound, frozen=False)

    def test_before_restart_replacement_native_peer_not_adopted(self):
        self.observed["native_control_peer"]["actor"]["pid"] += 1
        with self.assertRaisesRegex(io.Refusal, "native_control_peer_changed"):
            scope.verify(self.bound, frozen=False)

    def test_birth_executable_namespace_or_credential_change_refused(self):
        for key, value in (
            ("start_ticks", 123),
            ("exe_sha256", "0" * 64),
            ("mount_namespace", "mnt:[999]"),
            ("uid", 1000),
            ("argv_sha256", "0" * 64),
            ("exe_identity", [9, 9]),
        ):
            with self.subTest(key=key):
                before = copy.deepcopy(self.observed)
                self.observed["actors"]["699029"][key] = value
                with self.assertRaisesRegex(io.Refusal, "actor_identity_changed"):
                    scope.verify(self.bound, frozen=False)
                self.observed = before

    def test_changed_parent_pipe_peer_module_or_policy_refused(self):
        for key in ("details", "stdio", "proxy_peers", "entrypoints", "confined_tunnel"):
            with self.subTest(key=key):
                before = copy.deepcopy(self.observed)
                self.observed["observation"][key] = {"changed": True}
                with self.assertRaisesRegex(io.Refusal, "topology_or_provenance_changed"):
                    scope.verify(self.bound, frozen=False)
                self.observed = before

    def test_boot_change_refused(self):
        self.observed["boot_id"] = "new-boot"
        with self.assertRaisesRegex(io.Refusal, "boot_changed"):
            scope.verify(self.bound, frozen=False)

    def test_extra_actor_cannot_be_added_to_stored_cohort(self):
        self.bound["freeze_actors"].append(copy.deepcopy(self.bound["freeze_actors"][-1]))
        with self.assertRaisesRegex(io.Refusal, "actor_set"):
            scope.actor_sets(self.bound)

    def test_frozen_requires_actual_boolean(self):
        with self.assertRaisesRegex(io.Refusal, "frozen_boolean"):
            scope.verify(self.bound, frozen=1)

    def test_staged_activation_only_daemon_runs(self):
        self.activation()
        result = scope.verify_activation(self.bound)
        self.assertFalse(result["auxiliary_released"])
        self.assertFalse(result["freeze_actors"][0]["stopped"])
        self.assertTrue(all(row["stopped"] for row in result["freeze_actors"][1:]))
        self.assertEqual(result["native_control_peer"]["actor"]["pid"], 1000001)

    def test_pre_stop_staging_requires_native_absent_and_auxiliary_stopped(self):
        self.activation()
        self.observed["native_control_peer"] = None
        result = scope.verify_staging(self.bound)
        self.assertEqual(result["schema"], "autostop.j1.crm-forward-process-scope-staging.v1")
        self.assertIsNone(result["native_control_peer"])
        self.assertTrue(all(row["stopped"] for row in result["freeze_actors"][1:]))

    def test_pre_stop_staging_refuses_still_running_native_peer(self):
        self.activation()
        with self.assertRaisesRegex(io.Refusal, "staging_native_peer_present"):
            scope.verify_staging(self.bound)

    def test_pre_stop_staging_refuses_buffered_kernel_ingress(self):
        self.activation()
        self.observed["native_control_peer"] = None
        self.observed["pending_ingress"] = True
        with self.assertRaisesRegex(io.Refusal, "queued_proxy_ingress"):
            scope.verify_staging(self.bound)

    def test_activation_refuses_running_auxiliary(self):
        self.activation()
        self.observed["thread_states"]["699029"] = ["S"]
        with self.assertRaisesRegex(io.Refusal, "activation_actor_state"):
            scope.verify_activation(self.bound)

    def test_activation_refuses_stopped_daemon(self):
        self.activation()
        self.observed["thread_states"][str(scope.DAEMON)] = ["T"]
        with self.assertRaisesRegex(io.Refusal, "activation_actor_state"):
            scope.verify_activation(self.bound)

    def test_activation_refuses_foreign_native_profile(self):
        self.activation()
        self.observed["native_control_peer"]["profile"]["exe_sha256"] = "0" * 64
        with self.assertRaisesRegex(io.Refusal, "activation_native_peer_required"):
            scope.verify_activation(self.bound)

    def test_activation_requires_guarded_native_peer(self):
        self.activation()
        self.observed["native_control_peer"] = None
        with self.assertRaisesRegex(io.Refusal, "activation_native_peer_required"):
            scope.verify_activation(self.bound)

    def test_postcommit_release_requires_all_writers_live(self):
        self.activation()
        with self.assertRaisesRegex(io.Refusal, "activation_actor_state"):
            scope.verify_released(self.bound)
        for pid in scope.FREEZE:
            self.observed["thread_states"][str(pid)] = ["S"]
        self.assertTrue(scope.verify_released(self.bound)["auxiliary_released"])


class KernelMetadataTests(unittest.TestCase):
    def test_late_accepted_daemon_socket_cannot_escape_original_peer_filter(self):
        original = {7: "socket:[123]", 8: "socket:[456]", 9: "pipe:[12]"}
        accepted = {**original, 10: "socket:[789]"}
        with self.assertRaisesRegex(io.Refusal, "daemon_socket_set_changed"):
            scope._same_daemon_sockets(original, accepted)
        scope._same_daemon_sockets(original, dict(original))

    def test_established_proxy_kernel_queue_derivation(self):
        for daemon_recv, daemon_send, proxy_recv, proxy_send in (
            (0, 0, 0, 0),
            (4, 0, 0, 0),
            (0, 5, 0, 0),
            (0, 0, 6, 0),
            (0, 0, 0, 7),
        ):
            with self.subTest(queues=(daemon_recv, daemon_send, proxy_recv, proxy_send)):
                rows = scope._parse_ss(
                    (
                        f"u_str ESTAB {daemon_recv} {daemon_send} /tmp/private/socket 123 * 456\n"
                        f"u_str ESTAB {proxy_recv} {proxy_send} * 456 * 123\n"
                    ).encode()
                )
                self.assertEqual(
                    scope._proxy_pending(rows, rows[0]),
                    any((daemon_recv, daemon_send, proxy_recv, proxy_send)),
                )

    def test_proxy_reciprocal_must_name_exact_same_daemon_inode(self):
        rows = scope._parse_ss(
            b"u_str ESTAB 0 0 /tmp/private/socket 123 * 456\nu_str ESTAB 0 0 * 456 * 999\n"
        )
        with self.assertRaisesRegex(io.Refusal, "proxy_reciprocal_peer"):
            scope._proxy_pending(rows, rows[0])

    def test_socket_parser_preserves_numeric_kernel_peer_without_payload(self):
        rows = scope._parse_ss(b"u_str ESTAB 0 0 /tmp/private/socket 123 * 456\n")
        self.assertEqual(rows[0]["local"], 123)
        self.assertEqual(rows[0]["peer"], 456)

    def test_malformed_socket_metadata_is_refused(self):
        with self.assertRaisesRegex(io.Refusal, "socket_table_shape"):
            scope._parse_ss(b"u_str ESTAB 0 0 * nope * 456\n")

    def test_acl_named_user_obeys_mask(self):
        raw = struct.pack("<I", 2) + b"".join(
            struct.pack("<HHI", *row)
            for row in [
                (1, 7, 0xFFFFFFFF),
                (2, 7, 996),
                (4, 0, 0xFFFFFFFF),
                (16, 5, 0xFFFFFFFF),
                (32, 0, 0xFFFFFFFF),
            ]
        )
        path = Mock()
        path.stat.return_value = SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFDIR | 0o750)
        with patch.object(os, "getxattr", return_value=raw, create=True):
            bits, digest = scope.LinuxObserver.permissions(path, 996, {988})
        self.assertEqual(bits, 5)
        self.assertEqual(digest, io.sha(raw))

    def test_dac_no_acl_does_not_grant_owner_permissions_to_tunnel(self):
        path = Mock()
        path.stat.return_value = SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFDIR | 0o700)
        error = OSError(errno.ENODATA, "no acl")
        with patch.object(os, "getxattr", side_effect=error, create=True):
            self.assertEqual(scope.LinuxObserver.permissions(path, 996, {988}), (0, None))

    def test_reachable_writable_source_directory_refused(self):
        observer = scope.LinuxObserver()
        observer.deadline = float("inf")
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(scope, "WRITER_ROOTS", (temporary,)),
        ):
            with patch.object(observer, "permissions", return_value=(7, None)):
                with self.assertRaisesRegex(io.Refusal, "tunnel_source_write_directory"):
                    observer.source_access(996, {988})

    def test_reachable_writable_source_file_refused(self):
        observer = scope.LinuxObserver()
        observer.deadline = float("inf")
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(scope, "WRITER_ROOTS", (temporary,)),
        ):
            file = Path(temporary) / "state.db"
            file.write_bytes(b"synthetic")
            with patch.object(
                observer, "permissions", side_effect=lambda p, *_args: (6 if p == file else 5, None)
            ):
                with self.assertRaisesRegex(io.Refusal, "tunnel_source_write_entry"):
                    observer.source_access(996, {988})


if __name__ == "__main__":
    unittest.main()
