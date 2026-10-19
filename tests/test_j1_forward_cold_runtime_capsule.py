from __future__ import annotations

import copy
import gzip
import io as streams
import os
import shutil
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_runtime_capsule as capsule
from scripts import j1_forward_io as io


class RuntimeCapsuleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.logical = lambda path: str(path)
        self.physical = Path
        if os.name == "nt" or getattr(self, "use_logical_paths", False):
            physical_root = self.root
            prefix = "/private/runtime-capsule-fixture"

            def logical(path):
                normalized = str(path).replace("\\", "/")
                if normalized == prefix or normalized.startswith(prefix + "/"):
                    return normalized
                relative = Path(path).relative_to(physical_root).as_posix()
                return prefix if relative == "." else prefix + "/" + relative

            def physical(path):
                value = os.fspath(path)
                normalized = value.replace("\\", "/")
                return (
                    physical_root.joinpath(*normalized.removeprefix(prefix + "/").split("/"))
                    if normalized.startswith(prefix + "/")
                    else physical_root
                    if normalized == prefix
                    else Path(value)
                )

            class FixturePath(type(self.root)):
                def __new__(cls, value, *segments):
                    return super().__new__(cls, physical(value), *segments)

                def __init__(self, value, *segments):
                    super().__init__(physical(value), *segments)

                def __str__(self):
                    return logical(super().__str__())

                def __fspath__(self):
                    return os.fspath(physical(super().__str__()))

            self.logical, self.physical = logical, physical
            mapped_paths = patch.object(capsule, "Path", FixturePath)
            mapped_paths.start()
            self.addCleanup(mapped_paths.stop)
            if os.name == "nt":
                # Windows fixture restore has no directory-fsync primitive;
                # native production capture is Linux-only and stays unchanged.
                directory_sync = patch.object(io, "fsync_directory")
                directory_sync.start()
                self.addCleanup(directory_sync.stop)
        self.roots = {key: self.logical(self.root / "input" / key) for key in ("python", "venv")}
        for path in self.roots.values():
            root = self.physical(path)
            (root / "bin").mkdir(parents=True)
            (root / "lib").mkdir()
            (root / "bin/python3.12").write_bytes(b"fixture runtime bytes; never executed\n")
            (root / "bin/python3.12").chmod(0o755)
            (root / "lib/package.py").write_bytes(
                b"raise AssertionError('archived code executed')\n"
            )
        self.binding = {"candidate_manager_sha": "1" * 40}
        self.artifact = self.root / "runtime.tar.gz"

    def produce(self):
        return capsule.produce(
            self.artifact, roots=self.roots, binding=self.binding, scope="native_fixture"
        )

    def test_logical_posix_references_use_actual_private_files(self):
        fixture = RuntimeCapsuleTests()
        fixture.use_logical_paths = True
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        document = fixture.produce()
        self.assertTrue(document["capsule_ref"]["path"].startswith("/private/"))
        self.assertEqual(fixture.physical(document["capsule_ref"]["path"]), fixture.artifact)
        target = fixture.root / "restored"
        target.mkdir(mode=0o700)
        with fixture.artifact.open("rb") as source:
            receipt = capsule.verify_archive(document, source, destination=target)
        self.assertEqual(receipt["files"], 4)

    def test_windows_path_and_descriptor_times_are_stable_independent_branches(self):
        fields = dict.fromkeys(
            ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_birthtime_ns", "st_uid", "st_gid"), 1
        )
        fields.update(st_mode=0o100600, st_nlink=1)
        before = types.SimpleNamespace(**fields, st_ctime_ns=2)
        opened = types.SimpleNamespace(**fields, st_ctime_ns=3)
        original = io.unchanged_read
        with patch.object(
            io, "unchanged_read", side_effect=lambda *rows: original(*rows, windows=True)
        ):
            self.assertTrue(capsule._same_file_read(before, opened, opened, before))
            for row, key in ((before, "st_ctime_ns"), (opened, "st_ctime_ns"), (opened, "st_mode")):
                changed = copy.copy(row)
                setattr(changed, key, getattr(row, key) + 1)
                after, final = (changed, before) if row is opened else (opened, changed)
                self.assertFalse(capsule._same_file_read(before, opened, after, final))

    def test_actual_files_have_deterministic_capsule_and_independent_full_disk_restore(self):
        document = self.produce()
        other = capsule.produce(
            self.root / "other.gz", roots=self.roots, binding=self.binding, scope="native_fixture"
        )
        self.assertEqual(document["capsule_ref"]["sha256"], other["capsule_ref"]["sha256"])
        destination = self.root / "independent"
        destination.mkdir(mode=0o700)
        with self.artifact.open("rb") as source:
            receipt = capsule.verify_archive(document, source, destination=destination)
        self.assertEqual(receipt["files"], 4)
        self.assertFalse(receipt["archived_code_executed"])
        self.assertEqual(receipt["bytes"], sum(row["bytes"] for row in document["entries"]))
        self.assertEqual(
            capsule.installed_entries({name: str(destination / name) for name in self.roots}),
            document["entries"],
        )
        self.assertFalse((destination / "venv/lib/__pycache__").exists())

    def test_restored_bytes_and_current_installation_drift_are_independent_denials(self):
        document = self.produce()
        (self.physical(self.roots["venv"]) / "lib/package.py").write_bytes(b"changed live source\n")
        with self.assertRaisesRegex(io.Refusal, "installed_index_changed"):
            capsule.verify_installed(document)
        with self.artifact.open("rb") as source:
            capsule.verify_archive(document, source)  # captured bytes still restore
        changed = copy.deepcopy(document)
        row = next(row for row in changed["entries"] if row["kind"] == "file")
        row["sha256"] = "0" * 64
        with (
            self.artifact.open("rb") as source,
            self.assertRaisesRegex(io.Refusal, "tar_file_hash"),
        ):
            capsule.verify_archive(changed, source)
        changed = copy.deepcopy(document)
        changed["capsule_ref"]["sha256"] = "0" * 64
        with self.artifact.open("rb") as source, self.assertRaisesRegex(io.Refusal, "capsule_hash"):
            capsule.verify_archive(changed, source)

    def test_shared_index_semantics_are_local_and_all_source_bytes_are_rechecked(self):
        document = self.produce()
        encoded = io.canonical(document)
        reference = {"path": self.logical(self.root / "index.json"), "sha256": io.sha(encoded)}
        files = {
            reference["path"]: encoded,
            self.logical(self.artifact): self.artifact.read_bytes(),
        }
        reads = []

        def read(ref):
            reads.append(ref["path"])
            return files[ref["path"]]

        reader = cold.Reader(read)
        with patch.object(capsule, "validate", wraps=capsule.validate) as semantic:
            for _role in range(6):
                self.assertEqual(
                    reader.index(reference, "runtime-index", self.binding, "native_fixture"),
                    document,
                )
            self.assertEqual(semantic.call_count, 1)
            self.assertEqual(len(reads), 2)
            reader.recheck()
            self.assertEqual(len(reads), 4)
            with self.assertRaises(io.Refusal):
                reader.index(
                    reference,
                    "runtime-index",
                    {"candidate_manager_sha": "2" * 40},
                    "native_fixture",
                )
            files[self.logical(self.artifact)] += b"changed after captured validation"
            with self.assertRaisesRegex(io.Refusal, "reference_changed"):
                reader.recheck()
            with self.assertRaisesRegex(io.Refusal, "reference_bytes"):
                cold.Reader(read).index(reference, "runtime-index", self.binding, "native_fixture")

    def test_exact_data_can_be_materialized_once_and_existing_conflict_never_overwritten(self):
        document = self.produce()
        target = self.root / "target"
        target.mkdir(mode=0o700)
        document["runtime_roots"] = {name: self.logical(target / name) for name in self.roots}

        def read(ref):
            return self.physical(ref["path"]).read_bytes()

        receipt = capsule.materialize(document, read_reference=read)
        self.assertFalse(receipt["activation_authorized"])
        first = capsule.installed_entries(document["runtime_roots"])
        capsule.materialize(document, read_reference=read)
        self.assertEqual(capsule.installed_entries(document["runtime_roots"]), first)
        original = target / "venv/lib/package.py"
        original.write_bytes(b"different installed state")
        with self.assertRaisesRegex(io.Refusal, "existing_conflict"):
            capsule.materialize(document, read_reference=read)
        self.assertEqual(original.read_bytes(), b"different installed state")

    def test_partial_two_root_publication_reconciles_only_exact_existing_bytes(self):
        document = self.produce()
        target = self.root / "target"
        target.mkdir(mode=0o700)
        shutil.copytree(self.physical(self.roots["python"]), target / "python")
        document["runtime_roots"] = {name: self.logical(target / name) for name in self.roots}
        capsule.materialize(document, read_reference=lambda ref: self.artifact.read_bytes())
        capsule.verify_installed(document)

    def test_index_paths_types_aliases_and_expansion_limits_fail_before_restore(self):
        document = self.produce()
        cases = (
            lambda value: value["entries"][0].update(name="../escape"),
            lambda value: value["entries"].append(copy.deepcopy(value["entries"][-1])),
            lambda value: value.update(unpacked_bytes=True),
            lambda value: value["runtime_roots"].update(venv=value["runtime_roots"]["python"]),
            lambda value: value["entries"][0].update(mode=0o777),
        )
        for damage in cases:
            with self.subTest(damage=damage):
                changed = copy.deepcopy(document)
                damage(changed)
                with self.assertRaises(io.Refusal):
                    capsule.validate_index(changed, binding=self.binding, scope="native_fixture")
        with (
            patch.object(capsule, "MAX_EXPANDED_BYTES", 1),
            self.assertRaisesRegex(io.Refusal, "expanded_limit"),
        ):
            capsule.validate_index(document, binding=self.binding, scope="native_fixture")
        production = copy.deepcopy(document)
        production["scope"] = "production_native"
        with self.assertRaisesRegex(io.Refusal, "production_root"):
            capsule.validate_index(production, binding=self.binding, scope="production_native")

    def test_hidden_second_archive_pax_bomb_and_compressed_limit_are_refused(self):
        document = self.produce()
        raw = self.artifact.read_bytes()
        uncompressed = gzip.decompress(raw)
        malicious = gzip.compress(uncompressed + uncompressed, mtime=0)
        document["capsule_ref"]["sha256"] = io.sha(malicious)
        with self.assertRaisesRegex(io.Refusal, "trailing_bytes"):
            capsule.verify_archive(document, streams.BytesIO(malicious))
        header = tarfile.TarInfo("pax")
        header.type, header.size = tarfile.XHDTYPE, 20 * 1024**2
        bomb = gzip.compress(header.tobuf() + b"\0" * 1024, mtime=0)
        document["capsule_ref"]["sha256"] = io.sha(bomb)
        with self.assertRaisesRegex(io.Refusal, "pax_limit"):
            capsule.verify_archive(document, streams.BytesIO(bomb))
        document["capsule_ref"]["sha256"] = io.sha(raw)
        with (
            patch.object(capsule, "MAX_CAPSULE_BYTES", 16),
            self.assertRaisesRegex(io.Refusal, "capsule_limit"),
        ):
            capsule.verify_archive(document, streams.BytesIO(raw))

    @unittest.skipUnless(os.name == "posix", "Requires real Linux flock, AF_UNIX and SO_PEERCRED")
    def test_internal_symlinks_are_indexed_and_escape_or_extra_file_is_denied(self):
        root = self.physical(self.roots["venv"])
        (root / "bin/python").symlink_to("python3.12")
        (root / "lib64").symlink_to("lib", target_is_directory=True)
        document = self.produce()
        target = self.root / "restored"
        target.mkdir(mode=0o700)
        with self.artifact.open("rb") as source:
            capsule.verify_archive(document, source, destination=target)
        self.assertEqual(os.readlink(target / "venv/bin/python"), "python3.12")
        (root / "bin/escape").symlink_to("../../outside")
        with self.assertRaisesRegex(io.Refusal, "symlink_escape"):
            capsule.produce(
                self.root / "bad.gz", roots=self.roots, binding=self.binding, scope="native_fixture"
            )

    def test_runtime_v2_reader_uses_capsule_capability_and_never_executes_indexed_source(self):
        document = self.produce()
        raw = io.canonical(document)
        reference = {"path": "/captured/runtime-index.json", "sha256": io.sha(raw)}
        calls = []

        class Reader:
            def read_reference(self, ref):
                self.assert_reference(ref)
                return raw

            def assert_reference(self, ref):
                if ref != reference:
                    raise io.Refusal("ordinary_source_must_not_read_capsule")

            def read_runtime_capsule(self, ref):
                calls.append(ref)
                return self_artifact.read_bytes()

        self_artifact = self.artifact
        reader = cold.Reader(Reader().read_reference)
        self.assertEqual(
            reader.index(reference, "runtime-index", self.binding, "native_fixture"), document
        )
        reader.recheck()
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
