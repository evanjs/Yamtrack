from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Game,
    IGDBGameMetadata,
    Item,
    MediaTypes,
    Sources,
    Status,
)
from app.tasks import backfill_igdb_game_taxonomies


class IGDBTaxonomyBackfillTests(TestCase):
    """Test resumable backfill of IGDB taxonomies for tracked games."""

    def setUp(self):
        """Create two tracked and one untracked IGDB game Items."""
        self.user = get_user_model().objects.create_user(username="taxonomy-backfill")
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

    @patch("app.tasks.igdb.game_taxonomies")
    def test_backfill_processes_bounded_tracked_item_batches(self, mock_taxonomies):
        """A cursor allows bounded batches and excludes untracked Items."""
        mock_taxonomies.return_value = {
            "igdb_taxonomies": {"genres": [], "themes": [], "keywords": []},
            "igdb_updated_at": None,
        }

        first_batch = backfill_igdb_game_taxonomies.run(batch_size=1)

        self.assertEqual(first_batch["processed"], 1)
        self.assertEqual(first_batch["succeeded"], 1)
        self.assertEqual(first_batch["failed_ids"], [])
        self.assertTrue(first_batch["has_more"])
        self.assertEqual(first_batch["next_after_item_id"], self.items[0].pk)
        self.assertTrue(IGDBGameMetadata.objects.filter(item=self.items[0]).exists())
        self.assertFalse(IGDBGameMetadata.objects.filter(item=self.items[1]).exists())

        second_batch = backfill_igdb_game_taxonomies.run(
            batch_size=1,
            after_item_id=first_batch["next_after_item_id"],
        )

        self.assertEqual(second_batch["processed"], 1)
        self.assertEqual(second_batch["succeeded"], 1)
        self.assertFalse(second_batch["has_more"])
        self.assertFalse(IGDBGameMetadata.objects.filter(item=self.items[2]).exists())
        self.assertEqual(mock_taxonomies.call_count, 2)
