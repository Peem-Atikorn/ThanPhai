import asyncio
from datetime import datetime, timezone
from pathlib import Path
import struct
import zlib
import numpy as np
import pytest

from api_backend.hazard_service import HazardService

DATA = Path(__file__).resolve().parents[2] / "data" / "processed"


def make_png(blob_y: int, blob_x: int, intensity: int = 255) -> bytes:
    """Create a synthetic 256x256 RGBA PNG with a rain blob."""
    raw = np.zeros((256, 256 * 4 + 1), dtype=np.uint8)
    raw[blob_y : blob_y + 15, (blob_x * 4) + 1 : ((blob_x + 15) * 4) + 1] = intensity
    compressed = zlib.compress(raw.tobytes())
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I4sIIBBBBB", 13, b"IHDR", 256, 256, 8, 6, 0, 0, 0)
        + b"crc1"
        + struct.pack(">I4s", len(compressed), b"IDAT")
        + compressed
        + b"crc2"
    )


class FakeArea:
    def __init__(self, code, name_th, lat, lon, parent_code=None, level="amphoe"):
        self.code = code
        self.name_th = name_th
        self.lat = lat
        self.lon = lon
        self.parent_code = parent_code
        self.level = level


class FakeGazetteer:
    def __init__(self):
        prov = FakeArea("TH10", "กรุงเทพมหานคร", 13.75, 100.5, level="province")
        amp1 = FakeArea("TH1001", "พระนคร", 13.75, 100.5, parent_code="TH10", level="amphoe")
        amp2 = FakeArea("TH1047", "บางนา", 13.66, 100.60, parent_code="TH10", level="amphoe")
        self.areas = [prov, amp1, amp2]
        self.by_code = {a.code: a for a in self.areas}


def test_get_radar_nowcast_with_real_moving_rain(monkeypatch):
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = ["TH1001", "TH1047"]
    svc.gazetteer = FakeGazetteer()

    fake_frames = [
        {"time": 1793722800, "path": "/v2/radar/frame1", "tile_url": "http://tiles/1.png"},
        {"time": 1793723400, "path": "/v2/radar/frame2", "tile_url": "http://tiles/2.png"},
    ]

    async def fake_fetch_maps(client=None):
        return fake_frames

    # Blob near Bangkok at (95, 115) moves to (96, 116) in 10 minutes (South-East)
    tile1 = make_png(95, 115)
    tile2 = make_png(96, 116)

    async def fake_fetch_tile(url, client=None):
        if "frame1" in url:
            return tile1
        return tile2

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)
    monkeypatch.setattr(rv, "fetch_tile_bytes", fake_fetch_tile)

    result = asyncio.run(svc.get_radar_nowcast())

    assert result["status"] == "ok"
    assert len(result["past_frames"]) == 2
    # forecast_frames contains 6 extrapolated frames (+10 to +60 minutes)
    assert len(result["forecast_frames"]) == 6
    assert result["forecast_frames"][0]["horizon_min"] == 10
    assert result["forecast_frames"][0]["dx_pixels"] > 0

    # Motion is calculated from optical flow on tiles
    assert result["motion"] is not None
    assert result["motion"]["speed_kmh"] > 0.0
    assert result["motion"]["direction_th"] == "ตะวันออกเฉียงใต้"

    # Rain cells detected from frame2
    assert len(result["rain_cells"]) >= 1
    assert result["rain_cells"][0]["peak_intensity_dbz"] > 0.0

    # Explanation text is safe and contains computed direction
    assert "ตะวันออกเฉียงใต้" in result["explanation"]
    assert "RainViewer" in result["source"]


def test_get_radar_nowcast_with_no_rain(monkeypatch):
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = []
    svc.gazetteer = FakeGazetteer()

    fake_frames = [
        {"time": 1793722800, "path": "/v2/radar/frame1", "tile_url": "http://tiles/1.png"},
        {"time": 1793723400, "path": "/v2/radar/frame2", "tile_url": "http://tiles/2.png"},
    ]

    empty_tile = make_png(0, 0, intensity=0)

    async def fake_fetch_maps(client=None):
        return fake_frames

    async def fake_fetch_tile(url, client=None):
        return empty_tile

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)
    monkeypatch.setattr(rv, "fetch_tile_bytes", fake_fetch_tile)

    result = asyncio.run(svc.get_radar_nowcast())

    assert result["status"] == "ok"
    assert len(result["past_frames"]) == 2
    assert result["motion"] is None
    assert result["rain_cells"] == []
    assert "ไม่พบกลุ่มฝนที่มีนัยสำคัญ" in result["explanation"]


def test_get_radar_nowcast_with_stationary_rain(monkeypatch):
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = ["TH1001", "TH1047"]
    svc.gazetteer = FakeGazetteer()

    fake_frames = [
        {"time": 1793722800, "path": "/v2/radar/frame1", "tile_url": "http://tiles/1.png"},
        {"time": 1793723400, "path": "/v2/radar/frame2", "tile_url": "http://tiles/2.png"},
    ]

    # Rain blob near Bangkok in the exact same location in both frames -> speed < 2.0 km/h
    tile = make_png(95, 115)

    async def fake_fetch_maps(client=None):
        return fake_frames

    async def fake_fetch_tile(url, client=None):
        return tile

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)
    monkeypatch.setattr(rv, "fetch_tile_bytes", fake_fetch_tile)

    result = asyncio.run(svc.get_radar_nowcast())

    assert result["status"] == "ok"
    assert result["motion"] is not None
    assert result["motion"]["direction_th"] == "เกือบอยู่กับที่"
    assert len(result["rain_cells"]) >= 1
    assert "เกือบอยู่กับที่" in result["explanation"] or "ฝนสะสม" in result["explanation"]


def test_get_radar_nowcast_unavailable_when_no_frames(monkeypatch):
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = []
    svc.gazetteer = FakeGazetteer()

    async def fake_fetch_maps(client=None):
        return []

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)

    result = asyncio.run(svc.get_radar_nowcast())

    assert result["status"] == "unavailable"
    assert "ไม่สามารถดึงข้อมูลเรดาร์ฝนได้ในขณะนี้" in result["explanation"]
