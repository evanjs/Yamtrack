import csv
from datetime import UTC, datetime
from io import StringIO
from unittest.mock import patch

import yaml
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse

from app.metadata import store_igdb_game_taxonomies
from app.models import (
    Anime,
    Book,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)


class ExportCSVTest(TestCase):
    """Test exporting media to CSV."""

    def setUp(self):
        """Create necessary data for the tests."""
        def get_media_metadata(media_type, *_args):
            if media_type == "tv_with_seasons":
                return {"season/1": {"episodes": [{}, {}]}}
            return {
                "title": "Test media",
                "image": "https://example.com/image.jpg",
                "max_progress": None,
                "details": {"seasons": 1},
            }

        self.metadata_patcher = patch(
            "app.providers.services.get_media_metadata",
            side_effect=get_media_metadata,
        )
        self.mock_get_media_metadata = self.metadata_patcher.start()
        self.addCleanup(self.metadata_patcher.stop)
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.client.login(**self.credentials)

        item_movie = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="https://image.url",
        )
        Movie.objects.create(
            item=item_movie,
            user=self.user,
            score=9,
            status=Status.COMPLETED.value,
            notes="Nice",
            start_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
        )

        season = Season.objects.create(
            item=item_season,
            user=self.user,
            score=9,
            status=Status.IN_PROGRESS.value,
            notes="Nice",
        )

        item_episode = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_episode,
            related_season=season,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_anime = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://image.url",
        )
        Anime.objects.create(
            item=item_anime,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_manga = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Berserk",
            image="https://image.url",
        )
        Manga.objects.create(
            item=item_manga,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_game = Item.objects.create(
            media_id="1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Witcher 3: Wild Hunt",
            image="https://image.url",
        )
        Game.objects.create(
            item=item_game,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_book = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Fantastic Mr. Fox",
            image="https://image.url",
        )
        Book.objects.create(
            item=item_book,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

    def test_export_share_yaml_has_typed_sections_and_tracking(self):
        """YAML export separates media types and tracking from item identity."""
        response = self.client.get(reverse("export_yaml"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/yaml")
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="yamtrack_share.yaml"',
        )
        document = yaml.safe_load(response.content)

        self.assertEqual(document["format_version"], 2)
        self.assertEqual(len(document["movie"]), 1)
        self.assertEqual(len(document["game"]), 1)
        self.assertEqual(len(document["book"]), 1)
        self.assertEqual(document["movie"][0]["item"]["title"], "Perfect Blue")
        self.assertEqual(document["movie"][0]["tracking"]["notes"], "Nice")
        self.assertEqual(document["movie"][0]["tracking"]["score"], 9)

    def test_export_share_yaml_includes_persisted_igdb_taxonomies(self):
        """Share YAML exports stored taxonomy IDs and labels without hydration."""
        game_item = Item.objects.get(
            media_id="1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
        )
        store_igdb_game_taxonomies(
            game_item,
            {
                "genres": [{"id": 31, "name": "Adventure"}],
                "themes": [{"id": 17, "name": "Fantasy"}],
                "keywords": [{"id": 99, "name": "metroidvania"}],
            },
        )

        self.mock_get_media_metadata.reset_mock()
        response = self.client.get(reverse("export_yaml"))

        document = yaml.safe_load(response.content)
        self.assertEqual(
            document["game"][0]["item"]["igdb"],
            {
                "genres": [{"id": 31, "name": "Adventure"}],
                "themes": [{"id": 17, "name": "Fantasy"}],
                "keywords": [{"id": 99, "name": "metroidvania"}],
            },
        )
        self.mock_get_media_metadata.assert_not_called()

    def test_export_share_yaml_is_scoped_to_current_user(self):
        """Share export never includes another user's tracking data."""
        other_user = get_user_model().objects.create_user(username="other")
        other_item = Item.objects.create(
            media_id="other-movie",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Private movie",
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=other_item,
                    user=other_user,
                    status=Status.COMPLETED.value,
                ),
            ],
        )

        response = self.client.get(reverse("export_yaml"))
        document = yaml.safe_load(response.content)

        self.assertNotIn(
            "Private movie",
            {entry["item"]["title"] for entry in document["movie"]},
        )

    def test_export_csv(self):
        """Basic test exporting media to CSV."""
        # Generate the CSV file by accessing the export view
        response = self.client.get(reverse("export_csv"))

        # Assert that the response is successful (status code 200)
        self.assertEqual(response.status_code, 200)

        # Assert that the response content type is text/csv
        self.assertEqual(response["Content-Type"], "text/csv")

        # Read the streaming content and decode it
        content = b"".join(response.streaming_content).decode("utf-8")

        # Create a CSV reader from the CSV content
        reader = csv.DictReader(StringIO(content))

        db_media_ids = set(
            Item.objects.filter(
                Q(tv__user=self.user)
                | Q(movie__user=self.user)
                | Q(season__user=self.user)
                | Q(episode__related_season__user=self.user)
                | Q(anime__user=self.user)
                | Q(manga__user=self.user)
                | Q(game__user=self.user)
                | Q(book__user=self.user),
            ).values_list("media_id", flat=True),
        )

        # Verify each row in the CSV exists in the database
        for row in reader:
            media_id = row["media_id"]
            self.assertIn(media_id, db_media_ids)
