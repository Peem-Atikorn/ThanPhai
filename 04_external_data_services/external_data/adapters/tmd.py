import os
from datetime import datetime, timezone
from typing import Any

import httpx

from external_data.adapters.base import Adapter, parse_local, to_float
from thanphai_shared import HazardType, OfficialWarning, SourceRecord

BASE_URL = "https://data.tmd.go.th/api"
ATTRIBUTION = "ที่มา: กรมอุตุนิยมวิทยา (TMD)"

# Keywords in the Thai warning title that map to our hazards. A warning can match several hazards.
HAZARD_KEYWORDS = {
    HazardType.RAIN_STORM: ("ฝนตกหนัก", "ฝนหนัก", "พายุ", "คลื่นลมแรง", "ดีเปรสชัน", "ไต้ฝุ่น"),
    HazardType.FLOOD: ("น้ำท่วม", "น้ำป่า", "น้ำล้นตลิ่ง", "ฝนตกหนักถึงหนักมาก"),
    HazardType.DROUGHT: ("ภัยแล้ง", "ฝนทิ้งช่วง"),
    HazardType.FIRE: ("ไฟป่า", "หมอกควัน"),
}


def _credentials() -> dict[str, str]:
    # TMD publishes uid=api / ukey=api12345 as a public demo pair for its open services.
    return {"uid": os.getenv("TMD_UID", "api"), "ukey": os.getenv("TMD_UKEY", "api12345"), "format": "json"}


def classify_hazards(text: str) -> list[HazardType]:
    return [hazard for hazard, words in HAZARD_KEYWORDS.items() if any(word in text for word in words)]


def as_list(value: Any) -> list:
    """TMD returns a single object when there is one item and a list when there are several."""
    if value is None or value == {}:
        return []
    return value if isinstance(value, list) else [value]


class TmdWarnings(Adapter):
    """Official weather warnings (WeatherWarningNews v2; v1 is stale)."""

    name = "tmd_warnings"
    attribution = ATTRIBUTION

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        response = await client.get(f"{BASE_URL}/WeatherWarningNews/v2/", params=_credentials(), timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        # Warnings are not numeric records; use parse_warnings(). Kept for the Adapter interface.
        return []

    def parse_warnings(self, raw: Any, fetched_at: datetime) -> list[OfficialWarning]:
        fmt = "%Y-%m-%d %H:%M:%S"
        warnings = []
        for item in as_list(raw.get("Warning")):
            title = (item.get("TitleThai") or "").strip()
            headline = (item.get("HeadlineThai") or "").strip() or None
            announced = parse_local(item.get("AnnounceDate"), fmt)
            if not title or announced is None:
                continue
            warnings.append(
                OfficialWarning(
                    source=self.name,
                    issue_no=str(item.get("IssueNo", "")),
                    title=title,
                    headline=headline,
                    hazards=classify_hazards(f"{title} {headline or ''}"),
                    announced_at=announced,
                    effective_from=parse_local(item.get("EffectStartDate"), fmt),
                    effective_to=parse_local(item.get("EffectEndDate"), fmt),
                    url=item.get("WebUrlThai"),
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                )
            )
        return warnings

    async def run_warnings(self, client: httpx.AsyncClient) -> list[OfficialWarning]:
        fetched_at = datetime.now(timezone.utc)
        return self.parse_warnings(await self.fetch(client), fetched_at)


class TmdWeather3Hours(Adapter):
    """Synoptic station observations every 3 hours."""

    name = "tmd_weather_3h"
    attribution = ATTRIBUTION
    variables = {
        "Rainfall": ("rain_3h", "mm"),
        "Rainfall24Hr": ("rain_24h", "mm"),
        "RelativeHumidity": ("relative_humidity", "%"),
        "AirTemperature": ("air_temperature", "°C"),
        "WindSpeed": ("wind_speed", "km/h"),
    }

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        response = await client.get(f"{BASE_URL}/Weather3Hours/V2/", params=_credentials(), timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        records = []
        for station in as_list((raw.get("Stations") or {}).get("Station")):
            obs = station.get("Observation") or {}
            observed_at = parse_local(obs.get("DateTime"), "%m/%d/%Y %H:%M:%S")
            for key, (variable, unit) in self.variables.items():
                records.append(
                    SourceRecord(
                        source=self.name,
                        variable=variable,
                        value=to_float(obs.get(key)),
                        unit=unit,
                        lat=to_float(station.get("Latitude")),
                        lon=to_float(station.get("Longitude")),
                        station_id=station.get("WmoStationNumber"),
                        observed_at=observed_at,
                        fetched_at=fetched_at,
                        attribution=self.attribution,
                        extra={"station_name_en": station.get("StationNameEnglish"), "province_th": station.get("Province")},
                    )
                )
        return records
