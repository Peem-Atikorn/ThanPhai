from datetime import datetime, timedelta, timezone

from thanphai_shared import SourceRecord

from api_backend.hazard_service import HazardService


def test_cached_tmd_quakes_stay_in_the_list_when_tmd_is_down():
    service = object.__new__(HazardService)
    service._tmd_quakes, service._tmd_last_ok, service._tmd_gaps = {}, None, []
    service._quake_file = None
    service.amphoe_codes = []
    now = datetime.now(timezone.utc)
    quake = SourceRecord(source="tmd_earthquakes", variable="earthquake", value=2.3, unit="magnitude", lat=19.1, lon=100.3,
                         station_id="tmd:kept", observed_at=now - timedelta(days=4), fetched_at=now, attribution="test",
                         extra={"depth_km": 2.0, "place_th": "ต.ปง อ.ปง จ.พะเยา"})

    service._quake_shaking([quake], [])
    service._quake_shaking(None, [])  # TMD is down, but USGS is still responding.

    assert [item["id"] for item in service._last_quakes] == ["tmd:kept"]


def test_a_feed_that_no_longer_reaches_the_last_answer_is_saved_as_a_gap(tmp_path):
    service = object.__new__(HazardService)
    service._tmd_quakes, service._tmd_last_ok, service._tmd_gaps = {}, None, []
    service._quake_file = tmp_path / "tmd.json"
    service.amphoe_codes = []
    now = datetime.now(timezone.utc)

    def quake(id_, hours_ago):
        return SourceRecord(source="tmd_earthquakes", variable="earthquake", value=2.3, unit="magnitude", lat=19.1,
                            lon=100.3, station_id=f"tmd:{id_}", observed_at=now - timedelta(hours=hours_ago),
                            fetched_at=now, attribution="test", extra={})

    service._remember_tmd_quakes([quake("a", 50)], now - timedelta(hours=48))
    service._remember_tmd_quakes([quake("b", 30), quake("a", 50)], now - timedelta(hours=40))  # overlaps: no gap
    assert service._tmd_gaps == []
    service._remember_tmd_quakes([quake("c", 2)], now)  # down from -40 h; the feed only reaches -2 h
    assert service._tmd_gaps == [(now - timedelta(hours=40), now - timedelta(hours=2))]

    loaded = object.__new__(HazardService)
    loaded._tmd_quakes, loaded._tmd_last_ok, loaded._tmd_gaps = {}, None, []
    loaded._quake_file = service._quake_file
    loaded._load_quake_history()
    assert loaded._tmd_gaps == service._tmd_gaps and loaded._tmd_last_ok == now
    assert loaded.get_quake_gaps()[0]["end"] == (now - timedelta(hours=2)).isoformat()
