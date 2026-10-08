import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from app.metadata import (
    store_igdb_game_taxonomies,
    store_tmdb_movie_taxonomies,
)
from app.models import (
    TV,
    Anime,
    Episode,
    IGDBGameMetadata,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
    TMDBMovieMetadata,
)


class CreateMedia(TestCase):
    """Test the creation of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.external_credentials = {"username": "test2", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.external_user = get_user_model().objects.create_user(
            **self.external_credentials
        )
        self.client.login(**self.credentials)

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_anime(self):
        """Test the creation of a TV object."""
        Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "1",
                "source": Sources.MAL.value,
                "media_type": MediaTypes.ANIME.value,
                "status": Status.PLANNING.value,
                "progress": 0,
                "repeats": 0,
            },
        )
        self.assertEqual(
            Anime.objects.filter(item__media_id="1", user=self.user).exists(),
            True,
        )

    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_tv(self):
        """Test the creation of a TV object through views."""
        Item.objects.create(
            media_id="5895",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "5895",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "status": Status.PLANNING.value,
            },
        )
        self.assertEqual(
            TV.objects.filter(item__media_id="5895", user=self.user).exists(),
            True,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_create_igdb_game_persists_taxonomies(self, mock_get_metadata):
        """Newly tracked IGDB games save their taxonomy metadata."""
        mock_get_metadata.return_value = {
            "title": "Example Game",
            "image": "https://example.com/cover.jpg",
            "max_progress": None,
            "igdb_taxonomies": {
                "genres": [{"id": 4, "name": "Fighting"}],
                "themes": [],
                "keywords": [{"id": 99, "name": "co-op"}],
            },
            "igdb_updated_at": None,
        }

        self.client.post(
            reverse("media_save"),
            {
                "media_id": "123",
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "status": Status.PLANNING.value,
                "progress": "",
            },
        )

        metadata = IGDBGameMetadata.objects.get(item__media_id="123")
        self.assertCountEqual(
            metadata.taxonomies.values_list("name", flat=True),
            ["Fighting", "co-op"],
        )

    @patch("app.providers.services.get_media_metadata")
    def test_create_tmdb_movie_persists_taxonomies(self, mock_get_metadata):
        """Newly tracked TMDB movies save genre and keyword metadata."""
        mock_get_metadata.return_value = {
            "title": "Example Movie",
            "image": "https://example.com/poster.jpg",
            "max_progress": 1,
            "tmdb_taxonomies": {
                "genres": [{"id": 27, "name": "Horror"}],
                "keywords": [{"id": 1852, "name": "haunting"}],
            },
        }

        self.client.post(
            reverse("media_save"),
            {
                "media_id": "321",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": Status.PLANNING.value,
                "progress": "",
            },
        )

        metadata = TMDBMovieMetadata.objects.get(item__media_id="321")
        self.assertCountEqual(
            metadata.taxonomies.values_list("name", flat=True),
            ["Horror", "haunting"],
        )

    def test_create_season(self):
        """Test the creation of a Season through views."""
        Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 1,
                "status": Status.PLANNING.value,
            },
        )
        self.assertEqual(
            Season.objects.filter(item__media_id="1668", user=self.user).exists(),
            True,
        )

    def test_create_episodes(self):
        """Test the creation of Episode through views."""
        self.client.post(
            reverse("episode_save"),
            {
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 1,
                "source": Sources.TMDB.value,
                "date": "2023-06-01T00:00",
            },
        )
        self.assertEqual(
            Episode.objects.filter(
                item__media_id="1668",
                related_season__user=self.user,
                item__episode_number=1,
            ).exists(),
            True,
        )


class EditMedia(TestCase):
    """Test the editing of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.external_credentials = {"username": "test2", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.external_user = get_user_model().objects.create_user(
            **self.external_credentials
        )
        self.client.login(**self.credentials)

    def test_edit_movie_score(self):
        """Test the editing of a movie score."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            score=9,
            progress=1,
            status=Status.COMPLETED.value,
            notes="Nice",
            start_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )

        self.client.post(
            reverse("media_save"),
            {
                "instance_id": movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 10,
                "progress": 1,
                "status": Status.COMPLETED.value,
                "notes": "Nice",
            },
        )
        self.assertEqual(Movie.objects.get(item__media_id="10494").score, 10)

    def test_cannot_edit_another_users_media(self):
        """Test users cannot edit another user's media by instance ID."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.external_user,
            score=9,
            progress=0,
            status=Status.PLANNING.value,
            notes="Nice",
        )

        response = self.client.post(
            reverse("media_save"),
            {
                "instance_id": movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 10,
                "progress": 0,
                "status": Status.PLANNING.value,
                "notes": "Changed",
            },
        )

        self.assertEqual(response.status_code, 404)
        movie.refresh_from_db()
        self.assertEqual(movie.score, 9)
        self.assertEqual(movie.notes, "Nice")

    def test_cannot_update_another_users_media_score(self):
        """Test users cannot update another user's score by instance ID."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.external_user,
            score=9,
            progress=0,
            status=Status.PLANNING.value,
        )

        response = self.client.post(
            reverse(
                "update_media_score",
                kwargs={
                    "media_type": MediaTypes.MOVIE.value,
                    "instance_id": movie.id,
                },
            ),
            {"score": 10},
        )

        self.assertEqual(response.status_code, 404)
        movie.refresh_from_db()
        self.assertEqual(movie.score, 9)


class DeleteMedia(TestCase):
    """Test the deletion of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.external_credentials = {"username": "test2", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.external_user = get_user_model().objects.create_user(
            **self.external_credentials
        )
        self.client.login(**self.credentials)

        self.item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.season = Season.objects.create(
            item=self.item_season,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        self.item_ep = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        self.episode = Episode.objects.create(
            item=self.item_ep,
            related_season=self.season,
            end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )

    def test_delete_tv(self):
        """Test the deletion of a tv through views."""
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        tv_obj = TV.objects.get(user=self.user)

        self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": tv_obj.id,
                "media_type": MediaTypes.TV.value,
            },
        )

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 0)

    def test_delete_season(self):
        """Test the deletion of a season through views."""
        self.client.post(
            reverse(
                "media_delete",
            ),
            data={"instance_id": self.season.id, "media_type": MediaTypes.SEASON.value},
        )

        self.assertEqual(Season.objects.filter(user=self.user).count(), 0)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )

    def test_cannot_delete_another_users_media(self):
        """Test users cannot delete another user's media by instance ID."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.external_user,
            progress=0,
            status=Status.PLANNING.value,
        )

        response = self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": movie.id,
                "media_type": MediaTypes.MOVIE.value,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Movie.objects.filter(id=movie.id).exists())

    def test_unwatch_episode(self):
        """Test unwatching of an episode through views."""
        self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": self.episode.id,
                "media_type": MediaTypes.EPISODE.value,
            },
        )

        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )


class SyncIGDBMetadata(TestCase):
    """Test persisting IGDB taxonomy on explicit provider refresh."""

    def setUp(self):
        """Create a logged-in user and an existing IGDB game."""
        self.user = get_user_model().objects.create_user(username="sync-igdb")
        self.client.force_login(self.user)
        self.item = Item.objects.create(
            media_id="123",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Example Game",
            image="https://example.com/old.jpg",
        )
        store_igdb_game_taxonomies(
            self.item,
            {
                "genres": [{"id": 4, "name": "Fighting"}],
                "themes": [],
                "keywords": [],
            },
        )

    @patch("app.models.Item.fetch_releases")
    @patch("app.views.cache.delete")
    @patch("app.views.cache.ttl", return_value=0)
    @patch("app.views.services.get_media_metadata")
    def test_successful_refresh_replaces_persisted_taxonomies(
        self,
        mock_get_metadata,
        _mock_cache_ttl,
        _mock_cache_delete,
        _mock_fetch_releases,
    ):
        """Explicit IGDB refresh replaces old facets after provider success."""
        mock_get_metadata.return_value = {
            "title": "Example Game Updated",
            "image": "https://example.com/new.jpg",
            "igdb_taxonomies": {
                "genres": [],
                "themes": [{"id": 17, "name": "Fantasy"}],
                "keywords": [{"id": 99, "name": "co-op"}],
            },
            "igdb_updated_at": None,
        }

        self.client.post(
            reverse(
                "sync_metadata",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "123",
                },
            ),
            {"next": "/"},
        )

        metadata = IGDBGameMetadata.objects.get(item=self.item)
        self.assertCountEqual(
            metadata.taxonomies.values_list("name", flat=True),
            ["Fantasy", "co-op"],
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.title, "Example Game Updated")

    @patch("app.models.Item.fetch_releases")
    @patch("app.views.cache.delete")
    @patch("app.views.cache.ttl", return_value=0)
    @patch("app.views.services.get_media_metadata")
    def test_tmdb_movie_refresh_replaces_persisted_taxonomies(
        self,
        mock_get_metadata,
        _mock_cache_ttl,
        _mock_cache_delete,
        _mock_fetch_releases,
    ):
        """Explicit TMDB refresh replaces stored genres and keywords."""
        movie_item = Item.objects.create(
            media_id="321",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Example Movie",
            image="https://example.com/poster.jpg",
        )
        store_tmdb_movie_taxonomies(
            movie_item,
            {"genres": [{"id": 27, "name": "Horror"}], "keywords": []},
        )
        mock_get_metadata.return_value = {
            "title": "Example Movie Updated",
            "image": "https://example.com/new-poster.jpg",
            "tmdb_taxonomies": {
                "genres": [],
                "keywords": [{"id": 1852, "name": "haunting"}],
            },
        }

        self.client.post(
            reverse(
                "sync_metadata",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "321",
                },
            ),
            {"next": "/"},
        )

        self.assertEqual(
            list(
                movie_item.tmdb_movie_metadata.taxonomies.values_list(
                    "name",
                    flat=True,
                ),
            ),
            ["haunting"],
        )
