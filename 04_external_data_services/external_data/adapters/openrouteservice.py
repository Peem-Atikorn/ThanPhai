"""openrouteservice (HeiGIT) directions: a road route between two points, optionally around areas to avoid.

The key goes in the Authorization header (no "Bearer"); it is never put in a URL or an error message.
api.openrouteservice.org is being retired (November 2026), so this uses api.heigit.org.
Free plan: 2,000 directions a day, 40 a minute (x-ratelimit-* headers).
"""

import os
from typing import Any

import httpx

ORS_URL = "https://api.heigit.org/openrouteservice/v2/directions/{profile}/geojson"
PROFILES = ("driving-car", "foot-walking")
ATTRIBUTION = "เส้นทาง: openrouteservice (HeiGIT) · ข้อมูลถนน © OpenStreetMap contributors"


class RouteError(Exception):
    """openrouteservice gave no route. `code` is ORS's error code when it sent one (e.g. 2004: avoid area too big)."""

    def __init__(self, message: str, status: int | None = None, code: int | None = None):
        super().__init__(message)
        self.status, self.code = status, code


def parse_route(payload: dict) -> dict:
    """The first route of an ORS GeoJSON answer: line (lon, lat), metres, seconds."""
    features = payload.get("features") or []
    if not features:
        raise RouteError("no route in the answer")
    feature = features[0]
    summary = feature.get("properties", {}).get("summary", {})
    return {
        "geometry": feature["geometry"],
        "distance_m": round(float(summary.get("distance", 0.0)), 1),
        "duration_s": round(float(summary.get("duration", 0.0)), 1),
    }


async def fetch_route(client: httpx.AsyncClient, start: tuple[float, float], end: tuple[float, float],
                      profile: str = "driving-car", avoid: dict | None = None, api_key: str | None = None,
                      timeout: float = 30.0) -> dict:
    """Route from start to end, each (lat, lon). `avoid`: a GeoJSON Polygon or MultiPolygon the route must not enter."""
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {PROFILES}")
    key = api_key if api_key is not None else os.getenv("ORS_API_KEY", "")
    if not key:
        raise RouteError("ORS_API_KEY is not set")
    body: dict[str, Any] = {"coordinates": [[start[1], start[0]], [end[1], end[0]]], "instructions": False}
    if avoid:
        body["options"] = {"avoid_polygons": avoid}
    try:
        response = await client.post(ORS_URL.format(profile=profile), json=body,
                                     headers={"Authorization": key, "Accept": "application/geo+json"}, timeout=timeout)
    except httpx.HTTPError as error:
        raise RouteError(f"openrouteservice did not answer ({type(error).__name__})") from None
    if response.status_code != 200:
        code = None
        try:
            code = response.json().get("error", {}).get("code")
        except (ValueError, AttributeError):
            pass
        # 403: key wrong or the day's quota used up; 429: too many a minute.
        raise RouteError(f"openrouteservice answered {response.status_code}", response.status_code, code)
    return parse_route(response.json())
