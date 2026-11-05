"""Client vehicle provenance remains normalized and detached from caller data."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.models import (  # noqa: E402
    CLIENT_VEHICLE_METADATA_FIELDS,
    ClientProfile,
    ClientVehicle,
)
from minimal_kanban.vehicle_profile import VehicleProfile  # noqa: E402


def empty_metadata():
    return {
        "manual_fields": [],
        "autofilled_fields": [],
        "tentative_fields": [],
        "field_sources": {},
        "source_summary": "",
        "source_confidence": 0.0,
        "source_links_or_refs": [],
    }


def reference_metadata(metadata):
    """Model the pre-optimization VehicleProfile conversion and overlap rules."""
    reference = VehicleProfile.from_dict(deepcopy(metadata))
    if reference.tentative_fields:
        reference.autofilled_fields = sorted(
            set(reference.autofilled_fields) | set(reference.tentative_fields)
        )
        reference.manual_fields = sorted(
            set(reference.manual_fields) - set(reference.tentative_fields)
        )
    return {name: getattr(reference, name) for name in CLIENT_VEHICLE_METADATA_FIELDS}


class ClientVehicleMetadataNormalizationTests(unittest.TestCase):
    def test_subclass_metadata_override_is_preserved(self):
        class CustomVehicle(ClientVehicle):
            def source_metadata(self):
                return {**super().source_metadata(), "source_summary": "custom source"}

        self.assertEqual(
            CustomVehicle(id="synthetic", vehicle="Synthetic").source_summary, "custom source"
        )

    def test_metadata_matches_vehicle_profile_normalization(self):
        cases = [
            ("canonical empty", {}),
            ("negative zero", {"source_confidence": -0.0}),
            ("integer zero", {"source_confidence": 0}),
            ("none", {name: None for name in CLIENT_VEHICLE_METADATA_FIELDS}),
            ("false", {"source_confidence": False}),
            ("true", {"source_confidence": True}),
            ("numeric string", {"source_confidence": "0,75"}),
            ("nonfinite string", {"source_confidence": "nan"}),
            (
                "metadata strings",
                {
                    "manual_fields": "vin engine_model",
                    "autofilled_fields": "engine_model",
                    "tentative_fields": "engine_model",
                    "source_links_or_refs": "first; second",
                    "source_summary": "  source  ",
                },
            ),
            (
                "malformed collections",
                {
                    "manual_fields": False,
                    "autofilled_fields": {"vin": "bad"},
                    "tentative_fields": 5,
                    "field_sources": ["bad"],
                    "source_links_or_refs": False,
                    "source_summary": ["source"],
                },
            ),
            (
                "overlapping provenance",
                {
                    "manual_fields": ["vin", "engine_model", "unknown", "vin"],
                    "autofilled_fields": ["engine_model", "model_display"],
                    "tentative_fields": ["vin", "engine_model"],
                    "field_sources": {"vin": " source ", "engine_model": "catalog"},
                    "source_links_or_refs": ["first", "first", "second"],
                    "source_summary": "verified",
                    "source_confidence": 0.976,
                },
            ),
        ]
        for label, changes in cases:
            with self.subTest(label=label):
                metadata = empty_metadata()
                metadata.update(changes)
                payload = {"id": "synthetic-vehicle", "vehicle": "Synthetic", **metadata}
                before = deepcopy(payload)
                vehicle = ClientVehicle.from_value(payload)
                self.assertIsNotNone(vehicle)
                actual = vehicle.source_metadata()
                expected = reference_metadata(metadata)
                # JSON comparison also distinguishes 0.0 and -0.0.
                self.assertEqual(
                    json.dumps(actual, sort_keys=True, ensure_ascii=False),
                    json.dumps(expected, sort_keys=True, ensure_ascii=False),
                )
                self.assertEqual(payload, before)

    def test_mutable_metadata_is_detached_in_both_directions(self):
        for label, changes in (
            ("empty", {}),
            (
                "nonempty",
                {
                    "manual_fields": ["vin"],
                    "autofilled_fields": ["engine_model"],
                    "tentative_fields": ["engine_model"],
                    "field_sources": {"vin": "synthetic"},
                    "source_links_or_refs": ["synthetic source"],
                },
            ),
        ):
            with self.subTest(label=label):
                metadata = empty_metadata()
                metadata.update(changes)
                raw_vehicle = {"id": "synthetic-vehicle", "vehicle": "Synthetic", **metadata}
                raw_client = {"id": "synthetic-client", "vehicles": [raw_vehicle]}
                original = deepcopy(raw_client)
                vehicle = ClientVehicle.from_value(raw_vehicle)
                profile = ClientProfile.from_dict(raw_client)
                other = profile.vehicles[0]
                self.assertIsNotNone(vehicle)
                other_before = other.to_dict()

                vehicle.manual_fields.append("model_display")
                vehicle.autofilled_fields.append("model_display")
                vehicle.tentative_fields.append("model_display")
                vehicle.field_sources["model_display"] = "changed"
                vehicle.source_links_or_refs.append("changed")
                self.assertEqual(raw_client, original)
                self.assertEqual(other.to_dict(), other_before)

                vehicle_before = vehicle.to_dict()
                raw_vehicle["manual_fields"].append("engine_model")
                raw_vehicle["autofilled_fields"].append("vin")
                raw_vehicle["tentative_fields"].append("vin")
                raw_vehicle["field_sources"]["vin"] = "changed raw"
                raw_vehicle["source_links_or_refs"].append("changed raw")
                self.assertEqual(vehicle.to_dict(), vehicle_before)
                self.assertEqual(other.to_dict(), other_before)


if __name__ == "__main__":
    unittest.main()
