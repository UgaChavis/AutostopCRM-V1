from __future__ import annotations

import importlib.util
import json
import sqlite3
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
            verified = self.module.verify_backup(backup_dir)
            self.assertTrue(verified["ok"])
            self.assertTrue((backup_dir / "audit-archive.tar.gz").is_file())

            (crm_data / "state.json").write_text('{"cards":[]}', encoding="utf-8")
            with closing(sqlite3.connect(manager_db)) as connection:
                connection.execute("UPDATE manager_runs SET status = 'failed'")
                connection.commit()

            restored = self.module.restore_changed_state_and_manager(backup_dir)
            self.assertEqual(set(restored["restored"]), {"state", "manager_sqlite"})
            state = json.loads((crm_data / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["cards"][0]["id"], "C-1")
            with closing(sqlite3.connect(manager_db)) as connection:
                status = connection.execute("SELECT status FROM manager_runs").fetchone()[0]
            self.assertEqual(status, "completed")

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
