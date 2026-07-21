from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "agent_release_backup.py"


def load_module():
    spec = importlib.util.spec_from_file_location("agent_release_backup", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("agent_release_backup.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AgentReleaseBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        crm_data = root / "crm-data"
        crm_data.mkdir()
        (crm_data / "state.json").write_text(
            json.dumps({"schema_version": 9, "cards": [{"id": "C-1"}]}),
            encoding="utf-8",
        )
        audit_dir = crm_data / "audit-archive"
        audit_dir.mkdir()
        (audit_dir / "2026-07.jsonl").write_text('{"event_id":"E-1"}\n', encoding="utf-8")
        with closing(sqlite3.connect(crm_data / "change_feed.sqlite3")) as connection:
            connection.execute(
                "CREATE TABLE consumers (consumer_id TEXT PRIMARY KEY, acked_sequence INTEGER)"
            )
            connection.execute("INSERT INTO consumers VALUES ('owner', 41)")
            connection.commit()
        manager_db = root / "manager.sqlite3"
        with closing(sqlite3.connect(manager_db)) as connection:
            connection.execute("CREATE TABLE manager_runs (id INTEGER PRIMARY KEY, status TEXT)")
            connection.execute("INSERT INTO manager_runs(status) VALUES ('completed')")
            connection.commit()
        return crm_data, manager_db, root / "backups"

    def test_create_verify_and_restore_changed_protected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            created = self.module.create_backup(
                output_root=output_root,
                crm_data_dir=crm_data,
                manager_db=manager_db,
                backup_id="release-1",
            )
            backup_dir = Path(created["backup_dir"])
            self.assertTrue(created["ok"])
            self.assertEqual(created["schema"], "autostop-agent-release-backup.v2")
            verified = self.module.verify_backup(backup_dir)
            self.assertTrue(verified["ok"])
            self.assertTrue((backup_dir / "audit-archive.tar.gz").is_file())
            self.assertTrue((backup_dir / "change_feed.sqlite3").is_file())

            (crm_data / "state.json").write_text('{"cards":[]}', encoding="utf-8")
            with closing(sqlite3.connect(crm_data / "change_feed.sqlite3")) as connection:
                connection.execute("UPDATE consumers SET acked_sequence = 99")
                connection.commit()
            with closing(sqlite3.connect(manager_db)) as connection:
                connection.execute("UPDATE manager_runs SET status = 'failed'")
                connection.commit()

            crm_restored = self.module.restore_crm_state_and_feed(backup_dir)
            manager_restored = self.module.restore_manager_database(backup_dir)
            self.assertEqual(
                set(crm_restored["restored"]),
                {"state", "change_feed_sqlite"},
            )
            self.assertEqual(manager_restored["restored"], ["manager_sqlite"])
            state = json.loads((crm_data / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["cards"][0]["id"], "C-1")
            with closing(sqlite3.connect(manager_db)) as connection:
                status = connection.execute("SELECT status FROM manager_runs").fetchone()[0]
            self.assertEqual(status, "completed")
            with closing(sqlite3.connect(crm_data / "change_feed.sqlite3")) as connection:
                acked = connection.execute(
                    "SELECT acked_sequence FROM consumers WHERE consumer_id = 'owner'"
                ).fetchone()[0]
            self.assertEqual(acked, 41)

    def test_backup_records_and_restores_original_file_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            targets = {
                "state": crm_data / "state.json",
                "change_feed_sqlite": crm_data / "change_feed.sqlite3",
                "manager_sqlite": manager_db,
            }
            if os.name != "nt":
                for path, mode in zip(targets.values(), (0o640, 0o620, 0o660), strict=True):
                    path.chmod(mode)
            expected = {
                name: self.module._file_restore_metadata(path) for name, path in targets.items()
            }

            created = self.module.create_backup(
                output_root=output_root,
                crm_data_dir=crm_data,
                manager_db=manager_db,
                backup_id="metadata-release",
            )
            backup_dir = Path(created["backup_dir"])
            for name, metadata in expected.items():
                self.assertEqual(created["artifacts"][name]["restore_metadata"], metadata)

            targets["state"].write_text('{"cards":[]}', encoding="utf-8")
            with closing(sqlite3.connect(targets["change_feed_sqlite"])) as connection:
                connection.execute("UPDATE consumers SET acked_sequence = 99")
                connection.commit()
            with closing(sqlite3.connect(targets["manager_sqlite"])) as connection:
                connection.execute("UPDATE manager_runs SET status = 'failed'")
                connection.commit()
            if os.name != "nt":
                for path in targets.values():
                    path.chmod(0o666)

            self.module.restore_crm_state_and_feed(backup_dir)
            self.module.restore_manager_database(backup_dir)

            for name, path in targets.items():
                restored = self.module._file_restore_metadata(path)
                self.assertEqual(restored, expected[name])

    def test_atomic_restore_applies_validated_metadata_and_uses_unique_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.bin"
            destination = root / "destination.bin"
            source.write_bytes(b"restored")
            destination.write_bytes(b"candidate")
            metadata = {"mode": 0o640, "uid": 123, "gid": 456}

            with patch.object(self.module.os, "chown", create=True) as chown:
                self.module._restore_file_atomic(
                    source,
                    destination,
                    restore_metadata=metadata,
                )

            self.assertEqual(destination.read_bytes(), b"restored")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o640)
            self.assertEqual(chown.call_count, 1)
            self.assertEqual(chown.call_args.args[1:], (123, 456))
            self.assertEqual(list(root.glob(".destination.bin.restore-*")), [])

    def test_legacy_manifest_restore_preserves_existing_metadata_and_fails_if_missing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.bin"
            destination = root / "destination.bin"
            source.write_bytes(b"restored")
            destination.write_bytes(b"candidate")
            if os.name != "nt":
                destination.chmod(0o604)
            expected = self.module._file_restore_metadata(destination)

            self.module._restore_file_atomic(source, destination)

            self.assertEqual(destination.read_bytes(), b"restored")
            self.assertEqual(self.module._file_restore_metadata(destination), expected)
            destination.unlink()
            with self.assertRaisesRegex(self.module.BackupError, "metadata is missing"):
                self.module._restore_file_atomic(source, destination)
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".destination.bin.restore-*")), [])

    def test_restore_metadata_validation_fails_closed(self) -> None:
        invalid_values = (
            None,
            {"mode": True, "uid": 1, "gid": 1},
            {"mode": -1, "uid": 1, "gid": 1},
            {"mode": 0o600, "uid": -1, "gid": 1},
            {"mode": 0o600, "uid": 1, "gid": self.module.MAX_RESTORE_ID + 1},
            {"mode": 0o600, "uid": 1, "gid": 1, "extra": 1},
        )
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(self.module.BackupError):
                self.module._validated_restore_metadata(value, label="state")

    def test_create_cli_returns_zero_for_a_complete_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "create",
                    "--output-root",
                    str(output_root),
                    "--crm-data-dir",
                    str(crm_data),
                    "--manager-db",
                    str(manager_db),
                    "--backup-id",
                    "cli-success",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertTrue(result["ok"])
            self.assertTrue(result["complete"])
            self.assertTrue(Path(result["backup_dir"]).is_dir())

    def test_restore_removes_feed_created_after_backup_of_legacy_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            (crm_data / "change_feed.sqlite3").unlink()
            created = self.module.create_backup(
                output_root=output_root,
                crm_data_dir=crm_data,
                manager_db=manager_db,
                backup_id="legacy-release",
            )
            backup_dir = Path(created["backup_dir"])
            self.assertIsNone(created["artifacts"]["change_feed_sqlite"])

            with closing(sqlite3.connect(crm_data / "change_feed.sqlite3")) as connection:
                connection.execute("CREATE TABLE candidate_only (id INTEGER PRIMARY KEY)")
                connection.commit()
            Path(f"{crm_data / 'change_feed.sqlite3'}-wal").touch()
            Path(f"{crm_data / 'change_feed.sqlite3'}-shm").touch()

            restored = self.module.restore_crm_state_and_feed(backup_dir)

            self.assertIn("change_feed_sqlite", restored["restored"])
            self.assertFalse((crm_data / "change_feed.sqlite3").exists())
            self.assertFalse(Path(f"{crm_data / 'change_feed.sqlite3'}-wal").exists())
            self.assertFalse(Path(f"{crm_data / 'change_feed.sqlite3'}-shm").exists())

    def test_v1_backup_verifies_and_restores_state_manager_and_absent_feed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            change_feed = crm_data / "change_feed.sqlite3"
            change_feed.unlink()
            created = self.module.create_backup(
                output_root=output_root,
                crm_data_dir=crm_data,
                manager_db=manager_db,
                backup_id="retained-v1-release",
            )
            backup_dir = Path(created["backup_dir"])
            manifest_path = backup_dir / self.module.MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema"] = self.module.LEGACY_BACKUP_SCHEMA
            manifest["artifacts"].pop("change_feed_sqlite")
            for artifact_name in ("state", "manager_sqlite"):
                manifest["artifacts"][artifact_name].pop("restore_metadata")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            loaded = self.module._load_manifest(backup_dir)
            self.assertIsNone(loaded["artifacts"]["change_feed_sqlite"])
            verified = self.module.verify_backup(backup_dir)
            self.assertTrue(verified["ok"])
            self.assertNotIn("change_feed_sqlite", verified["verified_artifacts"])

            (crm_data / "state.json").write_text('{"cards":[]}', encoding="utf-8")
            with closing(sqlite3.connect(manager_db)) as connection:
                connection.execute("UPDATE manager_runs SET status = 'failed'")
                connection.commit()
            with closing(sqlite3.connect(change_feed)) as connection:
                connection.execute("CREATE TABLE candidate_only (id INTEGER PRIMARY KEY)")
                connection.commit()
            Path(f"{change_feed}-wal").touch()
            Path(f"{change_feed}-shm").touch()

            crm_restored = self.module.restore_crm_state_and_feed(backup_dir)
            manager_restored = self.module.restore_manager_database(backup_dir)

            self.assertEqual(
                {"state", "change_feed_sqlite"},
                set(crm_restored["restored"]),
            )
            self.assertEqual(["manager_sqlite"], manager_restored["restored"])
            state = json.loads((crm_data / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("C-1", state["cards"][0]["id"])
            with closing(sqlite3.connect(manager_db)) as connection:
                status = connection.execute("SELECT status FROM manager_runs").fetchone()[0]
            self.assertEqual("completed", status)
            self.assertFalse(change_feed.exists())
            self.assertFalse(Path(f"{change_feed}-wal").exists())
            self.assertFalse(Path(f"{change_feed}-shm").exists())

    def test_malformed_or_inexact_v1_manifest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_dir = Path(temp_dir)
            manifest_path = backup_dir / self.module.MANIFEST_NAME
            malformed = {
                "schema": self.module.LEGACY_BACKUP_SCHEMA,
                "complete": True,
                "artifacts": [],
            }
            manifest_path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaisesRegex(self.module.BackupError, "manifest"):
                self.module.verify_backup(backup_dir)

            malformed["schema"] = f"{self.module.LEGACY_BACKUP_SCHEMA}.unexpected"
            malformed["artifacts"] = {}
            manifest_path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaisesRegex(self.module.BackupError, "Unsupported"):
                self.module.verify_backup(backup_dir)

    def test_manifest_cannot_verify_one_file_and_restore_another(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            created = self.module.create_backup(
                output_root=output_root,
                crm_data_dir=crm_data,
                manager_db=manager_db,
                backup_id="confused-artifact",
            )
            backup_dir = Path(created["backup_dir"])
            manifest_path = backup_dir / self.module.MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            verified_copy = backup_dir / "verified-copy.json"
            verified_copy.write_bytes((backup_dir / self.module.STATE_BACKUP_NAME).read_bytes())
            manifest["artifacts"]["state"]["name"] = verified_copy.name
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            (backup_dir / self.module.STATE_BACKUP_NAME).write_text(
                '{"schema_version":9,"cards":[{"id":"TAMPERED"}]}',
                encoding="utf-8",
            )
            candidate_state = '{"schema_version":9,"cards":[{"id":"CANDIDATE"}]}'
            (crm_data / "state.json").write_text(candidate_state, encoding="utf-8")

            with self.assertRaisesRegex(self.module.BackupError, "artifact filename"):
                self.module.verify_backup(backup_dir)
            with self.assertRaisesRegex(self.module.BackupError, "artifact filename"):
                self.module.restore_crm_state_and_feed(backup_dir)
            self.assertEqual((crm_data / "state.json").read_text(encoding="utf-8"), candidate_state)

    def test_v2_manifest_requires_exact_artifacts_and_restore_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            created = self.module.create_backup(
                output_root=output_root,
                crm_data_dir=crm_data,
                manager_db=manager_db,
                backup_id="strict-v2",
            )
            backup_dir = Path(created["backup_dir"])
            manifest_path = backup_dir / self.module.MANIFEST_NAME
            original = json.loads(manifest_path.read_text(encoding="utf-8"))

            extra_artifact = json.loads(json.dumps(original))
            extra_artifact["artifacts"]["unexpected"] = None
            manifest_path.write_text(json.dumps(extra_artifact), encoding="utf-8")
            with self.assertRaisesRegex(self.module.BackupError, "artifact keys"):
                self.module.verify_backup(backup_dir)

            missing_metadata = json.loads(json.dumps(original))
            missing_metadata["artifacts"]["state"].pop("restore_metadata")
            manifest_path.write_text(json.dumps(missing_metadata), encoding="utf-8")
            with self.assertRaisesRegex(self.module.BackupError, "metadata fields"):
                self.module.verify_backup(backup_dir)

    def test_manager_restore_discards_wal_only_candidate_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            with closing(sqlite3.connect(manager_db)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].casefold(),
                    "wal",
                )
            created = self.module.create_backup(
                output_root=output_root,
                crm_data_dir=crm_data,
                manager_db=manager_db,
                backup_id="wal-only-release",
            )
            backup_dir = Path(created["backup_dir"])
            main_hash_before = self.module._sha256(manager_db)

            candidate = """
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA journal_mode=WAL")
connection.execute("PRAGMA wal_autocheckpoint=0")
connection.execute("UPDATE manager_runs SET status = 'candidate'")
connection.commit()
os._exit(0)
"""
            subprocess.run([sys.executable, "-c", candidate, str(manager_db)], check=True)

            self.assertEqual(main_hash_before, self.module._sha256(manager_db))
            self.assertTrue(Path(f"{manager_db}-wal").is_file())
            comparison = self.module.compare_current_protected_state(backup_dir)
            self.assertFalse(comparison["ok"])
            self.assertIn("manager_sqlite", comparison["changed_artifacts"])

            restored = self.module.restore_manager_database(backup_dir)

            self.assertEqual(["manager_sqlite"], restored["restored"])
            self.assertFalse(Path(f"{manager_db}-wal").exists())
            self.assertFalse(Path(f"{manager_db}-shm").exists())
            with closing(sqlite3.connect(manager_db)) as connection:
                status = connection.execute("SELECT status FROM manager_runs").fetchone()[0]
            self.assertEqual("completed", status)

    def test_change_feed_restore_discards_wal_before_opening_restored_main(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            change_feed = crm_data / "change_feed.sqlite3"
            with closing(sqlite3.connect(change_feed)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].casefold(),
                    "wal",
                )
            created = self.module.create_backup(
                output_root=output_root,
                crm_data_dir=crm_data,
                manager_db=manager_db,
                backup_id="feed-wal-release",
            )
            backup_dir = Path(created["backup_dir"])
            main_hash_before = self.module._sha256(change_feed)
            candidate = """
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA journal_mode=WAL")
connection.execute("PRAGMA wal_autocheckpoint=0")
connection.execute("UPDATE consumers SET acked_sequence = 99")
connection.commit()
os._exit(0)
"""
            subprocess.run([sys.executable, "-c", candidate, str(change_feed)], check=True)
            self.assertEqual(main_hash_before, self.module._sha256(change_feed))
            self.assertTrue(Path(f"{change_feed}-wal").is_file())

            restored = self.module.restore_crm_state_and_feed(backup_dir)

            self.assertIn("change_feed_sqlite", restored["restored"])
            self.assertFalse(Path(f"{change_feed}-wal").exists())
            self.assertFalse(Path(f"{change_feed}-shm").exists())
            with closing(sqlite3.connect(change_feed)) as connection:
                acked = connection.execute(
                    "SELECT acked_sequence FROM consumers WHERE consumer_id = 'owner'"
                ).fetchone()[0]
            self.assertEqual(41, acked)

    def test_incomplete_backup_directory_is_never_published(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data = root / "crm-data"
            crm_data.mkdir()
            (crm_data / "state.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(self.module.BackupError):
                self.module.create_backup(
                    output_root=root / "backups",
                    crm_data_dir=crm_data,
                    manager_db=root / "missing.sqlite3",
                    backup_id="release-failed",
                )

            self.assertFalse((root / "backups" / "release-failed").exists())


if __name__ == "__main__":
    unittest.main()
