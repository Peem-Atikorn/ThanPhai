"""openrouteservice and GISTDA flood cells for shelter routes: offline, with recorded and made-up answers."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from external_data.adapters.gistda import fetch_flood_cells
from external_data.adapters.openrouteservice import RouteError, fetch_route, parse_route

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ors_route.json").read_text(encoding="utf-8"))
KEY = "test-key-123"


def run(handler, coro):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await coro(client)
    return asyncio.run(go())


def test_parse_recorded_route():
    route = parse_route(FIXTURE)
    assert route["geometry"]["type"] == "LineString" and len(route["geometry"]["coordinates"]) > 50
    assert 10_000 < route["distance_m"] < 13_000 and route["duration_s"] > 0


def test_request_sends_lon_lat_the_key_in_a_header_and_the_avoid_area():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"] = str(request.url), request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=FIXTURE)

    avoid = {"type": "Polygon", "coordinates": [[[100.5, 7.0], [100.51, 7.0], [100.51, 7.01], [100.5, 7.0]]]}
    run(handler, lambda c: fetch_route(c, (7.0086, 100.4747), (7.0518, 100.5278), "foot-walking", avoid, api_key=KEY))
    assert seen["url"] == "https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson"
    assert seen["auth"] == KEY and KEY not in seen["url"]
    assert seen["body"]["coordinates"] == [[100.4747, 7.0086], [100.5278, 7.0518]]
    assert seen["body"]["options"]["avoid_polygons"] == avoid


def test_errors_carry_the_ors_code_but_never_the_key():
    def handler(request):
        return httpx.Response(400, json={"error": {"code": 2004, "message": f"too big {KEY}"}})

    with pytest.raises(RouteError) as caught:
        run(handler, lambda c: fetch_route(c, (7.0, 100.4), (7.1, 100.5), api_key=KEY))
    assert caught.value.code == 2004 and caught.value.status == 400 and KEY not in str(caught.value)


def test_no_key_and_unknown_profile_are_refused_before_any_request():
    def handler(request):
        raise AssertionError("no request expected")

    with pytest.raises(RouteError):
        run(handler, lambda c: fetch_route(c, (7.0, 100.4), (7.1, 100.5), api_key=""))
    with pytest.raises(ValueError):
        run(handler, lambda c: fetch_route(c, (7.0, 100.4), (7.1, 100.5), "cycling-road", api_key=KEY))


def test_flood_cells_page_through_the_box_and_keep_only_geometries():
    cell = {"type": "MultiPolygon", "coordinates": [[[[100.4, 14.3], [100.401, 14.3], [100.401, 14.301], [100.4, 14.3]]]]}
    offsets = []

    def handler(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        count = 2 if offset < 4 else 1
        features = [{"geometry": cell, "properties": {}, "links": [{"href": f"x?api_key={KEY}"}]} for _ in range(count)]
        return httpx.Response(200, json={"numberMatched": 5, "features": features})

    cells, total = run(handler, lambda c: fetch_flood_cells(c, (100.3, 14.2, 100.5, 14.4), page=2, api_key=KEY))
    assert total == 5 and len(cells) == 5 and offsets == [0, 2, 4] and KEY not in json.dumps(cells)


def test_flood_cells_stop_at_the_cap():
    cell = {"type": "Polygon", "coordinates": [[[100.4, 14.3], [100.401, 14.3], [100.401, 14.301], [100.4, 14.3]]]}

    def handler(request):
        limit = int(request.url.params["limit"])
        return httpx.Response(200, json={"numberMatched": 9000, "features": [{"geometry": cell}] * limit})

    cells, total = run(handler, lambda c: fetch_flood_cells(c, (100.3, 14.2, 100.5, 14.4), max_cells=3, page=2, api_key=KEY))
    assert len(cells) == 3 and total == 9000
