from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.metadata import get_tmdb_content_rating_display, store_tmdb_content_ratings
from app.models import (
    TV,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from app.tasks import (
    backfill_tmdb_content_ratings,
    cleanup_expired_tmdb_content_ratings,
)


class TMDBContentRatingTests(TestCase):
    """Test durable TMDB movie and TV rating workflows."""

    def setUp(self):
        """Create one tracked TMDB movie and TV show."""
        self.user = get_user_model().objects.create_user(username="rating-owner")
        self.movie_item = Item.objects.create(
            media_id="321",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Example Movie",
            image="https://example.com/movie.jpg",
        )
        self.tv_item = Item.objects.create(
            media_id="654",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Example Show",
            image="https://example.com/tv.jpg",
        )
        Movie.objects.create(
            item=self.movie_item,
            user=self.user,
            status=Status.COMPLETED,
        )
        TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.COMPLETED,
        )

    @patch("app.tasks.tmdb.content_ratings")
    def test_backfill_fetches_and_persists_movies_and_tv_in_bounded_batches(
        self, fetch
    ):
        """Backfill refreshes both supported media types in one bounded run."""
        fetch.side_effect = [
            {"US": [{"certification": "R", "type": 3, "release_date": "2024-01-01"}]},
            {"US": [{"rating": "TV-MA"}]},
        ]

        result = backfill_tmdb_content_ratings(batch_size=2)

        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["succeeded"], 2)
        self.assertFalse(result["has_more"])
        self.assertEqual(
            list(fetch.call_args_list[0].args),
            [MediaTypes.MOVIE.value, "321"],
        )
        self.assertEqual(
            list(fetch.call_args_list[1].args),
            [MediaTypes.TV.value, "654"],
        )
        self.assertEqual(
            self.movie_item.tmdb_content_rating_metadata.ratings["US"][0][
                "certification"
            ],
            "R",
        )
        self.assertEqual(
            self.tv_item.tmdb_content_rating_metadata.ratings["US"][0]["rating"],
            "TV-MA",
        )

    def test_movie_card_label_deduplicates_certifications_and_hides_unset(self):
        """Card labels deduplicate variants and stay hidden when unset."""
        store_tmdb_content_ratings(
            self.movie_item,
            {
                "US": [
                    {"certification": "R", "type": 3, "release_date": "2024-01-01"},
                    {"certification": "R", "type": 4, "release_date": "2024-02-01"},
                    {"certification": "PG-13", "type": 3, "release_date": "2024-03-01"},
                ],
            },
        )

        display = get_tmdb_content_rating_display(self.movie_item, "US")

        self.assertEqual(display["label"], "R / PG-13")
        self.assertEqual(len(display["entries"]), 3)
        self.assertIsNone(get_tmdb_content_rating_display(self.movie_item, "UNSET"))

    def test_tv_card_label_uses_configured_region_text(self):
        """TV cards show their configured-region text rating."""
        store_tmdb_content_ratings(
            self.tv_item,
            {"US": [{"rating": "TV-MA"}], "GB": [{"rating": "15"}]},
        )

        self.assertEqual(
            get_tmdb_content_rating_display(self.tv_item, "US")["label"],
            "TV-MA",
        )

    def test_expired_rating_is_not_displayed_before_cleanup_runs(self):
        """Expired labels stop displaying even before the cleanup task executes."""
        metadata = store_tmdb_content_ratings(
            self.movie_item,
            {"US": [{"certification": "R", "type": 3, "release_date": ""}]},
        )
        metadata.synced_at = timezone.now() - timedelta(days=151)
        metadata.save(update_fields=["synced_at"])
        self.movie_item.refresh_from_db()

        self.assertIsNone(get_tmdb_content_rating_display(self.movie_item, "US"))

    @patch("app.tasks.tmdb.content_ratings", side_effect=ValueError("bad response"))
    def test_failed_refresh_preserves_last_good_rating_and_applies_cooldown(
        self, fetch
    ):
        """A failed request leaves stored ratings intact and is cooled down."""
        store_tmdb_content_ratings(self.tv_item, {})
        metadata = store_tmdb_content_ratings(
            self.movie_item,
            {"US": [{"certification": "R", "type": 3, "release_date": ""}]},
        )
        metadata.synced_at = timezone.now() - timedelta(days=31)
        metadata.last_attempted_at = metadata.synced_at
        metadata.save(update_fields=["synced_at", "last_attempted_at"])

        first = backfill_tmdb_content_ratings(batch_size=1)
        second = backfill_tmdb_content_ratings(batch_size=1)

        self.assertEqual(first["failed_ids"], [self.movie_item.pk])
        self.assertEqual(second["processed"], 0)
        self.assertEqual(fetch.call_count, 1)
        metadata.refresh_from_db()
        self.assertEqual(metadata.ratings["US"][0]["certification"], "R")

    def test_cleanup_deletes_rating_rows_past_hard_retention(self):
        """Cleanup physically removes data older than the retention horizon."""
        metadata = store_tmdb_content_ratings(
            self.tv_item,
            {"US": [{"rating": "TV-MA"}]},
        )
        metadata.synced_at = timezone.now() - timedelta(days=151)
        metadata.save(update_fields=["synced_at"])

        result = cleanup_expired_tmdb_content_ratings()

        self.assertEqual(result["metadata_deleted"], 1)
        self.assertFalse(
            type(metadata).objects.filter(pk=metadata.pk).exists(),
        )
