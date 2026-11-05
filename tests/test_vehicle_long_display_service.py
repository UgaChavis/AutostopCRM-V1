"""A long existing profile must not prevent unrelated card edits."""

# The fixture selects this checkout before importing the application.
# ruff: noqa: I001
from tests.services_case import CardServiceCase

from minimal_kanban.services.errors import ServiceError


class VehicleLongDisplayServiceTests(CardServiceCase):
    def test_full_profile_update_preserves_parts_at_both_limits(self):
        for model_length in (44, 45, 109, 110, 120):
            for include_year in (False, True):
                with self.subTest(model_length=model_length, include_year=include_year):
                    card = self.service.create_card(
                        {
                            "vehicle": "SYNTHETIC SHORT",
                            "title": "SYNTHETIC",
                            "description": "SYNTHETIC DESCRIPTION",
                            "vehicle_profile": {
                                "make_display": "Land Rover",
                                "model_display": "R" * model_length,
                                "production_year": 2020,
                                "vin": "WVWZZZ1KZAW000001",
                            },
                        }
                    )["card"]
                    profile = card["vehicle_profile"].copy()
                    if not include_year:
                        profile["display_name"] = " ".join(
                            (profile["make_display"], profile["model_display"])
                        )
                    self.service.update_card(
                        {
                            "card_id": card["id"],
                            "vehicle": card["vehicle_raw"],
                            "title": "SYNTHETIC UPDATED",
                            "vehicle_profile": profile,
                        }
                    )
                    saved = self.service.get_card({"card_id": card["id"]})["card"]
                    self.assertEqual(saved["vehicle_raw"], card["vehicle_raw"])
                    self.assertEqual(saved["description"], card["description"])
                    self.assertEqual(saved["vehicle_profile"], card["vehicle_profile"])

    def test_long_matching_alias_normalizes_unicode_spaces(self):
        model = "R" * 110
        profile, _, _ = self.service._vehicle_profiles.normalize_profile_payload(
            {
                "make_display": "Land Rover",
                "model_display": model,
                "display_name": "Land\u00a0Rover\u2003" + model,
                "production_year": 2020,
                "manual_fields": ["display_name"],
            }
        )
        self.assertEqual((profile.make_display, profile.model_display), ("Land Rover", model))

    def test_alias_comparison_rejects_unmatched_long_suffix(self):
        name = "Land Rover " + "R" * 110
        profile, _, _ = self.service._vehicle_profiles.normalize_profile_payload(
            {
                "make_display": "Land Rover",
                "model_display": "R" * 110,
                "display_name": name + " EXTRA",
                "production_year": 2020,
                "manual_fields": ["display_name"],
            }
        )
        self.assertEqual(profile.make_display, "Land")
        self.assertNotEqual(profile.model_display, "R" * 110)

    def test_new_long_label_is_rejected_without_write(self):
        card = self.service.create_card(
            {
                "vehicle": "Honda Civic",
                "title": "SYNTHETIC",
                "vehicle_profile": {"make_display": "Honda", "model_display": "Civic"},
            }
        )["card"]
        before = self.state_file.read_bytes()
        with self.assertRaises(ServiceError) as explicit:
            self.service.update_card({"card_id": card["id"], "vehicle": "X" * 61})
        self.assertEqual(explicit.exception.code, "validation_error")
        self.assertEqual(self.state_file.read_bytes(), before)
        with self.assertRaises(ServiceError) as derived:
            self.service.update_card(
                {
                    "card_id": card["id"],
                    "vehicle_profile": {"make_display": "Land Rover", "model_display": "R" * 55},
                }
            )
        self.assertEqual(derived.exception.code, "validation_error")
        self.assertEqual(self.state_file.read_bytes(), before)
