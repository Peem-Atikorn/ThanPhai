"""Offline parser tests against real responses saved on 2026-10-19."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from external_data.adapters import (
    FirmsHotspots,
    OpenMeteoForecast,
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
    assert classify_hazards("ไฟป่าและหมอกควันภาคเหนือ") == [HazardType.FIRE]
    assert classify_hazards("พายุโซนร้อน") == [HazardType.RAIN_STORM]
    assert classify_hazards("อากาศหนาว") == []
