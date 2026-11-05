"""Refresh behaviour: stale data is served at once, and processing never blocks the event loop."""

import asyncio
import time
from datetime import date, datetime, timedelta, timezone
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
    svc._tmd_quakes, svc._tmd_last_ok, svc._tmd_gaps = {}, None, []  # earthquakes kept by earlier tests
    svc._lock = asyncio.Lock()
    # Fresh forecasts, so fresh_service() does not start real Open-Meteo jobs in the background.
    svc.grid_updated_at = {name: datetime.now(timezone.utc) for name in svc.grid_updated_at}
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


def test_arrived_input_stays_pending_until_decisions_use_it():
    # Without data files: inputs_pending reads only these attributes.
    svc = HazardService.__new__(HazardService)
    svc.satellite_updated_at = svc.forecast_updated_at = svc.fire_updated_at = None
    svc.grid_updated_at = {"heat_model": datetime.now(timezone.utc), "marine_model": None}
    svc._jobs, svc._built_with = {}, set()
    # The heat forecast has arrived, but the decisions on show were built without it: keep clients polling.
    assert svc.inputs_pending == ["heat_model"]
    svc._built_with = {"heat_model"}
    assert svc.inputs_pending == []


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
    from types import SimpleNamespace

    monkeypatch.setitem(svc.grid, "air_model", {sena: SimpleNamespace(value=50.0), other: SimpleNamespace(value=50.0)})
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


def test_heat_level_comes_from_the_forecast_heat_index(base_service, monkeypatch):
    from types import SimpleNamespace

    svc = stale_copy(base_service)
    sena = next(a.code for a in svc.gazetteer.resolve("อำเภอเสนา"))
    this_hour = datetime.now(timezone(timedelta(hours=7))).replace(minute=0, second=0, microsecond=0).isoformat()
    peak = {"peak_time": "2026-04-20T14:00:00+07:00", "temperature_c": 38.0, "humidity_pct": 55.0,
            "hourly": {this_hour: 31.2},
            "rain_chance": {"max_pct": 70.0, "peak_time": "2026-04-20T15:00:00+07:00", "hourly": {this_hour: 20.0}}}
    monkeypatch.setitem(svc.grid, "heat_model", {sena: SimpleNamespace(value=44.6, extra=peak)})
    decisions, _, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []}, [])
    heat = next(i for i in decisions[sena] if i["hazard"] == "heat")
    # The level follows the 24-h peak; "now" is only shown.
    assert heat["level"] == "WARNING" and heat["rule_ids"][0] == "HEAT_INDEX_DANGER"
    assert heat["observations"]["heat_index_now"] == 31.2
    # The chance of rain comes in the same forecast and is shown under rain/storm; it does not set the level.
    rain = next(i for i in decisions[sena] if i["hazard"] == "rain_storm")
    assert rain["observations"]["rain_chance_now"] == 20.0 and rain["observations"]["rain_chance_max_24h"] == 70.0
    assert rain["observations"]["rain_chance_peak_hour"] == 15.0  # Thai hour of the peak, for the card
    assert any("70%" in n and "15:00" in n for n in rain["notices"])
    assert "14:00" in heat["notices"][0]
    other = next(c for c in svc.amphoe_codes if c != sena)
    assert next(i for i in decisions[other] if i["hazard"] == "heat")["level"] is None  # no forecast yet = no data


def test_earthquake_shaking_per_district(base_service):
    from thanphai_shared import SourceRecord

    svc = stale_copy(base_service)
    mae_sai = next(a.code for a in svc.gazetteer.resolve("อำเภอแม่สาย"))
    hat_yai = next(a.code for a in svc.gazetteer.resolve("อำเภอหาดใหญ่"))
    now = datetime.now(timezone.utc)
    # M 6.0, 10 km deep, just across the border from Mae Sai, an hour ago; and an M 7 three days ago that no longer counts.
    quake = SourceRecord(source="tmd_earthquakes", variable="earthquake", value=6.0, unit="magnitude", lat=20.5, lon=99.75,
                         observed_at=now - timedelta(hours=1), fetched_at=now, attribution="t",
                         extra={"depth_km": 10.0, "place_th": "ประเทศเมียนมา"})
    old = SourceRecord(source="tmd_earthquakes", variable="earthquake", value=7.0, unit="magnitude", lat=7.0, lon=100.5,
                       observed_at=now - timedelta(days=3), fetched_at=now, attribution="t", extra={"depth_km": 10.0})
    decisions, _, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": [],
                                    "tmd_earthquakes": [quake, old], "usgs_earthquakes": []}, [])
    near = next(i for i in decisions[mae_sai] if i["hazard"] == "earthquake")
    assert near["level"] in ("WARNING", "CRITICAL") and near["observations"]["quake_magnitude"] == 6.0
    assert all(isinstance(v, (int, float)) for v in near["observations"].values())  # map layer and JSON need numbers
    assert "ประเทศเมียนมา" in near["notices"][-1]
    far = next(i for i in decisions[hat_yai] if i["hazard"] == "earthquake")
    assert far["level"] == "NORMAL"  # 1,300 km away, and the M 7 near Hat Yai was 3 days ago
    assert [q["magnitude"] for q in svc._last_quakes] == [6.0, 7.0]  # the week list keeps both


