import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from app.metadata import (
    mark_tmdb_movie_taxonomy_attempt,
    mark_steam_app_mapping_attempt,
    mark_steam_app_rating_attempt,
    store_igdb_game_taxonomies,
    store_steam_app_mapping,
    store_steam_app_ratings,
    store_tmdb_movie_credits,
    store_tmdb_movie_taxonomies,
)
from app.models import (
    Item,
    MediaTypes,
    Sources,
    STEAM_METADATA_REFRESH_AFTER,
    STEAM_METADATA_RETRY_AFTER,
    SteamAppMapping,
    SteamAppRating,
    TMDBMovieCredit,
    TMDBMovieMetadata,
    TMDBMovieTaxonomy,
    UserMessage,
    tmdb_metadata_expiry_cutoff,
    tmdb_metadata_refresh_cutoff,
    tmdb_metadata_retry_cutoff,
)
from app.providers import igdb, services, steam, tmdb

logger = logging.getLogger(__name__)


@shared_task(name="Cleanup user messages")
def cleanup_user_messages():
    """Delete shown user messages older than the configured retention window."""
    cutoff = timezone.now() - timedelta(days=settings.USER_MESSAGE_RETENTION_DAYS)
    deleted_count, _ = UserMessage.objects.filter(
        shown_at__isnull=False,
        shown_at__lt=cutoff,
    ).delete()

    logger.info("Deleted %s old shown user messages.", deleted_count)

    return deleted_count


@shared_task(name="Backfill IGDB game taxonomies")
def backfill_igdb_game_taxonomies(batch_size=100, after_item_id=0):
    """Persist IGDB taxonomy metadata for one bounded batch of tracked games."""
    if batch_size <= 0:
        msg = "batch_size must be greater than zero"
        raise ValueError(msg)

    items = list(
        Item.objects.filter(
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            game__isnull=False,
            igdb_game_metadata__isnull=True,
            id__gt=after_item_id,
        )
        .distinct()
        .order_by("id")[:batch_size]
    )
    succeeded = 0
    failed_ids = []

    for item in items:
        try:
            metadata = igdb.game_taxonomies(item.media_id)
            store_igdb_game_taxonomies(
                item,
                metadata["igdb_taxonomies"],
                provider_updated_at=metadata.get("igdb_updated_at"),
            )
        except (services.ProviderAPIError, KeyError, TypeError, ValueError):
            logger.exception("Failed to backfill IGDB taxonomy for Item %s", item.pk)
            failed_ids.append(item.pk)
        else:
            succeeded += 1

    next_after_item_id = items[-1].pk if items else after_item_id
    has_more = Item.objects.filter(
        source=Sources.IGDB.value,
        media_type=MediaTypes.GAME.value,
        game__isnull=False,
        igdb_game_metadata__isnull=True,
        id__gt=next_after_item_id,
    ).exists()
    result = {
        "processed": len(items),
        "succeeded": succeeded,
        "failed_ids": failed_ids,
        "next_after_item_id": next_after_item_id,
        "has_more": has_more,
    }
    logger.info("IGDB taxonomy backfill batch completed: %s", result)
    return result


def get_due_steam_app_items(after_item_id=0, batch_size=100, *, fair_order=False):
    """Return a bounded batch of tracked IGDB games needing Steam metadata."""
    now = timezone.now()
    refresh_cutoff = now - STEAM_METADATA_REFRESH_AFTER
    retry_cutoff = now - STEAM_METADATA_RETRY_AFTER
    mapping_due = (
        Q(steam_app_mapping__isnull=True)
        | Q(steam_app_mapping__synced_at__isnull=True)
        | Q(steam_app_mapping__synced_at__lt=refresh_cutoff)
    ) & (
        Q(steam_app_mapping__last_attempted_at__isnull=True)
        | Q(steam_app_mapping__last_attempted_at__lt=retry_cutoff)
        | Q(
            steam_app_mapping__synced_at__gte=F(
                "steam_app_mapping__last_attempted_at",
            ),
        )
    )
    ratings_due = Q(steam_app_mapping__synced_at__gte=refresh_cutoff) & (
        Q(steam_app_mapping__ratings_synced_at__isnull=True)
        | Q(steam_app_mapping__ratings_synced_at__lt=refresh_cutoff)
    ) & (
        Q(steam_app_mapping__ratings_last_attempted_at__isnull=True)
        | Q(steam_app_mapping__ratings_last_attempted_at__lt=retry_cutoff)
        | Q(
            steam_app_mapping__ratings_synced_at__gte=F(
                "steam_app_mapping__ratings_last_attempted_at",
            ),
        )
    )
    items = Item.objects.filter(
        source=Sources.IGDB.value,
        media_type=MediaTypes.GAME.value,
        game__isnull=False,
        id__gt=after_item_id,
    ).filter(mapping_due | ratings_due)

    if fair_order:
        items = items.order_by(
            F("steam_app_mapping__last_attempted_at").asc(nulls_first=True),
            F("steam_app_mapping__ratings_last_attempted_at").asc(nulls_first=True),
            F("steam_app_mapping__synced_at").asc(nulls_first=True),
            "id",
        )
    else:
        items = items.order_by("id")
    return list(items.distinct()[:batch_size])


