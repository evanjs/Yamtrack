"""Small, cache-backed client for ProtonDB's observed summary endpoint."""

import logging
import math

import requests
from django.conf import settings
from django.core.cache import cache

from app.providers import services

logger = logging.getLogger(__name__)

BASE_URL = "https://www.protondb.com/api/v1/reports/summaries"
SUMMARY_CACHE_TIMEOUT = settings.CACHE_TIMEOUT
ERROR_CACHE_TIMEOUT = 15 * 60
NO_SUMMARY = "__no_protondb_summary__"
VALID_TIERS = {"platinum", "gold", "silver", "bronze", "borked", "pending", "native"}
TIER_BANNER_COLORS = {
    "platinum": "#b7c9d7",
    "gold": "#c8b33f",
    "silver": "#adb5bd",
    "bronze": "#d19a68",
    "borked": "#e07b7b",
    "pending": "#b9bec5",
    "native": "#9ac68a",
}


def summary(app_id):
    """Return a normalized report summary for an app, or None when unavailable."""
    if (
        not isinstance(app_id, str)
        or not app_id.isascii()
        or not app_id.isdecimal()
        or int(app_id) <= 0
    ):
        return None

    cache_key = f"protondb_summary_{app_id}"
    cached_summary = cache.get(cache_key)
    if isinstance(cached_summary, dict) and "banner_color" not in cached_summary:
        cache.delete(cache_key)
        cached_summary = None
    if cached_summary == NO_SUMMARY:
        return None
    if cached_summary is not None:
        return cached_summary

    try:
        response = services.api_request(
            "ProtonDB",
            "GET",
            f"{BASE_URL}/{app_id}.json",
        )
    except (requests.exceptions.RequestException, services.ProviderAPIError):
        logger.warning("Could not retrieve ProtonDB summary for app %s", app_id)
        cache.set(cache_key, NO_SUMMARY, ERROR_CACHE_TIMEOUT)
        return None

    data = _normalize_summary(response)
    cache.set(
        cache_key,
        data if data is not None else NO_SUMMARY,
        SUMMARY_CACHE_TIMEOUT,
    )
    return data


def _normalize_summary(response):
    """Keep only the simple, stable-looking fields used by the detail UI."""
    if not isinstance(response, dict):
        return None

    tier = response.get("tier")
    if not isinstance(tier, str) or tier.casefold() not in VALID_TIERS:
        return None

    normalized = {
        "tier": tier.capitalize(),
        "banner_color": TIER_BANNER_COLORS[tier.casefold()],
    }
    trending_tier = response.get("trendingTier")
    if (
        isinstance(trending_tier, str)
        and trending_tier.casefold() in VALID_TIERS
    ):
        normalized["trending_tier"] = trending_tier.capitalize()
    confidence = response.get("confidence")
    if isinstance(confidence, str) and confidence.casefold() in {
        "strong",
        "medium",
        "weak",
    }:
        normalized["confidence"] = confidence.capitalize()

    score = response.get("score")
    if (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and math.isfinite(score)
        and 0 <= score <= 1
    ):
        normalized["score"] = score

    total = response.get("total")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        normalized["total"] = total

    return normalized
