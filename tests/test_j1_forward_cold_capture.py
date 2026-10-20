from __future__ import annotations

import copy
import os
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_backup as backup
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal


class RestoredIntervalTests(unittest.TestCase):
    def test_boundary_emission_refuses_at_the_exact_package_deadline(self):
        interval = object.__new__(backup.CaptureInterval)
        interval.value = {
            "package": {
                "binding": {
                    **dict.fromkeys(("attempt_id", "boot_id", "generation"), str(uuid.uuid4())),
                    "consumer_sha256": "1" * 64,
                    "candidate_crm_sha": "2" * 40,
                    "candidate_manager_sha": "3" * 40,
                    "deployment_lock_identity": [1, 2],
                    "not_before_ns": 1,
                    "deadline_ns": 100,
                }
            }
        }
        with patch.object(backup.time, "monotonic_ns", return_value=100):
            with self.assertRaisesRegex(io.Refusal, "binding_expired_or_window"):
                interval._common()

    def test_all_six_cold_native_unit_roots_keep_the_existing_file_classifier(self):
        previous = backup.original.profile_document()
        profile = backup.engine("1" * 40, str(uuid.uuid4()), str(uuid.uuid4()))
        units = [(name, path) for name, path in profile.ROOTS if name.startswith("unit_cold_")]
        self.assertEqual(len(units), 6)
        self.assertTrue(
            all(profile.expected_root_kind(name, path) == "file" for name, path in units)
        )
        self.assertEqual(backup.original.profile_document(), previous)

    def fixture(self, damage=lambda _stage, _value: None):
        files = {}

        def save(name, document):
            damage(name, document)
            raw = io.canonical(document)
            path = "/private/" + name + ".json"
            files[path] = raw
            return {"path": path, "sha256": io.sha(raw)}

        binding = {
            **dict.fromkeys(("attempt_id", "boot_id", "generation"), str(uuid.uuid4())),
            "consumer_sha256": "1" * 64,
            "candidate_crm_sha": "2" * 40,
            "candidate_manager_sha": "3" * 40,
            "deployment_lock_identity": [1, 2],
            "not_before_ns": 1,
            "deadline_ns": 100,
        }
        package_ref, epoch_ref, plan_ref = [
            {"path": "/private/" + name + ".json", "sha256": str(index) * 64}
            for index, name in enumerate(("package", "epoch", "plan"), 1)
        ]
        package = {
            "scope": "native_fixture",
            "binding": binding,
            "policies": dict.fromkeys(cold.ALLOWED, package_ref),
        }
        controller = SimpleNamespace(
            provider=SimpleNamespace(scope="native_fixture"),
            package_ref=package_ref,
            epoch_ref=epoch_ref,
        )
        with patch.object(backup.time, "monotonic_ns", return_value=9):
            native_body = backup.full_native(
                {"package": package, "history": {"inventory": {}}}, controller
            )
        common = {
            key: native_body[key] for key in backup.COMMON - {"plan_ref", "observed_monotonic_ns"}
        }
        common.update(plan_ref=plan_ref, observed_monotonic_ns=10)
        native_body["observed_monotonic_ns"] = 9
        entry_native = save("entry-native", native_body)
        entry = {
            "schema": backup.ENTRY,
            **common,
            "native_ref": entry_native,
            "source_cut_sha256": "f" * 64,
        }
        entry_ref = save("entry", entry)
        witnesses = []
        for index in range(4):
            boundary = {
                "schema": backup.BOUNDARY,
                **common,
                "observed_monotonic_ns": 11 + index,
                "entry_ref": entry_ref,
                "index": index,
                "source_cut_sha256": "f" * 64,
                "original_barriers_closed": True,
                "original_hold_retained": True,
                "old_writer_allowed": False,
                "candidate_running": False,
            }
            reference = save("boundary-" + str(index), boundary)
            witnesses.append({"attempt_id": binding["attempt_id"], **reference})
        manifest = {"artifacts": [{}], "external_closure_observations": witnesses}
        damage("manifest", manifest)
        native_body["observed_monotonic_ns"] = 15
        exit_native = save("exit-native", native_body)
        completion = {
            "schema": backup.COMPLETE,
            **common,
            "observed_monotonic_ns": 16,
            "entry_ref": entry_ref,
            "native_ref": exit_native,
            "source_cut_sha256": "f" * 64,
            "boundary_count": len(witnesses),
            "witnesses_sha256": cold.digest(witnesses),
        }
        completion_ref = save("completion", completion)
        response = {
            "plan_ref": plan_ref,
            "plan": {
                "attempt_id": binding["attempt_id"],
                "cold_authority_package_ref": package_ref,
                "dispatch_epoch_ref": epoch_ref,
            },
            "evidence": {
                "package": package,
                "epoch": {"scope": "native_fixture", "boot_id": binding["boot_id"]},
                "backup_capture": {"capture_interval_ref": completion_ref},
            },
        }
        return manifest, response, lambda ref: files[ref["path"]]

    def test_both_full_native_ends_and_every_ordered_boundary_are_required(self):
        result = backup.validate_restored_interval(*self.fixture())
        self.assertEqual(result["boundary_count"], 4)
        self.assertFalse(result["native_acceptance"])

    def test_hash_valid_incomplete_stale_weak_or_reordered_chains_deny(self):
        cases = (
            ("entry-native", lambda value: value.update(candidate_running=True)),
            ("exit-native", lambda value: value.update(observed_monotonic_ns=12)),
            ("exit-native", lambda value: value.update(retired_scope_sha256="9" * 64)),
            ("boundary-2", lambda value: value.update(index=True)),
            ("boundary-2", lambda value: value.update(index=3)),
            ("boundary-2", lambda value: value.update(source_cut_sha256="9" * 64)),
            ("boundary-2", lambda value: value.update(original_hold_retained=False)),
            ("boundary-2", lambda value: value.update(boot_id=str(uuid.uuid4()))),
            ("boundary-2", lambda value: value.update(observed_monotonic_ns=99)),
            ("manifest", lambda value: value["external_closure_observations"].reverse()),
            ("manifest", lambda value: value["external_closure_observations"].pop()),
            (
                "manifest",
                lambda value: value["external_closure_observations"].__setitem__(
                    2, copy.deepcopy(value["external_closure_observations"][1])
                ),
            ),
            ("completion", lambda value: value.update(boundary_count=True)),
            ("completion", lambda value: value.update(witnesses_sha256="9" * 64)),
            ("completion", lambda value: value.update(source_cut_sha256="9" * 64)),
        )
        for stage, mutate in cases:
            with self.subTest(stage=stage, mutate=mutate):

                def damage(name, value):
                    if name == stage:
                        mutate(value)

                with self.assertRaises(io.Refusal):
                    backup.validate_restored_interval(*self.fixture(damage))


