from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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
    def test_numeric_normalizers_reject_non_finite_and_pathological_values(self) -> None:
        self.assertIsNone(normalize_vehicle_float("9" * 400))
        self.assertIsNone(normalize_vehicle_float("1e309"))
        self.assertIsNone(normalize_vehicle_float(float("inf")))
        self.assertIsNone(normalize_vehicle_int("9" * 20))
        self.assertEqual(normalize_source_confidence("nan"), 0.0)
        self.assertEqual(normalize_source_confidence(float("inf")), 0.0)

    def test_service_normalizes_non_finite_and_boolean_timeouts(self) -> None:
        service = VehicleProfileService(timeout_seconds=float("inf"))

        self.assertEqual(service._timeout_seconds, 12.0)
        self.assertEqual(VehicleProfileService(timeout_seconds=True)._timeout_seconds, 12.0)
        self.assertIsNone(service._kw_or_hp_to_hp(None, "999999"))
        self.assertEqual(service._kw_or_hp_to_hp(None, "100"), 134)

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

    def test_vin_enrichment_ignores_non_object_nhtsa_json(self) -> None:
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def raise_for_status(self) -> None:
                return None

            def iter_bytes(self, *, chunk_size=None):
                _ = chunk_size
                yield b"[]"

        service = VehicleProfileService()
        with patch(
            "minimal_kanban.services.vehicle_profile_service.httpx.stream",
            return_value=_Response(),
        ):
            self.assertIsNone(service._enrich_from_vin_decode("JSAZC72S001234567"))

    def test_vin_enrichment_ignores_nonstandard_nhtsa_json_constants(self) -> None:
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def raise_for_status(self) -> None:
                return None

            def iter_bytes(self, *, chunk_size=None):
                _ = chunk_size
                yield b'{"Results":[{"Make":NaN}]}'

        service = VehicleProfileService()
        with patch(
            "minimal_kanban.services.vehicle_profile_service.httpx.stream",
            return_value=_Response(),
        ):
            self.assertIsNone(service._enrich_from_vin_decode("JSAZC72S001234567"))

    def test_vin_enrichment_rejects_oversized_nhtsa_response(self) -> None:
        class _Response:
            chunk_size = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def raise_for_status(self) -> None:
                return None

            def iter_bytes(self, *, chunk_size=None):
                self.chunk_size = int(chunk_size or 1)
                yield b"x" * self.chunk_size

        response = _Response()
        service = VehicleProfileService()
        with (
            patch(
                "minimal_kanban.services.vehicle_profile_service.NHTSA_VIN_RESPONSE_MAX_BYTES", 4
            ),
            patch(
                "minimal_kanban.services.vehicle_profile_service.httpx.stream",
                return_value=response,
            ),
        ):
            self.assertIsNone(service._enrich_from_vin_decode("JSAZC72S001234567"))

        self.assertEqual(response.chunk_size, 5)

    def test_vin_enrichment_does_not_follow_redirects(self) -> None:
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def raise_for_status(self) -> None:
                return None

            def iter_bytes(self, *, chunk_size=None):
                _ = chunk_size
                yield (b'{"Results":[{"Make":"Toyota","Model":"Camry","ModelYear":"2020"}]}')

        stream_kwargs: dict[str, object] = {}

        def fake_stream(*args, **kwargs):
            _ = args
            stream_kwargs.update(kwargs)
            return _Response()

        service = VehicleProfileService()
        with patch(
            "minimal_kanban.services.vehicle_profile_service.httpx.stream",
            side_effect=fake_stream,
        ):
            profile = service._enrich_from_vin_decode("JSAZC72S001234567")

        self.assertIsNotNone(profile)
        self.assertIs(stream_kwargs["follow_redirects"], False)

    def test_finalize_profile_metadata_rejects_non_finite_confidence(self) -> None:
        service = VehicleProfileService()
        profile = VehicleProfile(make_display="Toyota", source_confidence=float("inf"))

        finalized = service.finalize_profile_metadata(profile)

        self.assertEqual(finalized.source_confidence, 0.95)


if __name__ == "__main__":
    unittest.main()
