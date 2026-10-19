"""Monthly rainfall history for SPI from Open-Meteo's ERA5 archive (no key needed).

Open-Meteo counts every 2 weeks of data as one call, so a 35-year daily request costs ~900 of the
10,000 free daily calls. Fetch history once, cache it, and only refresh recent months.
For all 77 provinces, use CHIRPS files instead (see docs/API_REQUIREMENTS.md).
"""

import asyncio
import json
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path

import httpx

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
RATE_LIMIT_WAIT_SECONDS = 65
MAX_RATE_LIMIT_RETRIES = 2


def monthly_from_daily(days: list[str], precipitation: list[float | None]) -> list[tuple[date, float]]:
    """Sum daily rain to calendar months. Months with any missing day are dropped from the end only."""
    totals: OrderedDict[date, float] = OrderedDict()
    missing: set[date] = set()
    for day, value in zip(days, precipitation):
        month = date.fromisoformat(day).replace(day=1)
        totals.setdefault(month, 0.0)
        if value is None:
            missing.add(month)
        else:
            totals[month] += value
    months = list(totals.items())
    # ERA5 lags real time by a few days, so the newest month is usually partial: either it has
    # missing values or the series simply stops before the month ends. Both must be dropped,
    # or a 12-day October would count as a full, very dry month.
    if days:
        last_day = date.fromisoformat(days[-1])
        if (last_day + timedelta(days=1)).month == last_day.month and months:
            missing.add(months[-1][0])
    while months and months[-1][0] in missing:
        months.pop()
    if any(m in missing for m, _ in months):
        raise ValueError("gap in rainfall history; SPI needs a continuous series")
    return [(m, round(v, 2)) for m, v in months]


async def _get_with_rate_limit(client: httpx.AsyncClient, params: dict) -> dict:
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        response = await client.get(ARCHIVE_URL, params=params, timeout=120)
        if response.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
            # Open-Meteo answers "Minutely API request limit exceeded. Please try again in one minute."
            await asyncio.sleep(RATE_LIMIT_WAIT_SECONDS)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("unreachable")


async def fetch_monthly_rain(
    client: httpx.AsyncClient, lat: float, lon: float, start: date, end: date, cache_dir: Path | None = None
) -> list[tuple[date, float]]:
    cache_file = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Keyed by the last complete month, so the cache refreshes once a month.
        cache_file = cache_dir / f"era5_monthly_{lat:.2f}_{lon:.2f}_{start:%Y%m}_{end:%Y%m}.json"
        if cache_file.exists():
            return [(date.fromisoformat(m), v) for m, v in json.loads(cache_file.read_text())]

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "precipitation_sum",
        "timezone": "Asia/Bangkok",
    }
    daily = (await _get_with_rate_limit(client, params))["daily"]
    monthly = monthly_from_daily(daily["time"], daily["precipitation_sum"])
    if cache_file is not None:
        cache_file.write_text(json.dumps([(m.isoformat(), v) for m, v in monthly]))
    return monthly
