from django.test import SimpleTestCase

from app.providers import igdb


class IGDBAgeRatingTests(SimpleTestCase):
    """Test extraction of IGDB's linked age-rating records."""

    def test_get_age_ratings_keeps_body_art_and_descriptions(self):
        """Normalize the linked organization, category, art URL, and descriptors."""
        response = {
            "age_ratings": [
                {
                    "organization": {"id": 1, "name": "ESRB"},
                    "rating_category": {"id": 11, "rating": "M"},
                    "rating_cover_url": "https://images.example/esrb-m.png",
                    "rating_content_descriptions": [
                        {"description": "Blood and Gore"},
                        {"description": "Strong Sexual Content"},
                    ],
                    "synopsis": "Mature content.",
                },
                {
                    "organization": {"id": 2, "name": "PEGI"},
                    "rating_category": {"id": 1, "rating": "Three"},
                    "rating_cover_url": None,
                    "rating_content_descriptions": [],
                },
            ],
        }

        self.assertEqual(
            igdb.get_age_ratings(response),
            [
                {
                    "organization": "ESRB",
                    "rating": "M",
                    "rating_cover_url": "https://images.example/esrb-m.png",
                    "content_descriptions": [
                        "Blood and Gore",
                        "Strong Sexual Content",
                    ],
                    "synopsis": "Mature content.",
                },
                {
                    "organization": "PEGI",
                    "rating": "Three",
                    "rating_cover_url": None,
                    "content_descriptions": [],
                    "synopsis": "",
                },
            ],
        )

    def test_get_age_ratings_tolerates_missing_and_malformed_values(self):
        """Incomplete optional rating records produce an empty safe collection."""
        self.assertEqual(igdb.get_age_ratings({}), [])
        self.assertEqual(igdb.get_age_ratings({"age_ratings": None}), [])
        self.assertEqual(igdb.get_age_ratings({"age_ratings": "malformed"}), [])
