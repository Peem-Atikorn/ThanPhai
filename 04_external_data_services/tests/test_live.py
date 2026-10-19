"""Live checks against the real providers. Run with: pytest -m live"""

import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from external_data.adapters import (
    FirmsHotspots,
    OpenMeteoForecast,
    OpenMeteoRiverDischarge,
    ThaiWaterRain24h,
    ThaiWaterWaterLevel,
    TmdWarnings,
    TmdWeather3Hours,
)

pytestmark = pytest.mark.live
HEADERS = {"User-Agent": "ThanPhai/0.1 (student project)"}


async def run(adapter):
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        return await adapter.run(client)


def assert_fresh(records, max_age: timedelta):
    newest = max(r.observed_at for r in records if r.observed_at)
    assert datetime.now(timezone.utc) - newest < max_age, f"newest data is {newest}"


async def test_open_meteo_forecast_live():
    records = await run(OpenMeteoForecast(13.75, 100.50))
    assert len(records) >= 72 * 3


async def test_open_meteo_flood_live():
    assert len(await run(OpenMeteoRiverDischarge(15.70, 100.13))) >= 7


async def test_thaiwater_rain_live():
    records = await run(ThaiWaterRain24h())
    assert len(records) > 500
    assert_fresh(records, timedelta(days=2))


async def test_thaiwater_waterlevel_live():
    records = await run(ThaiWaterWaterLevel())
    assert len(records) > 300
    assert_fresh(records, timedelta(hours=12))
    assert any(r.extra["above_bank_m"] is not None and r.extra["above_bank_m"] < 0 for r in records)


async def test_thaiwater_dams_live():
    from external_data.adapters.thaiwater_dams import ThaiWaterDams

    records = await run(ThaiWaterDams())
    assert len(records) >= 30  # 35 large dams
    assert_fresh(records, timedelta(days=3))
    assert all(r.extra["status"] in {"HIGH", "LOW_CRITICAL", "NORMAL", "UNKNOWN"} for r in records)


async def test_firms_keyless_live():
    records = await run(FirmsHotspots(map_key=""))
    # Can legitimately be 0 in the rainy season; the parse must still succeed.
    assert isinstance(records, list)


async def test_tmd_weather_3h_live():
    records = await run(TmdWeather3Hours())
    if not records:
        # Seen 2026-10-19 19:04: right after an observation round TMD answers 200 with "Stations": {}.
        pytest.skip("TMD published no stations this round")
    assert len(records) > 50
    assert_fresh(records, timedelta(hours=12))


@pytest.mark.skipif(not os.getenv("TMD_NWP_TOKEN"), reason="TMD_NWP_TOKEN not set")
async def test_tmd_nwp_daily_live():
    from external_data.adapters.tmd_nwp import TmdNwpDailyForecast

    records = await run(TmdNwpDailyForecast(["พระนครศรีอยุธยา"], days=2))
    assert {r.extra["area_code"] for r in records} >= {"TH14", "TH1412"}  # province + อ.เสนา


async def test_tmd_warnings_v2_live():
    async with httpx.AsyncClient(headers=HEADERS) as client:
        warnings = await TmdWarnings().run_warnings(client)
    for w in warnings:
        # v1 served a 2022 item; v2 must not be years old.
        assert w.announced_at.year >= 2025


async def test_thaiwater_media_images_and_series_live():
    from external_data.adapters.thaiwater import BASE_URL
    from external_data.adapters.thaiwater_graphs import fetch_dam_year, fetch_station_graph
    from external_data.adapters.thaiwater_media import parse_media

    async with httpx.AsyncClient(follow_redirects=True) as client:
        raw = (await client.get(f"{BASE_URL}/thailand_main", timeout=90)).json()
        media = parse_media(raw)
        assert media["radar"] and media["rain_forecast"]["thailand"]["days"]
        # The token URL must serve the image itself.
        image = await client.get(media["radar"][0]["thumb"], timeout=30)
        assert image.status_code == 200 and image.headers["content-type"].startswith("image/")
        graph = await fetch_station_graph(client, 2744, 2)  # C.13, below the Chao Phraya Dam
        assert graph["points"] and graph["qmax_m3s"]
        year = await fetch_dam_year(client, 1, "storage")  # Bhumibol
        assert year["series"] and year["upper_rule_curve"]
