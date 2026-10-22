from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from external_data.adapters.base import Adapter
from thanphai_shared import SourceRecord

# South-East Asia and the seas around it, where cyclones, floods and earthquakes can reach Thailand or its
# neighbours. West, south, east, north.
GDACS_BBOX = (88.0, -11.0, 135.0, 30.0)
EVENT_TYPES = {"EQ": "แผ่นดินไหว", "TC": "พายุหมุนเขตร้อน", "FL": "น้ำท่วม", "TS": "สึนามิ", "VO": "ภูเขาไฟ", "DR": "ภัยแล้ง"}
ALERT_TH = {"Green": "เขียว", "Orange": "ส้ม", "Red": "แดง"}
LOOK_BACK_DAYS = {"EQ": 7, "TC": 30, "FL": 30}


class GdacsEvents(Adapter):
    """GDACS (UN/EU) disaster events in GDACS_BBOX that are current or ended within `days` (no key)."""

    name = "gdacs"
    attribution = "Global Disaster Alert and Coordination System (GDACS), UN / European Commission"
    url = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
    timeout_seconds = 45.0

    def __init__(self, days: int = 7):
        self.days = days

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        today = datetime.now(timezone.utc).date()
        features = []
        # One call per type: a combined list stops at 100 events worldwide (seen 2026-10-22, cyclones cut off).
        # fromdate filters on when an event started, so cyclones and floods that began weeks ago and are still
        # going need a longer look-back; parse() keeps only the recent ones.
        for event_type, look_back in LOOK_BACK_DAYS.items():
            params = {"eventlist": event_type, "fromdate": (today - timedelta(days=look_back)).isoformat(),
                      "todate": today.isoformat(), "alertlevel": "green;orange;red"}
            response = await client.get(self.url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            # No events comes back as an empty body, not as an empty list.
            features += (response.json() if response.content else {}).get("features") or []
        return {"features": features}

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        west, south, east, north = GDACS_BBOX
        records = []
        for feature in raw.get("features") or []:
            p = feature["properties"]
            lon, lat = feature["geometry"]["coordinates"][:2]
            if not (south <= lat <= north and west <= lon <= east):
                continue
            # GDACS dates have no zone; they are UTC.
            last = datetime.fromisoformat(p["todate"]).replace(tzinfo=timezone.utc)
            current = str(p.get("iscurrent")).lower() == "true"
            if not current and fetched_at - last > timedelta(days=self.days):
                continue
            severity = p.get("severitydata") or {}
            records.append(
                SourceRecord(
                    source=self.name,
                    variable=f"gdacs_{p['eventtype'].lower()}",
                    value=float(p.get("alertscore") or 0),
                    unit="alert score",
                    lat=lat,
                    lon=lon,
                    station_id=f"{p['eventtype']}{p['eventid']}",
                    observed_at=last,
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                    extra={
                        "event_type": p["eventtype"],
                        "type_th": EVENT_TYPES.get(p["eventtype"], p["eventtype"]),
                        "name": p.get("name"),
                        "alert": p.get("alertlevel"),
                        "alert_th": ALERT_TH.get(p.get("alertlevel"), p.get("alertlevel")),
                        "countries": p.get("country"),
                        "severity": severity.get("severity"),
                        "severity_text": severity.get("severitytext"),
                        "from": p.get("fromdate"),
                        "current": current,
                        "url": (p.get("url") or {}).get("report"),
                    },
                )
            )
        return records
