import asyncio
from types import SimpleNamespace

import httpx
import pytest

import api_backend.main as main
from external_data.adapters.rainviewer import parse_radar_frames


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
    # The frame list the module 04 adapter returns for a weather-maps.json response.
    return parse_radar_frames({"host": "https://tilecache.rainviewer.com", "radar": {"past": [
        {"time": timestamp - 600, "path": "/v2/radar/a"},
        {"time": timestamp, "path": "/v2/radar/b"},
    ]}})


def cached_frames(*frames):
    return [{"tiles": frame["tile_url"], "time": frame["time"]} for frame in frames]


async def test_radar_returns_the_seven_latest_frames_from_the_last_hour(radar_state, monkeypatch):
    latest_time = 1800000000 - 600  # RainViewer's latest image can lag the wall clock.
    frames = parse_radar_frames({"host": "https://tilecache.rainviewer.com", "radar": {"past": [
        {"time": latest_time - minutes * 60, "path": f"/v2/radar/{minutes}"}
        for minutes in range(80, -1, -10)
    ]}})

    async def fetch():
        return frames

    monkeypatch.setattr(main, "fetch_radar_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.get("/v1/radar")
    assert response.status_code == 200
    result = response.json()["frames"]
    assert [frame["time"] for frame in result] == [latest_time - minutes * 60 for minutes in (60, 50, 40, 30, 20, 10, 0)]
    assert all(set(frame) == {"tiles", "time"} for frame in result)


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

    monkeypatch.setattr(main, "fetch_radar_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        responses = await asyncio.gather(*(client.get("/v1/radar") for _ in range(20)))
    assert calls == [1]
    assert all(r.status_code == (502 if initial == "failed" else 200) for r in responses)
    if initial != "failed":
        assert all(r.json()["frames"] == cached_frames(*maps()) for r in responses)


@pytest.mark.parametrize("has_frame", [False, True])
async def test_radar_failure_waits_before_retry_and_recovers(radar_state, monkeypatch, has_frame):
    frame = {"tiles": "last-good", "time": 1799999400}
    if has_frame:
        monkeypatch.setattr(main, "_radar_cache", (9600.0, [frame]))
    calls = []

    async def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("offline")
        return maps()

    monkeypatch.setattr(main, "fetch_radar_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        for now in (10000.0, 10001.0, 10029.0):
            radar_state.monotonic = lambda: now
            response = await client.get("/v1/radar")
            assert response.status_code == (200 if has_frame else 502)
            if has_frame:
                assert response.json()["frames"] == [frame]
        assert calls == [1]
        radar_state.monotonic = lambda: 10030.0
        response = await client.get("/v1/radar")
        assert response.status_code == 200 and response.json()["frames"][-1]["time"] == 1800000000
        assert calls == [1, 1]


@pytest.mark.parametrize("cached_age", [10, 400])
async def test_radar_drops_old_frame_even_during_cache_ttl(radar_state, monkeypatch, cached_age):
    monkeypatch.setattr(main, "_radar_cache", (10000.0 - cached_age, [{"tiles": "expired", "time": 1799989200}]))

    async def fetch():
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(main, "fetch_radar_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.get("/v1/radar")
    assert response.status_code == 200 and response.json() == {"frames": []}


async def test_radar_drops_history_when_latest_frame_is_over_two_hours_old(radar_state, monkeypatch):
    monkeypatch.setattr(main, "_radar_cache", (10000.0, [{"tiles": "expired", "time": 1799992799}]))

    async def fetch():
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(main, "fetch_radar_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.get("/v1/radar")
    assert response.status_code == 200 and response.json() == {"frames": []}


async def test_radar_frame_can_expire_while_retry_is_delayed(radar_state, monkeypatch):
    monkeypatch.setattr(main, "_radar_cache", (9600.0, [{"tiles": "last-good", "time": 1799992810}]))
    calls = []

    async def fetch():
        calls.append(1)
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(main, "fetch_radar_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        assert (await client.get("/v1/radar")).json()["frames"]
        radar_state.time = lambda: 1800000020.0
        radar_state.monotonic = lambda: 10020.0
        assert (await client.get("/v1/radar")).json() == {"frames": []}
    assert calls == [1]


@pytest.mark.parametrize("setting", [None, "off", "public"])
async def test_radar_disabled_never_fetches(radar_state, monkeypatch, setting):
    if setting is None:
        monkeypatch.delenv("RADAR_LAYER")
    else:
        monkeypatch.setenv("RADAR_LAYER", setting)

    async def fetch():
        pytest.fail("disabled radar must not call RainViewer")

    monkeypatch.setattr(main, "fetch_radar_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        assert (await client.get("/v1/radar")).status_code == 404


@pytest.mark.parametrize("empty", [False, True])
async def test_radar_success_is_cached_for_five_minutes(radar_state, monkeypatch, empty):
    calls = []

    async def fetch():
        calls.append(1)
        return [] if empty else maps()

    monkeypatch.setattr(main, "fetch_radar_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        first = await client.get("/v1/radar")
        radar_state.monotonic = lambda: 10299.0
        second = await client.get("/v1/radar")
        assert first.json() == second.json() and calls == [1]
        assert (not first.json()["frames"]) == empty
        radar_state.monotonic = lambda: 10301.0
        assert (await client.get("/v1/radar")).status_code == 200
        assert calls == [1, 1]


@pytest.mark.parametrize("image_time, want", [
    (1799999400, [{"tiles": "last-good", "time": 1799999400}]),
    (1799989200, []),
])
async def test_radar_cached_response_does_not_wait_for_refresh(radar_state, monkeypatch, image_time, want):
    monkeypatch.setattr(main, "_radar_cache", (9600.0, [{"tiles": "last-good", "time": image_time}]))
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def fetch():
        calls.append(1)
        started.set()
        await release.wait()
        return maps()

    monkeypatch.setattr(main, "fetch_radar_maps", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        refresh = asyncio.create_task(client.get("/v1/radar"))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            cached = await asyncio.wait_for(client.get("/v1/radar"), timeout=1)
            assert cached.status_code == 200 and cached.json() == {"frames": want}
            assert not refresh.done() and calls == [1]
        finally:
            release.set()
            refreshed = await refresh
        assert refreshed.status_code == 200 and refreshed.json()["frames"][-1]["time"] == 1800000000
        assert (await client.get("/v1/radar")).json() == refreshed.json()
        assert calls == [1]


@pytest.mark.parametrize("has_frame", [False, True])
async def test_radar_handles_adapter_retry_wait(radar_state, monkeypatch, has_frame):
    # The nowcast hit a RainViewer failure just before: the real adapter now refuses new requests
    # for its retry wait, and /v1/radar must answer as for any failed refresh.
    import time as real_time

    import external_data.adapters.rainviewer as rv

    monkeypatch.setattr(main, "fetch_radar_maps", rv.fetch_radar_maps)
    monkeypatch.setattr(rv, "_CACHE_FRAMES", None)
    monkeypatch.setattr(rv, "_FAIL_ERROR", httpx.ConnectError("offline"))
    monkeypatch.setattr(rv, "_FAIL_TIME", real_time.time())
    frame = {"tiles": "last-good", "time": 1799999400}
    if has_frame:
        monkeypatch.setattr(main, "_radar_cache", (9600.0, [frame]))  # due for a refresh

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        r = await client.get("/v1/radar")
    if has_frame:
        assert r.status_code == 200 and r.json() == {"frames": [frame]}
    else:
        assert r.status_code == 502
    assert main._radar_retry_at == 10000.0 + main.RADAR_RETRY_S
