from datetime import datetime, timezone
import threading

from api_backend.hazard_service import BANGKOK, HazardService


def test_agent_data_tools_expose_current_records_and_hotspot_counts():
    service = HazardService.__new__(HazardService)
    service.quakes = [{"magnitude": 3.1}]
    service.storms = [{"name": "TEST"}]
    service.wave_points = [{"wave_height_max_24h": 2.0}]
    service.water_quality = [{"salinity_gl": 0.4}]
    service.pm25_stations = [{"pm25": 20.0}]
    service.media = {"radar": [{"image": "https://example.test/radar.jpg"}]}
    service.updated_at = datetime(2026, 11, 4, tzinfo=timezone.utc)
    service.decisions = {
        "TH5001": [{"hazard": "fire", "observations": {"hotspots_24h": 3}}],
        "TH5002": [{"hazard": "fire", "observations": {"hotspots_24h": 2}}],
        "TH1406": [{"hazard": "flood", "observations": {"hotspots_24h": 99}}],
    }

    assert service.get_earthquakes() == service.quakes
    assert service.get_storms() == service.storms
    assert service.get_waves() == service.wave_points
    assert service.get_water_quality() == service.water_quality
    assert service.get_pm25_stations() == service.pm25_stations
    assert service.get_fire_hotspots() == [
        {"area_code": "TH5001", "hotspots_24h": 3, "time": service.updated_at.isoformat()},
        {"area_code": "TH5002", "hotspots_24h": 2, "time": service.updated_at.isoformat()},
    ]
    assert service.get_media()["source"] == "คลังข้อมูลน้ำแห่งชาติ (สสน.)"


def test_sync_dam_history_tool_returns_cached_history():
    service = HazardService.__new__(HazardService)
    service._agent_history_lock = threading.Lock()
    service.dams = [{"id": "123"}]
    value = {"series": [{"year": 2026, "values": [["2026-11-03", 50]]}]}
    service._graph_cache = {
        ("dam", "123", "storage", datetime.now(BANGKOK).year): (datetime.now(timezone.utc), value)
    }
    assert service.get_dam_history("123") == value


def test_cyclone_tool_preserves_wind_band_threshold_semantics():
    service = HazardService.__new__(HazardService)
    service.cyclone_overlay = lambda: {"features": [
        {"properties": {"kind": "wind_area", "event_id": "EV1", "name": "TEST", "event": "TC", "wind_kmh": 64}},
        {"properties": {"kind": "wind_area", "event_id": "EV1", "name": "TEST", "event": "TC", "wind_kmh": 93}},
        {"properties": {"kind": "track", "event_id": "EV1", "name": "TEST", "event": "TC"}},
        {"properties": {"kind": "track", "event_id": "EV2", "name": "TRACK ONLY", "event": "TC"}},
    ]}
    service.cyclone_updated_at = datetime(2026, 11, 4, tzinfo=timezone.utc)

    assert service.get_cyclones() == [
        {"event_id": "EV1", "name": "TEST", "event": "TC", "wind_threshold_kmh": 93, "time": "2026-11-04T00:00:00+00:00"},
        {"event_id": "EV2", "name": "TRACK ONLY", "event": "TC", "wind_threshold_kmh": None, "time": "2026-11-04T00:00:00+00:00"},
    ]
