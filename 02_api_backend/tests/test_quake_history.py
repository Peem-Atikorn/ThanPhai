from datetime import datetime, timedelta, timezone

from thanphai_shared import SourceRecord

from api_backend.hazard_service import HazardService


def test_cached_tmd_quakes_stay_in_the_list_when_tmd_is_down():
    service = object.__new__(HazardService)
    service._tmd_quakes = {}
    service._quake_file = None
    service.amphoe_codes = []
    now = datetime.now(timezone.utc)
    quake = SourceRecord(source="tmd_earthquakes", variable="earthquake", value=2.3, unit="magnitude", lat=19.1, lon=100.3,
                         station_id="tmd:kept", observed_at=now - timedelta(days=4), fetched_at=now, attribution="test",
                         extra={"depth_km": 2.0, "place_th": "ต.ปง อ.ปง จ.พะเยา"})

    service._quake_shaking([quake], [])
    service._quake_shaking(None, [])  # TMD is down, but USGS is still responding.

    assert [item["id"] for item in service._last_quakes] == ["tmd:kept"]
