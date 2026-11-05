"""Water-level and dam-inflow datasets on small synthetic histories written in the downloader's format."""

import gzip
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from risk_models.water.dataset import RIVER_FEATURES, add_dam_climatology, dam_table, river_table, station_rain, station_series
from risk_models.water.model import river_scores, trend_forecast


def _gz(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f)


def _station(raw, sid, levels, start=datetime(2024, 1, 1), basin="A", bank=10.0):
    rows = [[(start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M"), v, None] for i, v in enumerate(levels)]
    _gz(raw / "rivers" / sid / "2024-01-01.json.gz", {"station_id": sid, "rows": rows})
    return {"id": sid, "basin_th": basin, "bank_msl": bank, "lat": 15.0, "lon": 100.0}


@pytest.fixture
def raw(tmp_path):
    hours = 24 * 70
    t = np.arange(hours)
    rising = 5 + 0.002 * t + 0.3 * np.sin(t / 24)
    stations = [_station(tmp_path, "1", rising.tolist()), _station(tmp_path, "2", (rising + 1).tolist())]
    (tmp_path / "stations.json").write_text(json.dumps(stations), encoding="utf-8")
    return tmp_path


def test_spike_is_dropped_and_short_gaps_filled(tmp_path):
    levels = [5.0] * 72
    levels[30] = 9.0  # one reading 4 m off that comes straight back
    _station(tmp_path, "9", levels)
    s = station_series(tmp_path / "rivers" / "9")
    assert s.max() == 5.0 and s.isna().sum() == 0


def test_bad_run_sentinel_and_datum_change(tmp_path):
    levels = [50.0] * 72 + [30.0] * 12 + [50.0] * 72 + [9999.9] + [50.0] * 30 + [20.0] * 72
    _station(tmp_path, "8", levels)
    s = station_series(tmp_path / "rivers" / "8")
    # The 12-hour run 20 m low and the 9999.9 marker are dropped; after the lasting 30 m step only the new datum stays.
    assert s.dropna().between(19.9, 20.1).all() and s.count() >= 60


def test_a_flash_flood_that_goes_down_is_water_not_a_datum_change(tmp_path):
    # Every 10 minutes: 4 m up within one reading, two days high, then down again. 24 readings are only 4 hours,
    # so the step must be judged on the week around it, which keeps the 30 days before.
    start = datetime(2024, 1, 1)
    levels = [5.0] * (6 * 24 * 30) + [9.0] * (6 * 48) + [5.0] * (6 * 24 * 10)
    rows = [[(start + timedelta(minutes=10 * i)).strftime("%Y-%m-%d %H:%M"), v, None] for i, v in enumerate(levels)]
    _gz(tmp_path / "rivers" / "6" / "2024-01-01.json.gz", {"station_id": "6", "rows": rows})
    s = station_series(tmp_path / "rivers" / "6")
    assert s.first_valid_index() == pd.Timestamp(start) and s.max() == pytest.approx(9.0)


def test_river_targets_are_future_change_and_basin_excludes_itself(raw):
    table = river_table(raw)
    assert set(RIVER_FEATURES) <= set(table.columns)
    one = table[table["station_id"] == "1"].set_index("time")
    level = one["level"]
    t0 = one.index[100]
    assert one.loc[t0, "y6"] == pytest.approx(level.loc[t0 + pd.Timedelta(hours=6)] - level.loc[t0])
    assert one.loc[t0, "d6h"] == pytest.approx(level.loc[t0] - level.loc[t0 - pd.Timedelta(hours=6)])
    # Station 2 moves exactly like station 1, so the basin signal seen by 1 is its own change.
    assert one.loc[t0, "basin_d6h"] == pytest.approx(one.loc[t0, "d6h"])
    assert one["to_bank_m"].iloc[0] == pytest.approx(level.iloc[0] - 10.0)


def test_rain_features_from_the_station_cell(raw):
    hours = pd.date_range("2024-01-01", periods=24 * 70, freq="h")
    rain = [2.0 if t.hour == 0 else 0.0 for t in hours]  # 2 mm at midnight, every day
    _gz(raw / "rain" / "15.00_100.00.json.gz", {"lat": 15.0, "lon": 100.0,
                                                 "time": [t.strftime("%Y-%m-%dT%H:%M") for t in hours], "precipitation": rain})
    table = river_table(raw, station_rain(raw)).set_index(["station_id", "time"])
    row = table.loc[("1", pd.Timestamp("2024-01-10 12:00"))]
    assert row["rain_24h"] == pytest.approx(2.0) and row["rain_72h"] == pytest.approx(6.0)
    # Both stations share the cell, so the basin mean equals the station's own rain.
    assert row["basin_rain_24h"] == pytest.approx(2.0)


def test_scores_count_a_rise_over_the_bank():
    df = pd.DataFrame({"to_bank_m": [-0.5, -0.5, 1.0], "y6": [1.0, 0.1, 0.0], "d6h": [0.6, 0.0, 0.0]})
    s = river_scores(df, 6, trend_forecast(df, 6, 1.0))
    assert s["crossings"] == 1 and s["recall"] == 1.0 and s["precision"] == 1.0
    assert river_scores(df, 6, np.zeros(3))["recall"] == 0.0


def test_dam_target_is_next_seven_days_and_climatology_uses_training_years(tmp_path):
    days = pd.date_range("2004-01-01", "2010-12-31", freq="D")
    inflow = (np.arange(len(days)) % 10).astype(float)
    rows = [{"year": y, "data": [{"date": str(d.date()), "value": v} for d, v in zip(days, inflow) if d.year == y]} for y in range(2004, 2011)]
    for series in ("dam_inflow", "dam_released", "dam_storage"):
        _gz(tmp_path / "dams" / f"7_{series}.json.gz", {"raw": {"data": {"graph_data": rows}}})
    (tmp_path / "dams.json").write_text(json.dumps([{"id": 7, "name_th": "ทดสอบ"}]), encoding="utf-8")
    table = dam_table(tmp_path)
    row = table.set_index("time").loc["2005-03-01"]
    expected = sum(inflow[days.get_loc(pd.Timestamp("2005-03-01")) + k] for k in range(1, 8))
    assert row["y7"] == pytest.approx(expected)
    with_clim = add_dam_climatology(table, pd.Timestamp("2008-01-01"))
    assert with_clim["clim_next7"].notna().all()
