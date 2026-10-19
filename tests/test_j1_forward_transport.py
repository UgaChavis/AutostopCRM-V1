"""Synthetic transport adversaries; no real source/cache/server access."""

import gzip
import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_transport as t


class ForwardTransportTests(unittest.TestCase):
    def setUp(self):
        self.b = t.core()
        temporary = tempfile.TemporaryDirectory(prefix="j1-transport-synthetic-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.staged = self.root / "staged"
        self.cache = self.root / "cache"
        self.asset = self.source / "asset.txt"
        self.asset.write_bytes(b"synthetic stable asset" * 100)
        for mocked in (
            patch.object(self.b, "ROOTS", (("crm", self.source.as_posix()),)),
            patch.object(self.b, "native_root", return_value=True),
            patch.object(
                self.b, "protected_parent", side_effect=lambda p: self.b.physical(p, directory=True)
            ),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.context = {
            "attempt_id": "00000000-0000-4000-8000-000000000001",
            "predecessor_sha256": "1" * 64,
            "candidate_crm_sha": "2" * 40,
            "candidate_manager_sha": "3" * 40,
            "scope_profile_sha256": self.b.profile_sha256(),
        }

    def prestage(self):
        staged = t.stage_assets(self.staged)
        packed = t.bundle_assets(
            self.staged, staged["manifest"]["sha256"], self.root / "assets.tgz"
        )
        checked = t.import_assets(
            self.root / "assets.tgz",
            packed["archive"]["sha256"],
            staged["manifest"]["sha256"],
            self.cache,
        )
        return checked

    def capture(self):
        return self.b.capture(
            self.root / "backup",
            self.context,
            lambda: {
                "attempt_id": self.context["attempt_id"],
                "path": str(self.root / "synthetic-closure"),
                "sha256": "f" * 64,
            },
        )

    def malformed(self, raw):
        path = self.root / "malformed.tgz"
        path.write_bytes(gzip.compress(raw, mtime=0))
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def header(self, name="manifest.json", content=b"{}", kind=tarfile.REGTYPE, size=None):
        info = tarfile.TarInfo(name)
        info.size, info.mode, info.type = len(content) if size is None else size, 0o600, kind
        return info.tobuf(format=tarfile.USTAR_FORMAT)

    def reject_tar(self, raw):
        path, sha = self.malformed(raw)
        with self.assertRaises((t.TransportRefusal, self.b.BackupRefusal)):
            t.extract(path, sha, self.root / "untrusted", time.monotonic() + 30)

    def test_full_roundtrip_cache_delta_then_independent_restore(self):
        cache = self.prestage()
        final = self.capture()
        delta = t.bundle_delta(
            self.root / "backup",
            final["manifest_sha256"],
            cache["inventory"],
            self.root / "delta.tgz",
        )
        self.assertEqual(delta["payloads_from_cache_hint"], 1)
        assembled = t.assemble_delta(
            self.root / "delta.tgz",
            delta["archive"]["sha256"],
            final["manifest_sha256"],
            self.cache,
            self.root / "assembled",
        )
        self.assertEqual(assembled["payloads_from_cache"], 1)
        verified = self.b.verify_and_restore(
            self.root / "assembled", final["manifest_sha256"], self.root / "restore", self.context
        )
        self.assertTrue(verified["provided_artifacts_restored_exactly"])
        self.assertFalse(assembled["backup_acceptance_granted"])
        self.assertFalse(assembled["all_writers_quiescent_proven"])
        self.assertEqual((self.root / "assembled/000000.bin").stat().st_nlink, 1)

    def test_changed_added_deleted_files_follow_final_manifest(self):
        removed = self.source / "removed.txt"
        removed.write_bytes(b"synthetic deleted")
        cache = self.prestage()
        removed.unlink()
        self.asset.write_bytes(b"new committed bytes")
        (self.source / "added.json").write_text('{"synthetic":true}')
        final = self.capture()
        delta = t.bundle_delta(
            self.root / "backup",
            final["manifest_sha256"],
            cache["inventory"],
            self.root / "delta.tgz",
        )
        self.assertEqual(delta["payloads_from_cache_hint"], 0)
        result = t.assemble_delta(
            self.root / "delta.tgz",
            delta["archive"]["sha256"],
            final["manifest_sha256"],
            self.cache,
            self.root / "assembled",
        )
        self.assertEqual(result["files"], 2)
        self.b.verify_and_restore(
            self.root / "assembled", final["manifest_sha256"], self.root / "restore", self.context
        )

    @unittest.skipIf(sqlite3.sqlite_version_info < (3, 51, 3), "requires supported SQLite")
    def test_wal_commit_is_not_a_prestage_cache_hit(self):
        db = self.source / "db.sqlite"
        with closing(sqlite3.connect(db)) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE values_test(id INTEGER)")
            con.commit()
            cache = self.prestage()
            manifest = json.loads((self.staged / "manifest.json").read_text())
            self.assertEqual(len(manifest["sources"]), 1)
            con.execute("INSERT INTO values_test VALUES(1)")
            con.commit()
            final = self.capture()
            delta = t.bundle_delta(
                self.root / "backup",
                final["manifest_sha256"],
                cache["inventory"],
                self.root / "delta.tgz",
            )
            self.assertEqual(delta["payloads_from_cache_hint"], 1)
            assembled = t.assemble_delta(
                self.root / "delta.tgz",
                delta["archive"]["sha256"],
                final["manifest_sha256"],
                self.cache,
                self.root / "assembled",
            )
            self.assertEqual(assembled["files"], 2)
            self.b.verify_and_restore(
                self.root / "assembled",
                final["manifest_sha256"],
                self.root / "restore",
                self.context,
            )

    def test_changed_during_stage_is_explicitly_skipped(self):
        original = self.b.copy_stable_file

        def changing(source, target, *args):
            value = original(source, target, *args)
            source.write_bytes(b"synthetic concurrent modification")
            return value

        with patch.object(self.b, "copy_stable_file", side_effect=changing):
            result = t.stage_assets(self.staged)
        self.assertEqual(result["skipped_files"], 1)
        self.assertEqual(result["files"], 0)
        self.assertEqual({p.name for p in self.staged.iterdir()}, {"manifest.json"})

    def test_corrupt_cached_blob_cannot_supply_final_bytes(self):
        cache = self.prestage()
        final = self.capture()
        delta = t.bundle_delta(
            self.root / "backup",
            final["manifest_sha256"],
            cache["inventory"],
            self.root / "delta.tgz",
        )
        next(self.cache.glob("*.blob")).write_bytes(b"corrupt")
        with self.assertRaisesRegex(t.TransportRefusal, "required_cache_blob_invalid"):
            t.assemble_delta(
                self.root / "delta.tgz",
                delta["archive"]["sha256"],
                final["manifest_sha256"],
                self.cache,
                self.root / "assembled",
            )

    def test_forged_cache_hint_fails_when_blob_absent(self):
        cache = self.prestage()
        final = self.capture()
        delta = t.bundle_delta(
            self.root / "backup",
            final["manifest_sha256"],
            cache["inventory"],
            self.root / "delta.tgz",
        )
        next(self.cache.glob("*.blob")).unlink()
        with self.assertRaises(FileNotFoundError):
            t.assemble_delta(
                self.root / "delta.tgz",
                delta["archive"]["sha256"],
                final["manifest_sha256"],
                self.cache,
                self.root / "assembled",
            )

    def test_wrong_external_manifest_pin_refused(self):
        stage = t.stage_assets(self.staged)
        archive = t.bundle_assets(
            self.staged, stage["manifest"]["sha256"], self.root / "assets.tgz"
        )
        with self.assertRaisesRegex(t.TransportRefusal, "manifest_hash_mismatch"):
            t.import_assets(
                self.root / "assets.tgz", archive["archive"]["sha256"], "0" * 64, self.cache
            )
        self.assertFalse((self.cache / "inventory.json").exists())

    def test_backup_code_pin_precedes_execution(self):
        with patch.object(t, "_CORE", None), patch.object(t, "BACKUP_SHA256", "0" * 64):
            with self.assertRaisesRegex(t.TransportRefusal, "backup_code_hash_mismatch"):
                t.core()

    def test_traversal_and_windows_aliases_rejected(self):
        for name in ("../escape", "/absolute", "C:evil", "manifest.json.", "./manifest.json"):
            with self.subTest(name=name):
                path, sha = self.malformed(self.header(name, b"") + b"\0" * 1024)
                with self.assertRaises(t.TransportRefusal):
                    t.extract(
                        path,
                        sha,
                        self.root / ("reject-" + hashlib.sha256(name.encode()).hexdigest()[:8]),
                        time.monotonic() + 30,
                    )

    def test_duplicate_members_rejected(self):
        block = self.header(content=b"{}").ljust(512, b"\0") + b"{}" + b"\0" * 510
        self.reject_tar(block + block + b"\0" * 1024)

    def test_links_devices_pax_gnu_sparse_rejected_before_body(self):
        for kind in (
            tarfile.SYMTYPE,
            tarfile.LNKTYPE,
            tarfile.CHRTYPE,
            tarfile.FIFOTYPE,
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_SPARSE,
        ):
            with self.subTest(kind=kind):
                path, sha = self.malformed(self.header(kind=kind, size=2**30) + b"\0" * 1024)
                with self.assertRaisesRegex(t.TransportRefusal, "tar_extension_or_type_refused"):
                    t.extract(path, sha, self.root / ("kind-" + kind.hex()), time.monotonic() + 30)

    def test_checksum_error_rejected(self):
        header = bytearray(self.header(content=b""))
        header[0] ^= 1
        self.reject_tar(bytes(header) + b"\0" * 1024)

    def test_oversized_declared_body_rejected_before_read(self):
        self.reject_tar(self.header(size=t.MAX_FILE + 1) + b"\0" * 1024)

    def test_decompression_envelope_limit(self):
        with patch.object(t, "MAX_ENVELOPE", 1024):
            self.reject_tar(self.header(content=b"a" * 1024) + b"a" * 1024 + b"\0" * 1024)

    def test_trailing_tar_padding_bomb_rejected(self):
        self.reject_tar(b"\0" * 1024 + b"\0" * 2**20)

    def test_second_gzip_member_and_compressed_tail_rejected(self):
        for extra in (gzip.compress(b"", mtime=0), b"unexpected"):
            with self.subTest(extra=extra):
                path = self.root / "tail.tgz"
                path.write_bytes(gzip.compress(b"\0" * 1024, mtime=0) + extra)
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
                with self.assertRaisesRegex(t.TransportRefusal, "gzip_extra_member_or_tail"):
                    t.extract(
                        path,
                        sha,
                        self.root / ("tail-" + hashlib.sha256(extra).hexdigest()[:8]),
                        time.monotonic() + 30,
                    )

    def test_truncated_or_corrupt_gzip_trailer_rejected(self):
        good = gzip.compress(b"\0" * 1024, mtime=0)
        for index, data in enumerate((good[:-4], good[:-8] + bytes([good[-8] ^ 1]) + good[-7:])):
            path = self.root / f"bad-{index}.tgz"
            path.write_bytes(data)
            with self.assertRaises(t.TransportRefusal):
                t.extract(
                    path,
                    hashlib.sha256(data).hexdigest(),
                    self.root / f"bad-{index}",
                    time.monotonic() + 30,
                )

    def test_member_count_limit(self):
        with patch.object(t, "MAX_MEMBERS", 0):
            self.reject_tar(self.header(content=b"") + b"\0" * 1024)

    def test_existing_output_is_never_overwritten(self):
        self.staged.mkdir()
        keep = self.staged / "keep"
        keep.write_bytes(b"synthetic")
        with self.assertRaisesRegex(t.TransportRefusal, "new_private_directory_required"):
            t.stage_assets(self.staged)
        self.assertEqual(keep.read_bytes(), b"synthetic")

    def test_archive_inside_source_evidence_is_refused_without_mutation(self):
        staged = t.stage_assets(self.staged)
        before = {p.name for p in self.staged.iterdir()}
        with self.assertRaisesRegex(t.TransportRefusal, "archive_inside_evidence"):
            t.bundle_assets(self.staged, staged["manifest"]["sha256"], self.staged / "archive.tgz")
        self.assertEqual({p.name for p in self.staged.iterdir()}, before)
        captured = self.capture()
        before = {p.name for p in (self.root / "backup").iterdir()}
        with self.assertRaisesRegex(t.TransportRefusal, "archive_inside_evidence"):
            t.bundle_delta(
                self.root / "backup",
                captured["manifest_sha256"],
                None,
                self.root / "backup/archive.tgz",
            )
        self.assertEqual({p.name for p in (self.root / "backup").iterdir()}, before)

    def test_cache_hardlink_rejected(self):
        cache = self.prestage()
        final = self.capture()
        delta = t.bundle_delta(
            self.root / "backup",
            final["manifest_sha256"],
            cache["inventory"],
            self.root / "delta.tgz",
        )
        blob = next(self.cache.glob("*.blob"))
        os.link(blob, self.root / "cache-hardlink")
        with self.assertRaisesRegex(
            self.b.BackupRefusal, "physical_directory_or_single_link_file_required"
        ):
            t.assemble_delta(
                self.root / "delta.tgz",
                delta["archive"]["sha256"],
                final["manifest_sha256"],
                self.cache,
                self.root / "assembled",
            )

    def test_narrow_control_limits_reject_an_oversized_header_before_payload(self):
        path, digest = self.malformed(self.header("000000.bin", size=33) + b"x" * 33)
        destination = self.root / "bounded-control"
        with self.assertRaisesRegex(t.TransportRefusal, "tar_member_limit"):
            t.extract(
                path,
                digest,
                destination,
                time.monotonic() + 5,
                max_file=32,
                max_total=64,
                max_members=2,
                max_manifest=32,
            )
        self.assertFalse((destination / "000000.bin").exists())

    def test_control_limits_cannot_expand_the_backup_transport_policy(self):
        with self.assertRaisesRegex(t.TransportRefusal, "archive_limits_invalid"):
            t.extract(
                self.root / "unused",
                "f" * 64,
                self.root / "unused-output",
                time.monotonic() + 5,
                max_total=t.MAX_TOTAL + 1,
            )


if __name__ == "__main__":
    unittest.main()
