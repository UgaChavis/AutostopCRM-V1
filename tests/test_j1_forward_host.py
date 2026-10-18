from __future__ import annotations

import datetime
import stat
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_host as host
from scripts import j1_forward_runtime as runtime
from scripts.j1_forward_io import Refusal, canonical, sha


def work_status(*, enabled=True):
    return {
        "ok": True,
        "status": {
            "enabled": enabled,
            "execution_paused": False,
            "pending_events": 1,
            "active_jobs": 1,
            "needs_review_jobs": 1,
            "needs_review_events": 1,
            "gap_count": 0,
        },
    }


def scheduler_status():
    return {
        "ok": True,
        "data": {
            "global_hold": {"enabled": False},
            "jobs": [{"lease_until": None}],
            "readiness": {
                "execution_packet": {
                    "manager_revision": host.MANAGER_REVISION,
                    "crm": {"revision": "a" * 40, "version": "0.9.0"},
                    "outbox": {"by_status": {}, "total_count": 0},
                }
            },
        },
    }


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.native = {
            "sha256": "b" * 64,
            "legacy_crm_revision": "a" * 40,
            "manager_root": "/sealed/manager",
            "desired_inbound": True,
            "warm_actors": [
                {
                    "pid": 42,
                    "start_ticks": 123,
                    "argv_sha256": host.APPSERVER_ARGV_SHA,
                    "exe": host.APPSERVER_EXE,
                    "uid": 0,
                    "cgroup": "0::/accepted.scope",
                    "mount_namespace": "mnt:[1]",
                }
            ],
            "profiles": {
                host.WORK_UNIT: {"credentials": [1001, 1001, [1001]]},
                host.WAKE_UNIT: {"credentials": [0, 0, [0]]},
            },
        }
        self.instance = host.Host(self.native, self.directory)

    def socket_call(self, unit, path, mode, *, peer_uid=None):
        uid = self.native["profiles"][unit]["credentials"][0]
        info = SimpleNamespace(st_mode=stat.S_IFSOCK | mode, st_uid=uid, st_dev=1, st_ino=2)
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.getsockopt.return_value = struct.pack(
            "3i", 42, uid if peer_uid is None else peer_uid, uid
        )
        connection.recv.return_value = canonical({"ok": True})
        with (
            patch.object(host.Path, "lstat", return_value=info),
            patch.object(host.Path, "stat", return_value=info),
            patch.object(host.socket, "socket", return_value=connection),
            patch.object(host.socket, "AF_UNIX", 1, create=True),
            patch.object(host.socket, "SO_PEERCRED", 17, create=True),
            patch.object(self.instance, "run", return_value=b"42\n"),
        ):
            operation = "job_status" if unit == host.WORK_UNIT else "status"
            return self.instance.local_request(unit, path, {"operation": operation}), connection

    def test_real_snapshot_credentials_and_distinct_socket_modes(self):
        for unit, path, mode in (
            (host.WORK_UNIT, host.WORK_SOCKET, 0o600),
            (host.WAKE_UNIT, host.WAKE_SOCKET, 0o660),
        ):
            with self.subTest(unit=unit):
                result, connection = self.socket_call(unit, path, mode)
                self.assertTrue(result["ok"])
                connection.sendall.assert_called_once()
        with self.assertRaisesRegex(Refusal, "socket_changed"):
            self.socket_call(host.WORK_UNIT, host.WORK_SOCKET, 0o660)
        with self.assertRaisesRegex(Refusal, "peer_changed"):
            self.socket_call(host.WORK_UNIT, host.WORK_SOCKET, 0o600, peer_uid=5)

    def test_pause_uses_monitor_enabled_then_serialized_status(self):
        with (
            patch.object(
                self.instance,
                "local_request",
                return_value={"ok": True, "monitor": {"enabled": False}},
            ),
            patch.object(
                self.instance, "job_status", return_value=work_status(enabled=False)
            ) as status,
            patch.object(
                self.instance, "_save", return_value={"path": "/private/proof", "sha256": "c" * 64}
            ),
        ):
            result = self.instance.pause_work()
        status.assert_called_once()
        self.assertTrue(result["paused"])
        self.assertEqual(1, result["needs_review_jobs"])
        self.assertFalse(result["outbound_queue_count_known"])
        self.assertTrue(result["serialized_mutation_barrier_passed"])

    def test_pause_requires_real_readback_and_does_not_infer_zero_outbound(self):
        with (
            patch.object(
                self.instance,
                "local_request",
                return_value={"ok": True, "monitor": {"enabled": False}},
            ),
            patch.object(self.instance, "job_status", return_value=work_status(enabled=True)),
        ):
            with self.assertRaisesRegex(Refusal, "pause_readback"):
                self.instance.pause_work()
        with self.assertRaisesRegex(Refusal, "counts_unknown"):
            host.work_counts({"ok": True, "status": {"enabled": False}})

    def test_capture_freezes_scheduler_version_and_private_provenance(self):
        with (
            patch.object(self.instance, "json", return_value={"ok": True}) as command,
            patch.object(self.instance, "manager", return_value=scheduler_status()),
            patch.object(self.instance, "job_status", return_value=work_status()),
            patch.object(
                self.instance,
                "_save",
                side_effect=lambda label, value: {"path": "/private/" + label, "sha256": "c" * 64},
            ),
            patch.object(
                host,
                "reference",
                return_value={
                    "path": str(self.directory / "coordinated/manifest.json"),
                    "sha256": "d" * 64,
                },
            ),
        ):
            baseline = self.instance.capture_baseline()
        self.assertEqual("0.9.0", baseline["scheduler_identity"]["crm"]["version"])
        self.assertEqual("a" * 40, baseline["scheduler_identity"]["crm"]["revision"])
        self.assertEqual(2, command.call_count)
        self.assertEqual(1, baseline["work"]["needs_review_jobs"])
        self.assertNotIn("jobs", baseline)

    def test_validate_manager_uses_existing_path_contract_and_frozen_identity(self):
        baseline = {
            "scheduler_identity": {"crm": {"revision": "a" * 40, "version": "original"}},
            "coordinated_snapshot": str(self.directory / "coordinated"),
        }
        proof = self.directory / "status.json"
        with (
            patch.object(self.instance, "_baseline", return_value=baseline),
            patch.object(
                self.instance, "_save", return_value={"path": str(proof), "sha256": "c" * 64}
            ),
            patch(
                "scripts.check_automation_center_release.validate_manager",
                return_value={"ok": True},
            ) as validate,
        ):
            self.instance.validate_manager(scheduler_status(), "attempt", held=True)
        self.assertEqual(proof, validate.call_args.args[0])
        self.assertEqual("original", validate.call_args.kwargs["crm_version"])
        self.assertEqual(
            self.directory / "coordinated", validate.call_args.kwargs["baseline_snapshot"]
        )

    def test_metadata_rpc_is_pinned_strict_idle_and_never_exposes_thread_id(self):
        raw = b"""class RPC:
    def __init__(self, path, deadline): self.peer = (42, 0, 0)
    def close(self): pass
def metadata(client, thread_id, cwd, *, cold):
    assert cold is True
    return {"loaded_count": 7, "thread_id": thread_id, "metadata_only": True}
"""
        wake = canonical({"thread_id": "private-thread-identity", "project_dir": "/private/cwd"})
        with (
            patch.object(host, "RPC_SHA", sha(raw)),
            patch.object(host, "WAKE_SHA", sha(wake)),
            patch.object(
                host,
                "read_protected",
                side_effect=lambda path: raw if path == host.RPC_PATH else wake,
            ),
            patch.object(host, "verify_appserver_peer") as verify,
        ):
            result = self.instance.observe_idle_appserver()
        self.assertEqual(2, verify.call_count)
        self.assertEqual((42, 0, 0), verify.call_args.args[0])
        self.assertEqual(0, result["active_turns"])
        self.assertEqual(7, result["loaded_count"])
        self.assertNotIn("private", canonical(result).decode())
        with patch.object(
            host, "read_protected", return_value=b"raise RuntimeError('must not execute')"
        ):
            with self.assertRaisesRegex(Refusal, "rpc_code_changed"):
                self.instance.observe_idle_appserver()

    def test_metadata_selects_only_unique_pinned_appserver_not_actor_order(self):
        actor = self.native["warm_actors"][0]
        other_codex = {**actor, "pid": 43, "argv_sha256": "c" * 64}
        self.native["warm_actors"] = [other_codex, actor]
        self.assertIs(actor, host.appserver_actor(self.native))
        self.native["warm_actors"].append(dict(actor))
        with self.assertRaisesRegex(Refusal, "daemon_identity_unknown"):
            host.appserver_actor(self.native)
        self.native["warm_actors"] = [other_codex]
        with self.assertRaisesRegex(Refusal, "daemon_identity_unknown"):
            host.appserver_actor(self.native)

    def test_metadata_rejects_other_accepted_actor_before_rpc_read(self):
        actor = self.native["warm_actors"][0]
        self.native["warm_actors"].append({**actor, "pid": 43, "argv_sha256": "c" * 64})
        raw = b"""class RPC:
    def __init__(self, path, deadline): self.peer = (43, 0, 0)
    def close(self): pass
def metadata(*args, **kwargs): raise AssertionError('must not send metadata RPC')
"""
        wake = canonical({"thread_id": "private-thread-identity", "project_dir": "/private/cwd"})
        with (
            patch.object(host, "RPC_SHA", sha(raw)),
            patch.object(host, "WAKE_SHA", sha(wake)),
            patch.object(
                host,
                "read_protected",
                side_effect=lambda path: raw if path == host.RPC_PATH else wake,
            ),
        ):
            with self.assertRaisesRegex(Refusal, "metadata_peer_changed"):
                self.instance.observe_idle_appserver()

    def test_metadata_rechecks_birth_and_full_daemon_identity(self):
        actor = {**self.native["warm_actors"][0], "argv_sha256": sha(b"accepted-argv\0")}
        contents = {
            "stat": b"42 (name with ) brackets) S " + b"0 " * 18 + b"123 0\n",
            "cmdline": b"accepted-argv\0",
            "cgroup": b"0::/accepted.scope\n",
        }
        with (
            patch.object(
                host.Path, "read_bytes", autospec=True, side_effect=lambda path: contents[path.name]
            ),
            patch.object(
                host.os,
                "readlink",
                side_effect=lambda path: actor["exe"] if path.name == "exe" else "mnt:[1]",
            ),
        ):
            host.verify_appserver_peer((42, 0, 0), actor)
            contents["stat"] = contents["stat"].replace(b"123", b"124")
            with self.assertRaisesRegex(Refusal, "metadata_daemon_changed"):
                host.verify_appserver_peer((42, 0, 0), actor)
            contents["stat"] = contents["stat"].replace(b"124", b"123")
            contents["cmdline"] = b"different-argv\0"
            with self.assertRaisesRegex(Refusal, "metadata_daemon_changed"):
                host.verify_appserver_peer((42, 0, 0), actor)

    def test_start_native_requires_exact_scope_and_preserves_failure_counters(self):
        from scripts.j1_forward_authority import ALL_UNITS, NATIVE_UNITS

        self.native["desired_units"] = {
            unit: {"active": unit in NATIVE_UNITS} for unit in ALL_UNITS
        }
        with patch.object(self.instance, "run") as run:
            self.instance.start_native()
        run.assert_called_once_with(
            ["/usr/bin/systemctl", "start", *NATIVE_UNITS], label="start-native", timeout=90
        )
        for desired in (
            {},
            {unit: {"active": False} for unit in ALL_UNITS},
            {unit: {"active": True} for unit in ALL_UNITS},
        ):
            self.native["desired_units"] = desired
            with patch.object(self.instance, "run") as run:
                with self.assertRaisesRegex(Refusal, "unsupported_native_activation"):
                    self.instance.start_native()
            run.assert_not_called()

    def test_drain_rejects_unknown_wake_before_metadata(self):
        baseline = {"safe": "fixture"}
        with (
            patch.object(self.instance, "_baseline", return_value=baseline),
            patch.object(self.instance, "manager", return_value=scheduler_status()),
            patch.object(
                self.instance,
                "_save",
                return_value={"path": str(self.directory / "held"), "sha256": "c" * 64},
            ),
            patch(
                "scripts.check_automation_center_release.validate_hold", return_value={"ok": True}
            ),
            patch.object(self.instance, "validate_manager", return_value={"ok": True}),
            patch.object(self.instance, "pause_work", return_value={"paused": True}),
            patch.object(
                self.instance,
                "local_request",
                return_value={"ok": True, "connected": True, "active": False},
            ),
            patch.object(self.instance, "observe_idle_appserver") as observe,
        ):
            with self.assertRaisesRegex(Refusal, "wake_not_drained"):
                self.instance.begin_drain("attempt", baseline)
        observe.assert_not_called()

    def test_drain_retains_review_queue_but_refuses_active_lease_before_work_pause(self):
        for active in (False, True):
            with self.subTest(active=active):
                self.instance.host_baseline = None
                baseline = {"safe": "fixture"}
                status = scheduler_status()
                if active:
                    status["data"]["jobs"][0]["lease_until"] = (
                        datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=2)
                    ).isoformat()
                with (
                    patch.object(self.instance, "_baseline", return_value=baseline),
                    patch.object(self.instance, "manager", return_value=status),
                    patch.object(self.instance, "_save", return_value={"path": "/private/held"}),
                    patch(
                        "scripts.check_automation_center_release.validate_hold",
                        return_value={"ok": True},
                    ),
                    patch.object(self.instance, "validate_manager", return_value={"ok": True}),
                    patch.object(
                        self.instance,
                        "pause_work",
                        return_value={"paused": True, "needs_review_jobs": 1},
                    ) as pause,
                    patch.object(
                        self.instance,
                        "local_request",
                        return_value={
                            "ok": True,
                            "connected": True,
                            "active": False,
                            "outcome_unknown": False,
                            "queued": 1,
                            "needs_review": 1,
                            "execution_state": "held",
                        },
                    ),
                    patch.object(
                        self.instance, "observe_idle_appserver", return_value={"active_turns": 0}
                    ),
                ):
                    if active:
                        with self.assertRaisesRegex(Refusal, "scheduler_not_drained"):
                            self.instance.begin_drain("attempt", baseline)
                        pause.assert_not_called()
                    else:
                        result = self.instance.begin_drain("attempt", baseline)
                        self.assertEqual(1, result["work"]["needs_review_jobs"])
                        self.assertFalse(result["closure_continuity_proven"])


