import pytest

from external_data.adapters.rainviewer import build_tile_url, parse_radar_frames

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
