"""Offline parser tests against real responses saved on 2026-10-19."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from external_data.adapters import (
    Air4ThaiPm25,
    FirmsHotspots,
    OpenMeteoAirQuality,
    OpenMeteoForecast,
    OpenMeteoHeatIndex,
    OpenMeteoRiverDischarge,
    ThaiWaterRain24h,
    ThaiWaterWaterLevel,
    TmdWarnings,
    TmdWeather3Hours,
)
from external_data.adapters.base import BANGKOK, in_thailand_bbox
from external_data.adapters.thaiwater import bank_quality_flag, signed_bank_diff
from external_data.adapters.tmd import as_list, classify_hazards
from thanphai_shared import HazardType

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_open_meteo_forecast_parses_every_hour_and_variable():
    raw = load_json("om_forecast.json")
    records = OpenMeteoForecast(18.79, 98.98).parse(raw, NOW)
    assert len(records) == len(raw["hourly"]["time"]) * 3
    first = records[0]
    assert first.valid_time.tzinfo == BANGKOK
    assert first.issued_at == NOW and first.observed_at is None
    assert {r.variable for r in records} == {"precipitation", "relative_humidity_2m", "wind_speed_10m"}


def test_open_meteo_river_discharge_uses_utc_dates():
    raw = load_json("om_flood.json")
    records = OpenMeteoRiverDischarge(15.70, 100.13).parse(raw, NOW)
    assert len(records) == len(raw["daily"]["time"])
    assert records[0].unit == "m3/s"
    assert records[0].valid_time.utcoffset().total_seconds() == 0


def test_thaiwater_rain_has_location_and_local_time():
    records = ThaiWaterRain24h().parse(load_json("tw_rain_24h.json"), NOW)
    assert len(records) == 5
    r = records[0]
    assert r.unit == "mm" and r.value is not None
    assert r.lat and r.lon and r.province_code
    assert r.observed_at.tzinfo == BANGKOK


def test_thaiwater_waterlevel_rebuilds_bank_sign():
    records = ThaiWaterWaterLevel().parse(load_json("tw_waterlevel_load.json"), NOW)
    assert len(records) == 5
    first = records[0]
    # First fixture station (Suphan Buri) is overflowing: 3.35 m MSL vs min bank 2.14 m.
    assert first.extra["above_bank_m"] == pytest.approx(1.21)
    assert first.extra["situation_level"] == 5
    # Song Phi Nong (07), Bang Ta Then (03), Suphan Buri (72).
    assert first.extra["tambon_code"] == "TH720703"


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"diff_wl_bank": "1.21", "diff_wl_bank_text": "ล้นตลิ่ง (ม.)", "station": {"min_bank": 2.14}}, 1.21),
        ({"diff_wl_bank": "0.80", "diff_wl_bank_text": "ต่ำกว่าตลิ่ง (ม.)", "station": {"min_bank": 9.0}}, -0.80),
        ({"diff_wl_bank": None, "waterlevel_msl": "5.0", "station": {"min_bank": 6.5}}, -1.5),
        ({"diff_wl_bank": None, "waterlevel_msl": None, "station": {"min_bank": 6.5}}, None),
        # Real row seen 2026-10-19: no bank configured, provider reports the MSL level as overflow.
        ({"diff_wl_bank": "331.76", "diff_wl_bank_text": "ล้นตลิ่ง (ม.)", "waterlevel_msl": "331.76", "station": {"min_bank": 0}}, None),
        ({"diff_wl_bank": "25.0", "diff_wl_bank_text": "ล้นตลิ่ง (ม.)", "station": {"min_bank": 3.0}}, None),
    ],
)
def test_signed_bank_diff(row, expected):
    assert signed_bank_diff(row) == (pytest.approx(expected) if expected is not None else None)


def test_bank_quality_flag():
    assert bank_quality_flag({"station": {"min_bank": 0}, "waterlevel_msl": "331.76"}) == "bank_unknown"
    assert bank_quality_flag({"station": {"min_bank": 3.0}, "diff_wl_bank": "25", "diff_wl_bank_text": "ล้นตลิ่ง", "waterlevel_msl": "28"}) == "implausible"
    assert bank_quality_flag({"station": {"min_bank": 2.14}, "diff_wl_bank": "1.21", "diff_wl_bank_text": "ล้นตลิ่ง"}) == "ok"


def test_firms_keeps_only_thailand_bbox_and_uses_utc():
    raw = (FIXTURES / "firms_sea_24h.csv").read_text(encoding="utf-8")
    records = FirmsHotspots(map_key="").parse(raw, NOW)
    # Fixture holds 6 hotspots inside the bbox and 3 outside.
    assert len(records) == 6
    assert all(in_thailand_bbox(r.lat, r.lon) for r in records)
    assert records[0].observed_at.tzinfo == timezone.utc


def test_firms_url_switches_to_area_api_with_key():
    assert "SouthEast_Asia_24h" in FirmsHotspots(map_key="").url
    assert "/api/area/csv/abc/VIIRS_SNPP_NRT/" in FirmsHotspots(map_key="abc", window="7d").url
    with pytest.raises(ValueError):
        FirmsHotspots(window="3d")


def test_tmd_warning_v2_single_object():
    warnings = TmdWarnings().parse_warnings(load_json("tmd_warning_v2.json"), NOW)
    assert len(warnings) == 1
    w = warnings[0]
    assert "ฝนตกหนัก" in w.title
    assert HazardType.RAIN_STORM in w.hazards and HazardType.FLOOD in w.hazards
    assert w.effective_from < w.effective_to
    assert w.announced_at.tzinfo == BANGKOK


def test_tmd_warning_handles_list_and_empty():
    raw = load_json("tmd_warning_v2.json")
    single = raw["Warning"]
    assert len(TmdWarnings().parse_warnings({"Warning": [single, single]}, NOW)) == 2
    assert TmdWarnings().parse_warnings({"Warnings": {}}, NOW) == []
    assert as_list({}) == [] and as_list(None) == []


def test_tmd_weather_3h_parses_stations():
    records = TmdWeather3Hours().parse(load_json("tmd_weather3h.json"), NOW)
    assert len(records) == 4 * 5
    rain = [r for r in records if r.variable == "rain_24h"]
    assert rain[0].observed_at.tzinfo == BANGKOK and rain[0].station_id


def test_classify_hazards():
    assert classify_hazards("ไฟป่าและหมอกควันภาคเหนือ") == [HazardType.FIRE, HazardType.AIR_QUALITY]
    assert classify_hazards("พายุโซนร้อน") == [HazardType.RAIN_STORM]
    assert classify_hazards("อากาศหนาว") == []


def test_air4thai_skips_missing_pm25_and_uses_local_time():
    # Saved 2026-10-22: three current stations, one stuck since 2026-10-13, one without PM2.5 (-1).
    records = Air4ThaiPm25().parse(load_json("air4thai_aqi.json"), NOW)
    assert [r.station_id for r in records] == ["119t", "52t", "53t", "81t"]
    first = records[0]
    assert first.variable == "pm25_24h" and first.value == 9.0 and first.unit == "µg/m³"
    assert first.observed_at == datetime(2026, 10, 22, 3, 0, tzinfo=BANGKOK)
    assert (first.lat, first.lon) == (8.0506237, 98.9180489)
    assert records[3].observed_at == datetime(2026, 10, 13, 19, 0, tzinfo=BANGKOK)  # dropped later as stale, not here


def test_open_meteo_air_quality_averages_next_24_hours():
    raw = load_json("om_air_quality.json")
    points = [("TH5001", 18.7883, 98.9853), ("TH1001", 13.7563, 100.5018)]
    adapter = OpenMeteoAirQuality(points)
    fetched = datetime(2026, 10, 22, 3, 20, tzinfo=BANGKOK)
    records = adapter.parse([raw], fetched)
    assert [r.extra["area_code"] for r in records] == ["TH5001", "TH1001"]
    hourly = raw[0]["hourly"]
    expected = [v for v in hourly["pm2_5"][3:27] if v is not None]  # 03:00 today to 02:00 tomorrow
    assert records[0].value == round(sum(expected) / len(expected), 1)
    assert records[0].extra["max_1h"] == max(expected)
    assert records[0].variable == "pm25_model_24h" and records[0].lat == 18.7883


def test_open_meteo_air_quality_single_point_object():
    raw = load_json("om_air_quality.json")[1]
    # Two districts in the same 0.4° CAMS cell share one requested point.
    adapter = OpenMeteoAirQuality([("TH1001", 13.7563, 100.5018), ("TH1002", 13.80, 100.55)])
    assert adapter.cells == [(13.6, 100.4)]
    records = adapter.parse([[raw]], datetime(2026, 10, 22, 3, 0, tzinfo=BANGKOK))
    assert [r.extra["area_code"] for r in records] == ["TH1001", "TH1002"] and records[0].value == records[1].value


@pytest.mark.parametrize(("temp", "rh", "expected"), [(26, 50, 26.0), (32, 70, 40.4), (35, 60, 45.1), (40, 60, 62.6)])
def test_heat_index_matches_noaa(temp, rh, expected):
    from external_data.adapters.open_meteo import heat_index_c

    assert heat_index_c(temp, rh) == pytest.approx(expected, abs=0.1)


def test_open_meteo_heat_takes_peak_of_next_24_hours():
    from external_data.adapters.open_meteo import heat_index_c

    raw = load_json("om_heat.json")
    adapter = OpenMeteoHeatIndex([("TH1001", 13.6, 100.4), ("TH5001", 18.8, 99.2)])
    records = adapter.parse([raw], datetime(2026, 10, 22, 0, 0, tzinfo=BANGKOK))
    h = raw[0]["hourly"]
    expected = max(heat_index_c(t, rh) for t, rh in zip(h["temperature_2m"][:24], h["relative_humidity_2m"][:24]))
    assert records[0].variable == "heat_index_max_24h" and records[0].value == round(expected, 1)
    assert records[0].extra["peak_time"].startswith("2026-10-22T") and records[0].extra["area_code"] == "TH1001"


def test_tmd_earthquakes_rss():
    from external_data.adapters.earthquakes import TmdEarthquakes

    raw = (FIXTURES / "tmd_earthquakes.xml").read_text(encoding="utf-8")
    records = TmdEarthquakes().parse(raw, NOW)
    assert len(records) == 3
    first = records[0]
    assert first.variable == "earthquake" and first.value == 3.3 and (first.lat, first.lon) == (14.936, 107.593)
    assert first.observed_at == datetime(2026, 10, 21, 13, 57, 50, tzinfo=timezone.utc)
    assert first.extra["depth_km"] == 10 and first.extra["place_th"].startswith("ประเทศเวียดนาม")
    assert "โขงเจียม" in first.extra["where_th"] and first.station_id == "tmd:17270"


def test_usgs_earthquakes_geojson():
    from external_data.adapters.earthquakes import UsgsEarthquakes

    records = UsgsEarthquakes().parse(load_json("usgs_earthquakes.json"), NOW)
    assert [r.value for r in records] == [5.0, 4.6, 4.8, 4.9]
    assert records[0].extra["depth_km"] == pytest.approx(75.876) and records[0].station_id.startswith("usgs:")
    assert records[0].observed_at.tzinfo is not None


def test_merge_quakes_prefers_tmd_for_the_same_event():
    from external_data.adapters.earthquakes import merge_quakes
    from thanphai_shared import SourceRecord

    def quake(source, minute, lat, lon):
        return SourceRecord(source=source, variable="earthquake", value=5.0, unit="magnitude", lat=lat, lon=lon,
                            observed_at=datetime(2026, 10, 21, 10, minute, tzinfo=timezone.utc), fetched_at=NOW, attribution="t")

    tmd = [quake("tmd", 0, 20.0, 99.0)]
    usgs = [quake("usgs", 0, 20.3, 99.2), quake("usgs", 30, 20.0, 99.0), quake("usgs", 0, 25.0, 99.0)]
    merged = merge_quakes(tmd, usgs)
    # Same time and 40 km apart = the same quake; 30 min later or 550 km away are different quakes.
    assert [r.source for r in merged] == ["tmd", "usgs", "usgs"] and merged[1].observed_at.minute == 30


def test_gdacs_keeps_south_east_asia_only():
    from external_data.adapters.gdacs import GdacsEvents

    raw = load_json("gdacs_events.json")
    records = GdacsEvents(days=60).parse(raw, datetime(2026, 10, 22, tzinfo=timezone.utc))
    assert [r.extra["event_type"] for r in records] == ["TC", "EQ", "EQ"]
    champi = records[0]
    assert champi.extra["name"] == "Tropical Cyclone CHAMPI-26" and champi.extra["alert"] == "Red"
    assert champi.extra["url"].startswith("https://www.gdacs.org/report.aspx")
    # A 7-day window drops events that ended earlier and are no longer current.
    later = GdacsEvents(days=7).parse(raw, datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert all(r.extra["current"] for r in later)


def test_open_meteo_marine_skips_land_cells():
    from external_data.adapters import OpenMeteoMarine

    raw = load_json("om_marine.json")  # off Phuket (sea) and central Bangkok (land: all null)
    adapter = OpenMeteoMarine([("TH8301", 7.9, 98.2), ("TH1001", 13.8, 100.5)])
    records = adapter.parse([raw], datetime(2026, 10, 22, 0, 0, tzinfo=BANGKOK))
    assert [r.extra["area_code"] for r in records] == ["TH8301"]
    waves = [v for v in raw[0]["hourly"]["wave_height"][:24] if v is not None]
    assert records[0].value == round(max(waves), 2) and records[0].unit == "m"


def test_gdacs_cyclone_wind_areas_and_track():
    from external_data.adapters.gdacs_cyclones import GdacsCyclones

    records = GdacsCyclones().parse([load_json("gdacs_cyclone_geometry.json")], NOW)
    wind = [r for r in records if r.variable == "cyclone_wind_area"]
    track = [r for r in records if r.variable == "cyclone_track_point"]
    assert sorted(r.value for r in wind) == [63.0, 93.0, 119.0]
    assert all(r.valid_time == datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc) for r in wind)
    assert wind[0].extra["geometry"]["type"] in ("Polygon", "MultiPolygon") and wind[0].extra["event"] == "CHAMPI-26"
    assert [r.extra["forecast"] for r in track] == [False, False, True]  # issued 21/10 18:00 UTC; the last point is 26/10
    assert track[0].valid_time == datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    assert 9 < track[0].lat < 10.5 and 160 < track[0].lon < 162  # centre of the small circle
