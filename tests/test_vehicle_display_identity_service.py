"""Combined display aliases preserve explicit make/model boundaries."""

from tests.services_case import CardServiceCase


class VehicleDisplayIdentityServiceTests(CardServiceCase):
    def test_matching_alias_preserves_explicit_parts_and_changed_or_empty_alias_applies(self):
        for alias, fields, expected in (
            ("Land Rover Range Rover", ["display_name"], ("Land Rover", "Range Rover")),
            ("Land Rover Range Rover 2020", ["display_name"], ("Land Rover", "Range Rover")),
            ("Honda Civic", ["display_name"], ("Honda", "Civic")),
            ("", ["display_name"], ("", "")),
        ):
            with self.subTest(alias=alias):
                card = self.service.create_card(
                    {
                        "title": "SYNTHETIC",
                        "vehicle_profile": {
                            "make_display": "Land Rover",
                            "model_display": "Range Rover",
                            "production_year": 2020,
                        },
                    }
                )["card"]
                self.service.update_card(
                    {
                        "card_id": card["id"],
                        "vehicle_profile": {
                            "make_display": "Land Rover",
                            "model_display": "Range Rover",
                            "production_year": 2020,
                            "display_name": alias,
                            "manual_fields": fields,
                        },
                    }
                )
                saved = self.service.get_card({"card_id": card["id"]})["card"]["vehicle_profile"]
                self.assertEqual((saved["make_display"], saved["model_display"]), expected)

    def test_alias_without_explicit_parts_still_splits(self):
        card = self.service.create_card(
            {
                "title": "SYNTHETIC",
                "vehicle_profile": {
                    "display_name": "Honda Civic",
                    "production_year": 2020,
                },
            }
        )["card"]
        self.assertEqual(
            (card["vehicle_profile"]["make_display"], card["vehicle_profile"]["model_display"]),
            ("Honda", "Civic"),
        )

    def test_matching_alias_preserves_make_without_model(self):
        card = self.service.create_card(
            {
                "title": "SYNTHETIC",
                "vehicle_profile": {
                    "make_display": "Land Rover",
                    "model_display": "",
                    "display_name": "Land Rover",
                    "production_year": 2020,
                },
            }
        )["card"]
        self.assertEqual(
            (card["vehicle_profile"]["make_display"], card["vehicle_profile"]["model_display"]),
            ("Land Rover", ""),
        )
