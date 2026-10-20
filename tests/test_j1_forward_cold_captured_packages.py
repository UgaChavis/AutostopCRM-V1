"""Pure archive memo mechanics; fixtures grant no native/release authority."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_runtime_capsule as capsule
from scripts import j1_forward_io as io
from tests import test_j1_forward_cold_runtime_capsule as runtime_tests


class CapturedPackageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_tests.RuntimeCapsuleTests()
        self.fixture.use_logical_paths = True
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.binding = {
            "attempt_id": "11111111-1111-4111-8111-111111111111",
            "boot_id": "22222222-2222-4222-8222-222222222222",
            "generation": "33333333-3333-4333-8333-333333333333",
            "consumer_sha256": "a" * 64,
            "candidate_crm_sha": "b" * 40,
            "candidate_manager_sha": "c" * 40,
            "deployment_lock_identity": [1, 2],
            "not_before_ns": 1000,
            "deadline_ns": 2000,
        }
        self.fixture.binding = self.binding
        self.index = self.fixture.produce()
        self.files = {
            "/synthetic/index.json": io.canonical(self.index),
            self.index["capsule_ref"]["path"]: self.fixture.artifact.read_bytes(),
            "/synthetic/source.py": b"raise AssertionError('must not execute')\n",
            "/synthetic/python3.12": b"\x7fELF synthetic never executed",
        }
        package = dict.fromkeys(cold.PACKAGE_FIELDS)
        package.update(
            schema=cold.SCHEMA, scope="native_fixture", contract=cold.CONTRACT, binding=self.binding
        )
        self.files["/synthetic/package.json"] = io.canonical(package)
        self.reference = self.ref("/synthetic/package.json")
        self.clock = {"boot_id": self.binding["boot_id"], "monotonic_ns": 1500}
        self.view = cold._CapturedPackages(self.clock)

    def ref(self, path):
        return {"path": path, "sha256": io.sha(self.files[path])}

    def reader(self, view):
        fixture = self

        class CapturedReader:
            def __init__(self):
                self._captured_packages, self.calls = view, []

            def read(self, reference):
                self.calls.append((reference["path"], "source"))
                return fixture.files[reference["path"]]

            def read_opaque_reference(self, reference):
                self.calls.append((reference["path"], "opaque"))
                return fixture.files[reference["path"]]

            def read_runtime_capsule(self, reference):
                self.calls.append((reference["path"], "capsule"))
                return fixture.files[reference["path"]]

        return CapturedReader()

    def decode(self, reference, reader, **kwargs):
        # The real capsule/index/hash/typed-reader validators still run. Only
        # unrelated package policy fields are absent from this small fixture.
        package = reader.doc(reference)
        cold._binding(package["binding"], now_ns=kwargs["now_ns"])
        if kwargs["required_scope"] is not None:
            cold.need(kwargs["required_scope"] == package["scope"], "package_scope")
        reader.index(
            self.ref("/synthetic/index.json"), "runtime-index", self.binding, "native_fixture"
        )
        reader.raw(self.ref("/synthetic/source.py"))
        reader.raw(self.ref("/synthetic/python3.12"), opaque=True)
        return {"package": package, "synthetic_only": True}

    def validate(self, reader, *, now=1500, scope=None):
        return cold.validate_package(
            self.reference, read_reference=reader.read, now_ns=now, required_scope=scope
        )

    def test_same_call_reuses_data_but_registers_all_typed_leaves_in_each_reader(self):
        first, second = self.reader(self.view), self.reader(self.view)
        with (
            patch.object(cold, "_decode_package", side_effect=self.decode) as decode,
            patch.object(capsule, "validate", wraps=capsule.validate) as packed,
        ):
            result = self.validate(first)
            result["package"]["scope"] = "mutated returned copy"
            self.assertEqual(self.validate(second)["package"]["scope"], "native_fixture")
        self.assertEqual(decode.call_count, 1)
        self.assertEqual(packed.call_count, 1)
        self.assertEqual(
            set(second.calls),
            {
                ("/synthetic/package.json", "source"),
                ("/synthetic/index.json", "source"),
                ("/synthetic/source.py", "source"),
                ("/synthetic/python3.12", "opaque"),
                (self.index["capsule_ref"]["path"], "capsule"),
            },
        )

    def test_distinct_requested_clock_revalidates_binding_and_has_no_new_index_claim(self):
        reader = self.reader(self.view)
        with (
            patch.object(cold, "_decode_package", side_effect=self.decode) as decode,
            patch.object(capsule, "validate", wraps=capsule.validate) as packed,
        ):
            self.validate(reader)
            self.validate(reader, now=1501)
            self.assertEqual(decode.call_count, 2)
            self.assertEqual(packed.call_count, 1)
            for now in (999, 2000, True, 1500.0):
                with self.subTest(now=now), self.assertRaises(io.Refusal):
                    self.validate(reader, now=now)
            with self.assertRaises(io.Refusal):
                self.validate(reader, scope="production_native")

    def test_changed_package_source_index_capsule_or_opaque_leaf_denies(self):
        for path in self.files:
            with self.subTest(path=path):
                view = cold._CapturedPackages(self.clock)
                reader = self.reader(view)
                with patch.object(cold, "_decode_package", side_effect=self.decode):
                    self.validate(reader)
                    original = self.files[path]
                    self.files[path] = original + b"changed"
                    try:
                        with self.assertRaises(io.Refusal):
                            self.validate(self.reader(view))
                    finally:
                        self.files[path] = original

    def test_new_context_and_ordinary_reader_cannot_reuse_prior_validation(self):
        with (
            patch.object(cold, "_decode_package", side_effect=self.decode) as decode,
            patch.object(capsule, "validate", wraps=capsule.validate) as packed,
        ):
            self.validate(self.reader(self.view))
            self.validate(self.reader(cold._CapturedPackages(self.clock)))
            ordinary = self.reader(None)
            self.validate(ordinary)
            self.validate(ordinary)
        self.assertEqual(decode.call_count, 4)
        self.assertEqual(packed.call_count, 4)

    def test_alias_restore_and_semantic_package_share_only_verified_index_bytes(self):
        reader = self.reader(self.view)
        with (
            patch.object(cold, "_decode_package", side_effect=self.decode) as decode,
            patch.object(capsule, "validate", wraps=capsule.validate) as packed,
        ):
            self.view.runtime_index(
                self.ref("/synthetic/index.json"),
                binding=self.binding,
                scope="native_fixture",
                read_reference=reader.read,
            )
            self.validate(self.reader(self.view))
        self.assertEqual(decode.call_count, 1)
        self.assertEqual(packed.call_count, 1)

    def test_outer_clock_and_ref_changes_are_not_cache_aliases(self):
        for change in ({"monotonic_ns": 1501}, {"boot_id": "another-boot"}, {"monotonic_ns": True}):
            with self.subTest(change=change), self.assertRaises(io.Refusal):
                self.view.bind_clock({**self.clock, **change})
        reader = self.reader(self.view)
        with patch.object(cold, "_decode_package", side_effect=self.decode):
            self.validate(reader)
            changed = copy.deepcopy(self.reference)
            changed["sha256"] = "0" * 64
            with self.assertRaises(io.Refusal):
                cold.validate_package(changed, read_reference=reader.read, now_ns=1500)


if __name__ == "__main__":
    unittest.main()