@unittest.skipUnless(os.name == "posix", "Requires real Linux flock, AF_UNIX and SO_PEERCRED")
class NativeCaptureIntervalTests(unittest.TestCase):
    def fixture(self):
        from scripts.j1_forward_cold_fixture import Fixture

        fixture = Fixture(candidates=True)
        self.addCleanup(fixture.close)
        self.enterContext(rehearsal.PrivateFilesystem(fixture.root))
        fixture.produce()
        epoch_ref = lifecycle.provision_epoch(
            fixture.package_ref, fixture.runtime_bindings, provider=fixture.provider
        )
        controller = lifecycle.Controller(
            fixture.package_ref,
            epoch_ref,
            provider=fixture.provider,
            backend=lifecycle.FixtureBackend(fixture),
        )
        controller.stage()
        directory = fixture.root / "capture"
        directory.mkdir(mode=0o700)
        plan_ref = fixture.publish(
            "capture/plan.json",
            {
                "attempt_id": fixture.binding["attempt_id"],
                "cold_authority_package_ref": fixture.package_ref,
                "dispatch_epoch_ref": epoch_ref,
            },
        )
        return fixture, controller, plan_ref, directory

    def test_real_held_cohort_and_kernel_are_remeasured_without_per_boundary_decoding(self):
        fixture, controller, plan_ref, directory = self.fixture()
        began = time.monotonic()
        with (
            patch.object(cold, "_decode_package", wraps=cold._decode_package) as decode,
            patch.object(
                fixture.provider, "verify_files", wraps=fixture.provider.verify_files
            ) as files,
            patch.object(
                lifecycle.native, "_mutable_package", wraps=lifecycle.native._mutable_package
            ) as mutable,
            backup.CaptureInterval(controller, plan_ref, directory, gate=lambda: None) as interval,
        ):
            self.assertEqual(decode.call_count, 1)
            for _index in range(4):
                interval.observe()
            self.assertEqual(decode.call_count, 1)
        self.assertEqual(decode.call_count, 1)
        self.assertEqual(files.call_count, 2)
        self.assertEqual(mutable.call_count, 2)
        self.assertIsNotNone(interval.completion_ref)
        self.assertEqual(list((fixture.root / "probe-effects").iterdir()), [])
        response = {
            "plan_ref": plan_ref,
            "plan": io.document(fixture.provider.read_reference(plan_ref)),
            "evidence": {
                "package": interval.value["package"],
                "epoch": controller.epoch,
                "backup_capture": {"capture_interval_ref": interval.completion_ref},
            },
        }
        result = backup.validate_restored_interval(
            {"artifacts": [{}], "external_closure_observations": interval.witnesses},
            response,
            fixture.provider.read_reference,
        )
        self.assertEqual(result["boundary_count"], 4)
        self.assertLess(time.monotonic() - began, 120)

    def test_source_replacement_during_interval_refuses_before_next_receipt(self):
        fixture, controller, plan_ref, directory = self.fixture()
        with self.assertRaisesRegex(io.Refusal, "source_version_changed"):
            with backup.CaptureInterval(
                controller, plan_ref, directory, gate=lambda: None
            ) as interval:
                interval.observe()
                source = fixture.root / "source/worker.py"
                replacement = source.with_name("same-bytes.py")
                replacement.write_bytes(source.read_bytes())
                replacement.chmod(source.stat().st_mode)
                replacement.replace(source)
                interval.observe()
        self.assertIsNone(interval.completion_ref)
        self.assertEqual(len(interval.witnesses), 1)

    def test_entry_gate_refusal_closes_interval_without_publishing_completion(self):
        _fixture, controller, plan_ref, directory = self.fixture()

        def refuse():
            raise io.Refusal("private_capture_gate_refusal")

        interval = backup.CaptureInterval(controller, plan_ref, directory, gate=refuse)
        with self.assertRaisesRegex(io.Refusal, "private_capture_gate_refusal"):
            with interval:
                self.fail("An entry refusal cannot expose an active interval")
        self.assertEqual(interval.status, "closed")
        self.assertIsNone(interval._qualification)
        self.assertIsNone(interval.kernel)
        self.assertIsNone(interval.completion_ref)
        self.assertFalse((directory / "cold-capture-completed.json").exists())

    def test_lost_hold_after_last_boundary_refuses_completion_and_closes_handles(self):
        import fcntl

        fixture, controller, plan_ref, directory = self.fixture()
        interval = backup.CaptureInterval(controller, plan_ref, directory, gate=lambda: None)
        with self.assertRaisesRegex(io.Refusal, "hold_lock_not_owned"):
            with interval:
                interval.observe()
                interval.observe()
                fcntl.flock(fixture.dispatch_fd, fcntl.LOCK_UN)
        self.assertEqual(interval.status, "closed")
        self.assertIsNone(interval._qualification)
        self.assertEqual(interval.kernel.descriptors, [])
        self.assertIsNone(interval.completion_ref)
        self.assertFalse((directory / "cold-capture-completed.json").exists())

    def test_lost_original_descriptor_or_hold_refuses_a_new_receipt(self):
        import fcntl

        fixture, controller, plan_ref, directory = self.fixture()
        with self.assertRaisesRegex(io.Refusal, "hold_lock_not_owned"):
            with backup.CaptureInterval(
                controller, plan_ref, directory, gate=lambda: None
            ) as interval:
                interval.observe()
                fcntl.flock(fixture.dispatch_fd, fcntl.LOCK_UN)
                interval.observe()
        self.assertIsNone(interval.completion_ref)
        self.assertEqual(len(interval.witnesses), 1)

    def test_kernel_exit_during_interval_is_not_hidden_by_its_ready_file(self):
        fixture, controller, plan_ref, directory = self.fixture()
        with self.assertRaisesRegex(io.Refusal, "capture_kernel_exited"):
            with backup.CaptureInterval(
                controller, plan_ref, directory, gate=lambda: None
            ) as interval:
                interval.observe()
                expected = interval.value["kernel"]["actor"]
                process = next(child for child in fixture.children if child.pid == expected["pid"])
                fixture._retire(process, expected)
                interval.observe()
        self.assertIsNone(interval.completion_ref)
        self.assertEqual(len(interval.witnesses), 1)

    def test_original_sql_hold_change_during_interval_refuses_a_new_receipt(self):
        import sqlite3

        fixture, controller, plan_ref, directory = self.fixture()
        with self.assertRaises(io.Refusal):
            with backup.CaptureInterval(
                controller, plan_ref, directory, gate=lambda: None
            ) as interval:
                interval.observe()
                with sqlite3.connect(fixture.root / "registry.sqlite3") as connection:
                    connection.execute(
                        "UPDATE manager_automation_controller_runtime SET hold_enabled=0"
                    )
                interval.observe()
        self.assertIsNone(interval.completion_ref)
        self.assertEqual(len(interval.witnesses), 1)
