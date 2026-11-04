import asyncio
import time
from unittest.mock import AsyncMock, MagicMock
import httpx
import pytest

from external_data.adapters import rainviewer
from external_data.adapters.rainviewer import build_tile_url, fetch_radar_maps, parse_radar_frames

SAMPLE_MAPS = {
    "version": "v2",
    "generated": 1793723400,
    "host": "https://tilecache.rainviewer.com",
    "radar": {
        "past": [
            {"time": 1793722200, "path": "/v2/radar/0c933e4ed2a3"},
            {"time": 1793722800, "path": "/v2/radar/c35c42546b19"},
            {"time": 1793723400, "path": "/v2/radar/320227828fee"},
        ],
        "nowcast": [],
    },
}


def test_parse_radar_frames():
    frames = parse_radar_frames(SAMPLE_MAPS)
    assert len(frames) == 3
    assert frames[-1]["time"] == 1793723400
    assert frames[-1]["path"] == "/v2/radar/320227828fee"
    assert "tile_url" in frames[-1]
    assert "https://tilecache.rainviewer.com/v2/radar/320227828fee/256/{z}/{x}/{y}/2/1_1.png" == frames[-1]["tile_url"]


def test_build_tile_url():
    url = build_tile_url("https://tilecache.rainviewer.com", "/v2/radar/abc12345")
    assert url == "https://tilecache.rainviewer.com/v2/radar/abc12345/256/{z}/{x}/{y}/2/1_1.png"


def test_fetch_radar_maps_does_not_close_caller_client(monkeypatch):
    # Reset cache
    rainviewer._CACHE_FRAMES = None
    rainviewer._CACHE_TIME = 0.0

    mock_resp = MagicMock()
    mock_resp.json.return_value = SAMPLE_MAPS
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    # Mock aclose to verify it is NOT called
    mock_client.aclose = AsyncMock()

    frames = asyncio.run(fetch_radar_maps(client=mock_client))
    assert len(frames) == 3
    mock_client.get.assert_called_once()
    mock_client.aclose.assert_not_called()


def test_fetch_radar_maps_caches_results(monkeypatch):
    rainviewer._CACHE_FRAMES = None
    rainviewer._CACHE_TIME = 0.0

    mock_resp = MagicMock()
    mock_resp.json.return_value = SAMPLE_MAPS
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    # First call hits mock_client
    frames1 = asyncio.run(fetch_radar_maps(client=mock_client))
    assert mock_client.get.call_count == 1

    # Second call within TTL hits cache without calling mock_client
    frames2 = asyncio.run(fetch_radar_maps(client=mock_client))
    assert mock_client.get.call_count == 1
    assert frames1 == frames2


@pytest.fixture
def fresh_adapter(monkeypatch):
    monkeypatch.setattr(rainviewer, "_CACHE_FRAMES", None)
    monkeypatch.setattr(rainviewer, "_CACHE_TIME", 0.0)
    monkeypatch.setattr(rainviewer, "_FAIL_ERROR", None)
    monkeypatch.setattr(rainviewer, "_FAIL_TIME", 0.0)


class SlowClient:
    """Answers after a pause so concurrent callers overlap the first request."""

    def __init__(self, fail: bool = False):
        self.calls = 0
        self.fail = fail

    async def get(self, url):
        self.calls += 1
        await asyncio.sleep(0.05)
        if self.fail:
            raise httpx.ConnectError("down")
        resp = MagicMock()
        resp.json.return_value = SAMPLE_MAPS
        return resp


async def _many(client, n=20):
    return await asyncio.gather(*(fetch_radar_maps(client=client) for _ in range(n)), return_exceptions=True)


@pytest.mark.parametrize("expired", [False, True])
def test_concurrent_callers_share_one_request(fresh_adapter, monkeypatch, expired):
    if expired:
        monkeypatch.setattr(rainviewer, "_CACHE_FRAMES", [{"time": 1, "path": "/old"}])
        monkeypatch.setattr(rainviewer, "_CACHE_TIME", time.time() - rainviewer.CACHE_TTL_SECONDS - 1)
    client = SlowClient()
    results = asyncio.run(_many(client))
    assert client.calls == 1
    assert all(r == results[0] for r in results)
    assert results[0][-1]["time"] == 1793723400


def test_concurrent_failure_shares_one_request_then_retries(fresh_adapter, monkeypatch):
    client = SlowClient(fail=True)
    results = asyncio.run(_many(client))
    assert client.calls == 1
    assert all(isinstance(r, Exception) for r in results)

    # Within the retry wait: no new request.
    with pytest.raises(httpx.HTTPError):
        asyncio.run(fetch_radar_maps(client=client))
    assert client.calls == 1

    # After the wait, concurrent callers retry with one request and succeed.
    monkeypatch.setattr(rainviewer, "_FAIL_TIME", time.time() - rainviewer.RETRY_SECONDS - 1)
    client.fail = False
    results = asyncio.run(_many(client))
    assert client.calls == 2
    assert all(isinstance(r, list) and len(r) == 3 for r in results)
