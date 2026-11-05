import csv
import io
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from external_data.adapters.base import THAILAND_BBOX, Adapter, in_thailand_bbox, to_float
from thanphai_shared import SourceRecord

KEYLESS_URL = (
    "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_SouthEast_Asia_{window}.csv"
)
AREA_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{bbox}/{days}"


class FirmsHotspots(Adapter):
    """VIIRS active fire hotspots over Thailand, or over a larger `bbox` (west, south, east, north).

    Without FIRMS_MAP_KEY it reads NASA's public South-East Asia file; with a key it uses the area API.
    """

    name = "nasa_firms"
    attribution = "Fire data: NASA FIRMS"
    timeout_seconds = 60.0

    def __init__(self, window: str = "24h", map_key: str | None = None, source: str = "VIIRS_SNPP_NRT",
                 bbox: tuple = THAILAND_BBOX):
        if window not in ("24h", "48h", "7d"):
            raise ValueError("window must be 24h, 48h, or 7d")
        self.window = window
        self.map_key = map_key if map_key is not None else os.getenv("FIRMS_MAP_KEY") or None
        self.source = source
        self.bbox = bbox

    @property
    def url(self) -> str:
        if self.map_key:
            days = {"24h": 1, "48h": 2, "7d": 7}[self.window]
            return AREA_URL.format(key=self.map_key, source=self.source, bbox=",".join(map(str, self.bbox)), days=days)
        return KEYLESS_URL.format(window=self.window)

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        response = await client.get(self.url, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.text

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        records = []
        for row in csv.DictReader(io.StringIO(raw)):
            lat, lon = to_float(row.get("latitude")), to_float(row.get("longitude"))
            if lat is None or lon is None or not in_thailand_bbox(lat, lon, self.bbox):
                continue
            acq = row["acq_date"] + row["acq_time"].zfill(4)
            records.append(
                SourceRecord(
                    source=self.name,
                    variable="fire_hotspot",
                    value=to_float(row.get("frp")),
                    unit="MW",
                    lat=lat,
                    lon=lon,
                    # FIRMS acquisition times are UTC.
                    observed_at=datetime.strptime(acq, "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc),
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                    extra={
                        "confidence": row.get("confidence"),
                        "satellite": row.get("satellite"),
                        "daynight": row.get("daynight"),
                        "bright_ti4": to_float(row.get("bright_ti4")),
                    },
                )
            )
        return records
