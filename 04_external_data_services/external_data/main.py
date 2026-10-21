from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Query

from external_data.adapters import (
    Air4ThaiPm25,
    FirmsHotspots,
    OpenMeteoForecast,
    OpenMeteoRiverDischarge,
    ThaiWaterRain24h,
    ThaiWaterWaterLevel,
    TmdWarnings,
    TmdWeather3Hours,
)
from thanphai_shared import OfficialWarning, SourceHealth, SourceRecord, redact

redact.install()
app = FastAPI(title="ThanPhai External Data Services", version="0.1.0")

STATION_SOURCES = {
    "thaiwater_rain_24h": ThaiWaterRain24h,
    "thaiwater_waterlevel": ThaiWaterWaterLevel,
    "nasa_firms": FirmsHotspots,
    "tmd_weather_3h": TmdWeather3Hours,
    "air4thai": Air4ThaiPm25,
}
POINT_SOURCES = {"open_meteo_forecast": OpenMeteoForecast, "open_meteo_flood": OpenMeteoRiverDischarge}
HEADERS = {"User-Agent": "ThanPhai/0.1 (student project)"}

health: dict[str, SourceHealth] = {}


def _newest(records: list[SourceRecord]) -> datetime | None:
    # Freshness is when the data was produced: observed_at for observations, issued_at for forecasts.
    # Never use valid_time, or a 3-day-old forecast would look fresh until its window ends.
    times = [r.observed_at or r.issued_at for r in records if (r.observed_at or r.issued_at)]
    return max(times) if times else None


async def _run(name: str, adapter) -> list[SourceRecord]:
    now = datetime.now(timezone.utc)
    previous = health.get(name)
    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
            records = await adapter.run(client)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        health[name] = SourceHealth(
            # httpx errors include the request URL, and FIRMS keeps its key in the URL path.
            source=name, ok=False, last_error=redact.redact(f"{type(exc).__name__}: {exc}")[:300],
            last_success=previous.last_success if previous else None,
        )
        raise HTTPException(status_code=502, detail=f"{name} failed: {type(exc).__name__}") from exc
    health[name] = SourceHealth(source=name, ok=True, record_count=len(records), last_success=now, newest_data_at=_newest(records))
    return records


@app.get("/health")
def get_health() -> dict:
    return {"status": "ok"}


@app.get("/sources", response_model=list[SourceHealth])
def get_sources() -> list[SourceHealth]:
    names = [*STATION_SOURCES, *POINT_SOURCES, "tmd_warnings"]
    return [health.get(n, SourceHealth(source=n, ok=False, last_error="not fetched yet")) for n in names]


@app.get("/v1/sources/{name}", response_model=list[SourceRecord])
async def fetch_source(
    name: str,
    lat: float | None = Query(None, ge=5.6, le=20.5),
    lon: float | None = Query(None, ge=97.3, le=105.7),
    limit: int = Query(500, ge=1, le=20000),
) -> list[SourceRecord]:
    if name in STATION_SOURCES:
        adapter = STATION_SOURCES[name]()
    elif name in POINT_SOURCES:
        if lat is None or lon is None:
            raise HTTPException(status_code=422, detail="lat and lon are required for point sources")
        adapter = POINT_SOURCES[name](lat, lon)
    else:
        raise HTTPException(status_code=404, detail=f"unknown source {name}")
    return (await _run(name, adapter))[:limit]


@app.get("/v1/warnings", response_model=list[OfficialWarning])
async def fetch_warnings() -> list[OfficialWarning]:
    adapter = TmdWarnings()
    now = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(headers=HEADERS) as client:
            warnings = await adapter.run_warnings(client)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        health["tmd_warnings"] = SourceHealth(source="tmd_warnings", ok=False, last_error=type(exc).__name__)
        raise HTTPException(status_code=502, detail="tmd_warnings failed") from exc
    health["tmd_warnings"] = SourceHealth(
        source="tmd_warnings", ok=True, record_count=len(warnings), last_success=now,
        newest_data_at=max((w.announced_at for w in warnings), default=None),
    )
    return warnings
