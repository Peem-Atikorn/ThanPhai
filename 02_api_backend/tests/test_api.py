from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_backend.main as main
from api_backend.hazard_service import HazardService
from hazard_agent import HazardAgent

DATA = Path(__file__).resolve().parents[2] / "data" / "processed"
pytestmark = pytest.mark.skipif(not (DATA / "gazetteer.json").exists(), reason="run scripts/build_admin_areas.py first")


def item(hazard, level, **obs):
    return {"hazard": hazard, "level": level, "confidence": 0.9, "rule_ids": [], "notices": [],
            "observations": obs, "sources": ["test"], "rules_version": "t"}


@pytest.fixture(scope="module")
def client():
    svc = HazardService(DATA)
    # Inject decisions instead of calling the providers.
    svc.decisions = {code: [item("flood", "NORMAL", above_bank_m=-1.0), item("fire", "NORMAL", hotspots_24h=0)] for code in svc.amphoe_codes}
    sena = next(a.code for a in svc.gazetteer.resolve("อำเภอเสนา"))
    svc.decisions[sena] = [item("flood", "CRITICAL", above_bank_m=2.11), item("fire", "NORMAL", hotspots_24h=0)]
    svc.warnings = [{"title": "ฝนตกหนักถึงหนักมากบริเวณประเทศไทย", "hazards": ["rain_storm", "flood"]}]
    svc.updated_at = datetime.now(timezone.utc)
    # Fresh forecasts, so requests do not start real Open-Meteo jobs in the background.
    svc.grid_updated_at = {name: svc.updated_at for name in svc.grid_updated_at}
    svc.wave_updated_at = svc.updated_at
    svc.source_status = {"thaiwater_waterlevel": {"ok": True}, "nasa_firms": {"ok": False}}
    main.service, main.agent = svc, HazardAgent(svc.gazetteer, svc)
    return TestClient(main.app), svc, sena


def test_search(client):
    c, _, sena = client
    hits = c.get("/v1/areas/search", params={"q": "อำเภอเสนา"}).json()
    assert hits[0]["code"] == sena and "พระนครศรีอยุธยา" in hits[0]["parents"]


def test_search_suggests_partial_names(client):
    c, _, _ = client
    hits = c.get("/v1/areas/search", params={"q": "เชียงให"}).json()
    assert any(a["name_th"] == "เชียงใหม่" and a["level"] == "province" for a in hits)
    provinces = c.get("/v1/areas/search", params={"q": "จังหวัดเชียง"}).json()
    assert provinces and all(a["level"] == "province" for a in provinces)


def test_gps_lookup_uses_containing_boundary(client):
    c, svc, _ = client
    polygon = svc.mapper.tambon.iloc[0]
    point = polygon.geometry.representative_point()
    response = c.get("/v1/areas/locate", params={"lat": point.y, "lon": point.x})
    assert response.status_code == 200
    assert response.json()["code"] == polygon["tambon_code"]
    assert response.json()["parents"]
    assert c.get("/v1/areas/locate", params={"lat": 0, "lon": 0}).status_code == 404
    assert c.get("/v1/areas/locate", params={"lat": 91, "lon": 100}).status_code == 422


