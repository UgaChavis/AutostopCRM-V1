from __future__ import annotations

import copy
import os
import sys
import unittest
from unittest.mock import patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_fixture as topology
from scripts import j1_forward_cold_kernel as kernel
from scripts import j1_forward_io as io
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


class FixtureBoundaryTests(unittest.TestCase):
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
        production["scope"] = "production_native"
        with self.assertRaisesRegex(io.Refusal, "fresh_home_manifest"):
            kernel._fresh_home(production, fixture.binding, "production_native")


if __name__ == "__main__":
    unittest.main()
