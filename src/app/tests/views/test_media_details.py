from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from app import config
from app.metadata import store_tmdb_content_ratings
from app.models import (
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
    SteamAppMapping,
    SteamAppRating,
    SteamDBHltbMapping,
)


class MediaDetailsViewTests(TestCase):
    """Test the media details views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @patch("app.views.refresh_tmdb_content_ratings_for_items.delay")
    @patch("app.providers.services.get_media_metadata")
    def test_untracked_tmdb_detail_visit_persists_embedded_rating(
        self, mock_get_metadata, mock_refresh
    ):
        """An embedded certification is saved instead of re-fetched."""
        self.user.tmdb_content_rating_region = "US"
        self.user.save(update_fields=["tmdb_content_rating_region"])
        mock_get_metadata.return_value = {
            "media_id": "7654",
            "title": "Untracked Movie",
            "media_type": "movie",
            "source": "tmdb",
            "image": "https://example.com/untracked.jpg",
            "tmdb_movie_ratings": {
                "US": [{"certification": "PG-13", "type": 3}]
            },
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": "tmdb",
                    "media_type": "movie",
                    "media_id": "7654",
                    "title": "untracked-movie",
                },
            )
        )
        item = Item.objects.get(source="tmdb", media_type="movie", media_id="7654")
        self.assertEqual(
            item.tmdb_content_rating_metadata.ratings,
            {"US": [{"certification": "PG-13", "type": 3}]},
        )
        self.assertContains(response, 'hx-trigger="load"')
        rating_response = self.client.get(
            reverse(
                "tmdb_content_rating",
                kwargs={
                    "source": "tmdb",
                    "media_type": "movie",
                    "media_id": "7654",
                },
            )
        )
        self.assertContains(rating_response, "US · PG-13")
        mock_refresh.assert_not_called()
        self.assertFalse(Movie.objects.filter(item=item, user=self.user).exists())

    @patch("app.views.refresh_tmdb_content_ratings_for_items.delay")
    @patch("app.providers.services.get_media_metadata")
    def test_tmdb_content_rating_fragment_queues_missing_rating_and_renders_loading(
        self, mock_get_metadata, mock_refresh
    ):
        """Queue a single background fetch while the badge is polling."""
        item = Item.objects.create(
            media_id="238",
            source="tmdb",
            media_type="movie",
            title="Test Movie",
            image="https://example.com/image.jpg",
        )
        self.user.tmdb_content_rating_region = "US"
        self.user.save(update_fields=["tmdb_content_rating_region"])
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": "movie",
            "source": "tmdb",
            "image": "https://example.com/image.jpg",
        }
        detail_response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": "tmdb",
                    "media_type": "movie",
                    "media_id": "238",
                    "title": "test-movie",
                },
            )
        )
        self.assertContains(detail_response, 'hx-trigger="load"')
        self.assertNotContains(detail_response, 'hx-trigger="every 3s"')
        response = self.client.get(
            reverse(
                "tmdb_content_rating",
                kwargs={
                    "source": "tmdb",
                    "media_type": "movie",
                    "media_id": "238",
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Loading content rating")
        self.assertContains(response, 'hx-trigger="every 3s"')
        self.assertNotContains(response, 'hx-trigger="load, every 3s"')
        mock_refresh.assert_called_once_with([item.pk])
        self.client.get(
            reverse(
                "tmdb_content_rating",
                kwargs={
                    "source": "tmdb",
                    "media_type": "movie",
                    "media_id": "238",
                },
            )
        )
        mock_refresh.assert_called_once()

    @patch("app.views.refresh_tmdb_content_ratings_for_items.delay")
    def test_tmdb_content_rating_fragment_stops_polling_after_success(
        self, mock_refresh
    ):
        """Render a saved rating and stop polling once the fetch succeeds."""
        item = Item.objects.create(
            media_id="987",
            source="tmdb",
            media_type="movie",
            title="Rated Movie",
            image="https://example.com/image.jpg",
        )
        store_tmdb_content_ratings(
            item,
            {"US": [{"certification": "PG-13", "type": 3}]},
        )
        self.user.tmdb_content_rating_region = "US"
        self.user.save(update_fields=["tmdb_content_rating_region"])

        response = self.client.get(
            reverse(
                "tmdb_content_rating",
                kwargs={
                    "source": "tmdb",
                    "media_type": "movie",
                    "media_id": "987",
                },
            )
        )

        self.assertContains(response, "US · PG-13")
        self.assertNotContains(response, "hx-get=")
        mock_refresh.assert_not_called()

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

    @patch("app.views.tmdb.get_trakt_ratings")
    @patch("app.providers.services.get_media_metadata")
    def test_tmdb_movie_details_display_external_ratings(
        self,
        mock_get_metadata,
        mock_get_trakt_ratings,
    ):
        """Show available Trakt scores alongside TMDB's existing score."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "https://example.com/image.jpg",
            "overview": "Test overview",
            "genres": [],
            "external_links": {"Trakt": "https://trakt.tv/movies/test-movie"},
        }
        mock_get_trakt_ratings.return_value = [
            {
                "label": "Rotten Tomatoes critic",
                "rating": 96,
                "scale": 100,
                "link": "https://www.rottentomatoes.com/m/test_movie/reviews",
            },
            {
                "label": "Rotten Tomatoes audience",
                "rating": 92,
                "scale": 100,
                "link": "https://www.rottentomatoes.com/m/test_movie/reviews/all-audience",
            },
        ]

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
        self.assertEqual(
            response.context["external_ratings"],
            mock_get_trakt_ratings.return_value,
        )
        self.assertContains(response, "Rotten Tomatoes critic")
        self.assertContains(response, "Rotten Tomatoes audience")
        self.assertContains(response, "96")
        self.assertContains(response, "92")
        self.assertIn(
            '<a href="https://www.rottentomatoes.com/m/test_movie/reviews"',
            response.content.decode(),
        )
        self.assertContains(
            response,
            "grid-template-columns: repeat(auto-fit, minmax(min(100%, 6rem), 1fr))",
        )
        mock_get_trakt_ratings.assert_called_once_with(
            "238",
            "https://trakt.tv/movies/test-movie",
            MediaTypes.MOVIE.value,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_game_details_include_hltb_link_from_persisted_steamdb_mapping(
        self,
        mock_get_metadata,
    ):
        """Add the HLTB link in Details when the Steam AppID is mapped."""
        SteamDBHltbMapping.objects.create(
            steam_app_id="292030",
            hltb_id="71481",
            synced_at="2026-11-04T00:00:00Z",
        )
        mock_get_metadata.return_value = {
            "media_id": "777",
            "title": "Rated Game",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "https://example.com/game.jpg",
            "details": {"format": "Game"},
            "genres": [],
            "screenshots": [],
            "steam_app_id": "292030",
            "external_links": {"Steam": "https://store.steampowered.com/app/292030/"},
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

        expected = "https://howlongtobeat.com/game/71481"
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["media"]["external_links"]["HowLongToBeat"],
            expected,
        )
        self.assertContains(response, f'href="{expected}"')
        self.assertContains(response, ">HowLongToBeat</a>")
        self.assertNotIn(
            "HowLongToBeat",
            mock_get_metadata.return_value["external_links"],
        )

    @patch("app.providers.services.get_media_metadata")
    def test_game_details_omit_hltb_link_without_persisted_mapping(
        self,
        mock_get_metadata,
    ):
        """Do not fabricate an HLTB link when no AppID mapping is stored."""
        mock_get_metadata.return_value = {
            "media_id": "778",
            "title": "Unmapped Game",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "https://example.com/game.jpg",
            "details": {"format": "Game"},
            "genres": [],
            "screenshots": [],
            "steam_app_id": "999999",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "778",
                    "title": "unmapped-game",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            "HowLongToBeat",
            response.context["media"].get("external_links", {}),
        )
        self.assertNotContains(response, "howlongtobeat.com")

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
            "details": {"format": "Game"},
            "genres": [],
            "screenshots": [],
            "external_links": {
                "Steam": "https://store.steampowered.com/app/292030/",
                "Trakt": "https://trakt.tv/movies/rated-game-2024",
                "Hardcover": "https://hardcover.app/books/123",
                "IMDb": "https://www.imdb.com/title/tt1234567/",
                "Letterboxd": "https://letterboxd.com/film/example/",
                "MyAnimeList": "https://myanimelist.net/anime/123",
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
        rendered = response.content.decode()
        self.assertRegex(
            rendered,
            r'(?s)<a href="https://store\.steampowered\.com/app/292030/".*?target="_self".*?>Steam</a>',
        )
        self.assertRegex(
            rendered,
            r'(?s)<a href="https://trakt\.tv/movies/rated-game-2024".*?target="_self".*?>Trakt</a>',
        )
        for name, url in (
            ("Hardcover", "https://hardcover.app/books/123"),
            ("IMDb", "https://www.imdb.com/title/tt1234567/"),
            ("Letterboxd", "https://letterboxd.com/film/example/"),
            ("MyAnimeList", "https://myanimelist.net/anime/123"),
        ):
            with self.subTest(name=name):
                self.assertRegex(
                    rendered,
                    rf'(?s)<a href="{url}".*?target="_self".*?>{name}</a>',
                )
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
    def test_anilist_media_details_view(self):
        """Test the media details view for an anime from AniList."""
        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.ANILIST.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "1",
                    "title": "cowboy-bebop",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media"]["title"], "Cowboy Bebop")
        self.assertContains(response, "https://anilist.co/anime/1")
        self.assertContains(response, "https://myanimelist.net/anime/1")
        self.assertContains(response, "Related Manga")

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
