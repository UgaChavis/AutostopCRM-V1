from __future__ import annotations

import contextlib
import copy
import hashlib
import io as text_io
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import cold_trace
from scripts import cold_v2_rehearsal as typed
from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_backup as backup
from scripts import j1_forward_cold_fixture as topology
from scripts import j1_forward_cold_kernel as kernel
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal
from scripts.cold_v2_rehearsal import TypedFixture
from tests import test_j1_forward_cold_kernel as preparation


class PendingVersionTests(unittest.TestCase):
    def read(self, scope, *, version=2, damage=lambda value: None):
        documents = {}

        def save(name, value):
            raw = io.canonical(value)
            documents["/private/" + name] = raw
            return {"path": "/private/" + name, "sha256": io.sha(raw)}

        inventory = save("inventory.json", {"private": True})
        quarantine = save("quarantine.json", {"private": "closed"})
        binding = {"explicit": "test-only binding"}
        row = {
            "op_id": "legacy/raw-id:never-normalized",
            "outcome": "unknown",
            "evidence_refs": [inventory],
            "retry": "forbidden",
        }
        row.update(
            {"fingerprint": {"kind": "observed_record_sha256", "sha256": "1" * 64}}
            if version == 2
            else {"payload_sha256": "1" * 64}
        )
        body = {
            "schema": "autostop.j1.cold-pending-disposition.v" + str(version),
            "scope": scope,
            "binding": binding,
            "quarantine_ref": quarantine,
            "inventory_ref": inventory,
            "requests": [row],
        }
        damage(body)
        reference = save("pending.json", body)
        reader = cold.Reader(lambda ref: documents[ref["path"]])
        result = cold._pending(
            reader, {"pending_ref": reference, "quarantine_ref": quarantine}, binding, scope
        )
        reader.recheck()
        return result

    def test_explicit_fixture_v2_preserves_fingerprint_and_raw_identity(self):
        for scope in ("native_fixture", "production_native"):
            with self.subTest(scope=scope):
                value = self.read(scope)
                row = value["requests"][0]
                self.assertNotIn("payload_sha256", row)
                self.assertEqual(row["op_id"], "legacy/raw-id:never-normalized")
                self.assertEqual(row["fingerprint"]["kind"], "observed_record_sha256")

    def test_legacy_fixture_remains_readable_and_production_cannot_fall_back(self):
        self.assertIn("payload_sha256", self.read("native_fixture", version=1)["requests"][0])
        with self.assertRaisesRegex(io.Refusal, "production_pending_v2_required"):
            self.read("production_native", version=1)

    def test_observed_record_is_never_positive_and_mixed_wire_fields_deny(self):
        for scope in ("native_fixture", "production_native"):
            for outcome in ("not_applied_terminal", "applied_readback"):
                with self.subTest(scope=scope, outcome=outcome):
                    with self.assertRaisesRegex(io.Refusal, "not_positive_outcome"):
                        self.read(
                            scope, damage=lambda value: value["requests"][0].update(outcome=outcome)
                        )
            with self.assertRaisesRegex(io.Refusal, "pending_request_fields"):
                self.read(
                    scope, damage=lambda value: value["requests"][0].update(payload_sha256="2" * 64)
                )


class CandidateBootstrapTests(unittest.TestCase):
    def test_fixture_bootstrap_never_accepts_a_production_or_caller_provider(self):
        with self.assertRaisesRegex(io.Refusal, "fixture_candidate_provider"):
            topology.candidate_inspection(
                SimpleNamespace(scope="production_native"), "any", "0" * 64
            )

    def test_binding_readback_requires_the_exact_entered_process_and_current_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "worker.py"
            source.write_bytes(b"pinned-private-source")
            source_ref = {"path": str(source), "sha256": io.sha(source.read_bytes())}
            documents = {}

            def save(path, value):
                raw = io.canonical(value)
                documents[str(path)] = raw
                return {"path": str(path), "sha256": io.sha(raw)}

            unit = cold.ALLOWED[0]
            index = save(root / "index.json", {"files": [source_ref]})
            policy = save(root / "policy.json", {"source_index_ref": index})
            package = {"scope": "native_fixture", "binding": {}, "policies": {unit: policy}}
            save(root / "authority.json", package)
            save(root / "execution/epoch.json", {})
            provider = object.__new__(topology.FixtureProvider)
            provider.root = root
            provider.reference = lambda path: {
                "path": str(path),
                "sha256": io.sha(documents[str(path)]),
            }
            provider.read_reference = lambda ref: (
                source.read_bytes() if ref == source_ref else documents[ref["path"]]
            )
            accepted = {"pid": os.getpid(), "start_ticks": 12}
            state = {"execution_possible": True, "claim": accepted, "acceptance": accepted}
            controller = SimpleNamespace(
                admitted=lambda: None,
                context=lambda _unit: (None, {"state": state}, {"policy_ref": policy}),
            )
            with (
                patch.object(lifecycle, "Controller", return_value=controller),
                patch.object(cold, "_binding"),
                patch.object(topology.native, "birth", return_value=12),
                patch.object(
                    topology.native,
                    "verify_package",
                    side_effect=AssertionError("second qualification"),
                ),
                patch.object(lifecycle, "_observe_process", return_value=accepted),
            ):
                receipt = topology.candidate_inspection(provider, unit, cold.digest({}))
                self.assertEqual(receipt["policy_ref"], policy)
                for mutation in (
                    {"execution_possible": False},
                    {"claim": {"pid": os.getpid() + 1, "start_ticks": 12}},
                ):
                    original = dict(state)
                    state.update(mutation)
                    with self.assertRaisesRegex(io.Refusal, "fixture_candidate_not_entered"):
                        topology.candidate_inspection(provider, unit, cold.digest({}))
                    state.clear()
                    state.update(original)
                original_read = provider.read_reference

                def replace_after_read(reference):
                    raw = original_read(reference)
                    if reference == source_ref:
                        source.write_bytes(b"replaced-during-binding-readback")
                    return raw

                provider.read_reference = replace_after_read
                with self.assertRaisesRegex(io.Refusal, "fixture_candidate_source_changed"):
                    topology.candidate_inspection(provider, unit, cold.digest({}))


