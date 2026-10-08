from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.metadata import store_tmdb_movie_taxonomies
from app.models import (
    BasicMedia,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
    TMDBMovieTaxonomy,
)
from users.models import MediaStatusChoices


class TMDBMovieTaxonomyFilterTests(TestCase):
    """Test structured TMDB taxonomy filters on tracked movies."""

    def setUp(self):
        """Create two tracked TMDB movies with different taxonomy values."""
        self.user = get_user_model().objects.create_user(
            username="tmdb-taxonomy-filter",
        )
        self.items = []
        movie_values = [
            (
                "321",
                "First Movie",
                {
                    "genres": [{"id": 27, "name": "Horror"}],
                    "keywords": [{"id": 12, "name": "ghost"}],
                },
            ),
            (
                "654",
                "Second Movie",
                {
                    "genres": [{"id": 53, "name": "Thriller"}],
                    "keywords": [{"id": 13, "name": "mystery"}],
                },
            ),
        ]
        for media_id, title, taxonomies in movie_values:
            item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=title,
                image="https://example.com/poster.jpg",
            )
            Movie.objects.bulk_create(
                [Movie(item=item, user=self.user, status=Status.COMPLETED)],
            )
            store_tmdb_movie_taxonomies(item, taxonomies)
            self.items.append(item)

    def test_movie_filters_include_any_selected_and_exclude_selected_ids(self):
        """TMDB facets OR includes and reject exclusions within each facet."""
        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={
                TMDBMovieTaxonomy.Kind.GENRE: {"include": [27, 53], "exclude": [27]},
            },
        )

        self.assertEqual([media.item for media in media_list], [self.items[1]])

    def test_movie_taxonomy_facets_and_status_compose(self):
        """Genres, keywords, and status are all applied together."""
        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=Status.COMPLETED.value,
            sort_filter="score",
            taxonomy_filters={
                TMDBMovieTaxonomy.Kind.GENRE: {"include": [27, 53], "exclude": []},
                TMDBMovieTaxonomy.Kind.KEYWORD: {"include": [12], "exclude": []},
            },
        )

        self.assertEqual([media.item for media in media_list], [self.items[0]])

    def test_expired_tmdb_taxonomy_is_treated_as_unknown(self):
        """Expired labels stop matching and follow the unknown-data preference."""
        metadata = self.items[0].tmdb_movie_metadata
        metadata.synced_at = timezone.now() - timedelta(days=151)
        metadata.save(update_fields=["synced_at"])

        excluded_by_default = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={
                TMDBMovieTaxonomy.Kind.GENRE: {"include": [], "exclude": [53]},
            },
        )
        including_unknown = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            taxonomy_filters={
                TMDBMovieTaxonomy.Kind.GENRE: {"include": [], "exclude": [53]},
            },
            include_unknown_taxonomy=True,
        )

        self.assertEqual(excluded_by_default.count(), 0)
        self.assertEqual([media.item for media in including_unknown], [self.items[0]])
