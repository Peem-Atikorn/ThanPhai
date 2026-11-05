"""Active tropical storms tracked by HII (ThaiWater `storm_data`), with ThaiWater's Saffir-Simpson category.

A second source next to GDACS for the cyclone tab: it gives the storm's category in Thai terms but no wind areas.
`storm_wind` is in knots (the payload's `setting` bands are in knots, with km/h and mph texts). The list is empty
when there is no storm. Positions are at the synoptic hours 00/06/12/18, so `storm_datetime` is read as UTC
(like ThaiWater's radar times); it has no zone.
"""

from datetime import datetime, timezone
from typing import Any

from external_data.adapters.base import to_float
from external_data.adapters.thaiwater import ATTRIBUTION, _ThaiWater
from thanphai_shared import SourceRecord

KNOT_KMH = 1.852
OPS = {">": lambda v, t: v > t, ">=": lambda v, t: v >= t, "<": lambda v, t: v < t}


def category(setting: list[dict], wind_kt: float | None) -> dict | None:
    """ThaiWater's band for this wind (bands are listed strongest first)."""
    if wind_kt is None:
        return None
    for band in setting:
        check = OPS.get(band.get("operator"))
        threshold = to_float(band.get("term"))
        if check and threshold is not None and check(wind_kt, threshold):
            return {"category": band.get("category"), "strength": band.get("strength"), "kmh_text": band.get("kmh_text"),
                    "color": band.get("color"), "level": band.get("level")}
    return None


class ThaiWaterStorms(_ThaiWater):
    name = "thaiwater_storms"
    endpoint = "storm_data"
    attribution = ATTRIBUTION
    timeout_seconds = 30.0

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        """One record per track point (variable "storm_wind_kt"); `extra.latest` marks each storm's newest point."""
        setting = raw.get("setting") or []
        records = []
        for storm in raw.get("data") or []:
            points = []
            for p in storm.get("storm_data") or []:
                lat, lon = to_float(p.get("storm_lat")), to_float(p.get("storm_long"))
                if lat is None or lon is None or not p.get("storm_datetime"):
                    continue
                lat = -lat if p.get("storm_direction_lat") == "S" else lat
                lon = -lon if p.get("storm_direction_long") == "W" else lon
                when = datetime.strptime(p["storm_datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                points.append((when, lat, lon, to_float(p.get("storm_wind")), to_float(p.get("storm_pressure"))))
            points.sort()
            for i, (when, lat, lon, wind, pressure) in enumerate(points):
                records.append(
                    SourceRecord(
                        source=self.name,
                        variable="storm_wind_kt",
                        value=wind,
                        unit="kt",
                        lat=lat,
                        lon=lon,
                        observed_at=when,
                        fetched_at=fetched_at,
                        attribution=self.attribution,
                        extra={
                            "name": storm.get("storm_name"),
                            "wind_kmh": round(wind * KNOT_KMH) if wind is not None else None,
                            "pressure_hpa": pressure,
                            "category": category(setting, wind),
                            "latest": i == len(points) - 1,
                        },
                    )
                )
        return records
