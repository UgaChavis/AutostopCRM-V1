from __future__ import annotations

import gc
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.module_loader_support import load_module_from_file
    from tests.source_path_support import prepend_source_path
else:
    from module_loader_support import load_module_from_file
    from source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.storage import change_feed_store as feed_module
from minimal_kanban.storage import idle_wal_anchor as anchor_module
from minimal_kanban.storage.change_feed_store import ChangeFeedProtocolError, ChangeFeedStore

ROOT = Path(__file__).resolve().parents[1]
REAL_CONNECT = sqlite3.connect


class ObservedConnection(sqlite3.Connection):
    closed = False
    fail_commit = False
    fail_setup = False
    fail_close = False
    fail_rollback = False
    fail_interrupt = False

    def execute(self, sql, *args, **kwargs):
        if self.fail_setup and sql == "PRAGMA foreign_keys = ON":
            raise sqlite3.OperationalError("synthetic configuration failure")
        return super().execute(sql, *args, **kwargs)

    def commit(self):
        if self.fail_interrupt:
            raise KeyboardInterrupt("synthetic interrupted commit")
        if self.fail_commit:
            raise sqlite3.OperationalError("synthetic commit failure")
        return super().commit()

    def rollback(self):
        if self.fail_rollback:
            raise sqlite3.OperationalError("synthetic rollback failure")
        return super().rollback()

    def close(self):
        if self.fail_close:
            raise sqlite3.OperationalError("synthetic close failure")
        super().close()
        self.closed = True


class _FeedFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.crm = self.directory / "crm"
        self.crm.mkdir()
        self.path = self.crm / "change_feed.sqlite3"
        self.feed = ChangeFeedStore(self.path)
        self.feed.initialize_baseline([])
        self.addCleanup(self.feed.close)

    def seed(self, *identities: str, durable: bool = True) -> None:
        with self.feed._transaction(immediate=True, durable=durable) as connection:
            for identity in identities:
                self.feed._publish_event(
                    connection,
                    dict(
                        source_event_id=identity,
                        occurred_at="2026-01-01T00:00:00Z",
                        action="card_updated",
                        entity_type="card",
                        entity_id=identity,
                        change_type="update",
                        tombstone=False,
                        correlation_ref="",
                        idempotency_ref="",
                        producer="test",
                    ),
                )

    def observe(self, *, fail_commit: bool = False, fail_setup: bool = False):
        connections = []

        def factory(*args, **kwargs):
            connection = REAL_CONNECT(*args, factory=ObservedConnection, **kwargs)
            connection.fail_commit = fail_commit
            connection.fail_setup = fail_setup
            connections.append(connection)
            self.addCleanup(connection.close)
            return connection

        return connections, patch.object(feed_module.sqlite3, "connect", side_effect=factory)

    def assert_closed(self, connections) -> None:
        self.assertTrue(connections)
        for connection in connections:
            self.assertTrue(connection.closed)
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def assert_no_consumer(self, name: str) -> None:
        with closing(REAL_CONNECT(self.path)) as connection:
            for table in ("consumers", "deliveries"):
                self.assertEqual(
                    0,
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE consumer_id = ?", (name,)
                    ).fetchone()[0],
                )