def test_small_quake_centred_next_to_a_district_is_reported_there_at_normal_level(base_service):
    from thanphai_shared import SourceRecord

    svc = stale_copy(base_service)
    pong = next(a.code for a in svc.gazetteer.resolve("อำเภอปง"))
    hat_yai = next(a.code for a in svc.gazetteer.resolve("อำเภอหาดใหญ่"))
    now = datetime.now(timezone.utc)
    # TMD, 4 Nov 2026 14:52: M 2.3, 2 km deep at ต.ปง, felt by villagers; MMI ~1.4 at the district centre 13 km away.
    quake = SourceRecord(source="tmd_earthquakes", variable="earthquake", value=2.3, unit="magnitude", lat=19.127, lon=100.293,
                         observed_at=now - timedelta(hours=1), fetched_at=now, attribution="t",
                         extra={"depth_km": 2.0, "place_th": "ต.ปง อ.ปง จ.พะเยา"})
    decisions, _, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": [],
                                    "tmd_earthquakes": [quake], "usgs_earthquakes": []}, [])
    near = next(i for i in decisions[pong] if i["hazard"] == "earthquake")
    assert near["level"] == "NORMAL" and near["observations"]["quake_magnitude"] == 2.3
    assert "ต.ปง อ.ปง จ.พะเยา" in near["notices"][-1] and "ใกล้จุดศูนย์กลาง" in near["notices"][-1]
    far = next(i for i in decisions[hat_yai] if i["hazard"] == "earthquake")
    assert "quake_magnitude" not in far["observations"]
    # The province answer must come from อ.ปง, not from the first district at the same NORMAL level.
    svc.decisions = decisions
    phayao = next(i for i in svc.get_assessments(pong[:4]) if i["hazard"] == "earthquake")
    assert phayao["worst_area"] == pong and phayao["observations"]["quake_magnitude"] == 2.3


def test_small_quake_nearby_is_not_pushed_out_by_a_weak_far_one(base_service):
    from thanphai_shared import SourceRecord

    svc = stale_copy(base_service)
    pong = next(a.code for a in svc.gazetteer.resolve("อำเภอปง"))
    now = datetime.now(timezone.utc)

    def quake(magnitude, lat, lon, depth, place):
        return SourceRecord(source="tmd_earthquakes", variable="earthquake", value=magnitude, unit="magnitude", lat=lat,
                            lon=lon, observed_at=now - timedelta(hours=1), fetched_at=now, attribution="t",
                            extra={"depth_km": depth, "place_th": place})

    # An M5.0 ~195 km away gives MMI ~1.9 at อ.ปง: more than the M2.3 next door (~1.75), but not felt (< 2).
    near = quake(2.3, 19.127, 100.293, 2.0, "ต.ปง อ.ปง จ.พะเยา")
    far = quake(5.0, 20.8, 99.6, 10.0, "ประเทศเมียนมา")
    decisions, _, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": [],
                                    "tmd_earthquakes": [near, far], "usgs_earthquakes": []}, [])
    obs = next(i for i in decisions[pong] if i["hazard"] == "earthquake")["observations"]
    assert obs["quake_magnitude"] == 2.3 and obs["quake_distance_km"] == 13


def test_earthquake_is_no_data_when_both_feeds_fail(base_service):
    svc = stale_copy(base_service)
    decisions, _, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": [],
                                    "tmd_earthquakes": RuntimeError("down"), "usgs_earthquakes": RuntimeError("down")}, [])
    quake = next(i for i in decisions[svc.amphoe_codes[0]] if i["hazard"] == "earthquake")
    assert quake["level"] is None and quake["notices"]


def test_marine_only_for_coastal_districts(base_service, monkeypatch):
    from types import SimpleNamespace

    svc = stale_copy(base_service)
    phuket = next(a.code for a in svc.gazetteer.resolve("อำเภอเมืองภูเก็ต"))
    sena = next(a.code for a in svc.gazetteer.resolve("อำเภอเสนา"))
    monkeypatch.setitem(svc.grid, "marine_model", {phuket: SimpleNamespace(value=3.2, extra={"peak_time": "2026-10-22T15:00:00+07:00"})})
    monkeypatch.setitem(svc.grid_updated_at, "marine_model", datetime.now(timezone.utc))
    decisions, _, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []}, [])
    sea = next(i for i in decisions[phuket] if i["hazard"] == "marine")
    assert sea["level"] == "WARNING" and "15:00" in sea["notices"][-1]
    inland = next(i for i in decisions[sena] if i["hazard"] == "marine")
    assert inland["level"] is None and inland["notices"] == ["อำเภอนี้ไม่ติดทะเล"]


def test_offshore_points_cover_the_coast(base_service):
    points = base_service._offshore_points()
    codes = {c for c, _, _ in points}
    phuket = next(a.code for a in base_service.gazetteer.resolve("อำเภอเมืองภูเก็ต"))
    chiang_mai = next(a.code for a in base_service.gazetteer.resolve("อำเภอเมืองเชียงใหม่"))
    assert phuket in codes and chiang_mai not in codes


