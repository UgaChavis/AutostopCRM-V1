"""Full repeated native cuts; only immutable decoding belongs to one operation."""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import threading
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_fixture as topology
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_cold_runtime_capsule as capsule
from scripts import j1_forward_io as io


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
)
class PackageRequalificationTests(unittest.TestCase):
    def fixture(self):
        fixture = topology.Fixture()
        self.addCleanup(fixture.close)
        fixture.produce()
        return fixture

    def operation(self, fixture, calls=2):
        return native.PackageRequalification(
            fixture.package_ref,
            provider=fixture.provider,
            required_scope="native_fixture",
            max_calls=calls,
        )

    def assert_discarded(self, operation, sources, reader):
        self.assertEqual(operation.status, "closed")
        self.assertIsNone(operation.value)
        self.assertIsNone(operation.sources)
        self.assertIsNone(operation.reader)
        self.assertEqual(reader.seen, {})
        self.assertEqual(reader.opaque, set())
        self.assertEqual(reader.capsules, set())
        self.assertEqual(reader._validated_indexes, set())
        self.assertIsNone(reader.read)
        self.assertEqual(sources.versions, {})
        self.assertEqual(sources.references, {})
        self.assertIsNone(sources.trees)

    def test_lazy_two_three_four_full_cuts_decode_once_and_next_operation_decodes_again(self):
        fixture = self.fixture()
        for calls in (2, 3, 4):
            with (
                self.subTest(calls=calls),
                patch.object(cold, "_decode_package", wraps=cold._decode_package) as decode,
                patch.object(
                    fixture.provider, "verify_files", wraps=fixture.provider.verify_files
                ) as files,
                patch.object(native, "_mutable_package", wraps=native._mutable_package) as mutable,
                patch.object(
                    fixture.provider, "boundary", wraps=fixture.provider.boundary
                ) as boundary,
                patch.object(
                    fixture.provider, "read_reference", wraps=fixture.provider.read_reference
                ) as reads,
            ):
                unused = self.operation(fixture, calls)
                with unused:
                    self.assertEqual(decode.call_count, 0)
                    self.assertEqual(files.call_count, 0)
                    self.assertEqual(mutable.call_count, 0)
                operation = self.operation(fixture, calls)
                with operation:
                    for _index in range(calls):
                        value = operation.observe(fixture.package_ref, fixture.provider)
                        self.assertEqual(value["package"]["binding"], fixture.binding)
                    sources, reader = operation.sources, operation.reader
                    before_exit = reads.call_count
                self.assertGreater(reads.call_count, before_exit)
                self.assertEqual((decode.call_count, files.call_count), (1, calls))
                self.assertEqual((mutable.call_count, boundary.call_count), (calls, calls))
                self.assert_discarded(operation, sources, reader)
                with self.operation(fixture) as second:
                    second.observe(fixture.package_ref, fixture.provider)
                self.assertEqual(decode.call_count, 2)
                self.assertEqual(files.call_count, calls + 1)
                self.assertEqual(mutable.call_count, calls + 1)

    def test_original_tree_versions_survive_a_new_full_installed_measurement(self):
        fixture = self.fixture()
        original = fixture.provider.verify_files
        measurements = []

        def measure(value):
            original(value)
            if measurements:
                transient = fixture.root / "source/transient-empty-directory"
                transient.mkdir()
                transient.rmdir()
            trees = native._SharedTrees(protect=fixture.provider._typed_read_directory)
            trees.measure(("test-source",), (fixture.root / "source",), lambda: None)
            trees.finish()
            measurements.append(trees)
            return trees

        with patch.object(fixture.provider, "verify_files", side_effect=measure):
            with self.assertRaisesRegex(io.Refusal, "installed_tree_changed"):
                with self.operation(fixture) as operation:
                    operation.observe(fixture.package_ref, fixture.provider)
                    original_trees = operation.sources.trees
                    operation.observe(fixture.package_ref, fixture.provider)
        self.assertEqual(len(measurements), 2)
        self.assertIs(original_trees, measurements[0])
        self.assertEqual(original_trees.measured, {})

    def test_typed_v2_capsule_and_opaque_bytes_are_reread_without_redecoding(self):
        fixture = self.fixture()
        roots = {name: str(fixture.root / name) for name in ("python", "venv")}
        for root in roots.values():
            leaf = Path(root) / "bin/python3.12"
            leaf.parent.mkdir(parents=True)
            leaf.write_bytes(b"\x7fELF inert typed-reader bytes, never executed\n")
            leaf.chmod(0o700)
        index = capsule.produce(
            fixture.root / "runtime.tar.gz",
            roots=roots,
            binding=fixture.binding,
            scope="native_fixture",
        )
        leaf = fixture.provider.reference(Path(roots["venv"]) / "bin/python3.12")
        actual_decode = cold._decode_package

        def decode(reference, reader, **keywords):
            value = actual_decode(reference, reader, **keywords)
            # Separate structural runtime-v2 reader fixture. Actual native
            # process gates remain the unchanged small kernel-v1 cohort;
            # these inert ELF members do not claim kernel-v2 acceptance.
            capsule.validate(
                index,
                binding=fixture.binding,
                scope="native_fixture",
                read_reference=reader.read_runtime_capsule,
            )
            reader.raw(leaf, opaque=True)
            return value

        with patch.object(cold, "_decode_package", side_effect=decode) as decoded:
            with self.operation(fixture) as operation:
                operation.observe(fixture.package_ref, fixture.provider)
                with (
                    patch.object(
                        fixture.provider,
                        "read_runtime_capsule",
                        wraps=fixture.provider.read_runtime_capsule,
                    ) as packed,
                    patch.object(
                        fixture.provider,
                        "read_opaque_reference",
                        wraps=fixture.provider.read_opaque_reference,
                    ) as opaque,
                ):
                    operation.observe(fixture.package_ref, fixture.provider)
                    self.assertGreaterEqual(packed.call_count, 2)
                    self.assertGreaterEqual(
                        sum(call.args == (leaf,) for call in opaque.call_args_list), 2
                    )
            self.assertEqual(decoded.call_count, 1)
            for reference in (leaf, index["capsule_ref"]):
                path = Path(reference["path"])
                raw = path.read_bytes()
                try:
                    with self.assertRaises(io.Refusal):
                        with self.operation(fixture) as operation:
                            operation.observe(fixture.package_ref, fixture.provider)
                            path.write_bytes(raw + b"changed")
                            operation.observe(fixture.package_ref, fixture.provider)
                finally:
                    path.write_bytes(raw)

    def test_mutable_gates_are_fresh_and_failures_poison_the_context(self):
        import fcntl

        for damage in ("source", "dispatch", "hold", "kernel", "boot", "deadline"):
            with self.subTest(damage=damage):
                fixture = self.fixture()
                with self.assertRaises(io.Refusal):
                    with self.operation(fixture) as operation:
                        value = operation.observe(fixture.package_ref, fixture.provider)
                        if damage == "source":
                            path = fixture.root / "source/worker.py"
                            before, raw = path.stat(), path.read_bytes()
                            path.write_bytes(raw + b"changed")
                            path.write_bytes(raw)
                            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                        elif damage == "dispatch":
                            fcntl.flock(fixture.dispatch_fd, fcntl.LOCK_UN)
                        elif damage == "hold":
                            with sqlite3.connect(fixture.root / "registry.sqlite3") as connection:
                                connection.execute(
                                    "UPDATE manager_automation_controller_runtime SET hold_enabled=0"
                                )
                        elif damage == "kernel":
                            actor = value["kernel"]["actor"]
                            child = next(row for row in fixture.children if row.pid == actor["pid"])
                            fixture._retire(child, actor)
                        override = (
                            patch.object(fixture.provider, "boot", return_value="changed")
                            if damage == "boot"
                            else patch.object(
                                native.time,
                                "monotonic_ns",
                                return_value=fixture.binding["deadline_ns"],
                            )
                            if damage == "deadline"
                            else nullcontext()
                        )
                        with override:
                            with self.assertRaises(
                                (io.Refusal, FileNotFoundError, ProcessLookupError)
                            ):
                                operation.observe(fixture.package_ref, fixture.provider)
                        with self.assertRaisesRegex(io.Refusal, "requalification_not_active"):
                            operation.observe(fixture.package_ref, fixture.provider)

    def test_reference_provider_thread_semantics_and_call_limits_are_bound(self):
        fixture = self.fixture()
        for damage in ("reference", "provider", "thread", "semantics", "limit"):
            with self.subTest(damage=damage), self.assertRaisesRegex(io.Refusal, "requalification"):
                with self.operation(fixture, 4) as operation:
                    operation.observe(fixture.package_ref, fixture.provider)
                    if damage == "semantics":
                        operation.value["package"]["binding"]["deadline_ns"] += 1
                    if damage == "thread":
                        failures = []

                        def wrong_owner():
                            try:
                                operation.observe(fixture.package_ref, fixture.provider)
                            except io.Refusal as error:
                                failures.append(str(error))

                        thread = threading.Thread(target=wrong_owner)
                        thread.start()
                        thread.join(timeout=5)
                        self.assertFalse(thread.is_alive())
                        self.assertEqual(len(failures), 1)
                    else:
                        for _index in range(4 if damage == "limit" else 1):
                            operation.observe(
                                {**fixture.package_ref, "sha256": "f" * 64}
                                if damage == "reference"
                                else fixture.package_ref,
                                object() if damage == "provider" else fixture.provider,
                            )

    def test_exit_checks_raw_semantics_clock_and_cleanup_even_when_exit_refuses(self):
        fixture = self.fixture()
        operation = self.operation(fixture)
        with self.assertRaisesRegex(io.Refusal, "source_version_changed"):
            with operation:
                operation.observe(fixture.package_ref, fixture.provider)
                sources, reader = operation.sources, operation.reader
                path = fixture.root / "source/worker.py"
                raw = path.read_bytes()
                path.write_bytes(raw + b"changed")
                path.write_bytes(raw)
        self.assert_discarded(operation, sources, reader)
        readers = []
        original_reader = cold.Reader

        def reader(read):
            result = original_reader(read)
            if not readers:
                readers.append(result)

                def refuse_outer_recheck():
                    raise io.Refusal("first_full_cut")

                result.recheck = refuse_outer_recheck
            return result

        with patch.object(cold, "Reader", side_effect=reader):
            with self.assertRaisesRegex(io.Refusal, "first_full_cut"):
                with self.operation(fixture) as operation:
                    operation.observe(fixture.package_ref, fixture.provider)
        self.assertIsNone(operation.reader)
        self.assertEqual(len(readers), 1)
        self.assertEqual(readers[0].seen, {})
        self.assertEqual(readers[0]._validated_indexes, set())
        self.assertIsNone(readers[0].read)

    def test_fixed_raw_budget_call_bounds_and_reentry_refuse(self):
        fixture = self.fixture()
        operation = self.operation(fixture)
        with patch.object(native.PackageRequalification, "MAX_BYTES", 1):
            with self.assertRaisesRegex(io.Refusal, "requalification_raw_budget"):
                with operation:
                    operation.observe(fixture.package_ref, fixture.provider)
        self.assertIsNone(operation.reader)
        with self.assertRaisesRegex(io.Refusal, "requalification"):
            operation.__enter__()
        for count in (True, 0, 1, 5):
            with self.assertRaisesRegex(io.Refusal, "requalification_limit"):
                self.operation(fixture, count)

    def test_success_exit_checks_deadline_and_semantics_without_another_native_cut(self):
        fixture = self.fixture()
        for damage in ("deadline", "semantics"):
            with (
                self.subTest(damage=damage),
                ExitStack() as patches,
                patch.object(native, "_mutable_package", wraps=native._mutable_package) as mutable,
                patch.object(
                    fixture.provider, "verify_files", wraps=fixture.provider.verify_files
                ) as files,
            ):
                with self.assertRaises(io.Refusal):
                    with self.operation(fixture) as operation:
                        operation.observe(fixture.package_ref, fixture.provider)
                        sources, reader = operation.sources, operation.reader
                        if damage == "deadline":
                            patches.enter_context(
                                patch.object(
                                    native.time,
                                    "monotonic_ns",
                                    return_value=fixture.binding["deadline_ns"],
                                )
                            )
                        else:
                            operation.value["package"]["binding"]["deadline_ns"] += 1
                self.assertEqual((mutable.call_count, files.call_count), (1, 1))
                self.assert_discarded(operation, sources, reader)

    def test_inherited_fork_is_denied_in_an_isolated_owned_single_thread_process(self):
        source = Path(__file__).resolve().parents[1]
        code = f"""
import os, select, sys, threading
sys.path.insert(0, {str(source)!r})
from scripts import j1_forward_cold_fixture as topology
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io
assert threading.active_count() == 1
with topology.Fixture() as fixture:
    fixture.produce()
    with native.PackageRequalification(fixture.package_ref, provider=fixture.provider,
            required_scope='native_fixture', max_calls=2) as operation:
        operation.observe(fixture.package_ref, fixture.provider)
        incoming, outgoing = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(incoming)
            try:
                operation.observe(fixture.package_ref, fixture.provider)
            except io.Refusal:
                os.write(outgoing, b'DENY')
                os._exit(0)
            os._exit(2)
        os.close(outgoing)
        reaped = False
        try:
            assert select.select([incoming], [], [], 5)[0]
            assert os.read(incoming, 16) == b'DENY'
            status = os.waitpid(pid, 0)[1]
            reaped = True
            assert status == 0
            operation.observe(fixture.package_ref, fixture.provider)
        finally:
            os.close(incoming)
            if not reaped:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
"""
        child = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = child.communicate(timeout=20)
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.communicate(timeout=5)
            raise
        self.assertEqual(child.returncode, 0, (stdout + stderr).decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
