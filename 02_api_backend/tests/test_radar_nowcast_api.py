import asyncio
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from api_backend.hazard_service import HazardService
from api_backend.main import app

DATA = Path(__file__).resolve().parents[2] / "data" / "processed"
LATEST_FRAME = 1793723400


@pytest.fixture(autouse=True)
def radar_clock(monkeypatch):
    """The fake RainViewer frames below are one minute old."""
    import api_backend.hazard_service as hs
    import time as real_time
    from types import SimpleNamespace

    clock = SimpleNamespace(monotonic=real_time.monotonic, time=lambda: LATEST_FRAME + 60.0)
    monkeypatch.setattr(hs, "time", clock)
    return clock


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


def test_radar_nowcast_endpoint_disabled_by_default(monkeypatch):
    """GET /v1/radar/nowcast must return 404 when RADAR_LAYER is not 'internal' without fetching maps."""
    monkeypatch.delenv("RADAR_LAYER", raising=False)
    client = TestClient(app)

    import external_data.adapters.rainviewer as rv
    fetch_called = False

    async def fake_fetch_maps(client=None):
        nonlocal fetch_called
        fetch_called = True
        return []

    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)

    resp = client.get("/v1/radar/nowcast")
    assert resp.status_code == 404
    assert not fetch_called


def test_radar_nowcast_endpoint_allowed_with_internal_env(monkeypatch):
    """GET /v1/radar/nowcast answers when RADAR_LAYER=internal."""
    monkeypatch.setenv("RADAR_LAYER", "internal")
    import api_backend.main as main_mod

    svc = HazardService.__new__(HazardService)
    svc.updated_at = datetime.now(timezone.utc)
    svc.source_status = {}
    monkeypatch.setattr(HazardService, "inputs_pending", property(lambda self: []))

    async def fake_get_nowcast():
        return {"status": "ok", "rain_cells": []}

    svc.get_radar_nowcast = fake_get_nowcast
    monkeypatch.setattr(main_mod, "service", svc)

    client = TestClient(app)
    resp = client.get("/v1/radar/nowcast")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_get_radar_nowcast_with_real_moving_rain(monkeypatch):
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = ["TH1001", "TH1047"]
    svc.gazetteer = FakeGazetteer()
    svc._radar_nowcast_lock = asyncio.Lock()
    svc._radar_nowcast_cache = None
    svc._thai_radar_mask = None

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
    # The backend projects every cell for each frame, so the map draws its positions instead of recomputing them.
    lead = result["rain_cells"][0]
    first_pos = next(p for p in result["forecast_frames"][0]["cells"] if p["id"] == lead["id"])
    last_pos = next(p for p in result["forecast_frames"][-1]["cells"] if p["id"] == lead["id"])
    assert lead["centroid_lon"] < first_pos["lon"] < last_pos["lon"], "South-east motion moves the cell east"
    assert lead["centroid_lat"] > first_pos["lat"] > last_pos["lat"], "South-east motion moves the cell south"
    assert result["motion"]["is_moving"] is True
    assert result["explanation_pending"] is False

    # Motion is calculated from optical flow on tiles
    assert result["motion"] is not None
    assert result["motion"]["speed_kmh"] > 0.0
    assert result["motion"]["direction_th"] == "ตะวันออกเฉียงใต้"

    # Rain cells detected from frame2
    assert len(result["rain_cells"]) >= 1
    assert result["rain_cells"][0]["peak_intensity_dbz"] > 0.0

    # Explanation text is safe and contains computed direction and current rainfall notice
    assert "ตะวันออกเฉียงใต้" in result["explanation"]
    assert "ขณะนี้ฝนตกที่" in result["explanation"] or "พระนคร" in result["explanation"]
    assert "RainViewer" in result["source"]


