import asyncio
from types import SimpleNamespace

import httpx
import pytest

import api_backend.main as main


@pytest.fixture
def radar_state(monkeypatch):
    clock = SimpleNamespace(monotonic=lambda: 10000.0, time=lambda: 1800000000.0)
    monkeypatch.setattr(main, "time", clock)
    monkeypatch.setattr(main, "_radar_cache", None)
    monkeypatch.setattr(main, "_radar_lock", asyncio.Lock(), raising=False)
    monkeypatch.setattr(main, "_radar_retry_at", 0.0, raising=False)
    monkeypatch.setenv("RADAR_LAYER", "internal")
    return clock


def maps(timestamp=1800000000):
    return {"host": "https://tilecache.rainviewer.com", "radar": {"past": [
        {"time": timestamp - 600, "path": "/v2/radar/a"},
        {"time": timestamp, "path": "/v2/radar/b"},
    ]}}


@pytest.mark.parametrize("initial", ["cold", "expired", "failed"])
async def test_radar_shares_concurrent_refresh(radar_state, monkeypatch, initial):
    if initial == "expired":
        monkeypatch.setattr(main, "_radar_cache", (9600.0, None))
    calls = []

    async def fetch():
        calls.append(1)
        await asyncio.sleep(0)  # Let the other requests reach the refresh before it finishes.
        if initial == "failed":
            raise httpx.ConnectError("offline")
        return maps()

    monkeypatch.setattr(main, "fetch_rainviewer_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        responses = await asyncio.gather(*(client.get("/v1/radar") for _ in range(20)))
    assert calls == [1]
    assert all(r.status_code == (502 if initial == "failed" else 200) for r in responses)
    if initial != "failed":
        assert all(r.json()["frame"] == {
            "tiles": "https://tilecache.rainviewer.com/v2/radar/b/256/{z}/{x}/{y}/2/1_1.png",
            "time": 1800000000,
        } for r in responses)


@pytest.mark.parametrize("has_frame", [False, True])
async def test_radar_failure_waits_before_retry_and_recovers(radar_state, monkeypatch, has_frame):
    frame = {"tiles": "last-good", "time": 1799999400}
    if has_frame:
        monkeypatch.setattr(main, "_radar_cache", (9600.0, frame))
    calls = []

    async def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("offline")
        return maps()

    monkeypatch.setattr(main, "fetch_rainviewer_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        for now in (10000.0, 10001.0, 10029.0):
            radar_state.monotonic = lambda: now
            response = await client.get("/v1/radar")
            assert response.status_code == (200 if has_frame else 502)
            if has_frame:
                assert response.json()["frame"] == frame
        assert calls == [1]
        radar_state.monotonic = lambda: 10030.0
        response = await client.get("/v1/radar")
        assert response.status_code == 200 and response.json()["frame"]["time"] == 1800000000
        assert calls == [1, 1]


@pytest.mark.parametrize("cached_age", [10, 400])
async def test_radar_drops_old_frame_even_during_cache_ttl(radar_state, monkeypatch, cached_age):
    monkeypatch.setattr(main, "_radar_cache", (10000.0 - cached_age, {"tiles": "expired", "time": 1799989200}))

    async def fetch():
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(main, "fetch_rainviewer_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.get("/v1/radar")
    assert response.status_code == 200 and response.json() == {"frame": None}


async def test_radar_frame_can_expire_while_retry_is_delayed(radar_state, monkeypatch):
    monkeypatch.setattr(main, "_radar_cache", (9600.0, {"tiles": "last-good", "time": 1799992810}))
    calls = []

    async def fetch():
        calls.append(1)
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(main, "fetch_rainviewer_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        assert (await client.get("/v1/radar")).json()["frame"] is not None
        radar_state.time = lambda: 1800000020.0
        radar_state.monotonic = lambda: 10020.0
        assert (await client.get("/v1/radar")).json() == {"frame": None}
    assert calls == [1]


@pytest.mark.parametrize("setting", [None, "off", "public"])
async def test_radar_disabled_never_fetches(radar_state, monkeypatch, setting):
    if setting is None:
        monkeypatch.delenv("RADAR_LAYER")
    else:
        monkeypatch.setenv("RADAR_LAYER", setting)

    async def fetch():
        pytest.fail("disabled radar must not call RainViewer")

    monkeypatch.setattr(main, "fetch_rainviewer_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        assert (await client.get("/v1/radar")).status_code == 404


@pytest.mark.parametrize("empty", [False, True])
async def test_radar_success_is_cached_for_five_minutes(radar_state, monkeypatch, empty):
    calls = []

    async def fetch():
        calls.append(1)
        return {"radar": {"past": []}} if empty else maps()

    monkeypatch.setattr(main, "fetch_rainviewer_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        first = await client.get("/v1/radar")
        radar_state.monotonic = lambda: 10299.0
        second = await client.get("/v1/radar")
        assert first.json() == second.json() and calls == [1]
        assert (first.json()["frame"] is None) == empty
        radar_state.monotonic = lambda: 10301.0
        assert (await client.get("/v1/radar")).status_code == 200
        assert calls == [1, 1]
