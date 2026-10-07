import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from app.metadata import store_igdb_game_taxonomies
from app.models import Item, MediaTypes, Sources, UserMessage
from app.providers import igdb, services

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
