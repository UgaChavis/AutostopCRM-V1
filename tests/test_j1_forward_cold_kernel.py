"""Private protocol/ledger negatives; no production or inference effects."""

from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import j1_forward_cold_kernel as kernel
from scripts import j1_forward_io as io


class FakeRPC:
    """An explicit transport double, never a production-auth acceptance claim."""

    def __init__(self, fixture):
        self.fixture = fixture
        self.peer = (101, os.getuid(), os.getgid())
        self.initialized = {"codexHome": str(fixture.codex), "platformOs": "linux"}
        self.closed = False

    def call(self, method, params):
        self.fixture.calls.append((method, params))
        if method == "account/read":
            return copy.deepcopy(self.fixture.account)
        if method == "thread/loaded/list":
            return {"data": [self.fixture.thread_id], "nextCursor": None}
        if method == "thread/read":
            return self.fixture.thread()
        raise AssertionError(method)

    def _exchange(self, method, params):
        self.fixture.calls.append((method, params))
        assert (self.fixture.root / "records/thread-start-intent.json").is_file()
        if self.fixture.unknown:
            raise TimeoutError("private lost reply")
        return self.fixture.thread()

    def close(self):
        self.closed = True


@unittest.skipUnless(os.name == "posix", "Release checks require POSIX ownership and permissions")
class KernelPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cold-kernel-ledger-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.codex = self.home / ".codex"
        self.cwd = self.root / "source"
        for path in (self.home, self.codex, self.cwd, self.root / "records"):
            path.mkdir(mode=0o700)
        self.thread_id = str(uuid.uuid4())
        self.rollout = self.codex / "sessions/2026/10/19/rollout.jsonl"
        self.rollout.parent.mkdir(mode=0o700, parents=True)
        self.rollout.write_bytes(
            io.canonical({"type": "session_meta", "payload": {"id": self.thread_id}})
        )
        self.rollout.chmod(0o600)
        self.binding = {
            "attempt_id": str(uuid.uuid4()),
            "boot_id": str(uuid.uuid4()),
            "generation": str(uuid.uuid4()),
            "consumer_sha256": "3" * 64,
            "candidate_crm_sha": "1" * 40,
            "candidate_manager_sha": "2" * 40,
            "deployment_lock_identity": [1, 2],
            "not_before_ns": time.monotonic_ns() - 10**8,
            "deadline_ns": time.monotonic_ns() + 60 * 10**9,
        }
        self.calls = []
        self.unknown = False
        self.account = {
            "account": {"type": "chatgpt", "email": "ARTIFICIAL"},
            "requiresOpenaiAuth": True,
        }
        self.actor = {"pid": 101, "uid": os.getuid(), "gid": os.getgid()}
        self.policy = {
            key: self.publish(key + ".json", {"private": key})
            for key in ("source_ref", "rpc_ref", "protocol_index_ref")
        }
        self.home_ref = self.publish(
            "home.json",
            {
                "schema": "autostop.j1.cold-kernel-home.v1",
                "scope": "native_fixture",
                "binding": self.binding,
                "home": str(self.home),
                "codex_home": str(self.codex),
                "cwd": str(self.cwd),
                "prestart_files": {"auth.json": "4" * 64},
                "authorization_ref": self.publish("authorization.json", {"private": True}),
                "created_ns": time.monotonic_ns(),
            },
        )
        self.provider = SimpleNamespace(
            scope="native_fixture",
            read_reference=lambda ref: Path(ref["path"]).read_bytes(),
            kernel_rpc_factory=lambda _path, _deadline: FakeRPC(self),
            verify_closure=lambda _validated: None,
        )
        self.context_ref = self.publish(
            "context.json",
            {
                "schema": "autostop.j1.cold-kernel-context.v1",
                "scope": "native_fixture",
                "binding": self.binding,
                "closure_ref": self.publish("closure.json", {}),
                "hold_ref": self.publish("hold.json", {}),
            },
        )
        self.custody = patch.object(io, "protected_directory", side_effect=lambda *_a, **_kw: None)
        self.custody.start()
        self.addCleanup(self.custody.stop)
        self.source_custody = patch.object(io, "ROOT_UID", os.getuid())
        self.source_custody.start()
        self.addCleanup(self.source_custody.stop)
        self.socket_patch = patch.object(
            kernel, "socket_identity", return_value={"private_socket": True}
        )
        self.socket_patch.start()
        self.addCleanup(self.socket_patch.stop)

    def publish(self, name, body):
        path = self.root / name
        path.write_bytes(io.canonical(body))
        path.chmod(0o600)
        return {"path": str(path), "sha256": io.sha(path.read_bytes())}

    def thread(self):
        return {
            "thread": {
                "id": self.thread_id,
                "cwd": str(self.cwd),
                "ephemeral": False,
                "turns": [],
                "status": {"type": "idle"},
                "path": str(self.rollout),
            }
        }

    def prepare(self):
        return kernel.prepare_owned_thread(
            self.binding,
            self.policy,
            self.home_ref,
            provider=self.provider,
            directory=self.root / "records",
            socket_path=self.root / "socket",
            appserver_actor=self.actor,
            supervisor_actor={"pid": 99},
            context_ref=self.context_ref,
        )

    def test_real_durable_intent_before_one_request_and_exact_duplicate_refusal(self):
        result = self.prepare()
        self.assertEqual(result["thread_id"], self.thread_id)
        self.assertEqual(
            [m for m, _p in self.calls],
            ["account/read", "thread/start", "thread/loaded/list", "thread/read"],
        )
        self.assertNotIn(b"ARTIFICIAL", (self.root / "records/account.json").read_bytes())
        with self.assertRaisesRegex(io.Refusal, "kernel_thread_start_consumed"):
            self.prepare()
        self.assertEqual(sum(method == "thread/start" for method, _ in self.calls), 1)
        proof = io.document(io.referenced(result["empty_thread_ref"]))
        self.assertFalse(proof["persistent_history_verified"])
        self.assertEqual(proof["capability"], kernel.CAPABILITY)

    def test_lost_ack_is_consumed_without_second_thread_start(self):
        self.unknown = True
        with self.assertRaises(TimeoutError):
            self.prepare()
        with self.assertRaisesRegex(io.Refusal, "kernel_thread_start_consumed"):
            self.prepare()
        self.assertEqual(sum(method == "thread/start" for method, _ in self.calls), 1)

    def test_no_auth_never_creates_thread_intent_or_start(self):
        self.account = {"account": None, "requiresOpenaiAuth": True}
        with self.assertRaisesRegex(io.Refusal, "kernel_fresh_auth_required"):
            self.prepare()
        self.assertFalse((self.root / "records/thread-start-intent.json").exists())
        self.assertFalse(any(method == "thread/start" for method, _ in self.calls))

    def test_nonempty_creation_retains_consumed_intent(self):
        original = self.thread
        self.thread = lambda: {"thread": {**original()["thread"], "turns": [{"private": True}]}}
        with self.assertRaisesRegex(io.Refusal, "kernel_owned_thread_metadata"):
            self.prepare()
        self.assertTrue((self.root / "records/thread-start-intent.json").exists())

    def test_extra_loaded_thread_denies_after_first_start(self):
        original = FakeRPC.call

        def extra(rpc, method, params):
            if method == "thread/loaded/list":
                return {"data": [self.thread_id, str(uuid.uuid4())], "nextCursor": None}
            return original(rpc, method, params)

        with patch.object(FakeRPC, "call", extra):
            with self.assertRaisesRegex(io.Refusal, "kernel_loaded_thread_population"):
                self.prepare()

    def test_rollout_event_or_conversation_is_not_empty_preparation(self):
        self.rollout.write_bytes(
            io.canonical({"type": "event_msg", "payload": {"id": self.thread_id}})
        )
        with self.assertRaisesRegex(io.Refusal, "kernel_rollout_not_metadata_only"):
            self.prepare()

    def test_not_materialized_rollout_is_explicit_and_never_claims_persistence(self):
        self.rollout.unlink()
        result = self.prepare()
        proof = io.document(io.referenced(result["empty_thread_ref"]))
        self.assertIsNone(proof["rollout_checkpoint_ref"])
        self.assertEqual(proof["rollout_observation"], "not_materialized_at_creation")
        self.assertFalse(proof["persistent_history_verified"])

    def test_hold_loss_after_intent_blocks_send_without_unconsuming_intent(self):
        checks = []

        def observe(_value):
            checks.append(True)
            if len(checks) == 3:
                raise io.Refusal("private_hold_lost")

        self.provider.verify_closure = observe
        with self.assertRaisesRegex(io.Refusal, "private_hold_lost"):
            self.prepare()
        self.assertTrue((self.root / "records/thread-start-intent.json").exists())
        self.assertFalse(any(method == "thread/start" for method, _ in self.calls))

    def test_expiry_is_checked_before_any_rpc(self):
        self.binding["deadline_ns"] = time.monotonic_ns() - 1
        with self.assertRaisesRegex(io.Refusal, "binding_expired"):
            self.prepare()
        self.assertFalse(self.calls)

    def test_expiry_after_auth_read_prevents_creating_intent_or_sending(self):
        original = FakeRPC.call

        def expire(rpc, method, params):
            response = original(rpc, method, params)
            if method == "account/read":
                self.binding["deadline_ns"] = time.monotonic_ns() - 1
            return response

        with patch.object(FakeRPC, "call", expire):
            with self.assertRaisesRegex(io.Refusal, "binding_expired"):
                self.prepare()
        self.assertFalse((self.root / "records/thread-start-intent.json").exists())
        self.assertFalse(any(method == "thread/start" for method, _ in self.calls))

    def test_metadata_or_wrong_home_cannot_replace_creation_provenance(self):
        home = io.document(Path(self.home_ref["path"]).read_bytes())
        home["codex_home"] = str(self.root / "old-existing-codex")
        self.home_ref = self.publish("different-home.json", home)
        with self.assertRaisesRegex(io.Refusal, "kernel_fresh_home_manifest"):
            self.prepare()
        self.assertFalse(self.calls)