def test_cyclone_wind_area_over_a_district(base_service):
    from shapely.geometry import box, mapping

    from thanphai_shared import SourceRecord

    svc = stale_copy(base_service)
    sena = next(a.code for a in svc.gazetteer.resolve("อำเภอเสนา"))
    area = svc.gazetteer.by_code[sena]
    now = datetime.now(timezone.utc)

    def wind(kmh, size, hours):
        return SourceRecord(source="gdacs_cyclones", variable="cyclone_wind_area", value=kmh, unit="km/h",
                            valid_time=now + timedelta(hours=hours), fetched_at=now, attribution="t",
                            extra={"event": "TEST-26", "geometry": mapping(box(area.lon - size, area.lat - size, area.lon + size, area.lat + size))})

    track = SourceRecord(source="gdacs_cyclones", variable="cyclone_track_point", value=None, unit="", lat=area.lat, lon=area.lon + 0.5,
                         valid_time=now + timedelta(hours=12), fetched_at=now, attribution="t", extra={"event": "TEST-26", "forecast": True})
    records = [wind(63.0, 3.0, 6), wind(93.0, 1.0, 12), wind(119.0, 0.1, 12)]
    # The 119 km/h box is 0.1° around Sena's centre, the 93 one covers ~1°, the 63 one ~3°.
    decisions, _, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": [],
                                    "gdacs_cyclones": records + [track]}, [])
    item = next(i for i in decisions[sena] if i["hazard"] == "tropical_cyclone")
    assert item["level"] == "CRITICAL" and item["observations"]["cyclone_distance_km"] < 60
    assert "TEST-26" in item["notices"][-1]
    far = next(c for c in svc.amphoe_codes if c.startswith("TH96"))  # Narathiwat, ~900 km away
    far_item = next(i for i in decisions[far] if i["hazard"] == "tropical_cyclone")
    assert far_item["level"] == "NORMAL" and far_item["observations"] == {"cyclone_wind_kmh": 0.0}


def test_landslide_only_where_landslides_happened(base_service, monkeypatch):
    from thanphai_shared import SourceRecord

    svc = stale_copy(base_service)
    tha_pla = next(a.code for a in svc.gazetteer.resolve("อำเภอท่าปลา"))
    sena = next(a.code for a in svc.gazetteer.resolve("อำเภอเสนา"))
    monkeypatch.setattr(svc, "landslide_traces", {tha_pla: 6482, sena: 2})
    monkeypatch.setattr(svc, "forecasts", {tha_pla[:4]: [{"rain_mm": 40.0}, {"rain_mm": 120.0}]})
    now = datetime.now(timezone.utc)

    def rain(code, mm):
        area = svc.gazetteer.by_code[code]
        return SourceRecord(source="thaiwater_rain_24h", variable="rain_24h", value=mm, unit="mm", lat=area.lat, lon=area.lon,
                            station_id=code, observed_at=now, fetched_at=now, attribution="t", extra={"tambon_code": None})

    decisions, _, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [rain(tha_pla, 130.0), rain(sena, 200.0)],
                                    "nasa_firms": [], "air4thai": []}, [])
    hill = next(i for i in decisions[tha_pla] if i["hazard"] == "landslide")
    assert hill["level"] == "WARNING" and hill["observations"]["landslide_forecast_rain_24h"] == 120.0
    assert "กรมอุตุนิยมวิทยา (พยากรณ์ฝน)" in hill["sources"]
    flat = next(i for i in decisions[sena] if i["hazard"] == "landslide")
    assert flat["level"] is None and "2 จุด" in flat["notices"][0]  # 200 mm of rain, but no landslide history


def test_fresh_pm25_stations_are_kept_for_the_map(base_service):
    from thanphai_shared import SourceRecord

    svc = stale_copy(base_service)
    now = datetime.now(timezone.utc)

    def station(sid, hours_ago, value):
        return SourceRecord(source="air4thai", variable="pm25_24h", value=value, unit="µg/m³", lat=13.7, lon=100.5, station_id=sid,
                            observed_at=now - timedelta(hours=hours_ago), fetched_at=now, attribution="t", extra={"name_th": sid})

    svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [],
                  "air4thai": [station("a", 1, 20.0), station("b", 1, 55.0), station("old", 9, 99.0)]}, [])
    assert [s["id"] for s in svc._last_pm25] == ["b", "a"]  # worst first; the 9-hour-old reading is dropped


