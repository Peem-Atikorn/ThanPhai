from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from geo_integration.area_mapper import AreaMapper
from geo_integration.features import fire_features, flood_features, pm25_features, rain_features
from thanphai_shared import SourceRecord

REAL_GPKG = Path(__file__).resolve().parents[2] / "data" / "processed" / "admin_areas.gpkg"
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)

# Two square tambons side by side near Bangkok (each ~0.1° wide).
TAMBON = gpd.GeoDataFrame(
    {"code": ["TH100101", "TH100201"], "parent_code": ["TH1001", "TH1002"]},
    geometry=[box(100.0, 13.0, 100.1, 13.1), box(100.1, 13.0, 100.2, 13.1)],
    crs="EPSG:4326",
)
mapper = AreaMapper(TAMBON)


def test_points_inside_polygons():
    out = mapper.assign_points([13.05, 13.05], [100.05, 100.15])
    assert list(out["tambon_code"]) == ["TH100101", "TH100201"]
    assert list(out["province_code"]) == ["TH10", "TH10"]
    assert set(out["match"]) == {"within"}


def test_point_just_outside_uses_nearest():
    # ~1.1 km east of the last polygon.
    out = mapper.assign_points([13.05], [100.21])
    assert out.loc[0, "tambon_code"] == "TH100201" and out.loc[0, "match"] == "nearest"


def test_point_far_away_is_outside():
    out = mapper.assign_points([18.0], [99.0])
    assert pd.isna(out.loc[0, "tambon_code"]) and out.loc[0, "match"] == "outside"


def test_order_is_preserved():
    lats, lons = [13.05, 18.0, 13.05], [100.15, 99.0, 100.05]
    assert list(mapper.assign_points(lats, lons)["idx"]) == [0, 1, 2]


def record(variable, value, lat, lon, station="s1", **extra):
    return SourceRecord(source="t", variable=variable, value=value, unit="x", lat=lat, lon=lon,
                        station_id=station, fetched_at=NOW, attribution="t", extra=extra)


def test_assign_records_keeps_records_without_location():
    rows = mapper.assign_records([record("rain_24h", 10, 13.05, 100.05), record("rain_24h", 5, None, None)])
    assert list(rows["match"]) == ["within", "no_location"]


def test_provider_tambon_code_wins_over_polygon():
    # Point lies in TH100101 but the station is registered in TH100201 (border river case).
    rows = mapper.assign_records([record("waterlevel_msl", 1.0, 13.05, 100.05, tambon_code="TH100201")])
    assert rows.loc[0, "tambon_code"] == "TH100201" and rows.loc[0, "match"] == "provider"


def test_unknown_provider_code_falls_back_to_polygon():
    rows = mapper.assign_records([record("waterlevel_msl", 1.0, 13.05, 100.05, tambon_code="TH999999")])
    assert rows.loc[0, "tambon_code"] == "TH100101" and rows.loc[0, "match"] == "within"


def test_flood_features_take_worst_station_and_ignore_unknown_bank():
    rows = mapper.assign_records([
        record("waterlevel_msl", 3.0, 13.05, 100.05, "a", above_bank_m=-0.5),
        record("waterlevel_msl", 4.0, 13.06, 100.06, "b", above_bank_m=0.4),
        record("waterlevel_msl", 331.0, 13.07, 100.07, "c", above_bank_m=None),
    ])
    f = flood_features(rows).set_index("area_code")
    assert f.loc["TH1001", "above_bank_m"] == pytest.approx(0.4)
    assert f.loc["TH1001", "stations"] == 3 and f.loc["TH1001", "stations_with_bank"] == 2


def test_rain_features():
    rows = mapper.assign_records([record("rain_24h", 40, 13.05, 100.05, "a"), record("rain_24h", 100, 13.05, 100.06, "b")])
    f = rain_features(rows).set_index("area_code")
    assert f.loc["TH1001", "rain_24h"] == 100 and f.loc["TH1001", "rain_24h_mean"] == 70


def test_fire_features_fill_zero_for_quiet_areas():
    rows = mapper.assign_records([record("fire_hotspot", 5.0, 13.05, 100.05), record("fire_hotspot", 7.0, 13.06, 100.05)])
    f = fire_features(rows, ["TH1001", "TH1002"]).set_index("area_code")
    assert f.loc["TH1001", "hotspots_24h"] == 2 and f.loc["TH1002", "hotspots_24h"] == 0


def test_zero_records_still_build_features():
    # Seen live on 2026-10-19: FIRMS had no hotspots over Thailand, and the empty frame crashed the refresh.
    rows = mapper.assign_records([])
    assert {"amphoe_code", "value", "station_id"} <= set(rows.columns)
    fire = fire_features(rows, ["TH1001", "TH1002"]).set_index("area_code")
    assert list(fire["hotspots_24h"]) == [0, 0]
    assert flood_features(rows).empty and rain_features(rows).empty


@pytest.mark.skipif(not REAL_GPKG.exists(), reason="run scripts/build_admin_areas.py first")
def test_real_boundaries_known_points():
    real = AreaMapper.from_gpkg(REAL_GPKG)
    # Suphan Buri water station TTC06 (Song Phi Nong) and Chiang Mai old city.
    out = real.assign_points([14.15657, 18.7883], [100.12744, 98.9853])
    assert out.loc[0, "province_code"] == "TH72"
    assert out.loc[1, "province_code"] == "TH50"


def test_pm25_features_take_worst_station():
    rows = mapper.assign_records([record("pm25_24h", 20.0, 13.05, 100.05, "a"), record("pm25_24h", 41.5, 13.06, 100.05, "b")])
    f = pm25_features(rows).set_index("area_code")
    assert f.loc["TH1001", "pm25_24h"] == 41.5 and f.loc["TH1001", "stations"] == 2
    assert pm25_features(mapper.assign_records([])).empty
