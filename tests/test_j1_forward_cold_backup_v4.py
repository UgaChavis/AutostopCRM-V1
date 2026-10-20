"""Actual WAL snapshots in private storage; no production or application imports."""

from __future__ import annotations

import copy
import os
import sqlite3
import tempfile
import threading
import time
import types
import unittest
import uuid
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_backup as original
from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_cold_backup as cold
from scripts import j1_forward_cold_kernel as kernel
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_cold_worker as worker
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal


def binding():
    now = time.monotonic_ns()
    return {
        **{name: str(uuid.uuid4()) for name in ("attempt_id", "boot_id", "generation")},
        "consumer_sha256": "1" * 64,
        "candidate_crm_sha": "2" * 40,
        "candidate_manager_sha": "3" * 40,
        "deployment_lock_identity": [1, 2],
        "not_before_ns": now - 10**9,
        "deadline_ns": now + 90 * 10**9,
    }


class DiagnosticSelectorTests(unittest.TestCase):
    def graph(self, damage=lambda _name, _body: None):
        files, bound = {}, binding()

        def save(name, body):
            damage(name, body)
            raw = io.canonical(body)
            path = "/private/" + name + ".json"
            files[path] = raw
            return {"path": path, "sha256": io.sha(raw)}

        def document(name, fields, **values):
            return {
                **dict.fromkeys(fields),
                "schema": "autostop.j1.cold-" + name + ".v1",
                "scope": "native_fixture",
                "binding": bound,
                **values,
            }

        home_path = f"/private/packages/{bound['attempt_id']}/kernel/home"
        ref = {"path": "/private/source.py", "sha256": "4" * 64}
        actor = {
            **dict.fromkeys(native.ACTOR_FIELDS, "synthetic"),
            "pid": 100,
            "start_ticks": 101,
            "uid": 1000,
            "gid": 1000,
            "exe_ref": {"path": "/private/codex", "sha256": cold.DIAGNOSTIC_CODEX_SHA256},
        }
        home = save(
            "home",
            document(
                "kernel-home",
                kernel.HOME_FIELDS,
                home=home_path,
                codex_home=home_path + "/.codex",
            ),
        )
        environment = save(
            "env",
            document(
                "environment",
                {"unit", "values"},
                unit="private.service",
                values={"HOME": home_path, "CODEX_HOME": home_path + "/.codex"},
            ),
        )
        preparation = save(
            "prep",
            document(
                "kernel-preparation",
                kernel.PREPARATION_FIELDS,
                actor=actor,
                home_ref=home,
                source_ref=ref,
                rpc_ref=ref,
                protocol_index_ref=ref,
            ),
        )
        fields = set(
            "unit source_ref interpreter_ref runtime_index_ref startup_index_ref environment_ref unit_ref argv cwd credentials appserver_ref appserver_argv appserver_environment_ref protocol_index_ref rpc_ref".split()
        )
        policy = save(
            "policy",
            document(
                "kernel-policy",
                fields,
                schema="autostop.j1.cold-kernel-policy.v2",
                unit="private.service",
                source_ref=ref,
                rpc_ref=ref,
                protocol_index_ref=ref,
                appserver_ref=actor["exe_ref"],
                appserver_environment_ref=environment,
            ),
        )
        kernel_ref = save(
            "kernel",
            document(
                "kernel",
                {
                    "actor",
                    "unit",
                    "policy_ref",
                    "ready_ref",
                    "cache_view_ref",
                    "supervisor_actor",
                    "preparation_ref",
                    "empty_thread_ref",
                },
                schema="autostop.j1.cold-kernel.v2",
                actor=actor,
                unit="private.service",
                policy_ref=policy,
                preparation_ref=preparation,
            ),
        )
        package = document(
            "package",
            contract.PACKAGE_FIELDS,
            schema=contract.SCHEMA,
            contract=contract.CONTRACT,
            kernel_ref=kernel_ref,
        )
        return save("package", package), files

    def test_selector_reconstructs_only_exact_bound_kernel_source(self):
        reference, files = self.graph()
        value = cold.diagnostic_selector(reference, lambda ref: files[ref["path"]])
        self.assertEqual(
            value["database_path"],
            f"/private/packages/{value['binding']['attempt_id']}/kernel/home/.codex/logs_2.sqlite",
        )
        self.assertEqual(value["package_ref"], reference)
        self.assertEqual(value["actor"]["exe_ref"]["sha256"], cold.DIAGNOSTIC_CODEX_SHA256)

    def test_wrong_binary_generation_home_and_kernel_version_refuse(self):
        for target, change in (
            (
                "policy",
                lambda body: body.update(
                    appserver_ref={"path": "/private/codex", "sha256": "0" * 64}
                ),
            ),
            ("home", lambda body: body.update(codex_home="/old/.codex")),
            (
                "prep",
                lambda body: body.update(
                    binding={**body["binding"], "generation": str(uuid.uuid4())}
                ),
            ),
            ("kernel", lambda body: body.update(schema="autostop.j1.cold-kernel.v1")),
        ):
            with self.subTest(target=target):
                reference, files = self.graph(
                    lambda name, body: change(body) if name == target else None
                )
                with self.assertRaises(io.Refusal):
                    cold.diagnostic_selector(reference, lambda ref: files[ref["path"]])

    def test_same_ref_changing_between_validation_and_final_read_refuses(self):
        reference, files = self.graph()
        counts = {}

        def read(ref):
            counts[ref["path"]] = counts.get(ref["path"], 0) + 1
            return files[ref["path"]] + (b" " if counts[ref["path"]] > 1 else b"")

        with self.assertRaisesRegex(io.Refusal, "reference_changed"):
            cold.diagnostic_selector(reference, read)

    def test_v3_hash_stays_separate_and_v4_selection_requires_its_exact_hash(self):
        value = binding()
        ref = {"path": "/private/package.json", "sha256": "8" * 64}
        args = value["candidate_manager_sha"], value["generation"], value["attempt_id"]
        old = cold.engine(*args)
        before = copy.deepcopy(original.profile_document())
        new = cold.engine(*args, version=4, package_ref=ref)
        plan = {
            "candidate_manager_sha": args[0],
            "attempt_id": args[2],
            "cold_authority_package_ref": ref,
            "dispatch_epoch_ref": {
                "path": f"/var/lib/autostop-crm-forward/cold/v1/epochs/{args[1]}/epoch.json",
                "sha256": "9" * 64,
            },
        }
        self.assertEqual(old.PROFILE, cold.PROFILE)
        self.assertEqual(new.PROFILE, cold.PROFILE_V4)
        self.assertNotEqual(old.profile_sha256(), new.profile_sha256())
        self.assertIs(cold.for_plan(plan, profile_sha256=old.profile_sha256()), old)
        self.assertIs(cold.for_plan(plan, profile_sha256=new.profile_sha256()), new)
        self.assertEqual(original.profile_document(), before)
        with self.assertRaises(original.BackupRefusal):
            cold.for_plan(plan, profile_sha256="f" * 64)

    def test_restored_authority_reader_does_not_widen_interval_witness_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            raw = b'{"fixture":true}\n'
            (root / "000000.bin").write_bytes(raw)
            reference = {"path": "/etc/private/package.json", "sha256": io.sha(raw)}
            entries = {
                "authority" + reference["path"]: {
                    "sha256": io.sha(raw),
                    "bytes": len(raw),
                    "payload": "000000.bin",
                }
            }
            read = worker._restored_capture_reader(
                entries, root, {"plan": {"attempt_id": str(uuid.uuid4())}}
            )
            with self.assertRaisesRegex(io.Refusal, "worker_capture_reference_path"):
                read(reference)
            self.assertEqual(read.read_source(reference), raw)
            (root / "000000.bin").write_bytes(raw + b" ")
            with self.assertRaises(io.Refusal):
                read.read_source(reference)

    def test_restored_selector_must_match_independent_captured_source_graph(self):
        reference, files = self.graph()

        def source(ref):
            io.require(ref["path"] in files, "fixture_missing_restored_authority")
            return files[ref["path"]]

        selector = cold.diagnostic_selector(reference, source)
        bound = selector["binding"]
        profile = cold.engine(
            bound["candidate_manager_sha"],
            bound["generation"],
            bound["attempt_id"],
            version=4,
            package_ref=reference,
        ).profile_document()
        profile["roots"] = (("cold_controller_state", "/private"),)
        start = bound["not_before_ns"] + 1
        vectors = {
            selector["database_path"] + suffix: [1, index, 4096, start, start, 1000, 1000, 0o600]
            for index, suffix in enumerate(("", "-wal", "-shm"), 1)
        }
        artifacts = [
            {
                "source": path,
                "component": "cold_controller_state",
                "source_version": cold._diagnostic_mask(version),
                "kind": "sqlite" if path == selector["database_path"] else "runtime",
                "sha256": "6" * 64,
                "inspection": {
                    "schema_sha256": "7" * 64,
                    "integrity": "ok",
                    "foreign_keys": "ok",
                    "table_rows": {"logs": 1, "_sqlx_migrations": 2, "sqlite_sequence": 1},
                },
            }
            for path, version in vectors.items()
        ]
        manifest = {
            "profile": profile,
            "artifacts": artifacts,
            "diagnostic_snapshot": {
                "schema": "autostop.j1.cold-diagnostic-sqlite-snapshot.v1",
                "selector": selector,
                **{
                    key: vectors
                    for key in ("initial", "final", "before_transaction", "after_transaction")
                },
                "started_monotonic_ns": start,
                "completed_monotonic_ns": start + 1,
                "payload_sha256": "6" * 64,
                "schema_sha256": "7" * 64,
                "source_readonly": True,
                "business_quiet_claimed": False,
                "cross_database_atomic_claimed": False,
            },
        }
        response = {
            "plan": {"cold_authority_package_ref": reference, "attempt_id": bound["attempt_id"]},
            "evidence": {"package": {"binding": bound}},
            "scope_profile_sha256": io.sha(original.canonical(profile)),
        }

        def interval_only(_ref):
            self.fail("Source selector must use restored authority bytes, not interval paths")

        interval_only.read_source = source
        self.assertEqual(
            cold.validate_restored_diagnostic(manifest, response, interval_only)["selector"],
            selector,
        )
        forged = copy.deepcopy(manifest)
        forged["diagnostic_snapshot"]["selector"]["actor"]["pid"] += 1
        with self.assertRaisesRegex(original.BackupRefusal, "diagnostic_restored_source_binding"):
            cold.validate_restored_diagnostic(forged, response, interval_only)
        del files[selector["home_ref"]["path"]]
        with self.assertRaisesRegex(io.Refusal, "fixture_missing_restored_authority"):
            cold.validate_restored_diagnostic(manifest, response, interval_only)


