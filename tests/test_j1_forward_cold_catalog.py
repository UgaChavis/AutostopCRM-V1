"""Synthetic catalog bytes only; no installed databases, services or APIs."""

from __future__ import annotations

import copy
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts import j1_forward_cold_catalog as catalog
from scripts import j1_forward_io as io


@unittest.skipUnless(os.name == "posix", "Requires private native Linux fixture")
class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cold-catalog-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binding = {"artificial": "catalog fixture"}
        self.provider = SimpleNamespace(scope="native_fixture")
        self.source_files = {}
        for kind, tables in catalog.DECODERS.items():
            path = self.root / (kind + ".sqlite3")
            with sqlite3.connect(path) as connection:
                for table, columns in tables:
                    connection.execute(
                        "CREATE TABLE "
                        + table
                        + " ("
                        + ",".join(name + " TEXT" for name in columns)
                        + ")"
                    )
            path.chmod(0o600)
            self.source_files[kind] = path
        self.source_files["idempotency_json"] = self.root / "idempotency.json"
        self.source_files["idempotency_json"].write_bytes(b"{}")
        self.source_files["idempotency_json"].chmod(0o600)
        ref = {"path": str(self.root / "private-binding.json"), "sha256": "1" * 64}
        self.policy = {
            "schema": "autostop.j1.cold-legacy-catalog-policy.v1",
            "scope": "native_fixture",
            "binding": self.binding,
            "predecessor_ref": ref,
            "profiles": {
                unit: [
                    {"kind": kind, "path": str(self.source_files[kind]), "path_binding_ref": ref}
                    for kind in sorted(kinds)
                ]
                for unit, kinds in catalog.REQUIRED_KINDS.items()
            },
        }

    def measure(self, **kwargs):
        return catalog.measure(
            self.policy,
            self.binding,
            provider=self.provider,
            verify_path_binding=lambda _unit, source, _policy: self.assertIn(
                Path(source["path"]), self.source_files.values()
            ),
            read_file=lambda path, _limit: Path(path).read_bytes(),
            **kwargs,
        )

    def insert(self, kind, table, **values):
        with sqlite3.connect(self.source_files[kind]) as connection:
            connection.execute(
                "INSERT INTO "
                + table
                + " ("
                + ",".join(values)
                + ") VALUES ("
                + ",".join("?" for _ in values)
                + ")",
                list(values.values()),
            )

    def test_all_six_profiles_and_all_rows_without_active_limit(self):
        with sqlite3.connect(self.source_files["manager_sqlite"]) as connection:
            connection.executemany(
                "INSERT INTO manager_runs VALUES (?,?,?)",
                [(f"request:{i}", "", "{}") for i in range(151)],
            )
        measured = self.measure()
        self.assertEqual(151, len(measured["rows"]))
        self.assertEqual({"unknown"}, {row["outcome"] for row in measured["rows"]})
        self.assertEqual({"forbidden"}, {row["retry"] for row in measured["rows"]})
        self.assertEqual("unenumerated_retired_without_replay", measured["ram_disposition"])

    def test_complete_source_row_includes_additional_json_identity(self):
        with sqlite3.connect(self.source_files["manager_sqlite"]) as connection:
            connection.execute("ALTER TABLE manager_runs ADD COLUMN result_json TEXT")
            connection.execute(
                "INSERT INTO manager_runs VALUES (?,?,?,?)",
                ("original", "", "{}", '{"operation_id":"nested-result-op"}'),
            )
        rows = self.measure()["rows"]
        self.assertEqual({"original", "nested-result-op"}, {row["op_id"] for row in rows})
        self.assertEqual({"unknown"}, {row["outcome"] for row in rows})

    def test_default_reader_preserves_exact_private_bytes_and_rejects_symlink(self):
        path = self.source_files["idempotency_json"]
        self.assertEqual(b"{}", catalog.read_catalog_bytes(path, catalog.MAX_BYTES))
        link = self.root / "link.json"
        link.symlink_to(path)
        with self.assertRaises(OSError):
            catalog.read_catalog_bytes(link, catalog.MAX_BYTES)

    def test_raw_ids_and_aggregate_loci_are_not_payload_hashes(self):
        raw_id = "arbitrary:старый ID/with spaces"
        self.insert("manager_sqlite", "manager_runs", request_id=raw_id, checkpoint_json="{}")
        first = self.measure()["rows"]
        self.insert(
            "scheduler_sqlite",
            "manager_automation_commands",
            command_id=raw_id,
            request_hash="a" * 64,
        )
        second = self.measure()["rows"]
        self.assertEqual([raw_id], [row["op_id"] for row in second])
        self.assertNotEqual(first[0]["fingerprint"], second[0]["fingerprint"])
        self.assertEqual("observed_record_sha256", second[0]["fingerprint"]["kind"])
        self.assertNotIn("payload_sha256", second[0])

    def test_attempted_and_verified_do_not_invent_applied(self):
        self.insert(
            "telegram_sqlite", "effects", effect_key="effect:string", attempted="1", verified="1"
        )
        self.assertEqual("unknown", self.measure()["rows"][0]["outcome"])

    def test_nested_record_identity_is_retained(self):
        self.insert(
            "manager_sqlite",
            "manager_runs",
            request_id="outer",
            checkpoint_json='{"steps":[{"op_id":"inner"}]}',
        )
        self.assertEqual({"outer", "inner"}, {row["op_id"] for row in self.measure()["rows"]})

    def test_omit_role_or_catalog_kind_refuses(self):
        old = copy.deepcopy(self.policy)
        del self.policy["profiles"]["autostop-j1.service"]
        with self.assertRaises(io.Refusal):
            self.measure()
        self.policy = old
        self.policy["profiles"]["autostop-telegram.service"] = self.policy["profiles"][
            "autostop-telegram.service"
        ][:1]
        with self.assertRaises(io.Refusal):
            self.measure()

    def test_wal_and_broken_symlink_sidecar_refuse(self):
        path = self.source_files["scheduler_sqlite"]
        sidecar = Path(str(path) + "-wal")
        sidecar.write_bytes(b"pending")
        with self.assertRaisesRegex(io.Refusal, "quiet_checkpoint"):
            self.measure()
        sidecar.unlink()
        if os.name == "posix":
            sidecar.symlink_to(self.root / "missing")
            with self.assertRaisesRegex(io.Refusal, "quiet_checkpoint"):
                self.measure()

    def test_missing_source_is_explicit_without_no_effect_claim(self):
        self.source_files["j1_sqlite"].unlink()
        measured = self.measure()
        row = next(row for row in measured["sources"] if row["kind"] == "j1_sqlite")
        self.assertIs(False, row["present"])
        self.assertEqual([], measured["rows"])
        self.assertNotIn("never_dispatched", io.canonical(measured).decode())

    def test_arbitrary_empty_path_cannot_replace_bound_source(self):
        self.policy["profiles"]["autostop-j1.service"][0]["path"] = str(
            self.root / "arbitrary.sqlite3"
        )
        with self.assertRaises(AssertionError):
            self.measure()

    def test_source_changed_during_read_refuses(self):
        path = self.source_files["idempotency_json"]

        def changed_read(target, _limit):
            raw = Path(target).read_bytes()
            if Path(target) == path:
                path.write_bytes(b'{"late-request":{}}')
            return raw

        with self.assertRaisesRegex(io.Refusal, "source_changed"):
            catalog.measure(
                self.policy,
                self.binding,
                provider=self.provider,
                verify_path_binding=lambda *_args: None,
                read_file=changed_read,
            )

    def test_duplicate_json_and_unbounded_operation_id_refuse(self):
        path = self.source_files["idempotency_json"]
        for raw in (b'{"same":{},"same":{}}', io.canonical({"x" * 201: {}})):
            path.write_bytes(raw)
            with self.assertRaises(io.Refusal):
                self.measure()

    def test_blob_data_never_leaks_to_measurement(self):
        self.insert(
            "scheduler_sqlite",
            "manager_automation_outbox",
            outbox_id="queued:1",
            payload_json='{"private_text":"ARTIFICIAL_SECRET"}',
        )
        self.assertNotIn(b"ARTIFICIAL_SECRET", io.canonical(self.measure()))
