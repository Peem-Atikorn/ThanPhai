from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from external_data.adapters.base import Adapter
from external_data.adapters.gdacs import GDACS_BBOX
from thanphai_shared import SourceRecord

LIST_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
GEOMETRY_URL = "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry"
# GDACS wind buffers along the whole track, forecast included: the lowest wind speed inside each class (km/h).
# Green = tropical-storm force (39 mph), Orange = 58 mph, Red = hurricane/typhoon force (74 mph).
WIND_CLASSES = {"Poly_Green": 63, "Poly_Orange": 93, "Poly_Red": 119}


class GdacsCyclones(Adapter):
    """Wind areas and track points of the tropical cyclones GDACS lists as current in GDACS_BBOX (no key)."""

    name = "gdacs_cyclones"
    attribution = "Global Disaster Alert and Coordination System (GDACS), UN / European Commission"
    timeout_seconds = 60.0

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        today = datetime.now(timezone.utc).date()
        # 30 days: fromdate filters on when a cyclone started (see GdacsEvents).
        params = {"eventlist": "TC", "fromdate": (today - timedelta(days=30)).isoformat(), "todate": today.isoformat(),
                  "alertlevel": "green;orange;red"}
        response = await client.get(LIST_URL, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        events = (response.json() if response.content else {}).get("features") or []
        west, south, east, north = GDACS_BBOX
        out = []
        for event in events:
            p = event["properties"]
            lon, lat = event["geometry"]["coordinates"][:2]
            if str(p.get("iscurrent")).lower() != "true" or not (south <= lat <= north and west <= lon <= east):
                continue
            response = await client.get(GEOMETRY_URL, params={"eventtype": "TC", "eventid": p["eventid"],
                                                              "episodeid": p["episodeid"]}, timeout=self.timeout_seconds)
            response.raise_for_status()
            out.append(response.json())
        return out

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        """WindRadii polygons (one per class and forecast time) and PointRadii track points.

        Seen 2026-10-22: track points are small circles labelled "14/10 12:00 UTC"; `polygondate` is when GDACS
        issued the track (GDACS dates have no zone; they are UTC), so later points are the forecast.
        """
        records = []
        for collection in raw:
            for feature in collection.get("features") or []:
                p, geometry = feature["properties"], feature["geometry"]
                issued = datetime.fromisoformat(p["polygondate"]).replace(tzinfo=timezone.utc) if p.get("polygondate") else None
                common = {"event": p.get("eventname"), "name": p.get("name"), "event_id": f"TC{p.get('eventid')}"}
                if p.get("featuretype") == "WindRadii" and p.get("Class") in WIND_CLASSES:
                    records.append(SourceRecord(
                        source=self.name, variable="cyclone_wind_area", value=float(WIND_CLASSES[p["Class"]]), unit="km/h",
                        issued_at=issued, valid_time=_label_time(p.get("polygonlabel"), issued), fetched_at=fetched_at,
                        attribution=self.attribution, extra={**common, "geometry": geometry}))
                elif p.get("featuretype") == "PointRadii" and geometry["type"] == "Polygon":
                    ring = geometry["coordinates"][0]
                    when = _label_time(p.get("polygonlabel"), issued)
                    records.append(SourceRecord(
                        source=self.name, variable="cyclone_track_point", value=None, unit="",
                        lat=round(sum(c[1] for c in ring) / len(ring), 3), lon=round(sum(c[0] for c in ring) / len(ring), 3),
                        issued_at=issued, valid_time=when, fetched_at=fetched_at, attribution=self.attribution,
                        extra={**common, "forecast": bool(when and issued and when > issued)}))
        return records


def _label_time(label: str | None, issued: datetime | None) -> datetime | None:
    """"22/10 06:00" or "14/10 12:00 UTC" -> UTC datetime, taking the year from the issue time."""
    if not label or issued is None:
        return None
    try:
        # The year goes into the string, not .replace(year=...): "29/02" only parses with a leap year present.
        when = datetime.strptime(f"{issued.year}/{label.replace(' UTC', '').strip()}", "%Y/%d/%m %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    # A track issued on 2 January can still list 30 December.
    return when.replace(year=issued.year - 1) if when - issued > timedelta(days=180) else when
