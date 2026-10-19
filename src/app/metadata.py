import re

from django.db import transaction
from django.utils import timezone

from app.models import (
    IGDBGameMetadata,
    IGDBGameTaxonomy,
    IGDBGameTaxonomyAssignment,
    Item,
    MediaTypes,
    Sources,
    SteamAppMapping,
    SteamAppRating,
    TMDBMovieCredit,
    TMDBMovieCreditAssignment,
    TMDBMovieMetadata,
    TMDBMovieTaxonomy,
    TMDBMovieTaxonomyAssignment,
)

TAXONOMY_KINDS = {
    "genres": IGDBGameTaxonomy.Kind.GENRE,
    "themes": IGDBGameTaxonomy.Kind.THEME,
    "keywords": IGDBGameTaxonomy.Kind.KEYWORD,
}


def store_igdb_game_taxonomies(item, taxonomies, provider_updated_at=None):
    """Atomically replace the stored taxonomy for one IGDB game Item."""
    if item.source != Sources.IGDB.value or item.media_type != MediaTypes.GAME.value:
        msg = "IGDB taxonomies can only be stored for IGDB game Items."
        raise ValueError(msg)

    if set(taxonomies) != set(TAXONOMY_KINDS):
        msg = "IGDB taxonomy data must include genres, themes, and keywords."
        raise ValueError(msg)

    normalized = {}
    for facet, kind in TAXONOMY_KINDS.items():
        values = {}
        for value in taxonomies[facet]:
            provider_id = value["id"]
            name = value["name"]
            if isinstance(provider_id, bool) or not isinstance(provider_id, int):
                msg = f"Invalid IGDB {facet} ID: {provider_id!r}"
                raise TypeError(msg)
            if not isinstance(name, str):
                msg = f"Invalid IGDB {facet} name: {name!r}"
                raise TypeError(msg)
            if provider_id <= 0 or not name:
                msg = f"Invalid IGDB {facet} value: {value!r}"
                raise ValueError(msg)
            values[provider_id] = {
                "kind": kind,
                "provider_id": provider_id,
                "name": name,
                "slug": value.get("slug", ""),
            }
        normalized[facet] = list(values.values())

    with transaction.atomic():
        metadata, _ = IGDBGameMetadata.objects.get_or_create(item=item)
        taxonomy_ids = []
        for values in normalized.values():
            for value in values:
                taxonomy, _ = IGDBGameTaxonomy.objects.update_or_create(
                    kind=value["kind"],
                    provider_id=value["provider_id"],
                    defaults={"name": value["name"], "slug": value["slug"]},
                )
                taxonomy_ids.append(taxonomy.pk)

        IGDBGameTaxonomyAssignment.objects.filter(metadata=metadata).delete()
        IGDBGameTaxonomyAssignment.objects.bulk_create(
            [
                IGDBGameTaxonomyAssignment(
                    metadata=metadata,
                    taxonomy_id=taxonomy_id,
                )
                for taxonomy_id in taxonomy_ids
            ],
            ignore_conflicts=True,
        )
        metadata.synced_at = timezone.now()
        metadata.provider_updated_at = provider_updated_at
        metadata.save(update_fields=["synced_at", "provider_updated_at"])

    return metadata


STEAM_APP_ID_MAX_LENGTH = 20


def validate_steam_rating_identity(item, steam_app_id):
    """Validate a Steam App ID and its shared IGDB game Item association."""
    if item.source != Sources.IGDB.value or item.media_type != MediaTypes.GAME.value:
        msg = "Steam ratings can only be stored for IGDB game Items."
        raise ValueError(msg)
    app_id = str(steam_app_id)
    if (
        isinstance(steam_app_id, bool)
        or not isinstance(steam_app_id, (str, int))
        or not app_id.isascii()
        or not app_id.isdecimal()
        or len(app_id) > STEAM_APP_ID_MAX_LENGTH
        or int(app_id) <= 0
    ):
        msg = f"Invalid Steam App ID: {steam_app_id!r}"
        raise ValueError(msg)
    return app_id


def store_steam_app_ratings(item, steam_app_id, ratings):
    """Persist all agency-keyed ratings from a successful Store response."""
    steam_app_id = validate_steam_rating_identity(item, steam_app_id)
    if not isinstance(ratings, dict) or any(
        not isinstance(key, str) for key in ratings
    ):
        msg = "Steam ratings must be an agency-keyed object."
        raise TypeError(msg)

    with transaction.atomic():
        metadata, created = SteamAppRating.objects.get_or_create(
            steam_app_id=steam_app_id,
            defaults={"item": item},
        )
        if not created and metadata.item_id != item.pk:
            msg = (
                f"Steam App ID {steam_app_id} is already associated with "
                "another Item."
            )
            raise ValueError(msg)
        metadata.ratings = ratings
        now = timezone.now()
        metadata.synced_at = now
        metadata.last_attempted_at = now
        metadata.save(update_fields=["ratings", "synced_at", "last_attempted_at"])

    return metadata


