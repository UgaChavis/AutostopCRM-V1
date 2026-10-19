from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

from scripts import j1_forward_cold_archive as archive
from scripts import j1_forward_io as io
from scripts import j1_forward_launch_permission as launch


class ColdArchiveLogicalPaths(unittest.TestCase):
    def test_capture_snapshot_reads_once_and_rechecks_actual_bytes_and_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.bin"
            path.write_bytes(b"immutable private source")
            reference = {"path": str(path), "sha256": io.sha(path.read_bytes())}
            calls = []

            def reader(ref):
                calls.append(ref)
                return Path(ref["path"]).read_bytes()

            snapshot = archive._ReadSnapshot(reader, archive._version)
            original = snapshot.read(reference)
            for _ in range(20):
                self.assertIs(snapshot.read(dict(reference)), original)
            self.assertEqual(len(calls), 1)
            with self.assertRaisesRegex(io.Refusal, "alias"):
                snapshot.read({**reference, "sha256": "0" * 64})
            snapshot.recheck()
            self.assertEqual(len(calls), 2)
            before = path.stat()
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaisesRegex(io.Refusal, "version"):
                snapshot.recheck()

    def test_pure_restored_snapshot_never_needs_native_metadata(self):
        reference = {"path": "/nonexistent/native/logical/source", "sha256": io.sha(b"saved")}
        saved = {reference["path"]: b"saved"}
        snapshot = archive._ReadSnapshot(lambda ref: saved[ref["path"]])
        self.assertEqual(snapshot.read(reference), b"saved")
        snapshot.recheck()
        saved[reference["path"]] = b"changed"
        with self.assertRaisesRegex(io.Refusal, "source_changed"):
            snapshot.recheck()

    def test_initial_journal_restores_only_its_canonical_empty_events_topology(self):
        attempt = str(uuid.uuid4())
        folder = "/var/lib/autostop-crm-forward/cold/v1/epochs/epoch/journals/" + attempt
        ticket = {
            "schema": launch.SCHEMA,
            "attempt_id": attempt,
            "nonce": str(uuid.uuid4()),
            "plan_ref": {"path": "/package.json", "sha256": "1" * 64},
            "source_sha256": "2" * 64,
            "context_sha256": "3" * 64,
            "host": "fixture",
            "boot_id": str(uuid.uuid4()),
            "owner_generation": str(uuid.uuid4()),
            "unit": "autostop-crm-forward-" + attempt + ".service",
            "not_before": 1,
            "expires_at": 2,
        }
        raw = launch.canonical(ticket)
        initial = launch._initial(io.sha(raw))
        files = {
            folder + "/ticket.json": raw,
            folder + "/context.json": b"{}\n",
            folder + "/operation.lock": b"",
            folder + "/state.json": io.canonical(initial),
        }
        self.assertIn(folder + "/events", archive.restored_children(files, folder))
        changed = dict(files)
        del changed[folder + "/operation.lock"]
        self.assertNotIn(folder + "/events", archive.restored_children(changed, folder))
        elsewhere = {name.replace("/journals/", "/other/"): value for name, value in files.items()}
        other = folder.replace("/journals/", "/other/")
        self.assertNotIn(other + "/events", archive.restored_children(elsewhere, other))

    def test_production_cold_graph_is_typed_even_beneath_controller_root(self):
        root = "/var/lib/autostop-crm-forward"
        for area in ("state", "attempts", "origins", "launch-permits"):
            with self.subTest(area=area):
                self.assertTrue(archive.control_path(root + "/" + area + "/record.json", root))
        for suffix in (
            "cold/v1/epochs/epoch/epoch.json",
            "cold/v1/epochs/epoch/journals/op/state.json",
            "cold-controller-launch/op/state.json",
            "cold-requests/op/request.json",
            "state-extra/record.json",
            "",
        ):
            with self.subTest(suffix=suffix):
                self.assertFalse(archive.control_path(root + "/" + suffix, root))
        self.assertFalse(
            archive.control_path("/etc/autostop-crm-forward/cold/v1/authority.json", root)
        )


if __name__ == "__main__":
    unittest.main()
