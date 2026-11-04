"""Exercise the HTTP route, agent and real service tools without boundary files or providers."""
from datetime import datetime, timedelta, timezone
import threading

import httpx
import pytest

import api_backend.main as main
from api_backend.hazard_service import BANGKOK, HazardService
from decision_engine.explainer import QwenConfig, QwenExplainer
from geo_integration import Area, Gazetteer
from hazard_agent import HazardAgent


@pytest.mark.asyncio
@pytest.mark.parametrize("question,intent,expected", [
    ("แผ่นดินไหวล่าสุดเกิดที่ไหน", "earthquake", "4.2"),
    ("ตอนนี้มีพายุอะไรบ้าง", "storms", "TEST-STORM"),
    ("คลื่นสูงสุดวันนี้เท่าไหร่", "waves", "3.2 เมตร"),
    ("สถานีไหนมีความเค็มสูงสุด", "water_quality", "0.8"),
    ("สถานี PM2.5 สูงสุดตอนนี้เท่าไหร่", "pm25_stations", "45.6 มคก./ลบ.ม."),
    ("ภาคเหนือมีจุดความร้อนไฟป่ากี่จุด", "fire_hotspots", "7 จุด"),
    ("เขื่อนภูมิพลเทียบกับสัปดาห์ก่อนเป็นอย่างไร", "dam_history", "40"),
    ("ขอลิงก์ภาพเรดาร์ล่าสุด", "media", "https://example.test/radar.jpg"),
])
async def test_ask_data_tools_http(monkeypatch, question, intent, expected):
    now = datetime.now(timezone.utc)
    today = now.astimezone(BANGKOK).date()
    service = HazardService.__new__(HazardService)
    service.gazetteer = Gazetteer([
        Area("TH50", "province", "เชียงใหม่", "Chiang Mai", None, 18.8, 98.9, 20000),
        Area("TH5001", "amphoe", "เมืองเชียงใหม่", "Mueang Chiang Mai", "TH50", 18.8, 98.9, 1000),
    ])
    service.source_status = {}
    service.updated_at = now
    service.satellite_updated_at = service.forecast_updated_at = service.fire_updated_at = now
    service.grid_updated_at = {}
    service._jobs = {}
    service._built_with = {"gistda_flood", "tmd_nwp_daily", "fire_model"}  # the decisions above include them
    service.quakes = [{"magnitude": 4.2, "place_th": "เชียงใหม่", "time": now.isoformat(), "source": "USGS"}]
    service.storms = [{"name": "TEST-STORM", "wind_kmh": 90, "time": now.isoformat()}]
    service.cyclone_overlay = lambda: {"features": []}
    service.cyclone_updated_at = now
    service.wave_points = [{"height_m": 3.2, "lat": 9.5, "lon": 101.0, "peak_time": now.isoformat()}]
    service.water_quality = [{"name_th": "สถานีทดสอบ", "salinity_gl": 0.8, "time": now.isoformat()}]
    service.pm25_stations = [{"name_th": "สถานีทดสอบ", "pm25": 45.6, "time": now.isoformat()}]
    service.decisions = {"TH5001": [{"hazard": "fire", "observations": {"hotspots_24h": 7}}]}
    service.media = {"radar": [{"image": "https://example.test/radar.jpg", "time": now.isoformat()}]}
    service.dams = [{"id": "1", "name_th": "ภูมิพล", "date": today.isoformat()}]
    service._agent_history_lock = threading.Lock()
    service._graph_cache = {
        ("dam", "1", "storage", today.year): (now, {"series": [{"year": today.year, "values": [
            [(today - timedelta(days=7)).isoformat(), 40], [today.isoformat(), 50],
        ]}], "source": "ThaiWater"})
    }

    async def fresh_service():
        return service

    # An LLM that invents a number: validate() must reject it, so the answer keeps the real records.
    inventing = QwenExplainer(QwenConfig(), requester=lambda q, facts: "ค่าล่าสุด 999 ครับ")

    monkeypatch.setattr(main, "fresh_service", fresh_service)
    monkeypatch.setattr(main, "agent", HazardAgent(service.gazetteer, service, explainer=inventing))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.post("/v1/ask", json={"question": question})
    assert response.status_code == 200
    answer = response.json()
    assert answer["intent"] == intent
    assert expected in answer["text"] and "999" not in answer["text"]
    assert answer["llm_used"] is False
    assert answer["evidence"] and answer["sources"]
    assert answer["data_updated_at"] == now.isoformat()
    assert response.headers["x-request-id"]