class KernelBootstrapTests(unittest.TestCase):
    def test_import_and_wrong_main_boundary_have_no_filesystem_effects(self):
        from scripts import j1_forward_cold_kernel_bootstrap as bootstrap
        from scripts import j1_forward_cold_kernel_supervisor as supervisor

        with patch.object(bootstrap, "protected", side_effect=AssertionError("unexpected read")):
            with self.assertRaisesRegex(ValueError, "kernel_bootstrap_boundary"):
                bootstrap.install(
                    Path("/tmp/private-not-production.py"), ["--attempt", str(uuid.uuid4())]
                )
        actual = hashlib.sha256(Path(bootstrap.__file__).read_bytes()).hexdigest()
        self.assertEqual(actual, supervisor.BOOTSTRAP_SHA256)

    def test_loader_rejects_unqualified_module_and_changed_source(self):
        import types

        from scripts import j1_forward_cold_kernel_bootstrap as bootstrap

        loader = bootstrap.SourceLoader({"scripts/j1_forward_private.py": "0" * 64})
        with self.assertRaisesRegex(ImportError, "module_unqualified"):
            loader.find_spec("scripts.j1_forward_other")
        with self.assertRaisesRegex(ImportError, "module_denied"):
            loader.find_spec("scripts.external")
        with patch.object(
            bootstrap, "protected", return_value=b"raise AssertionError('must not execute')"
        ):
            with self.assertRaisesRegex(ImportError, "source_changed"):
                loader.exec_module(types.ModuleType("scripts.j1_forward_private"))


if __name__ == "__main__":
    unittest.main()