def test_tmd_warning_raises_only_the_provinces_it_names(base_service, monkeypatch):
    from thanphai_shared import HazardType, OfficialWarning

    svc = stale_copy(base_service)
    now = datetime.now(timezone.utc)
    warning = OfficialWarning(source="tmd_warnings", issue_no="5", title="ฝนตกหนักบริเวณจังหวัดนนทบุรี",
                              headline="ฝนตกหนักบางแห่งบริเวณจังหวัดนนทบุรี", hazards=[HazardType.RAIN_STORM],
                              announced_at=now, fetched_at=now, attribution="t")
    empty = {"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []}
    decisions, warnings, _ = svc._process(empty, [warning])
    assert warnings[0]["provinces"] == ["TH12"]

    def rain(code):
        return next(i for i in decisions[code] if i["hazard"] == "rain_storm")

    bang_bua_thong = next(a.code for a in svc.gazetteer.resolve("อำเภอบางบัวทอง"))
    warned = rain(bang_bua_thong)
    assert warned["level"] == svc.engine.rules["official_warning"]["min_level"]
    assert "OFFICIAL_WARNING" in warned["rule_ids"] and "ฝนตกหนักบริเวณจังหวัดนนทบุรี" in warned["notices"][0]
    assert "กรมอุตุนิยมวิทยา (ประกาศเตือน)" in warned["sources"]
    others = [code for code in svc.amphoe_codes if not code.startswith("TH12")]
    assert all("OFFICIAL_WARNING" not in rain(code)["rule_ids"] for code in others)
    # The warning is about rain only; flood in the same district is untouched.
    assert "OFFICIAL_WARNING" not in next(i for i in decisions[bang_bua_thong] if i["hazard"] == "flood")["rule_ids"]

    # TMD down this time: the last good warnings still apply.
    monkeypatch.setattr(svc, "warnings", warnings)
    decisions, kept, status = svc._process(empty, RuntimeError("down"))
    assert kept is None and status["tmd_warnings"]["ok"] is False
    assert "OFFICIAL_WARNING" in rain(bang_bua_thong)["rule_ids"]


def test_tmd_warning_by_region_is_a_watch_and_named_provinces_keep_warning(base_service):
    # Issue 288/2569 named "ประเทศไทยตอนบน" and "ภาคใต้" and made all 928 districts a WARNING.
    from thanphai_shared import HazardType, OfficialWarning

    svc = stale_copy(base_service)
    now = datetime.now(timezone.utc)
    warning = OfficialWarning(source="tmd_warnings", issue_no="1", title="ฝนตกหนักบริเวณภาคใต้ โดยเฉพาะจังหวัดสงขลา",
                              headline="ภาคใต้จะมีฝนตกหนักบางแห่ง", hazards=[HazardType.RAIN_STORM],
                              announced_at=now, fetched_at=now, attribution="t")
    decisions, warnings, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []},
                                          [warning])
    # No strong wording ("ฝนตกหนักบางแห่ง"): only the province named by name sets min_level.
    # Title and headline are two statements; สงขลา is named in the title only.
    assert {code: p for code, p in warnings[0]["periods_by_hazard"]["rain_storm"].items() if code in ("TH83", "TH90")} == {
        "TH83": [[None, None, "regional"], [None, None, "regional"]], "TH90": [[None, None, "named"], [None, None, "regional"]]}
    assert [(e["areas"], e["level"]) for e in warnings[0]["by_region"]] == [(["จ.สงขลา"], "WARNING"), (["ภาคใต้"], "WATCH")]

    def rain(district):
        code = next(a.code for a in svc.gazetteer.resolve(district))
        return next(i for i in decisions[code] if i["hazard"] == "rain_storm")

    rules = svc.engine.rules["official_warning"]
    named, regional = rain("อำเภอหาดใหญ่"), rain("อำเภอเมืองภูเก็ต")
    assert named["level"] == rules["min_level"] and named["rule_ids"][0] == "OFFICIAL_WARNING"
    assert regional["level"] == rules["regional_level"] and regional["rule_ids"][0] == "OFFICIAL_WARNING_REGIONAL"
    assert "กรมอุตุนิยมวิทยาประกาศเตือน" in regional["notices"][0]


@pytest.mark.parametrize(("periods", "kinds"), [
    (None, ["named"]),  # cached before periods_by_hazard existed: min_level everywhere, as then
    ([[None, None, False]], ["regional"]),  # cached by #41, when a period held only "sets min_level"
    ([[None, None, True]], ["named"]),
    ([[None, None, "heavy_rain"]], ["heavy_rain"]),
])
def test_cached_warning_periods_keep_their_level(periods, kinds):
    from thanphai_shared import HazardType

    warning = {"title": "t", "provinces_by_hazard": {"rain_storm": ["TH90"]}, "impact_from": None, "impact_to": None}
    if periods is not None:
        warning["periods_by_hazard"] = {"rain_storm": {"TH90": periods}}
    assert HazardService._official_by_province([warning], date(2026, 11, 4), 1) == {"TH90": {HazardType.RAIN_STORM: [("t", None, kinds)]}}