class SchedulerDrainTests(unittest.TestCase):
    def test_future_lease_and_sending_are_counted_and_unknown_is_rejected(self):
        value = scheduler_status()
        self.assertEqual(
            {"active_leases": 0, "outbox_sending": 0, "outbox_total": 0},
            host.scheduler_counts(value),
        )
        future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=2)
        value["data"]["jobs"][0]["lease_until"] = future.isoformat()
        value["data"]["readiness"]["execution_packet"]["outbox"]["by_status"]["sending"] = 2
        self.assertEqual(1, host.scheduler_counts(value)["active_leases"])
        self.assertEqual(2, host.scheduler_counts(value)["outbox_sending"])
        del value["data"]["jobs"][0]["lease_until"]
        with self.assertRaisesRegex(Refusal, "lease_unknown"):
            host.scheduler_counts(value)


class RuntimeOperatorTests(unittest.TestCase):
    def client(self, *, admin=True, read_status=200, logout_status=200, invalidated=True):
        calls = []
        logged_out = False

        def response(status=200, data=None):
            return runtime.Response(
                status, "application/json", canonical({"ok": True, "data": data or {}})
            )

        def request(url, **kwargs):
            nonlocal logged_out
            calls.append((url, kwargs))
            if url.endswith("/api/login_operator"):
                return response(
                    data={"session": {"token": "private-session-token", "is_admin": admin}}
                )
            if url.endswith("/api/logout_operator"):
                logged_out = True
                return response(logout_status, {"logged_out": True})
            if url.endswith("/api/get_operator_profile"):
                return response(
                    401 if logged_out and invalidated else 200, {"user": {"is_admin": admin}}
                )
            if url.endswith("/api/automation_center/status"):
                return response(
                    data={
                        "format": "crm_automation_center_status_v1",
                        "can_manage": True,
                        "controller": {"overall_state": "off", "error_count": 0},
                    }
                )
            return response(read_status)

        return SimpleNamespace(request=request), calls

    def environment(self):
        return {
            "AUTOSTOP_SMOKE_OPERATOR_USERNAME": "private-username",
            "AUTOSTOP_SMOKE_OPERATOR_PASSWORD": "private-password",
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "must-never-be-promoted",
        }

    def test_admin_session_reads_and_logout_are_confirmed_without_secrets(self):
        client, calls = self.client()
        result = runtime.operator_checks(client, "http://127.0.0.1:41731", self.environment())
        self.assertEqual(5, len(result["authorized_reads"]))
        self.assertTrue(result["logout_confirmed"])
        self.assertTrue(result["session_invalidated"])
        self.assertNotIn("private-", canonical(result).decode())
        self.assertTrue(
            all("Authorization" not in kwargs.get("headers", {}) for _, kwargs in calls)
        )
        self.assertTrue(calls[-2][0].endswith("/api/logout_operator"))

    def test_nonadmin_and_read_failure_still_close_temporary_session(self):
        for options, error in (
            ({"admin": False}, "not_admin"),
            ({"read_status": 500}, "api_read_failed"),
        ):
            with self.subTest(options=options):
                client, calls = self.client(**options)
                with self.assertRaisesRegex(runtime.ProbeError, error):
                    runtime.operator_checks(client, "http://127.0.0.1:41731", self.environment())
                self.assertTrue(any(url.endswith("/api/logout_operator") for url, _ in calls))
                self.assertTrue(calls[-1][0].endswith("/api/get_operator_profile"))

    def test_failed_logout_or_still_valid_session_fails_closed(self):
        for options in ({"logout_status": 500}, {"invalidated": False}):
            with self.subTest(options=options):
                client, _ = self.client(**options)
                with self.assertRaisesRegex(runtime.ProbeError, "cleanup_failed"):
                    runtime.operator_checks(client, "http://127.0.0.1:41731", self.environment())

    def test_missing_credentials_and_nonloopback_api_fail_before_http(self):
        with patch.object(runtime, "Client") as client:
            with self.assertRaisesRegex(runtime.ProbeError, "credentials_missing"):
                runtime.run_probe(
                    "https://crm.autostopcrm.ru", "http://127.0.0.1:41731", environment={}
                )
            with self.assertRaisesRegex(runtime.ProbeError, "loopback_required"):
                runtime.run_probe(
                    "https://crm.autostopcrm.ru",
                    "http://192.0.2.1:41731",
                    environment=self.environment(),
                )
        client.assert_not_called()

    def test_transport_does_not_follow_redirects_or_environment_proxy(self):
        with patch.object(runtime.urllib.request, "build_opener") as build:
            runtime.Client(10)
        handlers = build.call_args.args
        self.assertEqual({}, handlers[0].proxies)
        self.assertIsInstance(handlers[1], runtime.NoRedirect)
        self.assertIsNone(
            handlers[1].redirect_request(None, None, 302, "moved", {}, "https://elsewhere")
        )

    def test_unknown_or_failed_anonymous_guards_are_not_passes(self):
        script = b"fetch('/api/login_operator')"
        asset = "/assets/board." + sha(script) + ".js"
        replies = [
            runtime.Response(200, "application/json", canonical({"ok": True, "data": {}})),
            runtime.Response(200, "text/html", ('<script src="' + asset + '"></script>').encode()),
            runtime.Response(200, "application/javascript", script),
            runtime.Response(200, "application/json", b"{}"),
        ]
        client = SimpleNamespace(request=Mock(side_effect=replies))
        with self.assertRaisesRegex(runtime.ProbeError, "anonymous_guard_failed_public_api"):
            runtime.public_checks(client, "https://crm.autostopcrm.ru", "http://127.0.0.1:41731")


if __name__ == "__main__":
    unittest.main()
