from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cleanup_audit_probe_consumer import cleanup_audit_probe  # noqa: E402


class CleanupAuditProbeConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "change_feed.sqlite3"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE events(sequence INTEGER PRIMARY KEY);
                CREATE TABLE consumers(consumer_id TEXT PRIMARY KEY, acked_sequence INTEGER NOT NULL);
                CREATE TABLE deliveries(consumer_id TEXT PRIMARY KEY, window_high_water INTEGER NOT NULL);
                CREATE TABLE digest_snapshots(
                    digest_id TEXT PRIMARY KEY,
                    consumer_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL
                );
                INSERT INTO metadata VALUES('high_water', '2');
                INSERT INTO events VALUES(1);
                INSERT INTO events VALUES(2);
                INSERT INTO consumers VALUES('audit-probe', 0);
                INSERT INTO consumers VALUES('owner', 2);
                INSERT INTO deliveries VALUES('audit-probe', 2);
                INSERT INTO deliveries VALUES('owner', 2);
                INSERT INTO digest_snapshots VALUES('digest-owner', 'owner', 'safe-hash');
                """
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dry_run_then_apply_removes_only_checkpoint_and_preserves_events(self) -> None:
        preview = cleanup_audit_probe(self.database, apply=False)
        applied = cleanup_audit_probe(
            self.database,
            apply=True,
            backup_dir=Path("/verified-backup"),
            backup_verifier=lambda _backup, _database: None,
        )
        absent = cleanup_audit_probe(self.database, apply=False)

        self.assertTrue(preview["present"])
        self.assertFalse(preview["applied"])
        self.assertTrue(applied["applied"])
        self.assertTrue(applied["verified"])
        self.assertFalse(absent["present"])
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            self.assertEqual(
                "2",
                connection.execute(
                    "SELECT value FROM metadata WHERE key = 'high_water'"
                ).fetchone()[0],
            )
            self.assertEqual(
                [("owner", 2)],
                connection.execute("SELECT consumer_id, acked_sequence FROM consumers").fetchall(),
            )
            self.assertEqual(
                [("owner", 2)],
                connection.execute(
                    "SELECT consumer_id, window_high_water FROM deliveries"
                ).fetchall(),
            )
            self.assertEqual(
                [("digest-owner", "owner", "safe-hash")],
                connection.execute(
                    "SELECT digest_id, consumer_id, content_hash FROM digest_snapshots"
                ).fetchall(),
            )

    def test_refuses_nonzero_checkpoint(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "UPDATE consumers SET acked_sequence = 1 WHERE consumer_id = 'audit-probe'"
            )
        with self.assertRaisesRegex(RuntimeError, "audit_probe_checkpoint_not_zero"):
            cleanup_audit_probe(
                self.database,
                apply=True,
                backup_dir=Path("/verified-backup"),
                backup_verifier=lambda _backup, _database: None,
            )

    def test_apply_requires_verified_backup_and_refuses_snapshot_cascade(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "verified_backup_required"):
            cleanup_audit_probe(self.database, apply=True)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "INSERT INTO digest_snapshots VALUES('digest-probe', 'audit-probe', 'hash')"
            )
        with self.assertRaisesRegex(RuntimeError, "audit_probe_digest_snapshots_present"):
            cleanup_audit_probe(
                self.database,
                apply=True,
                backup_dir=Path("/verified-backup"),
                backup_verifier=lambda _backup, _database: None,
            )

    def test_legacy_schema_without_digest_snapshots_is_supported(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DROP TABLE digest_snapshots")

        preview = cleanup_audit_probe(self.database, apply=False)
        applied = cleanup_audit_probe(
            self.database,
            apply=True,
            backup_dir=Path("/verified-backup"),
            backup_verifier=lambda _backup, _database: None,
        )

        self.assertTrue(preview["present"])
        self.assertEqual(0, preview["digest_snapshots"])
        self.assertTrue(applied["verified"])
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM consumers WHERE consumer_id = 'audit-probe'"
                ).fetchone()[0],
            )
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
