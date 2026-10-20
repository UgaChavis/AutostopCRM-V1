"""Exact cold verifier source pins; no installed paths or native effects."""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_archive as archive
from scripts import j1_forward_io as io
from scripts import j1_forward_launch_native as native
from scripts import j1_forward_release as release
from scripts import j1_forward_release_rehearsal as rehearsal
from scripts import j1_forward_variant as variant
from scripts import j1_forward_worker as worker
from tests import test_j1_forward_archive as archive_tests
from tests import test_j1_forward_launch_native as launch_tests

HELPER = "scripts/j1_manager_transition_source.py"
COLD_PLAN = {"schema": "autostop.j1.crm-manager-cold-forward-plan.v1"}


class SourceInventoryTests(unittest.TestCase):
    def test_cold_inventory_includes_only_the_required_additional_helper(self):
        source = Path(release.__file__).resolve().parents[1]
        names = sorted(
            str(path.relative_to(source)) for path in (source / "scripts").glob("j1_forward_*.py")
        )
        result = subprocess.CompletedProcess([], 0, ("\n".join(names) + "\n").encode())
        with (
            patch.object(release, "CRM", source),
            patch.object(release.subprocess, "run", return_value=result),
        ):
            warm = release.source_consumers()
            cold = release.source_consumers(cold=True)
            mixed = release.source_consumers(mixed=True)
        self.assertEqual(set(cold) - set(warm), {HELPER})
        self.assertEqual(set(mixed) - set(warm), variant.SUPPLEMENTAL)
        self.assertEqual(cold[HELPER], io.sha((source / HELPER).read_bytes()))
        self.assertEqual(native.COLD_SUPPLEMENTAL, variant.COLD_SUPPLEMENTAL)
        self.assertEqual(rehearsal.source_inventory()[HELPER], (source / HELPER).read_bytes())

    def test_custodian_refuses_missing_or_changed_cold_helper(self):
        inventory = rehearsal.source_inventory()
        baseline = {"source_consumers": {name: io.sha(raw) for name, raw in inventory.items()}}
        plan = {
            **COLD_PLAN,
            "candidate": {"consumer_sha256": io.sha(io.canonical(baseline["source_consumers"]))},
        }
        worker._source_consumers(baseline, plan)
        for mutation in (
            lambda rows: rows.pop(HELPER),
            lambda rows: rows.update({HELPER: "0" * 64}),
        ):
            altered = dict(baseline["source_consumers"])
            mutation(altered)
            changed_plan = {
                **COLD_PLAN,
                "candidate": {"consumer_sha256": io.sha(io.canonical(altered))},
            }
            with self.assertRaisesRegex(io.Refusal, "consumer_hash_mismatch"):
                worker._source_consumers({"source_consumers": altered}, changed_plan)

    def test_archive_retains_exact_helper_bytes_without_executing_them(self):
        raw = b"raise AssertionError('archived code must remain inert')\n"
        inventory = {HELPER: io.sha(raw)}
        document = {
            "source_consumers": inventory,
            "consumer_sha256": io.sha(io.canonical(inventory)),
        }
        digest = document["consumer_sha256"]
        retained = {"/private/origin/consumers/" + HELPER: raw}
        self.assertEqual(
            archive._consumer_maps({"/private/code.json": document}), {digest: inventory}
        )
        with patch.object(
            io, "read_protected", side_effect=AssertionError("original source reread")
        ):
            result = archive._consumer_bytes({"/private/code.json": document}, retained, {})
        self.assertEqual(result, {digest: {HELPER: raw}})

    def test_full_disposable_graph_restore_reconstructs_the_helper_without_original_source(self):
        fixture = archive_tests.ControlArchiveTests()
        original_write = io.write_new
        helper_raw = b"raise AssertionError('restoring source must never execute it')\n"
        helper_inventory = None

        def source_receipt(path, document):
            nonlocal helper_inventory
            if Path(path).name == "recovery_code_ref.json" and "source_consumers" in document:
                helper_inventory = {**document["source_consumers"], HELPER: io.sha(helper_raw)}
                document = {
                    **document,
                    "source_consumers": helper_inventory,
                    "consumer_sha256": io.sha(io.canonical(helper_inventory)),
                }
            return original_write(path, document)

        with patch.object(io, "write_new", side_effect=source_receipt):
            fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source = fixture.private / "helper-source"
        (source / "scripts").mkdir(parents=True)
        (source / HELPER).write_bytes(helper_raw)
        digest = io.sha(io.canonical(helper_inventory))
        result = fixture.capture(consumer_roots={digest: source})
        shutil.rmtree(fixture.root)
        shutil.rmtree(source)
        receipt = fixture.restore(result)
        self.assertTrue(receipt["semantic_graph_verified"])
        manifest = io.document((fixture.package / "manifest.json").read_bytes())
        restored_helper = next(
            row
            for row in manifest["artifacts"]
            if row["logical_name"] == "code/" + digest + "/" + HELPER
        )
        self.assertEqual((fixture.restored / restored_helper["payload"]).read_bytes(), helper_raw)


@unittest.skipUnless(os.name == "posix", "Requires private native Linux fixture")
class NativeColdSourceTests(unittest.TestCase):
    setUp = launch_tests.NativeLaunchTests.setUp

    def test_cold_source_pins_deny_missing_changed_and_extra_manager_helpers(self):
        with self.assertRaisesRegex(native.journal.Refusal, "cold_consumers_incomplete"):
            native._sources(self.consumers, cold=True)
        path = self.crm / HELPER
        path.write_bytes(b"# private helper; never execute\n")
        self.consumers[HELPER] = io.sha(path.read_bytes())
        native._sources(self.consumers, cold=True)
        with self.assertRaisesRegex(native.journal.Refusal, "source_name"):
            native._sources(self.consumers)
        unknown = "scripts/j1_manager_unqualified.py"
        (self.crm / unknown).write_bytes(b"# unqualified\n")
        with self.assertRaisesRegex(native.journal.Refusal, "source_name"):
            native._sources({**self.consumers, unknown: io.sha(b"# unqualified\n")}, cold=True)
        path.write_bytes(b"# replaced verifier\n")
        with self.assertRaisesRegex(native.journal.Refusal, "consumer_changed"):
            native._sources(self.consumers, cold=True)
