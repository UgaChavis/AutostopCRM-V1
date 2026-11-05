"""A year is removed from a vehicle alias only when it is a separate token."""

# The service fixture installs the source path before importing the application.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.vehicle_profile import VehicleProfile, split_vehicle_display_alias


class VehicleYearBoundaryServiceTests(CardServiceCase):
    def test_alias_split_respects_year_token_boundary(self):
        for value, expected in (
            ("Honda S2000", ("Honda", "S2000")),
            ("Honda S2000 2000", ("Honda", "S2000")),
            ("Honda   S2000   2000", ("Honda", "S2000")),
            ("Honda S2000 2001", ("Honda", "S2000 2001")),
        ):
            with self.subTest(value=value):
                self.assertEqual(split_vehicle_display_alias(value, 2000), expected)
                profile = VehicleProfile.from_dict({"display_name": value, "production_year": 2000})
                self.assertEqual((profile.make_display, profile.model_display), expected)

    def test_card_update_alias_preserves_model_with_and_without_separate_year(self):
        for alias, expected_model in (
            ("Honda S2000", "S2000"),
            ("Honda S2000 2000", "S2000"),
            ("Honda S2000 2001", "S2000 2001"),
        ):
            with self.subTest(alias=alias):
                card = self.service.create_card(
                    {
                        "title": "SYNTHETIC VEHICLE ALIAS",
                        "vehicle_profile": {
                            "make_display": "Honda",
                            "model_display": "Civic",
                            "production_year": 2000,
                        },
                    }
                )["card"]
                self.service.update_card(
                    {
                        "card_id": card["id"],
                        "vehicle_profile": {
                            "display_name": alias,
                            "production_year": 2000,
                            "manual_fields": ["display_name"],
                        },
                    }
                )
                saved = self.service.get_card({"card_id": card["id"]})["card"]["vehicle_profile"]
                self.assertEqual(saved["make_display"], "Honda")
                self.assertEqual(saved["model_display"], expected_model)
                self.assertEqual(saved["production_year"], 2000)