def test_get_radar_nowcast_with_no_rain(monkeypatch):
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = []
    svc.gazetteer = FakeGazetteer()
    svc._radar_nowcast_lock = asyncio.Lock()
    svc._radar_nowcast_cache = None
    svc._thai_radar_mask = None

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
    svc._radar_nowcast_lock = asyncio.Lock()
    svc._radar_nowcast_cache = None
    svc._thai_radar_mask = None

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
    assert result["motion"]["is_moving"] is False
    lead = result["rain_cells"][0]
    assert all(abs(p["lat"] - lead["centroid_lat"]) < 0.05 and abs(p["lon"] - lead["centroid_lon"]) < 0.05
               for frame in result["forecast_frames"] for p in frame["cells"] if p["id"] == lead["id"])
    assert len(result["rain_cells"]) >= 1
    assert "เกือบอยู่กับที่" in result["explanation"] or "ฝนสะสม" in result["explanation"]


def test_get_radar_nowcast_with_undetermined_motion(monkeypatch):
    """When storm motion exceeds 120 km/h, report motion: None and 'ยังประเมินทิศทางไม่ได้'."""
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = ["TH1001", "TH1047"]
    svc.gazetteer = FakeGazetteer()
    svc._radar_nowcast_lock = asyncio.Lock()
    svc._radar_nowcast_cache = None
    svc._thai_radar_mask = None

    fake_frames = [
        {"time": 1793722800, "path": "/v2/radar/frame1", "tile_url": "http://tiles/1.png"},
        {"time": 1793723400, "path": "/v2/radar/frame2", "tile_url": "http://tiles/2.png"},
    ]

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
    svc._radar_nowcast_lock = asyncio.Lock()
    svc._radar_nowcast_cache = None
    svc._thai_radar_mask = None

    fake_frames = [
        {"time": 1793722800, "path": "/v2/radar/frame1", "tile_url": "http://tiles/1.png"},
        {"time": 1793723400, "path": "/v2/radar/frame2", "tile_url": "http://tiles/2.png"},
    ]

    # Cell located at bottom-right corner of tile 4/12/7 (> 200 km away from Bangkok amphoes)
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
    # Filtered out by raster mask: no cells in Thailand territory
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
    svc._thai_radar_mask = None

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


def test_get_radar_nowcast_unavailable_cached_shortly(monkeypatch):
    """When tile fetch fails, the unavailable result is cached for 30s to prevent thundering herd."""
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = []
    svc.gazetteer = FakeGazetteer()
    svc._radar_nowcast_lock = asyncio.Lock()
    svc._radar_nowcast_cache = None
    svc._thai_radar_mask = None

    fake_frames = [
        {"time": 1793722800, "path": "/v2/radar/frame1", "tile_url": "http://tiles/1.png"},
        {"time": 1793723400, "path": "/v2/radar/frame2", "tile_url": "http://tiles/2.png"},
    ]

    fetch_tile_attempts = 0

    async def fake_fetch_maps(client=None):
        return fake_frames

    async def failing_fetch_tile(url, client=None):
        nonlocal fetch_tile_attempts
        fetch_tile_attempts += 1
        raise RuntimeError("tile network timeout")

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)
    monkeypatch.setattr(rv, "fetch_tile_bytes", failing_fetch_tile)

    res1 = asyncio.run(svc.get_radar_nowcast())
    assert res1["status"] == "unavailable"
    # Initial attempt triggered tile download
    assert fetch_tile_attempts >= 1

    # Second call uses cache within 30s and does not retry network
    initial_attempts = fetch_tile_attempts
    res2 = asyncio.run(svc.get_radar_nowcast())
    assert res2["status"] == "unavailable"
    assert fetch_tile_attempts == initial_attempts


def _bare_service():
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = ["TH1001", "TH1047"]
    svc.gazetteer = FakeGazetteer()
    svc._radar_nowcast_lock = asyncio.Lock()
    svc._radar_nowcast_cache = None
    svc._thai_radar_mask = None
    return svc


