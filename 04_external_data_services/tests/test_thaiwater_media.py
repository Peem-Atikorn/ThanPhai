import json
from datetime import datetime, timezone
from pathlib import Path

from external_data.adapters.thaiwater import ThaiWaterWaterLevel, river_flow, station_code
from external_data.adapters.thaiwater_graphs import parse_dam_year, parse_station_graph
from external_data.adapters.thaiwater_media import MEDIA_URL, parse_media, radar_name

FIXTURES = Path(__file__).parent / "fixtures"
MEDIA = json.loads((FIXTURES / "tw_thailand_main_media.json").read_text(encoding="utf-8"))
RIVERS = json.loads((FIXTURES / "tw_waterlevel_rivers.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 19, 11, 40, tzinfo=timezone.utc)  # 18:40 Thai time, when the fixture was taken


def test_stale_radars_are_dropped_and_times_are_thai():
    media = parse_media(MEDIA, NOW)
    names = [r["name_th"] for r in media["radar"]]
    # phb240's last image is from 2017; it must not be shown as the latest radar.
    assert not any(n.startswith("เพชรบูรณ์") for n in names)
    assert "อุบลราชธานี (รัศมี 120 กม.)" in names and "ตาคลี (นครสวรรค์)" in names
    ubon = next(r for r in media["radar"] if r["id"] == "ubn120")
    assert ubon["time"] == "2026-10-19T18:10:00+07:00"  # stamped 11:10 UTC
    njk = next(r for r in media["radar"] if r["id"] == "njk")
    # BMA rows claim Thai time ("TST"), but the image is stamped 17:55 Thai time: the value is UTC.
    assert njk["time"] == "2026-10-19T17:55:00+07:00"
    assert all(r["image"].startswith(MEDIA_URL) for r in media["radar"])


def test_forecast_storm_and_wave_images():
    media = parse_media(MEDIA, NOW)
    thailand = media["rain_forecast"]["thailand"]
    assert thailand["label_th"] == "ประเทศไทย" and [d["date"] for d in thailand["days"]] == ["2026-10-19", "2026-10-20", "2026-10-21"]
    assert len(media["rain_forecast"]["sea"]["days"]) == 2
    regions = {s["region_th"] for s in media["storm"]}
    assert "มหาสมุทรแปซิฟิกตะวันตก" in regions
    assert all(s["source"] != "us" for s in media["storm"])  # the US Navy image in the bundle is from 2020
    assert len(media["wave"]) == 7


def test_forecast_older_than_two_days_is_hidden():
    media = parse_media(MEDIA, datetime(2026, 10, 30, tzinfo=timezone.utc))
    assert media["rain_forecast"]["thailand"]["days"] == [] and media["wave"] == [] and media["radar"] == []


def test_media_handles_missing_sections():
    assert parse_media({}, NOW) == {"radar": [], "rain_forecast": {k: {"label_th": v, "days": []} for k, v in
                                                                       {"thailand": "ประเทศไทย", "basin": "รายลุ่มน้ำ", "sea": "เอเชียตะวันออกเฉียงใต้", "asia": "เอเชีย"}.items()},
                                    "storm": [], "wave": []}


def test_radar_name():
    assert radar_name("svp120") == "สุวรรณภูมิ (รัศมี 120 กม.)"
    assert radar_name("xyz") == "xyz"


def test_river_fields_and_duplicate_stations():
    records = ThaiWaterWaterLevel().parse(RIVERS, NOW)
    c13 = [r for r in records if r.extra["station_code"] == "C.13"]
    assert len(c13) == 2  # "C.13" and "ridhydro_C.13" are the same station
    # One row has the flow in `discharge`, the other in `flow_rate`; both must read 1,800 m³/s.
    assert {r.extra["discharge_m3s"] for r in c13} == {1800.0}
    key = next(r for r in c13 if r.extra["is_key_station"])
    assert key.extra["river_name"] == "แม่น้ำเจ้าพระยา" and key.extra["qmax_m3s"] == 2720


def test_station_code_and_flow_helpers():
    assert station_code("ridhydro_C.13") == "C.13" and station_code("K.35A") == "K.35A" and station_code(None) is None
    assert river_flow({"discharge": "0.00", "flow_rate": None}) is None  # 0 = no flow gauge
    assert river_flow({"discharge": None, "flow_rate": "954.00"}) == 954.0


def test_parse_station_graph_keeps_measured_hours():
    raw = {"data": {"graph_data": [{"datetime": "2026-10-19 00:00", "value": None, "discharge": None},
                                   {"datetime": "2026-10-19 01:00", "value": 14.05, "discharge": 1800}],
                    "min_bank": 16.34, "ground_level": 2.08, "qmax": 2720}}
    graph = parse_station_graph(raw)
    assert graph["points"] == [{"time": "2026-10-19 01:00", "level_msl": 14.05, "discharge_m3s": 1800.0}]
    assert graph["bank_msl"] == 16.34 and graph["qmax_m3s"] == 2720


def test_parse_dam_year_reads_string_rule_curves():
    raw = {"data": {"graph_data": [{"year": 2026, "data": [{"date": "2026-01-01T00:00:00+07:00", "value": 10.5},
                                                           {"date": "2026-12-31T00:00:00+07:00", "value": None}]}],
                    # Seen live: rule curves arrive as the repr of a Python list.
                    "upper_rule_curve": "[{'date': '2020-01-01', 'value': 163.76}]",
                    "lower_rule_curve": [{"date": "2020-01-01", "value": 70.59}],
                    "normal_bound": "164", "upper_bound": "205.85", "lower_bound": "13.5"}}
    year = parse_dam_year(raw)
    assert year["series"] == [{"year": 2026, "values": [["2026-01-01", 10.5]]}]
    assert year["upper_rule_curve"] == [["01-01", 163.76]] and year["lower_rule_curve"] == [["01-01", 70.59]]
    assert year["normal_storage_mcm"] == 164
