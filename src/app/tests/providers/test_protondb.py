from unittest.mock import patch

from django.test import SimpleTestCase

from app.providers import protondb


class ProtonDBTests(SimpleTestCase):
    """Test conservative ProtonDB summary retrieval and normalization."""

    @patch(
        "app.providers.protondb.cache.get",
        return_value={
            "tier": "Silver",
            "banner_color": "#adb5bd",
            "confidence": "Weak",
            "total": 3,
        },
    )
    @patch("app.providers.protondb.services.api_request")
    def test_summary_cache_hit_does_not_contact_provider(
        self,
        mock_api_request,
        _mock_cache_get,
    ):
        """Use cached values instead of requesting ProtonDB again."""
        result = protondb.summary("292030")

        self.assertEqual(
            result,
            {
                "tier": "Silver",
                "banner_color": "#adb5bd",
                "confidence": "Weak",
                "total": 3,
            },
        )
        mock_api_request.assert_not_called()

    @patch("app.providers.protondb.cache.delete")
    @patch("app.providers.protondb.cache.set")
    @patch(
        "app.providers.protondb.cache.get",
        return_value={"tier": "Gold", "confidence": "Strong"},
    )
    @patch("app.providers.protondb.services.api_request")
    def test_summary_refreshes_cache_entries_missing_banner_fields(
        self,
        mock_api_request,
        _mock_cache_get,
        _mock_cache_set,
        mock_cache_delete,
    ):
        """Replace cached pre-banner payloads so the icon and color can render."""
        mock_api_request.return_value = {"tier": "gold", "trendingTier": "gold"}

        result = protondb.summary("292030")

        self.assertEqual(result["banner_color"], "#c8b33f")
        self.assertEqual(result["trending_tier"], "Gold")
        mock_cache_delete.assert_called_once_with("protondb_summary_292030")
        mock_api_request.assert_called_once()

    @patch("app.providers.protondb.cache.set")
    @patch("app.providers.protondb.cache.get", return_value=None)
    @patch("app.providers.protondb.services.api_request")
    def test_summary_normalizes_supported_fields_and_caches_by_app_id(
        self,
        mock_api_request,
        mock_cache_get,
        mock_cache_set,
    ):
        """Normalize only the data needed by the ProtonDB details UI."""
        mock_api_request.return_value = {
            "tier": "platinum",
            "confidence": "strong",
            "score": 0.87,
            "total": 1873,
            "bestReportedTier": "platinum",
            "trendingTier": "gold",
        }

        result = protondb.summary("292030")

        self.assertEqual(
            result,
            {
                "tier": "Platinum",
                "banner_color": "#b7c9d7",
                "trending_tier": "Gold",
                "confidence": "Strong",
                "score": 0.87,
                "total": 1873,
            },
        )
        mock_api_request.assert_called_once_with(
            "ProtonDB",
            "GET",
            "https://www.protondb.com/api/v1/reports/summaries/292030.json",
        )
        mock_cache_get.assert_called_once_with("protondb_summary_292030")
        mock_cache_set.assert_called_once_with(
            "protondb_summary_292030",
            result,
            protondb.SUMMARY_CACHE_TIMEOUT,
        )

    def test_summary_rejects_invalid_app_ids_without_requesting_provider(self):
        """Reject malformed IDs before building a provider request."""
        with patch("app.providers.protondb.services.api_request") as mock_api_request:
            self.assertIsNone(protondb.summary("../292030"))

        mock_api_request.assert_not_called()

    @patch("app.providers.protondb.cache.set")
    @patch("app.providers.protondb.cache.get", return_value=None)
    @patch("app.providers.protondb.services.api_request", return_value={})
    def test_summary_returns_none_for_empty_or_unrecognized_response(
        self,
        _mock_api_request,
        _mock_cache_get,
        mock_cache_set,
    ):
        """Treat response data without a known tier as unavailable."""
        self.assertIsNone(protondb.summary("292030"))
        mock_cache_set.assert_called_once_with(
            "protondb_summary_292030",
            protondb.NO_SUMMARY,
            protondb.SUMMARY_CACHE_TIMEOUT,
        )
