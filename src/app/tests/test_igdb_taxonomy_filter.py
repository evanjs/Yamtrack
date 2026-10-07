from django.contrib.auth import get_user_model
from django.test import TestCase

from app.metadata import store_igdb_game_taxonomies
from app.models import (
    BasicMedia,
    Game,
    IGDBGameMetadata,
    Item,
    MediaTypes,
    Sources,
    Status,
)
from users.models import MediaStatusChoices


class IGDBGameTaxonomyFilterTests(TestCase):
    """Test structured IGDB taxonomy filters on tracked games."""

    def setUp(self):
        """Create two tracked games with distinct taxonomy values."""
        self.user = get_user_model().objects.create_user(username="taxonomy-filter")
        self.items = []
        for media_id, title, game_taxonomies in [
            (
                "101",
                "First Game",
                {
                    "genres": [{"id": 4, "name": "Fighting"}],
                    "themes": [{"id": 10, "name": "Fantasy"}],
                    "keywords": [{"id": 30, "name": "co-op"}],
                },
            ),
            (
                "202",
                "Second Game",
                {
                    "genres": [{"id": 5, "name": "Adventure"}],
                    "themes": [{"id": 11, "name": "Science fiction"}],
                    "keywords": [{"id": 31, "name": "single-player"}],
                },
            ),
        ]:
            item = Item.objects.create(
                media_id=media_id,
                source=Sources.IGDB.value,
                media_type=MediaTypes.GAME.value,
                title=title,
                image="https://example.com/cover.jpg",
            )
            Game.objects.create(item=item, user=self.user, status=Status.COMPLETED)
            store_igdb_game_taxonomies(item, game_taxonomies)
            self.items.append(item)

    def test_taxonomy_filter_ors_within_facet_and_ands_across_facets(self):
        """Selected IDs OR within a facet and combine across facet kinds."""
        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.GAME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={"genre": [4, 5], "theme": [10]},
        )

        self.assertEqual([media.item for media in media_list], [self.items[0]])

    def test_taxonomy_filter_does_not_duplicate_retracked_items(self):
        """Multiple matching associations still return one row per Item."""
        Game.objects.create(item=self.items[0], user=self.user, status=Status.PAUSED)

        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.GAME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={"genre": [4], "keyword": [30]},
        )

        self.assertEqual(media_list.count(), 1)
        self.assertEqual(media_list[0].item, self.items[0])

    def test_nonmatching_taxonomy_facets_return_no_games(self):
        """Distinct facet filters must both match the same tracked game."""
        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.GAME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={"genre": [4], "theme": [11]},
        )

        self.assertEqual(media_list.count(), 0)

    def test_unknown_item_has_no_taxonomy_metadata(self):
        """An unhydrated item remains outside taxonomy-filtered results."""
        unknown_item = Item.objects.create(
            media_id="303",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Unknown Game",
            image="https://example.com/cover.jpg",
        )
        Game.objects.create(item=unknown_item, user=self.user, status=Status.COMPLETED)

        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.GAME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={"genre": [4]},
        )

        self.assertEqual(media_list.count(), 1)
        self.assertNotIn(unknown_item, [media.item for media in media_list])
        self.assertFalse(IGDBGameMetadata.objects.filter(item=unknown_item).exists())
