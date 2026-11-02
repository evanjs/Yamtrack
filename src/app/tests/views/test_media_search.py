from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import (
    MediaTypes,
    Sources,
)


class MediaSearchViewTests(TestCase):
    """Test the media search view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.services.search")
    def test_media_search_view(self, mock_search):
        """Test the media search view."""
        mock_search.return_value = {
            "page": 1,
            "total_results": 1,
            "total_pages": 1,
            "results": [
                {
                    "media_id": "238",
                    "title": "Test Movie",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "image": "http://example.com/image.jpg",
                },
            ],
        }

        response = self.client.get(
            reverse("search") + "?media_type=movie&q=test",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/search.html")

        self.user.refresh_from_db()
        self.assertEqual(self.user.last_search_type, MediaTypes.MOVIE.value)

        mock_search.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "test",
            1,
            Sources.TMDB.value,
            show_adult_titles=True,
        )

    @patch("app.providers.services.search")
    def test_media_search_view_hides_adult_titles(self, mock_search):
        """Test the media search view applies the user preference."""
        self.user.show_adult_titles = False
        self.user.save(update_fields=["show_adult_titles"])
        mock_search.return_value = {
            "page": 1,
            "total_results": 0,
            "total_pages": 1,
            "results": [],
        }

        response = self.client.get(
            reverse("search") + "?media_type=movie&q=test",
        )

        self.assertEqual(response.status_code, 200)
        mock_search.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "test",
            1,
            Sources.TMDB.value,
            show_adult_titles=False,
        )

    def test_media_search_view_anilist(self):
        """Test the media search view with AniList as the anime source."""
        response = self.client.get(
            reverse("search") + "?media_type=anime&q=Cowboy Bebop&source=anilist",
        )

        self.assertEqual(response.status_code, 200)
        results = response.context["data"]["results"]
        self.assertTrue(results)
        self.assertEqual(results[0]["item"]["source"], Sources.ANILIST.value)
        self.assertContains(response, Sources.ANILIST.label)

    @patch("app.providers.services.search")
    def test_anilist_genre_search(self, mock_search):
        """A genre link reaches the AniList catalog search as a facet."""
        mock_search.return_value = {
            "page": 1,
            "total_results": 0,
            "total_pages": 1,
            "results": [],
        }

        response = self.client.get(
            reverse("search") + "?media_type=anime&source=anilist&genre=Sci-Fi",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["genre"], "Sci-Fi")
        self.assertContains(response, "AniList genre")
        self.assertContains(response, "Sci-Fi")
        self.assertContains(response, "border-left: 4px solid #7c3aed")
        self.assertContains(response, "bg-violet-600 px-3 py-1")
        self.assertContains(response, "q=Sci-Fi&media_type=anime&source=mal")
        self.assertContains(response, "genre_text=Sci-Fi")
        mock_search.assert_called_once_with(
            MediaTypes.ANIME.value,
            "",
            1,
            Sources.ANILIST.value,
            show_adult_titles=True,
            genre="Sci-Fi",
        )

    @patch("app.providers.services.search")
    def test_genre_source_switch_is_labeled_as_text_search(self, mock_search):
        """Switching away from AniList genre search explains the text fallback."""
        mock_search.return_value = {
            "page": 1,
            "total_results": 0,
            "total_pages": 1,
            "results": [],
        }

        response = self.client.get(
            reverse("search")
            + "?media_type=manga&source=mangaupdates&q=Comedy&genre_text=Comedy",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["genre_text"], "Comedy")
        self.assertContains(response, "This is a text search, not a genre filter")
        self.assertContains(
            response,
            "/search?q=&media_type=manga&source=anilist&layout=grid&genre=Comedy",
        )
        mock_search.assert_called_once_with(
            MediaTypes.MANGA.value,
            "Comedy",
            1,
            Sources.MANGAUPDATES.value,
            show_adult_titles=True,
        )

    @patch("app.helpers.enrich_items_with_user_data")
    @patch("app.providers.services.search")
    def test_anilist_pagination_shows_range_without_total(
        self,
        mock_search,
        mock_enrich,
    ):
        """AniList pagination uses the known result range, not a guessed total."""
        results = [
            {
                "media_id": str(index),
                "title": f"Test Anime {index}",
                "media_type": MediaTypes.ANIME.value,
                "source": Sources.ANILIST.value,
                "image": "https://example.com/poster.jpg",
            }
            for index in range(1, 4)
        ]
        mock_search.return_value = {
            "page": 3,
            "total_results": 100,
            "total_pages": 6,
            "has_next_page": False,
            "results": results,
        }
        mock_enrich.return_value = [
            {"item": result, "media": None} for result in results
        ]

        response = self.client.get(
            reverse("search")
            + "?media_type=anime&source=anilist&q=test&page=3&layout=list",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["result_range_start"],
            settings.PER_PAGE * 2 + 1,
        )
        self.assertEqual(
            response.context["result_range_end"],
            settings.PER_PAGE * 2 + len(results),
        )
        self.assertContains(
            response,
            f"{settings.PER_PAGE * 2 + 1}\u2013{settings.PER_PAGE * 2 + len(results)}",
        )
        self.assertNotContains(response, "Page 3 of 6")
        self.assertContains(response, 'aria-label="First page"')
        self.assertContains(
            response,
            'href="?q=test&media_type=anime&source=anilist&page=1&layout=list"',
        )
