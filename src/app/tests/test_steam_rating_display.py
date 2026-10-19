from django.conf import settings
from django.test import TestCase

from app.metadata import (
    format_steam_rating_value,
    get_igdb_rating_for_steam,
    get_local_steam_rating_icon_path,
    get_steam_rating_value,
)
from app.models import (
    Item,
    MediaTypes,
    Sources,
    SteamAppMapping,
    SteamAppRating,
)


class SteamRatingDisplayTests(TestCase):
    """Test explicit-agency Steam rating display semantics."""

    def setUp(self):
        """Create a shared IGDB game Item."""
        self.item = Item.objects.create(
            media_id="123",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Example Game",
            image="https://example.com/cover.jpg",
        )

    def test_unset_or_missing_mapping_hides_the_rating(self):
        """No selected agency or no verified mapping means no badge."""
        self.assertIsNone(get_steam_rating_value(self.item, "UNSET"))
        self.assertIsNone(get_steam_rating_value(self.item, "esrb"))

    def test_selected_agency_returns_only_nonempty_unambiguous_rating(self):
        """Display the selected raw agency rating when every mapped App ID agrees."""
        SteamAppMapping.objects.create(item=self.item, app_ids=["292030", "570"])
        SteamAppRating.objects.create(
            item=self.item,
            steam_app_id="292030",
            ratings={"esrb": {"rating": "m"}, "pegi": {"rating": "18"}},
        )
        SteamAppRating.objects.create(
            item=self.item,
            steam_app_id="570",
            ratings={"esrb": {"rating": "m"}, "pegi": {"rating": "18"}},
        )

        self.assertEqual(get_steam_rating_value(self.item, "esrb"), "m")
        self.assertEqual(get_steam_rating_value(self.item, "pegi"), "18")
        self.assertIsNone(get_steam_rating_value(self.item, "usk"))

    def test_missing_or_conflicting_app_ratings_are_not_guessed(self):
        """Do not show partial data or choose one value across multiple App IDs."""
        SteamAppMapping.objects.create(item=self.item, app_ids=["292030", "570"])
        SteamAppRating.objects.create(
            item=self.item,
            steam_app_id="292030",
            ratings={"esrb": {"rating": "m"}},
        )
        self.assertIsNone(get_steam_rating_value(self.item, "esrb"))

        SteamAppRating.objects.create(
            item=self.item,
            steam_app_id="570",
            ratings={"esrb": {"rating": "t"}},
        )
        self.assertIsNone(get_steam_rating_value(self.item, "esrb"))

    def test_card_labels_capitalize_esrb_and_cero_values_only(self):
        """Card labels use familiar agency capitalization without changing PEGI."""
        self.assertEqual(format_steam_rating_value("esrb", "m"), "M")
        self.assertEqual(format_steam_rating_value("cero", "d"), "D")
        self.assertEqual(format_steam_rating_value("pegi", "18"), "18")

    def test_igdb_rating_art_requires_matching_organization_and_category(self):
        """Use IGDB rating art only when its organization and category match Steam."""
        age_ratings = [
            {
                "organization": "ESRB",
                "rating": "M",
                "rating_cover_url": "https://images.example/esrb-m.png",
                "content_descriptions": ["Violence"],
            },
            {
                "organization": "CERO",
                "rating": "CERO_D",
                "rating_cover_url": "https://images.example/cero-d.png",
                "content_descriptions": [],
            },
        ]

        self.assertEqual(
            get_igdb_rating_for_steam("esrb", "m", age_ratings),
            age_ratings[0],
        )
        self.assertEqual(
            get_igdb_rating_for_steam("cero", "d", age_ratings),
            age_ratings[1],
        )
        self.assertIsNone(get_igdb_rating_for_steam("esrb", "t", age_ratings))
        self.assertIsNone(get_igdb_rating_for_steam("pegi", "18", age_ratings))

    def test_local_rating_icons_cover_selected_verified_pd_values(self):
        """Supported ESRB and CERO categories map only from exact IGDB records."""
        cases = [
            ("esrb", "e", "Everyone", "everyone.svg"),
            ("esrb", "e10", "Everyone 10+", "everyone-10-plus.svg"),
            ("esrb", "t", "Teen", "teen.svg"),
            ("esrb", "m", "M", "mature.svg"),
            ("esrb", "ao", "Adults Only 18+", "adults-only.svg"),
            ("esrb", "ec", "Early Childhood", "early-childhood.svg"),
            ("esrb", "rp", "Rating Pending", "rating-pending.svg"),
            ("cero", "a", "CERO_A", "a.svg"),
            ("cero", "b", "CERO_B", "b.svg"),
            ("cero", "c", "CERO_C", "c.svg"),
            ("cero", "d", "CERO_D", "d.svg"),
            ("cero", "z", "CERO_Z", "z.svg"),
        ]

        for agency, steam_rating, igdb_rating, filename in cases:
            with self.subTest(agency=agency, steam_rating=steam_rating):
                icon_path = get_local_steam_rating_icon_path(
                    agency,
                    steam_rating,
                    {"organization": agency.upper(), "rating": igdb_rating},
                )
                self.assertEqual(
                    icon_path,
                    f"img/ratings/{agency}/{filename}",
                )
                self.assertTrue((settings.BASE_DIR / "static" / icon_path).is_file())

        self.assertIsNone(
            get_local_steam_rating_icon_path(
                "esrb",
                "m",
                {"organization": "ESRB", "rating": "T"},
            ),
        )
        self.assertIsNone(
            get_local_steam_rating_icon_path(
                "pegi",
                "18",
                {"organization": "PEGI", "rating": "18"},
            ),
        )
