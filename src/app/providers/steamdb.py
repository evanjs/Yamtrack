import gzip
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

STEAMDB_DUMP_URL = (
    "https://github.com/leinstay/steamdb/releases/latest/download/steamdb.min.json.gz"
)

_HLTB_ID = re.compile(r"^/game/(\d+)/?$")
_STEAM_APP_ID_MAX_LENGTH = 20
_CATALOG_FIELDS = (
    "id",
    "kind",
    "name",
    "steam_url",
    "gog_id",
    "gog_url",
    "hltb_url",
    "platforms",
    "developers",
    "publishers",
    "languages",
    "voiceovers",
    "categories",
    "genres",
    "tags",
    "achievements",
    "steam_reviews_percent",
    "steam_reviews_count",
    "steam_reviews_label",
    "steam_recent_percent",
    "steam_recent_count",
    "steam_recent_label",
    "updated_at",
)


def load_steamdb_index(path):
    """Load selected SteamDB catalog metadata indexed by valid Steam AppID."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as dump:
        records = json.load(dump)

    if not isinstance(records, list):
        msg = "SteamDB dump must be a JSON array."
        raise TypeError(msg)

    index = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        app_id = record.get("steam_appid")
        if not isinstance(app_id, (int, str)) or isinstance(app_id, bool):
            continue
        app_id = str(app_id)
        if (
            not app_id.isdecimal()
            or int(app_id) <= 0
            or len(app_id) > _STEAM_APP_ID_MAX_LENGTH
        ):
            continue
        metadata = {field: record.get(field) for field in _CATALOG_FIELDS}
        metadata["steam_appid"] = app_id
        index[app_id] = metadata
    return index


def load_hltb_index(path):
    """Load SteamDB JSON (plain or gzip) as a Steam AppID → HLTB ID index."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as dump:
        records = json.load(dump)

    if not isinstance(records, list):
        msg = "SteamDB dump must be a JSON array."
        raise TypeError(msg)

    index = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        app_id = record.get("steam_appid")
        link = record.get("hltb_url")
        if not isinstance(app_id, (int, str)) or isinstance(app_id, bool):
            continue
        app_id = str(app_id)
        if (
            not app_id.isdecimal()
            or int(app_id) <= 0
            or len(app_id) > _STEAM_APP_ID_MAX_LENGTH
            or not isinstance(link, str)
        ):
            continue
        hltb_id = parse_hltb_id(link)
        if hltb_id:
            index[app_id] = hltb_id
    return index


def parse_hltb_id(link):
    """Return a validated HLTB game ID from a canonical HLTB URL."""
    if not isinstance(link, str):
        return None
    parsed = urlparse(link)
    match = _HLTB_ID.fullmatch(parsed.path)
    if parsed.scheme == "https" and parsed.netloc in {
        "howlongtobeat.com",
        "www.howlongtobeat.com",
    } and match:
        return match.group(1)
    return None


def download_dump(url, destination):
    """Download a compressed SteamDB snapshot to a temporary path."""
    response = requests.get(url, stream=True, timeout=(15, 300))
    try:
        response.raise_for_status()
        with Path(destination).open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
    finally:
        response.close()


def find_hltb_matches(app_ids, hltb_index):
    """Return matched Steam AppIDs and HLTB IDs for a collection of app IDs."""
    return {
        str(app_id): hltb_index[str(app_id)]
        for app_id in app_ids
        if str(app_id) in hltb_index
    }
