"""Strict v5 data aliases; archived ELF/Python bytes are never executed."""

from __future__ import annotations

import copy
import gzip
import io as streams
import os
import tarfile
import unittest
from unittest.mock import patch

from scripts import j1_forward_archive as transport
from scripts import j1_forward_cold_archive as archive
from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_state
from scripts import j1_forward_io as io
from scripts import j1_forward_transport as wire
from tests import test_j1_forward_cold_runtime_capsule as runtime_tests
from tests import test_j1_forward_state as state_tests


class InterpreterAliasTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_tests.RuntimeCapsuleTests()
        self.fixture.use_logical_paths = True
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.elf = b"\x7fELF" + b"synthetic immutable interpreter; never execute" * 3
        self.path = self.fixture.roots["venv"] + "/bin/python3.12"
        self.fixture.physical(self.path).write_bytes(self.elf)
        self.document = self.fixture.produce()
        self.index_path = "/private/index.json"
        self.files = {
            self.index_path: io.canonical(self.document),
            self.document["capsule_ref"]["path"]: self.fixture.artifact.read_bytes(),
            self.path: self.elf,
        }
        self.index_ref = self.ref(self.index_path)
        self.package = {"binding": self.fixture.binding, "scope": "native_fixture", "policies": {}}
        for unit in cold.ALLOWED:
            policy = dict.fromkeys(cold.POLICY_FIELDS)
            policy.update(
                unit=unit,
                binding=self.fixture.binding,
                runtime_index_ref=self.index_ref,
                interpreter_ref=self.ref(self.path),
            )
            path = "/private/policy-" + unit + ".json"
            self.files[path] = io.canonical(policy)
            self.package["policies"][unit] = self.ref(path)

    def ref(self, path):
        return {"path": path, "sha256": io.sha(self.files[path])}

    def read(self, ref):
        raw = self.files[ref["path"]]
        self.assertEqual(io.sha(raw), ref["sha256"])
        return raw

    def aliases(self):
        return archive._interpreter_aliases(self.package, self.read)

    def restored_inputs(self):
        return {path: raw for path, raw in self.files.items() if path != self.path}

    def update_index(self, change):
        document = copy.deepcopy(self.document)
        change(document)
        self.files[self.index_path] = io.canonical(document)
        self.index_ref = self.ref(self.index_path)
        for unit, ref in self.package["policies"].items():
            policy = io.document(self.files[ref["path"]])
            policy["runtime_index_ref"] = self.index_ref
            self.files[ref["path"]] = io.canonical(policy)
            self.package["policies"][unit] = self.ref(ref["path"])

    def test_same_policy_interpreter_has_one_exact_member_alias_and_full_bytes(self):
        aliases = self.aliases()
        self.assertEqual(set(aliases), {self.path})
        self.assertEqual(aliases[self.path]["member"], "venv/bin/python3.12")
        self.assertEqual(aliases[self.path]["runtime_index_ref"], self.index_ref)
        with patch("subprocess.Popen", side_effect=AssertionError("archived code executed")):
            restored = archive.restore_interpreters(aliases, self.restored_inputs())
        self.assertEqual(restored, {self.path: self.elf})
        self.assertEqual(io.sha(restored[self.path]), aliases[self.path]["sha256"])

    def test_physical_budget_omits_alias_but_retains_and_rechecks_full_graph(self):
        aliases = self.aliases()
        snapshot = archive._ReadSnapshot(lambda ref: self.files[ref["path"]], codec=5)
        snapshot.kernel_v2 = True
        snapshot.runtime_aliases = aliases
        snapshot.read(self.index_ref)
        snapshot.read_runtime_capsule(self.document["capsule_ref"])
        snapshot.read_opaque_reference(self.ref(self.path))
        self.assertEqual(
            set(snapshot.files), {self.index_path, self.document["capsule_ref"]["path"], self.path}
        )
        self.assertEqual(
            snapshot.total,
            sum(len(raw) for path, raw in snapshot.files.items() if path != self.path),
        )
        snapshot.recheck()
        self.files[self.path] += b"changed"
        with self.assertRaisesRegex(io.Refusal, "source_changed"):
            snapshot.recheck()

    @unittest.skipUnless(os.name == "posix", "Actual inode/mode checks are POSIX")
    def test_actual_mode_hardlinks_and_final_source_version_are_checked(self):
        aliases = self.aliases()
        physical = self.fixture.physical(self.path)
        for mode, hardlink in ((0o700, False), (0o755, True)):
            physical.chmod(mode)
            link = physical.with_name("same-inode")
            if hardlink:
                os.link(physical, link)
            try:
                snapshot = archive._ReadSnapshot(
                    self.read, lambda _: archive._version(physical), codec=5
                )
                snapshot.kernel_v2 = True
                snapshot.runtime_aliases = aliases
                with self.assertRaisesRegex(io.Refusal, "source_metadata"):
                    snapshot.read_opaque_reference(self.ref(self.path))
            finally:
                link.unlink(missing_ok=True)
        physical.chmod(0o755)
        snapshot = archive._ReadSnapshot(self.read, lambda _: archive._version(physical), codec=5)
        snapshot.kernel_v2 = True
        snapshot.runtime_aliases = aliases
        snapshot.read_opaque_reference(self.ref(self.path))
        physical.chmod(0o700)
        with self.assertRaisesRegex(io.Refusal, "source_version_changed"):
            snapshot.recheck()

    def test_equal_hash_at_an_unrelated_path_is_not_a_capsule_alias(self):
        for unit, ref in self.package["policies"].items():
            policy = io.document(self.files[ref["path"]])
            policy["interpreter_ref"] = {
                "path": "/private/unrelated/python3.12",
                "sha256": io.sha(self.elf),
            }
            self.files[ref["path"]] = io.canonical(policy)
            self.package["policies"][unit] = self.ref(ref["path"])
        self.assertEqual(self.aliases(), {})

    def test_wrong_member_binding_symlink_and_cross_binding_indexes_refuse(self):
        for change in (
            lambda d: d.update(binding={"candidate_manager_sha": "2" * 40}),
            lambda d: next(r for r in d["entries"] if r["name"] == "venv/bin/python3.12").update(
                sha256="0" * 64
            ),
            lambda d: next(r for r in d["entries"] if r["name"] == "venv/bin/python3.12").update(
                bytes=True
            ),
            lambda d: next(r for r in d["entries"] if r["name"] == "venv/bin/python3.12").update(
                kind="symlink", bytes=0, sha256=None, target="../lib/package.py", mode=0o777
            ),
        ):
            with self.subTest(change=change):
                saved = copy.deepcopy(self.files), copy.deepcopy(self.package), self.index_ref
                self.update_index(change)
                with self.assertRaises(io.Refusal):
                    self.aliases()
                self.files, self.package, self.index_ref = saved

    def test_descriptor_mutations_and_an_extra_raw_payload_refuse(self):
        original = self.aliases()
        for key, value in (
            ("member", "venv/lib/package.py"),
            ("member", ["venv/bin/python3.12"]),
            ("bytes", True),
            ("bytes", len(self.elf) + 1),
            ("mode", 0o777),
            ("mode", 0o700),
            ("sha256", "0" * 64),
            ("runtime_index_ref", {**self.index_ref, "sha256": "0" * 64}),
            ("extra", False),
        ):
            aliases = copy.deepcopy(original)
            aliases[self.path][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(io.Refusal):
                archive.restore_interpreters(aliases, self.restored_inputs())
        with self.assertRaisesRegex(io.Refusal, "payload_present"):
            archive.restore_interpreters(original, self.files)

    def test_corrupt_unselected_capsule_member_is_not_skipped(self):
        raw = self.files[self.document["capsule_ref"]["path"]]
        destination = streams.BytesIO()
        with gzip.GzipFile(fileobj=destination, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as output:
                with tarfile.open(fileobj=streams.BytesIO(raw), mode="r:gz") as source:
                    for member in source:
                        stream = source.extractfile(member) if member.isfile() else None
                        payload = stream.read() if stream else b""
                        if member.name == "venv/lib/package.py":
                            payload += b"changed unselected source"
                            member.size = len(payload)
                        output.addfile(member, streams.BytesIO(payload) if stream else None)
        changed = destination.getvalue()
        self.files[self.document["capsule_ref"]["path"]] = changed
        self.update_index(lambda d: d["capsule_ref"].update(sha256=io.sha(changed)))
        with self.assertRaises(io.Refusal):
            archive.restore_interpreters(self.aliases(), self.restored_inputs())

    def test_non_elf_interpreter_cannot_be_reconstructed(self):
        self.fixture.physical(self.path).write_bytes(b"plain archived Python source")
        self.fixture.artifact.unlink()
        self.document = self.fixture.produce()
        self.files[self.document["capsule_ref"]["path"]] = self.fixture.artifact.read_bytes()
        self.files[self.path] = b"plain archived Python source"
        self.update_index(lambda d: d.update(**self.document))
        for unit, ref in self.package["policies"].items():
            policy = io.document(self.files[ref["path"]])
            policy["interpreter_ref"] = self.ref(self.path)
            self.files[ref["path"]] = io.canonical(policy)
            self.package["policies"][unit] = self.ref(ref["path"])
        with self.assertRaisesRegex(io.Refusal, "alias_elf"):
            archive.restore_interpreters(self.aliases(), self.restored_inputs())

    def test_v5_is_explicit_and_legacy_transport_caps_are_unchanged(self):
        self.assertEqual(transport.transport_total_limit(5), 512 * 1024**2)
        self.assertEqual(transport.transport_total_limit(4), 512 * 1024**2)
        self.assertEqual(transport.transport_total_limit(3), 128 * 1024**2)
        self.assertEqual(transport.COLD_RUNTIME_MAX_FILE_BYTES, 384 * 1024**2)
        manifest = {
            "schema": transport.COLD_V5_SCHEMA,
            "artifacts": [{"logical_name": "authority" + self.index_path}],
            "runtime_aliases": self.aliases(),
        }
        self.assertEqual(transport._runtime_aliases(manifest), self.aliases())
        for schema in (transport.COLD_SCHEMA, transport.COLD_V3_SCHEMA, transport.SCHEMA):
            with self.subTest(schema=schema), self.assertRaises(io.Refusal):
                transport._runtime_aliases({**manifest, "schema": schema})
        manifest["artifacts"].append({"logical_name": "authority" + self.path})
        with self.assertRaisesRegex(io.Refusal, "alias_inventory"):
            transport._runtime_aliases(manifest)

    @unittest.skipUnless(os.name == "posix", "Physical mode materialization is POSIX")
    def test_flat_transport_restores_exact_interpreter_before_semantic_replay(self):
        # Exercise the wire and physical restoration boundary. Full cold package
        # semantics have separate integration coverage, rather than a fake grant.
        root = "/synthetic/cold-control"
        plan_path, backup_path = (
            root + "/attempts/test/plan.json",
            root + "/attempts/test/backup.json",
        )
        plan = io.canonical({"schema": j1_forward_cold_state.PLAN_SCHEMA})
        backup = io.canonical({"synthetic_backup": True})
        plan_ref = {"path": plan_path, "sha256": io.sha(plan)}
        backup_ref = {"path": backup_path, "sha256": io.sha(backup)}
        physical = self.fixture.root / "flat-package"
        physical.mkdir(mode=0o700)
        rows = [
            ("control/attempts/test/plan.json", plan),
            ("control/attempts/test/backup.json", backup),
        ]
        rows += [("authority" + p, raw) for p, raw in sorted(self.restored_inputs().items())]
        artifacts = []
        for index, (name, raw) in enumerate(rows):
            payload = f"{index:06d}.bin"
            transport._write_bytes(physical / payload, raw, custody=lambda *_a, **_k: None)
            artifacts.append(
                {"logical_name": name, "payload": payload, "bytes": len(raw), "sha256": io.sha(raw)}
            )
        manifest = {
            "schema": transport.COLD_V5_SCHEMA,
            "stage": "closed",
            "root": root,
            "plan_ref": plan_ref,
            "backup_manifest_ref": backup_ref,
            "artifacts": artifacts,
            "semantic": {"boundary_only": True},
            "witnesses": [backup_ref] * 3,
            "native_capture_clock": {"boot_id": "synthetic", "monotonic_ns": 1},
            "runtime_blobs": {},
            "runtime_aliases": self.aliases(),
            **transport.CLAIMS,
        }
        raw_manifest = io.canonical(manifest)
        transport._write_bytes(
            physical / "manifest.json", raw_manifest, custody=lambda *_a, **_k: None
        )
        packed, unpacked, output = (
            self.fixture.root / name for name in ("flat.tar.gz", "unpacked", "output")
        )

        def semantic(*args, **kwargs):
            self.assertEqual(args[6], self.files)
            self.assertEqual(kwargs["runtime_aliases"], self.aliases())
            return manifest["semantic"]

        with (
            patch.object(io, "ROOT_UID", os.geteuid()),
            patch.object(
                io, "protected_directory", state_tests.ForwardStateTests.physical_directory
            ),
            patch.object(
                wire.core(),
                "protected_parent",
                lambda path: wire.core().physical(path, directory=True),
            ),
            patch("subprocess.Popen", side_effect=AssertionError("archived code executed")),
        ):
            envelope = transport.bundle(physical, io.sha(raw_manifest), packed)
            transport.unbundle(packed, envelope["archive"]["sha256"], unpacked, codec=5)
            with patch.object(transport, "_semantic", side_effect=semantic) as replay:
                receipt = transport.verify_and_restore(
                    unpacked,
                    io.sha(raw_manifest),
                    output,
                    plan_ref=plan_ref,
                    backup_manifest_ref=backup_ref,
                    stage="closed",
                    logical_root=root,
                )
        self.assertEqual(replay.call_count, 1)
        self.assertEqual((output / "runtime-alias-000000.elf").read_bytes(), self.elf)
        self.assertEqual((output / "runtime-alias-000000.elf").stat().st_mode & 0o777, 0o755)
        self.assertEqual(receipt["runtime_aliases_restored"], 1)
        self.assertTrue(receipt["runtime_alias_posix_modes_materialized"])
        self.assertTrue(all(receipt[key] is False for key in transport.CLAIMS))


if __name__ == "__main__":
    unittest.main()
