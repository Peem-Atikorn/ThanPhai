"""TMD NWP (WRF model) daily forecast for every province and district.

One `daily/place?province=...&subarea=1` call returns the province and all its districts, so the whole
country is 77 calls. The geocode in each entry maps directly to our P-codes: "14" -> TH14, "1401" -> TH1401.
Docs: https://data.tmd.go.th/nwpapi/doc/
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any

import httpx

from external_data.adapters.base import Adapter, to_float
from thanphai_shared import SourceRecord

log = logging.getLogger("tmd_nwp")
URL = "https://data.tmd.go.th/nwpapi/v1/forecast/location/daily/place"
FIELDS = "rain,ws10m,cond"
# The docs give no number, but the API answers x-ratelimit-limit: 60 (per minute) with retry-after on 429.
# 77 provinces therefore take two windows; the rest wait for retry-after.
MAX_CONCURRENT = 4
MAX_RATE_LIMIT_RETRIES = 2
COND_TH = {
    1: "ท้องฟ้าแจ่มใส", 2: "มีเมฆบางส่วน", 3: "เมฆเป็นส่วนมาก", 4: "มีเมฆมาก", 5: "ฝนตกเล็กน้อย", 6: "ฝนปานกลาง",
    7: "ฝนตกหนัก", 8: "ฝนฟ้าคะนอง", 9: "อากาศหนาวจัด", 10: "อากาศหนาว", 11: "อากาศเย็น", 12: "อากาศร้อนจัด",
}


class TmdNwpDailyForecast(Adapter):
    name = "tmd_nwp_daily"
    attribution = "พยากรณ์: กรมอุตุนิยมวิทยา (แบบจำลอง WRF)"
    timeout_seconds = 60.0

    def __init__(self, provinces_th: list[str], days: int = 3, token: str | None = None):
        self.provinces_th = provinces_th
        self.days = days
        self.token = token if token is not None else os.getenv("TMD_NWP_TOKEN", "")

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        if not self.token:
            raise ValueError("TMD_NWP_TOKEN is not set")
        headers = {"Authorization": f"Bearer {self.token}", "accept": "application/json"}
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def one(province: str) -> list[dict]:
            params = {"province": province, "subarea": 1, "fields": FIELDS, "duration": self.days}
            for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
                async with semaphore:
                    response = await client.get(URL, params=params, headers=headers, timeout=self.timeout_seconds)
                if response.status_code == 401:
                    raise PermissionError("TMD NWP token rejected (401)")
                if response.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                    # TMD allows 60 requests/minute and says when to come back (retry-after, seconds).
                    wait = min(float(response.headers.get("retry-after", 30)), 65.0)
                    await asyncio.sleep(wait + 1)
                    continue
                response.raise_for_status()
                return response.json().get("WeatherForecasts", [])
            raise RuntimeError("unreachable")

        results = await asyncio.gather(*(one(p) for p in self.provinces_th), return_exceptions=True)
        entries, failed = [], []
        for province, result in zip(self.provinces_th, results):
            if isinstance(result, PermissionError):
                raise result  # a bad token fails every province; report it instead of 77 warnings
            if isinstance(result, Exception):
                failed.append(province)
                log.warning("TMD NWP %s failed: %s", province, type(result).__name__)
            else:
                entries.extend(result)
        if failed and not entries:
            raise RuntimeError(f"TMD NWP failed for all {len(failed)} provinces")
        return {"entries": entries, "failed": failed}

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        records = []
        for entry in raw.get("entries", []):
            loc = entry.get("location") or {}
            geocode = str(loc.get("geocode") or "")
            if not geocode:
                continue
            for day in entry.get("forecasts", []):
                data = day.get("data") or {}
                cond = data.get("cond")
                records.append(
                    SourceRecord(
                        source=self.name,
                        variable="forecast_rain_24h",
                        value=to_float(data.get("rain")),
                        unit="mm",
                        lat=to_float(loc.get("lat")),
                        lon=to_float(loc.get("lon")),
                        province_code=geocode[:2],
                        issued_at=fetched_at,
                        valid_time=datetime.fromisoformat(day["time"]),
                        fetched_at=fetched_at,
                        attribution=self.attribution,
                        extra={
                            # Our P-code for this area: TH + geocode (province 2 digits, amphoe 4 digits).
                            "area_code": f"TH{geocode}",
                            "areatype": loc.get("areatype"),
                            "wind_max_kmh": round(to_float(data.get("ws10m")) * 3.6, 1) if data.get("ws10m") is not None else None,
                            "cond": cond,
                            "cond_th": COND_TH.get(cond),
                        },
                    )
                )
        return records
