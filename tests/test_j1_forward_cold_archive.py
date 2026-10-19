from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_cold_archive as archive
from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_io as io
from scripts import j1_forward_launch_permission as launch


class ColdArchiveLogicalPaths(unittest.TestCase):
    def limits(self):
        patches = [
            patch.object(archive, "MAX_FILE_BYTES", 16),
            patch.object(archive, "MAX_BYTES", 32),
            patch.object(archive, "V4_MAX_BYTES", 64),
            patch.object(cold, "MAX_SOURCE_BYTES", 16),
            patch.object(cold, "MAX_RUNTIME_BYTES", 48),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_v4_large_runtime_is_typed_kernel_v2_only_and_never_source(self):
        self.limits()
        raw = b"\x7fELF" + b"x" * 20
        reference = {"path": "/saved/runtime/python", "sha256": io.sha(raw)}
        calls = []

        class Reader:
            def read_reference(self, ref):
                raise io.Refusal("ordinary_reader_budget")

            def read_opaque_reference(self, ref):
                calls.append(dict(ref))
                return raw

        reader = Reader()
        for codec, kernel in ((3, False), (3, True), (4, False)):
            snapshot = archive._ReadSnapshot(reader.read_reference, codec=codec)
            snapshot.kernel_v2 = kernel
            with self.assertRaisesRegex(io.Refusal, "ordinary_reader_budget"):
                snapshot.read_opaque_reference(reference)
        snapshot = archive._ReadSnapshot(reader.read_reference, codec=4)
        snapshot.kernel_v2 = True
        self.assertEqual(snapshot.read_opaque_reference(reference), raw)
        self.assertEqual(
            snapshot.runtime_blob_paths,
            {reference["path"]: {"sha256": reference["sha256"], "bytes": len(raw)}},
        )
        with self.assertRaisesRegex(io.Refusal, "large_runtime_untyped"):
            snapshot.read(reference)
        typed = cold.Reader(snapshot.read)
        self.assertEqual(typed.raw(reference, opaque=True), raw)
        with self.assertRaisesRegex(io.Refusal, "source_limit"):
            typed.raw(reference)
        snapshot.recheck()
        self.assertEqual(len(calls), 2)  # actual initial read + full final read

    def test_v4_opaque_does_not_accept_large_source_fake_binary_or_total_overflow(self):
        self.limits()
        for name, raw in (("source.py", b"\x7fELF" + b"x" * 20), ("runtime", b"x" * 24)):
            with self.subTest(name=name):
                reference = {"path": "/saved/" + name, "sha256": io.sha(raw)}
                snapshot = archive._ReadSnapshot(lambda ref: raw, codec=4)
                snapshot.kernel_v2 = True
                with self.assertRaisesRegex(io.Refusal, "runtime_blob_kind"):
                    snapshot.read_opaque_reference(reference)
        raw = b"\x7fELF" + b"x" * 28
        snapshot = archive._ReadSnapshot(lambda ref: raw, codec=4)
        snapshot.kernel_v2 = True
        for name in ("a", "b"):
            snapshot.read_opaque_reference({"path": "/saved/" + name, "sha256": io.sha(raw)})
        with self.assertRaisesRegex(io.Refusal, "budget"):
            snapshot.read_opaque_reference({"path": "/saved/c", "sha256": io.sha(raw)})

    def test_pure_v4_restore_rechecks_large_leaf_and_rejects_hash_alias(self):
        self.limits()
        raw = b"\x7fELF" + b"x" * 20
        reference = {"path": "/nonexistent/runtime", "sha256": io.sha(raw)}
        saved = {reference["path"]: raw}
        snapshot = archive._ReadSnapshot(lambda ref: saved[ref["path"]], codec=4)
        snapshot.kernel_v2 = True
        snapshot.read_opaque_reference(reference)
        with self.assertRaisesRegex(io.Refusal, "alias"):
            snapshot.read_opaque_reference({**reference, "sha256": "0" * 64})
        graph = archive.CapturedGraph(snapshot.files, snapshot.runtime_blob_paths)
        self.assertEqual(dict(graph), saved)
        self.assertEqual(set(graph.runtime_blob_paths), {reference["path"]})
        saved[reference["path"]] = raw + b"changed"
        with self.assertRaisesRegex(io.Refusal, "source_changed"):
            snapshot.recheck()

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
