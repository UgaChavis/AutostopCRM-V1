"""Disposable control-graph restores; no services, queues or production data."""

from __future__ import annotations

import copy
import shutil
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import patch

from scripts import j1_forward_archive as archive
from scripts import j1_forward_io as io
from scripts import j1_forward_recovery_state as recovery_state
from scripts import j1_forward_state as state
from tests import test_j1_forward_recovery as recovery_tests


class ControlReferenceRoutingTests(unittest.TestCase):
    def test_cold_authorities_under_production_root_use_the_typed_collector(self):
        root = PurePosixPath("/var/lib/autostop-crm-forward")
        for suffix in (
            "cold/v1/epochs/id/epoch.json",
            "cold/v1/cuts/id/history.json",
            "cold-requests/id/ticket.json",
        ):
            reference = {"path": str(root / suffix), "sha256": "a" * 64}
            with self.subTest(suffix=suffix):
                self.assertFalse(archive._control_reference(root, reference, cold=True))
                self.assertTrue(archive._control_reference(root, reference, cold=False))
                with self.assertRaises(io.Refusal):
                    archive._path(root, reference["path"])

    def test_canonical_store_namespaces_and_external_boundaries_remain_exact(self):
        root = PurePosixPath("/var/lib/autostop-crm-forward")
        for prefix in archive.CONTROL_PREFIXES:
            reference = {"path": str(root / prefix / "proof.json"), "sha256": "a" * 64}
            with self.subTest(prefix=prefix):
                self.assertTrue(archive._control_reference(root, reference, cold=True))
                self.assertEqual(
                    archive._path(root, reference["path"]), root / prefix / "proof.json"
                )
        for name in (
            "/etc/autostop-crm-forward/cold/v1/authority.json",
            str(root) + "-other/state/x",
        ):
            self.assertFalse(archive._control_reference(root, {"path": name}, cold=True))

    def test_external_authority_backlink_closes_the_plain_control_graph(self):
        root = PurePosixPath("/var/lib/autostop-crm-forward")
        history = str(root / "origins/old/plan.json")
        anchor = str(root / "attempts/new/predecessor-anchor.json")
        rows = {history: io.canonical({"historical": True})}
        history_ref = {"path": history, "sha256": io.sha(rows[history])}
        rows[anchor] = io.canonical({"origin": history_ref})
        anchor_ref = {"path": anchor, "sha256": io.sha(rows[anchor])}
        external = {
            "/etc/autostop-crm-forward/cold/v1/authority.json": io.canonical(
                {
                    "predecessor_anchor_ref": anchor_ref,
                    "epoch_ref": {"path": str(root / "cold/v1/epoch.json"), "sha256": "e" * 64},
                }
            )
        }
        files, launches = {}, {}
        with patch.object(
            io, "read_protected", side_effect=lambda path, _limit: rows[path.as_posix()]
        ):
            archive._capture_graph(
                root,
                archive._inward_references(root, external),
                files,
                launches,
                time.monotonic() + 5,
                cold=True,
            )
        self.assertEqual(files, rows)
        self.assertEqual(launches, {})
        changed = {
            "/etc/authority.json": io.canonical({"anchor": anchor_ref | {"sha256": "0" * 64}})
        }
        with self.assertRaisesRegex(io.Refusal, "reference_changed"):
            archive._capture_graph(
                root,
                archive._inward_references(root, changed),
                files,
                launches,
                time.monotonic() + 5,
                cold=True,
            )


