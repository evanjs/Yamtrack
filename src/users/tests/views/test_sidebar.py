from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from app.models import MediaTypes
from users.models import DefaultEditEntryDateChoices, WeekStartDayChoices


class SidebarViewTests(TestCase):
    """Tests for the sidebar view."""

    def setUp(self):
        """Create user for the tests."""
        self.watch_regions_patcher = patch(
            "users.views.tmdb.watch_provider_regions",
            return_value=[("UNSET", "Disabled"), ("US", "United States")],
        )
        self.watch_regions_patcher.start()
        self.addCleanup(self.watch_regions_patcher.stop)

        self.credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_preferences_get(self):
        """Test GET request to preferences view."""
        response = self.client.get(reverse("preferences"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/preferences.html")

        self.assertIn("media_types", response.context)
        self.assertIn(MediaTypes.TV.value, response.context["media_types"])
        self.assertIn(MediaTypes.MOVIE.value, response.context["media_types"])
        self.assertNotIn(MediaTypes.EPISODE.value, response.context["media_types"])
        self.assertContains(
            response,
            "When excluding tags, include items with unknown tags?",
        )
        self.assertContains(response, "Steam rating system")
        self.assertContains(response, 'name="steam_rating_agency"')
        self.assertContains(response, "TMDB content-rating region")
        self.assertContains(response, 'name="tmdb_content_rating_region"')
        self.assertNotContains(response, "img/tmdb-logo.svg")
        self.assertNotContains(
            response,
            "This product uses TMDB and the TMDB APIs",
        )

    def test_about_page_contains_provider_attributions(self):
        """Provider credits live in About rather than the global page footer."""
        response = self.client.get(reverse("about"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Movie and TV streaming providers from")
        self.assertContains(response, "JustWatch")
        self.assertContains(
            response,
            (
                "This product uses TMDB and the TMDB APIs but is not endorsed, "
                "certified, or otherwise approved by TMDB."
            ),
        )
        self.assertContains(response, "img/tmdb-logo.svg")

    def test_unknown_taxonomy_preference_can_be_enabled(self):
        """The preferences form stores the opt-in for unknown taxonomy results."""
        response = self.client.post(
            reverse("preferences"),
            {
                "include_unknown_taxonomy": "on",
                "media_types_checkboxes": [MediaTypes.TV.value],
            },
        )

        self.assertRedirects(response, reverse("preferences"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.include_unknown_taxonomy)

    def test_steam_rating_agency_preference_is_independent_and_optional(self):
        """The user can select one Steam system without setting a country."""
        self.assertEqual(self.user.steam_rating_agency, "UNSET")
        response = self.client.post(
            reverse("preferences"),
            {
                "steam_rating_agency": "esrb",
                "media_types_checkboxes": [MediaTypes.GAME.value],
            },
        )

        self.assertRedirects(response, reverse("preferences"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.steam_rating_agency, "esrb")

    def test_invalid_steam_rating_agency_disables_the_badge(self):
        """Unknown preference values fail closed to the unset option."""
        response = self.client.post(
            reverse("preferences"),
            {
                "steam_rating_agency": "unknown_provider_key",
                "media_types_checkboxes": [MediaTypes.GAME.value],
            },
        )

        self.assertRedirects(response, reverse("preferences"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.steam_rating_agency, "UNSET")

    def test_tmdb_rating_region_is_independent_from_watch_provider_region(self):
        """TMDB rating preference saves a region without coupling streaming."""
        self.client.post(
            reverse("preferences"),
            {
                "tmdb_content_rating_region": "US",
                "watch_provider_region": "UNSET",
                "media_types_checkboxes": [MediaTypes.MOVIE.value],
            },
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.tmdb_content_rating_region, "US")
        self.assertEqual(self.user.watch_provider_region, "UNSET")

    def test_invalid_tmdb_rating_region_disables_the_badge(self):
        """Unknown TMDB region preference values fail closed to unset."""
        self.client.post(
            reverse("preferences"),
            {
                "tmdb_content_rating_region": "INVALID",
                "media_types_checkboxes": [MediaTypes.MOVIE.value],
            },
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.tmdb_content_rating_region, "UNSET")

    def test_sidebar_post_update_preferences(self):
        """Test POST request to update preferences."""
        self.user.tv_enabled = True
        self.user.movie_enabled = True
        self.user.anime_enabled = True
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value, MediaTypes.ANIME.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.tv_enabled)
        self.assertFalse(self.user.movie_enabled)
        self.assertTrue(self.user.anime_enabled)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Settings updated", str(messages[0]))

    def test_sidebar_post_demo_user(self):
        """Test POST request from a demo user to preferences."""
        self.user.is_demo = True
        self.user.tv_enabled = True
        self.user.movie_enabled = False
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value, MediaTypes.MOVIE.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.tv_enabled)
        self.assertFalse(self.user.movie_enabled)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("view-only for demo accounts", str(messages[0]))

    def test_obfuscate_unseen_seasons_preferences_persist_and_clear(self):
        """Season obfuscation and its detail choices save as independent checkboxes."""
        self.assertFalse(self.user.obfuscate_unseen_seasons)
        self.assertTrue(self.user.obfuscate_unseen_season_titles)
        self.assertTrue(self.user.obfuscate_unseen_season_artwork)
        self.assertTrue(self.user.obfuscate_unseen_season_episode_counts)

        self.client.post(
            reverse("preferences"),
            {
                "obfuscate_unseen_seasons": "on",
                "obfuscate_unseen_season_titles": "on",
                "obfuscate_unseen_season_artwork": "on",
                "obfuscate_unseen_season_episode_counts": "on",
                "media_types_checkboxes": [MediaTypes.TV.value],
            },
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.obfuscate_unseen_seasons)

        self.client.post(
            reverse("preferences"),
            {"media_types_checkboxes": [MediaTypes.TV.value]},
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.obfuscate_unseen_seasons)
        self.assertFalse(self.user.obfuscate_unseen_season_titles)
        self.assertFalse(self.user.obfuscate_unseen_season_artwork)
        self.assertFalse(self.user.obfuscate_unseen_season_episode_counts)

    def test_auto_start_next_season_preference_can_be_disabled(self):
        """The automatic next-season preference defaults on and is persisted."""
        self.assertTrue(self.user.auto_start_next_season)
        self.assertContains(
            self.client.get(reverse("preferences")),
            "Automatically Start Next Season",
        )

        self.client.post(
            reverse("preferences"),
            {"media_types_checkboxes": [MediaTypes.TV.value]},
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.auto_start_next_season)

        self.client.post(
            reverse("preferences"),
            {
                "auto_start_next_season": "on",
                "media_types_checkboxes": [MediaTypes.TV.value],
            },
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.auto_start_next_season)

    def test_obfuscate_unseen_episodes_post_enable(self):
        """Test enabling obfuscate_unseen_episodes via preferences."""
        self.user.obfuscate_unseen_episodes = False
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "obfuscate_unseen_episodes": "on",
                "media_types_checkboxes": [MediaTypes.TV.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.obfuscate_unseen_episodes)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Settings updated", str(messages[0]))

    def test_obfuscate_unseen_episodes_post_disable(self):
        """Test disabling obfuscate_unseen_episodes via preferences."""
        self.user.obfuscate_unseen_episodes = True
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertFalse(self.user.obfuscate_unseen_episodes)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Settings updated", str(messages[0]))

    def test_clickable_media_cards_and_obfuscate_unseen_episodes(self):
        """Test updating both clickable_media_cards and obfuscate_unseen_episodes."""
        self.user.clickable_media_cards = False
        self.user.obfuscate_unseen_episodes = False
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "clickable_media_cards": "on",
                "obfuscate_unseen_episodes": "on",
                "media_types_checkboxes": [MediaTypes.TV.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.clickable_media_cards)
        self.assertTrue(self.user.obfuscate_unseen_episodes)

    def test_obfuscate_unseen_episodes_post_demo_user(self):
        """Test that demo users cannot update obfuscate_unseen_episodes."""
        self.user.is_demo = True
        self.user.obfuscate_unseen_episodes = False
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "obfuscate_unseen_episodes": "on",
                "media_types_checkboxes": [MediaTypes.TV.value],
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertFalse(self.user.obfuscate_unseen_episodes)

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("view-only for demo accounts", str(messages[0]))

    def test_post_updates_week_start_day_valid(self):
        """Posting a valid week_start_day updates the user preference."""
        self.assertEqual(self.user.week_start_day, WeekStartDayChoices.MONDAY)
        response = self.client.post(
            reverse("preferences"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value],
                "week_start_day": WeekStartDayChoices.SUNDAY,
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertEqual(self.user.week_start_day, WeekStartDayChoices.SUNDAY)

    def test_post_updates_default_edit_entry_date(self):
        """Posting no_date persists the edit entry date preference."""
        response = self.client.post(
            reverse("preferences"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value],
                "default_edit_entry_date": DefaultEditEntryDateChoices.NO_DATE,
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertEqual(
            self.user.default_edit_entry_date,
            DefaultEditEntryDateChoices.NO_DATE,
        )

    def test_post_ignores_invalid_week_start_day(self):
        """Posting an invalid week_start_day leaves the value unchanged."""
        self.user.week_start_day = WeekStartDayChoices.SUNDAY
        self.user.save()

        response = self.client.post(
            reverse("preferences"),
            {
                "media_types_checkboxes": [MediaTypes.TV.value],
                "week_start_day": "saturday",
            },
        )
        self.assertRedirects(response, reverse("preferences"))

        self.user.refresh_from_db()
        self.assertEqual(self.user.week_start_day, WeekStartDayChoices.SUNDAY)
