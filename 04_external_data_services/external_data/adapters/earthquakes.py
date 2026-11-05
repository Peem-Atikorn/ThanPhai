import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from external_data.adapters.base import Adapter, to_float
from thanphai_shared import SourceRecord

# Thailand and the regions whose earthquakes can be felt there: Myanmar (Sagaing fault), Laos, southern China,
# the Andaman Sea and northern Sumatra. West, south, east, north.
QUAKE_BBOX = (88.0, -2.0, 112.0, 30.0)
TMD_NS = {"geo": "http://www.w3.org/2003/01/geo/", "tmd": "http://www.earthquake.tmd.go.th"}


class TmdEarthquakes(Adapter):
    """Latest earthquakes from TMD's Earthquake Observation Division (RSS, no key).

    Includes small events near Thailand (M 1-3) that USGS does not list, with a Thai description of
    where they are ("ทางทิศ... ของ อ.แม่สาย จ.เชียงราย ประมาณ 15 กม.").
    """

    name = "tmd_earthquakes"
    attribution = "ข้อมูลแผ่นดินไหว: กองเฝ้าระวังแผ่นดินไหว กรมอุตุนิยมวิทยา"
    url = "https://earthquake.tmd.go.th/feed/rss_tmd.xml"

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        response = await client.get(self.url, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.text

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        records = []
        for item in ET.fromstring(raw.encode("utf-8")).iter("item"):
            lat = to_float(item.findtext("geo:lat", namespaces=TMD_NS))
            lon = to_float(item.findtext("geo:long", namespaces=TMD_NS))
            mag = to_float(item.findtext("tmd:magnitude", namespaces=TMD_NS))
            time = item.findtext("tmd:time", namespaces=TMD_NS)  # "2026-10-21 13:57:50 UTC"
            if lat is None or lon is None or mag is None or not time:
                continue
            link = item.findtext("link") or ""
            records.append(
                SourceRecord(
                    source=self.name,
                    variable="earthquake",
                    value=mag,
                    unit="magnitude",
                    lat=lat,
                    lon=lon,
                    station_id=f"tmd:{link.rsplit('=', 1)[-1]}" if "=" in link else None,
                    observed_at=datetime.strptime(time.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc),
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                    extra={
                        "depth_km": to_float(item.findtext("tmd:depth", namespaces=TMD_NS)),
                        "place_th": (item.findtext("title") or "").strip(),
                        "where_th": (item.findtext("comments") or "").strip(),
                        "url": link or None,
                    },
                )
            )
        return records


class UsgsEarthquakes(Adapter):
    """USGS earthquakes of magnitude >= `min_magnitude` in QUAKE_BBOX over the last `days` days (no key)."""

    name = "usgs_earthquakes"
    attribution = "Earthquake data: USGS"
    url = "https://earthquake.usgs.gov/fdsnws/event/1/query"

    def __init__(self, days: int = 3, min_magnitude: float = 3.5):
        self.days, self.min_magnitude = days, min_magnitude

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        west, south, east, north = QUAKE_BBOX
        start = (datetime.now(timezone.utc) - timedelta(days=self.days)).strftime("%Y-%m-%dT%H:%M:%S")
        params = {"format": "geojson", "starttime": start, "minmagnitude": self.min_magnitude, "orderby": "time",
                  "minlatitude": south, "maxlatitude": north, "minlongitude": west, "maxlongitude": east}
        response = await client.get(self.url, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        records = []
        for feature in raw["features"]:
            p = feature["properties"]
            lon, lat, depth = feature["geometry"]["coordinates"]
            if p.get("mag") is None or p.get("type") != "earthquake":
                continue
            records.append(
                SourceRecord(
                    source=self.name,
                    variable="earthquake",
                    value=float(p["mag"]),
                    unit="magnitude",
                    lat=lat,
                    lon=lon,
                    station_id=f"usgs:{feature['id']}",
                    observed_at=datetime.fromtimestamp(p["time"] / 1000, tz=timezone.utc),
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                    extra={"depth_km": depth, "place": p.get("place"), "url": p.get("url"),
                           "tsunami": bool(p.get("tsunami")), "status": p.get("status")},
                )
            )
        return records


def merge_quakes(tmd: list[SourceRecord], usgs: list[SourceRecord]) -> list[SourceRecord]:
    """One record per earthquake: TMD's (Thai description) when both list it, USGS's otherwise.

    Same event = origin times within 90 s and epicentres within 100 km (the two agencies locate it differently).
    """
    from math import cos, radians

    def same(a: SourceRecord, b: SourceRecord) -> bool:
        if abs((a.observed_at - b.observed_at).total_seconds()) > 90:
            return False
        dx = (a.lon - b.lon) * 111.32 * cos(radians(a.lat))
        dy = (a.lat - b.lat) * 110.57
        return (dx * dx + dy * dy) ** 0.5 <= 100

    return list(tmd) + [u for u in usgs if not any(same(u, t) for t in tmd)]
