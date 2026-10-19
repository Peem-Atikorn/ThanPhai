import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from external_data.adapters.tmd_nwp import URL, TmdNwpDailyForecast

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tmd_nwp_daily_place.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def test_parse_maps_geocode_to_p_codes():
    records = TmdNwpDailyForecast(["x"]).parse({"entries": FIXTURE["WeatherForecasts"]}, NOW)
    codes = {r.extra["area_code"] for r in records}
    assert "TH14" in codes and "TH1401" in codes
    r = records[0]
    assert r.variable == "forecast_rain_24h" and r.unit == "mm"
    assert r.issued_at == NOW and r.valid_time.utcoffset().total_seconds() == 7 * 3600
    assert r.extra["cond_th"] and r.extra["wind_max_kmh"] is not None


async def test_missing_token_is_an_error():
    with pytest.raises(ValueError):
        async with httpx.AsyncClient() as client:
            await TmdNwpDailyForecast(["พระนครศรีอยุธยา"], token="").fetch(client)


@respx.mock
async def test_rejected_token_raises_once():
    respx.get(URL).mock(return_value=httpx.Response(401, json={"error": "Unauthenticated."}))
    with pytest.raises(PermissionError):
        async with httpx.AsyncClient() as client:
            await TmdNwpDailyForecast(["ก", "ข", "ค"], token="bad").fetch(client)


@respx.mock
async def test_some_provinces_failing_keeps_the_rest():
    def reply(request):
        if request.url.params["province"] == "ข":
            return httpx.Response(500)
        return httpx.Response(200, json=FIXTURE)

    route = respx.get(URL).mock(side_effect=reply)
    async with httpx.AsyncClient() as client:
        raw = await TmdNwpDailyForecast(["ก", "ข"], token="t").fetch(client)
    assert raw["failed"] == ["ข"] and len(raw["entries"]) == len(FIXTURE["WeatherForecasts"])
    assert route.calls[0].request.headers["authorization"] == "Bearer t"
    assert route.calls[0].request.url.params["subarea"] == "1"


@respx.mock
async def test_rate_limit_waits_for_retry_after_then_succeeds(monkeypatch):
    # Live 2026-10-19: 17 of 77 provinces got 429 (limit 60/min, retry-after 33).
    import external_data.adapters.tmd_nwp as mod

    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    route = respx.get(URL).mock(side_effect=[
        httpx.Response(429, headers={"retry-after": "33"}),
        httpx.Response(200, json=FIXTURE),
    ])
    async with httpx.AsyncClient() as client:
        raw = await TmdNwpDailyForecast(["ยะลา"], token="t").fetch(client)
    assert raw["failed"] == [] and raw["entries"]
    assert waits == [34.0] and route.call_count == 2


@respx.mock
async def test_all_provinces_failing_is_an_error():
    respx.get(URL).mock(return_value=httpx.Response(503))
    with pytest.raises(RuntimeError):
        async with httpx.AsyncClient() as client:
            await TmdNwpDailyForecast(["ก", "ข"], token="t").fetch(client)
