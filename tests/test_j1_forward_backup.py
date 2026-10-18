"""Synthetic-only recovery tests; no application imports or production paths."""

import base64
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_backup as b


class ForwardBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="j1-backup-synthetic-")
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "backup"
        self.restore = self.root / "restored"
        self.data = self.source / "state.json"
        self.data.write_text('{"synthetic":true}', encoding="utf-8")
        self.addCleanup(self.temporary.cleanup)
        for mocked in (
            patch.object(b, "ROOTS", (("crm", self.source.as_posix()),)),
            patch.object(b, "native_root", return_value=True),
            patch.object(
                b, "protected_parent", side_effect=lambda p: b.physical(p, directory=True)
            ),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.context = {
            "attempt_id": "00000000-0000-4000-8000-000000000001",
            "predecessor_sha256": "1" * 64,
            "candidate_crm_sha": "2" * 40,
            "candidate_manager_sha": "3" * 40,
            "scope_profile_sha256": b.profile_sha256(),
        }

    def fence(self):
        return {
            "attempt_id": self.context["attempt_id"],
            "path": str(self.root / "closure.json"),
            "sha256": "5" * 64,
        }

    def capture(self, observer=None):
        return b.capture(self.output, self.context, observer or self.fence)

    def verify(self, result):
        return b.verify_and_restore(
            self.output, result["manifest_sha256"], self.restore, self.context
        )

    def manifest(self):
        return json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))

    def altered_manifest(self, change):
        manifest = self.manifest()
        change(manifest)
        path = self.output / "manifest.json"
        path.write_bytes(b.canonical(manifest))
        return {"manifest_sha256": b.digest(path, time.monotonic() + 30)}

    @unittest.skipIf(
        sqlite3.sqlite_version_info < (3, 51, 3), "requires supported native/offhost SQLite"
    )
    def test_committed_wal_nested_sqlite_json_and_fts_restore(self):
        nested = self.source / "plugins"
        nested.mkdir()
        db = nested / "workbench.bin"  # Header detection, not an extension allowlist.
        with closing(sqlite3.connect(db)) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, body TEXT)")
            con.execute("CREATE VIRTUAL TABLE search USING fts5(body)")
            con.execute("INSERT INTO records VALUES(1, 'synthetic')")
            con.execute("INSERT INTO search VALUES('synthetic')")
            con.commit()
            self.assertGreater(Path(str(db) + "-wal").stat().st_size, 0)
            result = self.capture()
            manifest = self.manifest()
            sql = next(item for item in manifest["artifacts"] if item["kind"] == "sqlite")
            self.assertEqual(sql["inspection"]["table_rows"]["records"], 1)
            self.assertEqual(sum(i["detail"] == "sqlite_sidecar" for i in manifest["artifacts"]), 2)
            checked = self.verify(result)
            self.assertEqual(checked["fts5_tables_checked"], 1)
            self.assertTrue(checked["source_evidence_unchanged"])
            self.assertEqual(
                b.digest(self.output / sql["payload"], time.monotonic() + 30), sql["sha256"]
            )

    def test_regular_restore_never_uses_source_destinations(self):
        result = self.capture()
        checked = self.verify(result)
        self.assertTrue(checked["provided_artifacts_restored_exactly"])
        self.assertEqual((self.restore / "000000.bin").read_bytes(), self.data.read_bytes())
        self.assertFalse(checked["native_source_ownership_restored"])
        self.assertFalse(checked["production_restore_performed"])
        for key in b.FALSE_CLAIMS:
            self.assertIs(checked[key], False)

    @staticmethod
    def oauth_ciphertext():
        # Shape-only fixture: deliberately no encryption key or authentic MAC.
        return base64.urlsafe_b64encode(b"\x80" + bytes(8 + 16 + 16 + 32))

    def test_encrypted_oauth_state_restores_exact_opaque_bytes(self):
        raw = self.oauth_ciphertext()
        (self.source / "mcp-oauth-state.json").write_bytes(raw)
        result = self.capture()
        item = next(
            i for i in self.manifest()["artifacts"] if i["logical_name"] == "mcp-oauth-state.json"
        )
        self.assertEqual(item["kind"], "file")
        self.assertNotIn("inspection", item)
        self.verify(result)
        self.assertEqual((self.restore / item["payload"]).read_bytes(), raw)
        self.assertEqual((self.source / "mcp-oauth-state.json").read_bytes(), raw)

    def test_legacy_plaintext_oauth_state_keeps_json_validation(self):
        (self.source / "mcp-oauth-state.json").write_bytes(b' {"synthetic":true}\n')
        result = self.capture()
        item = next(
            i for i in self.manifest()["artifacts"] if i["logical_name"] == "mcp-oauth-state.json"
        )
        self.assertEqual(item["kind"], "json")
        self.assertEqual(item["inspection"]["json"], "valid")
        self.verify(result)

    def test_malformed_oauth_plaintext_or_envelope_is_not_exempt(self):
        token = self.oauth_ciphertext()
        malformed = (
            b'{"broken":',
            b"not-json",
            b" " + token,
            token + b"\n",
            token + b"=",
            base64.urlsafe_b64encode(b"\x81" + bytes(72)),
            base64.urlsafe_b64encode(b"\x80" + bytes(71)),
            base64.urlsafe_b64encode(b"\x80" + bytes(2 * 1024**2)),
        )
        for index, raw in enumerate(malformed):
            with self.subTest(index=index):
                (self.source / "mcp-oauth-state.json").write_bytes(raw)
                output = self.root / f"malformed-{index}"
                with self.assertRaisesRegex(b.BackupRefusal, "invalid_json"):
                    b.capture(output, self.context, self.fence)
                self.assertFalse((output / "manifest.json").exists())

    def test_oauth_ciphertext_exception_is_exact_root_and_component(self):
        raw = self.oauth_ciphertext()
        candidate = self.source / "mcp-oauth-state.json"
        candidate.write_bytes(raw)
        self.assertEqual(b.payload_kind("crm", candidate.name, candidate), "file")
        for component, relative in (
            ("crm", "other.json"),
            ("crm", "nested/mcp-oauth-state.json"),
            ("telegram", candidate.name),
            ("forward_state", candidate.name),
        ):
            with self.subTest(component=component, relative=relative):
                self.assertEqual(b.payload_kind(component, relative, candidate), "json")
                with self.assertRaisesRegex(b.BackupRefusal, "invalid_json"):
                    b.inspect_json(candidate, "json", time.monotonic() + 5)

    def test_forged_opaque_kind_cannot_bypass_plaintext_oauth_validation(self):
        (self.source / "mcp-oauth-state.json").write_text('{"synthetic":true}')
        self.capture()

        def forge(manifest):
            item = next(
                i for i in manifest["artifacts"] if i["logical_name"] == "mcp-oauth-state.json"
            )
            item["kind"] = "file"
            item.pop("inspection")

        with self.assertRaisesRegex(b.BackupRefusal, "artifact_kind_mismatch"):
            self.verify(self.altered_manifest(forge))

    def test_callback_refusal_leaves_no_complete_marker(self):
        calls = 0

        def fail():
            nonlocal calls
            calls += 1
            if calls == 4:
                raise b.BackupRefusal("closure_lost")
            return self.fence()

        with self.assertRaisesRegex(b.BackupRefusal, "closure_lost"):
            self.capture(fail)
        self.assertFalse((self.output / "manifest.json").exists())

    def test_wrong_attempt_refused(self):
        with self.assertRaisesRegex(b.BackupRefusal, "external_closure_reference_invalid"):
            self.capture(
                lambda: {**self.fence(), "attempt_id": "00000000-0000-4000-8000-000000000002"}
            )

    def test_added_source_file_detected_before_manifest_seal(self):
        calls = 0

        def mutate():
            nonlocal calls
            calls += 1
            if calls == 5:
                (self.source / "late.txt").write_text("synthetic")
            return self.fence()

        with self.assertRaisesRegex(b.BackupRefusal, "final_inventory_changed"):
            self.capture(mutate)
        self.assertFalse((self.output / "manifest.json").exists())

    def test_corrupt_payload_rejected(self):
        result = self.capture()
        (self.output / "000000.bin").write_bytes(b"modified")
        with self.assertRaisesRegex(b.BackupRefusal, "artifact_bytes_mismatch"):
            self.verify(result)

    def test_traversal_manifest_rejected(self):
        self.capture()
        result = self.altered_manifest(
            lambda m: m["artifacts"][1].update(payload="../source/state.json")
        )
        with self.assertRaisesRegex(b.BackupRefusal, "payload_path_invalid"):
            self.verify(result)

    def test_extra_payload_rejected(self):
        result = self.capture()
        (self.output / "unexpected.bin").write_bytes(b"synthetic")
        with self.assertRaisesRegex(b.BackupRefusal, "exact_payload_inventory_required"):
            self.verify(result)

    def test_duplicate_json_rejected(self):
        self.data.write_text('{"duplicate":1,"duplicate":2}')
        with self.assertRaisesRegex(b.BackupRefusal, "duplicate_json_key"):
            self.capture()
        self.assertFalse((self.output / "manifest.json").exists())

    def test_existing_output_preserved(self):
        self.output.mkdir()
        keep = self.output / "keep"
        keep.write_text("synthetic")
        with self.assertRaisesRegex(b.BackupRefusal, "new_destination_or_timeout_required"):
            self.capture()
        self.assertEqual(keep.read_text(), "synthetic")

    def test_hardlink_source_rejected(self):
        os.link(self.data, self.source / "alias")
        with self.assertRaisesRegex(b.BackupRefusal, "source_hardlink_refused"):
            self.capture()

    def test_error_in_nested_scandir_fails_closed(self):
        blocked = self.source / "unreadable"
        blocked.mkdir()
        original = os.scandir

        def fail(path):
            if Path(path) == blocked:
                raise PermissionError("synthetic scandir denied")
            return original(path)

        with patch.object(b.os, "scandir", side_effect=fail), self.assertRaises(PermissionError):
            self.capture()
        self.assertFalse((self.output / "manifest.json").exists())

    def test_durable_dependency_lockfiles_are_preserved(self):
        for name in ("Cargo.lock", "uv.lock", "poetry.lock"):
            (self.source / name).write_text("synthetic dependency")
        result = self.capture()
        self.assertEqual(result["files"], 4)

    def test_runtime_lock_is_metadata_only(self):
        with patch.object(b, "ROOTS", (("crm", self.source.as_posix()),)):
            self.context["scope_profile_sha256"] = b.profile_sha256()
            (self.source / "state.lock").write_text("synthetic process lock")
            result = self.capture()
            lock = next(
                i for i in self.manifest()["artifacts"] if i["logical_name"] == "state.lock"
            )
            self.assertEqual(lock["kind"], "runtime")
            self.assertNotIn("payload", lock)
            self.assertEqual(result["files"], 1)

    def test_expected_database_cannot_degrade_to_regular_file(self):
        with patch.object(b, "ROOTS", (("manager", self.data.as_posix()),)):
            self.context["scope_profile_sha256"] = b.profile_sha256()
            with self.assertRaisesRegex(b.BackupRefusal, "required_root_type_invalid"):
                self.capture()

    def test_required_directory_cannot_be_a_symlink(self):
        alias = self.root / "alias"
        try:
            alias.symlink_to(self.source, target_is_directory=True)
        except OSError:
            self.skipTest("Windows symlink privilege unavailable")
        with patch.object(b, "ROOTS", (("synthetic", alias.as_posix()),)):
            with self.assertRaisesRegex(b.BackupRefusal, "required_root_type_invalid"):
                b.build_profile()

    def test_nested_symlink_recorded_without_following(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "not-in-scope").write_text("synthetic")
        try:
            (self.source / "external").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Windows symlink privilege unavailable")
        result = self.capture()
        self.assertEqual(result["files"], 1)
        self.assertEqual(
            next(i for i in self.manifest()["artifacts"] if i["logical_name"] == "external")[
                "kind"
            ],
            "link",
        )
        self.verify(result)

    @unittest.skipIf(
        sqlite3.sqlite_version_info < (3, 51, 3), "requires supported native/offhost SQLite"
    )
    def test_wal_snapshot_size_limited_before_copy(self):
        db = self.source / "large.sqlite"
        with closing(sqlite3.connect(db)) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE payload(value BLOB)")
            con.execute("INSERT INTO payload VALUES(zeroblob(100000))")
            con.commit()
            with (
                patch.object(b, "MAX_FILE_BYTES", 16384),
                self.assertRaisesRegex(b.BackupRefusal, "file_size_limit|sqlite_size_limit"),
            ):
                b.snapshot_sqlite(db, self.root / "partial.sqlite", time.monotonic() + 30, 16384)
            if (self.root / "partial.sqlite").exists():
                self.assertLessEqual((self.root / "partial.sqlite").stat().st_size, 16384)

    def test_hashing_obeys_deadline(self):
        with self.assertRaisesRegex(b.BackupRefusal, "operation_deadline"):
            b.digest(self.data, time.monotonic() - 1)

    def test_wrong_context_and_unsupported_claim_rejected(self):
        result = self.capture()
        with self.assertRaisesRegex(b.BackupRefusal, "manifest_schema_or_context_invalid"):
            b.verify_and_restore(
                self.output,
                result["manifest_sha256"],
                self.restore,
                {**self.context, "candidate_crm_sha": "f" * 40},
            )
        changed = self.altered_manifest(lambda m: m.update(all_writers_quiescent_proven=True))
        with self.assertRaisesRegex(b.BackupRefusal, "unsupported_manifest_claim"):
            self.verify(changed)

    def test_scope_hash_is_platform_independent_posix_alias(self):
        self.assertEqual(
            b.profile_document()["j1_alias"],
            "/run/autostop-j1-cache-view-private/rw/research.sqlite3",
        )

    def test_forward_state_is_durable_but_store_lock_is_metadata(self):
        with patch.object(b, "ROOTS", (("forward_state", self.source.as_posix()),)):
            self.context["scope_profile_sha256"] = b.profile_sha256()
            (self.source / ".store.lock").write_bytes(b"")
            (self.source / "HEAD.json").write_text('{"synthetic":true}')
            result = self.capture()
            artifacts = self.manifest()["artifacts"]
            self.assertEqual(
                next(i["kind"] for i in artifacts if i["logical_name"] == ".store.lock"), "runtime"
            )
            self.assertEqual(
                next(i["kind"] for i in artifacts if i["logical_name"] == "HEAD.json"), "json"
            )
            self.verify(result)

    def test_absent_required_root_cannot_produce_success(self):
        self.capture()
        result = self.altered_manifest(lambda m: m["artifacts"][0].update(kind="absent"))
        with self.assertRaisesRegex(b.BackupRefusal, "required_root_type_invalid"):
            self.verify(result)

    def test_jsonc_workspace_fixture_kept_as_opaque_bytes(self):
        with patch.object(b, "ROOTS", (("codex", self.source.as_posix()),)):
            self.context["scope_profile_sha256"] = b.profile_sha256()
            workspace = self.source / "worktrees" / "synthetic" / ".vscode"
            workspace.mkdir(parents=True)
            (workspace / "settings.json").write_text('{/* JSONC intentionally */ "fixture": true}')
            result = self.capture()
            self.verify(result)

    @unittest.skipIf(
        sqlite3.sqlite_version_info < (3, 51, 3), "requires supported native/offhost SQLite"
    )
    def test_closed_wal_database_without_sidecars_can_be_captured(self):
        db = self.source / "closed.sqlite"
        with closing(sqlite3.connect(db)) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE closed_test(id INTEGER)")
            con.execute("INSERT INTO closed_test VALUES(1)")
            con.commit()
        self.assertFalse(Path(str(db) + "-wal").exists())
        result = self.capture()
        self.verify(result)


if __name__ == "__main__":
    unittest.main()