@pytest.mark.parametrize(("today", "hat_yai_rain", "hat_yai_flood"), [
    (date(2026, 11, 5), ("WATCH", "OFFICIAL_WARNING_UPCOMING"), ("WATCH", "OFFICIAL_WARNING_UPCOMING")),
    (date(2026, 11, 6), ("WARNING", "OFFICIAL_WARNING"), ("WARNING", "OFFICIAL_WARNING")),
])
def test_tmd_bulletin_body_gives_each_region_its_days_and_level(base_service, monkeypatch, today, hat_yai_rain, hat_yai_flood):
    # Issue 5 (288/2569) PDF: upper Thailand 5-7 Nov "ฝนตกหนักบางแห่ง" (WATCH); the south 7-9 Nov "ฝนตกหนักหลายพื้นที่ …
    # หนักมากบางแห่ง … น้ำท่วมฉับพลัน" (WARNING from the day before, rain and flood). The title alone said 5-9 for the south.
    import json as _json

    from external_data.adapters.tmd import classify_hazards
    from thanphai_shared import OfficialWarning

    svc = stale_copy(base_service)
    monkeypatch.setattr(svc, "_today", lambda: today)
    fixture = Path(__file__).resolve().parents[2] / "05_geo_data_integration" / "tests" / "fixtures" / "tmd_bulletin_5.json"
    bulletin = _json.loads(fixture.read_text(encoding="utf-8"))
    at = datetime.fromisoformat(bulletin["announced"]).replace(tzinfo=timezone(timedelta(hours=7)))
    warning = OfficialWarning(source="tmd_warnings", issue_no="5", title=bulletin["title"], body=bulletin["body"],
                              hazards=classify_hazards(bulletin["title"]), announced_at=at, fetched_at=at, attribution="t")
    decisions, warnings, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []},
                                          [warning])
    assert warnings[0]["by_region"] == [
        {"from": "2026-11-05", "to": "2026-11-07", "level": "WATCH", "hazards": ["rain_storm"], "areas": ["ประเทศไทยตอนบน"]},
        {"from": "2026-11-07", "to": "2026-11-09", "level": "WARNING", "hazards": ["rain_storm", "flood"], "areas": ["ภาคใต้"]},
    ]

    def item(district, hazard):
        code = next(a.code for a in svc.gazetteer.resolve(district))
        return next(i for i in decisions[code] if i["hazard"] == hazard)

    for district in ("อำเภอเมืองเชียงใหม่", "เขตบางรัก"):
        rain = item(district, "rain_storm")
        assert (rain["level"], rain["rule_ids"][0]) == ("WATCH", "OFFICIAL_WARNING_REGIONAL")
        assert not any(r.startswith("OFFICIAL_WARNING") for r in item(district, "flood")["rule_ids"])
    assert tuple((lambda i: (i["level"], i["rule_ids"][0]))(item("อำเภอหาดใหญ่", "rain_storm"))) == hat_yai_rain
    assert tuple((lambda i: (i["level"], i["rule_ids"][0]))(item("อำเภอหาดใหญ่", "flood"))) == hat_yai_flood


@pytest.mark.parametrize(("today", "district", "rain", "flood"), [
    # เชียงใหม่ is listed for heavy rain on 6 Nov only, and stays on through upper Thailand's 5-7 Nov.
    (date(2026, 11, 4), "อำเภอเมืองเชียงใหม่", ("WATCH", "OFFICIAL_WARNING_REGIONAL"), None),
    (date(2026, 11, 5), "อำเภอเมืองเชียงใหม่", ("WATCH", "OFFICIAL_WARNING_HEAVY_RAIN"), None),
    (date(2026, 11, 7), "อำเภอเมืองเชียงใหม่", ("WATCH", "OFFICIAL_WARNING_HEAVY_RAIN"), None),
    (date(2026, 11, 8), "อำเภอเมืองเชียงใหม่", None, None),
    # นครศรีธรรมราช: heavy rain listed from 5 Nov (page 1 says the south from 7 Nov), very heavy on 8-9 Nov.
    (date(2026, 11, 4), "อำเภอนครศรีธรรมราช", ("WATCH", "OFFICIAL_WARNING_HEAVY_RAIN"), ("WATCH", "OFFICIAL_WARNING_UPCOMING")),
    (date(2026, 11, 6), "อำเภอนครศรีธรรมราช", ("WATCH", "OFFICIAL_WARNING_HEAVY_RAIN"), ("WATCH", "OFFICIAL_WARNING_REGIONAL")),
    (date(2026, 11, 7), "อำเภอนครศรีธรรมราช", ("WARNING", "OFFICIAL_WARNING_VERY_HEAVY_RAIN"),
     ("WARNING", "OFFICIAL_WARNING_VERY_HEAVY_RAIN")),
    (date(2026, 11, 10), "อำเภอนครศรีธรรมราช", None, None),
    # ภูเก็ต: heavy rain on 9 Nov only, so its flood is the south's flood statement alone.
    (date(2026, 11, 8), "อำเภอเมืองภูเก็ต", ("WATCH", "OFFICIAL_WARNING_HEAVY_RAIN"), ("WATCH", "OFFICIAL_WARNING_REGIONAL")),
    # ชลบุรี: listed on 5 and 8 Nov, past upper Thailand's 7 Nov: on until 8 Nov.
    (date(2026, 11, 8), "อำเภอเมืองชลบุรี", ("WATCH", "OFFICIAL_WARNING_HEAVY_RAIN"), None),
])
def test_bulletin_daily_tables_set_each_province_level(base_service, monkeypatch, today, district, rain, flood):
    # Issue 5 (288/2569), every page: the daily tables say province by province where rain is heavy or very heavy.
    import json as _json

    from external_data.adapters.tmd import bulletin_text, classify_hazards, rain_days
    from thanphai_shared import OfficialWarning

    svc = stale_copy(base_service)
    monkeypatch.setattr(svc, "_today", lambda: today)
    fixture = Path(__file__).resolve().parents[2] / "05_geo_data_integration" / "tests" / "fixtures" / "tmd_bulletin_5.json"
    bulletin = _json.loads(fixture.read_text(encoding="utf-8"))
    at = datetime.fromisoformat(bulletin["announced"]).replace(tzinfo=timezone(timedelta(hours=7)))
    warning = OfficialWarning(source="tmd_warnings", issue_no="5", title=bulletin["title"], body=bulletin["body"],
                              rain_days=rain_days(bulletin_text(bulletin["pages"]), at.date()),
                              hazards=classify_hazards(bulletin["title"]), announced_at=at, fetched_at=at, attribution="t")
    decisions, warnings, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []},
                                          [warning])
    # The regions keep regional_level; the tables carry the heavier days.
    assert [(e["areas"], e["level"]) for e in warnings[0]["by_region"]] == [(["ประเทศไทยตอนบน"], "WATCH"), (["ภาคใต้"], "WATCH")]
    assert [(d["day"], d["very_heavy"], len(d["provinces"])) for d in warnings[0]["rain_days"]][-2:] == [
        ("2026-11-09", False, 10), ("2026-11-09", True, 6)]
    code = next(a.code for a in svc.gazetteer.resolve(district))
    for hazard, want in (("rain_storm", rain), ("flood", flood)):
        item = next(i for i in decisions[code] if i["hazard"] == hazard)
        official = [r for r in item["rule_ids"] if r.startswith("OFFICIAL_WARNING")]
        assert ((item["level"], official[0]) if official else None) == want, hazard


