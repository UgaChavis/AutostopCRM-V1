"""Stable graph fingerprints must preserve data and detect unrelated changes."""

from __future__ import annotations

import copy
import unittest

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.services.manager_tool_status import (  # noqa: E402
    COMPUTED_FIELDS,
    durable_digest,
    durable_projection,
    status_scope_digest,
)


class ManagerStructureDigestTests(unittest.TestCase):
    @staticmethod
    def graph():
        return {
            "version": 3,
            "tool_statuses": {
                "demo.inspect": {
                    "state": "working",
                    "updated_at": "2026-10-15T00:00:00+00:00",
                    "updated_by": "ADMIN",
                }
            },
            "extension": {"nested": [None, True, -0.0, "ремонт"], "can_edit": False},
        }

    def test_projection_detaches_mutable_data_and_keeps_nested_computed_names(self):
        data = self.graph()
        before = copy.deepcopy(data)
        data.update(dict.fromkeys(COMPUTED_FIELDS, object()))
        projected = durable_projection(data)
        self.assertEqual(projected, before)
        projected["extension"]["nested"].append("changed")
        projected["extension"]["can_edit"] = True
        projected["tool_statuses"]["demo.inspect"]["state"] = "not_commissioned"
        self.assertEqual(data["extension"], before["extension"])
        self.assertEqual(data["tool_statuses"], before["tool_statuses"])

    def test_digest_matches_known_utf8_canonical_json_without_mutation(self):
        data = self.graph()
        before = copy.deepcopy(data)
        # Sorted keys, UTF-8, compact separators and negative zero are significant.
        expected = "f7e3effed2542bbaea65cfaaf9c5a687be6672638f5cb48f1056de9662161a3f"
        self.assertEqual(durable_digest(data), expected)
        self.assertEqual(durable_digest(dict(reversed(list(data.items())))), expected)
        self.assertEqual(data, before)
        for field in COMPUTED_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(durable_digest({**data, field: object()}), expected)

    def test_digest_detects_version_status_and_unknown_extension_changes(self):
        before = self.graph()
        expected = durable_digest(before)
        for field, value in (
            ("version", 4),
            ("tool_statuses", {}),
            ("extension", {"nested": [None, True, 0.0, "ремонт"], "can_edit": False}),
            ("extension", {"nested": [None, True, -0.0, "ремонт"], "can_edit": True}),
            ("future_field", {"receipts": [1]}),
        ):
            with self.subTest(field=field, value=value):
                self.assertNotEqual(durable_digest({**before, field: value}), expected)

    def test_scope_ignores_only_version_and_the_requested_status_without_mutation(self):
        data = self.graph()
        before = copy.deepcopy(data)
        expected = "f4d899c2662a5679f7a5a5c1d0f0d0d690c5ecf4378f9bc7230d3480eb7bd5f8"
        self.assertEqual(status_scope_digest(data, "demo.inspect"), expected)
        self.assertEqual(data, before)
        changed = copy.deepcopy(data)
        changed["version"] += 1
        changed["tool_statuses"]["demo.inspect"]["state"] = "not_commissioned"
        self.assertEqual(status_scope_digest(changed, "demo.inspect"), expected)
        del changed["tool_statuses"]["demo.inspect"]
        self.assertEqual(status_scope_digest(changed, "demo.inspect"), expected)
        changed["tool_statuses"]["demo.other"] = before["tool_statuses"]["demo.inspect"]
        self.assertNotEqual(status_scope_digest(changed, "demo.inspect"), expected)
        changed = copy.deepcopy(data)
        changed["extension"]["can_edit"] = True
        self.assertNotEqual(status_scope_digest(changed, "demo.inspect"), expected)
        self.assertNotEqual(status_scope_digest(data, "demo.other"), expected)

    def test_scope_normalizes_absent_status_map_without_adding_it_to_source(self):
        absent = {"version": 1, "extension": {}}
        expected = status_scope_digest({"extension": {}, "tool_statuses": {}}, "missing")
        self.assertEqual(status_scope_digest(absent, "missing"), expected)
        self.assertEqual(absent, {"version": 1, "extension": {}})
        self.assertEqual(status_scope_digest({}, "missing"), status_scope_digest({}, "other"))

    def test_invalid_json_does_not_remove_fields_from_source(self):
        for value, error in ((object(), TypeError), ([], ValueError)):
            if isinstance(value, list):
                value.append(value)
            data = {"version": 1, "extension": value, "tool_statuses": {"demo.inspect": {}}}
            for digest in (
                durable_digest,
                lambda graph: status_scope_digest(graph, "demo.inspect"),
            ):
                with self.subTest(error=error, digest=digest):
                    with self.assertRaises(error):
                        digest(data)
                    self.assertIs(data["extension"], value)
                    self.assertEqual(data["version"], 1)
                    self.assertEqual(data["tool_statuses"], {"demo.inspect": {}})
