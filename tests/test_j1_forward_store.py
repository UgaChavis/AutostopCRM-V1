from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import j1_forward_store as store
from scripts.j1_forward_io import Refusal, canonical

REVISION = "b" * 40
INSTANCE = "c" * 32
IDENTITY = {
    "pid": 42,
    "start_ticks": "1234",
    "boot_id": "11111111-2222-3333-4444-555555555555",
    "revision": REVISION,
    "invocation_id": "a" * 32,
    "source_root": "/sealed/manager",
}


def snapshot(*, accepting=True, pending=0, instance=INSTANCE):
    drained = not accepting and pending == 0
    return {
        "format": "store_worker_lifecycle_v1",
        "instance_id": instance,
        "pid": 42,
        "accepting": accepting,
        "status": "accepting" if accepting else "drained" if drained else "draining",
        "drained": drained,
        "pending": {"read": 0, "stateful": pending, "transport": 0},
    }


class DrainTests(unittest.TestCase):
    def setUp(self):
        self.identity = self.enterContext(
            patch.object(store, "_identity", return_value=dict(IDENTITY))
        )
        self.request = self.enterContext(patch.object(store, "_request"))
        self.enterContext(patch.object(store, "POLL_SECONDS", 0))
        self.deadline = time.monotonic() + 10
        self.command = Mock()
        self.native = {"manager_revision": REVISION}

    def run_drain(self):
        return store.drain_before_stop(self.native, deadline=self.deadline, command=self.command)

    def responses(self, *values):
        self.request.side_effect = [
            value if isinstance(value, Exception) else (value[0], canonical(value[1]))
            for value in values
        ]

    def test_waits_for_all_workers_and_posts_exactly_once(self):
        self.responses(
            (200, snapshot()),
            (202, snapshot(accepting=False, pending=1)),
            (200, snapshot(accepting=False)),
        )
        report = self.run_drain()
        self.assertEqual(report["mode"], "store_workers_drained")
        self.assertIs(report["stop_permitted"], True)
        self.assertIs(report["store_workers_drained"], True)
        self.assertIs(report["worker_drain_supported"], True)
        self.assertEqual(
            [call.args[:2] for call in self.request.call_args_list],
            [("GET", None), ("POST", INSTANCE), ("GET", INSTANCE)],
        )
        self.assertGreaterEqual(self.identity.call_count, 8)

    def test_already_closed_does_not_post(self):
        self.responses((200, snapshot(accepting=False)))
        self.assertTrue(self.run_drain()["store_workers_drained"])
        self.assertEqual(self.request.call_count, 1)

    def test_uncertain_post_only_recovers_through_same_instance_get(self):
        self.responses(
            (200, snapshot()), store.RequestUncertain("unknown"), (200, snapshot(accepting=False))
        )
        report = self.run_drain()
        self.assertIs(report["post_outcome_recovered"], True)
        self.assertEqual(
            [call.args[0] for call in self.request.call_args_list], ["GET", "POST", "GET"]
        )

    def test_uncertain_post_with_open_admission_refuses_without_retry(self):
        self.responses((200, snapshot()), store.RequestUncertain("unknown"), (200, snapshot()))
        with self.assertRaisesRegex(Refusal, "store_admission_still_open"):
            self.run_drain()
        self.assertEqual(self.request.call_count, 3)

    def test_instance_change_after_post_refuses(self):
        self.responses((200, snapshot()), (200, snapshot(accepting=False, instance="d" * 32)))
        with self.assertRaisesRegex(Refusal, "store_instance_changed"):
            self.run_drain()

    def test_process_change_after_http_refuses(self):
        self.identity.side_effect = [IDENTITY, IDENTITY, {**IDENTITY, "start_ticks": "5678"}]
        self.responses((200, snapshot(accepting=False)))
        with self.assertRaisesRegex(Refusal, "store_process_incarnation_changed"):
            self.run_drain()

    def test_legacy_is_explicitly_not_quiescence_and_no_http(self):
        self.identity.return_value = {**IDENTITY, "revision": store.LEGACY_REVISION}
        report = self.run_drain()
        self.assertEqual(report["mode"], "legacy_unsupported")
        self.assertIsNone(report["store_workers_drained"])
        self.assertIs(report["worker_drain_supported"], False)
        self.request.assert_not_called()

    def test_absent_process_is_not_worker_quiescence(self):
        self.identity.return_value = {**IDENTITY, "pid": 0, "start_ticks": None}
        report = self.run_drain()
        self.assertEqual(report["mode"], "no_active_process")
        self.assertIsNone(report["store_workers_drained"])
        self.request.assert_not_called()

    def test_unknown_revision_does_not_get_legacy_exception(self):
        self.responses((404, {}))
        with self.assertRaisesRegex(Refusal, "store_http_status_invalid"):
            self.run_drain()

    def test_receipt_persistence_process_change_refuses(self):
        self.responses((200, snapshot(accepting=False)))
        report = self.run_drain()
        self.identity.return_value = {**IDENTITY, "pid": 43}
        with self.assertRaisesRegex(Refusal, "store_process_incarnation_changed"):
            store.verify_stop_identity(
                self.native, report, deadline=self.deadline, command=self.command
            )

    def test_post_recovery_cannot_hide_identity_change(self):
        self.identity.side_effect = [IDENTITY] * 4 + [{**IDENTITY, "pid": 43}]
        self.responses((200, snapshot()), store.RequestUncertain("unknown"))
        with self.assertRaisesRegex(Refusal, "store_process_incarnation_changed"):
            self.run_drain()
        self.assertEqual(self.request.call_count, 2)

    def test_invalid_deadline_and_unreadable_identity_refuse(self):
        for deadline in (True, float("nan"), float("inf")):
            with (
                self.subTest(deadline=deadline),
                self.assertRaisesRegex(Refusal, "store_deadline_invalid"),
            ):
                store.drain_before_stop(self.native, deadline=deadline, command=self.command)
        self.identity.side_effect = PermissionError()
        with self.assertRaisesRegex(Refusal, "store_stop_proof_unavailable"):
            self.run_drain()

    def test_command_timeout_never_permits_stop_or_escapes_as_raw_process_error(self):
        self.identity.side_effect = subprocess.TimeoutExpired("systemctl", 1)
        with self.assertRaisesRegex(Refusal, "store_stop_proof_unavailable"):
            self.run_drain()
        report = {
            "ok": True,
            "stop_permitted": True,
            "mode": "store_workers_drained",
            "identity": IDENTITY,
        }
        with self.assertRaisesRegex(Refusal, "store_stop_identity_unavailable"):
            store.verify_stop_identity(
                self.native, report, deadline=self.deadline, command=self.command
            )
        self.request.assert_not_called()


