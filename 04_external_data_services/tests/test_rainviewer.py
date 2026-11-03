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
