"""Refresh behaviour: stale data is served at once, and processing never blocks the event loop."""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import api_backend.main as main
from api_backend.hazard_service import HazardService

DATA = Path(__file__).resolve().parents[2] / "data" / "processed"
pytestmark = pytest.mark.skipif(not (DATA / "gazetteer.json").exists(), reason="run scripts/build_admin_areas.py first")
OLD = [{"hazard": "flood", "level": "WATCH", "observations": {}, "rule_ids": [], "notices": [], "sources": [], "confidence": 1}]
NEW = [{**OLD[0], "level": "CRITICAL"}]


@pytest.fixture(scope="module")
def base_service():
    return HazardService(DATA)


def stale_copy(svc: HazardService) -> HazardService:
    svc.decisions = {code: OLD for code in svc.amphoe_codes}
    svc.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    svc._background = None
    svc._lock = asyncio.Lock()
    return svc


async def test_stale_data_is_served_immediately_and_refreshed_behind(base_service, monkeypatch):
    svc = stale_copy(base_service)

    async def slow_fetch():
        await asyncio.sleep(0.5)
        return {}, []

    monkeypatch.setattr(svc, "_fetch", slow_fetch)
    monkeypatch.setattr(svc, "_process", lambda results, warnings: ({c: NEW for c in svc.amphoe_codes}, [], {}))
    monkeypatch.setattr(main, "service", svc)

    start = time.perf_counter()
    got = await main.fresh_service()
    assert time.perf_counter() - start < 0.1, "a stale cache must not make the request wait"
    assert got.decisions[svc.amphoe_codes[0]] == OLD

    await svc._background
    assert svc.decisions[svc.amphoe_codes[0]] == NEW and not svc.stale


async def test_only_one_background_refresh_at_a_time(base_service, monkeypatch):
    svc = stale_copy(base_service)
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.2)
        return {}, []

    monkeypatch.setattr(svc, "_fetch", fetch)
    monkeypatch.setattr(svc, "_process", lambda results, warnings: ({c: NEW for c in svc.amphoe_codes}, [], {}))
    for _ in range(5):
        svc.refresh_in_background()
    await svc._background
    assert calls == 1


async def test_processing_runs_off_the_event_loop(base_service, monkeypatch):
    svc = stale_copy(base_service)

    async def fetch():
        return {}, []

    def heavy_process(results, warnings):
        time.sleep(0.6)  # stands in for mapping thousands of points
        return {c: NEW for c in svc.amphoe_codes}, [], {}

    monkeypatch.setattr(svc, "_fetch", fetch)
    monkeypatch.setattr(svc, "_process", heavy_process)

    # A ticker runs alongside the refresh; if processing blocks the loop, one gap between ticks is long.
    gaps: list[float] = []

    async def ticker():
        last = time.perf_counter()
        while not refresh.done():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    refresh = asyncio.create_task(svc.refresh())
    await ticker()
    await refresh
    assert max(gaps) < 0.3, f"event loop was blocked for {max(gaps):.2f}s by processing"


async def test_failed_background_refresh_keeps_old_data(base_service, monkeypatch):
    svc = stale_copy(base_service)

    async def broken():
        raise RuntimeError("all providers down")

    monkeypatch.setattr(svc, "_fetch", broken)
    svc.refresh_in_background()
    await svc._background  # must not raise
    assert svc.decisions[svc.amphoe_codes[0]] == OLD


def test_zero_hotspots_and_one_broken_source_still_give_decisions(base_service, monkeypatch):
    """Live case 2026-10-19: FIRMS returned no Thai hotspots. Also force flood processing to fail."""
    import api_backend.hazard_service as hs

    svc = stale_copy(base_service)
    monkeypatch.setattr(hs, "flood_features", lambda rows: (_ for _ in ()).throw(ValueError("bad frame")))
    decisions, _, status = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []}, [])
    first = {item["hazard"]: item for item in decisions[svc.amphoe_codes[0]]}
    assert first["fire"]["level"] == "NORMAL"           # zero hotspots is real data, not missing data
    assert first["flood"]["level"] is None              # broken source -> no data, not "normal"
    assert status["thaiwater_waterlevel"]["ok"] is False and status["nasa_firms"]["ok"] is True


