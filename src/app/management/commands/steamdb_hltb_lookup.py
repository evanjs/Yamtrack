import json

from django.core.management.base import BaseCommand

from app.models import SteamDBHltbMapping


class Command(BaseCommand):
    """Look up persisted HLTB IDs by Steam AppID."""

    help = "Look up HLTB IDs by Steam AppID in the processed SteamDB catalog."

    def add_arguments(self, parser):
        """Add the AppID list argument."""
        parser.add_argument("app_ids", nargs="+", help="Steam AppIDs to look up")

    def handle(self, *args, **options):
        """Print matching Steam AppIDs and HLTB IDs as JSON."""
        del args
        app_ids = {str(app_id) for app_id in options["app_ids"]}
        rows = SteamDBHltbMapping.objects.filter(steam_app_id__in=app_ids)
        matches = {row.steam_app_id: row.hltb_id for row in rows}
        self.stdout.write(json.dumps(matches, indent=2, sort_keys=True))
