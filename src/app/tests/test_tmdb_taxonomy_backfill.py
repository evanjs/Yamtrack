from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.metadata import (
    mark_tmdb_movie_taxonomy_attempt,
    store_tmdb_movie_credits,
    store_tmdb_movie_taxonomies,
)
from app.models import (
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
    TMDBMovieMetadata,
)
from app.providers.services import ProviderAPIError
from app.tasks import (
    backfill_tmdb_movie_taxonomies,
    cleanup_expired_tmdb_movie_taxonomies,
    refresh_due_tmdb_movie_taxonomies,
)


class TMDBMovieTaxonomyBackfillTests(TestCase):
    """Test bounded resumable TMDB movie taxonomy backfill."""

    def setUp(self):
        """Create one shared movie tracked by multiple users."""
        self.user = get_user_model().objects.create_user(username="movie-owner")
        self.other_user = get_user_model().objects.create_user(username="other-owner")
        self.item = Item.objects.create(
            media_id="321",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Example Movie",
            image="https://example.com/poster.jpg",
        )
        Movie.objects.bulk_create(
            [
                Movie(item=self.item, user=self.user, status=Status.COMPLETED),
                Movie(item=self.item, user=self.other_user, status=Status.COMPLETED),
            ],
        )

    @patch("app.providers.tmdb.movie_taxonomies")
    def test_backfills_each_shared_item_once_and_returns_cursor(self, mock_taxonomies):
        """A batch hydrates distinct tracked items and returns its continuation ID."""
        mock_taxonomies.return_value = {
            "tmdb_taxonomies": {
                "genres": [{"id": 27, "name": "Horror"}],
                "keywords": [{"id": 1852, "name": "haunting"}],
            },
            "tmdb_credits": {"cast": [], "crew": []},
        }

        result = backfill_tmdb_movie_taxonomies(batch_size=1)

        mock_taxonomies.assert_called_once_with("321")
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["failed_ids"], [])
        self.assertEqual(result["next_after_item_id"], self.item.pk)
        self.assertFalse(result["has_more"])
        self.assertTrue(TMDBMovieMetadata.objects.filter(item=self.item).exists())

    def test_backfill_rejects_nonpositive_batch_sizes(self):
        """A bounded backfill requires a positive batch size."""
        with self.assertRaises(ValueError):
            backfill_tmdb_movie_taxonomies(batch_size=0)

    def test_backfill_skips_items_with_fresh_taxonomy_and_credit_data(self):
        """Items with both taxonomies and credits are skipped by backfill."""
        store_tmdb_movie_taxonomies(
            self.item,
            {"genres": [{"id": 27, "name": "Horror"}], "keywords": []},
        )
        store_tmdb_movie_credits(self.item, {"cast": [], "crew": []})

        with patch("app.providers.tmdb.movie_taxonomies") as mock_taxonomies:
            result = backfill_tmdb_movie_taxonomies(batch_size=1)

        mock_taxonomies.assert_not_called()
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["succeeded"], 0)

    @patch("app.providers.tmdb.movie_taxonomies")
    def test_backfill_refreshes_metadata_before_the_expiry_horizon(
        self,
        mock_taxonomies,
    ):
        """Aged TMDB taxonomy is refreshed before it reaches its hard expiry."""
        metadata = store_tmdb_movie_taxonomies(
            self.item,
            {"genres": [{"id": 27, "name": "Horror"}], "keywords": []},
        )
        metadata.synced_at = timezone.now() - timedelta(days=31)
        metadata.last_attempted_at = metadata.synced_at
        metadata.save(update_fields=["synced_at", "last_attempted_at"])
        mock_taxonomies.return_value = {
            "tmdb_taxonomies": {"genres": [], "keywords": []},
            "tmdb_credits": {"cast": [], "crew": []},
        }

        result = backfill_tmdb_movie_taxonomies(batch_size=1)

        mock_taxonomies.assert_called_once_with("321")
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["failed_ids"], [])

    def test_cleanup_deletes_expired_metadata_and_orphaned_taxonomies(self):
        """Expired TMDB cache rows and now-unused labels are removed together."""
        metadata = store_tmdb_movie_taxonomies(
            self.item,
            {"genres": [{"id": 27, "name": "Horror"}], "keywords": []},
        )
        metadata.synced_at = timezone.now() - timedelta(days=151)
        metadata.save(update_fields=["synced_at"])

        result = cleanup_expired_tmdb_movie_taxonomies()

        self.assertEqual(result["metadata_deleted"], 1)
        self.assertEqual(result["taxonomies_deleted"], 1)
        self.assertFalse(TMDBMovieMetadata.objects.filter(pk=metadata.pk).exists())

    @patch(
        "app.providers.tmdb.movie_taxonomies",
        side_effect=ProviderAPIError(
            Sources.TMDB.value,
            RuntimeError("provider unavailable"),
        ),
    )
    def test_failed_refresh_preserves_last_good_values_and_waits_before_retry(
        self,
        mock_taxonomies,
    ):
        """A refresh failure preserves data and records a retry cooldown."""
        metadata = store_tmdb_movie_taxonomies(
            self.item,
            {"genres": [{"id": 27, "name": "Horror"}], "keywords": []},
        )
        metadata.synced_at = timezone.now() - timedelta(days=31)
        metadata.last_attempted_at = metadata.synced_at
        metadata.save(update_fields=["synced_at", "last_attempted_at"])

        first_result = backfill_tmdb_movie_taxonomies(batch_size=1)
        second_result = backfill_tmdb_movie_taxonomies(batch_size=1)

        self.assertEqual(first_result["failed_ids"], [self.item.pk])
        self.assertEqual(second_result["processed"], 0)
        self.assertEqual(mock_taxonomies.call_count, 1)
        self.assertEqual(
            list(metadata.taxonomies.values_list("name", flat=True)),
            ["Horror"],
        )

    @patch("app.providers.tmdb.movie_taxonomies")
    def test_hourly_batches_advance_past_recently_failed_items(self, mock_taxonomies):
        """The retry cooldown lets later eligible IDs progress without a cursor."""
        next_item = Item.objects.create(
            media_id="654",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Next Movie",
            image="https://example.com/next.jpg",
        )
        Movie.objects.bulk_create(
            [Movie(item=next_item, user=self.user, status=Status.COMPLETED)],
        )
        mock_taxonomies.side_effect = [
            ValueError("temporary provider failure"),
            {
                "tmdb_taxonomies": {"genres": [], "keywords": []},
                "tmdb_credits": {"cast": [], "crew": []},
            },
        ]

        first_batch = backfill_tmdb_movie_taxonomies(batch_size=1)
        second_batch = backfill_tmdb_movie_taxonomies(batch_size=1)

        self.assertEqual(first_batch["failed_ids"], [self.item.pk])
        self.assertTrue(first_batch["has_more"])
        self.assertEqual(second_batch["succeeded"], 1)
        self.assertEqual(second_batch["next_after_item_id"], next_item.pk)
        self.assertEqual(
            [call.args[0] for call in mock_taxonomies.call_args_list],
            ["321", "654"],
        )

    @patch("app.providers.tmdb.movie_taxonomies")
    def test_scheduled_refresh_prioritizes_never_attempted_items(self, mock_taxonomies):
        """A batch-sized retry backlog cannot starve never-attempted titles."""
        failed_items = [self.item]
        for media_id in ("654", "777"):
            item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Failed Movie {media_id}",
                image="https://example.com/failed.jpg",
            )
            Movie.objects.bulk_create(
                [Movie(item=item, user=self.user, status=Status.COMPLETED)],
            )
            mark_tmdb_movie_taxonomy_attempt(item)
            failed_items.append(item)

        for item in failed_items:
            mark_tmdb_movie_taxonomy_attempt(item)
            metadata = TMDBMovieMetadata.objects.get(item=item)
            metadata.last_attempted_at = timezone.now() - timedelta(days=2)
            metadata.save(update_fields=["last_attempted_at"])

        next_item = Item.objects.create(
            media_id="987",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Never Attempted Movie",
            image="https://example.com/next.jpg",
        )
        Movie.objects.bulk_create(
            [Movie(item=next_item, user=self.user, status=Status.COMPLETED)],
        )
        mock_taxonomies.return_value = {
            "tmdb_taxonomies": {"genres": [], "keywords": []},
            "tmdb_credits": {"cast": [], "crew": []},
        }

        result = refresh_due_tmdb_movie_taxonomies(batch_size=1)

        mock_taxonomies.assert_called_once_with("987")
        self.assertEqual(result["succeeded"], 1)