def test_static_heat_sources_do_not_count_as_fire(base_service, monkeypatch):
    """Cement kilns (Saraburi) must not raise the fire level; the forest point still counts."""
    from datetime import datetime, timezone

    from thanphai_shared import SourceRecord

    svc = stale_copy(base_service)
    monkeypatch.setattr(svc, "static_cells", {"1455_10108"})  # one kiln cell near Kaeng Khoi
    now = datetime.now(timezone.utc)

    def hotspot(lat, lon):
        return SourceRecord(source="nasa_firms", variable="fire_hotspot", value=5.0, unit="MW", lat=lat, lon=lon,
                            observed_at=now, fetched_at=now, attribution="t")

    kiln = [hotspot(14.5505, 101.0805) for _ in range(5)]
    kept = svc._drop_static_records(kiln + [hotspot(19.5, 98.9)])
    assert [(r.lat, r.lon) for r in kept] == [(19.5, 98.9)]


async def test_new_satellite_data_rebuilds_without_hiding_data_time(base_service, monkeypatch):
    svc = stale_copy(base_service)
    svc.updated_at = datetime.now(timezone.utc)
    svc._inputs_changed = False
    assert not svc.stale
    svc._inputs_changed = True  # what refresh_satellite / refresh_fire do
    assert svc.stale and svc.updated_at is not None  # UI still shows when the data was fetched


async def test_input_change_during_refresh_is_not_lost(base_service, monkeypatch):
    svc = stale_copy(base_service)

    async def fetch():
        svc._inputs_changed = True  # satellite finished while this refresh was fetching
        return {}, []

    monkeypatch.setattr(svc, "_fetch", fetch)
    monkeypatch.setattr(svc, "_process", lambda results, warnings: ({c: NEW for c in svc.amphoe_codes}, [], {}))
    await svc.refresh()
    assert svc.stale  # the next request rebuilds again with the new input


async def test_failed_slow_job_waits_before_retrying(base_service):
    """A failing TMD/GISTDA job must not restart on every request (it hammered the provider)."""
    import api_backend.hazard_service as hs

    svc = stale_copy(base_service)
    svc._jobs, svc._job_failed_at = {}, {}
    calls = 0

    async def broken():
        nonlocal calls
        calls += 1
        raise PermissionError("token rejected")

    svc._start_job("tmd_nwp_daily", broken, stale=True)
    await svc._jobs["tmd_nwp_daily"]
    for _ in range(5):  # five more requests right after the failure
        svc._start_job("tmd_nwp_daily", broken, stale=True)
    await asyncio.sleep(0)
    assert calls == 1
    assert svc.source_status["tmd_nwp_daily"]["ok"] is False and "next_try_after" in svc.source_status["tmd_nwp_daily"]

    svc._job_failed_at["tmd_nwp_daily"] -= hs.RETRY_AFTER_FAILURE  # the wait is over
    svc._start_job("tmd_nwp_daily", broken, stale=True)
    await svc._jobs["tmd_nwp_daily"]
    assert calls == 2


async def test_slow_job_runs_once_at_a_time_and_clears_failure(base_service):
    svc = stale_copy(base_service)
    svc._jobs, svc._job_failed_at = {}, {"gistda_flood": datetime(2000, 1, 1, tzinfo=timezone.utc)}
    calls = 0

    async def slow_ok():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)

    for _ in range(3):
        svc._start_job("gistda_flood", slow_ok, stale=True)
    await svc._jobs["gistda_flood"]
    assert calls == 1 and "gistda_flood" not in svc._job_failed_at
    svc._start_job("gistda_flood", slow_ok, stale=False)  # fresh data: nothing to do
    assert svc._jobs["gistda_flood"].done() and calls == 1


