from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

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
