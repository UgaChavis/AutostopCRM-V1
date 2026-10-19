"""Pure typed authority-graph restore; all identities and pins are synthetic."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_archive as archive
from scripts import j1_forward_io as io
from scripts import j1_forward_mixed_archive as graph
from scripts import j1_forward_variant as variant
from scripts import j1_manager_successor_guard_v2 as guard
from scripts import j1_manager_successor_native_v2 as native
from scripts import j1_manager_transition_source as source
from tests import test_j1_manager_successor_transition as producer_tests
from tests import test_j1_manager_transition_source as source_tests


class MixedArchiveTests(unittest.TestCase):
    def setUp(self):
        f = self.f = producer_tests.ProducerTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.inventory = {
            name: (Path(__file__).parents[1] / name).read_bytes() for name in variant.SUPPLEMENTAL
        }
        f.controller_files.update({name: io.sha(raw) for name, raw in self.inventory.items()})
        inputs = {"schema": source.INPUT_SCHEMA, "attempt_id": f.plan["attempt_id"]}
        fixture_ci = source_tests.OwnedFiles()
        revision = "c" * 40
        for project, sha in (("manager", guard.REVISION), ("controller", revision)):
            ci = fixture_ci.ci("crm" if project == "controller" else project, sha)
            inputs[project] = {"ci": f.fixture.add("/synthetic/" + project + "-ci.json", ci)}
            result = source.cached_ci(ci, "crm" if project == "controller" else project, sha)
            if project == "manager":
                f.fixture.capsule["candidate"]["ci"] = result
            else:
                f.fixture.capsule["controller"] = {"revision": revision, "ci": result}
        f.fixture.reseal()
        reference = f.fixture.add(str(producer_tests.transition.SOURCE_INPUTS), inputs)
        receipt = io.document(f.fixture.raw[f.transition_ref["path"]])
        receipt.update(
            binding_ref=f.fixture.ref(guard.BINDING),
            source_inputs_ref=reference,
            controller_files=copy.deepcopy(f.controller_files),
        )
        f.plan["manager_transition_ref"] = f.fixture.add(f.transition_ref["path"], receipt)
        f.transition_ref = f.plan["manager_transition_ref"]
        f.plan_ref = f.fixture.add(f.plan_ref["path"], f.plan)
        f.payload["plan_sha256"] = f.plan_ref["sha256"]
        f.fixture.add(guard.BASE, self.inventory["scripts/j1_manager_successor_guard.py"])
        f.fixture.add(str(native.BASE), self.inventory["scripts/j1_manager_successor_native.py"])
        for path in (
            guard.STATE,
            guard.ADMISSION,
            *(guard.STATE_DIR + "/proofs/" + name + ".json" for name in guard.PROOF_FACTS),
        ):
            f.fixture.raw.pop(path, None)

    def referenced(self, ref):
        io.require(ref["path"] in self.f.fixture.raw, "synthetic_reference_missing")
        raw = self.f.fixture.raw[ref["path"]]
        io.require(io.sha(raw) == ref["sha256"], "synthetic_reference_changed")
        return raw

    def collect(self):
        return graph.collect(
            self.f.plan,
            stage="closed",
            controller_inventory=self.f.controller_files,
            read_reference=self.referenced,
            read_file=lambda *_: self.fail("closed graph cannot read mutable armed state"),
        )

    def test_rebuilds_old_inputs_warm_chain_runtime_ci_and_both_base_modules(self):
        files = self.collect()
        self.assertGreater(len(files), 40)
        for path in (
            guard.BINDING,
            guard.CAPSULE,
            guard.BASELINE,
            guard.RUNTIME,
            guard.BASE,
            str(native.BASE),
        ):
            self.assertIn(path, files)
        self.assertTrue(any(path.endswith("/input") for path in files))
        self.assertTrue(any(path.endswith("-ci.json") for path in files))
        self.assertTrue(any("lifecycle" in path or "warm" in path for path in files))
        self.assertNotIn(guard.STATE, files)

    def test_missing_historical_input_or_changed_ci_is_not_a_complete_restore(self):
        files = self.collect()
        missing = next(path for path in files if path.endswith("/input"))
        self.f.fixture.raw.pop(missing)
        with self.assertRaisesRegex(io.Refusal, "synthetic_reference_missing"):
            self.collect()

    def test_changed_sealed_base_or_controller_bytes_are_rejected(self):
        self.f.fixture.raw[guard.BASE] = b"# replacement; never executed\n"
        with self.assertRaisesRegex(io.Refusal, "synthetic_reference_changed"):
            self.collect()
        self.f.fixture.raw[guard.BASE] = self.inventory["scripts/j1_manager_successor_guard.py"]
        with self.assertRaisesRegex(io.Refusal, "mixed_controller_bytes_changed"):
            graph.collect(
                self.f.plan, stage="closed", controller_inventory={}, read_reference=self.referenced
            )

    def test_pure_graph_budget_has_a_finite_file_bound(self):
        with patch.object(graph, "MAX_FILES", 5):
            with self.assertRaisesRegex(io.Refusal, "mixed_archive_limit"):
                self.collect()

    def test_rehashed_ci_input_cannot_change_the_capsules_reviewed_ci_binding(self):
        receipt = io.document(self.f.fixture.raw[self.f.plan["manager_transition_ref"]["path"]])
        inputs = io.document(self.f.fixture.raw[receipt["source_inputs_ref"]["path"]])
        ci = io.document(self.f.fixture.raw[inputs["manager"]["ci"]["path"]])
        ci["run"]["head_branch"] = "another-reviewed-branch"
        inputs["manager"]["ci"] = self.f.fixture.add(inputs["manager"]["ci"]["path"], ci)
        receipt["source_inputs_ref"] = self.f.fixture.add(
            receipt["source_inputs_ref"]["path"], inputs
        )
        self.f.plan["manager_transition_ref"] = self.f.fixture.add(
            self.f.plan["manager_transition_ref"]["path"], receipt
        )
        with self.assertRaisesRegex(io.Refusal, "mixed_archive_ci_binding"):
            self.collect()

    def test_terminal_graph_restores_real_issuer_output_without_executing_it(self):
        self.f.issue()
        result = graph.collect(
            self.f.plan,
            stage="terminal",
            controller_inventory=self.f.controller_files,
            read_reference=self.referenced,
            read_file=self.f.fixture.read,
        )
        self.assertIn(guard.STATE, result)
        self.assertIn(guard.ADMISSION, result)
        self.assertTrue(
            all(
                guard.STATE_DIR + "/proofs/" + name + ".json" in result
                for name in guard.PROOF_FACTS
            )
        )

    def test_terminal_boot_binding_comes_from_the_sealed_predecessor_graph(self):
        self.f.issue()
        state = io.document(self.f.fixture.raw[guard.STATE])
        state["boot_id"] = "00000000-0000-4000-8000-000000000003"
        self.f.fixture.add(guard.STATE, state)
        with self.assertRaisesRegex(ValueError, "state_context"):
            graph.collect(
                self.f.plan,
                stage="terminal",
                controller_inventory=self.f.controller_files,
                read_reference=self.referenced,
                read_file=self.f.fixture.read,
            )

    def test_control_semantic_restore_requires_all_and_only_the_typed_authority_bytes(self):
        authorities = self.collect()
        root = "/var/lib/autostop-crm-forward"
        directory = root + "/attempts/" + self.f.plan["attempt_id"]
        inventory = {**self.inventory, "scripts/j1_forward_release.py": b"# pinned, never run\n"}
        digest = io.sha(io.canonical({name: io.sha(raw) for name, raw in inventory.items()}))
        baseline = {
            "native": {"schema": "synthetic-native"},
            "source_consumers": {name: io.sha(raw) for name, raw in inventory.items()},
        }
        baseline_raw = io.canonical(baseline)
        plan = copy.deepcopy(self.f.plan)
        plan["candidate"]["consumer_sha256"] = digest
        plan["baseline_ref"] = {
            "path": directory + "/baseline.json",
            "sha256": io.sha(baseline_raw),
        }
        raw = io.canonical(plan)
        plan_ref = {"path": directory + "/plan.json", "sha256": io.sha(raw)}
        backup_raw = io.canonical({"fixture": "data backup manifest"})
        backup_ref = {"path": directory + "/backup/manifest.json", "sha256": io.sha(backup_raw)}
        files = {
            plan_ref["path"]: raw,
            plan["baseline_ref"]["path"]: baseline_raw,
            root + "/state/pending.json": raw,
            backup_ref["path"]: backup_raw,
            directory + "/backup-evidence.json": io.canonical(
                {"manifest": backup_ref, "manifest_sha256": backup_ref["sha256"]}
            ),
        }
        result = archive._semantic(
            root, files, {digest: inventory}, plan_ref, backup_ref, "closed", authorities
        )
        self.assertEqual(result["mixed_authority_graph_files"], len(authorities))
        self.assertTrue(result["pending_present"])
        for changed in ({}, {**authorities, "/unrelated/extra.json": b"{}\n"}):
            with self.subTest(keys=len(changed)), self.assertRaises(io.Refusal):
                archive._semantic(
                    root, files, {digest: inventory}, plan_ref, backup_ref, "closed", changed
                )


if __name__ == "__main__":
    unittest.main()
