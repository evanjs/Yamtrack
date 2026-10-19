from unittest.mock import patch
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import (
    Game,
    Item,
    MediaTypes,
    Sources,
    Status,
    SteamAppMapping,
    SteamAppRating,
)
from app.tasks import backfill_steam_app_ratings, get_due_steam_app_items


class SteamAppRatingsBackfillTests(TestCase):
    """Test bounded Steam rating hydration for IGDB-linked tracked games."""

    def setUp(self):
        """Create two tracked and one untracked IGDB game Items."""
        self.user = get_user_model().objects.create_user(username="steam-ratings")
        self.items = []
        for media_id in ("101", "202", "303"):
            item = Item.objects.create(
                media_id=media_id,
                source=Sources.IGDB.value,
                media_type=MediaTypes.GAME.value,
                title=f"Game {media_id}",
                image="https://example.com/cover.jpg",
            )
            self.items.append(item)
        for item in self.items[:2]:
            Game.objects.create(item=item, user=self.user, status=Status.PLANNING)

    @patch("app.tasks.steam.app_ratings", return_value={"esrb": {"rating": "m"}})
    @patch("app.tasks.igdb.steam_app_ids", side_effect=[["292030"], []])
    def test_backfill_is_bounded_and_persists_mapping_and_ratings(
        self,
        mock_app_ids,
        mock_ratings,
    ):
        """A resumable batch hydrates verified IDs and records an empty mapping."""
        first = backfill_steam_app_ratings.run(batch_size=1)

        self.assertEqual(first["processed"], 1)
        self.assertEqual(first["succeeded"], 1)
        self.assertEqual(first["next_after_item_id"], self.items[0].pk)
        self.assertTrue(first["has_more"])
        self.assertEqual(
            SteamAppMapping.objects.get(item=self.items[0]).app_ids,
            ["292030"],
        )
        self.assertEqual(
            SteamAppRating.objects.get(steam_app_id="292030").ratings,
            {"esrb": {"rating": "m"}},
        )

        second = backfill_steam_app_ratings.run(
            batch_size=1,
            after_item_id=first["next_after_item_id"],
        )

        self.assertEqual(second["processed"], 1)
        self.assertEqual(second["succeeded"], 1)
        self.assertFalse(second["has_more"])
        self.assertEqual(SteamAppMapping.objects.get(item=self.items[1]).app_ids, [])
        self.assertFalse(SteamAppMapping.objects.filter(item=self.items[2]).exists())
        self.assertEqual(mock_app_ids.call_count, 2)
        self.assertEqual(mock_ratings.call_count, 1)

    @patch("app.tasks.steam.app_ratings", side_effect=ValueError("unavailable"))
    @patch("app.tasks.igdb.steam_app_ids", return_value=["292030"])
    def test_failed_fetch_keeps_last_good_ratings_and_records_attempt(
        self,
        _mock_app_ids,
        _mock_ratings,
    ):
        """A failed request is visible as failure without losing previous data."""
        mapping = SteamAppMapping.objects.create(
            item=self.items[0],
            app_ids=["292030"],
        )
        rating = SteamAppRating.objects.create(
            item=self.items[0],
            steam_app_id="292030",
            ratings={"esrb": {"rating": "t"}},
        )
        Game.objects.create(item=self.items[2], user=self.user, status=Status.PLANNING)

        result = backfill_steam_app_ratings.run(batch_size=1)

        self.assertEqual(result["failed_ids"], [self.items[0].pk])
        rating.refresh_from_db()
        mapping.refresh_from_db()
        self.assertEqual(rating.ratings, {"esrb": {"rating": "t"}})
        self.assertIsNotNone(rating.last_attempted_at)
        self.assertIsNotNone(mapping.ratings_last_attempted_at)

    def test_scheduled_selection_cools_down_failed_items_for_healthy_ones(self):
        """A recent failure cohort does not block an older healthy due item."""
        now = timezone.now()
        old_success = now - timedelta(days=31)
        recent_attempt = now - timedelta(hours=1)
        for item in self.items[:2]:
            SteamAppMapping.objects.create(
                item=item,
                app_ids=["292030"],
                synced_at=now,
                last_attempted_at=now,
                ratings_synced_at=old_success,
                ratings_last_attempted_at=recent_attempt,
            )
        healthy_item = self.items[2]
        Game.objects.create(item=healthy_item, user=self.user, status=Status.PLANNING)
        SteamAppMapping.objects.create(
            item=healthy_item,
            app_ids=["570"],
            synced_at=now,
            last_attempted_at=now,
            ratings_synced_at=old_success,
            ratings_last_attempted_at=old_success,
        )

        due_items = get_due_steam_app_items(batch_size=1, fair_order=True)

        self.assertEqual([item.pk for item in due_items], [healthy_item.pk])