class FixtureBoundaryTests(unittest.TestCase):
    def test_guard_diagnostics_never_read_a_live_child_or_emit_raw_stderr(self):
        for exit_code, raw, expected in (
            (None, b"Refusal: cold_example\n", None),
            (
                2,
                b"Refusal: root_protected_directory_required\n",
                "root_protected_directory_required",
            ),
            (2, b"arbitrary private output\n", None),
            (2, b"Refusal: cold_example\nPRIVATE\n", None),
            (2, b"x" * 4097, None),
        ):
            with self.subTest(exit_code=exit_code, raw_length=len(raw)):
                child = SimpleNamespace(
                    poll=lambda: exit_code, stderr=SimpleNamespace(fileno=lambda: 9)
                )
                backend = SimpleNamespace(started={"owned.service": (child, "private")})
                with (
                    patch.object(typed.os, "get_blocking", return_value=True),
                    patch.object(typed.os, "set_blocking"),
                    patch.object(typed.os, "read", return_value=raw) as read,
                ):
                    status = typed.private_guard_status(backend, "owned.service")
                self.assertEqual(status["stderr_code"], expected)
                if exit_code is None:
                    read.assert_not_called()
                else:
                    read.assert_called_once_with(9, 4097)

    def test_non_bytecode_mode_refuses_before_process_or_filesystem_effects(self):
        with (
            patch.object(sys, "dont_write_bytecode", False),
            patch.object(topology.Fixture, "_prepare") as prepare,
        ):
            with self.assertRaisesRegex(io.Refusal, "requires_no_bytecode_writes"):
                TypedFixture()
            prepare.assert_not_called()

    @unittest.skipUnless(
        os.name == "posix", "Release checks require POSIX ownership and permissions"
    )
    def test_artificial_auth_fixture_does_not_remove_production_auth_material_gate(self):
        fixture = preparation.KernelPreparationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        value = io.document(fixture.provider.read_reference(fixture.home_ref))
        value["prestart_files"] = {}
        kernel._fresh_home(value, fixture.binding, "native_fixture")
        production = copy.deepcopy(value)
        production.update(
            schema="autostop.j1.cold-kernel-home.v2",
            scope="production_native",
            maintenance_lock=kernel.create_maintenance_lock(fixture.codex),
            prestart_files={kernel.MAINTENANCE_LOCK: kernel.EMPTY_SHA256},
        )
        with self.assertRaisesRegex(io.Refusal, "fresh_home_manifest"):
            kernel._fresh_home(production, fixture.binding, "production_native")

    @unittest.skipUnless(
        os.name == "posix", "Release checks require POSIX ownership and permissions"
    )
    def test_drift_diagnostic_preserves_actual_capture_refusal_and_source_version_check(self):
        attempt, generation = str(uuid.uuid4()), str(uuid.uuid4())
        engine = backup.engine("2" * 40, generation, attempt)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir(mode=0o700)
            leaf = source / "private.fixture"
            leaf.write_bytes(b"first private bytes")
            fence = root / "fence.json"
            fence.write_bytes(b"{}\n")
            count = 0

            def observe():
                nonlocal count
                count += 1
                if count == 2:
                    leaf.write_bytes(b"changed private bytes")
                return {
                    "attempt_id": attempt,
                    "path": str(fence),
                    "sha256": io.sha(fence.read_bytes()),
                }

            fixture = SimpleNamespace(root=root, provider=SimpleNamespace(scope="native_fixture"))
            before = engine.build_profile
            output = text_io.StringIO()
            with (
                patch.object(engine, "ROOTS", (("fixture", str(source)),)),
                patch.object(engine, "OPTIONAL_ROOTS", ()),
                patch.object(engine, "SELECTORS", ()),
                rehearsal.PrivateFilesystem(root, engines=(engine,)),
                contextlib.redirect_stdout(output),
            ):
                context = {
                    "attempt_id": attempt,
                    "predecessor_sha256": "0" * 64,
                    "candidate_crm_sha": "1" * 40,
                    "candidate_manager_sha": "2" * 40,
                    "scope_profile_sha256": engine.profile_sha256(),
                }
                with self.assertRaisesRegex(engine.BackupRefusal, "inventory_changed"):
                    cold_trace._capture_data(
                        fixture, engine, root / "backup", context, observe, timeout=10
                    )
            self.assertIs(engine.build_profile, before)
            self.assertIn('"logical_name":"private.fixture"', output.getvalue())
            self.assertNotIn("changed private bytes", output.getvalue())
            self.assertFalse((root / "backup/manifest.json").exists())


