"""File-backed controller checks; real flock/Unix RPC tests run only on Linux."""

from __future__ import annotations

import copy
import io
import json
import os
import socket
import stat
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts import j1_work_duty as duty

NATIVE_LINUX = os.name == "posix" and hasattr(socket, "AF_UNIX")


class WorkDutyFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.root = Path(temporary.name).resolve()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(self.make_writable)
        self.attempt = "a5f8daac-e7e0-471d-8c46-31b030184b8c"
        self.crm_revision = "b" * 40
        self.preparation = self.root / "attempt" / "preparation.json"
        self.preparation.parent.mkdir()
        self.marker = self.root / "maintenance"
        self.lock = self.root / "deploy.lock"
        self.lock.write_bytes(b"")
        installed = self.root / "controller" / "restore.py"
        installed.parent.mkdir()
        installed.write_bytes(Path(duty.__file__).read_bytes())
        constants = {
            "CONTROLLER": installed,
            "INTENT": self.root / "intent.json",
            "COMMIT": self.root / "state" / "committed.json",
            "PENDING": self.root / "state" / "commit-pending.json",
            "POLICY": self.root / "policy.json",
            "STATE": self.root / "guard-state" / "armed.json",
            "GUARD_PENDING": self.root / "guard-state" / "arming-pending.json",
            "GUARD": self.root / "guard.py",
            "INDEX": self.root / "source-index.json",
            "DROPIN": self.root / "98-j1-work-duty-restore.conf",
            "SOCKET": self.root / "bridge.sock",
            "HOST": socket.gethostname(),
            "GUARD_SHA256": duty.digest(b"synthetic guard identity\n"),
            "INDEX_SHA256": duty.digest(b"synthetic source index identity\n"),
            "__file__": str(installed),
        }
        for name in ("COMMIT", "STATE"):
            constants[name].parent.mkdir()
        fixture = patch.multiple(duty, **constants)
        fixture.start()
        self.addCleanup(fixture.stop)
        duty.GUARD.write_bytes(b"synthetic guard identity\n")
        duty.INDEX.write_bytes(b"synthetic source index identity\n")
        # Only host/root ownership is substituted. Paths, bytes, source inventory,
        # JSON bindings, lock acquisition and RPC validation remain real.
        ownership = patch.object(duty, "owner")
        ownership.start()
        self.addCleanup(ownership.stop)
        platform = patch.object(duty.sys, "platform", "linux")
        platform.start()
        self.addCleanup(platform.stop)
        uid = patch.object(duty.os, "geteuid", return_value=0, create=True)
        uid.start()
        self.addCleanup(uid.stop)
        self.sources = {}
        for name in ("manager", "work-telegram", "personal-telegram"):
            root = self.root / name
            root.mkdir()
            (root / "REVISION").write_bytes((duty.MANAGER_REVISION + "\n").encode())
            (root / "README.md").write_bytes(b"Synthetic immutable source; never imported.\n")
            manifest = b"".join(
                f"{duty.digest((root / path).read_bytes())}  ./{path}\n".encode()
                for path in ("README.md", "REVISION")
            )
            (root / "MANIFEST.sha256").write_bytes(manifest)
            (root / "MANIFEST.files0").write_bytes(b"./README.md\0./REVISION\0")
            self.sources[name] = {
                "path": str(root),
                "identity": [root.stat().st_dev, root.stat().st_ino],
                "manifest_sha256": duty.digest(manifest),
            }
        self.build_chain(True)

    def make_writable(self):
        for root, _, files in os.walk(self.root):
            for name in files:
                path = Path(root) / name
                if not path.is_symlink():
                    path.chmod(0o600)

    def put(self, name, value, path=None):
        path = path or self.preparation.parent / f"{name}.json"
        self.docs[name] = value
        self.refs[name] = {"path": str(path), "sha256": ""}
        self.flush(name)
        return self.refs[name]

    def flush(self, name):
        path = Path(self.refs[name]["path"])
        if path.exists():
            path.chmod(0o600)
        raw = duty.canonical(self.docs[name])
        path.write_bytes(raw)
        self.refs[name]["sha256"] = duty.digest(raw)
        if path in (duty.INTENT, duty.COMMIT):
            path.chmod(0o444)

    def build_chain(self, desired):
        self.docs, self.refs = {}, {}
        self.put(
            "preparation",
            {
                "schema": "autostop.crm.j1-release-preparation.v1",
                "attempt_id": self.attempt,
                "crm": {"revision": self.crm_revision, "tree": "c" * 40},
                "manager": {"revision": duty.MANAGER_REVISION, "tree": "d" * 40},
                "sources": self.sources,
            },
            self.preparation,
        )
        prep_hash = self.refs["preparation"]["sha256"]
        self.put(
            "closure-acceptance",
            {
                "schema": "autostop.crm.j1-closure-acceptance.v1",
                "scope": duty.SCOPE,
                "attempt_id": self.attempt,
                "preparation_sha256": prep_hash,
                "work_inbound_before": desired,
            },
        )
        self.put(
            "intent",
            {
                "schema": "autostop.crm.j1-work-duty-intent.v1",
                "scope": duty.SCOPE,
                "attempt_id": self.attempt,
                "preparation_path": str(self.preparation),
                "preparation_sha256": prep_hash,
                "closure_sha256": self.refs["closure-acceptance"]["sha256"],
                "crm_revision": self.crm_revision,
                "manager_revision": duty.MANAGER_REVISION,
                "inbound_enabled": desired,
                "maintenance_marker": str(self.marker),
                "deploy_lock": str(self.lock),
                "controller_sha256": duty.digest(duty.CONTROLLER.read_bytes()),
            },
            duty.INTENT,
        )
        self.intent_hash = self.refs["intent"]["sha256"]
        duty.DROPIN.write_bytes(duty.dropin_bytes(self.intent_hash))
        bound_files = [
            self.refs["intent"],
            {"path": str(duty.CONTROLLER), "sha256": self.docs["intent"]["controller_sha256"]},
            {"path": str(duty.DROPIN), "sha256": duty.digest(duty.DROPIN.read_bytes())},
        ]
        environment = {
            "AUTOSTOP_WORK_TELEGRAM_MONITOR_INCOMING": "0",
            "AUTOSTOP_WORK_TELEGRAM_DURABLE_INBOX": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        environment_hash = duty.digest(duty.canonical(environment))
        runtime = self.put(
            "runtime",
            {
                "stable_input_environment": environment,
                "stable_input_environment_sha256": environment_hash,
            },
        )
        source = self.sources["work-telegram"]
        self.put(
            "policy",
            {
                "schema": "autostop.j1.candidate-policy.v2",
                "attempt_id": self.attempt,
                "host": duty.HOST,
                "revision": duty.MANAGER_REVISION,
                "guard_sha256": duty.GUARD_SHA256,
                "index_sha256": duty.INDEX_SHA256,
                "bindings": {
                    duty.WORK_UNIT: {
                        "source_root": source["path"],
                        "source_identity": source["identity"],
                        "manifest_sha256": source["manifest_sha256"],
                        "module": "autostop_manager.telegram_bridge",
                        "arguments": ["--account", "work", "daemon"],
                        "runtime_receipt": runtime["path"],
                        "runtime_files": [runtime],
                        "stable_environment_sha256": environment_hash,
                        "unit_files": bound_files,
                    }
                },
            },
            duty.POLICY,
        )
        self.put("quiet", {"synthetic": True})
        self.put(
            "armed",
            {
                "schema": "autostop.j1.candidate-state.v2",
                "scope": duty.SCOPE,
                "attempt_id": self.attempt,
                "revision": duty.MANAGER_REVISION,
                "policy_sha256": self.refs["policy"]["sha256"],
                "guard_sha256": duty.GUARD_SHA256,
                "index_sha256": duty.INDEX_SHA256,
                "old_writer_allowed": False,
                "candidate_may_have_written": True,
                "prerequisite_sha256": self.refs["quiet"]["sha256"],
            },
            duty.STATE,
        )
        self.put(
            "handoff",
            {
                "schema": "autostop.crm.j1-activation-handoff.v1",
                "scope": duty.SCOPE,
                "attempt_id": self.attempt,
                "host": duty.HOST,
                "preparation_sha256": prep_hash,
                "closure_sha256": self.refs["closure-acceptance"]["sha256"],
                "crm": self.docs["preparation"]["crm"],
                "manager": self.docs["preparation"]["manager"],
                "sources": self.sources,
                "work_duty_intent": self.refs["intent"],
                "work_duty_controller_sha256": self.docs["intent"]["controller_sha256"],
                "files": bound_files,
            },
        )
        self.put(
            "activation-acceptance",
            {
                "schema": "autostop.crm.j1-activation-acceptance.v1",
                "scope": duty.SCOPE,
                "attempt_id": self.attempt,
                "handoff_sha256": self.refs["handoff"]["sha256"],
                "evidence": {
                    "policy": self.refs["policy"],
                    "state": self.refs["armed"],
                    "quiet_window": self.refs["quiet"],
                },
            },
        )
        self.put(
            "admitted",
            {
                "schema": "autostop.crm.j1-admission-receipt.v1",
                "scope": duty.SCOPE,
                "attempt_id": self.attempt,
                "preparation_sha256": prep_hash,
                "handoff_sha256": self.refs["handoff"]["sha256"],
                "acceptance_sha256": self.refs["activation-acceptance"]["sha256"],
            },
        )
        self.committed = {
            "schema": "autostop.crm.j1-work-duty-committed.v1",
            "scope": duty.SCOPE,
            "attempt_id": self.attempt,
            "intent_sha256": self.intent_hash,
            "preparation_sha256": prep_hash,
            "handoff_sha256": self.refs["handoff"]["sha256"],
            "acceptance_sha256": self.refs["activation-acceptance"]["sha256"],
            "policy_sha256": self.refs["policy"]["sha256"],
            "crm_revision": self.crm_revision,
            "inbound_enabled": desired,
        }

    def seed_commit(self):
        self.put("committed", copy.deepcopy(self.committed), duty.COMMIT)

    def rebind_authorization(self):
        """Rehash modified fixture receipts so negative tests reach semantic checks."""
        self.flush("policy")
        self.docs["armed"]["policy_sha256"] = self.refs["policy"]["sha256"]
        self.flush("armed")
        self.flush("handoff")
        self.docs["activation-acceptance"]["handoff_sha256"] = self.refs["handoff"]["sha256"]
        self.flush("activation-acceptance")
        self.docs["admitted"]["handoff_sha256"] = self.refs["handoff"]["sha256"]
        self.docs["admitted"]["acceptance_sha256"] = self.refs["activation-acceptance"]["sha256"]
        self.flush("admitted")
        self.docs["committed"].update(
            handoff_sha256=self.refs["handoff"]["sha256"],
            acceptance_sha256=self.refs["activation-acceptance"]["sha256"],
            policy_sha256=self.refs["policy"]["sha256"],
        )
        self.flush("committed")

    def deny_restore(self, error=None):
        if error:
            with self.assertRaisesRegex(duty.DutyError, error):
                duty.restore(self.intent_hash)
        else:
            with self.assertRaises((duty.DutyError, OSError, ValueError, KeyError)):
                duty.restore(self.intent_hash)


class WorkDutyFileTests(WorkDutyFixture):
    def test_first_start_is_closed_without_socket_lock_or_environment_changes(self):
        self.lock.unlink()
        before = dict(os.environ)
        self.assertEqual("closed_uncommitted", duty.restore(self.intent_hash))
        self.assertEqual(before, dict(os.environ))
        self.assertFalse(duty.COMMIT.exists())
        self.assertFalse(duty.PENDING.exists())

    def test_complete_chain_matches_explicit_commit_contract(self):
        self.assertEqual(
            self.committed, duty.expected_commit(self.docs["intent"], self.intent_hash)
        )

    def test_missing_intent_denies_even_first_start(self):
        duty.INTENT.chmod(0o600)
        duty.INTENT.unlink()
        self.deny_restore()

    def test_wrong_cli_hash_and_changed_controller_deny(self):
        with self.assertRaisesRegex(duty.DutyError, "hash_mismatch"):
            duty.restore("0" * 64)
        duty.CONTROLLER.write_bytes(b"changed controller")
        self.deny_restore("controller_changed")

    def test_unknown_or_partial_commit_never_becomes_first_start(self):
        self.seed_commit()
        duty.COMMIT.chmod(0o600)
        duty.COMMIT.write_bytes(b'{"schema":')
        duty.COMMIT.chmod(0o444)
        self.deny_restore()
        self.docs["committed"]["unknown"] = True
        self.flush("committed")
        self.deny_restore("committed_binding_mismatch")

    def test_pending_denies_with_and_without_final_receipt(self):
        duty.PENDING.write_bytes(b"unknown publication")
        self.deny_restore("commit_pending")
        self.seed_commit()
        self.deny_restore("commit_pending")

    def test_stale_commit_context_and_nonboolean_duty_deny(self):
        self.seed_commit()
        for key, value in (
            ("scope", "synthetic"),
            ("attempt_id", "other"),
            ("crm_revision", "0" * 40),
            ("intent_sha256", "0" * 64),
            ("policy_sha256", "0" * 64),
            ("handoff_sha256", "0" * 64),
            ("acceptance_sha256", "0" * 64),
            ("inbound_enabled", 1),
        ):
            with self.subTest(key=key):
                original = self.docs["committed"][key]
                self.docs["committed"][key] = value
                self.flush("committed")
                self.deny_restore("committed_binding_mismatch")
                self.docs["committed"][key] = original

    def test_mutated_chain_receipts_and_guard_package_deny(self):
        self.seed_commit()
        for path in (
            self.preparation,
            self.preparation.parent / "closure-acceptance.json",
            self.preparation.parent / "handoff.json",
            self.preparation.parent / "activation-acceptance.json",
            duty.STATE,
            duty.POLICY,
            duty.GUARD,
            duty.INDEX,
            duty.DROPIN,
        ):
            with self.subTest(path=path.name):
                before = path.read_bytes()
                path.write_bytes(b"{}\n")
                self.deny_restore()
                path.write_bytes(before)

    def test_mutated_source_and_extra_bytecode_deny(self):
        self.seed_commit()
        path = Path(self.sources["work-telegram"]["path"]) / "README.md"
        before = path.read_bytes()
        path.write_bytes(b"changed source")
        self.deny_restore("source_content_changed")
        path.write_bytes(before)
        (path.parent / "unaccepted.pyc").write_bytes(b"new bytecode")
        self.deny_restore("unknown_source_file")

    def test_rehashed_unsafe_armed_state_still_denies(self):
        self.seed_commit()
        self.docs["armed"]["old_writer_allowed"] = True
        self.flush("armed")
        self.flush("activation-acceptance")
        self.docs["admitted"]["acceptance_sha256"] = self.refs["activation-acceptance"]["sha256"]
        self.flush("admitted")
        self.docs["committed"]["acceptance_sha256"] = self.refs["activation-acceptance"]["sha256"]
        self.flush("committed")
        self.deny_restore("guard_state_changed")

    def test_guard_pending_denies_even_with_valid_commit(self):
        self.seed_commit()
        duty.GUARD_PENDING.write_bytes(b"{}")
        self.deny_restore("guard_arm_pending")

    def test_arbitrary_rehashed_controller_identity_is_not_accepted(self):
        self.seed_commit()
        self.docs["handoff"]["work_duty_controller_sha256"] = "0" * 64
        self.rebind_authorization()
        self.deny_restore("handoff_changed")

    def test_controller_inputs_must_be_in_both_handoff_and_guard(self):
        self.seed_commit()
        binding = self.docs["policy"]["bindings"][duty.WORK_UNIT]
        for subject, key in ((binding, "unit_files"), (self.docs["handoff"], "files")):
            for missing in (duty.CONTROLLER, duty.INTENT, duty.DROPIN):
                with self.subTest(collection=key, missing=missing.name):
                    original = subject[key]
                    subject[key] = [ref for ref in original if ref["path"] != str(missing)]
                    self.rebind_authorization()
                    self.deny_restore("controller_inputs_not_bound")
                    subject[key] = original

    def test_unknown_admission_fields_are_not_ignored(self):
        self.seed_commit()
        self.docs["admitted"]["unknown"] = True
        self.flush("admitted")
        self.deny_restore("admission_fields_invalid")

    def test_rehashed_incoming_startup_is_still_rejected(self):
        self.seed_commit()
        runtime = self.docs["runtime"]
        runtime["stable_input_environment"]["AUTOSTOP_WORK_TELEGRAM_MONITOR_INCOMING"] = "1"
        changed_hash = duty.digest(duty.canonical(runtime["stable_input_environment"]))
        runtime["stable_input_environment_sha256"] = changed_hash
        self.flush("runtime")
        self.docs["policy"]["bindings"][duty.WORK_UNIT]["stable_environment_sha256"] = changed_hash
        self.rebind_authorization()
        self.deny_restore("work_startup_changed")

    def test_maintenance_preserves_closed_without_opening_missing_lock(self):
        self.seed_commit()
        self.marker.write_bytes(b"")
        self.lock.unlink()
        before = dict(os.environ)
        self.assertEqual("closed_maintenance", duty.restore(self.intent_hash))
        self.assertEqual(before, dict(os.environ))
        with self.assertRaisesRegex(duty.DutyError, "maintenance_active"):
            duty.commit(self.intent_hash)

    def test_writable_commit_is_not_an_immutable_commit(self):
        self.seed_commit()
        duty.COMMIT.chmod(0o600)
        self.deny_restore("immutable_commit_required")

    def test_duplicate_json_keys_deny(self):
        self.seed_commit()
        duty.COMMIT.chmod(0o600)
        duty.COMMIT.write_bytes(b'{"schema":"x","schema":"y"}')
        duty.COMMIT.chmod(0o444)
        self.deny_restore("duplicate_json_key")


@unittest.skipUnless(NATIVE_LINUX, "Requires real Linux flock, AF_UNIX and SO_PEERCRED")
class WorkDutyNativeTests(WorkDutyFixture):
    def setUp(self):
        super().setUp()
        import pwd

        user = patch.object(duty, "BRIDGE_USER", pwd.getpwuid(os.getuid()).pw_name)
        user.start()
        self.addCleanup(user.stop)

    @contextmanager
    def bridge(self, desired, *, bad_readback=False, rejected=False, delay=0, ready=True):
        requests, errors = [], []
        stopped = threading.Event()
        listening = threading.Event()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        def serve():
            if delay:
                time.sleep(delay)
            if stopped.is_set():
                return
            listener.bind(str(duty.SOCKET))
            listener.listen(4)
            listener.settimeout(0.1)
            listening.set()
            while not stopped.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                try:
                    with connection:
                        connection.settimeout(2)
                        with connection.makefile("rb") as stream:
                            request = json.loads(stream.readline(4096))
                        requests.append(request)
                        if request["operation"] == "duty_set":
                            self.assertEqual({"operation": "duty_set", "enabled": desired}, request)
                            reply = (
                                {"ok": False, "error": "PRIVATE_ACCOUNT_SECRET"}
                                if rejected
                                else {"ok": True}
                            )
                        else:
                            self.assertEqual({"operation": "status"}, request)
                            reply = {
                                "ok": True,
                                "transport_ready": ready,
                                "inbound_enabled": not desired if bad_readback else desired,
                                "account": {"private": "PRIVATE_ACCOUNT_SECRET"},
                            }
                        connection.sendall(duty.canonical(reply))
                except BaseException as exc:
                    errors.append(exc)
                    break

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        if not delay:
            self.assertTrue(listening.wait(2))
        try:
            yield requests
        finally:
            stopped.set()
            listener.close()
            thread.join(timeout=3)
            duty.SOCKET.unlink(missing_ok=True)
            self.assertFalse(thread.is_alive())
            if errors:
                raise errors[0]

    def test_commit_is_durable_exclusive_and_idempotent_without_rpc(self):
        self.assertEqual("committed", duty.commit(self.intent_hash))
        self.assertEqual(self.committed, duty.document(duty.COMMIT))
        self.assertEqual(0o444, stat.S_IMODE(duty.COMMIT.stat().st_mode))
        self.assertFalse(duty.PENDING.exists())
        before = duty.COMMIT.read_bytes()
        self.assertEqual("already_committed", duty.commit(self.intent_hash))
        self.assertEqual(before, duty.COMMIT.read_bytes())

    def test_restart_restores_enabled_intent_through_real_unix_rpc(self):
        duty.commit(self.intent_hash)
        before = dict(os.environ)
        with self.bridge(True) as requests:
            self.assertEqual("restored", duty.restore(self.intent_hash))
            self.assertEqual("restored", duty.restore(self.intent_hash))
        self.assertEqual(6, len(requests))
        self.assertEqual(before, dict(os.environ))

    def test_restart_preserves_disabled_intent_through_real_unix_rpc(self):
        self.build_chain(False)
        duty.commit(self.intent_hash)
        with self.bridge(False) as requests:
            self.assertEqual("restored", duty.restore(self.intent_hash))
        self.assertEqual(3, len(requests))

    def test_delayed_socket_startup_waits_before_single_mutating_rpc(self):
        duty.commit(self.intent_hash)
        with self.bridge(True, delay=0.15) as requests:
            self.assertEqual("restored", duty.restore(self.intent_hash))
        self.assertEqual(["status", "duty_set", "status"], [item["operation"] for item in requests])

    def test_readiness_expiry_never_enables_duty(self):
        duty.commit(self.intent_hash)
        with self.bridge(True, ready=False) as requests, patch.object(duty, "TIMEOUT_SECONDS", 1):
            self.deny_restore("deadline_exceeded")
        self.assertTrue(requests)
        self.assertTrue(all(item == {"operation": "status"} for item in requests))

    def test_exclusive_deploy_lock_blocks_shared_readonly_lock_before_rpc(self):
        import fcntl

        duty.commit(self.intent_hash)
        fd = os.open(self.lock, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.bridge(True) as requests, patch.object(duty, "TIMEOUT_SECONDS", 0.15):
                self.deny_restore("deadline_exceeded")
            self.assertEqual([], requests)
        finally:
            os.close(fd)

    def test_missing_lock_denies_before_rpc(self):
        duty.commit(self.intent_hash)
        self.lock.unlink()
        with self.bridge(True) as requests:
            self.deny_restore()
        self.assertEqual([], requests)

    def test_commit_fsync_failure_retains_pending_and_denies_restore(self):
        original = os.fsync
        calls = 0

        def fail_final_file(fd):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated final file fsync failure")
            original(fd)

        with patch.object(duty.os, "fsync", side_effect=fail_final_file):
            with self.assertRaises(OSError):
                duty.commit(self.intent_hash)
        self.assertTrue(duty.PENDING.exists())
        self.assertTrue(duty.COMMIT.exists())
        self.deny_restore("commit_pending")

    def test_rpc_readback_failure_is_not_success(self):
        duty.commit(self.intent_hash)
        with self.bridge(True, bad_readback=True):
            self.deny_restore("duty_readback_failed")

    def test_rpc_rejection_does_not_print_private_response(self):
        duty.commit(self.intent_hash)
        output = io.StringIO()
        with self.bridge(True, rejected=True), patch("sys.stdout", output):
            self.assertEqual(1, duty.main(["restore", "--intent-sha256", self.intent_hash]))
        self.assertNotIn("PRIVATE_ACCOUNT_SECRET", output.getvalue())
        self.assertEqual({"ok": False, "error": "rpc_rejected"}, json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