def refresh_steam_app_items(items, *, force=False):
    """Refresh verified Steam ratings and report item-level provider failures."""
    succeeded = 0
    failed_ids = []
    refresh_cutoff = timezone.now() - STEAM_METADATA_REFRESH_AFTER

    for item in items:
        try:
            mapping = SteamAppMapping.objects.filter(item=item).first()
            mapping_due = (
                force
                or mapping is None
                or mapping.synced_at is None
                or mapping.synced_at < refresh_cutoff
            )
            if mapping_due:
                mark_steam_app_mapping_attempt(item)
                app_ids = igdb.steam_app_ids(item.media_id, force=force)
                mapping = store_steam_app_mapping(item, app_ids)
            else:
                app_ids = mapping.app_ids

            for app_id in app_ids:
                rating = SteamAppRating.objects.filter(steam_app_id=app_id).first()
                if (
                    not force
                    and rating
                    and rating.synced_at
                    and rating.synced_at >= refresh_cutoff
                ):
                    continue
                mark_steam_app_rating_attempt(item, app_id)
                ratings = steam.app_ratings(app_id)
                store_steam_app_ratings(item, app_id, ratings)

            mapping.ratings_synced_at = timezone.now()
            mapping.ratings_last_attempted_at = mapping.ratings_synced_at
            mapping.save(
                update_fields=["ratings_synced_at", "ratings_last_attempted_at"],
            )
        except (
            services.ProviderAPIError,
            requests.exceptions.RequestException,
            KeyError,
            TypeError,
            ValueError,
        ):
            logger.exception("Failed to refresh Steam ratings for Item %s", item.pk)
            mapping = SteamAppMapping.objects.filter(item=item).first()
            if mapping:
                mapping.ratings_last_attempted_at = timezone.now()
                mapping.save(update_fields=["ratings_last_attempted_at"])
            failed_ids.append(item.pk)
        else:
            succeeded += 1

    return {"processed": len(items), "succeeded": succeeded, "failed_ids": failed_ids}


@shared_task(name="Backfill Steam app ratings")
def backfill_steam_app_ratings(batch_size=25, after_item_id=0):
    """Backfill Steam mappings and ratings in resumable ID-ordered batches."""
    if batch_size <= 0:
        msg = "batch_size must be greater than zero"
        raise ValueError(msg)

    items = get_due_steam_app_items(after_item_id, batch_size)
    result = refresh_steam_app_items(items)
    next_after_item_id = items[-1].pk if items else after_item_id
    result["next_after_item_id"] = next_after_item_id
    result["has_more"] = bool(get_due_steam_app_items(next_after_item_id, 1))
    logger.info("Steam app rating backfill batch completed: %s", result)
    return result


@shared_task(name="Refresh due Steam app ratings")
def refresh_due_steam_app_ratings(batch_size=25):
    """Refresh a fair bounded batch of due Steam App ratings."""
    if batch_size <= 0:
        msg = "batch_size must be greater than zero"
        raise ValueError(msg)

    items = get_due_steam_app_items(batch_size=batch_size, fair_order=True)
    result = refresh_steam_app_items(items)
    result["has_more"] = bool(
        get_due_steam_app_items(batch_size=1, fair_order=True),
    )
    logger.info("Steam app rating refresh batch completed: %s", result)
    return result


