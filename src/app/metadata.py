from django.db import transaction
from django.utils import timezone

from app.models import (
    IGDBGameMetadata,
    IGDBGameTaxonomy,
    IGDBGameTaxonomyAssignment,
    MediaTypes,
    Sources,
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