class ControlArchiveTests(unittest.TestCase):
    def setUp(self):
        self.origin = recovery_tests.OriginTests()
        self.origin.setUp()
        self.addCleanup(self.origin.doCleanups)
        self.root = self.origin.root
        self.store = self.origin.files.store
        temporary = tempfile.TemporaryDirectory(prefix="j1-control-archive-synthetic-")
        self.addCleanup(temporary.cleanup)
        self.private = Path(temporary.name).resolve()
        self.package, self.restored = self.private / "package", self.private / "restored"
        self.plan = self.origin.files.plan()
        now = datetime.now(UTC)
        self.plan["created_at"] = (now - timedelta(seconds=1)).isoformat()
        self.plan["deadline_utc"] = (now + timedelta(seconds=300)).isoformat()
        self.directory = self.root / "attempts" / self.plan["attempt_id"]
        self.directory.mkdir()
        origin = copy.deepcopy(self.origin.origin)
        for field in ("recovery_code_ref", "ci_ref", "hold_ref"):
            doc = {"technical": field}
            if field == "recovery_code_ref":
                doc.update(
                    source_consumers=self.origin.consumers,
                    consumer_sha256=io.sha(io.canonical(self.origin.consumers)),
                )
            origin[field] = io.write_new(self.directory / (field + ".json"), doc)
        self.plan["candidate"]["consumer_sha256"] = io.sha(io.canonical(self.origin.consumers))
        baseline = {
            **self.origin.baseline,
            "attempt_id": self.plan["attempt_id"],
            "recovery_origin": origin,
        }
        self.plan["baseline_ref"] = io.write_new(self.directory / "baseline.json", baseline)
        self.plan_ref = io.write_new(self.directory / "plan.json", self.plan)
        self.envelope = {
            "schema": recovery_state.RECOVERY_SCHEMA,
            "recovery_id": self.plan["attempt_id"],
            **origin,
            "plan_ref": self.plan_ref,
        }
        self.store.begin_recovery(self.envelope)
        self.closure_ref = io.write_new(
            self.directory / "closure-owned.json", {"attempt_id": self.plan["attempt_id"]}
        )
        for name in ("process-scope.json", "ingress-drain.json"):
            io.write_new(self.directory / name, {"synthetic_evidence": name})
        backup = self.directory / "backup"
        backup.mkdir()
        self.backup_ref = io.write_new(
            backup / "manifest.json", {"synthetic_base_snapshot": self.plan_ref["sha256"]}
        )
        io.write_new(
            self.directory / "backup-evidence.json",
            {
                "manifest": self.backup_ref,
                "manifest_sha256": self.backup_ref["sha256"],
            },
        )
        self.original_pending = self.store.pending_path.read_bytes()

    def capture(self, *, stage="closed", observer=None, output=None, **kwargs):
        return archive.capture(
            self.root,
            self.plan_ref,
            self.backup_ref,
            output or self.package,
            stage=stage,
            observer=observer or (lambda: self.closure_ref),
            **kwargs,
        )

    def restore(self, result, *, stage="closed", output=None, **kwargs):
        return archive.verify_and_restore(
            self.package,
            result["manifest_sha256"],
            output or self.restored,
            plan_ref=self.plan_ref,
            backup_manifest_ref=self.backup_ref,
            stage=stage,
            logical_root=self.root,
            **kwargs,
        )

    def alter(self, change):
        path = self.package / "manifest.json"
        manifest = io.document(path.read_bytes())
        change(manifest)
        path.write_bytes(io.canonical(manifest))
        return {"manifest_sha256": io.sha(path.read_bytes())}

    def terminate(self):
        proofs = {
            name: io.write_new(self.directory / (name + ".json"), {"proof_kind": name})
            for name in (
                "closure",
                "backup",
                "offhost_restore",
                "native_acceptance",
                "runtime_acceptance",
            )
        }
        commit = self.store.commit_recovery(self.envelope, proofs)
        auxiliary = io.write_new(self.directory / "auxiliary-release.json", {"released": "owned"})
        cohort = io.write_new(self.directory / "released-cohort.json", {"cohort": "exact"})
        completion = io.write_new(
            self.directory / "completion.json",
            {
                "ok": True,
                "plan_ref": self.plan_ref,
                "commit": commit,
                "auxiliary_release_ref": auxiliary,
                "released_cohort_ref": cohort,
            },
        )
        self.store.finish_recovery(self.envelope)
        io.write_new(
            self.directory / "progress.json", {"phase": "complete", "completion": completion}
        )
        return proofs

    def test_closed_restores_original_pending_active_recovery_and_pinned_consumers(self):
        result = self.capture()
        receipt = self.restore(result)
        self.assertTrue(receipt["actual_disposable_restore"])
        self.assertTrue(receipt["semantic_graph_verified"])
        self.assertTrue(receipt["semantic"]["pending_present"])
        self.assertTrue(receipt["semantic"]["active_recovery_present"])
        self.assertEqual(self.store.pending_path.read_bytes(), self.original_pending)
        self.assertTrue(all(receipt[key] is False for key in archive.CLAIMS))

    def test_disposable_restore_does_not_read_the_original_control_tree(self):
        result = self.capture()
        shutil.rmtree(self.root)
        receipt = self.restore(result)
        self.assertTrue(receipt["semantic_graph_verified"])
        self.assertFalse(self.root.exists())

    def test_terminal_retains_all_five_proofs_head_originals_and_late_receipts(self):
        proofs = self.terminate()
        result = self.capture(stage="terminal")
        manifest = io.document((self.package / "manifest.json").read_bytes())
        names = {row["logical_name"] for row in manifest["artifacts"]}
        for ref in proofs.values():
            self.assertIn("control/" + Path(ref["path"]).relative_to(self.root).as_posix(), names)
        self.assertTrue(any(name.endswith("/released-cohort.json") for name in names))
        self.assertTrue(any(name.endswith("/auxiliary-release.json") for name in names))
        shutil.rmtree(self.root)
        receipt = self.restore(result, stage="terminal")
        self.assertEqual(receipt["semantic"]["head_sequence"], 1)
        self.assertFalse(receipt["semantic"]["pending_present"])
        self.assertFalse(receipt["semantic"]["active_recovery_present"])

    def test_state_only_copy_without_attempts_and_origins_is_not_semantic_restore(self):
        self.capture()
        result = self.alter(
            lambda doc: doc.update(
                artifacts=[
                    row
                    for row in doc["artifacts"]
                    if row["logical_name"].startswith("control/state/")
                ]
            )
        )
        with self.assertRaises(io.Refusal):
            self.restore(result)

    def test_missing_origin_pending_or_sealed_consumer_refuses_capture(self):
        for name in ("pending.json", "consumers/scripts/j1_forward_release.py"):
            path = self.origin.retained / name
            raw = path.read_bytes()
            path.unlink()
            with self.subTest(name=name), self.assertRaises((io.Refusal, FileNotFoundError)):
                self.capture()
            path.write_bytes(raw)
        self.assertFalse(self.package.exists())

    def test_changed_sealed_source_cannot_be_replaced_with_current_checkout(self):
        path = self.origin.retained / "consumers/scripts/j1_forward_release.py"
        path.write_bytes(b"# changed historical consumer\n")
        with self.assertRaises(io.Refusal):
            self.capture()

    def test_payload_tamper_is_rejected_before_semantic_claim(self):
        result = self.capture()
        (self.package / "000000.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(io.Refusal, "payload_changed"):
            self.restore(result)
        self.assertFalse((self.restored / "restore.json").exists())

    def test_resealed_manifest_cannot_change_plan_or_backup_binding(self):
        self.capture()
        result = self.alter(lambda doc: doc["plan_ref"].update(sha256="f" * 64))
        with self.assertRaisesRegex(io.Refusal, "manifest_binding"):
            self.restore(result)

    def test_resealed_entry_cannot_escape_logical_root(self):
        self.capture()
        result = self.alter(
            lambda doc: doc["artifacts"][0].update(logical_name="control/../../outside.json")
        )
        with self.assertRaisesRegex(io.Refusal, "logical_name"):
            self.restore(result)

    def test_extra_archive_file_or_symlink_is_rejected(self):
        result = self.capture()
        (self.package / "extra.bin").write_bytes(b"unknown")
        with self.assertRaisesRegex(io.Refusal, "exact_inventory"):
            self.restore(result)
        self.assertFalse(self.restored.exists())

    def test_capture_cannot_include_its_own_changing_attempt_tree(self):
        with self.assertRaisesRegex(io.Refusal, "new_external_destination"):
            self.capture(output=self.directory / "archive")

    def test_mutating_history_between_fences_aborts_capture(self):
        calls = 0

        def observer():
            nonlocal calls
            calls += 1
            if calls == 2:
                io.write_new(self.root / "state" / "HEAD.json", {"changed": True})
            return self.closure_ref

        with self.assertRaisesRegex(io.Refusal, "state_changed"):
            self.capture(observer=observer)

    def test_late_origin_drift_is_not_hidden_by_stable_state_files(self):
        calls = 0
        path = self.origin.retained / "pending.json"

        def observer():
            nonlocal calls
            calls += 1
            if calls == 2:
                path.write_bytes(path.read_bytes() + b"\n")
            return self.closure_ref

        with self.assertRaisesRegex(io.Refusal, "source_changed"):
            self.capture(observer=observer)

    def test_final_fence_rechecks_sources_before_manifest_publication(self):
        calls = 0
        path = self.origin.retained / "pending.json"

        def observer():
            nonlocal calls
            calls += 1
            if calls == 3:
                path.write_bytes(path.read_bytes() + b"\n")
            return self.closure_ref

        with self.assertRaisesRegex(io.Refusal, "source_changed"):
            self.capture(observer=observer)
        self.assertFalse((self.package / "manifest.json").exists())

    def test_workstation_restore_uses_supplied_custody_for_all_inputs_and_outputs(self):
        result, checked = self.capture(), []

        def custody(path, *, directory=False):
            checked.append((Path(path), directory))
            if directory:
                self.assertTrue(Path(path).is_dir())
            else:
                self.assertTrue(Path(path).is_file())

        receipt = self.restore(result, custody=custody)
        self.assertTrue(receipt["semantic_graph_verified"])
        self.assertIn((self.package / "manifest.json", False), checked)
        self.assertIn((self.restored / "restore.json", False), checked)
        self.assertIn((self.restored, True), checked)

    def test_logical_linux_paths_do_not_use_the_workstations_windows_path_flavour(self):
        result = self.capture()
        with (
            patch.object(state, "Path", PureWindowsPath),
            patch.object(recovery_state, "Path", PureWindowsPath),
        ):
            receipt = self.restore(result)
        self.assertTrue(receipt["semantic_graph_verified"])

    def test_captured_path_syntax_is_explicit_and_cannot_escape_its_logical_root(self):
        linux = io.logical_path("/var/lib/synthetic-release")
        windows = io.logical_path(r"C:\synthetic-release")
        self.assertIsInstance(linux, PurePosixPath)
        self.assertIsInstance(windows, PureWindowsPath)
        self.assertEqual(
            PureWindowsPath(r"C:\synthetic-release\state\HEAD.json"),
            archive._path(windows, r"C:\synthetic-release\state\HEAD.json"),
        )
        for path in (
            r"D:\synthetic-release\state\HEAD.json",
            r"C:\synthetic-release\..\state\HEAD.json",
        ):
            with self.subTest(path=path), self.assertRaises(io.Refusal):
                archive._path(windows, path)

    def test_file_and_total_limits_are_enforced_before_publication(self):
        for name, value in (("MAX_FILES", 1), ("MAX_TOTAL_BYTES", 1)):
            with self.subTest(limit=name), patch.object(archive, name, value):
                with self.assertRaisesRegex(io.Refusal, "limit"):
                    self.capture()
        self.assertFalse(self.package.exists())

    def test_restore_never_targets_the_logical_production_root(self):
        result = self.capture()
        with self.assertRaisesRegex(io.Refusal, "production_restore_refused"):
            self.restore(result, output=self.root / "restore")

    def test_read_only_store_rejects_all_mutation_entrypoints(self):
        view = archive._RestoredStore(self.root / "state", self.origin.files.base, {})
        for name in (
            "begin",
            "commit",
            "recover",
            "finish",
            "begin_recovery",
            "commit_recovery",
            "recover_recovery",
            "finish_recovery",
        ):
            with self.subTest(operation=name), self.assertRaisesRegex(io.Refusal, "read_only"):
                getattr(view, name)({})

    def test_closed_and_terminal_cannot_be_relabelled(self):
        self.capture()
        result = self.alter(lambda doc: doc.update(stage="terminal"))
        with self.assertRaises(io.Refusal):
            self.restore(result, stage="terminal")

    def test_terminal_requires_late_cohort_evidence(self):
        self.terminate()
        path = self.directory / "released-cohort.json"
        path.unlink()
        with self.assertRaises((io.Refusal, FileNotFoundError)):
            self.capture(stage="terminal")

    def test_flat_transport_roundtrip_restores_a_semantic_graph_independently(self):
        from scripts import j1_forward_transport as transport

        captured = self.capture()
        engine = transport.core()
        envelope = self.private / "control.tar.gz"
        package = self.private / "transported"
        with patch.object(
            engine,
            "protected_parent",
            side_effect=lambda path: engine.physical(path, directory=True),
        ):
            packed = archive.bundle(self.package, captured["manifest_sha256"], envelope)
            archive.unbundle(envelope, packed["archive"]["sha256"], package)
        shutil.rmtree(self.root)
        restored = archive.verify_and_restore(
            package,
            captured["manifest_sha256"],
            self.restored,
            plan_ref=self.plan_ref,
            backup_manifest_ref=self.backup_ref,
            stage="closed",
            logical_root=self.root,
        )
        self.assertTrue(restored["semantic_graph_verified"])
        self.assertFalse(self.root.exists())

    def test_fresh_fence_documents_remain_in_the_independent_proof_graph(self):
        references = []

        def observe():
            reference = io.write_new(
                self.directory / f"fresh-fence-{len(references)}.json",
                {"closure": self.closure_ref},
            )
            references.append(reference)
            return reference

        captured = self.capture(observer=observe)
        manifest = io.document((self.package / "manifest.json").read_bytes())
        names = {entry["logical_name"] for entry in manifest["artifacts"]}
        self.assertGreaterEqual(len(references), 3)
        for reference in references:
            self.assertIn(
                "control/" + Path(reference["path"]).relative_to(self.root).as_posix(), names
            )
        shutil.rmtree(self.root)
        self.assertTrue(self.restore(captured)["semantic_graph_verified"])


if __name__ == "__main__":
    unittest.main()
