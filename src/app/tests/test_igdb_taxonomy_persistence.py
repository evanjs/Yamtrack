from django.test import TestCase
from django.utils import timezone

from app.metadata import store_igdb_game_taxonomies
from app.models import (
    IGDBGameMetadata,
    IGDBGameTaxonomy,
    Item,
    MediaTypes,
    Sources,
)


class IGDBTaxonomyPersistenceTests(TestCase):
    """Test durable storage of IGDB game taxonomy facets."""

    def setUp(self):
        """Create a shared IGDB game item for the persistence tests."""
        self.item = Item.objects.create(
            media_id="123",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Example Game",
            image="https://example.com/cover.jpg",
        )

    def test_successful_refresh_replaces_taxonomies_and_records_empty_facets(self):
        """A successful refresh replaces values and keeps known-empty facets."""
        provider_updated_at = timezone.now()
        store_igdb_game_taxonomies(
            self.item,
            {
                "genres": [{"id": 4, "name": "Fighting"}],
                "themes": [],
                "keywords": [{"id": 99, "name": "co-op"}],
            },
            provider_updated_at=provider_updated_at,
        )

        metadata = IGDBGameMetadata.objects.get(item=self.item)
        self.assertEqual(metadata.provider_updated_at, provider_updated_at)
        self.assertCountEqual(
            metadata.taxonomies.values_list("kind", "provider_id", "name"),
            [
                (IGDBGameTaxonomy.Kind.GENRE, 4, "Fighting"),
                (IGDBGameTaxonomy.Kind.KEYWORD, 99, "co-op"),
            ],
        )

        store_igdb_game_taxonomies(
            self.item,
            {
                "genres": [],
                "themes": [{"id": 17, "name": "Fantasy"}],
                "keywords": [],
            },
        )

        metadata.refresh_from_db()
        self.assertCountEqual(
            metadata.taxonomies.values_list("kind", "provider_id", "name"),
            [(IGDBGameTaxonomy.Kind.THEME, 17, "Fantasy")],
        )
        self.assertEqual(
            IGDBGameMetadata.objects.filter(item=self.item).count(),
            1,
        )

    def test_invalid_refresh_does_not_replace_last_good_taxonomies(self):
        """Malformed provider values leave the last successful set untouched."""
        store_igdb_game_taxonomies(
            self.item,
            {
                "genres": [{"id": 4, "name": "Fighting"}],
                "themes": [],
                "keywords": [],
            },
        )
        metadata = IGDBGameMetadata.objects.get(item=self.item)
        synced_at = metadata.synced_at

        with self.assertRaises(ValueError):
            store_igdb_game_taxonomies(
                self.item,
                {
                    "genres": [{"id": 0, "name": "Invalid"}],
                    "themes": [],
                    "keywords": [],
                },
            )

        metadata.refresh_from_db()
        self.assertEqual(metadata.synced_at, synced_at)
        self.assertCountEqual(
            metadata.taxonomies.values_list("name", flat=True),
            ["Fighting"],
        )