def get_due_tmdb_movie_items(after_item_id=0, batch_size=100, *, fair_order=False):
    """Return a bounded batch of missing or stale tracked TMDB movies."""
    now = timezone.now()
    refresh_cutoff = tmdb_metadata_refresh_cutoff(now)
    retry_cutoff = tmdb_metadata_retry_cutoff(now)
    due_metadata = (
        Q(tmdb_movie_metadata__isnull=True)
        | Q(tmdb_movie_metadata__synced_at__isnull=True)
        | Q(tmdb_movie_metadata__synced_at__lt=refresh_cutoff)
        | Q(tmdb_movie_metadata__credits_synced_at__isnull=True)
        | Q(tmdb_movie_metadata__credits_synced_at__lt=refresh_cutoff)
    )
    retry_allowed = (
        Q(tmdb_movie_metadata__last_attempted_at__isnull=True)
        | Q(tmdb_movie_metadata__last_attempted_at__lt=retry_cutoff)
        | Q(
            tmdb_movie_metadata__synced_at__isnull=False,
            tmdb_movie_metadata__last_attempted_at__lte=F(
                "tmdb_movie_metadata__synced_at",
            ),
        )
    )
    items = Item.objects.filter(
        source=Sources.TMDB.value,
        media_type=MediaTypes.MOVIE.value,
        movie__isnull=False,
        id__gt=after_item_id,
    ).filter(due_metadata, retry_allowed)

    if fair_order:
        items = items.order_by(
            F("tmdb_movie_metadata__last_attempted_at").asc(nulls_first=True),
            F("tmdb_movie_metadata__synced_at").asc(nulls_first=True),
            "id",
        )
    else:
        items = items.order_by("id")
    return list(items.distinct()[:batch_size])


def refresh_tmdb_movie_items(items):
    """Refresh taxonomy for a list of items and tally provider failures."""
    succeeded = 0
    failed_ids = []

    for item in items:
        try:
            mark_tmdb_movie_taxonomy_attempt(item)
            metadata = tmdb.movie_taxonomies(item.media_id)
            store_tmdb_movie_taxonomies(item, metadata["tmdb_taxonomies"])
            if "tmdb_credits" in metadata:
                store_tmdb_movie_credits(item, metadata["tmdb_credits"])
        except (
            services.ProviderAPIError,
            requests.exceptions.RequestException,
            KeyError,
            TypeError,
            ValueError,
        ):
            mark_tmdb_movie_taxonomy_attempt(item)
            logger.exception("Failed to backfill TMDB taxonomy for Item %s", item.pk)
            failed_ids.append(item.pk)
        else:
            succeeded += 1
    return {"processed": len(items), "succeeded": succeeded, "failed_ids": failed_ids}


@shared_task(name="Backfill TMDB movie taxonomies")
def backfill_tmdb_movie_taxonomies(batch_size=100, after_item_id=0):
    """Backfill and refresh due TMDB taxonomy in a resumable ID-ordered batch."""
    if batch_size <= 0:
        msg = "batch_size must be greater than zero"
        raise ValueError(msg)

    items = get_due_tmdb_movie_items(after_item_id, batch_size)
    result = refresh_tmdb_movie_items(items)

    next_after_item_id = items[-1].pk if items else after_item_id
    result["next_after_item_id"] = next_after_item_id
    result["has_more"] = bool(get_due_tmdb_movie_items(next_after_item_id, 1))
    logger.info("TMDB taxonomy backfill batch completed: %s", result)
    return result


@shared_task(name="Refresh due TMDB movie taxonomies")
def refresh_due_tmdb_movie_taxonomies(batch_size=100):
    """Refresh due TMDB movies fairly, prioritizing never-attempted records."""
    if batch_size <= 0:
        msg = "batch_size must be greater than zero"
        raise ValueError(msg)

    items = get_due_tmdb_movie_items(batch_size=batch_size, fair_order=True)
    result = refresh_tmdb_movie_items(items)
    result["has_more"] = bool(
        get_due_tmdb_movie_items(batch_size=1, fair_order=True),
    )
    logger.info("TMDB taxonomy refresh batch completed: %s", result)
    return result


@shared_task(name="Cleanup expired TMDB movie taxonomies")
def cleanup_expired_tmdb_movie_taxonomies():
    """Delete TMDB taxonomy records older than the configured hard retention cap."""
    expired = TMDBMovieMetadata.objects.filter(
        Q(synced_at__lt=tmdb_metadata_expiry_cutoff())
        | Q(credits_synced_at__lt=tmdb_metadata_expiry_cutoff())
        | Q(
            synced_at__isnull=True,
            last_attempted_at__lt=tmdb_metadata_expiry_cutoff(),
        ),
    )
    expired_count = expired.count()
    expired.delete()

    orphaned_taxonomies = TMDBMovieTaxonomy.objects.filter(
        movie_assignments__isnull=True,
    )
    taxonomy_count = orphaned_taxonomies.count()
    orphaned_taxonomies.delete()

    orphaned_credits = TMDBMovieCredit.objects.filter(movie_assignments__isnull=True)
    credits_deleted = orphaned_credits.count()
    orphaned_credits.delete()

    result = {
        "metadata_deleted": expired_count,
        "taxonomies_deleted": taxonomy_count,
        "credits_deleted": credits_deleted,
    }
    logger.info("TMDB taxonomy cleanup completed: %s", result)
    return result