def _bulletin_decisions(svc, body):
    from external_data.adapters.tmd import classify_hazards
    from thanphai_shared import OfficialWarning

    at = datetime(2026, 11, 3, 17, 0, tzinfo=timezone(timedelta(hours=7)))
    title = "ฝนตกหนักบริเวณประเทศไทย (มีผลกระทบในช่วงวันที่ 5-9 พฤศจิกายน 2569) ฉบับที่ 1"
    warning = OfficialWarning(source="tmd_warnings", issue_no="1", title=title, body=body, hazards=classify_hazards(body),
                              announced_at=at, fetched_at=at, attribution="t")
    return svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []}, [warning])


@pytest.mark.parametrize(("today", "level", "rule"), [
    (date(2026, 11, 5), "WATCH", "OFFICIAL_WARNING_REGIONAL"),  # only the 5-7 Nov "บางแห่ง" statement is in force
    (date(2026, 11, 8), "WARNING", "OFFICIAL_WARNING"),  # the 8-9 Nov "หลายพื้นที่" statement
])
def test_each_statement_sets_its_level_only_on_its_own_days(base_service, monkeypatch, today, level, rule):
    # Review of #41: one region WATCH on 5-7 Nov and WARNING on 8-9 Nov made 5 Nov a WARNING too.
    svc = stale_copy(base_service)
    monkeypatch.setattr(svc, "_today", lambda: today)
    monkeypatch.setitem(svc.engine.rules["official_warning"], "lead_days", 0)
    decisions, _, _ = _bulletin_decisions(svc, "ในช่วงวันที่ 5–7 พฤศจิกายน 2569 ภาคเหนือจะมีฝนตกหนักบางแห่ง "
                                               "ส่วนในช่วงวันที่ 8–9 พฤศจิกายน 2569 ภาคเหนือจะมีฝนตกหนักหลายพื้นที่")
    code = next(a.code for a in svc.gazetteer.resolve("อำเภอเมืองเชียงใหม่"))
    rain = next(i for i in decisions[code] if i["hazard"] == "rain_storm")
    assert (rain["level"], rain["rule_ids"][0]) == (level, rule)


def test_by_region_keeps_each_region_with_its_own_hazards(base_service):
    # Review of #41: rain in the north and flood in the south, same days and level, showed "เหนือ · ใต้ — ฝน, น้ำท่วม".
    svc = stale_copy(base_service)
    _, warnings, _ = _bulletin_decisions(svc, "ในช่วงวันที่ 5–7 พฤศจิกายน 2569 ภาคเหนือจะมีฝนตกหนักหลายพื้นที่ "
                                              "ส่วนในช่วงวันที่ 5–7 พฤศจิกายน 2569 ภาคใต้ระวังน้ำท่วมฉับพลัน")
    assert [(e["areas"], e["hazards"]) for e in warnings[0]["by_region"]] == [(["ภาคเหนือ"], ["rain_storm"]), (["ภาคใต้"], ["flood"])]


def test_tmd_issue_12_does_not_warn_chiang_mai_of_floods(base_service, monkeypatch):
    # Heavy rain (rain + flood) in the east and south; "ส่วนภาคเหนือตอนล่าง …" only thunderstorms. Before, every
    # hazard went to every province named, and เชียงใหม่ got a flood WARNING.
    import json as _json

    from external_data.adapters.tmd import classify_hazards
    from thanphai_shared import OfficialWarning

    svc = stale_copy(base_service)
    monkeypatch.setattr(svc, "_today", lambda: date(2026, 10, 22))  # impact "จนถึงวันที่ 25 ตุลาคม 2569"
    fixture = Path(__file__).resolve().parents[2] / "05_geo_data_integration" / "tests" / "fixtures" / "tmd_warning_texts.json"
    issue = next(f for f in _json.loads(fixture.read_text(encoding="utf-8")) if f["issue_no"] == "12")
    at = datetime.fromisoformat(issue["announced"]).replace(tzinfo=timezone(timedelta(hours=7)))
    warning = OfficialWarning(source="tmd_warnings", issue_no="12", title=issue["text"], hazards=classify_hazards(issue["text"]),
                              announced_at=at, fetched_at=at, attribution="t")
    decisions, warnings, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []},
                                          [warning])
    assert "TH50" in warnings[0]["provinces"]  # still listed as named, for the warning panel

    def rule_ids(district, hazard):
        code = next(a.code for a in svc.gazetteer.resolve(district))
        return next(i for i in decisions[code] if i["hazard"] == hazard)["rule_ids"]

    for hazard in ("flood", "rain_storm"):
        assert not any(r.startswith("OFFICIAL_WARNING") for r in rule_ids("อำเภอเมืองเชียงใหม่", hazard))
        # "ภาคตะวันออกและภาคใต้ … ฝนตกหนักถึงหนักมากบางพื้นที่": regions, but strong wording keeps min_level.
        assert "OFFICIAL_WARNING" in rule_ids("อำเภอเมืองชลบุรี", hazard)
        assert "OFFICIAL_WARNING" in rule_ids("อำเภอหาดใหญ่", hazard)


