from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import TestCase

from app.metadata import store_igdb_game_taxonomies
from app.models import (
    BasicMedia,
    Game,
    IGDBGameMetadata,
    IGDBGameTaxonomy,
    Item,
    MediaTypes,
    Sources,
    Status,
)
from app.views import get_igdb_game_taxonomy_filters
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

    def test_filter_parser_separates_repeated_includes_and_excludes(self):
        """Repeated taxonomy IDs preserve independent include/exclude states."""
        query_params = QueryDict(
            "genre=4&genre=5&genre_exclude=10&keyword_exclude=30&genre=bad"
        )

        current_filters, active_filters = get_igdb_game_taxonomy_filters(query_params)

        self.assertEqual(
            current_filters["genre"],
            {"include": [4, 5], "exclude": [10]},
        )
        self.assertEqual(
            active_filters[IGDBGameTaxonomy.Kind.GENRE],
            {"include": [4, 5], "exclude": [10]},
        )

    def test_taxonomy_filter_ors_within_facet_and_ands_across_facets(self):
        """Selected IDs OR within a facet and combine across facet kinds."""
        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.GAME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={
                "genre": {"include": [4, 5], "exclude": []},
                "theme": {"include": [10], "exclude": []},
            },
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
            taxonomy_filters={
                "genre": {"include": [4], "exclude": []},
                "keyword": {"include": [30], "exclude": []},
            },
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
            taxonomy_filters={
                "genre": {"include": [4], "exclude": []},
                "theme": {"include": [11], "exclude": []},
            },
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
            taxonomy_filters={"genre": {"include": [4], "exclude": []}},
        )

        self.assertEqual(media_list.count(), 1)
        self.assertNotIn(unknown_item, [media.item for media in media_list])
        self.assertFalse(IGDBGameMetadata.objects.filter(item=unknown_item).exists())

    def test_unknown_items_still_need_a_positive_match_when_preference_enabled(self):
        """The unknown-data option never bypasses a positive facet match."""
        unknown_item = Item.objects.create(
            media_id="606",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Unknown Game",
            image="https://example.com/cover.jpg",
        )
        Game.objects.bulk_create(
            [Game(item=unknown_item, user=self.user, status=Status.COMPLETED)],
        )

        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.GAME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={"genre": {"include": [4], "exclude": []}},
            include_unknown_taxonomy=True,
        )

        self.assertEqual([media.item for media in media_list], [self.items[0]])

    def test_excluded_values_reject_matches_but_allow_known_empty(self):
        """Exclusions reject matching taxonomy while retaining known-empty items."""
        empty_item = Item.objects.create(
            media_id="404",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Known Empty Game",
            image="https://example.com/cover.jpg",
        )
        Game.objects.bulk_create(
            [Game(item=empty_item, user=self.user, status=Status.COMPLETED)],
        )
        store_igdb_game_taxonomies(
            empty_item,
            {"genres": [], "themes": [], "keywords": []},
        )

        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.GAME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={"genre": {"include": [], "exclude": [4]}},
        )

        self.assertEqual(
            {media.item for media in media_list},
            {self.items[1], empty_item},
        )

    def test_unknown_items_can_be_included_by_negative_filters_when_enabled(self):
        """The opt-in unknown policy lets unhydrated items pass exclusions."""
        unknown_item = Item.objects.create(
            media_id="505",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Unknown Game",
            image="https://example.com/cover.jpg",
        )
        Game.objects.bulk_create(
            [Game(item=unknown_item, user=self.user, status=Status.COMPLETED)],
        )

        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.GAME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={"genre": {"include": [], "exclude": [4]}},
            include_unknown_taxonomy=True,
        )

        self.assertEqual(
            {media.item for media in media_list},
            {self.items[1], unknown_item},
        )

    def test_included_and_excluded_values_compose_within_facet(self):
        """Included values are ORed and excluded values are rejected."""
        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.GAME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={"genre": {"include": [4, 5], "exclude": [4]}},
        )

        self.assertEqual([media.item for media in media_list], [self.items[1]])
