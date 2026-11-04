import gzip
import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from app.models import SteamDBHltbMapping
from app.tasks import refresh_steamdb_hltb_mappings


class SteamDBHltbRefreshTests(TestCase):
    """Test refresh of the persisted shared SteamDB lookup table."""

    @patch("app.tasks.steamdb.download_dump")
    def test_refresh_persists_only_valid_hltb_records(self, mock_download):
        """A refresh replaces old records with parsed AppID mappings."""
        SteamDBHltbMapping.objects.create(
            steam_app_id="999", hltb_id="9999", synced_at="2025-01-01T00:00:00Z"
        )

        def write_dump(_url, destination):
            with gzip.open(destination, "wt", encoding="utf-8") as dump:
                json.dump(
                    [
                        {"steam_appid": 292030, "hltb_url": "https://howlongtobeat.com/game/71481"},
                        {"steam_appid": 570, "hltb_url": "https://howlongtobeat.com/game/2721"},
                        {"steam_appid": 8, "hltb_url": "https://invalid.example/game/8"},
                    ],
                    dump,
                )

        mock_download.side_effect = write_dump
        result = refresh_steamdb_hltb_mappings.run()

        self.assertEqual(result["mappings"], 2)
        self.assertFalse(SteamDBHltbMapping.objects.filter(steam_app_id="999").exists())
        self.assertEqual(
            SteamDBHltbMapping.objects.get(steam_app_id="292030").hltb_id,
            "71481",
        )
        self.assertEqual(
            SteamDBHltbMapping.objects.get(steam_app_id="570").hltb_id,
            "2721",
        )

    @patch("app.tasks.steamdb.download_dump")
    def test_invalid_empty_snapshot_preserves_existing_records(self, mock_download):
        """An unusable snapshot does not erase last-good persisted data."""
        existing = SteamDBHltbMapping.objects.create(
            steam_app_id="292030", hltb_id="71481", synced_at="2025-01-01T00:00:00Z"
        )

        def write_empty_dump(_url, destination):
            with gzip.open(destination, "wt", encoding="utf-8") as dump:
                dump.write("[]")

        mock_download.side_effect = write_empty_dump
        with self.assertRaises(ValueError):
            refresh_steamdb_hltb_mappings.run()

        self.assertEqual(
            SteamDBHltbMapping.objects.get(steam_app_id="292030").pk,
            existing.pk,
        )

    def test_lookup_command_reads_processed_rows_only(self):
        """The lookup command returns persisted mappings without a dump file."""
        SteamDBHltbMapping.objects.create(
            steam_app_id="292030", hltb_id="71481", synced_at="2025-01-01T00:00:00Z"
        )
        output = StringIO()
        call_command("steamdb_hltb_lookup", "292030", "570", stdout=output)
        self.assertEqual(json.loads(output.getvalue()), {"292030": "71481"})
