import json

from django.core.management.base import BaseCommand

from app.tasks import backfill_steam_app_ratings


class Command(BaseCommand):
    """Backfill a bounded batch of Steam ratings for tracked IGDB games."""

    help = "Persist Steam Store content ratings for mapped tracked games."

    def add_arguments(self, parser):
        """Add batch and resume cursor options."""
        parser.add_argument("--batch-size", type=int, default=25)
        parser.add_argument("--after-item-id", type=int, default=0)

    def handle(self, *args, **options):
        """Run one batch and print its cursor and failure summary."""
        del args
        result = backfill_steam_app_ratings.run(
            batch_size=options["batch_size"],
            after_item_id=options["after_item_id"],
        )
        self.stdout.write(json.dumps(result, sort_keys=True))