class ChangeFeedCheckpointLifecycleTests(_FeedFixture):
    def test_full_foreign_keys_and_default_checkpoint_policy_are_preserved(self) -> None:
        with self.feed._transaction(immediate=True) as connection:
            self.assertEqual(2, connection.execute("PRAGMA synchronous").fetchone()[0])
            self.assertEqual(1, connection.execute("PRAGMA foreign_keys").fetchone()[0])
            self.assertEqual(1000, connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0])
            self.assertEqual("wal", connection.execute("PRAGMA journal_mode").fetchone()[0])
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO deliveries VALUES ('missing-consumer', 1)")

    def test_committed_normal_wal_replays_in_new_process_without_saved_shm(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Retained WAL lifecycle is enabled only on Linux")
        recovered = self.directory / "recovered"
        recovered.mkdir()
        self.seed("first", "second", durable=False)
        first = self.feed.read_page("owner", limit=1)
        wal = Path(f"{self.path}-wal")
        self.assertGreater(wal.stat().st_size, 0)
        # Quiescent disposable snapshot models WAL recovery without trusting SHM.
        shutil.copyfile(self.path, recovered / self.path.name)
        shutil.copyfile(wal, recovered / wal.name)
        self.assertFalse((recovered / f"{self.path.name}-shm").exists())
        script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from minimal_kanban.storage.change_feed_store import ChangeFeedStore
feed = ChangeFeedStore(Path(sys.argv[2]))
first = json.loads(sys.argv[3])
assert feed.read_page('owner', cursor=first['replay_cursor']) == first
second = feed.read_page('owner', cursor=first['next_cursor'])
assert [e['event_id'] for e in second['events']] == ['second']
assert feed.acknowledge('owner', first['ack'])['acked_sequence'] == 1
assert feed.acknowledge('owner', second['ack'])['delivery_complete']
assert not feed.acknowledge('owner', second['ack'])['changed']
status = {'acked_sequence': feed.bootstrap('owner')['acked_sequence']}
feed.close()
print(json.dumps(status))
"""
        environment = {
            name: os.environ[name]
            for name in ("PATH", "SYSTEMROOT", "WINDIR")
            if name in os.environ
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                script,
                str(ROOT / "src"),
                str(recovered / self.path.name),
                json.dumps(first),
            ],
            env=environment,
            cwd=recovered,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"acked_sequence": 2}, json.loads(result.stdout))

    def test_online_backup_includes_committed_wal_and_restore_discards_stale_sidecars(self) -> None:
        backup = load_module_from_file(
            "checkpoint_backup", ROOT / "scripts/agent_release_backup.py"
        )
        (self.crm / "state.json").write_text('{"schema_version":9,"cards":[]}', encoding="utf-8")
        manager = self.directory / "manager.sqlite3"
        with closing(REAL_CONNECT(manager)) as connection:
            connection.execute("CREATE TABLE technical_marker (id INTEGER)")
            connection.commit()
        if sys.platform != "linux":
            self.skipTest("Retained WAL fixture is enabled only on Linux")
        self.seed("first", "second", durable=False)
        first = self.feed.read_page("owner", limit=1)
        self.assertGreater(Path(f"{self.path}-wal").stat().st_size, 0)
        created = backup.create_backup(
            output_root=self.directory / "backups",
            crm_data_dir=self.crm,
            manager_db=manager,
            backup_id="checkpoint-test",
        )
        self.seed("candidate-only", durable=False)
        stale = {suffix: Path(f"{self.path}{suffix}").read_bytes() for suffix in ("-wal", "-shm")}
        self.feed.close()
        # The restore target is quiescent. Reintroduce candidate sidecars to prove
        # canonical restore removes them before opening the restored database.
        for suffix, content in stale.items():
            Path(f"{self.path}{suffix}").write_bytes(content)
        restored = backup.restore_crm_state_and_feed(Path(created["backup_dir"]))
        self.assertIn("change_feed_sqlite", restored["restored"])
        for suffix in stale:
            self.assertFalse(Path(f"{self.path}{suffix}").exists())
        restarted = ChangeFeedStore(self.path)
        self.addCleanup(restarted.close)
        self.assertEqual(
            ["first", "second"], [row["event_id"] for row in restarted.raw_events_for_test()]
        )
        self.assertEqual(first, restarted.read_page("owner", cursor=first["replay_cursor"]))

    def test_protocol_error_rolls_back_partial_consumer_and_closes_handle(self) -> None:
        self.seed("first")
        first = self.feed.read_page("owner")
        self.feed.close()
        connections, observing = self.observe()
        with observing, self.assertRaises(ChangeFeedProtocolError) as failure:
            self.feed.read_page("other", cursor=first["replay_cursor"])
        self.assertEqual("cursor_consumer_mismatch", failure.exception.code)
        self.assert_closed(connections)
        self.assert_no_consumer("other")

    def test_failed_commit_rolls_back_registration_and_next_request_succeeds(self) -> None:
        self.seed("first")
        if sys.platform != "linux":
            self.skipTest("Reusable page failure cleanup is Linux only")
        for name, error, rollback_fails in (
            ("commit", sqlite3.OperationalError, False),
            ("rollback", sqlite3.OperationalError, True),
            ("interrupt", KeyboardInterrupt, False),
        ):
            with self.subTest(failure=name):
                self.feed.close()
                connections, observing = self.observe()
                with observing:
                    self.feed._wal_anchor.ensure_open()
                keeper = connections[-1]
                keeper.fail_commit = name != "interrupt"
                keeper.fail_interrupt = name == "interrupt"
                keeper.fail_rollback = rollback_fails
                with self.assertRaisesRegex(error, "synthetic"):
                    self.feed.read_page(name)
                self.assert_closed(connections)
                self.assert_no_consumer(name)
                page = self.feed.read_page(name)
                self.assertEqual(["first"], [event["event_id"] for event in page["events"]])
                self.assertTrue(self.feed.acknowledge(name, page["ack"])["delivery_complete"])

    def test_same_consumer_threads_and_instances_share_one_frozen_window(self) -> None:
        self.seed("first", "second")
        stores = (self.feed, ChangeFeedStore(self.path))
        self.addCleanup(stores[1].close)
        barrier = threading.Barrier(6)

        def read(index):
            barrier.wait(timeout=15)
            return stores[index % 2].read_page("shared-owner", limit=1)

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(read, index) for index in range(6)]
            pages = [future.result(timeout=30) for future in futures]
        for page in pages:
            self.assertEqual(pages[0], page)
        self.seed("later")
        self.assertEqual(pages[0], stores[1].read_page("shared-owner", limit=1))
        stores[0].acknowledge("shared-owner", pages[0]["ack"])
        second = stores[1].read_page("shared-owner", cursor=pages[0]["next_cursor"])
        self.assertEqual(["second"], [event["event_id"] for event in second["events"]])
        stores[1].acknowledge("shared-owner", second["ack"])
        next_page = stores[0].read_page("shared-owner")
        self.assertEqual(["later"], [event["event_id"] for event in next_page["events"]])

    def test_other_platforms_keep_original_short_connection_lifecycle(self) -> None:
        self.seed("first")
        for platform in ("win32", "darwin", "freebsd"):
            with (
                self.subTest(platform=platform),
                patch.object(feed_module.sys, "platform", platform),
            ):
                feed = ChangeFeedStore(self.path)
                try:
                    self.assertIsNone(feed._wal_anchor)
                    first = feed.read_page(platform)
                    self.assertEqual(["first"], [event["event_id"] for event in first["events"]])
                    self.assertEqual(first, feed.read_page(platform, cursor=first["replay_cursor"]))
                    self.assertTrue(feed.acknowledge(platform, first["ack"])["delivery_complete"])
                    with closing(feed._connect()) as connection:
                        self.assertEqual(2, connection.execute("PRAGMA synchronous").fetchone()[0])
                finally:
                    feed.close()

    def test_configuration_error_propagates_and_closes_new_connection(self) -> None:
        connections, observing = self.observe(fail_setup=True)
        with observing, self.assertRaisesRegex(sqlite3.OperationalError, "synthetic configuration"):
            self.feed._connect()
        self.assert_closed(connections)


@unittest.skipUnless(sys.platform == "linux", "Idle WAL ownership is Linux only")
class ChangeFeedAnchorOwnershipTests(_FeedFixture):
    def test_keeper_is_idle_and_does_not_block_checkpoint_or_writer(self) -> None:
        self.seed("first")
        connections, observing = self.observe()
        with observing:
            feed = ChangeFeedStore(self.path)
        self.addCleanup(feed.close)
        keepers = [connection for connection in connections if not connection.closed]
        self.assertEqual(1, len(keepers))
        keeper = keepers[0]
        self.assertFalse(keeper.in_transaction)
        self.assertEqual(2, keeper.execute("PRAGMA synchronous").fetchone()[0])
        self.assertEqual(1, keeper.execute("PRAGMA foreign_keys").fetchone()[0])
        self.assertEqual(1000, keeper.execute("PRAGMA wal_autocheckpoint").fetchone()[0])
        with patch.object(
            feed, "_connect", side_effect=AssertionError("Unexpected page connection")
        ):
            first = feed.read_page("same-connection")
            self.assertEqual(
                first, feed.read_page("same-connection", cursor=first["replay_cursor"])
            )
        self.assertIs(keeper, feed._wal_anchor._finalizer.peek()[2][0])
        self.assertFalse(keeper.in_transaction)
        with feed._transaction(durable=False) as fresh:
            self.assertIsNot(keeper, fresh)
            self.assertEqual(1, fresh.execute("PRAGMA synchronous").fetchone()[0])
        self.assertEqual(2, keeper.execute("PRAGMA synchronous").fetchone()[0])
        with closing(REAL_CONNECT(self.path, isolation_level=None)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO metadata VALUES ('test_idle_writer', '1')")
            writer.commit()
            self.assertEqual(
                (0, 0, 0), tuple(writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
            )
        self.assertFalse(keeper.in_transaction)
        feed.close()
        self.assert_closed(keepers)

    def test_worker_close_is_idempotent_and_same_owner_lazily_reopens(self) -> None:
        self.seed("first")
        first = self.feed.read_page("owner")
        finalizer = self.feed._wal_anchor._finalizer
        started, finished = threading.Event(), threading.Event()

        def close():
            started.set()
            self.feed.close()
            finished.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.feed._transaction(immediate=True, reuse_page_connection=True) as connection:
                connection.execute("INSERT INTO metadata VALUES ('held-page', 'committed')")
                future = pool.submit(close)
                self.assertTrue(started.wait(5))
                self.assertFalse(
                    finished.wait(0.1), "Close must wait for the active page transaction"
                )
                self.assertTrue(connection.in_transaction)
            future.result(timeout=15)
        self.assertFalse(finalizer.alive)
        self.feed.close()
        self.assertEqual(first, self.feed.read_page("owner", cursor=first["replay_cursor"]))
        self.assertTrue(self.feed._wal_anchor._finalizer.alive)
        with self.feed._transaction() as connection:
            self.assertEqual(
                "committed",
                connection.execute("SELECT value FROM metadata WHERE key='held-page'").fetchone()[
                    0
                ],
            )

    def test_nested_page_and_active_close_rejection_preserve_outer_transaction(self) -> None:
        with self.feed._transaction(immediate=True, reuse_page_connection=True) as connection:
            connection.execute("INSERT INTO metadata VALUES ('outer-page', 'committed')")
            with self.assertRaisesRegex(RuntimeError, "active"):
                self.feed.close()
            with self.assertRaises(RuntimeError):
                self.feed.read_page("nested-owner")
            self.assertTrue(connection.in_transaction)
        self.assert_no_consumer("nested-owner")
        with self.feed._transaction() as connection:
            self.assertEqual(
                "committed",
                connection.execute("SELECT value FROM metadata WHERE key='outer-page'").fetchone()[
                    0
                ],
            )

    def test_fresh_wal_reader_finishes_while_page_writer_is_active(self) -> None:
        with self.feed._transaction(immediate=True) as connection:
            connection.execute("INSERT INTO metadata VALUES ('page-visibility', 'before')")

        def read():
            with self.feed._transaction() as connection:
                return connection.execute(
                    "SELECT value FROM metadata WHERE key='page-visibility'"
                ).fetchone()[0]

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.feed._transaction(immediate=True, reuse_page_connection=True) as connection:
                connection.execute("UPDATE metadata SET value='after' WHERE key='page-visibility'")
                self.assertEqual("before", pool.submit(read).result(timeout=5))
            self.assertEqual("after", read())

    def test_closing_one_instance_preserves_other_instance_delivery(self) -> None:
        self.seed("first")
        other = ChangeFeedStore(self.path)
        self.addCleanup(other.close)
        first = other.read_page("owner")
        self.feed.close()
        self.assertEqual(first, other.read_page("owner", cursor=first["replay_cursor"]))
        self.assertTrue(other.acknowledge("owner", first["ack"])["delivery_complete"])

    def test_failed_close_retains_ownership_and_can_retry(self) -> None:
        connections, observing = self.observe()
        with observing:
            feed = ChangeFeedStore(self.path)
        self.addCleanup(feed.close)
        keeper = next(connection for connection in connections if not connection.closed)
        keeper.fail_close = True
        try:
            with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic close"):
                feed.close()
            self.assertTrue(feed._wal_anchor._finalizer.alive)
        finally:
            keeper.fail_close = False
        feed.close()
        self.assertFalse(feed._wal_anchor._finalizer.alive)
        self.assert_closed([keeper])

    def test_gc_finalizer_releases_handles_without_descriptor_growth(self) -> None:
        gc.collect()
        before = len(list(Path("/proc/self/fd").iterdir()))
        for index in range(8):
            feed = ChangeFeedStore(self.directory / f"gc-{index}.sqlite3")
            reference = weakref.ref(feed)
            finalizer = feed._wal_anchor._finalizer
            del feed
            gc.collect()
            self.assertIsNone(reference())
            self.assertFalse(finalizer.alive)
        self.assertLessEqual(len(list(Path("/proc/self/fd").iterdir())), before)

    def test_replaced_or_missing_database_requires_fresh_owner(self) -> None:
        self.seed("first")
        first = self.feed.read_page("owner")
        self.feed.close()
        replacement = self.directory / "replacement.sqlite3"
        shutil.copyfile(self.path, replacement)
        os.replace(replacement, self.path)
        with self.assertRaisesRegex(RuntimeError, "replaced"):
            self.feed.read_page("owner")
        fresh = ChangeFeedStore(self.path)
        self.addCleanup(fresh.close)
        self.assertEqual(first, fresh.read_page("owner", cursor=first["replay_cursor"]))
        fresh.close()
        self.path.unlink()
        with self.assertRaisesRegex(RuntimeError, "replaced"):
            fresh.read_page("owner")
        self.assertFalse(self.path.exists())

    def test_anchor_setup_failure_closes_connection_and_allows_retry(self) -> None:
        anchor = anchor_module.IdleWalAnchor(self.path)
        self.addCleanup(anchor.close)
        connections, observing = self.observe(fail_setup=True)
        with observing, self.assertRaisesRegex(sqlite3.OperationalError, "synthetic configuration"):
            anchor.ensure_open()
        self.assert_closed(connections)
        anchor.ensure_open()
        self.assertTrue(anchor._finalizer.alive)

    def test_fork_rejects_inherited_store_and_supports_fresh_child_owner(self) -> None:
        self.seed("first")
        script = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from minimal_kanban.storage.change_feed_store import ChangeFeedStore
store = ChangeFeedStore(Path(sys.argv[2]))
first = store.read_page('fork-owner')
reader, writer = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(reader)
    try:
        try:
            store.readiness('inherited')
        except RuntimeError as error:
            assert 'inherited' in str(error)
        else:
            raise AssertionError('Inherited store unexpectedly accepted')
        fresh = ChangeFeedStore(Path(sys.argv[2]))
        assert fresh.read_page('fork-owner', cursor=first['replay_cursor']) == first
        assert fresh.acknowledge('fork-owner', first['ack'])['delivery_complete']
        fresh.close()
        os.write(writer, b'{"fresh_child":true,"inherited_rejected":true}')
        os.close(writer)
        os._exit(0)
    except BaseException:
        os._exit(1)
os.close(writer)
result = json.loads(os.read(reader, 4096))
os.close(reader)
assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0
assert not store._wal_anchor._finalizer.alive
assert store.bootstrap('fork-owner')['acked_sequence'] == 1
assert store._wal_anchor._finalizer.alive
store.close()
print(json.dumps(result))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(ROOT / "src"), str(self.path)],
            cwd=self.directory,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            {"fresh_child": True, "inherited_rejected": True}, json.loads(result.stdout)
        )
