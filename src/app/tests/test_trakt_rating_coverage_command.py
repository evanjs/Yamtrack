import json
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from app.models import TV, Item, MediaTypes, Movie, Sources


class TraktRatingCoverageCommandTests(TestCase):
    """Test the read-only Trakt external ratings coverage command."""

    def setUp(self):
        """Create one tracked movie and one tracked show for sampling."""
        self.user = get_user_model().objects.create_user(username="coverage-test")
        movie = Item.objects.create(
            media_id="101",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Example Movie",
            image="https://example.com/movie.jpg",
        )
        show = Item.objects.create(
            media_id="202",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Example Show",
            image="https://example.com/show.jpg",
        )
        Movie.objects.bulk_create([Movie(user=self.user, item=movie)])
        TV.objects.bulk_create([TV(user=self.user, item=show)])

    @patch("app.management.commands.check_trakt_rating_coverage.services.api_request")
    def test_command_resolves_tmdb_ids_and_reports_available_rating_sources(self, api):
        """The report counts non-null sources separately across media types."""
        def response(_provider, _method, url, *, params=None, headers=None):
            del params, headers
            if "/search/tmdb/101" in url:
                return [
                    {"type": "movie", "movie": {"ids": {"tmdb": 101, "trakt": 501}}},
                ]
            if "/search/tmdb/202" in url:
                return [
                    {"type": "show", "show": {"ids": {"tmdb": 202, "trakt": 502}}},
                ]
            if "/movies/501/ratings" in url:
                return {
                    "rating": 8.0,
                    "rotten_tomatoes": {"rating": 96, "user_rating": 90},
                    "imdb": {"rating": 8.5},
                    "letterboxd": {"rating": None},
                }
            if "/shows/502/ratings" in url:
                return {
                    "rating": 7.0,
                    "tmdb": {"rating": 7.5},
                    "rotten_tomatoes": {"rating": None, "user_rating": 82},
                    "mal": {"rating": None},
                }
            self.fail(f"Unexpected Trakt URL: {url}")
            return None

        api.side_effect = response
        output = StringIO()

        call_command(
            "check_trakt_rating_coverage",
            username=self.user.username,
            limit=10,
            stdout=output,
        )

        report = json.loads(output.getvalue())
        movie = report["media_types"]["movie"]
        show = report["media_types"]["show"]
        self.assertEqual(movie["sampled"], 1)
        self.assertEqual(show["sampled"], 1)
        self.assertEqual(movie["matched"], 1)
        self.assertEqual(show["matched"], 1)
        self.assertEqual(movie["sources"]["rotten_tomatoes_critic"]["available"], 1)
        self.assertEqual(movie["sources"]["rotten_tomatoes_audience"]["available"], 1)
        self.assertEqual(show["sources"]["rotten_tomatoes_critic"]["available"], 0)
        self.assertEqual(show["sources"]["rotten_tomatoes_audience"]["available"], 1)
        self.assertEqual(movie["sources"]["imdb"]["available"], 1)
        self.assertEqual(show["sources"]["tmdb"]["available"], 1)
        self.assertEqual(api.call_count, 4)
