"""Input parsing remains equivalent across offsets, legacy dates and configuration."""

import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban import models  # noqa: E402


class BusinessDatetimeInputTests(unittest.TestCase):
    def setUp(self):
        models.clear_business_timezone_cache()
        self.addCleanup(models.clear_business_timezone_cache)

    def test_cached_and_uncached_parsers_preserve_all_supported_inputs(self):
        cases = [
            None,
            "",
            " ",
            False,
            0,
            123,
            {},
            [],
            "bad date",
            "31.02.2026",
            "26.07.2026 14:35",
            "26.07.26",
            "2026-10-13",
            "20261013",
            "2026-W42-2",
            "2026-10-13+07:00",
            "2026-10-13T07:00",
            "2026-10-13Z",
            "2026-10-13T12:00:00Z",
            "2026-10-13 12:00:00+07:00",
            "2026-10-13T12:00:00.123456+05:45",
            "2026-10-13T12:00:00,123456-04:00",
            "2026-10-13T12:00:00+00:00:12",
            "2026-10-13x12:00:00+07:00",
            "26.07.2026" + " " * 140,
        ]
        for day in range(28):
            moment = datetime(2026, 10, 1, 12, 34, 56, tzinfo=UTC) + timedelta(days=day)
            cases.extend(
                (
                    moment.isoformat(),
                    moment.replace(tzinfo=None).isoformat(),
                    moment.strftime("%d.%m.%Y %H:%M"),
                )
            )
        for zone in ("Asia/Krasnoyarsk", "UTC", "Europe/Berlin", "Invalid/Autostop-Zone"):
            with patch.dict(
                os.environ, {"AUTOSTOPCRM_BUSINESS_TIMEZONE": zone, "AUTOSTOPCRM_TIMEZONE": ""}
            ):
                for value in cases:
                    with self.subTest(zone=zone, value=value):
                        raw = str(value).strip() if value else ""
                        expected = (
                            models._parse_business_datetime(raw, models._business_timezone_name())
                            if raw
                            else None
                        )
                        for _ in range(2):
                            actual = models.parse_business_datetime(value)
                            self.assertEqual(actual, expected)
                            self.assertEqual(
                                actual.isoformat() if actual else None,
                                expected.isoformat() if expected else None,
                            )

    def test_explicit_iso_offset_does_not_read_unrelated_timezone_configuration(self):
        with patch.object(
            models, "_business_timezone_name", side_effect=AssertionError("unneeded timezone read")
        ):
            self.assertEqual(
                models.parse_business_datetime("2026-10-13T12:34:56+07:00").isoformat(),
                "2026-10-13T12:34:56+07:00",
            )

    def test_date_cache_is_bounded_and_cleared_with_the_existing_cache(self):
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for index in range(8200):
            models.parse_business_datetime((base + timedelta(seconds=index)).isoformat())
        self.assertEqual(models._parse_aware_iso_datetime_cached.cache_info().maxsize, 8192)
        self.assertEqual(models._parse_aware_iso_datetime_cached.cache_info().currsize, 8192)
        models.parse_business_datetime("26.07.2026 14:35")
        models.clear_business_timezone_cache()
        self.assertEqual(models._parse_aware_iso_datetime_cached.cache_info().currsize, 0)
        self.assertEqual(models._parse_business_datetime_cached.cache_info().currsize, 0)
