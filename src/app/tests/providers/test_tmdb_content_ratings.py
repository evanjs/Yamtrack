from unittest.mock import patch

from django.test import SimpleTestCase

from app.models import MediaTypes, Sources
from app.providers import tmdb


class TMDBContentRatingProviderTests(SimpleTestCase):
    """Test TMDB content-rating endpoint and normalization contracts."""

    @patch("app.providers.tmdb.cache.set")
    @patch("app.providers.tmdb.cache.get", return_value=None)
    @patch("app.providers.tmdb.services.api_request")
    def test_fetches_tv_ratings_from_content_rating_endpoint(
        self,
        api_request,
        _cache_get,
        _cache_set,
    ):
        """The TV helper uses its dedicated content-rating endpoint."""
        api_request.return_value = {
            "results": [
                {"iso_3166_1": "US", "rating": " TV-MA "},
                {"iso_3166_1": "GB", "rating": "15"},
                {"iso_3166_1": "CA", "rating": ""},
            ],
        }

        ratings = tmdb.content_ratings(MediaTypes.TV.value, "654")

        self.assertEqual(
            ratings,
            {"US": [{"rating": "TV-MA"}], "GB": [{"rating": "15"}]},
        )
        api_request.assert_called_once_with(
            Sources.TMDB.value,
            "GET",
            f"{tmdb.base_url}/tv/654/content_ratings",
            params=tmdb.base_params,
        )

    def test_movie_certifications_preserve_release_context_and_ignore_empty(self):
        """Movie records retain release context while omitting empty labels."""
        ratings = tmdb.get_movie_certifications(
            {
                "results": [
                    {
                        "iso_3166_1": "US",
                        "release_dates": [
                            {
                                "certification": " R ",
                                "type": 3,
                                "release_date": "2024-01-01",
                            },
                            {"certification": "", "type": 3, "release_date": ""},
                        ],
                    },
                ],
            },
        )

        self.assertEqual(
            ratings,
            {"US": [{"certification": "R", "type": 3, "release_date": "2024-01-01"}]},
        )

    def test_tv_normalizer_ignores_missing_and_blank_ratings(self):
        """Missing and blank TV ratings do not produce display entries."""
        self.assertEqual(
            tmdb.get_tv_content_ratings(
                {
                    "results": [
                        {"iso_3166_1": "US"},
                        {"iso_3166_1": "CA", "rating": " "},
                    ]
                },
            ),
            {},
        )