class SchemaTests(unittest.TestCase):
    def test_valid_status_schema(self):
        value = snapshot(accepting=False)
        self.assertEqual(store._snapshot(200, canonical(value), "GET", 42), value)

    def test_rejects_noncanonical_types_counts_and_inconsistent_states(self):
        values = []
        for key, value in (
            ("pid", True),
            ("accepting", 0),
            ("drained", 0),
            ("instance_id", "A" * 32),
            ("format", "unknown"),
            ("status", "drained"),
        ):
            document = snapshot()
            document[key] = value
            values.append(document)
        for pending in (
            {"read": True, "stateful": 0, "transport": 0},
            {"read": -1, "stateful": 0, "transport": 0},
            {"read": 0, "stateful": 0},
            {"read": 0, "stateful": 0, "transport": 2**31},
        ):
            values.append({**snapshot(), "pending": pending})
        values += [{**snapshot(), "extra": 1}, {**snapshot(), "drained": True}]
        for document in values:
            with self.subTest(document=document), self.assertRaises(Refusal):
                store._snapshot(200, canonical(document), "GET", 42)

    def test_duplicate_json_keys_and_nonfinite_rejected(self):
        for raw in (b'{"pid":42,"pid":42}', b'{"pending":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(Refusal):
                store._snapshot(200, raw, "GET", 42)

    def test_post_status_must_match_actual_closed_state(self):
        for code, value in (
            (202, snapshot(accepting=False)),
            (200, snapshot(accepting=False, pending=1)),
            (200, snapshot()),
            (301, snapshot(accepting=False)),
        ):
            with self.subTest(code=code, value=value), self.assertRaises(Refusal):
                store._snapshot(code, canonical(value), "POST", 42, INSTANCE)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary)
        self.proc = self.root / "proc"
        self.cgroups = self.root / "cgroups"
        self.source = self.root / "sealed"
        self.source.mkdir()
        self.proc.mkdir()
        (self.proc / "42").mkdir()
        (self.proc / "sys/kernel/random").mkdir(parents=True)
        self.cgroups.mkdir()
        (self.cgroups / "cgroup.controllers").write_bytes(b"cpu memory\n")
        self.group = self.cgroups / "system.slice" / store.UNIT
        self.group.mkdir(parents=True)
        (self.group / "cgroup.events").write_bytes(b"populated 0\nfrozen 0\n")
        self.binding = {
            "source_root": "/sealed/manager",
            "cwd": "/sealed/manager",
            "interpreter": "/runtime/bin/python",
            "interpreter_realpath": "/usr/bin/python3.12",
            "module": "autostop_manager.mcp_server",
            "arguments": [],
        }
        self.native = {"manager_revision": REVISION, "profiles": {store.UNIT: self.binding}}
        (self.proc / "sys/kernel/random/boot_id").write_text(IDENTITY["boot_id"], encoding="ascii")
        self.stat = b"42 (python worker) " + b" ".join([b"S"] + [b"0"] * 18 + [b"1234"])
        (self.proc / "42/stat").write_bytes(self.stat)
        (self.proc / "42/cgroup").write_text(
            "0::/system.slice/" + store.UNIT + "\n", encoding="ascii"
        )
        (self.proc / "42/cmdline").write_bytes(
            b"/runtime/bin/python\0-P\0-m\0autostop_manager.mcp_server\0"
        )
        (self.proc / "42/environ").write_bytes(b"PYTHONPATH=/sealed/manager\0")
        self.enterContext(patch.object(store, "PROC", self.proc))
        self.enterContext(patch.object(store, "CGROUP", self.cgroups))
        self.enterContext(patch.object(store.sys, "platform", "linux"))
        self.links = self.enterContext(
            patch.object(
                store.os,
                "readlink",
                side_effect=lambda path: {
                    "cwd": self.binding["cwd"],
                    "exe": self.binding["interpreter_realpath"],
                }[path.name],
            )
        )
        self.protected = self.enterContext(
            patch.object(store.io, "read_protected", return_value=(REVISION + "\n").encode())
        )
        self.command = Mock(return_value=self.systemd())
        self.deadline = time.monotonic() + 10

    def systemd(self, *, pid=42, active="active", sub="running", group=None):
        group = "/system.slice/" + store.UNIT if group is None else group
        return (
            f"MainPID={pid}\nActiveState={active}\nSubState={sub}\nControlGroup={group}\n"
            f"InvocationID={'a' * 32}\n"
        ).encode()

    def observe(self):
        return store._identity(self.native, self.command, self.deadline)

    def test_binds_systemd_birth_revision_cwd_argv_and_environment(self):
        self.assertEqual(self.observe(), IDENTITY)
        self.assertEqual(self.command.call_args.args[1], self.deadline)
        self.assertIn("/usr/bin/systemctl", self.command.call_args.args[0])

    def test_running_environment_uses_physical_root_after_guard_dispatch(self):
        self.binding["selector"] = "/selectors/manager-mcp/current"
        self.assertEqual(self.observe()["source_root"], self.binding["source_root"])
        (self.proc / "42/environ").write_bytes(b"PYTHONPATH=/selectors/manager-mcp/current\0")
        with self.assertRaisesRegex(Refusal, "store_process_source_changed"):
            self.observe()

    def test_mcp_successor_revision_requires_modern_drain_despite_legacy_other_roles(self):
        self.native["manager_revision"] = store.LEGACY_REVISION
        self.binding["manager_revision"] = REVISION
        with patch.object(
            store, "_request", return_value=(200, canonical(snapshot(accepting=False)))
        ) as request:
            report = store.drain_before_stop(
                self.native, deadline=self.deadline, command=self.command
            )
        self.assertEqual(report["identity"]["revision"], REVISION)
        self.assertEqual(report["mode"], "store_workers_drained")
        self.assertIs(report["worker_drain_supported"], True)
        self.assertIs(report["store_workers_drained"], True)
        request.assert_called_once()

    def test_missing_mcp_revision_cannot_treat_new_source_as_legacy(self):
        self.native["manager_revision"] = store.LEGACY_REVISION
        with patch.object(store, "_request") as request:
            with self.assertRaisesRegex(Refusal, "store_process_revision_changed"):
                store.drain_before_stop(self.native, deadline=self.deadline, command=self.command)
        request.assert_not_called()

    def test_invalid_explicit_profile_revision_has_no_global_fallback(self):
        for value in (None, True, "", "B" * 40):
            self.binding["manager_revision"] = value
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(Refusal, "store_native_binding_required"),
            ):
                self.observe()

    def test_wrong_revision_or_process_bindings_refuse(self):
        self.protected.return_value = b"a" * 40
        with self.assertRaisesRegex(Refusal, "store_process_revision_changed"):
            self.observe()
        self.protected.return_value = REVISION.encode()
        for file, raw, expected in (
            ("cmdline", b"/usr/bin/python\0wrong.py\0", "argv_changed"),
            ("cgroup", b"0::/wrong.service\n", "binding_changed"),
            ("environ", b"PYTHONPATH=/other\0", "source_changed"),
            (
                "environ",
                b"PYTHONPATH=/sealed/manager\0PYTHONPATH=/sealed/manager\0",
                "source_changed",
            ),
        ):
            path = self.proc / "42" / file
            original = path.read_bytes()
            path.write_bytes(raw)
            try:
                with self.subTest(file=file), self.assertRaisesRegex(Refusal, expected):
                    self.observe()
            finally:
                path.write_bytes(original)

    def test_pid_zero_requires_terminal_state_and_empty_subtree(self):
        for active, sub in (("inactive", "dead"), ("failed", "failed")):
            self.command.return_value = self.systemd(pid=0, active=active, sub=sub, group="")
            self.assertEqual(self.observe()["pid"], 0)
        self.command.return_value = self.systemd(pid=0, active="deactivating", sub="stop")
        with self.assertRaisesRegex(Refusal, "inactive_state_unproven"):
            self.observe()
        self.command.return_value = self.systemd(pid=0, active="inactive", sub="dead")
        (self.group / "cgroup.events").write_bytes(b"populated 1\n")
        with self.assertRaisesRegex(Refusal, "inactive_cgroup_populated"):
            self.observe()

    def test_duplicate_systemd_fields_are_not_last_wins(self):
        self.command.return_value += b"MainPID=43\n"
        with self.assertRaisesRegex(Refusal, "systemd_response_invalid"):
            self.observe()

    def test_pid_birth_changes_during_inspection_refuse(self):
        with patch.object(store, "_birth", side_effect=["1234", "1235"]):
            with self.assertRaisesRegex(Refusal, "source_changed"):
                self.observe()

    def test_socket_connection_must_be_owned_by_main_pid(self):
        connection = Mock()
        connection.getpeername.return_value = store.ADDRESS
        connection.getsockname.return_value = ("127.0.0.1", 54321)
        (self.proc / "42/net").mkdir()
        (self.proc / "42/fd").mkdir()
        (self.proc / "42/fd/7").write_bytes(b"")
        local = store._endpoint(store.ADDRESS)
        remote = store._endpoint(connection.getsockname())
        row = f"0: {local} {remote} 01 0 0 0 0 0 456\n"
        table = self.proc / "42/net/tcp"
        table.write_text("header\n" + row, encoding="ascii")
        self.links.side_effect = None
        self.links.return_value = "socket:[456]"
        self.assertTrue(store._owns_connection(42, connection))
        self.links.return_value = "socket:[457]"
        self.assertFalse(store._owns_connection(42, connection))
        table.write_text("header\n" + row + row, encoding="ascii")
        with self.assertRaisesRegex(Refusal, "socket_peer_ambiguous"):
            store._owns_connection(42, connection)


