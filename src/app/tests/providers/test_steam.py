from unittest.mock import patch

from django.test import SimpleTestCase

from app.providers import steam


class SteamStoreRatingsTests(SimpleTestCase):
    """Test defensive parsing of Steam Store appdetails ratings."""

    @patch("app.providers.steam.services.api_request")
    def test_app_ratings_extracts_all_agencies_for_one_app(self, mock_request):
        """Return the complete agency object without normalizing its values."""
        ratings = {
            "esrb": {"rating": "m", "required_age": "17"},
            "steam_germany": {"rating": "16", "rating_generated": 1},
            "future_agency": {"rating": "example"},
        }
        mock_request.return_value = {
            "292030": {"success": True, "data": {"ratings": ratings}},
        }

        self.assertEqual(steam.app_ratings("292030"), ratings)
        mock_request.assert_called_once_with(
            "steam",
            "GET",
            "https://store.steampowered.com/api/appdetails",
            params={"appids": "292030", "l": "en"},
        )

    @patch("app.providers.steam.services.api_request")
    def test_unsuccessful_or_incomplete_response_is_not_empty_success(self, mock_request):
        """A false or malformed response raises instead of erasing last-good data."""
        for response in (
            {"292030": {"success": False}},
            {"292030": {"success": True, "data": {}}},
            {"292030": {"success": True, "data": {"ratings": None}}},
        ):
            with self.subTest(response=response):
                mock_request.return_value = response
                with self.assertRaises(ValueError):
                    steam.app_ratings("292030")

    def test_app_ratings_rejects_invalid_app_ids(self):
        """Only positive decimal Steam App IDs are accepted."""
        for app_id in ("", "0", "abc", True):
            with self.subTest(app_id=app_id), self.assertRaises(ValueError):
                steam.app_ratings(app_id)
