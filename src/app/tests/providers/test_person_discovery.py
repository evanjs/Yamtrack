from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from app.providers import tmdb


class TMDBPersonDiscoveryTests(TestCase):
    """Test provider-backed TMDB person discovery."""

    def setUp(self):
        """Clear provider response cache between tests."""
        cache.clear()

    @patch("app.providers.services.api_request")
    def test_search_people_normalizes_person_choices(self, mock_api_request):
        """Normalize the provider person response for the picker."""
        mock_api_request.return_value = {
            "results": [
                {
                    "id": 123,
                    "name": "Jordan Peele",
                    "known_for_department": "Directing",
                    "profile_path": "/jordan.jpg",
                },
            ],
        }

        results = tmdb.search_people("Jordan Peele")

        self.assertEqual(
            results,
            [
                {
                    "person_id": 123,
                    "name": "Jordan Peele",
                    "known_for": "Directing",
                    "image": "https://image.tmdb.org/t/p/w500/jordan.jpg",
                },
            ],
        )
        args, kwargs = mock_api_request.call_args
        self.assertEqual(args[:2], ("tmdb", "GET"))
        self.assertTrue(args[2].endswith("/search/person"))
        self.assertEqual(kwargs["params"]["query"], "Jordan Peele")

    @patch("app.providers.services.api_request")
    def test_person_movie_credits_filters_role_and_labels_cast(self, mock_api_request):
        """Filter credits by exact role and show cast character labels."""
        mock_api_request.return_value = {
            "cast": [
                {
                    "id": 1,
                    "title": "Get Out",
                    "release_date": "2017-02-24",
                    "poster_path": "/get-out.jpg",
                    "character": "Rod Williams",
                },
            ],
            "crew": [
                {
                    "id": 2,
                    "title": "Nope",
                    "release_date": "2022-07-22",
                    "poster_path": "/nope.jpg",
                    "job": "Director",
                },
            ],
        }

        director_results = tmdb.person_movie_credits("123", "director")
        cast_results = tmdb.person_movie_credits("123", "cast")

        self.assertEqual([result["title"] for result in director_results], ["Nope"])
        self.assertEqual(director_results[0]["role"], "Director")
        self.assertEqual([result["title"] for result in cast_results], ["Get Out"])
        self.assertEqual(cast_results[0]["role"], "Rod Williams")
        self.assertEqual(mock_api_request.call_count, 1)
        requested_url = mock_api_request.call_args.args[2]
        self.assertTrue(requested_url.endswith("/person/123/movie_credits"))

    @patch("app.providers.services.api_request")
    def test_person_birthday_is_cached_and_normalized(self, mock_api_request):
        """Fetch a person's birthday once and reuse the cached value."""
        mock_api_request.return_value = {"birthday": "1958-05-27"}

        self.assertEqual(tmdb.person_birthday("123"), "1958-05-27")
        self.assertEqual(tmdb.person_birthday("123"), "1958-05-27")

        self.assertEqual(mock_api_request.call_count, 1)
        self.assertTrue(mock_api_request.call_args.args[2].endswith("/person/123"))

    @patch("app.providers.services.api_request")
    def test_movie_credit_sort_orders_all_directions(self, mock_api_request):
        """Sort credits by title or release date, keeping unknown dates last."""
        mock_api_request.return_value = {
            "cast": [
                {"id": 1, "title": "Zebra", "release_date": "1980-01-01"},
                {"id": 2, "title": "Alpha", "release_date": "2000-01-01"},
                {"id": 3, "title": "No Date", "release_date": ""},
            ],
            "crew": [],
        }

        title_order = tmdb.person_movie_credits("123", "all", "title_asc")
        reverse_title_order = tmdb.person_movie_credits("123", "all", "title_desc")
        date_order = tmdb.person_movie_credits("123", "all", "release_asc")
        reverse_date_order = tmdb.person_movie_credits("123", "all", "release_desc")
        self.assertEqual(
            [credit["title"] for credit in title_order],
            ["Alpha", "No Date", "Zebra"],
        )
        self.assertEqual(
            [credit["title"] for credit in reverse_title_order],
            ["Zebra", "No Date", "Alpha"],
        )
        self.assertEqual(
            [credit["title"] for credit in date_order],
            ["Zebra", "Alpha", "No Date"],
        )
        self.assertEqual(
            [credit["title"] for credit in reverse_date_order],
            ["Alpha", "Zebra", "No Date"],
        )
        self.assertEqual(mock_api_request.call_count, 1)

    def test_person_movie_credits_rejects_invalid_provider_ids(self):
        """Reject invalid person IDs before making an upstream request."""
        with self.assertRaises(ValueError):
            tmdb.person_movie_credits("not-an-id")

    @patch("app.providers.services.api_request")
    def test_person_tv_credits_groups_and_filters_cast_and_crew(self, mock_api_request):
        """Normalize TMDB TV credits and filter by their cast/crew roles."""
        mock_api_request.return_value = {
            "cast": [
                {
                    "id": 1668,
                    "name": "Friends",
                    "first_air_date": "1994-09-22",
                    "poster_path": "/friends.jpg",
                    "character": "Chandler Bing",
                },
            ],
            "crew": [
                {
                    "id": 1668,
                    "name": "Friends",
                    "first_air_date": "1994-09-22",
                    "poster_path": "/friends.jpg",
                    "job": "Executive Producer",
                },
            ],
        }

        results = tmdb.person_tv_credits("123", "all")
        crew_results = tmdb.person_tv_credits("123", "crew")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Friends")
        self.assertEqual(results[0]["role"], "Chandler Bing · Executive Producer")
        self.assertEqual(crew_results[0]["role"], "Executive Producer")
        self.assertEqual(mock_api_request.call_count, 1)
        self.assertTrue(
            mock_api_request.call_args.args[2].endswith("/person/123/tv_credits"),
        )
