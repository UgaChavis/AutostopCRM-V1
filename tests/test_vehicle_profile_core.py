from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.vehicle_profile_service import VehicleProfileService  # noqa: E402
from minimal_kanban.vehicle_profile import (  # noqa: E402
    VehicleProfile,
    normalize_source_confidence,
    normalize_vehicle_float,
    normalize_vehicle_int,
)


class VehicleProfileCoreTests(unittest.TestCase):
    def test_catalog_aliases_preserve_model_detection_without_inventing_specs(self) -> None:
        service = VehicleProfileService()
        for make, russian_make, alias, expected in (
            ("SUZUKI", "СУЗУКИ", "SWIFT", "Swift"),
            ("KIA", "КИА", "RIO", "Rio"),
            ("TOYOTA", "ТОЙОТА", "CAMRY", "Camry"),
            ("TOYOTA", "ТОЙОТА", "CAMRY 70", "Camry"),
            ("TOYOTA", "ТОЙОТА", "XV70", "Camry"),
            ("NISSAN", "НИССАН", "X-TRAIL", "X-Trail"),
            ("NISSAN", "НИССАН", "XTRAIL", "X-Trail"),
            ("NISSAN", "НИССАН", "T32", "X-Trail"),
            ("LADA", "ЛАДА", "VESTA", "Vesta"),
        ):
            for text in (
                f"{make} {alias}",
                f"{make} {alias}".casefold(),
                f"{russian_make} {alias}",
            ):
                with self.subTest(text=text):
                    profile, _, _ = service._parse_text_payload(text)
                    self.assertEqual(profile.model_display, expected)
                    self.assertIsNone(profile.engine_displacement_l)
                    self.assertEqual(profile.engine_code, "")
                    self.assertEqual(service._detect_model_from_catalog(alias, "HONDA"), "")

    def test_numeric_normalizers_reject_non_finite_and_pathological_values(self) -> None:
        self.assertIsNone(normalize_vehicle_float("9" * 400))
        self.assertIsNone(normalize_vehicle_float("1e309"))
        self.assertIsNone(normalize_vehicle_float(float("inf")))
        self.assertIsNone(normalize_vehicle_int("9" * 20))
        self.assertEqual(normalize_source_confidence("nan"), 0.0)
        self.assertEqual(normalize_source_confidence(float("inf")), 0.0)

    def test_to_dict_normalizes_direct_dataclass_meta_values(self) -> None:
        profile = VehicleProfile(
            customer_phone="+7 900 111-22-33",
            customer_phones="+7 901 222-33-44",  # type: ignore[arg-type]
            source_confidence=float("nan"),
            source_links_or_refs="https://one.example\nhttps://two.example",  # type: ignore[arg-type]
            manual_fields="vin make_display unknown",  # type: ignore[arg-type]
            autofilled_fields="model_display",  # type: ignore[arg-type]
            tentative_fields={"bad": "value"},  # type: ignore[arg-type]
            field_sources=["bad"],  # type: ignore[arg-type]
            warnings="duplicate warning",  # type: ignore[arg-type]
        )

        payload = profile.to_dict()
        compact = profile.to_compact_dict()

        self.assertEqual(
            payload["customer_phones"],
            ["+7 900 111-22-33", "+7 901 222-33-44"],
        )
        self.assertEqual(payload["source_confidence"], 0.0)
        self.assertEqual(
            payload["source_links_or_refs"],
            ["https://one.example", "https://two.example"],
        )
        self.assertEqual(payload["manual_fields"], ["vin", "make_display"])
        self.assertEqual(payload["autofilled_fields"], ["model_display"])
        self.assertEqual(payload["tentative_fields"], [])
        self.assertEqual(payload["field_sources"], {})
        self.assertEqual(payload["warnings"], ["duplicate warning"])
        self.assertEqual(compact["manual_fields"], ["vin", "make_display"])

    def test_finalize_profile_metadata_rejects_non_finite_confidence(self) -> None:
        service = VehicleProfileService()
        profile = VehicleProfile(make_display="Toyota", source_confidence=float("inf"))

        finalized = service.finalize_profile_metadata(profile)

        self.assertEqual(finalized.source_confidence, 0.95)


if __name__ == "__main__":
    unittest.main()
