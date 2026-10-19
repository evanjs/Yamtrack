import re

from app.providers import services

APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAM_APP_ID_PATTERN = re.compile(r"^[0-9]{1,20}$")


def app_ratings(steam_app_id):
    """Fetch and return one app's complete Steam agency ratings object."""
    app_id = str(steam_app_id)
    if isinstance(steam_app_id, bool) or not STEAM_APP_ID_PATTERN.fullmatch(app_id):
        msg = f"Invalid Steam App ID: {steam_app_id!r}"
        raise ValueError(msg)
    if int(app_id) <= 0:
        msg = f"Invalid Steam App ID: {steam_app_id!r}"
        raise ValueError(msg)

    response = services.api_request(
        "steam",
        "GET",
        APP_DETAILS_URL,
        params={"appids": app_id, "l": "en"},
    )
    app_response = response.get(app_id) if isinstance(response, dict) else None
    if (
        not isinstance(app_response, dict)
        or app_response.get("success") is not True
        or not isinstance(app_response.get("data"), dict)
    ):
        msg = f"Steam appdetails returned no successful data for App ID {app_id}."
        raise ValueError(msg)

    ratings = app_response["data"].get("ratings")
    if not isinstance(ratings, dict) or any(
        not isinstance(key, str) for key in ratings
    ):
        msg = f"Steam appdetails returned no valid ratings object for App ID {app_id}."
        raise ValueError(msg)
    return ratings