def test_vegetation_drought_cases(base_service, monkeypatch):
    """Low NDVI is drought only when the drop is real, the area is not urban, and it is not flooded."""
    svc = stale_copy(base_service)
    dry, wet, city, green = svc.amphoe_codes[:4]
    veg = {
        dry: {"vci": 8.0, "ndvi_max": 0.82, "ndvi_deficit": 0.09},    # real stress
        wet: {"vci": 8.0, "ndvi_max": 0.82, "ndvi_deficit": 0.09},    # same numbers, but flooded
        city: {"vci": 0.0, "ndvi_max": 0.53, "ndvi_deficit": 0.06},   # บางพลี-like: buildings, not drought
        green: {"vci": 1.0, "ndvi_max": 0.84, "ndvi_deficit": 0.02},  # บางขัน-like: tiny rainy-season drop
    }
    monkeypatch.setattr(svc, "_current_drought", lambda: {"month": "2026-09", "districts": veg})
    monkeypatch.setattr(svc, "satellite_cells", {wet: {"cells": 500, "approx_km2": 62.0}})
    decisions, _, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []}, [])
    level = lambda code: next(i for i in decisions[code] if i["hazard"] == "drought")  # noqa: E731
    assert level(dry)["level"] == "WATCH" and "NASA MODIS NDVI (2026-09)" in level(dry)["sources"]
    assert level(wet)["level"] is None and any("น้ำท่วม" in n for n in level(wet)["notices"])
    assert level(city)["level"] is None and any("เมือง" in n for n in level(city)["notices"])
    assert level(green)["level"] == "NORMAL"


async def test_first_refresh_failure_gives_503_not_500(base_service, monkeypatch):
    from fastapi import HTTPException

    svc = stale_copy(base_service)
    svc.decisions = {}

    async def broken():
        raise RuntimeError("all providers down")

    monkeypatch.setattr(svc, "_fetch", broken)
    monkeypatch.setattr(main, "service", svc)
    with pytest.raises(HTTPException) as err:
        await main.fresh_service()
    assert err.value.status_code == 503


async def test_failed_tmd_keeps_last_warnings(base_service, monkeypatch):
    svc = stale_copy(base_service)
    svc.warnings = [{"title": "เดิม", "hazards": ["flood"]}]

    async def fetch():
        return {}, RuntimeError("tmd down")

    monkeypatch.setattr(svc, "_fetch", fetch)
    monkeypatch.setattr(svc, "_process", lambda results, warnings: ({c: NEW for c in svc.amphoe_codes}, None, {}))
    await svc.refresh()
    assert svc.warnings == [{"title": "เดิม", "hazards": ["flood"]}]


def test_stale_water_level_does_not_drive_flood(base_service):
    """Live case 2026-10-19: gauge BKC002 stopped the day before, its last reading still over the bank."""
    from thanphai_shared import SourceRecord

    svc = stale_copy(base_service)
    sena = next(a.code for a in svc.gazetteer.resolve("อำเภอเสนา"))
    area = svc.gazetteer.by_code[sena]
    now = datetime.now(timezone.utc)

    def reading(hours_ago: float, above: float) -> SourceRecord:
        return SourceRecord(source="thaiwater_waterlevel", variable="waterlevel_msl", value=5.0, unit="m MSL",
                            lat=area.lat, lon=area.lon, station_id=f"s{hours_ago}", observed_at=now - timedelta(hours=hours_ago),
                            fetched_at=now, attribution="t",
                            extra={"above_bank_m": above, "bank_quality": "ok", "tambon_code": None})

    decisions, _, status = svc._process({"thaiwater_waterlevel": [reading(26, 0.8)], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []}, [])
    flood = next(i for i in decisions[sena] if i["hazard"] == "flood")
    assert flood["level"] is None  # the only gauge is stale: no data, not CRITICAL
    assert status["thaiwater_waterlevel"]["error"] == "stale"

    decisions, _, status = svc._process({"thaiwater_waterlevel": [reading(26, 0.8), reading(1, -2.0)], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []}, [])
    flood = next(i for i in decisions[sena] if i["hazard"] == "flood")
    assert flood["level"] == "NORMAL" and status["thaiwater_waterlevel"]["stale_dropped"] == 1


