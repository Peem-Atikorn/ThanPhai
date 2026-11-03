import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api_backend.hazard_service import HazardService

DATA = Path(__file__).resolve().parents[2] / "data" / "processed"


def test_hazard_service_get_radar_nowcast(monkeypatch):
    svc = HazardService.__new__(HazardService)
    svc.decisions = {}
    svc.amphoe_codes = []
    
    # Mock gazetteer
    class FakeGazetteer:
        by_code = {}
    svc.gazetteer = FakeGazetteer()

    fake_frames = [
        {"time": 1793722200, "path": "/v2/radar/0c933e4ed2a3", "tile_url": "http://tiles/1.png"},
        {"time": 1793722800, "path": "/v2/radar/c35c42546b19", "tile_url": "http://tiles/2.png"},
        {"time": 1793723400, "path": "/v2/radar/320227828fee", "tile_url": "http://tiles/3.png"},
    ]

    async def fake_fetch(client=None):
        return fake_frames

    import external_data.adapters.rainviewer as rv
    monkeypatch.setattr(rv, "fetch_radar_maps", fake_fetch)

    result = asyncio.run(svc.get_radar_nowcast())

    assert "past_frames" in result
    assert "forecast_frames" in result
    assert "explanation" in result
    assert "rain_cells" in result
    assert "motion" in result

    assert len(result["past_frames"]) == 3
    assert len(result["forecast_frames"]) == 6
    assert result["forecast_frames"][0]["offset_minutes"] == 10
    assert result["forecast_frames"][5]["offset_minutes"] == 60
    assert isinstance(result["explanation"], str)
    assert len(result["explanation"]) > 0
    assert "RainViewer" in result["source"]
