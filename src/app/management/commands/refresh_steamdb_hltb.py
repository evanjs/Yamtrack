import json

from django.core.management.base import BaseCommand

from app.tasks import refresh_steamdb_hltb_mappings


class Command(BaseCommand):
    """Refresh the shared SteamDB lookup and game metadata immediately."""

    help = "Download and persist SteamDB game metadata and HLTB mappings."

    def add_arguments(self, parser):
        """Add an optional source URL override."""
        parser.add_argument("--url", help="Override the SteamDB gzip JSON URL")

    def handle(self, *args, **options):
        """Run one refresh immediately and print its summary."""
        del args
        result = refresh_steamdb_hltb_mappings.run(url=options["url"])
        self.stdout.write(json.dumps(result, sort_keys=True))
