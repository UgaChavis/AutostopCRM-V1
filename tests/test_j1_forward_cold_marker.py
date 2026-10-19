from __future__ import annotations

import copy
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import j1_forward_cold_marker as cold
from scripts import j1_forward_io as io
from scripts import j1_forward_marker as marker
from scripts import j1_forward_release as release
from tests import test_j1_forward_marker as base


class ColdRetainedMarkerTests(unittest.TestCase):
    def setUp(self):
        base.MarkerTests.setUp(self)
        self.documents = {}

        def write(path, value):
            path = Path(path)
            raw = io.canonical(value)
            self.documents[str(path)] = raw
            return {"path": str(path), "sha256": io.sha(raw)}

        def read(reference):
            raw = self.documents[reference["path"]]
            io.require(io.sha(raw) == reference["sha256"], "fixture_reference_changed")
            return raw

        self.write = write
        self.stack.enter_context(patch.object(io, "write_new", side_effect=write))
        self.stack.enter_context(patch.object(io, "referenced", side_effect=read))
        self.stack.enter_context(
            patch.object(
                io, "read_protected", side_effect=lambda path, *_: self.documents[str(path)]
            )
        )
        old_base = write("/control/attempts/old/baseline.json", {"maintenance_parent": self.pin})
        self.old_plan = {"attempt_id": str(uuid.uuid4()), "baseline_ref": old_base}
        old_ref = write("/control/attempts/old/plan.json", self.old_plan)
        old_payload = release.marker_value(old_ref, self.old_plan)
        self.old_identity = marker.ensure(self.pin, old_payload, authorize=Mock())
        owner = write(
            "/control/attempts/old/marker-identity-" + "a" * 32 + ".json",
            {"plan_ref": old_ref, "parent": self.pin, "identity": self.old_identity},
        )
        write("/control/attempts/old/marker-owned.json", {"plan_ref": old_ref, "record": owner})
        self.baseline = {
            "maintenance_parent": self.pin,
            "recovery_origin": {"origin_plan_ref": old_ref},
        }
        baseline_ref = write("/control/attempts/new/baseline.json", self.baseline)
        self.plan = {"attempt_id": str(uuid.uuid4()), "baseline_ref": baseline_ref}
        self.plan_ref = write("/control/attempts/new/plan.json", self.plan)
        self.controller, self.store = Mock(), Mock()
        self.store.active_plan.return_value = {"plan": self.plan}
        self.fs.events.clear()

    def retain(self):
        with (
            patch.object(release, "marker_record", wraps=release.marker_record),
            patch.object(Path, "exists", return_value=True),
        ):
            return cold.retain(
                Path("/control/attempts/new"),
                self.plan_ref,
                self.baseline,
                controller=self.controller,
                store=self.store,
            )

    def test_old_bytes_and_ownership_are_retained_until_committed_removal(self):
        node = self.fs.entry
        reference = self.retain()
        with patch.object(Path, "exists", return_value=True):
            record = cold.require(reference, self.plan_ref)
        self.assertTrue(record["old_bytes_retained"])
        self.assertEqual(record["identity"], self.old_identity)
        self.assertIs(self.fs.entry, node)
        self.assertFalse(any(event[0] in {"write", "unlink"} for event in self.fs.events))
        commit = {"path": "/control/state/history/commit.json", "sha256": "c" * 64}
        self.store.head.return_value = {"sha256": commit["sha256"], "record": {"plan": self.plan}}
        self.controller.ready.return_value = {"ok": True}
        with patch.object(Path, "exists", return_value=True):
            cold.remove(
                reference, self.plan_ref, commit, controller=self.controller, store=self.store
            )
        self.assertIsNone(self.fs.entry)

    def test_replaced_old_inode_and_nonstrict_retention_flag_never_adopt(self):
        reference = self.retain()
        raw = self.documents[reference["path"]]
        self.fs.entry.ino += 1
        with patch.object(Path, "exists", return_value=True), self.assertRaises(io.Refusal):
            cold.require(reference, self.plan_ref)
        self.fs.entry.ino -= 1
        record = copy.deepcopy(io.document(raw))
        record["old_bytes_retained"] = 1
        changed = self.write("/control/changed.json", record)
        with self.assertRaisesRegex(io.Refusal, "cold_marker_record"):
            cold.require(changed, self.plan_ref)
        self.assertIsNotNone(self.fs.entry)


if __name__ == "__main__":
    unittest.main()