@unittest.skipUnless(os.name == "posix", "Release checks require POSIX ownership and permissions")
@unittest.skipIf(
    sqlite3.sqlite_version_info < (3, 51, 3), "requires supported native/offhost SQLite"
)
class DiagnosticSQLiteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cold-diagnostic-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.root.chmod(0o700)
        self.binding = binding()
        self.actor = native.actor(os.getpid())
        # Replace only the native executor for this narrow SQLite unit fixture.
        # The full kernel2 rehearsal uses the actual pinned Codex executable.
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.object(cold, "DIAGNOSTIC_CODEX_SHA256", self.actor["exe_ref"]["sha256"])
        )
        self.reference = {"path": "/private/package.json", "sha256": "7" * 64}
        self.engine = cold.engine(
            self.binding["candidate_manager_sha"],
            self.binding["generation"],
            self.binding["attempt_id"],
            version=4,
            package_ref=self.reference,
        )
        self.source = self.root / "source"
        self.database = self.source / self.engine.DIAGNOSTIC_RULE["relative_path"]
        self.database.parent.mkdir(parents=True, mode=0o700)
        self.database.parent.chmod(0o700)
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA wal_autocheckpoint=0")
        self.connection.executescript("""
            CREATE TABLE _sqlx_migrations(version BIGINT PRIMARY KEY, description TEXT NOT NULL,
              installed_on TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, success BOOLEAN NOT NULL,
              checksum BLOB NOT NULL, execution_time BIGINT NOT NULL);
            CREATE TABLE logs(id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER NOT NULL,
              ts_nanos INTEGER NOT NULL,level TEXT NOT NULL,target TEXT NOT NULL,
              feedback_log_body TEXT,module_path TEXT,file TEXT,line INTEGER,thread_id TEXT,
              process_uuid TEXT,estimated_bytes INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX idx_logs_ts ON logs(ts DESC,ts_nanos DESC,id DESC);
            CREATE INDEX idx_logs_thread_id ON logs(thread_id);
            CREATE INDEX idx_logs_thread_id_ts ON logs(thread_id,ts DESC,ts_nanos DESC,id DESC);
            CREATE INDEX idx_logs_process_uuid_threadless_ts ON logs(process_uuid,ts DESC,ts_nanos DESC,id DESC) WHERE thread_id IS NULL;
        """)
        for version, description, success, checksum in cold.LOG_MIGRATIONS:
            self.connection.execute(
                "INSERT INTO _sqlx_migrations(version,description,success,checksum,execution_time) VALUES(?,?,?,?,0)",
                (version, description, success, bytes.fromhex(checksum)),
            )
        self.connection.execute(
            "INSERT INTO logs(ts,ts_nanos,level,target) VALUES(1,2,'INFO','synthetic')"
        )
        self.connection.commit()
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.database) + suffix).chmod(0o600)
        self.stack.enter_context(
            patch.object(self.engine, "ROOTS", (("cold_controller_state", str(self.source)),))
        )
        self.stack.enter_context(patch.object(self.engine, "OPTIONAL_ROOTS", ()))
        self.stack.enter_context(rehearsal.PrivateFilesystem(self.root, engines=(self.engine,)))
        self.context = {
            "attempt_id": self.binding["attempt_id"],
            "predecessor_sha256": "1" * 64,
            "candidate_crm_sha": self.binding["candidate_crm_sha"],
            "candidate_manager_sha": self.binding["candidate_manager_sha"],
            "scope_profile_sha256": self.engine.profile_sha256(),
        }
        self.selector = {
            "schema": "autostop.j1.cold-diagnostic-sqlite-selector.v1",
            "scope": "native_fixture",
            "binding": self.binding,
            "package_ref": self.reference,
            **dict.fromkeys(
                ("kernel_ref", "policy_ref", "preparation_ref", "home_ref"), self.reference
            ),
            "actor": self.actor,
            "database_path": str(self.database),
        }
        self.output = self.root / "backup"

    def fence(self):
        return {
            "attempt_id": self.binding["attempt_id"],
            "path": str(self.root / "fence.json"),
            "sha256": "5" * 64,
        }

    def capture(self, observer=None, gate=lambda: None):
        # Execute the unmodified fixed engine's capture algorithm with the v4
        # namespace; native interval admission is tested separately.
        capture = types.FunctionType(original.capture.__code__, self.engine.__dict__)
        with cold._DiagnosticSnapshot(self.engine, self.selector, gate=gate):
            return capture(self.output, self.context, observer or self.fence, timeout=10)

    def manifest(self):
        return io.document((self.output / "manifest.json").read_bytes())

    def test_actual_concurrent_wal_writer_captures_and_restores_transaction(self):
        stop, active = threading.Event(), threading.Event()
        failures = []

        def writer():
            try:
                with closing(sqlite3.connect(self.database)) as connection:
                    while not stop.is_set():
                        connection.execute(
                            "INSERT INTO logs(ts,ts_nanos,level,target) VALUES(1,2,'INFO','private-writer')"
                        )
                        connection.commit()
                        active.set()
                        stop.wait(0.002)
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=writer)
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(timeout=5)))
        self.assertTrue(active.wait(3))
        captured = self.capture()
        stop.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        manifest = self.manifest()
        diagnostic = manifest["diagnostic_snapshot"]
        self.assertNotEqual(diagnostic["initial"], diagnostic["final"])
        self.assertIs(diagnostic["business_quiet_claimed"], False)
        self.assertIs(manifest["all_writers_quiescent_proven"], False)
        checked = self.engine.verify_and_restore(
            self.output,
            captured["manifest_sha256"],
            self.root / "restored",
            self.context,
            timeout=10,
        )
        self.assertTrue(checked["provided_artifacts_restored_exactly"])
        row = next(row for row in manifest["artifacts"] if row["source"] == str(self.database))
        with closing(sqlite3.connect(self.root / "restored" / row["payload"])) as connection:
            count = connection.execute("SELECT count(*) FROM logs").fetchone()[0]
            self.assertGreater(count, 1)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertGreaterEqual(
            self.connection.execute("SELECT count(*) FROM logs").fetchone()[0], count
        )

    def test_unrelated_old_sqlite_wal_mutation_still_refuses(self):
        old = self.source / "old.sqlite"
        with closing(sqlite3.connect(old)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE old(value)")
            connection.commit()
            calls = 0

            def observe():
                nonlocal calls
                calls += 1
                if calls == 2:
                    connection.execute("INSERT INTO old VALUES(1)")
                    connection.commit()
                return self.fence()

            with self.assertRaises(self.engine.BackupRefusal):
                self.capture(observe)
        self.assertFalse((self.output / "manifest.json").exists())

    def test_wrong_path_extra_schema_kernel_replacement_and_expiry_refuse(self):
        for label in ("path", "binary", "schema", "kernel", "expiry"):
            with self.subTest(label=label), ExitStack() as stack:
                selector = copy.deepcopy(self.selector)
                if label == "path":
                    selector["database_path"] += ".other"
                if label == "binary":
                    selector["actor"]["exe_ref"]["sha256"] = "f" * 64
                if label == "kernel":
                    selector["actor"]["start_ticks"] += 1
                if label == "expiry":
                    selector["binding"]["deadline_ns"] = time.monotonic_ns()
                if label == "schema":
                    self.connection.execute("CREATE TABLE unauthorized(value)")
                    self.connection.commit()
                    stack.callback(lambda: self.connection.execute("DROP TABLE unauthorized"))
                with self.assertRaises((self.engine.BackupRefusal, io.Refusal)):
                    with cold._DiagnosticSnapshot(
                        self.engine, selector, gate=lambda: None
                    ) as snapshot:
                        snapshot.copy(
                            self.database,
                            self.root / (label + ".sqlite"),
                            time.monotonic() + 5,
                            10**6,
                        )

    def test_held_wal_inode_replacement_and_gate_loss_are_detected(self):
        with cold._DiagnosticSnapshot(self.engine, self.selector, gate=lambda: None) as snapshot:
            wal = Path(str(self.database) + "-wal")
            saved = wal.with_suffix(".held")
            wal.rename(saved)
            wal.write_bytes(saved.read_bytes())
            wal.chmod(0o600)
            with self.assertRaisesRegex(
                self.engine.BackupRefusal, "diagnostic_source_identity_changed"
            ):
                snapshot._check()
            wal.unlink()
            saved.rename(wal)
            snapshot.gate = lambda: (_ for _ in ()).throw(io.Refusal("fixture_lock_lost"))
            with self.assertRaisesRegex(io.Refusal, "fixture_lock_lost"):
                snapshot._check()

    def test_restore_refuses_forged_identity_and_wrong_migration_payload(self):
        self.capture()
        manifest = self.manifest()
        for wrong in ([], None, {"selector": {}}):
            malformed = {**manifest, "diagnostic_snapshot": wrong}
            with self.assertRaises(original.BackupRefusal):
                cold._validate_diagnostic_manifest(malformed, self.engine.profile_document())
        source = str(self.database)
        manifest["diagnostic_snapshot"]["final"][source][1] += 1
        with self.assertRaisesRegex(original.BackupRefusal, "diagnostic_source_identity"):
            cold._validate_diagnostic_manifest(manifest, self.engine.profile_document())
        manifest = self.manifest()
        row = next(row for row in manifest["artifacts"] if row["source"] == source)
        path = self.output / row["payload"]
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("UPDATE _sqlx_migrations SET description='changed'")
            connection.commit()
        row.update(
            sha256=self.engine.digest(path, time.monotonic() + 5),
            inspection=self.engine.inspect_sqlite(path, time.monotonic() + 5),
        )
        manifest["diagnostic_snapshot"].update(
            payload_sha256=row["sha256"], schema_sha256=row["inspection"]["schema_sha256"]
        )
        raw = self.engine.canonical(manifest)
        (self.output / "manifest.json").write_bytes(raw)
        with self.assertRaisesRegex(self.engine.BackupRefusal, "diagnostic_sqlite_schema"):
            self.engine.verify_and_restore(
                self.output, io.sha(raw), self.root / "restored", self.context, timeout=10
            )

    def test_v4_capture_requires_active_native_interval_not_a_caller_observer(self):
        with self.assertRaisesRegex(self.engine.BackupRefusal, "diagnostic_native_interval"):
            self.engine.capture(self.output, self.context, self.fence, timeout=10)


if __name__ == "__main__":
    unittest.main()