class ResponseTests(unittest.TestCase):
    def connection(self, raw):
        connection = Mock()
        chunks = [raw]
        connection.recv.side_effect = lambda size: chunks.pop(0) if chunks else b""
        return connection

    def test_valid_bounded_response(self):
        raw = canonical(snapshot())
        connection = self.connection(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(raw)).encode()
            + b"\r\n\r\n"
            + raw
        )
        self.assertEqual(store._response(connection, time.monotonic() + 1), (200, raw))

    def test_bad_response_framing_and_bounds_refuse(self):
        for headers, body in (
            (b"Content-Length: 2\r\nTransfer-Encoding: chunked", b"{}"),
            (b"Content-Length: 2\r\nContent-Encoding: gzip", b"{}"),
            (b"Content-Length: 2\r\nContent-Length: 2", b"{}"),
            (b"Content-Length: 65536", b"{}"),
            (b"Content-Length: 2", b"{}extra"),
            (b"Content-Length: -1", b""),
        ):
            raw = (
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + headers
                + b"\r\n\r\n"
                + body
            )
            with self.subTest(headers=headers), self.assertRaises(Refusal):
                store._response(self.connection(raw), time.monotonic() + 1)

    def test_incomplete_body_is_unknown_transport(self):
        raw = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 5\r\n\r\n{}"
        with self.assertRaises(store.RequestUncertain):
            store._response(self.connection(raw), time.monotonic() + 1)


if __name__ == "__main__":
    unittest.main()
