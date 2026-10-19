from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from app.providers import igdb


class IGDBSteamMappingTests(SimpleTestCase):
    """Test discovery of verified Steam App IDs for an IGDB game."""

    @override_settings(IGDB_ID="client-id")
    @patch("app.providers.igdb.services.api_request")
    @patch("app.providers.igdb.get_access_token", return_value="access-token")
    @patch("app.providers.igdb.cache.set")
    @patch("app.providers.igdb.cache.get", return_value=None)
    def test_returns_unique_steam_ids_only_and_caches_result(
        self,
        _mock_cache_get,
        mock_cache_set,
        _mock_access_token,
        mock_api_request,
    ):
        """Ignore other stores, deduplicate Steam records, and keep their IDs."""
        mock_api_request.return_value = [
            {
                "external_games": [
                    {"uid": "292030", "external_game_source": 1},
                    {"uid": "gog-292030", "external_game_source": 5},
                    {"uid": "292030", "external_game_source": 1},
                    {"uid": "123", "external_game_source": 1},
                ],
            },
        ]

        self.assertEqual(igdb.steam_app_ids("123"), ["292030", "123"])
        self.assertEqual(mock_api_request.call_args.args[:3], (
            "igdb",
            "POST",
            "https://api.igdb.com/v4/games",
        ))
        self.assertIn("external_games.uid", mock_api_request.call_args.kwargs["data"])
        self.assertEqual(mock_cache_set.call_args.args[1], ["292030", "123"])

    @patch("app.providers.igdb.cache.get", return_value=[])
    @patch("app.providers.igdb.services.api_request")
    def test_cached_empty_mapping_does_not_call_igdb(self, mock_api_request, _mock_get):
        """A cached no-mapping result is distinct from a cache miss."""
        self.assertEqual(igdb.steam_app_ids("123"), [])
        mock_api_request.assert_not_called()

    def test_rejects_non_numeric_igdb_ids(self):
        """IGDB IDs are validated before interpolation into the query."""
        with self.assertRaises(ValueError):
            igdb.steam_app_ids("123; delete")

    @patch("app.providers.igdb.services.api_request")
    @patch("app.providers.igdb.get_access_token", return_value="access-token")
    @patch("app.providers.igdb.cache.set")
    @patch("app.providers.igdb.cache.get", return_value=["old"])
    def test_force_refresh_bypasses_cached_app_ids(
        self,
        _mock_get,
        _mock_set,
        _mock_access_token,
        mock_api_request,
    ):
        """An explicit metadata sync can refresh stale cached IGDB mappings."""
        mock_api_request.return_value = [
            {"external_games": [{"uid": "456", "external_game_source": 1}]},
        ]

        self.assertEqual(igdb.steam_app_ids("123", force=True), ["456"])
        mock_api_request.assert_called_once()
