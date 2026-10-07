import json

from django.core.management.base import BaseCommand

from app.tasks import backfill_igdb_game_taxonomies


class Command(BaseCommand):
    """Backfill a bounded batch of tracked IGDB game taxonomies."""

    help = "Persist IGDB genres, themes, and keywords for tracked games."

    def add_arguments(self, parser):
        """Add batch and resume cursor options."""
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--after-item-id", type=int, default=0)

    def handle(self, *args, **options):
        """Run one batch and print its cursor and failure summary."""
        del args
        result = backfill_igdb_game_taxonomies.run(
            batch_size=options["batch_size"],
            after_item_id=options["after_item_id"],
        )
        self.stdout.write(json.dumps(result, sort_keys=True))