def mark_steam_app_rating_attempt(item, steam_app_id):
    """Record an attempt without marking ratings fresh or replacing last-good data."""
    steam_app_id = validate_steam_rating_identity(item, steam_app_id)
    metadata, created = SteamAppRating.objects.get_or_create(
        steam_app_id=steam_app_id,
        defaults={"item": item},
    )
    if not created and metadata.item_id != item.pk:
        msg = (
            f"Steam App ID {steam_app_id} is already associated with "
            "another Item."
        )
        raise ValueError(msg)
    metadata.last_attempted_at = timezone.now()
    metadata.save(update_fields=["last_attempted_at"])
    return metadata


def store_steam_app_mapping(item, app_ids):
    """Persist a successful IGDB-to-Steam mapping, including no-match results."""
    if not isinstance(app_ids, list):
        msg = "Steam App ID mappings must be a list."
        raise TypeError(msg)
    normalized_ids = []
    for app_id in app_ids:
        valid_app_id = validate_steam_rating_identity(item, app_id)
        if valid_app_id not in normalized_ids:
            normalized_ids.append(valid_app_id)
    now = timezone.now()
    mapping, created = SteamAppMapping.objects.get_or_create(
        item=item,
        defaults={
            "app_ids": normalized_ids,
            "synced_at": now,
            "last_attempted_at": now,
        },
    )
    if not created:
        mapping_changed = mapping.app_ids != normalized_ids
        mapping.app_ids = normalized_ids
        mapping.synced_at = now
        mapping.last_attempted_at = now
        fields = ["app_ids", "synced_at", "last_attempted_at"]
        if mapping_changed:
            mapping.ratings_synced_at = None
            mapping.ratings_last_attempted_at = None
            fields.extend(["ratings_synced_at", "ratings_last_attempted_at"])
        mapping.save(update_fields=fields)
    return mapping


def mark_steam_app_mapping_attempt(item):
    """Record an IGDB mapping attempt without replacing the last-good result."""
    if item.source != Sources.IGDB.value or item.media_type != MediaTypes.GAME.value:
        msg = "Steam mappings can only be recorded for IGDB game Items."
        raise ValueError(msg)
    mapping, _ = SteamAppMapping.objects.get_or_create(item=item)
    mapping.last_attempted_at = timezone.now()
    mapping.save(update_fields=["last_attempted_at"])
    return mapping


IGDB_RATING_ORGANIZATION_BY_STEAM_AGENCY = {
    "esrb": "esrb",
    "pegi": "pegi",
    "cero": "cero",
    "usk": "usk",
    "kgrb": "grac",
}

STEAM_RATING_ICON_PATHS = {
    ("esrb", "e"): "img/ratings/esrb/everyone.svg",
    ("esrb", "e10"): "img/ratings/esrb/everyone-10-plus.svg",
    ("esrb", "t"): "img/ratings/esrb/teen.svg",
    ("esrb", "m"): "img/ratings/esrb/mature.svg",
    ("esrb", "ao"): "img/ratings/esrb/adults-only.svg",
    ("esrb", "ec"): "img/ratings/esrb/early-childhood.svg",
    ("esrb", "rp"): "img/ratings/esrb/rating-pending.svg",
    ("cero", "a"): "img/ratings/cero/a.svg",
    ("cero", "b"): "img/ratings/cero/b.svg",
    ("cero", "c"): "img/ratings/cero/c.svg",
    ("cero", "d"): "img/ratings/cero/d.svg",
    ("cero", "z"): "img/ratings/cero/z.svg",
}


