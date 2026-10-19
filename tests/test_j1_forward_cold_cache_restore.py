"""Independent opaque cache restore must match both accepted checkpoint trees."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts import j1_forward_cold_worker as cold
from scripts import j1_forward_io as io


class CacheRestoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / "package"
        self.control = self.root / "control-restored"
        self.data = self.root / "restored"
        for path in (self.package, self.control, self.data):
            path.mkdir()
        self.binding = {"attempt_id": "00000000-0000-4000-8000-000000000001"}
        self.base = (
            "/var/lib/autostop-crm-forward/cold/v1/packages/"
            + self.binding["attempt_id"]
            + "/kernel/"
        )

    def prepare(self, *, empty=False, legacy=False):
        raw = b"artificial immutable cache bytes\n"
        checkpoint = {
            "schema": "autostop.j1.cold-cache-checkpoint.v1",
            "scope": "native_fixture",
            "binding": self.binding,
            "root": self.base + "cache-source",
            "files": []
            if empty
            else [
                {
                    "name": "nested/cache.bin",
                    "bytes": len(raw),
                    "sha256": io.sha(raw),
                    "mode": 0o600,
                }
            ],
        }
        entries = []

        def put(name, body):
            payload = f"{len(entries):06d}.bin"
            body_raw = io.canonical(body)
            reference = {"path": self.base + name, "sha256": io.sha(body_raw)}
            entries.append(
                {
                    "logical_name": "authority" + reference["path"],
                    "payload": payload,
                    "sha256": reference["sha256"],
                    "bytes": len(body_raw),
                }
            )
            (self.control / payload).write_bytes(body_raw)
            return reference

        checkpoint_ref = put("cache-checkpoint.json", checkpoint)
        view_ref = put(
            "cache-view.json",
            {
                "schema": "autostop.j1.cold-cache-view.v2",
                "binding": self.binding,
                "kind": "kernel_read_only_checkpoint_mount",
                "path": self.base + "cache-view",
                "checkpoint_ref": checkpoint_ref,
            },
        )
        kernel_ref = put(
            "kernel.json",
            {
                "schema": "autostop.j1.cold-kernel.v1" if legacy else "autostop.j1.cold-kernel.v2",
                "binding": self.binding,
                "cache_view_ref": view_ref,
            },
        )
        control_raw = io.canonical({"artifacts": entries})
        (self.package / "manifest.json").write_bytes(control_raw)
        response = {
            "control_archive": {"manifest_sha256": io.sha(control_raw)},
            "evidence": {
                "package": {
                    "scope": "native_fixture",
                    "binding": self.binding,
                    "kernel_ref": kernel_ref,
                }
            },
        }
        artifacts = []
        for index, name in enumerate(("cache-source", "cache-view")):
            source = self.base + name
            artifacts.append(
                {"component": "cold_controller_state", "source": source, "kind": "directory"}
            )
            if not empty:
                payload = f"{index:06d}.bin"
                (self.data / payload).write_bytes(raw)
                artifacts.append(
                    {
                        "component": "cold_controller_state",
                        "source": source + "/nested/cache.bin",
                        "kind": "file",
                        "payload": payload,
                        "bytes": len(raw),
                        "sha256": io.sha(raw),
                        "source_version": [1, 2, len(raw), 3, 4, 0, 0, 0o600],
                    }
                )
        return response, {"artifacts": artifacts}

    def check(self, response, manifest):
        return cold.restored_kernel_cache(response, manifest, self.package, self.control, self.data)

    def test_both_independent_trees_match_without_claiming_native_restoration(self):
        result = self.check(*self.prepare())
        self.assertTrue(result["cache_checkpoint_checked"])
        self.assertEqual(result["files_per_tree"], 1)
        self.assertEqual(result["trees"], [self.base + "cache-source", self.base + "cache-view"])
        self.assertFalse(result["native_source_ownership_restored"])
        self.assertFalse(result["production_restore_performed"])

    def test_missing_view_file_root_or_extra_unindexed_file_prevents_signing(self):
        response, original = self.prepare()
        for failure in ("missing_view", "missing_root", "extra", "duplicate_root"):
            manifest = copy.deepcopy(original)
            if failure == "missing_view":
                manifest["artifacts"].pop()
            elif failure == "missing_root":
                manifest["artifacts"].pop(0)
            elif failure == "extra":
                extra = copy.deepcopy(manifest["artifacts"][-1])
                extra["source"] += ".unindexed"
                manifest["artifacts"].append(extra)
            else:
                manifest["artifacts"].append(copy.deepcopy(manifest["artifacts"][0]))
            with self.subTest(failure=failure), self.assertRaises(io.Refusal):
                self.check(response, manifest)

    def test_data_manifest_hash_mode_and_actual_restored_bytes_are_checked(self):
        response, original = self.prepare()
        for field, value in (("sha256", "a" * 64), ("bytes", 0), ("source_version", [1] * 8)):
            manifest = copy.deepcopy(original)
            manifest["artifacts"][-1][field] = value
            with self.subTest(field=field), self.assertRaises(io.Refusal):
                self.check(response, manifest)
        (self.data / "000001.bin").write_bytes(b"x" * original["artifacts"][-1]["bytes"])
        with self.assertRaisesRegex(io.Refusal, "restored_bytes"):
            self.check(response, original)

    def test_empty_checkpoint_requires_both_directory_artifacts(self):
        response, manifest = self.prepare(empty=True)
        self.assertEqual(self.check(response, manifest)["files_per_tree"], 0)
        manifest["artifacts"].pop()
        with self.assertRaisesRegex(io.Refusal, "data_incomplete"):
            self.check(response, manifest)

    def test_historical_kernel_never_claims_checkpoint_restoration(self):
        result = self.check(*self.prepare(legacy=True))
        self.assertEqual(result, {"cache_checkpoint_checked": False, "reason": "legacy_kernel_v1"})


if __name__ == "__main__":
    unittest.main()
