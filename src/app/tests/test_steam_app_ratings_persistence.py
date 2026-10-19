from django.test import TestCase
from django.utils import timezone

from app.metadata import (
    mark_steam_app_mapping_attempt,
    mark_steam_app_rating_attempt,
    store_steam_app_mapping,
    store_steam_app_ratings,
)
from app.models import Item, MediaTypes, Sources, SteamAppMapping, SteamAppRating


class SteamAppRatingsPersistenceTests(TestCase):
    """Test durable Steam ratings keyed to verified IGDB game Items."""

    def setUp(self):
        """Create an IGDB game Item for the ratings tests."""
        self.item = Item.objects.create(
            media_id="123",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Example Game",
            image="https://example.com/cover.jpg",
        )

    def test_store_ratings_preserves_unknown_agencies_and_empty_success(self):
        """Keep agency payloads verbatim and distinguish a fetched-empty result."""
        payload = {
            "esrb": {"rating": "M", "descriptors": ["Violence"]},
            "future_agency": {"rating": "Example"},
        }
        metadata = store_steam_app_ratings(self.item, "292030", payload)

        self.assertEqual(metadata.item, self.item)
        self.assertEqual(metadata.steam_app_id, "292030")
        self.assertEqual(metadata.ratings, payload)
        self.assertIsNotNone(metadata.synced_at)

        empty_metadata = store_steam_app_ratings(self.item, "292030", {})
        self.assertEqual(empty_metadata.ratings, {})
        self.assertGreaterEqual(empty_metadata.synced_at, metadata.synced_at)
        self.assertEqual(SteamAppRating.objects.count(), 1)

    def test_invalid_payload_preserves_last_good_values(self):
        """A malformed agency container must not replace the last good response."""
        metadata = store_steam_app_ratings(
            self.item,
            "292030",
            {"esrb": {"rating": "M"}},
        )
        synced_at = metadata.synced_at

        with self.assertRaises(TypeError):
            store_steam_app_ratings(self.item, "292030", ["invalid"])

        metadata.refresh_from_db()
        self.assertEqual(metadata.ratings, {"esrb": {"rating": "M"}})
        self.assertEqual(metadata.synced_at, synced_at)

    def test_invalid_identity_or_item_is_rejected(self):
        """Only positive numeric Steam IDs on IGDB game Items are accepted."""
        for app_id in ("0", "29x", "", True):
            with self.subTest(app_id=app_id), self.assertRaises(ValueError):
                store_steam_app_ratings(self.item, app_id, {})

        non_game = Item.objects.create(
            media_id="456",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Not a game",
            image="https://example.com/movie.jpg",
        )
        with self.assertRaises(ValueError):
            store_steam_app_ratings(non_game, "292030", {})

    def test_app_id_cannot_be_reassigned_to_another_item(self):
        """A conflicting IGDB mapping must not silently move stored metadata."""
        store_steam_app_ratings(self.item, "292030", {"esrb": {"rating": "M"}})
        other_item = Item.objects.create(
            media_id="456",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Another Game",
            image="https://example.com/other.jpg",
        )

        with self.assertRaises(ValueError):
            store_steam_app_ratings(other_item, "292030", {"esrb": {"rating": "T"}})

        metadata = SteamAppRating.objects.get(steam_app_id="292030")
        self.assertEqual(metadata.item, self.item)
        self.assertEqual(metadata.ratings, {"esrb": {"rating": "M"}})

    def test_attempt_timestamp_does_not_mark_ratings_fresh(self):
        """Failed refresh attempts preserve last-good values and success time."""
        metadata = store_steam_app_ratings(
            self.item,
            "292030",
            {"esrb": {"rating": "M"}},
        )
        synced_at = metadata.synced_at
        before_attempt = timezone.now()

        attempted = mark_steam_app_rating_attempt(self.item, "292030")

        self.assertEqual(attempted.synced_at, synced_at)
        self.assertGreaterEqual(attempted.last_attempted_at, before_attempt)
        self.assertEqual(attempted.ratings, {"esrb": {"rating": "M"}})

    def test_successful_empty_mapping_is_persisted(self):
        """An IGDB item with no Steam record is not rescanned as never checked."""
        mapping = store_steam_app_mapping(self.item, [])

        self.assertEqual(mapping.app_ids, [])
        self.assertIsNotNone(mapping.synced_at)
        self.assertEqual(SteamAppMapping.objects.count(), 1)

    def test_failed_mapping_attempt_preserves_last_good_ids(self):
        """A failed mapping refresh updates only the attempt timestamp."""
        mapping = store_steam_app_mapping(self.item, ["292030"])
        synced_at = mapping.synced_at

        attempted = mark_steam_app_mapping_attempt(self.item)

        self.assertEqual(attempted.app_ids, ["292030"])
        self.assertEqual(attempted.synced_at, synced_at)
        self.assertIsNotNone(attempted.last_attempted_at)
