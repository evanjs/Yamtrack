import json

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.models import TV, MediaTypes, Movie, Sources
from app.providers import services

TRAKT_API_BASE_URL = "https://api.trakt.tv"
RATING_FIELDS = {
    "trakt": ("rating",),
    "tmdb": ("tmdb", "rating"),
    "imdb": ("imdb", "rating"),
    "metascore": ("metascore", "rating"),
    "rotten_tomatoes_critic": ("rotten_tomatoes", "rating"),
    "rotten_tomatoes_audience": ("rotten_tomatoes", "user_rating"),
    "letterboxd": ("letterboxd", "rating"),
    "mal": ("mal", "rating"),
}


class Command(BaseCommand):
    """Report Trakt external-rating coverage for one user's TMDB items."""

    help = "Read-only sample of Trakt ratings available for tracked movies and shows."

    def add_arguments(self, parser):
        """Add account and sample-selection options."""
        parser.add_argument("--username", required=True)
        parser.add_argument(
            "--media-type",
            choices=("all", "movie", "show"),
            default="all",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=25,
            help="Maximum number of titles to sample per media type (default: 25).",
        )

    def handle(self, *args, **options):
        """Query Trakt and print only aggregate coverage, without writing data."""
        del args
        username = options["username"]
        media_type = options["media_type"]
        limit = options["limit"]
        if limit < 1:
            message = "--limit must be greater than zero."
            raise CommandError(message)
        if not settings.TRAKT_API:
            message = "TRAKT_API is not configured."
            raise CommandError(message)

        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": settings.TRAKT_API,
        }
        datasets = {}
        if media_type in ("all", "movie"):
            datasets["movie"] = (
                Movie.objects.filter(
                    user__username=username,
                    item__source=Sources.TMDB.value,
                    item__media_type=MediaTypes.MOVIE.value,
                )
                .order_by("item__media_id")
                .values_list("item__media_id", flat=True)
                .distinct()[:limit]
            )
        if media_type in ("all", "show"):
            datasets["show"] = (
                TV.objects.filter(
                    user__username=username,
                    item__source=Sources.TMDB.value,
                    item__media_type=MediaTypes.TV.value,
                )
                .order_by("item__media_id")
                .values_list("item__media_id", flat=True)
                .distinct()[:limit]
            )

        report = {
            "username": username,
            "sample_limit_per_type": limit,
            "media_types": {},
        }
        for kind, tmdb_ids in datasets.items():
            report["media_types"][kind] = self._check_media_type(
                kind,
                list(tmdb_ids),
                headers,
            )
        self.stdout.write(json.dumps(report, sort_keys=True))

    def _check_media_type(self, kind, tmdb_ids, headers):
        """Resolve TMDB IDs to Trakt IDs, then count non-null source ratings."""
        trakt_type = "show" if kind == "show" else "movie"
        endpoint_type = "shows" if kind == "show" else "movies"
        result = {
            "sampled": len(tmdb_ids),
            "matched": 0,
            "unmatched": 0,
            "api_errors": 0,
            "sources": {
                source: {"available": 0, "checked": 0}
                for source in RATING_FIELDS
                if kind == "movie" or source != "letterboxd"
            },
        }

        for tmdb_id in tmdb_ids:
            try:
                matches = services.api_request(
                    "TRAKT",
                    "GET",
                    f"{TRAKT_API_BASE_URL}/search/tmdb/{tmdb_id}",
                    params={"type": trakt_type},
                    headers=headers,
                )
                match = next(
                    (
                        entry.get(trakt_type)
                        for entry in matches
                        if entry.get("type") == trakt_type
                        and str(entry.get(trakt_type, {}).get("ids", {}).get("tmdb"))
                        == str(tmdb_id)
                    ),
                    None,
                )
                trakt_id = match.get("ids", {}).get("trakt") if match else None
                if not trakt_id:
                    result["unmatched"] += 1
                    continue

                ratings = services.api_request(
                    "TRAKT",
                    "GET",
                    f"{TRAKT_API_BASE_URL}/{endpoint_type}/{trakt_id}/ratings",
                    params={"extended": "all"},
                    headers=headers,
                )
            except requests.exceptions.RequestException:
                result["api_errors"] += 1
                continue

            result["matched"] += 1
            for source, path in RATING_FIELDS.items():
                if source not in result["sources"]:
                    continue
                if source == "trakt":
                    value = ratings.get("trakt", ratings).get("rating")
                else:
                    rating_data = ratings.get(path[0])
                    value = (
                        rating_data.get(path[1])
                        if isinstance(rating_data, dict)
                        else None
                    )
                result["sources"][source]["checked"] += 1
                if value is not None:
                    result["sources"][source]["available"] += 1

        return result
