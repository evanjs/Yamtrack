import json
import tempfile
from pathlib import Path
from unittest import TestCase

from app.providers.steamdb import find_hltb_matches, load_hltb_index


class SteamDBIndexTests(TestCase):
    """Test SteamDB AppID-to-HLTB index extraction."""

    def test_hltb_index_maps_steam_app_ids_to_hltb_ids(self):
        """Resolve valid SteamDB HLTB URLs while ignoring incomplete records."""
        with tempfile.TemporaryDirectory() as directory:
            dump = Path(directory) / "steamdb.json"
            dump.write_text(
                json.dumps(
                    [
                        {"steam_appid": 10, "hltb_url": "https://howlongtobeat.com/game/12345"},
                        {"steam_appid": 11, "hltb_url": None},
                        {"steam_appid": 12, "hltb_url": "https://example.com/game/9"},
                    ],
                ),
                encoding="utf-8",
            )

            index = load_hltb_index(dump)

        self.assertEqual(index, {"10": "12345"})
        self.assertEqual(
            find_hltb_matches(["10", "11", "99"], index),
            {"10": "12345"},
        )
