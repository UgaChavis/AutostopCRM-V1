"""Synthetic auth-only material; no Telegram connection or real credentials."""

from __future__ import annotations

import base64
import copy
import os
import sqlite3
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

from scripts import j1_forward_cold_producer_inputs as inputs
from scripts import j1_forward_cold_producer_native as native
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal


def material():
    clock = time.monotonic_ns()
    binding = {
        "attempt_id": str(uuid.uuid4()),
        "boot_id": str(uuid.uuid4()),
        "generation": str(uuid.uuid4()),
        "consumer_sha256": "1" * 64,
        "candidate_crm_sha": "2" * 40,
        "candidate_manager_sha": "3" * 40,
        "deployment_lock_identity": [4, 5],
        "not_before_ns": clock - 1_000_000,
        "deadline_ns": clock + 30_000_000_000,
    }
    document = {
        "schema": inputs.AUTH_SESSION_SCHEMA,
        "scope": "production_native",
        "binding": binding,
        "role": "work-telegram",
        "account_self_sha256": "6" * 64,
        "authorization_id": str(uuid.uuid4()),
        "created_ns": clock,
        "expires_ns": binding["deadline_ns"],
        "dc_id": 2,
        "server_address": "149.154.167.51",
        "port": 443,
        "auth_key_b64": base64.b64encode(b"\xa5" * 256).decode(),
    }
    return binding, document


class AuthSessionContractTests(unittest.TestCase):
    def setUp(self):
        self.binding, self.document = material()

    def build(self, document=None, **kwargs):
        return inputs.auth_session_bytes(
            io.canonical(self.document if document is None else document),
            binding=self.binding,
            role="work-telegram",
            expected_account_sha256="6" * 64,
            **kwargs,
        )

    def test_new_session_has_only_the_exact_empty_telethon_eight_schema(self):
        raw = self.build()
        self.assertEqual(
            inputs.inspect_auth_session(raw),
            {"schema_version": 8, "history_copied": False, "rows": 1},
        )
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.deserialize(raw)
        self.assertEqual(
            connection.execute("SELECT * FROM sessions").fetchall(),
            [(2, "149.154.167.51", 443, b"\xa5" * 256, None, b"")],
        )
        for table in ("entities", "sent_files", "update_state"):
            self.assertEqual(connection.execute(f"SELECT * FROM {table}").fetchall(), [])

    def test_account_role_attempt_and_expiry_cannot_be_rebound(self):
        for key, value in (
            ("account_self_sha256", "7" * 64),
            ("role", "personal-telegram"),
            ("expires_ns", self.binding["not_before_ns"]),
            ("created_ns", self.binding["not_before_ns"] - 1),
            ("authorization_id", "not-a-uuid"),
            ("binding", {**self.binding, "attempt_id": str(uuid.uuid4())}),
        ):
            with self.subTest(key=key), self.assertRaises(io.Refusal):
                self.build({**self.document, key: value})
        with self.assertRaises(io.Refusal):
            self.build(now_ns=self.binding["deadline_ns"])

    def test_no_old_database_or_cached_state_can_enter_the_material_contract(self):
        for key in ("entities", "pts", "outbox", "session_path"):
            with self.subTest(key=key), self.assertRaises(io.Refusal):
                self.build({**self.document, key: []})
        with self.assertRaises(io.Refusal):
            inputs.auth_session_bytes(
                self.build(),
                binding=self.binding,
                role="work-telegram",
                expected_account_sha256="6" * 64,
            )

    def test_invalid_endpoints_and_auth_keys_are_refused_without_network(self):
        for key, value in (
            ("dc_id", True),
            ("dc_id", 0),
            ("dc_id", 6),
            ("port", 80),
            ("server_address", "localhost"),
            ("server_address", "127.0.0.1"),
            ("auth_key_b64", base64.b64encode(bytes(256)).decode()),
            ("auth_key_b64", base64.b64encode(b"x" * 255).decode()),
            ("auth_key_b64", "!" * 344),
        ):
            with self.subTest(key=key, value_type=type(value).__name__):
                with self.assertRaises(io.Refusal):
                    self.build({**self.document, key: value})

    def test_independent_inspector_refuses_a_cursor_trigger_extra_table_or_corruption(self):
        for sql in (
            "INSERT INTO update_state VALUES (1,2,3,4,5)",
            "CREATE TABLE hidden_queue (message text)",
            "CREATE TRIGGER hidden AFTER INSERT ON sessions BEGIN SELECT 1; END",
            "UPDATE sessions SET takeout_id=1",
        ):
            connection = sqlite3.connect(":memory:")
            try:
                connection.deserialize(self.build())
                connection.execute(sql)
                connection.commit()
                with self.assertRaises(io.Refusal):
                    inputs.inspect_auth_session(connection.serialize())
            finally:
                connection.close()
        with self.assertRaises(io.Refusal):
            inputs.inspect_auth_session(self.build()[:-1])


@unittest.skipUnless(os.name == "posix", "Requires private native Linux fixture")
class NativeAuthSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cold-auth-only-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.enterContext(rehearsal.PrivateFilesystem(self.root))
        self.uid, self.gid = (65534 if os.geteuid() == 0 else os.geteuid()), os.getegid()
        self.base = self.root / "role"
        self.base.mkdir(mode=0o700)
        os.chown(self.base, self.uid, self.gid)
        self.binding, self.document = material()
        self.backend = object.__new__(native.NativeBackend)
        self.backend.continuity = Mock()

    def install(self):
        self.backend.install_auth_session(
            io.canonical(self.document),
            self.base,
            self.binding,
            role="work-telegram",
            account="6" * 64,
            uid=self.uid,
            gid=self.gid,
        )
        return self.base / "account.session"

    def test_real_private_file_is_new_role_owned_mutable_and_inert(self):
        target = self.install()
        self.assertEqual((target.stat().st_uid, target.stat().st_gid), (self.uid, self.gid))
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.stat().st_nlink, 1)
        self.assertEqual(inputs.inspect_auth_session(target.read_bytes())["schema_version"], 8)
        self.assertEqual(self.backend.continuity.call_count, 2)
        initial = target.read_bytes()
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(target.read_bytes(), initial)

    def test_sidecar_and_preexisting_target_symlink_are_never_followed(self):
        sidecar = self.base / "account.session-wal"
        sidecar.write_bytes(b"synthetic stale sidecar")
        with self.assertRaisesRegex(io.Refusal, "session_sidecar"):
            self.install()
        self.assertFalse((self.base / "account.session").exists())
        sidecar.unlink()
        untouched = self.root / "untouched"
        untouched.write_bytes(b"private sentinel")
        (self.base / "account.session").symlink_to(untouched)
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(untouched.read_bytes(), b"private sentinel")

    def test_invalid_account_material_never_creates_a_destination(self):
        self.document = copy.deepcopy(self.document)
        self.document["account_self_sha256"] = "7" * 64
        with self.assertRaises(io.Refusal):
            self.install()
        self.assertEqual(list(self.base.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