def test_tmd_live_issue_3_floods_south_rain_upper_thailand_one_notice(base_service, monkeypatch):
    # Live 2026-11-02, issues 2 and 3 of one warning in the feed. Before: ขอนแก่น got a flood WARNING from the title's
    # heavy rain in the south, the north/central/east got nothing, and each card listed both issues.
    import json as _json

    from external_data.adapters.tmd import classify_hazards
    from thanphai_shared import OfficialWarning

    svc = stale_copy(base_service)
    monkeypatch.setattr(svc, "_today", lambda: date(2026, 11, 4))  # impact 5-9 Nov: active from the day before
    fixture = Path(__file__).resolve().parents[2] / "05_geo_data_integration" / "tests" / "fixtures" / "tmd_warning_texts.json"
    issue = next(f for f in _json.loads(fixture.read_text(encoding="utf-8")) if f["issue_no"] == "3")
    now = datetime.now(timezone.utc)

    def warning(no, title, at):
        return OfficialWarning(source="tmd_warnings", issue_no=no, title=title, headline=issue["headline"],
                               hazards=classify_hazards(issue["text"]), announced_at=at, fetched_at=now, attribution="t")

    older = warning("2", issue["title"].replace("ฉบับที่ 3 (286/2569)", "ฉบับที่ 2 (285/2569)"), now - timedelta(hours=9))
    decisions, warnings, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []},
                                          [older, warning("3", issue["title"], now)])
    assert [w["issue_no"] for w in warnings] == ["3"]

    def item(district, hazard):
        code = next(a.code for a in svc.gazetteer.resolve(district))
        return next(i for i in decisions[code] if i["hazard"] == hazard)

    # Upper Thailand "ฝนตกหนักบางแห่ง": regional_level. The south "ฝนตกหนักถึงหนักมาก" (title): min_level.
    for district, rule in (("อำเภอเมืองขอนแก่น", "OFFICIAL_WARNING_REGIONAL"), ("อำเภอเมืองเชียงใหม่", "OFFICIAL_WARNING_REGIONAL"),
                           ("อำเภอเมืองชลบุรี", "OFFICIAL_WARNING_REGIONAL"), ("อำเภอหาดใหญ่", "OFFICIAL_WARNING")):
        rain = item(district, "rain_storm")
        assert rain["rule_ids"][0] == rule and sum("กรมอุตุนิยมวิทยาประกาศเตือน" in n for n in rain["notices"]) == 1
    assert item("อำเภอหาดใหญ่", "flood")["rule_ids"][0] == "OFFICIAL_WARNING"
    assert all(not any(r.startswith("OFFICIAL_WARNING") for r in item(d, "flood")["rule_ids"])
               for d in ("อำเภอเมืองขอนแก่น", "อำเภอเมืองเชียงใหม่", "อำเภอเมืองชลบุรี"))