@unittest.skipUnless(os.name == "posix", "Release checks require POSIX ownership and permissions")
class VoiceModelDataTests(unittest.TestCase):
    def data(self, root):
        source = root / "inputs"
        source.mkdir(mode=0o700)
        pins = {}
        for name in typed.MODEL_FILES:
            raw = b'{"synthetic":true}' if name.endswith(".json") else b"synthetic-data-only"
            (source / name).write_bytes(raw)
            pins[name] = len(raw), hashlib.sha256(raw).hexdigest()
        for role in typed.MODEL_ROLES:
            (root / "execution/roles" / role / "state").mkdir(parents=True, mode=0o700)
        fixture = SimpleNamespace(root=root, custody=rehearsal.PrivateFilesystem(root))
        return fixture, source, pins

    def test_both_physical_copies_and_all_three_logical_occurrences_restore_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, source, pins = self.data(root)
            attempt, generation = str(uuid.uuid4()), str(uuid.uuid4())
            engine = backup.engine("2" * 40, generation, attempt)
            roles = str(root / "execution/roles")
            roots = (
                ("cold_config", roles),
                ("cold_state", roles),
                ("cold_controller_state", str(root / "execution")),
            )
            with (
                patch.object(typed, "MODEL_FILES", pins),
                patch.object(engine, "ROOTS", roots),
                patch.object(engine, "OPTIONAL_ROOTS", ()),
                patch.object(engine, "SELECTORS", ()),
                rehearsal.PrivateFilesystem(root, engines=(engine,)),
            ):
                entries = typed.stage_voice_models(fixture, source)
                self.assertEqual(len(entries), 8)
                self.assertEqual(len({Path(row["source"]).stat().st_ino for row in entries}), 8)
                context = {
                    "attempt_id": attempt,
                    "predecessor_sha256": "0" * 64,
                    "candidate_crm_sha": "1" * 40,
                    "candidate_manager_sha": "2" * 40,
                    "scope_profile_sha256": engine.profile_sha256(),
                }
                fence = {
                    "attempt_id": attempt,
                    "path": str(root / "fixture-fence"),
                    "sha256": "3" * 64,
                }
                captured = engine.capture(root / "backup", context, lambda: fence, timeout=10)
                manifest = io.document(io.referenced(captured["manifest"]))
                for name in ("first", "second"):
                    restored = root / name
                    engine.verify_and_restore(
                        root / "backup", captured["manifest_sha256"], restored, context, timeout=10
                    )
                    result = typed.verify_restored_models(
                        entries, manifest, restored, deadline=typed.time.monotonic() + 5
                    )
                    self.assertEqual(result["logical_model_payload_files"], 24)
                    self.assertEqual(
                        result["logical_model_payload_bytes"], 3 * result["physical_model_bytes"]
                    )
                    self.assertIs(result["all_payload_hashes_verified"], True)
                bad = copy.deepcopy(manifest)
                del bad["artifacts"][
                    next(
                        index
                        for index, row in enumerate(bad["artifacts"])
                        if row["source"] == entries[0]["source"]
                    )
                ]
                with self.assertRaisesRegex(io.Refusal, "fixture_model_backup_coverage"):
                    typed.verify_restored_models(
                        entries, bad, root / "first", deadline=typed.time.monotonic() + 5
                    )
                row = next(
                    row for row in manifest["artifacts"] if row["source"] == entries[0]["source"]
                )
                (root / "second" / row["payload"]).write_bytes(b"changed")
                with self.assertRaises(io.Refusal):
                    typed.verify_restored_models(
                        entries, manifest, root / "second", deadline=typed.time.monotonic() + 5
                    )

    def test_wrong_input_bytes_or_extra_inventory_refuses_before_role_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, source, pins = self.data(root)
            with patch.object(typed, "MODEL_FILES", pins):
                (source / "model.bin").write_bytes(b"changed bytes")
                with self.assertRaises(io.Refusal):
                    typed.stage_voice_models(fixture, source)
                self.assertFalse((root / "execution/roles/work-telegram/state/models").exists())
                (source / "extra").write_bytes(b"unapproved")
                with self.assertRaisesRegex(io.Refusal, "fixture_model_inventory"):
                    typed.stage_voice_models(fixture, source)


if __name__ == "__main__":
    unittest.main()
