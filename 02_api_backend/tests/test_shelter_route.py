"""Routes to shelters: only to listed shelters, around GISTDA flood water when possible, and never called safe."""

import pytest
from fastapi.testclient import TestClient

import api_backend.hazard_service as hs
import api_backend.main as main
from api_backend.hazard_service import HazardService
from external_data.adapters.openrouteservice import RouteError

DATA = main.DATA_DIR
SHELTER = {"name": "โรงเรียนวัดเสนา", "tambon": "เสนา", "amphoe": "เสนา", "amphoe_code": "TH1406", "capacity": 300,
           "lat": 14.33, "lon": 100.40, "approximate": False}
START = {"lat": 14.30, "lon": 100.40}
# A straight route north; the flood square sits across it at 14.31-14.32.
LINE = {"type": "LineString", "coordinates": [[100.40, 14.30], [100.40, 14.33]]}
FLOOD = {"type": "Polygon", "coordinates": [[[100.39, 14.31], [100.41, 14.31], [100.41, 14.32], [100.39, 14.32], [100.39, 14.31]]]}
DETOUR = {"type": "LineString", "coordinates": [[100.40, 14.30], [100.43, 14.30], [100.43, 14.33], [100.40, 14.33]]}


@pytest.fixture(scope="module")
def svc():
    service = HazardService(DATA)
    service.shelter_data = {"source": "ปภ.", "source_url": "x", "updated": "2024-08-09", "shelters": [SHELTER]}
    main.service = service
    return service


@pytest.fixture
def client(svc, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "k")
    svc._flood_shapes.clear()
    return TestClient(main.app)


def body(**extra):
    return {**START, "to_lat": SHELTER["lat"], "to_lon": SHELTER["lon"], **extra}


def fake_routes(monkeypatch, *answers):
    calls = []

    async def fetch_route(client, start, end, profile="driving-car", avoid=None, api_key=None):
        calls.append({"start": start, "end": end, "profile": profile, "avoid": avoid})
        answer = answers[len(calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return {"geometry": answer, "distance_m": 3400.0, "duration_s": 400.0}
    monkeypatch.setattr(hs, "fetch_route", fetch_route)
    return calls


def fake_flood(monkeypatch, cells):
    async def fetch_flood_cells(client, bbox, window="7days", max_cells=3000, page=1000, api_key=None):
        return cells, len(cells)
    monkeypatch.setenv("GISTDA_API_KEY", "g")
    monkeypatch.setattr(hs, "fetch_flood_cells", fetch_flood_cells)


def test_without_gistda_the_route_is_unchecked(client, monkeypatch):
    monkeypatch.delenv("GISTDA_API_KEY", raising=False)
    calls = fake_routes(monkeypatch, LINE)
    r = client.post("/v1/shelters/route", json=body(profile="foot-walking"))
    assert r.status_code == 200
    d = r.json()
    assert d["flood"]["status"] == "unchecked" and d["shelter"]["name"] == "โรงเรียนวัดเสนา" and "OpenStreetMap" in d["attribution"]
    assert calls == [{"start": (14.30, 100.40), "end": (14.33, 100.40), "profile": "foot-walking", "avoid": None}]


def test_flood_water_is_sent_to_be_avoided_with_both_ends_left_open(client, monkeypatch):
    fake_flood(monkeypatch, [FLOOD])
    calls = fake_routes(monkeypatch, DETOUR)
    d = client.post("/v1/shelters/route", json=body()).json()
    avoid = hs.shapely_shape(calls[0]["avoid"])
    assert avoid.intersects(hs.shapely_shape(FLOOD)) and not avoid.contains(hs.Point(100.40, 14.30))
    assert d["flood"]["status"] == "avoided" and d["flood"]["crossing_km"] == 0 and d["flood"]["cells"] == 1


def test_refused_area_falls_back_and_warns_when_the_route_crosses_water(client, monkeypatch):
    fake_flood(monkeypatch, [FLOOD])
    calls = fake_routes(monkeypatch, RouteError("too big", 400, 2004), LINE)
    d = client.post("/v1/shelters/route", json=body()).json()
    assert calls[0]["avoid"] is not None and calls[1]["avoid"] is None
    assert d["flood"]["status"] == "crosses" and d["flood"]["avoid_refused"] and 0.9 < d["flood"]["crossing_km"] < 1.3


def test_no_flood_water_nearby_is_clear(client, monkeypatch):
    fake_flood(monkeypatch, [])
    calls = fake_routes(monkeypatch, LINE)
    d = client.post("/v1/shelters/route", json=body()).json()
    assert d["flood"]["status"] == "clear" and calls[0]["avoid"] is None


def test_only_listed_shelters_nearby_and_with_a_key(client, monkeypatch):
    fake_routes(monkeypatch, LINE)
    assert client.post("/v1/shelters/route", json=body(to_lat=14.5)).status_code == 404
    monkeypatch.setattr(main.service, "find_shelter", lambda lat, lon: {**SHELTER, "lat": 15.5})
    assert client.post("/v1/shelters/route", json=body(to_lat=15.5)).status_code == 422  # ~130 km away
    monkeypatch.undo()
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    assert client.post("/v1/shelters/route", json=body()).status_code == 503


def test_failed_route_is_a_502_and_visitors_are_rate_limited(client, monkeypatch):
    monkeypatch.delenv("GISTDA_API_KEY", raising=False)
    fake_routes(monkeypatch, RouteError("down", 503), LINE, LINE)
    assert client.post("/v1/shelters/route", json=body()).status_code == 502
    monkeypatch.setattr(main, "ROUTE_LIMIT_PER_MINUTE", 2)
    assert client.post("/v1/shelters/route", json=body()).status_code == 200
    assert client.post("/v1/shelters/route", json=body()).status_code == 429
