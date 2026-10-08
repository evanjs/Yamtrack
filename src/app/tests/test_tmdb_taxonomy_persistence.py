from django.test import TestCase

from app.metadata import store_tmdb_movie_taxonomies
from app.models import (
    Item,
    MediaTypes,
    Sources,
    TMDBMovieMetadata,
    TMDBMovieTaxonomy,
)


class TMDBMovieTaxonomyPersistenceTests(TestCase):
    """Persist and refresh TMDB movie taxonomy by provider IDs."""

    def setUp(self):
        """Create a shared TMDB movie catalog item."""
        self.item = Item.objects.create(
            media_id="321",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Example Movie",
            image="https://example.com/poster.jpg",
        )

    def test_persists_genres_and_keywords_with_tmdb_identity(self):
        """Genre and keyword IDs remain separate TMDB taxonomy values."""
        metadata = store_tmdb_movie_taxonomies(
            self.item,
            {
                "genres": [{"id": 27, "name": "Horror"}],
                "keywords": [{"id": 1852, "name": "haunting"}],
            },
        )

        stored = {
            (taxonomy.kind, taxonomy.provider_id, taxonomy.name)
            for taxonomy in metadata.taxonomies.all()
        }
        self.assertEqual(
            stored,
            {
                (TMDBMovieTaxonomy.Kind.GENRE, 27, "Horror"),
                (TMDBMovieTaxonomy.Kind.KEYWORD, 1852, "haunting"),
            },
        )

    def test_refresh_replaces_old_values_and_records_successful_empty(self):
        """A successful refresh replaces associations, including empty facets."""
        store_tmdb_movie_taxonomies(
            self.item,
            {
                "genres": [{"id": 27, "name": "Horror"}],
                "keywords": [{"id": 1852, "name": "haunting"}],
            },
        )

        metadata = store_tmdb_movie_taxonomies(
            self.item,
            {"genres": [], "keywords": [{"id": 1870, "name": "ghost"}]},
        )

        self.assertEqual(
            list(metadata.taxonomies.values_list("kind", "provider_id")),
            [(TMDBMovieTaxonomy.Kind.KEYWORD, 1870)],
        )
        self.assertTrue(TMDBMovieMetadata.objects.filter(item=self.item).exists())

    def test_invalid_data_does_not_overwrite_last_good_taxonomy(self):
        """Invalid provider data leaves previously stored taxonomy intact."""
        store_tmdb_movie_taxonomies(
            self.item,
            {
                "genres": [{"id": 27, "name": "Horror"}],
                "keywords": [],
            },
        )

        with self.assertRaises(ValueError):
            store_tmdb_movie_taxonomies(
                self.item,
                {"genres": [{"id": 0, "name": "Invalid"}], "keywords": []},
            )

        self.assertEqual(
            list(
                self.item.tmdb_movie_metadata.taxonomies.values_list(
                    "provider_id",
                    flat=True,
                ),
            ),
            [27],
        )

    def test_rejects_non_tmdb_movie_items(self):
        """TMDB movie taxonomy cannot be attached to another provider or type."""
        game_item = Item.objects.create(
            media_id="99",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Game",
            image="https://example.com/game.jpg",
        )

        with self.assertRaises(ValueError):
            store_tmdb_movie_taxonomies(game_item, {"genres": [], "keywords": []})
