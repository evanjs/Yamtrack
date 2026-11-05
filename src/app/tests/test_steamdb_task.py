import gzip
import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from app.models import SteamDBGameMetadata, SteamDBHltbMapping
from app.providers import steamdb
from app.tasks import refresh_steamdb_hltb_mappings


class SteamDBHltbRefreshTests(TestCase):
    """Test refresh of the persisted shared SteamDB lookup table."""

    def test_loader_extracts_selected_metadata_by_valid_steam_app_id(self):
        """The loader keeps selected metadata and omits unrelated image data."""
        with tempfile.TemporaryDirectory() as directory:
            dump_path = Path(directory) / "steamdb.json.gz"
            with gzip.open(dump_path, "wt", encoding="utf-8") as dump:
                json.dump(
                    [
                        {
                            "steam_appid": 292030,
                            "id": 123,
                            "kind": "steam",
                            "name": "The Witcher 3",
                            "steam_url": "https://store.steampowered.com/app/292030/",
                            "gog_id": 1207658920,
                            "gog_url": "https://www.gog.com/game/the_witcher_3_wild_hunt",
                            "platforms": ["WIN", "MAC", "LNX"],
                            "developers": ["CD PROJEKT RED"],
                            "publishers": ["CD PROJEKT RED"],
                            "languages": ["English"],
                            "voiceovers": ["English"],
                            "categories": ["Single-player"],
                            "genres": ["RPG"],
                            "tags": ["Open World"],
                            "achievements": 78,
                            "steam_reviews_percent": 96,
                            "steam_reviews_count": 700000,
                            "steam_reviews_label": "Overwhelmingly Positive",
                            "steam_recent_percent": 95,
                            "steam_recent_count": 1000,
                            "steam_recent_label": "Overwhelmingly Positive",
                            "updated_at": "2025-01-01T00:00:00Z",
                            "image": "https://example.com/image.jpg",
                        },
                        {"steam_appid": 0, "name": "Invalid"},
                        {"steam_appid": "9" * 21, "name": "Too long"},
                    ],
                    dump,
                )

            result = steamdb.load_steamdb_index(dump_path)

        self.assertEqual(result["292030"]["steam_reviews_percent"], 96)
        self.assertEqual(result["292030"]["platforms"], ["WIN", "MAC", "LNX"])
        self.assertNotIn("image", result["292030"])
        self.assertNotIn("0", result)
        self.assertNotIn("9" * 21, result)

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
        self.assertEqual(
            SteamDBGameMetadata.objects.get(steam_app_id="292030").metadata[
                "steam_appid"
            ],
            "292030",
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
