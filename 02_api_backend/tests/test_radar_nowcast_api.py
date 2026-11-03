import asyncio
from datetime import datetime, timezone
import io
from pathlib import Path
import numpy as np
from PIL import Image
import pytest

from api_backend.hazard_service import HazardService

DATA = Path(__file__).resolve().parents[2] / "data" / "processed"


def make_png(blob_y: int = 0, blob_x: int = 0, color_rgb: tuple[int, int, int] | None = (255, 68, 0)) -> bytes:
    """Create a synthetic 256x256 RGBA PNG using official Universal Blue color palette.

    (255, 68, 0) corresponds to 45.0 dBZ.
    """
    im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    if color_rgb is not None:
        for y in range(blob_y, min(256, blob_y + 15)):
            for x in range(blob_x, min(256, blob_x + 15)):
                im.putpixel((x, y), (*color_rgb, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


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

    # Blob near Bangkok at (90, 112) moves to (91, 113) in 10 minutes (South-East)
    tile1 = make_png(90, 112)
    tile2 = make_png(91, 113)

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

    empty_tile = make_png(color_rgb=None)

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
    tile = make_png(90, 112)

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


def test_get_radar_nowcast_with_undetermined_motion(monkeypatch):
    """When storm motion exceeds 120 km/h, report motion: None and 'ยังประเมินทิศทางไม่ได้'."""
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = ["TH1001", "TH1047"]
    svc.gazetteer = FakeGazetteer()

    fake_frames = [
        {"time": 1793722800, "path": "/v2/radar/frame1", "tile_url": "http://tiles/1.png"},
        {"time": 1793723400, "path": "/v2/radar/frame2", "tile_url": "http://tiles/2.png"},
    ]

    # Massive displacement between frame1 and frame2 (> 120 km/h) -> triggers noise cutoff
    # Frame 2 has rain cell over Bangkok (90, 112); Frame 1 has it shifted by 40 pixels
    tile1 = make_png(50, 72)
    tile2 = make_png(90, 112)

    async def fake_fetch_maps(client=None):
        return fake_frames

    async def fake_fetch_tile(url, client=None):
        if "frame1" in url:
            return tile1
        return tile2

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)
    monkeypatch.setattr(rv, "fetch_tile_bytes", fake_fetch_tile)

    result = asyncio.run(svc.get_radar_nowcast())

    assert result["status"] == "ok"
    assert result["motion"] is None
    assert len(result["rain_cells"]) >= 1
    # Rain cells returned without ETA / target districts
    assert result["rain_cells"][0]["target_districts"] == []
    assert "ยังประเมินทิศทางไม่ได้" in result["explanation"]


def test_get_radar_nowcast_filters_cells_outside_thailand(monkeypatch):
    """Rain cell in Cambodia (far from any Thai amphoe centroid) must be filtered out."""
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = ["TH1001", "TH1047"]
    svc.gazetteer = FakeGazetteer()

    fake_frames = [
        {"time": 1793722800, "path": "/v2/radar/frame1", "tile_url": "http://tiles/1.png"},
        {"time": 1793723400, "path": "/v2/radar/frame2", "tile_url": "http://tiles/2.png"},
    ]

    # Cell located at bottom-right corner of tile 4/12/7 (approx 104°E, 6°N - Gulf/Malaysia/Cambodia sea)
    # > 200 km away from Bangkok amphoes
    tile = make_png(230, 230)

    async def fake_fetch_maps(client=None):
        return fake_frames

    async def fake_fetch_tile(url, client=None):
        return tile

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)
    monkeypatch.setattr(rv, "fetch_tile_bytes", fake_fetch_tile)

    result = asyncio.run(svc.get_radar_nowcast())

    assert result["status"] == "ok"
    # Filtered out: no cells in Thailand territory
    assert result["rain_cells"] == []
    assert "ไม่พบกลุ่มฝนที่มีนัยสำคัญ" in result["explanation"]


def test_get_radar_nowcast_concurrent_lock(monkeypatch):
    """Concurrent requests for the same frame must execute computation only once."""
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = ["TH1001", "TH1047"]
    svc.gazetteer = FakeGazetteer()
    svc._radar_nowcast_lock = asyncio.Lock()
    svc._radar_nowcast_cache = None

    fake_frames = [
        {"time": 1793722800, "path": "/v2/radar/frame1", "tile_url": "http://tiles/1.png"},
        {"time": 1793723400, "path": "/v2/radar/frame2", "tile_url": "http://tiles/2.png"},
    ]

    fetch_tile_count = 0
    tile = make_png(90, 112)

    async def fake_fetch_maps(client=None):
        return fake_frames

    async def fake_fetch_tile(url, client=None):
        nonlocal fetch_tile_count
        fetch_tile_count += 1
        await asyncio.sleep(0.05)  # Simulate network latency
        return tile

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)
    monkeypatch.setattr(rv, "fetch_tile_bytes", fake_fetch_tile)

    async def run_concurrent():
        return await asyncio.gather(
            svc.get_radar_nowcast(),
            svc.get_radar_nowcast(),
            svc.get_radar_nowcast(),
        )

    results = asyncio.run(run_concurrent())

    assert len(results) == 3
    for r in results:
        assert r["status"] == "ok"
    # Only 2 tile fetches occurred (1 for frame1, 1 for frame2), NOT 6 fetches
    assert fetch_tile_count == 2


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
