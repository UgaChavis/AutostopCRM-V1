"""Cold v4 budgets are explicit; declarations never grant runtime authority."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_archive as archive
from scripts import j1_forward_io as io


class ColdArchiveCodecTests(unittest.TestCase):
    def manifest(self, *, path="/opt/vendor/codex", size=33 * 1024**2):
        value = {"sha256": "a" * 64, "bytes": size}
        return {
            "schema": archive.COLD_SCHEMA,
            "runtime_blobs": {path: dict(value)},
            "artifacts": [{"logical_name": "authority" + path, "payload": "000000.bin", **value}],
        }

    def test_only_explicit_v4_transport_uses_the_runtime_budget(self):
        self.assertEqual(archive.transport_total_limit(), 128 * 1024**2)
        self.assertEqual(archive.transport_total_limit(3), 128 * 1024**2)
        self.assertEqual(archive.transport_total_limit(4), 512 * 1024**2)
        for value in (True, False, 4.0, "4", 5, None):
            with self.subTest(value=value), self.assertRaisesRegex(io.Refusal, "codec"):
                archive.transport_total_limit(value)

    def test_existing_cold_archive_and_restore_schema_remain_readable(self):
        with patch.object(archive, "_cold", return_value=True):
            self.assertEqual(archive._schema({}, codec=3), archive.COLD_V3_SCHEMA)
            self.assertEqual(
                archive._schema({}, codec=3, restore=True), archive.COLD_V3_RESTORE_SCHEMA
            )
            self.assertEqual(archive._schema({}, codec=4), archive.COLD_SCHEMA)

    def test_large_runtime_leaf_requires_exact_declared_path_hash_and_size(self):
        manifest = self.manifest()
        blobs = archive._runtime_blobs(manifest)
        self.assertEqual(blobs, manifest["runtime_blobs"])
        self.assertEqual(
            archive._entry_limit(manifest["schema"], manifest["artifacts"][0], blobs),
            384 * 1024**2,
        )
        for field, changed in (("sha256", "b" * 64), ("bytes", 34 * 1024**2)):
            bad = copy.deepcopy(manifest)
            bad["artifacts"][0][field] = changed
            with self.subTest(field=field), self.assertRaisesRegex(io.Refusal, "inventory"):
                archive._runtime_blobs(bad)

    def test_unlisted_large_payload_and_unused_large_declaration_are_rejected(self):
        for change in ("remove_declaration", "remove_payload", "duplicate_payload"):
            manifest = self.manifest()
            if change == "remove_declaration":
                manifest["runtime_blobs"].clear()
            elif change == "remove_payload":
                manifest["artifacts"].clear()
            else:
                manifest["artifacts"].append(dict(manifest["artifacts"][0]))
            with self.subTest(change=change), self.assertRaises(io.Refusal):
                archive._runtime_blobs(manifest)

    def test_large_json_source_configuration_and_traversal_are_not_runtime_blobs(self):
        for path in (
            "/opt/a.json",
            "/opt/a.py",
            "/etc/a.toml",
            "/etc/a.env",
            "/opt/a/../codex",
            "relative/codex",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(io.Refusal, "runtime_blob"):
                archive._runtime_blobs(self.manifest(path=path))

    def test_runtime_extension_does_not_raise_json_or_legacy_limits(self):
        manifest = self.manifest()
        blobs = archive._runtime_blobs(manifest)
        entry = {**manifest["artifacts"][0], "logical_name": "control/attempts/a/plan.json"}
        self.assertEqual(archive._entry_limit(archive.COLD_SCHEMA, entry, blobs), 8 * 1024**2)
        self.assertEqual(archive._entry_limit(archive.COLD_V3_SCHEMA, entry, {}), 32 * 1024**2)
        self.assertEqual(archive._entry_limit(archive.MIXED_SCHEMA, entry, {}), 8 * 1024**2)
        manifest["schema"] = archive.COLD_V3_SCHEMA
        with self.assertRaisesRegex(io.Refusal, "unexpected_runtime_blobs"):
            archive._runtime_blobs(manifest)

    def test_large_leaf_boundaries_reject_boolean_small_and_excess_sizes(self):
        for size in (True, 32 * 1024**2, 384 * 1024**2 + 1, -1):
            with self.subTest(size=size), self.assertRaisesRegex(io.Refusal, "runtime_blob"):
                archive._runtime_blobs(self.manifest(size=size))

    def test_oversize_write_is_rejected_before_creating_any_directory_or_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "absent" / "payload.bin"
            with self.assertRaisesRegex(io.Refusal, "file_limit"):
                archive._write_bytes(target, b"too large", limit=1)
            self.assertFalse(target.parent.exists())

    def test_final_runtime_read_uses_the_same_typed_capsule_or_executable_capability(self):
        class Reader:
            def read(self, _ref):
                raise AssertionError("ordinary JSON reader cannot revalidate runtime leaves")

            def read_opaque_reference(self, _ref):
                return b"ELF-copy"

            def read_runtime_capsule(self, _ref):
                return b"capsule-copy"

        reader = Reader()
        self.assertEqual(archive._runtime_reader(reader.read, b"\x7fELF")(None), b"ELF-copy")
        self.assertEqual(archive._runtime_reader(reader.read, b"\x1f\x8b")(None), b"capsule-copy")


if __name__ == "__main__":
    unittest.main()
