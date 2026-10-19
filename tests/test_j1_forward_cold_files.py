"""Real private files; native unit and mapping observations are explicit fixtures."""

from __future__ import annotations

import copy
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_cold_runtime_capsule as capsule
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal
from tests import test_j1_forward_cold_runtime_capsule as capsule_fixture


@unittest.skipUnless(
    sys.platform.startswith("linux"), "Requires real Linux flock, AF_UNIX and SO_PEERCRED"
)
class SharedNativeTreeTests(unittest.TestCase):
    def exercise(self, damage=None):
        fixture = capsule_fixture.RuntimeCapsuleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source_root = fixture.root / "source"
        source_root.mkdir(mode=0o700)
        source = source_root / "entry.py"
        source.write_bytes(b"never executed source fixture\n")
        source_sha = io.sha(source.read_bytes())
        guard_raw = b"not a native permit; unit file-measurement fixture"
        consumers = {
            name: io.sha(
                Path(native.__file__).with_name("j1_forward_cold_" + name + ".py").read_bytes()
            )
            for name in ("authority", "native", "history", "hold")
        }
        consumers["guard"] = io.sha(guard_raw)
        fixture.binding["consumer_sha256"] = cold.digest(consumers)
        runtime = fixture.produce()
        files = {}

        def ref(name, value, *, path=None):
            raw = value if type(value) is bytes else io.canonical(value)
            path = path or str(fixture.root / name)
            files[path] = raw
            return {"path": path, "sha256": io.sha(raw)}

        runtime_ref = ref("runtime.json", runtime)
        guard_ref = ref("guard.py", guard_raw, path=str(native.INSTALL / "guard.py"))
        native_ref = ref(
            "native.py", Path(native.__file__).read_bytes(), path=str(native.INSTALL / "native.py")
        )
        index_ref = ref(
            "source-index.json", {"files": [{"path": str(source), "sha256": source_sha}]}
        )
        source_package = ref("source.json", {"guard_ref": guard_ref, "native_ref": native_ref})
        properties = dict.fromkeys(native.UNIT_PROPERTIES, "")
        properties.update(
            ActiveState="inactive",
            SubState="dead",
            MainPID="0",
            ExecCondition="{ path=/usr/bin/false ; argv[]=/usr/bin/false ; ignore_errors=no ; }",
        )
        unit_ref = ref("unit.json", {"properties": properties})
        validated = {
            "package": {
                "binding": fixture.binding,
                "scope": "native_fixture",
                "source_package_ref": source_package,
            },
            "policies": {
                unit: {
                    "binding": fixture.binding,
                    "source_root": str(source_root),
                    "source_index_ref": index_ref,
                    "runtime_index_ref": runtime_ref,
                    "unit_ref": unit_ref,
                }
                for unit in cold.ALLOWED
            },
        }
        units, source_reads = [], []

        def source_measure(policy):
            source_reads.append(policy["source_index_ref"])
            self.assertEqual(io.sha(source.read_bytes()), source_sha)
            self.assertEqual(list(source_root.iterdir()), [source])

        def systemd(unit):
            units.append(unit)
            if len(units) == 5 and damage:
                target = (
                    source if damage == "source" else Path(fixture.roots["venv"]) / "lib/package.py"
                )
                if damage == "replacement":
                    replacement = target.with_name("new-file.py")
                    replacement.write_bytes(target.read_bytes())
                    replacement.replace(target)
                else:
                    target.write_bytes(b"changed between first and last profile\n")
            return copy.deepcopy(properties)

        def read(reference):
            raw = files[reference["path"]]
            self.assertEqual(io.sha(raw), reference["sha256"])
            return raw

        provider = native.NativeProvider()
        with (
            rehearsal.PrivateFilesystem(fixture.root),
            patch.object(provider, "read_reference", side_effect=read),
            patch.object(provider, "systemd", side_effect=systemd),
            patch.object(provider, "_source_tree_root", return_value=source_root),
            patch.object(provider, "_exact_source_tree", side_effect=source_measure),
            patch.object(capsule, "verify_installed", wraps=capsule.verify_installed) as measured,
        ):
            if damage:
                with self.assertRaisesRegex(io.Refusal, "installed_tree_changed"):
                    provider.verify_files(validated)
                self.assertEqual(len(units), 6)
                self.assertEqual(measured.call_count, 1)
                self.assertEqual(len(source_reads), 1)
            else:
                for _observation in range(2):
                    provider.verify_files(validated)
                self.assertEqual(units, [*cold.ALLOWED, *cold.ALLOWED])
                self.assertEqual(measured.call_count, 2)
                self.assertEqual(len(source_reads), 2)

    def test_six_profiles_share_one_measurement_but_each_observation_is_fresh(self):
        self.exercise()

    def test_source_or_runtime_change_and_same_byte_replacement_during_unit_checks_deny(self):
        for damage in ("source", "runtime", "replacement"):
            with self.subTest(damage=damage):
                self.exercise(damage)