def test_get_radar_nowcast_drops_stale_frames(monkeypatch, radar_clock):
    """A latest RainViewer image over 2 h old gives no frames and no forecast (same rule as /v1/radar)."""
    svc = _bare_service()
    radar_clock.time = lambda: LATEST_FRAME + 3 * 3600.0
    tile_called = False

    async def fake_fetch_maps(client=None):
        return [
            {"time": LATEST_FRAME - 600, "path": "/v2/radar/frame1", "tile_url": "http://tiles/1.png"},
            {"time": LATEST_FRAME, "path": "/v2/radar/frame2", "tile_url": "http://tiles/2.png"},
        ]

    async def fake_fetch_tile(url, client=None):
        nonlocal tile_called
        tile_called = True
        return make_png(90, 112)

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)
    monkeypatch.setattr(rv, "fetch_tile_bytes", fake_fetch_tile)

    result = asyncio.run(svc.get_radar_nowcast())
    assert result["status"] == "unavailable"
    assert result["past_frames"] == [] and result["forecast_frames"] == []
    assert not tile_called


def test_get_radar_nowcast_keeps_only_the_last_hour(monkeypatch):
    svc = _bare_service()
    frames = [{"time": LATEST_FRAME - minutes * 60, "path": f"/v2/radar/{minutes}", "tile_url": f"http://tiles/{minutes}.png"}
              for minutes in (120, 70, 50, 40, 30, 20, 10, 0)]

    async def fake_fetch_maps(client=None):
        return frames

    async def fake_fetch_tile(url, client=None):
        return make_png(90, 112)

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)
    monkeypatch.setattr(rv, "fetch_tile_bytes", fake_fetch_tile)

    result = asyncio.run(svc.get_radar_nowcast())
    assert [f["time"] for f in result["past_frames"]] == [LATEST_FRAME - m * 60 for m in (50, 40, 30, 20, 10, 0)]


def test_slow_qwen_answers_with_template_then_fills_cache(monkeypatch):
    """The radar does not wait for a slow Qwen; its validated text replaces the template for later readers."""
    import threading
    import api_backend.hazard_service as hs
    from decision_engine.nowcast_explainer import NowcastExplainer

    svc = _bare_service()
    monkeypatch.setattr(hs, "NOWCAST_EXPLAIN_WAIT_S", 0.05)

    release = threading.Event()

    def slow_explain(self, summary, gazetteer=None):
        assert release.wait(timeout=5), "test did not release the explainer"
        return "ข้อความจาก Qwen", "qwen"

    monkeypatch.setattr(NowcastExplainer, "explain_with_source", slow_explain)

    async def fake_fetch_maps(client=None):
        return [
            {"time": LATEST_FRAME - 600, "path": "/v2/radar/frame1", "tile_url": "http://tiles/1.png"},
            {"time": LATEST_FRAME, "path": "/v2/radar/frame2", "tile_url": "http://tiles/2.png"},
        ]

    async def fake_fetch_tile(url, client=None):
        return make_png(90, 112) if "frame1" in url else make_png(91, 113)

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch_maps)
    monkeypatch.setattr(rv, "fetch_tile_bytes", fake_fetch_tile)

    async def run():
        try:
            first = await svc.get_radar_nowcast()
            assert first["explanation_source"] == "template" and first["explanation_pending"] is True
        finally:
            release.set()

        async def wait_for_cache():
            while svc._radar_nowcast_cache[1]["explanation_source"] != "qwen":
                await asyncio.sleep(0)  # Yield until the completion callback updates the cache.
        await asyncio.wait_for(wait_for_cache(), timeout=5)
        return first, await svc.get_radar_nowcast()

    first, later = asyncio.run(run())
    assert first["explanation_source"] == "template" and first["explanation"] != "ข้อความจาก Qwen"
    assert later["explanation_source"] == "qwen" and later["explanation"] == "ข้อความจาก Qwen"
    assert later["explanation_pending"] is False
    assert later["forecast_frames"] == first["forecast_frames"]