async def test_inputs_pending_only_while_first_load_runs(base_service):
    svc = base_service
    saved = (svc._jobs, svc.satellite_updated_at)
    try:
        gate = asyncio.Event()
        svc._jobs, svc.satellite_updated_at = {"gistda_flood": asyncio.create_task(gate.wait())}, None
        assert "gistda_flood" in svc.inputs_pending  # loading for the first time: clients should poll
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "gistda_flood" not in svc.inputs_pending  # finished (or failed): stop fast polling
    finally:
        svc._jobs, svc.satellite_updated_at = saved


def test_pm25_station_wins_and_model_fills_districts_without_one(base_service, monkeypatch):
    from thanphai_shared import SourceRecord

    svc = stale_copy(base_service)
    sena = next(a.code for a in svc.gazetteer.resolve("อำเภอเสนา"))
    other = next(c for c in svc.amphoe_codes if c != sena)
    area = svc.gazetteer.by_code[sena]
    now = datetime.now(timezone.utc)

    def station(hours_ago: float, value: float) -> SourceRecord:
        return SourceRecord(source="air4thai", variable="pm25_24h", value=value, unit="µg/m³", lat=area.lat, lon=area.lon,
                            station_id=f"a{hours_ago}", observed_at=now - timedelta(hours=hours_ago), fetched_at=now, attribution="t")

    # The model says 50 everywhere; the station in Sena says 80 and a stuck station says 200.
    monkeypatch.setattr(svc, "air_model", {sena: 50.0, other: 50.0})
    decisions, _, status = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [],
                                         "air4thai": [station(1, 80.0), station(30, 200.0)]}, [])
    at_sena = next(i for i in decisions[sena] if i["hazard"] == "air_quality")
    assert at_sena["level"] == "WARNING" and at_sena["observations"] == {"pm25_24h": 80.0, "pm25_model_24h": None, "cross_border_hotspots_24h": 0.0}
    assert status["air4thai"]["stale_dropped"] == 1
    elsewhere = next(i for i in decisions[other] if i["hazard"] == "air_quality")
    assert elsewhere["level"] == "WATCH" and elsewhere["observations"]["pm25_model_24h"] == 50.0
    assert elsewhere["sources"] == ["Open-Meteo / CAMS (แบบจำลอง)"] and elsewhere["notices"]


def test_cross_border_hotspots_are_context_for_pm25_not_fire(base_service):
    from thanphai_shared import SourceRecord

    svc = stale_copy(base_service)
    mae_sai = next(a.code for a in svc.gazetteer.resolve("อำเภอแม่สาย"))
    nong_khai = next(a.code for a in svc.gazetteer.resolve("อำเภอเมืองหนองคาย"))
    now = datetime.now(timezone.utc)

    def hotspot(lat, lon):
        return SourceRecord(source="nasa_firms", variable="fire_hotspot", value=5.0, unit="MW", lat=lat, lon=lon,
                            observed_at=now, fetched_at=now, attribution="t")

    # North of Vientiane (Laos) is inside the Thai bbox; Shan State (Myanmar) north of Mae Sai is outside it.
    burning = [hotspot(18.05, 102.65) for _ in range(4)] + [hotspot(21.0, 99.6) for _ in range(3)]
    decisions, _, status = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": burning,
                                         "air4thai": []}, [])
    items = {i["hazard"]: i for i in decisions[nong_khai]}
    assert items["fire"]["observations"]["hotspots_24h"] == 0  # fires in Laos never raise Thai fire levels
    assert items["air_quality"]["observations"]["cross_border_hotspots_24h"] == 4
    assert items["air_quality"]["level"] is None  # context only: no PM2.5 value, no level
    assert next(i for i in decisions[mae_sai] if i["hazard"] == "air_quality")["observations"]["cross_border_hotspots_24h"] == 3
    far = next(c for c in svc.amphoe_codes if c.startswith("TH90"))  # Songkhla
    assert next(i for i in decisions[far] if i["hazard"] == "air_quality")["observations"]["cross_border_hotspots_24h"] == 0