@unittest.skipUnless(
    sys.platform.startswith("linux"), "Requires real Linux flock, AF_UNIX and SO_PEERCRED"
)
class LoadedLibraryIdentityTests(unittest.TestCase):
    def test_mapped_inode_unknown_path_and_postread_replacement_are_denied(self):
        fixture = capsule_fixture.RuntimeCapsuleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        library = fixture.root / "host-libc.so"
        library.write_bytes(b"private bytes, never loaded as code")
        reference = {"path": str(library), "sha256": io.sha(library.read_bytes())}
        files = {
            "/policy.json": io.canonical(
                dict.fromkeys(
                    ("source_index_ref", "runtime_index_ref", "startup_index_ref"),
                    {"path": "/index.json", "sha256": "0" * 64},
                )
            ),
            "/index.json": io.canonical({"files": [reference]}),
        }
        context = {"policy_ref": {"path": "/policy.json", "sha256": "1" * 64}}
        maps_path = Path("/proc/987654321/maps")
        original_read_text = Path.read_text
        swap_on_read = False

        def read(row):
            if row["path"] in files:
                return files[row["path"]]
            raw = library.read_bytes()
            self.assertEqual(io.sha(raw), row["sha256"])
            if swap_on_read:
                replacement = library.with_name("replacement.so")
                replacement.write_bytes(raw)
                replacement.replace(library)
            return raw

        provider = types.SimpleNamespace(read_reference=read)

        def mapping(path):
            info = library.stat()
            return f"1000-2000 r-xp 0 {os.major(info.st_dev):x}:{os.minor(info.st_dev):x} {info.st_ino} {path}\n"

        raw_maps = mapping(library)

        def text(path, *args, **kwargs):
            return raw_maps if path == maps_path else original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", text):
            lifecycle._loaded_libraries(987654321, context, provider)
            original_policy = files["/policy.json"]
            kernel_policy = io.document(original_policy)
            kernel_policy["protocol_index_ref"] = kernel_policy.pop("source_index_ref")
            files["/policy.json"] = io.canonical(kernel_policy)
            lifecycle._loaded_libraries(987654321, context, provider)
            files["/policy.json"] = original_policy
            raw_maps = mapping(fixture.root / "unlisted.so")
            with self.assertRaisesRegex(io.Refusal, "unaccepted_loaded_library"):
                lifecycle._loaded_libraries(987654321, context, provider)
            raw_maps = mapping(library)
            replacement = library.with_name("before-read.so")
            replacement.write_bytes(library.read_bytes())
            replacement.replace(library)
            with self.assertRaisesRegex(io.Refusal, "loaded_library_identity"):
                lifecycle._loaded_libraries(987654321, context, provider)
            raw_maps = mapping(library)
            swap_on_read = True
            with self.assertRaisesRegex(io.Refusal, "loaded_library_changed"):
                lifecycle._loaded_libraries(987654321, context, provider)
