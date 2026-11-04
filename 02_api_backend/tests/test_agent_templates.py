from datetime import datetime, timezone

from fastapi.testclient import TestClient

import api_backend.main as main
from api_backend.hazard_service import HazardService
from geo_integration import Gazetteer
from hazard_agent import HazardAgent


def test_ask_source_list_matches_sources_endpoint(monkeypatch):
    # Exercise source reporting without loading boundary files or fetching live observations.
    service = object.__new__(HazardService)
    service.source_status = {"thaiwater_waterlevel": {"ok": True}, "new_provider": {"ok": False}}
    service.updated_at = datetime.now(timezone.utc)
    service.satellite_updated_at = service.forecast_updated_at = service.fire_updated_at = service.updated_at
    service.grid_updated_at = {}
    service._jobs = {}
    service._built_with = {"gistda_flood", "tmd_nwp_daily", "fire_model"}

    async def fresh_service():
        return service

    monkeypatch.setattr(main, "fresh_service", fresh_service)
    monkeypatch.setattr(main, "agent", HazardAgent(Gazetteer([]), service))
    client = TestClient(main.app)
    sources_response = client.get("/v1/sources")
    answer_response = client.post("/v1/ask", json={"question": "ทันภัยใช้ข้อมูลจากไหน"})
    assert sources_response.status_code == answer_response.status_code == 200
    sources = sources_response.json()["sources"]
    answer = answer_response.json()
    assert answer["intent"] == "sources"
    assert answer["sources"] == []  # Do not repeat the provider list below the answer in the web chat.
    assert {e["source"]: e["ok"] for e in answer["evidence"]} == {name: state["ok"] for name, state in sources.items()}
    assert answer["degraded_services"] == ["new_provider"]