def _rating_token(value):
    """Return a case- and punctuation-insensitive rating token."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _canonical_rating_value(agency, value):
    """Normalize equivalent Steam and IGDB labels for known rating systems."""
    token = _rating_token(value)
    aliases = {
        "esrb": {
            "everyone": "e",
            "everyone10": "e10",
            "everyone10plus": "e10",
            "teen": "t",
            "mature": "m",
            "mature17": "m",
            "adultsonly": "ao",
            "adultsonly18": "ao",
            "earlychildhood": "ec",
            "ratingpending": "rp",
        },
        "pegi": {
            "three": "3",
            "seven": "7",
            "twelve": "12",
            "sixteen": "16",
            "eighteen": "18",
        },
        "cero": {
            "ceroa": "a",
            "cerob": "b",
            "ceroc": "c",
            "cerod": "d",
            "ceroz": "z",
        },
        "usk": {
            "usk0": "0",
            "usk6": "6",
            "usk12": "12",
            "usk16": "16",
            "usk18": "18",
        },
        "kgrb": {
            "gracall": "all",
            "gractwelve": "12",
            "gracfifteen": "15",
            "graceighteen": "18",
        },
    }
    return aliases.get(agency, {}).get(token, token)


def format_steam_rating_value(agency, value):
    """Format selected agency values using familiar card capitalization."""
    if not isinstance(value, str):
        return value
    token = _rating_token(value)
    if agency == "cero" and token.startswith("cero"):
        return token.removeprefix("cero").upper()
    if agency in {"esrb", "cero"}:
        return value.upper()
    return value


def get_igdb_rating_for_steam(agency, steam_rating, age_ratings):
    """Return unambiguous IGDB rating artwork matching a Steam rating value."""
    organization = IGDB_RATING_ORGANIZATION_BY_STEAM_AGENCY.get(agency)
    expected_rating = _canonical_rating_value(agency, steam_rating)
    if not organization or not expected_rating or not isinstance(age_ratings, list):
        return None

    matching = [
        age_rating
        for age_rating in age_ratings
        if isinstance(age_rating, dict)
        and _rating_token(age_rating.get("organization")) == organization
        and _canonical_rating_value(agency, age_rating.get("rating"))
        == expected_rating
    ]
    return matching[0] if len(matching) == 1 else None


def get_local_steam_rating_icon_path(agency, steam_rating, igdb_age_rating):
    """Return a bundled icon path for an exact IGDB organization/category match."""
    organization = IGDB_RATING_ORGANIZATION_BY_STEAM_AGENCY.get(agency)
    if not organization or not isinstance(igdb_age_rating, dict):
        return None
    if _rating_token(igdb_age_rating.get("organization")) != organization:
        return None

    expected_rating = _canonical_rating_value(agency, steam_rating)
    actual_rating = _canonical_rating_value(agency, igdb_age_rating.get("rating"))
    if not expected_rating or actual_rating != expected_rating:
        return None
    return STEAM_RATING_ICON_PATHS.get((agency, expected_rating))


def _steam_rating_value_for_record(rating_record, agency):
    """Extract a non-empty scalar rating from one agency payload."""
    if rating_record is None or not isinstance(rating_record.ratings, dict):
        return None
    agency_data = rating_record.ratings.get(agency)
    if not isinstance(agency_data, dict):
        return None
    value = agency_data.get("rating")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value).strip() or None


def get_steam_rating_value(item, agency):
    """Return a selected agency rating only when all mapped Apps agree."""
    if (
        not isinstance(item, Item)
        or item.source != Sources.IGDB.value
        or item.media_type != MediaTypes.GAME.value
        or not agency
        or agency == "UNSET"
    ):
        return None
    try:
        mapping = item.steam_app_mapping
    except SteamAppMapping.DoesNotExist:
        return None
    if not isinstance(mapping.app_ids, list) or not mapping.app_ids:
        return None

    ratings_by_app_id = {
        rating.steam_app_id: rating for rating in item.steam_app_ratings.all()
    }
    values = [
        _steam_rating_value_for_record(ratings_by_app_id.get(str(app_id)), agency)
        for app_id in mapping.app_ids
    ]
    if any(value is None for value in values) or len(set(values)) != 1:
        return None
    return values[0]


TMDB_TAXONOMY_KINDS = {
    "genres": TMDBMovieTaxonomy.Kind.GENRE,
    "keywords": TMDBMovieTaxonomy.Kind.KEYWORD,
}


def store_tmdb_movie_taxonomies(item, taxonomies):
    """Atomically replace the stored taxonomy for one TMDB movie Item."""
    if item.source != Sources.TMDB.value or item.media_type != MediaTypes.MOVIE.value:
        msg = "TMDB taxonomies can only be stored for TMDB movie Items."
        raise ValueError(msg)

    if set(taxonomies) != set(TMDB_TAXONOMY_KINDS):
        msg = "TMDB taxonomy data must include genres and keywords."
        raise ValueError(msg)

    normalized = {}
    for facet, kind in TMDB_TAXONOMY_KINDS.items():
        values = {}
        for value in taxonomies[facet]:
            provider_id = value["id"]
            name = value["name"]
            if isinstance(provider_id, bool) or not isinstance(provider_id, int):
                msg = f"Invalid TMDB {facet} ID: {provider_id!r}"
                raise TypeError(msg)
            if not isinstance(name, str):
                msg = f"Invalid TMDB {facet} name: {name!r}"
                raise TypeError(msg)
            if provider_id <= 0 or not name:
                msg = f"Invalid TMDB {facet} value: {value!r}"
                raise ValueError(msg)
            values[provider_id] = {
                "kind": kind,
                "provider_id": provider_id,
                "name": name,
            }
        normalized[facet] = list(values.values())

    with transaction.atomic():
        metadata, _ = TMDBMovieMetadata.objects.get_or_create(item=item)
        taxonomy_ids = []
        for values in normalized.values():
            for value in values:
                taxonomy, _ = TMDBMovieTaxonomy.objects.update_or_create(
                    kind=value["kind"],
                    provider_id=value["provider_id"],
                    defaults={"name": value["name"]},
                )
                taxonomy_ids.append(taxonomy.pk)

        TMDBMovieTaxonomyAssignment.objects.filter(metadata=metadata).delete()
        TMDBMovieTaxonomyAssignment.objects.bulk_create(
            [
                TMDBMovieTaxonomyAssignment(
                    metadata=metadata,
                    taxonomy_id=taxonomy_id,
                )
                for taxonomy_id in taxonomy_ids
            ],
            ignore_conflicts=True,
        )
        now = timezone.now()
        metadata.synced_at = now
        metadata.last_attempted_at = now
        metadata.save(update_fields=["synced_at", "last_attempted_at"])

    return metadata


def mark_tmdb_movie_taxonomy_attempt(item):
    """Record an attempt without marking failed or partial data as fresh."""
    if item.source != Sources.TMDB.value or item.media_type != MediaTypes.MOVIE.value:
        msg = "TMDB taxonomy attempts can only be recorded for TMDB movie Items."
        raise ValueError(msg)

    metadata, _ = TMDBMovieMetadata.objects.get_or_create(item=item)
    metadata.last_attempted_at = timezone.now()
    metadata.save(update_fields=["last_attempted_at"])
    return metadata


def store_tmdb_movie_credits(item, credit_data):
    """Atomically replace a TMDB movie's cast and crew credit relationships."""
    if item.source != Sources.TMDB.value or item.media_type != MediaTypes.MOVIE.value:
        msg = "TMDB credits can only be stored for TMDB movie Items."
        raise ValueError(msg)
    if set(credit_data) != {"cast", "crew"}:
        msg = "TMDB credit data must include cast and crew."
        raise ValueError(msg)

    normalized = {}
    for credit_type, values in (
        (TMDBMovieCreditAssignment.CreditType.CAST, credit_data["cast"]),
        (TMDBMovieCreditAssignment.CreditType.CREW, credit_data["crew"]),
    ):
        for value in values:
            provider_id = value.get("id")
            name = value.get("name")
            job = (
                "Actor"
                if credit_type == TMDBMovieCreditAssignment.CreditType.CAST
                else value.get("job")
            )
            character = (
                value.get("character", "")
                if credit_type == TMDBMovieCreditAssignment.CreditType.CAST
                else ""
            )
            cast_order = (
                value.get("order")
                if credit_type == TMDBMovieCreditAssignment.CreditType.CAST
                else None
            )
            department = (
                value.get("department", "")
                if credit_type == TMDBMovieCreditAssignment.CreditType.CREW
                else "Acting"
            )
            if isinstance(provider_id, bool) or not isinstance(provider_id, int):
                msg = f"Invalid TMDB person ID: {provider_id!r}"
                raise TypeError(msg)
            if (
                provider_id <= 0
                or not isinstance(name, str)
                or not name
                or not isinstance(job, str)
                or not job
            ):
                msg = f"Invalid TMDB credit value: {value!r}"
                raise ValueError(msg)
            if not isinstance(department, str) or not isinstance(character, str):
                msg = f"Invalid TMDB credit details: {value!r}"
                raise TypeError(msg)
            if cast_order is not None and (
                isinstance(cast_order, bool)
                or not isinstance(cast_order, int)
                or cast_order < 0
            ):
                msg = f"Invalid TMDB cast order: {cast_order!r}"
                raise ValueError(msg)
            key = (provider_id, credit_type, job, character)
            normalized[key] = {
                "provider_id": provider_id,
                "name": name,
                "credit_type": credit_type,
                "department": department,
                "job": job,
                "character": character,
                "cast_order": cast_order,
            }

    with transaction.atomic():
        metadata, _ = TMDBMovieMetadata.objects.get_or_create(item=item)
        assignments = []
        for value in normalized.values():
            person, _ = TMDBMovieCredit.objects.update_or_create(
                provider_id=value["provider_id"],
                defaults={"name": value["name"]},
            )
            assignments.append(
                TMDBMovieCreditAssignment(
                    metadata=metadata,
                    person=person,
                    credit_type=value["credit_type"],
                    department=value["department"],
                    job=value["job"],
                    character=value["character"],
                    cast_order=value["cast_order"],
                ),
            )
        TMDBMovieCreditAssignment.objects.filter(metadata=metadata).delete()
        TMDBMovieCreditAssignment.objects.bulk_create(
            assignments, ignore_conflicts=True
        )
        now = timezone.now()
        metadata.last_attempted_at = now
        metadata.credits_synced_at = now
        metadata.save(update_fields=["last_attempted_at", "credits_synced_at"])
    return metadata
