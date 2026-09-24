from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
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


def symlink_or_skip(test_case: unittest.TestCase, link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            test_case.skipTest("Windows symlink privilege is unavailable")
        raise


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
        printing_dir = crm_data / "printing"
        printing_dir.mkdir()
        (printing_dir / "completion_act_forms.json").write_text(
            json.dumps(
                {
                    "card-act-1:cycle:1": {
                        "version": 1,
                        "overrides": {"document_number": "ACT-1"},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manager_db = root / "manager.sqlite3"
        with closing(sqlite3.connect(manager_db)) as connection:
            connection.execute("CREATE TABLE manager_runs (id INTEGER PRIMARY KEY, status TEXT)")
            connection.execute("INSERT INTO manager_runs(status) VALUES ('completed')")
            connection.commit()
        return crm_data, manager_db, root / "backups"

    def _convert_completion_act_fixture_to_shards(self, crm_data: Path) -> tuple[Path, Path]:
        printing_dir = crm_data / "printing"
        legacy = printing_dir / "completion_act_forms.json"
        payload = json.loads(legacy.read_text(encoding="utf-8"))
        legacy.unlink()
        shard_dir = printing_dir / "completion_act_forms"
        shard_dir.mkdir()
        if os.name != "nt":
            shard_dir.chmod(0o700)
        for cycle_key, record in payload.items():
            record = {**record, "cycle_key": cycle_key}
            path = shard_dir / f"{hashlib.sha256(cycle_key.encode('utf-8')).hexdigest()}.json"
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            if os.name != "nt":
                path.chmod(0o600)
        return legacy, shard_dir

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
            self.assertEqual(created["schema"], "autostop-agent-release-backup.v3")
            verified = self.module.verify_backup(backup_dir)
            self.assertTrue(verified["ok"])
            self.assertTrue((backup_dir / "audit-archive.tar.gz").is_file())
            self.assertTrue((backup_dir / "change_feed.sqlite3").is_file())
            self.assertTrue((backup_dir / "completion_act_forms.json").is_file())

            (crm_data / "state.json").write_text('{"cards":[]}', encoding="utf-8")
            with closing(sqlite3.connect(crm_data / "change_feed.sqlite3")) as connection:
                connection.execute("UPDATE consumers SET acked_sequence = 99")
                connection.commit()
            with closing(sqlite3.connect(manager_db)) as connection:
                connection.execute("UPDATE manager_runs SET status = 'failed'")
                connection.commit()
            completion_act_forms = crm_data / "printing" / "completion_act_forms.json"
            completion_act_forms.write_text(
                json.dumps({"candidate-only": {"version": 99}}),
                encoding="utf-8",
            )

            crm_restored = self.module.restore_crm_state_and_feed(backup_dir)
            manager_restored = self.module.restore_manager_database(backup_dir)
            self.assertEqual(
                set(crm_restored["restored"]),
                {"state", "change_feed_sqlite", "completion_act_forms"},
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
            restored_forms = json.loads(completion_act_forms.read_text(encoding="utf-8"))
            self.assertIn("card-act-1:cycle:1", restored_forms)
            self.assertNotIn("candidate-only", restored_forms)

    def test_sharded_completion_act_store_round_trips_through_v3_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            legacy, shard_dir = self._convert_completion_act_fixture_to_shards(crm_data)

            created = self.module.create_backup(
                output_root=output_root,
                crm_data_dir=crm_data,
                manager_db=manager_db,
                backup_id="sharded-completion-act",
            )
            backup_dir = Path(created["backup_dir"])
            verified = self.module.verify_backup(backup_dir)
            snapshot = json.loads(
                (backup_dir / "completion_act_forms.json").read_text(encoding="utf-8")
            )
            self.assertTrue(verified["ok"])
            self.assertEqual(created["schema"], self.module.BACKUP_SCHEMA)
            self.assertIn("card-act-1:cycle:1", snapshot)
            self.assertEqual(
                snapshot["card-act-1:cycle:1"]["cycle_key"],
                "card-act-1:cycle:1",
            )
            self.assertTrue(self.module.compare_current_protected_state(backup_dir)["ok"])

            record_path = next(shard_dir.glob("*.json"))
            changed = json.loads(record_path.read_text(encoding="utf-8"))
            changed["version"] = 99
            record_path.write_text(json.dumps(changed), encoding="utf-8")
            self.assertIn(
                "completion_act_forms",
                self.module.compare_current_protected_state(backup_dir)["changed_artifacts"],
            )

            restored = self.module.restore_crm_state_and_feed(backup_dir)
            self.assertIn("completion_act_forms", restored["restored"])
            self.assertFalse(shard_dir.exists())
            self.assertTrue(legacy.is_file())
            restored_payload = json.loads(legacy.read_text(encoding="utf-8"))
            self.assertEqual(restored_payload, snapshot)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(legacy.stat().st_mode), 0o600)

    def test_sharded_backup_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            _, shard_dir = self._convert_completion_act_fixture_to_shards(crm_data)
            record_path = next(shard_dir.glob("*.json"))
            record_payload = record_path.read_bytes()
            record_path.unlink()
            outside = root / "outside.json"
            outside.write_bytes(record_payload)
            symlink_or_skip(self, record_path, outside)

            with self.assertRaisesRegex(self.module.BackupError, "symlink"):
                self.module.create_backup(
                    output_root=output_root,
                    crm_data_dir=crm_data,
                    manager_db=manager_db,
                    backup_id="shard-symlink",
                )
            self.assertFalse((output_root / "shard-symlink").exists())

    def test_sharded_backup_rejects_bounded_directory_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            _, shard_dir = self._convert_completion_act_fixture_to_shards(crm_data)
            record_path = next(shard_dir.glob("*.json"))
            record_payload = record_path.read_bytes()
            extra = shard_dir / ("f" * 64 + ".json")
            extra.write_bytes(record_payload)
            if os.name != "nt":
                extra.chmod(0o600)
            with (
                patch.object(self.module, "COMPLETION_ACT_FORMS_MAX_RECORDS", 1),
                self.assertRaisesRegex(self.module.BackupError, "too many entries"),
            ):
                self.module.create_backup(
                    output_root=output_root,
                    crm_data_dir=crm_data,
                    manager_db=manager_db,
                    backup_id="shard-count-overflow",
                )
            self.assertFalse((output_root / "shard-count-overflow").exists())

    def test_completion_act_backup_rejects_dangling_legacy_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            legacy = crm_data / "printing" / "completion_act_forms.json"
            legacy.unlink()
            symlink_or_skip(self, legacy, root / "missing-completion-act-store.json")

            with self.assertRaisesRegex(self.module.BackupError, "not a regular file"):
                self.module.create_backup(
                    output_root=output_root,
                    crm_data_dir=crm_data,
                    manager_db=manager_db,
                    backup_id="dangling-legacy-symlink",
                )
            self.assertFalse((output_root / "dangling-legacy-symlink").exists())

    def test_backup_records_and_restores_original_file_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            targets = {
                "state": crm_data / "state.json",
                "change_feed_sqlite": crm_data / "change_feed.sqlite3",
                "manager_sqlite": manager_db,
                "completion_act_forms": (crm_data / "printing" / "completion_act_forms.json"),
            }
            if os.name != "nt":
                for path, mode in zip(targets.values(), (0o640, 0o620, 0o660, 0o600), strict=True):
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
            targets["completion_act_forms"].write_text("{}", encoding="utf-8")
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

    def test_completion_act_restore_recreates_private_runtime_directory_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            completion_act_forms = crm_data / "printing" / "completion_act_forms.json"
            expected_metadata = self.module._file_restore_metadata(completion_act_forms)
            created = self.module.create_backup(
                output_root=output_root,
                crm_data_dir=crm_data,
                manager_db=manager_db,
                backup_id="completion-parent-restore",
            )
            shutil.rmtree(completion_act_forms.parent)

            restored = self.module.restore_crm_state_and_feed(Path(created["backup_dir"]))

            self.assertIn("completion_act_forms", restored["restored"])
            self.assertEqual(
                self.module._file_restore_metadata(completion_act_forms), expected_metadata
            )
            if os.name != "nt":
                parent_stat = completion_act_forms.parent.stat()
                self.assertEqual(stat.S_IMODE(parent_stat.st_mode), 0o700)
                self.assertEqual(parent_stat.st_uid, expected_metadata["uid"])
                self.assertEqual(parent_stat.st_gid, expected_metadata["gid"])

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
            completion_act_forms = crm_data / "printing" / "completion_act_forms.json"
            completion_act_forms.unlink()
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
            manifest["artifacts"].pop("completion_act_forms")
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
            completion_act_forms.write_text(
                json.dumps({"candidate-only": {"version": 1}}), encoding="utf-8"
            )
            Path(f"{change_feed}-wal").touch()
            Path(f"{change_feed}-shm").touch()

            crm_restored = self.module.restore_crm_state_and_feed(backup_dir)
            manager_restored = self.module.restore_manager_database(backup_dir)

            self.assertEqual(
                {"state", "change_feed_sqlite", "completion_act_forms"},
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
            self.assertFalse(completion_act_forms.exists())

    def test_v2_backup_normalizes_absent_completion_act_store_for_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            completion_act_forms = crm_data / "printing" / "completion_act_forms.json"
            completion_act_forms.unlink()
            created = self.module.create_backup(
                output_root=output_root,
                crm_data_dir=crm_data,
                manager_db=manager_db,
                backup_id="retained-v2-release",
            )
            backup_dir = Path(created["backup_dir"])
            manifest_path = backup_dir / self.module.MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema"] = self.module.PREVIOUS_BACKUP_SCHEMA
            manifest["artifacts"].pop("completion_act_forms")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            loaded = self.module._load_manifest(backup_dir)
            self.assertIsNone(loaded["artifacts"]["completion_act_forms"])
            self.assertTrue(self.module.verify_backup(backup_dir)["ok"])

            completion_act_forms.write_text(
                json.dumps({"candidate-only": {"version": 1}}), encoding="utf-8"
            )
            restored = self.module.restore_crm_state_and_feed(backup_dir)

            self.assertIn("completion_act_forms", restored["restored"])
            self.assertFalse(completion_act_forms.exists())

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

    def test_completion_act_draft_backup_is_bounded_and_requires_json_object(self) -> None:
        self.assertEqual(
            self.module.COMPLETION_ACT_FORMS_MAX_BYTES,
            64 * 1024 * 1024,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crm_data, manager_db, output_root = self._fixture(root)
            completion_act_forms = crm_data / "printing" / "completion_act_forms.json"
            completion_act_forms.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(self.module.BackupError, "JSON object"):
                self.module.create_backup(
                    output_root=output_root,
                    crm_data_dir=crm_data,
                    manager_db=manager_db,
                    backup_id="invalid-completion-act-store",
                )
            self.assertFalse((output_root / "invalid-completion-act-store").exists())

            invalid_records = (
                ('{"bad":[]}', "record is not a JSON object"),
                ('{"bad":{"version":"1","overrides":{}}}', "version is invalid"),
                ('{"bad":{"version":NaN,"overrides":{}}}', "not valid JSON"),
            )
            for index, (payload, message) in enumerate(invalid_records, start=1):
                completion_act_forms.write_text(payload, encoding="utf-8")
                backup_id = f"invalid-completion-act-record-{index}"
                with (
                    self.subTest(payload=payload),
                    self.assertRaisesRegex(self.module.BackupError, message),
                ):
                    self.module.create_backup(
                        output_root=output_root,
                        crm_data_dir=crm_data,
                        manager_db=manager_db,
                        backup_id=backup_id,
                    )
                self.assertFalse((output_root / backup_id).exists())

            completion_act_forms.write_bytes(b"{" + (b"x" * 128) + b"}")
            with (
                patch.object(self.module, "COMPLETION_ACT_FORMS_MAX_BYTES", 64),
                self.assertRaisesRegex(self.module.BackupError, "bounded backup size"),
            ):
                self.module.create_backup(
                    output_root=output_root,
                    crm_data_dir=crm_data,
                    manager_db=manager_db,
                    backup_id="oversized-completion-act-store",
                )
            self.assertFalse((output_root / "oversized-completion-act-store").exists())

    def test_verify_rejects_logically_invalid_completion_act_records_with_matching_hash(
        self,
    ) -> None:
        invalid_records = (
            ('{"bad":[]}', "record is not a JSON object"),
            ('{"bad":{"version":NaN,"overrides":{}}}', "not valid JSON"),
        )
        for index, (payload, message) in enumerate(invalid_records, start=1):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                crm_data, manager_db, output_root = self._fixture(root)
                created = self.module.create_backup(
                    output_root=output_root,
                    crm_data_dir=crm_data,
                    manager_db=manager_db,
                    backup_id=f"logical-verification-{index}",
                )
                backup_dir = Path(created["backup_dir"])
                artifact_path = backup_dir / "completion_act_forms.json"
                artifact_path.write_text(payload, encoding="utf-8")
                manifest_path = backup_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                metadata = manifest["artifacts"]["completion_act_forms"]
                metadata["size_bytes"] = artifact_path.stat().st_size
                metadata["sha256"] = self.module._sha256(artifact_path)
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, allow_nan=False),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(self.module.BackupError, message):
                    self.module.verify_backup(backup_dir)


if __name__ == "__main__":
    unittest.main()
