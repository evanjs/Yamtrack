from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app import config
from app.models import (
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
    SteamAppMapping,
    SteamAppRating,
)


class MediaDetailsViewTests(TestCase):
    """Test the media details views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_view(self, mock_get_metadata):
        """Test the media details view."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
            "screenshots": [
                "https://images.igdb.com/igdb/image/upload/t_screenshot_big/one.jpg",
                "https://images.igdb.com/igdb/image/upload/t_screenshot_big/two.jpg",
            ],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")

        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Test Movie")
        self.assertContains(response, "Gameplay screenshots")
        self.assertContains(response, 'aria-label="Next screenshot"')
        self.assertContains(response, "t_screenshot_big/one.jpg")

        mock_get_metadata.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "238",
            Sources.TMDB.value,
        )

    @patch(
        "app.providers.protondb.summary",
        return_value={
            "tier": "Gold",
            "banner_color": "#c8b33f",
            "trending_tier": "Gold",
        },
    )
    @patch("app.providers.services.get_media_metadata")
    def test_game_details_show_attributed_protondb_summary(
        self,
        mock_get_metadata,
        mock_protondb_summary,
    ):
        """Show the cached, source-attributed summary on mapped game details."""
        mock_get_metadata.return_value = {
            "media_id": "777",
            "title": "Rated Game",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "https://example.com/game.jpg",
            "genres": [],
            "screenshots": [],
            "external_links": {
                "ProtonDB": "https://www.protondb.com/app/292030",
            },
            "steam_app_id": "292030",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "777",
                    "title": "rated-game",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["protondb_summary"],
            {
                "tier": "Gold",
                "banner_color": "#c8b33f",
                "trending_tier": "Gold",
            },
        )
        self.assertContains(response, "ProtonDB compatibility")
        self.assertContains(response, ">Compatibility</h2>")
        self.assertContains(response, ">ProtonDB</p>")
        self.assertContains(response, 'class="uppercase">Gold</span>')
        self.assertContains(response, "Trending tier: Gold")
        self.assertContains(response, 'href="https://www.protondb.com/app/292030"')
        mock_protondb_summary.assert_called_once_with("292030")

    @patch("app.providers.services.get_media_metadata")
    def test_game_details_show_selected_persisted_steam_rating(self, mock_get_metadata):
        """Game detail reads the selected rating from SQL without a provider call."""
        item = Item.objects.create(
            media_id="777",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Rated Game",
            image="https://example.com/game.jpg",
        )
        SteamAppMapping.objects.create(item=item, app_ids=["292030"])
        SteamAppRating.objects.create(
            item=item,
            steam_app_id="292030",
            ratings={"esrb": {"rating": "m"}},
        )
        self.user.steam_rating_agency = "esrb"
        self.user.save(update_fields=["steam_rating_agency"])
        mock_get_metadata.return_value = {
            "media_id": "777",
            "title": "Rated Game",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "https://example.com/game.jpg",
            "genres": [],
            "screenshots": [],
            "age_ratings": [
                {
                    "organization": "ESRB",
                    "rating": "M",
                    "rating_cover_url": "https://images.example/esrb-m.png",
                    "content_descriptions": ["Blood and Gore"],
                    "synopsis": "Mature content.",
                },
            ],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "777",
                    "title": "rated-game",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["steam_rating"], "M")
        self.assertContains(response, "ESRB")
        self.assertContains(response, "Age rating for: ESRB")
        self.assertContains(response, 'src="https://images.example/esrb-m.png"')
        self.assertContains(response, "Blood and Gore")

    @patch("app.providers.services.get_media_metadata")
    def test_game_details_fall_back_to_text_without_matching_igdb_art(
        self,
        mock_get_metadata,
    ):
        """Keep the selected Steam rating visible when IGDB has no matching art."""
        item = Item.objects.create(
            media_id="778",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Unmatched Rating Game",
            image="https://example.com/game.jpg",
        )
        SteamAppMapping.objects.create(item=item, app_ids=["570"])
        SteamAppRating.objects.create(
            item=item,
            steam_app_id="570",
            ratings={"esrb": {"rating": "m"}},
        )
        self.user.steam_rating_agency = "esrb"
        self.user.save(update_fields=["steam_rating_agency"])
        mock_get_metadata.return_value = {
            "media_id": "778",
            "title": "Unmatched Rating Game",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "https://example.com/game.jpg",
            "genres": [],
            "screenshots": [],
            "age_ratings": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "778",
                    "title": "unmatched-rating-game",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["steam_rating_art"])
        self.assertContains(response, "ESRB")
        self.assertContains(response, "M")
        self.assertNotContains(response, "images.example")

    @patch("app.providers.services.get_media_metadata")
    def test_game_details_use_local_icon_when_igdb_cover_url_is_empty(
        self,
        mock_get_metadata,
    ):
        """Use the locally bundled exact-match icon when IGDB's URL is null."""
        item = Item.objects.create(
            media_id="779",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Phantasmagoria",
            image="https://example.com/game.jpg",
        )
        SteamAppMapping.objects.create(item=item, app_ids=["501990"])
        SteamAppRating.objects.create(
            item=item,
            steam_app_id="501990",
            ratings={
                "esrb": {
                    "rating": "m",
                    "descriptors": "Blood and Gore\\r\\nStrong Sexual Content",
                },
            },
        )
        self.user.steam_rating_agency = "esrb"
        self.user.save(update_fields=["steam_rating_agency"])
        mock_get_metadata.return_value = {
            "media_id": "779",
            "title": "Phantasmagoria",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "https://example.com/game.jpg",
            "genres": [],
            "screenshots": [],
            "age_ratings": [
                {
                    "organization": "ESRB",
                    "rating": "M",
                    "rating_cover_url": None,
                    "content_descriptions": ["Blood and Gore"],
                    "synopsis": "",
                },
            ],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "779",
                    "title": "phantasmagoria",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["steam_rating_icon"],
            "img/ratings/esrb/mature.svg",
        )
        self.assertContains(response, "img/ratings/esrb/mature.svg")
        self.assertContains(response, "Blood and Gore")

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_view(self, mock_process_episodes, mock_get_metadata):
        """Test the season details view."""
        self.user.obfuscate_unseen_episodes = True
        self.user.obfuscate_unseen_seasons = True
        self.user.save(
            update_fields=["obfuscate_unseen_episodes", "obfuscate_unseen_seasons"]
        )

        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "related": {
                "seasons": [
                    {
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.SEASON.value,
                        "media_id": "1668",
                        "title": "Test TV Show",
                        "season_title": "Coven",
                        "season_number": 1,
                        "image": "http://example.com/season.jpg",
                        "max_progress": 10,
                    },
                ],
            },
            "season/1": {
                "title": "Season 1",
                "season_title": "Coven",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "season_number": 1,
                "image": "http://example.com/season.jpg",
                "related": {
                    "seasons": [
                        {
                            "source": Sources.TMDB.value,
                            "media_type": MediaTypes.SEASON.value,
                            "media_id": "1668",
                            "title": "Test TV Show",
                            "season_title": "Coven",
                            "season_number": 1,
                            "image": "http://example.com/season.jpg",
                            "max_progress": 10,
                        },
                    ],
                },
                "episodes": [],
            },
        }

        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "title": "Episode 1",
                "name": "Episode 1",
                "air_date": "2023-01-01",
                "watched": False,
            },
        ]

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")

        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Season 1")
        self.assertEqual(len(response.context["media"]["episodes"]), 1)
        self.assertContains(response, "line-clamp-1 blur cursor-pointer")
        self.assertContains(response, "Season 1")
        self.assertContains(response, 'x-show="seasonRevealed" x-cloak>Coven</span>')
        self.assertContains(response, 'aria-label="Reveal season details"')
        self.assertNotContains(response, "10 Episodes")

        mock_get_metadata.assert_called_once_with(
            "tv_with_seasons",
            "1668",
            Sources.TMDB.value,
            [1],
        )

    @patch("app.providers.services.get_media_metadata")
    def test_cast_card_keeps_tmdb_link_and_adds_yamtrack_search(
        self,
        mock_get_metadata,
    ):
        """Keep the main person link and offer a separate Yamtrack search."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "details": {"release_date": "1985-04-25"},
            "cast": [
                {
                    "id": 678,
                    "name": "Linnea Quigley",
                    "character": "Trash",
                    "image": "http://example.com/person.jpg",
                },
            ],
            "total_cast_count": 1,
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="https://www.themoviedb.org/person/678"')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, "Search Yamtrack")
        self.assertContains(response, 'hx-trigger="intersect once"')
        self.assertContains(
            response,
            'hx-get="/discover/person-age/678?release_date=1985-04-25"',
        )
        self.assertContains(
            response,
            'href="/discover/person?person_id=678&amp;person_name=Linnea%20Quigley&amp;role=cast&amp;media_type=movie"',
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_refreshes_missing_item_image(self, mock_get_metadata):
        """Item.image is updated when missing and live metadata has one."""
        live_image = "http://example.com/fresh.jpg"
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": live_image,
            "max_progress": 1,
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image=settings.IMG_NONE,
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.image, live_image)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_keeps_existing_item_image(self, mock_get_metadata):
        """Item.image is left alone when already set."""
        existing_image = "http://example.com/stored.jpg"
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/fresh.jpg",
            "max_progress": 1,
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image=existing_image,
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.image, existing_image)

    def _get_tracked_movie_details(self, mock_get_metadata, status):
        """Return the details page of a movie tracked with the given status."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(item=item, user=self.user, status=status)

        return self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

    @patch("app.providers.services.get_media_metadata")
    def test_track_button_uses_status_color(self, mock_get_metadata):
        """The track button is filled with the status color of the tracked media."""
        response = self._get_tracked_movie_details(
            mock_get_metadata,
            Status.PAUSED.value,
        )

        # main button and "more tracking options" button
        self.assertContains(
            response,
            config.get_status_button_color(Status.PAUSED.value),
            count=2,
        )
        self.assertNotContains(
            response,
            config.get_status_button_color(Status.IN_PROGRESS.value),
        )

    @patch("app.providers.services.get_media_metadata")
    def test_track_button_color_ignores_progress_bar_setting(self, mock_get_metadata):
        """The track button uses the status color with the progress bar off."""
        self.user.progress_bar = False
        self.user.save(update_fields=["progress_bar"])

        response = self._get_tracked_movie_details(
            mock_get_metadata,
            Status.PAUSED.value,
        )

        self.assertContains(
            response,
            config.get_status_button_color(Status.PAUSED.value),
            count=2,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_track_button_has_no_progress_bar(self, mock_get_metadata):
        """The track button does not render a progress bar."""
        response = self._get_tracked_movie_details(
            mock_get_metadata,
            Status.IN_PROGRESS.value,
        )

        self.assertNotContains(response, "h-1.25")
