"""Operation qualification tests; private native evidence is never production admission."""

from __future__ import annotations

import os
import sys
import threading
import unittest
from unittest.mock import patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
)
class PackageObservationTests(unittest.TestCase):
    def fixture(self):
        from scripts.j1_forward_cold_fixture import Fixture

        fixture = Fixture()
        self.addCleanup(fixture.close)
        fixture.produce()
        return fixture

    def observation(self, fixture):
        return native.PackageObservation(
            fixture.package_ref, provider=fixture.provider, required_scope="native_fixture"
        )

    def test_nine_native_observations_share_one_decode_and_two_full_file_measurements(self):
        fixture = self.fixture()
        provider = fixture.provider
        observation = self.observation(fixture)
        with (
            patch.object(cold, "_decode_package", wraps=cold._decode_package) as decode,
            patch.object(provider, "verify_files", wraps=provider.verify_files) as files,
            patch.object(provider, "verify_kernel", wraps=provider.verify_kernel) as kernel,
            patch.object(provider, "verify_pending", wraps=provider.verify_pending) as pending,
            patch.object(
                provider, "verify_original_hold", wraps=provider.verify_original_hold
            ) as hold,
        ):
            with observation:
                for _index in range(9):
                    value = observation.observe(fixture.package_ref, provider)
                    self.assertEqual(value["package"]["binding"], fixture.binding)
                self.assertEqual(decode.call_count, 1)
                self.assertEqual(files.call_count, 1)
            self.assertEqual(decode.call_count, 1)
            self.assertEqual(files.call_count, 2)
            self.assertEqual((kernel.call_count, pending.call_count, hold.call_count), (11, 11, 11))
            with self.observation(fixture) as second:
                second.observe(fixture.package_ref, provider)
            self.assertEqual(decode.call_count, 2)
            self.assertEqual(files.call_count, 4)
        self.assertEqual(list((fixture.root / "probe-effects").iterdir()), [])
        for call in (
            lambda: observation.observe(fixture.package_ref, provider),
            observation.__enter__,
        ):
            with self.assertRaisesRegex(io.Refusal, "package_observation"):
                call()
        self.assertIsNone(observation.reader)

    def test_same_bytes_replacement_and_mutation_revert_keep_the_original_version_cut(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                fixture = self.fixture()
                source = fixture.root / "source/worker.py"
                with self.assertRaisesRegex(io.Refusal, "source_version_changed"):
                    with self.observation(fixture) as observation:
                        observation.observe(fixture.package_ref, fixture.provider)
                        before, raw = source.stat(), source.read_bytes()
                        if replace:
                            temporary = source.with_name("identical.py")
                            temporary.write_bytes(raw)
                            temporary.chmod(before.st_mode)
                            temporary.replace(source)
                        else:
                            source.write_bytes(b"mutated and restored before the next cut\n")
                            source.write_bytes(raw)
                        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
                        observation.observe(fixture.package_ref, fixture.provider)

    def test_exit_full_reread_is_not_replaced_by_a_version_only_success(self):
        fixture = self.fixture()
        source = fixture.root / "source/worker.py"
        original, reads = fixture.provider.read_reference, []

        def read(reference):
            raw = original(reference)
            if reference["path"] == str(source):
                reads.append(reference)
            return raw

        with patch.object(fixture.provider, "read_reference", side_effect=read):
            with self.observation(fixture) as observation:
                before_exit = len(reads)
                observation.observe(fixture.package_ref, fixture.provider)
                self.assertEqual(len(reads), before_exit)
            self.assertGreater(len(reads), before_exit)

    def test_original_dispatch_lock_loss_refuses_the_next_observation(self):
        import fcntl

        fixture = self.fixture()
        with self.assertRaisesRegex(io.Refusal, "hold_lock_not_owned"):
            with self.observation(fixture) as observation:
                observation.observe(fixture.package_ref, fixture.provider)
                fcntl.flock(fixture.dispatch_fd, fcntl.LOCK_UN)
                observation.observe(fixture.package_ref, fixture.provider)

    def test_original_hold_change_refuses_the_next_observation(self):
        import sqlite3

        fixture = self.fixture()
        with self.assertRaises(io.Refusal):
            with self.observation(fixture) as observation:
                observation.observe(fixture.package_ref, fixture.provider)
                with sqlite3.connect(fixture.root / "registry.sqlite3") as connection:
                    connection.execute(
                        "UPDATE manager_automation_controller_runtime SET hold_enabled=0"
                    )
                observation.observe(fixture.package_ref, fixture.provider)

    def test_actual_kernel_exit_is_not_hidden_by_the_saved_ready_receipt(self):
        fixture = self.fixture()
        with self.assertRaises((io.Refusal, FileNotFoundError, ProcessLookupError)):
            with self.observation(fixture) as observation:
                value = observation.observe(fixture.package_ref, fixture.provider)
                actor = value["kernel"]["actor"]
                child = next(row for row in fixture.children if row.pid == actor["pid"])
                fixture._retire(child, actor)
                observation.observe(fixture.package_ref, fixture.provider)

    def test_failure_cannot_be_caught_then_reused_and_observation_limit_is_fixed(self):
        fixture = self.fixture()
        with self.assertRaisesRegex(io.Refusal, "package_observation_not_active"):
            with self.observation(fixture) as observation:
                for _index in range(9):
                    observation.observe(fixture.package_ref, fixture.provider)
                with self.assertRaisesRegex(io.Refusal, "package_observation_limit"):
                    observation.observe(fixture.package_ref, fixture.provider)
                with self.assertRaisesRegex(io.Refusal, "package_observation_not_active"):
                    observation.observe(fixture.package_ref, fixture.provider)

    def test_provider_reference_thread_and_declared_semantics_are_bound(self):
        fixture = self.fixture()
        for damage in ("reference", "provider", "thread", "semantics"):
            with self.subTest(damage=damage):
                observation = self.observation(fixture)
                with self.assertRaisesRegex(io.Refusal, "package_observation"):
                    with observation:
                        if damage == "semantics":
                            observation.value["package"]["binding"] = {
                                **fixture.binding,
                                "deadline_ns": fixture.binding["deadline_ns"] + 1,
                            }
                        if damage == "thread":
                            failures = []

                            def other_thread():
                                try:
                                    observation.observe(fixture.package_ref, fixture.provider)
                                except io.Refusal as error:
                                    failures.append(str(error))

                            thread = threading.Thread(target=other_thread)
                            thread.start()
                            thread.join(timeout=5)
                            self.assertFalse(thread.is_alive())
                            self.assertEqual(len(failures), 1)
                        else:
                            observation.observe(
                                {**fixture.package_ref, "sha256": "f" * 64}
                                if damage == "reference"
                                else fixture.package_ref,
                                object() if damage == "provider" else fixture.provider,
                            )

    def test_deadline_after_entry_refuses_without_extending_the_original_binding(self):
        fixture = self.fixture()
        with self.assertRaisesRegex(io.Refusal, "binding_expired_or_window"):
            with self.observation(fixture) as observation:
                with patch.object(
                    native.time, "monotonic_ns", return_value=fixture.binding["deadline_ns"]
                ):
                    observation.observe(fixture.package_ref, fixture.provider)

    def test_ready_cannot_return_a_result_when_its_full_exit_cut_fails(self):
        fixture = self.fixture()
        controller = object.__new__(lifecycle.Controller)
        controller.package_ref, controller.provider = fixture.package_ref, fixture.provider

        def changed(_observation):
            source = fixture.root / "source/worker.py"
            raw = source.read_bytes()
            source.write_bytes(raw + b"\n")
            source.write_bytes(raw)
            return {"ok": True, "synthetic_body_only": True}

        # This isolates the ready wrapper, not six application acceptances.
        with patch.object(controller, "_ready", side_effect=changed):
            with self.assertRaisesRegex(io.Refusal, "source_version_changed"):
                controller.ready()

    def test_ready_checks_current_epoch_after_the_final_native_exit(self):
        fixture = self.fixture()
        controller = object.__new__(lifecycle.Controller)
        controller.package_ref, controller.provider = fixture.package_ref, fixture.provider
        order = []
        measure = fixture.provider.verify_files

        def full(value):
            order.append("full_files")
            return measure(value)

        def body(observation):
            observation.observe(fixture.package_ref, fixture.provider)
            order.append("body")
            return {"synthetic_body_only": True}

        def epoch(_value):
            order.append("epoch")
            raise io.Refusal("synthetic_epoch_changed_after_native_exit")

        with (
            patch.object(fixture.provider, "verify_files", side_effect=full),
            patch.object(controller, "_ready", side_effect=body),
            patch.object(controller, "_verify_epoch", side_effect=epoch),
            self.assertRaisesRegex(io.Refusal, "epoch_changed_after_native_exit"),
        ):
            controller.ready()
        self.assertEqual(order, ["full_files", "body", "full_files", "epoch"])