def test_gps_lookup_accepts_small_boundary_offset_but_rejects_distant_points(client, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import box
    from geo_integration.area_mapper import AreaMapper

    c, svc, _ = client
    area = svc.mapper.tambon.iloc[0]
    boundary = gpd.GeoDataFrame(
        {"code": [area["tambon_code"]], "parent_code": [area["amphoe_code"]]},
        geometry=[box(100, 13, 100.01, 13.01)], crs="EPSG:4326",
    )
    monkeypatch.setattr(svc, "mapper", AreaMapper(boundary))
    # About 540 m outside the synthetic boundary, within the mapper's 2 km limit.
    response = c.get("/v1/areas/locate", params={"lat": 13.005, "lon": 100.015})
    assert response.status_code == 200
    assert response.json()["code"] == area["tambon_code"]
    assert response.json()["lat"] == 13.005 and response.json()["lon"] == 100.015
    assert c.get("/v1/areas/locate", params={"lat": 13.005, "lon": 100.04}).status_code == 404


def test_area_hazards_for_amphoe(client):
    c, _, sena = client
    body = c.get(f"/v1/areas/{sena}/hazards").json()
    flood = next(a for a in body["assessments"] if a["hazard"] == "flood")
    assert flood["level"] == "CRITICAL"
    assert body["degraded_services"] == ["nasa_firms"]


def test_province_reports_worst_district(client):
    c, _, sena = client
    body = c.get(f"/v1/areas/{sena[:4]}/hazards").json()
    flood = next(a for a in body["assessments"] if a["hazard"] == "flood")
    assert flood["level"] == "CRITICAL" and flood["worst_area"] == sena


def test_tambon_maps_to_its_district(client):
    c, svc, sena = client
    tambon = next(a.code for a in svc.gazetteer.areas if a.level == "tambon" and a.parent_code == sena)
    assert c.get(f"/v1/areas/{tambon}/hazards").json()["assessments"][0]["level"] == "CRITICAL"


def test_unknown_area(client):
    assert client[0].get("/v1/areas/TH9999/hazards").status_code == 404


def test_map_layer_is_geojson_for_every_district(client):
    c, svc, sena = client
    layer = c.get("/v1/map/layers/flood").json()
    assert layer["type"] == "FeatureCollection" and len(layer["features"]) == len(svc.amphoe_codes)
    sena_feature = next(f for f in layer["features"] if f["properties"]["code"] == sena)
    assert sena_feature["properties"]["level"] == "CRITICAL"


def test_map_layer_is_served_from_cache_until_data_changes(client):
    c, svc, sena = client
    main._layer_cache.clear()
    first = c.get("/v1/map/layers/flood")
    assert first.headers["content-type"] == "application/json" and len(main._layer_cache) == 1
    assert c.get("/v1/map/layers/flood").content == first.content  # same bytes, no re-encoding
    svc.updated_at = datetime.now(timezone.utc)  # new data version
    c.get("/v1/map/layers/flood")
    assert len(main._layer_cache) == 2


def test_map_layer_gzip_comes_from_cache_and_decodes(client):
    import gzip as gz
    import json as js

    c, _, _ = client
    r = c.get("/v1/map/layers/flood", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"
    assert js.loads(r.content)["type"] == "FeatureCollection"  # TestClient already decoded it once
    raw = c.get("/v1/map/layers/flood", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in raw.headers
    key = next(iter(main._layer_cache))
    assert gz.decompress(main._layer_cache[key][1]) == main._layer_cache[key][0]


def test_dams_endpoint_and_province_filter(client):
    c, svc, sena = client
    svc.dams = [
        {"name_th": "ป่าสักชลสิทธิ์", "province_code": "TH16", "storage_percent": 95.0, "status": "HIGH"},
        {"name_th": "สมมติอยุธยา", "province_code": sena[:4], "storage_percent": 40.0, "status": "NORMAL"},
    ]
    assert [d["name_th"] for d in c.get("/v1/dams").json()["dams"]] == ["ป่าสักชลสิทธิ์", "สมมติอยุธยา"]
    area = c.get(f"/v1/areas/{sena}/hazards").json()
    assert [d["name_th"] for d in area["dams"]] == ["สมมติอยุธยา"]  # only dams in the same province


def test_drought_layer_is_available(client):
    layer = client[0].get("/v1/map/layers/drought").json()
    assert layer["type"] == "FeatureCollection" and len(layer["features"]) == 928


def test_ask_end_to_end(client):
    body = client[0].post("/v1/ask", json={"question": "น้ำจะท่วมอำเภอเสนาไหม"}).json()
    assert "น้ำท่วม: วิกฤต" in body["text"] and body["area"]["code"] == client[2]


def test_ask_reports_llm_flag(client):
    body = client[0].post("/v1/ask", json={"question": "น้ำจะท่วมอำเภอเสนาไหม"}).json()
    assert body["llm_used"] is False  # no explainer in tests


def test_ask_validates_input(client):
    assert client[0].post("/v1/ask", json={"question": ""}).status_code == 422
    assert client[0].post("/v1/ask", json={"question": "ฝนตกไหม", "area_code": "../x"}).status_code == 422


def test_ask_uses_the_open_area_when_the_question_names_none(client):
    body = client[0].post("/v1/ask", json={"question": "น้ำจะท่วมไหม", "area_code": client[2]}).json()
    assert body["intent"] == "risk" and body["area"]["code"] == client[2]


def test_request_id_header(client):
    assert client[0].get("/health").headers["x-request-id"]


def test_river_rows_merge_duplicate_stations_and_match_flood_rules():
    import json

    from external_data.adapters.thaiwater import ThaiWaterWaterLevel

    raw = json.loads((Path(__file__).parent / "tw_waterlevel_rivers.json").read_text(encoding="utf-8"))
    rows = HazardService._river_rows(ThaiWaterWaterLevel().parse(raw, datetime.now(timezone.utc)))
    by_code = {r["code"]: r for r in rows}
    assert sorted(by_code) == ["C.13", "C.29B", "C.36"]  # "C.13" + "ridhydro_C.13" -> one station
    c13 = by_code["C.13"]
    assert c13["id"] == "2744" and c13["river_th"] == "แม่น้ำเจ้าพระยา" and c13["discharge_m3s"] == 1800
    assert c13["capacity_percent"] == 66 and c13["status"] == "NORMAL" and c13["chain_order"] == 3
    assert by_code["C.36"]["status"] == "OVERFLOW" and by_code["C.36"]["above_bank_m"] > 0
    assert rows[0]["status"] == "OVERFLOW"  # over-bank stations first
    assert HazardService.river_status(-0.3) == ("NEAR_BANK", "น้ำใกล้ล้นตลิ่ง")  # same edge as FLOOD_NEAR_BANK
    assert HazardService.river_status(-0.31)[0] == "NORMAL" and HazardService.river_status(None)[0] == "UNKNOWN"


def test_river_and_media_endpoints(client):
    c, svc, sena = client
    svc.rivers = [{"id": "2611", "code": "C.36", "province_code": sena[:4], "status": "OVERFLOW"},
                  {"id": "2744", "code": "C.13", "province_code": "TH18", "status": "NORMAL"}]
    assert [r["code"] for r in c.get("/v1/rivers").json()["rivers"]] == ["C.36", "C.13"]
    assert [r["code"] for r in c.get(f"/v1/areas/{sena}/hazards").json()["rivers"]] == ["C.36"]
    # Graphs and histories are fetched only for known stations and dams: never an open proxy.
    assert c.get("/v1/rivers/999/graph").status_code == 404
    assert c.get("/v1/rivers/2744/graph", params={"days": 30}).status_code == 422
    svc.dams = [{"id": "1", "name_th": "ภูมิพล", "province_code": "TH63"}]
    assert c.get("/v1/dams/2/history").status_code == 404
    assert c.get("/v1/dams/1/history", params={"series": "evil"}).status_code == 422
    svc.media = None
    assert c.get("/v1/media").json()["radar"] == []


def test_graph_endpoint_uses_cache(client, monkeypatch):
    c, svc, _ = client
    svc.rivers = [{"id": "2744", "code": "C.13", "province_code": "TH18", "status": "NORMAL"}]
    calls = []

    async def fake(client_, station_id, days, today):
        calls.append(station_id)
        return {"points": [{"time": "2026-10-19 18:00", "level_msl": 14.05, "discharge_m3s": 1800.0}],
                "bank_msl": 16.34, "ground_msl": 2.08, "qmax_m3s": 2720.0}

    monkeypatch.setattr("api_backend.hazard_service.fetch_station_graph", fake)
    svc._graph_cache.clear()
    first = c.get("/v1/rivers/2744/graph").json()
    second = c.get("/v1/rivers/2744/graph").json()
    assert first == second and first["qmax_m3s"] == 2720 and calls == [2744]


def test_river_forecast_is_display_only_and_only_where_the_model_was_chosen(client, monkeypatch):
    from datetime import datetime, timedelta

    from api_backend.hazard_service import BANGKOK
    from risk_models.water.dataset import LIVE_RIVER_FEATURES
    from risk_models.water.model import WaterModels

    c, svc, _ = client
    svc.rivers = [{"id": "2744", "code": "C.13", "province_code": "TH18", "status": "NORMAL", "bank_msl": 16.0},
                  {"id": "2611", "code": "C.36", "province_code": "TH14", "status": "NORMAL", "bank_msl": 9.0}]
    now = datetime.now(BANGKOK).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    points = [{"time": (now - timedelta(hours=k)).strftime("%Y-%m-%d %H:%M"), "level_msl": 14.0 - 0.01 * k,
               "discharge_m3s": 1800.0} for k in range(80, -1, -1)]  # rising 1 cm an hour, latest reading this hour
    calls = []

    async def fake(client_, station_id, days, today):
        calls.append(station_id)
        return {"points": points, "bank_msl": 16.0, "ground_msl": 2.0, "qmax_m3s": 2720.0}

    monkeypatch.setattr("api_backend.hazard_service.fetch_station_graph", fake)
    svc._graph_cache.clear()

    class Rise:  # predicts the last hour's change times the horizon, so the answer is known
        def __init__(self, h):
            self.h = h

        def predict(self, x):
            return x["d1h"].to_numpy() * self.h

    svc.water_model = WaterModels(river={}, river_features=[], trend_damping={}, dam=None, dam_features=[], dam_scale={},
                                  version="test", metrics={"rivers_live": {"6h": {"test": {"model": {"mae_m": 0.2}},
                                                                            "test_chosen": {"mae_m": 0.08}}}},
                                  river_live={6: Rise(6), 24: Rise(24)}, river_live_features=list(LIVE_RIVER_FEATURES),
                                  live_stations={6: ["2744"], 24: []})
    f = c.get("/v1/rivers/2744/forecast").json()
    # Only +6 h: the station was not chosen at +24 h. 14.00 m now, +6 cm in 6 hours, 1.94 m below the bank.
    assert f["available"] and [p["hours"] for p in f["points"]] == [6]
    assert f["points"][0]["level_msl"] == pytest.approx(14.06) and f["points"][0]["above_bank_m"] == pytest.approx(-1.94)
    assert f["points"][0]["test_mae_m"] == pytest.approx(0.08) and f["from_time"] == now.strftime("%Y-%m-%d %H:%M")
    assert "level" not in f and "status" not in f  # never an alert level or a river status
    # The graph request is shared with the chart.
    c.get("/v1/rivers/2744/graph")
    assert calls == [2744]
    # A station the model was not chosen for gets no forecast and no upstream request; unknown stations are 404.
    assert c.get("/v1/rivers/2611/forecast").json() == {"station_id": "2611", "available": False, "reason": "not_chosen"}
    assert calls == [2744] and c.get("/v1/rivers/999/forecast").status_code == 404
    # Readings that stopped 5 hours ago are too old to forecast from.
    points[:] = points[:-5]
    svc._graph_cache.clear()
    assert c.get("/v1/rivers/2744/forecast").json()["reason"] == "no_recent_readings"
    svc.water_model = None
    assert c.get("/v1/rivers/2744/forecast").json()["reason"] == "no_model"


def test_area_boundary_for_highlighting(client):
    c, svc, sena = client
    province = c.get("/v1/areas/TH25/boundary").json()  # ปราจีนบุรี
    assert province["properties"] == {"code": "TH25", "level": "province", "name_th": "ปราจีนบุรี"}
    assert province["geometry"]["type"] in ("Polygon", "MultiPolygon")
    west, south, east, north = province["bbox"]
    assert 101 < west < east < 103 and 13 < south < north < 15
    tambon = next(a.code for a in svc.gazetteer.areas if a.level == "tambon" and a.parent_code == sena)
    assert c.get(f"/v1/areas/{tambon}/boundary").json()["properties"]["level"] == "tambon"
    # Only gazetteer codes reach the file query.
    assert c.get("/v1/areas/TH25' OR '1'='1/boundary").status_code == 404
    assert c.get("/v1/areas/TH99/boundary").status_code == 404


def test_graph_requests_are_shared_and_failures_remembered(client, monkeypatch):
    import asyncio

    import httpx

    c, svc, _ = client
    svc.rivers = [{"id": "2744", "code": "C.13", "province_code": "TH18", "status": "NORMAL"}]
    svc._graph_cache.clear()
    calls = []

    async def slow(client_, station_id, days, today):
        calls.append(station_id)
        await asyncio.sleep(0.05)
        return {"points": [], "bank_msl": None, "ground_msl": None, "qmax_m3s": None}

    monkeypatch.setattr("api_backend.hazard_service.fetch_station_graph", slow)

    async def many():
        return await asyncio.gather(*(svc.river_graph("2744") for _ in range(5)))

    asyncio.run(many())
    assert calls == [2744]  # five simultaneous requests, one upstream call

    svc._graph_cache.clear()
    calls.clear()

    async def broken(client_, station_id, days, today):
        calls.append(station_id)
        raise httpx.ConnectError("down")

    monkeypatch.setattr("api_backend.hazard_service.fetch_station_graph", broken)
    assert c.get("/v1/rivers/2744/graph").status_code == 502
    assert c.get("/v1/rivers/2744/graph").status_code == 502
    assert calls == [2744]  # the second click is answered from the remembered failure


def test_overview_layer_takes_each_districts_worst_hazard(client):
    c, _, sena = client
    layer = c.get("/v1/map/layers/overview").json()
    props = {f["properties"]["code"]: f["properties"] for f in layer["features"]}
    assert props[sena]["level"] == "CRITICAL" and props[sena]["worst_hazard"] == "flood"
    assert props[sena]["hazards_at_risk"] == 1
    other = next(p for code, p in props.items() if code != sena)
    assert other["level"] == "NORMAL" and other["worst_hazard"] is None
    assert c.get("/v1/map/layers/not_a_hazard").status_code == 404


def test_air_stations_endpoint(client):
    c, svc, _ = client
    svc.pm25_stations = [{"id": "1t", "name_th": "ทดสอบ", "area_th": "", "lat": 13.7, "lon": 100.5, "pm25": 42.0, "aqi": 90.0,
                          "time": "2026-10-22T03:00:00+07:00"}]
    body = c.get("/v1/air/stations").json()
    assert body["stations"][0]["pm25"] == 42.0 and "data_updated_at" in body


def test_flood_overlay_lists_only_districts_with_satellite_flood(client):
    c, svc, sena = client
    other = next(code for code in svc.amphoe_codes if code != sena)
    svc.satellite_cells = {sena: {"cells": 100, "approx_km2": 12.4}, other: {"cells": 0, "approx_km2": 0.0}}
    svc.satellite_updated_at = datetime.now(timezone.utc)
    body = c.get("/v1/map/overlays/flood").json()
    assert body["type"] == "FeatureCollection" and body["overlay"]["status"] == "ok"
    assert [f["properties"]["code"] for f in body["features"]] == [sena]
    props = body["features"][0]["properties"]
    assert props["flood_km2"] == 12.4 and 0 < props["flood_share"] < 1 and body["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_flood_overlay_reports_error_when_gistda_failed_before_any_data(client, monkeypatch):
    c, svc, _ = client
    monkeypatch.setenv("GISTDA_API_KEY", "x")
    monkeypatch.setattr(svc, "_start_job", lambda *args: None)  # no real GISTDA calls
    monkeypatch.setattr(svc, "satellite_updated_at", None)
    monkeypatch.setattr(svc, "source_status", {**svc.source_status, "gistda_flood": {"ok": False, "error": "ReadTimeout"}})
    assert c.get("/v1/map/overlays/flood").json()["overlay"]["status"] == "error"
    monkeypatch.setattr(svc, "source_status", {k: v for k, v in svc.source_status.items() if k != "gistda_flood"})
    assert c.get("/v1/map/overlays/flood").json()["overlay"]["status"] == "pending"


def cyclone_fixture_records():
    import json as _json

    from external_data.adapters.gdacs_cyclones import GdacsCyclones

    fixture = Path(__file__).resolve().parents[2] / "04_external_data_services" / "tests" / "fixtures" / "gdacs_cyclone_geometry.json"
    return GdacsCyclones().parse([_json.loads(fixture.read_text(encoding="utf-8"))], datetime.now(timezone.utc))


def test_cyclone_overlay_has_wind_areas_tracks_and_positions(client, monkeypatch):
    c, svc, _ = client
    monkeypatch.setattr(svc, "cyclone_records", cyclone_fixture_records())
    monkeypatch.setattr(svc, "cyclone_updated_at", datetime.now(timezone.utc))
    body = c.get("/v1/map/overlays/cyclones").json()
    kinds = [f["properties"]["kind"] for f in body["features"]]
    areas = sorted((f["properties"] for f in body["features"] if f["properties"]["kind"] == "wind_area"), key=lambda p: p["wind_kmh"])
    assert [(a["wind_kmh"], a["level"]) for a in areas] == [(63.0, "WATCH"), (93.0, "WARNING"), (119.0, "CRITICAL")]  # rules.yaml tropical_cyclone
    assert kinds.count("track") == 1 and kinds.count("forecast") == 1 and kinds.count("track_point") == 3
    points = [f["properties"] for f in body["features"] if f["properties"]["kind"] == "track_point"]
    assert [p["forecast"] for p in points] == [False, False, True] and [p["latest"] for p in points] == [False, True, False]
    assert body["overlay"]["storms"] == 1 and body["features"][0]["properties"]["event"] == "CHAMPI-26"
    built = svc._cyclone_features[1]
    c.get("/v1/map/overlays/cyclones")
    assert svc._cyclone_features[1] is built  # same records: not rebuilt per request


def test_cyclone_overlay_keeps_its_time_when_gdacs_fails(client, monkeypatch):
    import asyncio

    import httpx

    c, svc, _ = client
    two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    records = cyclone_fixture_records()
    for name, value in (("cyclone_records", records), ("cyclone_updated_at", two_hours_ago), ("updated_at", svc.updated_at),
                        ("source_status", dict(svc.source_status)), ("_inputs_changed", True)):
        monkeypatch.setattr(svc, name, value)

    async def failed_fetch():
        return {"gdacs_cyclones": httpx.ReadTimeout("slow")}, None

    monkeypatch.setattr(svc, "_fetch", failed_fetch)
    monkeypatch.setattr(svc, "_process", lambda results, warnings: (svc.decisions, None, {"gdacs_cyclones": {"ok": False}}))
    asyncio.run(svc.refresh())
    body = c.get("/v1/map/overlays/cyclones").json()
    assert svc.cyclone_records is records and svc.updated_at > two_hours_ago
    assert body["overlay"]["updated_at"] == two_hours_ago.isoformat() and "gdacs_cyclones" in body["degraded_services"]


def test_wave_overlay_levels_come_from_the_marine_rules(client, monkeypatch):
    import asyncio

    from api_backend import hazard_service
    from thanphai_shared import SourceRecord

    c, svc, _ = client
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(svc, "_wave_grid", None)
    asked = []

    async def fake_run(self, http):
        asked.append(len(self.points))
        return [SourceRecord(source="open_meteo_waves", variable="wave_height_max_24h", value=height, unit="m", lat=9.5, lon=lon,
                             issued_at=now, valid_time=now, fetched_at=now, attribution="t",
                             extra={"area_code": f"9.5,{lon}", "direction_from_deg": 30, "period_s": 6.5, "peak_time": now.isoformat()})
                for lon, height in ((101.0, 0.6), (101.5, 3.2))]

    monkeypatch.setattr(hazard_service.OpenMeteoWaveField, "run", fake_run)
    asyncio.run(svc.refresh_waves())
    body = c.get("/v1/map/overlays/waves").json()
    props = [f["properties"] for f in body["features"]]
    assert [p["level"] for p in props] == ["NORMAL", "WARNING"]  # rules.yaml: >= 3 m is WARNING
    assert props[0]["direction_from_deg"] == 30 and props[0]["direction_to_deg"] == 210
    assert body["features"][1]["geometry"]["coordinates"] == [101.5, 9.5] and body["overlay"]["status"] == "ok"
    asyncio.run(svc.refresh_waves())
    assert asked == [486, 2]  # land points (no waves) are not asked again


def test_wave_point_missing_on_the_full_check_comes_back_on_the_next_one(client, monkeypatch):
    import asyncio

    from api_backend import hazard_service
    from thanphai_shared import SourceRecord

    c, svc, _ = client
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(svc, "_wave_grid", None)
    monkeypatch.setattr(svc, "_wave_grid_checked_at", None)
    answering = {"9.5,101.0"}  # sea point B (9.5,101.5) has no data for a while
    asked = []

    async def fake_run(self, http):
        asked.append(len(self.points))
        return [SourceRecord(source="open_meteo_waves", variable="wave_height_max_24h", value=1.0, unit="m", lat=lat, lon=lon,
                             issued_at=now, valid_time=now, fetched_at=now, attribution="t",
                             extra={"area_code": code, "direction_from_deg": 30, "period_s": 6.5, "peak_time": now.isoformat()})
                for code, lat, lon in self.points if code in answering]

    def shown():
        return [f["geometry"]["coordinates"] for f in c.get("/v1/map/overlays/waves").json()["features"]]

    monkeypatch.setattr(hazard_service.OpenMeteoWaveField, "run", fake_run)
    asyncio.run(svc.refresh_waves())
    answering.add("9.5,101.5")  # B is back at the source
    asyncio.run(svc.refresh_waves())
    assert asked == [486, 1] and shown() == [[101.0, 9.5]]  # between full checks only A is asked
    svc._wave_grid_checked_at -= hazard_service.WAVE_FULL_GRID_EVERY + timedelta(minutes=1)
    asyncio.run(svc.refresh_waves())
    asyncio.run(svc.refresh_waves())
    assert asked[2:] == [486, 2] and shown() == [[101.0, 9.5], [101.5, 9.5]]  # the daily full check brings B back


def test_water_quality_endpoint_keeps_fresh_stations_saltiest_first(client, monkeypatch):
    import json as _json

    from external_data.adapters import ThaiWaterWaterQuality

    c, svc, _ = client
    fixture = Path(__file__).resolve().parents[2] / "04_external_data_services" / "tests" / "fixtures" / "tw_thailand_main_waterquality.json"
    records = ThaiWaterWaterQuality().parse(_json.loads(fixture.read_text(encoding="utf-8")), datetime.now(timezone.utc))
    at = datetime(2026, 10, 23, 4, 30, tzinfo=timezone(timedelta(hours=7)))
    monkeypatch.setattr(svc, "water_quality", svc._water_quality_rows(svc.fresh_levels(records, now=at)))
    body = c.get("/v1/water-quality").json()
    names = [s["name_th"] for s in body["stations"]]
    assert len(names) == 41 and "อยุธยา" not in names  # its reading (22:00) is older than 6 h
    first = body["stations"][0]
    assert first["name_th"] == "หนองหวาย" and first["salinity_gl"] == 0.54 and first["exceeds"]["salinity"]["text"] == "> 0.5"
    assert first["province_code"].startswith("TH") and "สสน." in body["source"]


def test_storms_endpoint_one_row_per_storm_with_track(client, monkeypatch):
    import json as _json

    from external_data.adapters import ThaiWaterStorms

    c, svc, _ = client
    fixture = Path(__file__).resolve().parents[2] / "04_external_data_services" / "tests" / "fixtures" / "tw_storm_data.json"
    records = ThaiWaterStorms().parse(_json.loads(fixture.read_text(encoding="utf-8")), datetime.now(timezone.utc))
    monkeypatch.setattr(svc, "storms", svc._storm_rows(records))
    storm = c.get("/v1/storms").json()["storms"][0]
    assert storm["name"] == "CHAMPI" and storm["wind_kmh"] == 185 and storm["category"]["category"] == "Cat 3"
    assert storm["time"] == "2026-10-22T12:00:00+00:00" and len(storm["track"]) == 1


def test_ask_rate_limit_per_visitor(client, monkeypatch):
    monkeypatch.setattr(main, "ASK_LIMIT_PER_MINUTE", 2)
    ask = lambda ip: client[0].post("/v1/ask", json={"question": "สวัสดี"}, headers={"cf-connecting-ip": ip})
    assert [ask("1.1.1.1").status_code for _ in range(3)] == [200, 200, 429]
    assert ask("2.2.2.2").status_code == 200  # another visitor is not blocked


def test_ask_rate_limit_window_expires(monkeypatch):
    monkeypatch.setattr(main, "ASK_LIMIT_PER_MINUTE", 1)
    main.check_ask_limit("9.9.9.9", now=100.0)
    with pytest.raises(main.HTTPException):
        main.check_ask_limit("9.9.9.9", now=130.0)
    main.check_ask_limit("9.9.9.9", now=161.0)


def test_shelters_filter_by_area_and_find_the_nearest(client, monkeypatch):
    c, svc, _ = client
    rows = [{"name": "โรงเรียนวัดเสนา", "responsible": "อบต.เสนา", "tambon": "เสนา", "amphoe_code": "TH1406", "capacity": 300,
             "lat": 14.32, "lon": 100.4, "approximate": False},
            {"name": "วัดบางนมโค", "responsible": "", "tambon": "บางนมโค", "amphoe_code": "TH1406", "capacity": 80,
             "lat": 14.28, "lon": 100.43, "approximate": True},
            {"name": "วัดปากนคร", "responsible": "", "tambon": "ปากนคร", "amphoe_code": "TH9303", "capacity": 100,
             "lat": 7.43, "lon": 100.01, "approximate": True}]
    monkeypatch.setattr(svc, "shelter_data", {"source": "ปภ.", "source_url": "https://data.go.th/x", "updated": "2024-08-09",
                                              "shelters": rows})
    every = c.get("/v1/shelters").json()
    assert len(every["shelters"]) == 3 and every["updated"] == "2024-08-09"
    in_district = [s["name"] for s in c.get("/v1/shelters", params={"area": "TH1406"}).json()["shelters"]]
    assert in_district == ["โรงเรียนวัดเสนา", "วัดบางนมโค"]
    assert len(c.get("/v1/shelters", params={"area": "TH14"}).json()["shelters"]) == 2  # the province
    tambon = next(a.code for a in svc.gazetteer.areas if a.level == "tambon" and a.parent_code == "TH1406")
    assert len(c.get("/v1/shelters", params={"area": tambon}).json()["shelters"]) == 2  # its district's
    assert c.get("/v1/shelters", params={"area": "x"}).status_code == 422
    nearest = c.get("/v1/shelters/nearest", params={"lat": 14.3, "lon": 100.42, "limit": 2}).json()["shelters"]
    assert [s["name"] for s in nearest] == ["วัดบางนมโค", "โรงเรียนวัดเสนา"] and nearest[0]["distance_km"] < 3
    assert c.get("/v1/shelters/nearest", params={"lat": 35, "lon": 100}).status_code == 422  # outside Thailand


def test_shelters_without_the_built_file_say_so(client, monkeypatch):
    c, svc, _ = client
    monkeypatch.setattr(svc, "shelter_data", None)
    assert c.get("/v1/shelters").status_code == 503
    assert c.get("/v1/shelters/nearest", params={"lat": 14.3, "lon": 100.4}).status_code == 503