@pytest.mark.parametrize(
    ("district", "today", "level", "rule", "notice"),
    [
        ("อำเภอเมืองเชียงใหม่", date(2026, 11, 2), "WATCH", "OFFICIAL_WARNING_UPCOMING", "ประกาศเตือนล่วงหน้า (คาดว่าเริ่ม 5 พฤศจิกายน 2569)"),
        ("อำเภอเมืองเชียงใหม่", date(2026, 11, 4), "WATCH", "OFFICIAL_WARNING_REGIONAL", "ประกาศเตือน: อากาศแปรปรวน"),
        ("อำเภอเมืองเชียงใหม่", date(2026, 11, 7), "WATCH", "OFFICIAL_WARNING_REGIONAL", "ประกาศเตือน: อากาศแปรปรวน"),
        ("อำเภอเมืองเชียงใหม่", date(2026, 11, 8), None, None, None),  # upper Thailand: 5-7 Nov in the headline
        ("อำเภอหาดใหญ่", date(2026, 11, 9), "WARNING", "OFFICIAL_WARNING", "ประกาศเตือน: อากาศแปรปรวน"),  # south: 5-9 Nov
        ("อำเภอหาดใหญ่", date(2026, 11, 10), None, None, None),  # past its last impact day: the warning no longer counts
    ],
)
def test_tmd_warning_is_a_watch_until_the_day_before_its_impact(base_service, monkeypatch, district, today, level, rule, notice):
    # Issue 3 was issued on 2 Nov, for 5-7 Nov in upper Thailand and 5-9 Nov in the south: rules.yaml lead_days 1 makes
    # it upcoming on 2-3 Nov and active from 4 Nov to each region's last day. It names regions only: upper Thailand
    # ("ฝนตกหนักบางแห่ง") is regional_level (WATCH), the south ("ฝนตกหนักถึงหนักมาก") keeps min_level.
    import json as _json

    from external_data.adapters.tmd import classify_hazards
    from thanphai_shared import OfficialWarning

    svc = stale_copy(base_service)
    monkeypatch.setattr(svc, "_today", lambda: today)
    fixture = Path(__file__).resolve().parents[2] / "05_geo_data_integration" / "tests" / "fixtures" / "tmd_warning_texts.json"
    issue = next(f for f in _json.loads(fixture.read_text(encoding="utf-8")) if f["issue_no"] == "3")
    at = datetime(2026, 11, 2, 15, 13, tzinfo=timezone(timedelta(hours=7)))
    warning = OfficialWarning(source="tmd_warnings", issue_no="3", title=issue["title"], headline=issue["headline"],
                              hazards=classify_hazards(issue["text"]), announced_at=at, fetched_at=at, attribution="t")
    empty = {"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []}
    decisions, warnings, _ = svc._process(empty, [warning])
    assert (warnings[0]["impact_from"], warnings[0]["impact_to"]) == ("2026-11-05", "2026-11-09")
    assert warnings[0]["impact_by_hazard"]["rain_storm"]["TH50"] == ["2026-11-05", "2026-11-07"]
    code = next(a.code for a in svc.gazetteer.resolve(district))
    item = next(i for i in decisions[code] if i["hazard"] == "rain_storm")
    if rule:
        assert item["rule_ids"][0] == rule and item["level"] == level and notice in item["notices"][0]
    else:
        assert not any(r.startswith("OFFICIAL_WARNING") for r in item["rule_ids"])
        assert not any("กรมอุตุนิยมวิทยาประกาศเตือน" in n for n in item["notices"])


@pytest.mark.parametrize(("today", "active"), [(date(2026, 12, 31), True), (date(2027, 1, 2), True), (date(2027, 1, 3), False)])
def test_tmd_warning_over_the_new_year_without_years_stays_in_force(base_service, monkeypatch, today, active):
    # Review of #17: "30 ธ.ค. - 2 ม.ค." with no year was read a year back and the warning was dropped as over.
    from thanphai_shared import HazardType, OfficialWarning

    svc = stale_copy(base_service)
    monkeypatch.setattr(svc, "_today", lambda: today)
    at = datetime(2026, 12, 29, 17, 0, tzinfo=timezone(timedelta(hours=7)))
    warning = OfficialWarning(source="tmd_warnings", issue_no="1", title="ฝนตกหนักบริเวณภาคใต้ (มีผลกระทบในช่วงวันที่ 30 ธ.ค. - 2 ม.ค.) ฉบับที่ 1",
                              headline="ในช่วงวันที่ 30 ธ.ค. - 2 ม.ค. ภาคใต้จะมีฝนตกหนัก", hazards=[HazardType.RAIN_STORM],
                              announced_at=at, fetched_at=at, attribution="t")
    decisions, warnings, _ = svc._process({"thaiwater_waterlevel": [], "thaiwater_rain_24h": [], "nasa_firms": [], "air4thai": []},
                                          [warning])
    assert (warnings[0]["impact_from"], warnings[0]["impact_to"]) == ("2026-12-30", "2027-01-02")
    assert warnings[0]["impact_by_hazard"]["rain_storm"]["TH90"] == ["2026-12-30", "2027-01-02"]
    code = next(a.code for a in svc.gazetteer.resolve("อำเภอหาดใหญ่"))
    rain = next(i for i in decisions[code] if i["hazard"] == "rain_storm")
    assert ("OFFICIAL_WARNING_REGIONAL" in rain["rule_ids"]) is active


def test_tmd_quakes_are_kept_after_they_leave_the_feed_and_saved_for_a_restart(base_service, tmp_path):
    from thanphai_shared import SourceRecord

    svc = stale_copy(base_service)
    svc._quake_file = tmp_path / "tmd.json"
    now = datetime.now(timezone.utc)

    def quake(id_: str, days_ago: float) -> SourceRecord:
        return SourceRecord(source="tmd_earthquakes", variable="earthquake", value=2.3, unit="magnitude", lat=19.1, lon=100.3,
                            station_id=f"tmd:{id_}", observed_at=now - timedelta(days=days_ago), fetched_at=now, attribution="t",
                            extra={"depth_km": 2.0, "place_th": "ต.ปง อ.ปง จ.พะเยา"})

    svc._quake_shaking([quake("1", 4), quake("old", 70)], [])
    # TMD's feed has moved on (only its last 10 events): the event from 4 days ago must stay.
    svc._quake_shaking([quake("2", 0.1)], [])
    assert [q["id"] for q in svc._last_quakes] == ["tmd:2", "tmd:1"]  # the 70-day-old one is past the 62 days kept
    svc.quakes = svc._last_quakes
    assert [q["id"] for q in svc.get_earthquakes(days=2)] == ["tmd:2"]
    assert {r.station_id for r in svc._load_quake_history().values()} == {"tmd:1", "tmd:2"}
