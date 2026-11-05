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


def test_a_flash_flood_rising_more_than_the_run_limit_is_kept(tmp_path):
    # 1.5 m an hour up to 7.5 m above the usual level, eight hours there, then back down: water, not a bad run.
    flood = [5.0 + 1.5 * k for k in range(1, 6)] + [12.5] * 8 + [12.5 - 1.5 * k for k in range(1, 6)]
    _station(tmp_path, "5", [5.0] * 72 + flood + [5.0] * 72)
    s = station_series(tmp_path / "rivers" / "5")
    assert s.max() == pytest.approx(12.5) and s.isna().sum() == 0


def test_a_flood_that_returns_after_the_sensor_was_off_is_not_a_datum_change(tmp_path):
    # 20 hours without readings, then 4 m higher for a week: the flow went up 5 times with it, so it is water.
    start = datetime(2024, 1, 1)
    before = [[(start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M"), 5.0, 100.0] for i in range(24 * 30)]
    after = [[(start + timedelta(hours=24 * 30 + 20 + i)).strftime("%Y-%m-%d %H:%M"), 9.0, 500.0] for i in range(24 * 8)]
    _gz(tmp_path / "rivers" / "4" / "2024-01-01.json.gz", {"station_id": "4", "rows": before + after})
    assert station_series(tmp_path / "rivers" / "4").first_valid_index() == pd.Timestamp(start)
    # The same step with the flow unchanged is a new datum: only the later part is kept.
    for row in after:
        row[2] = 100.0
    _gz(tmp_path / "rivers" / "3" / "2024-01-01.json.gz", {"station_id": "3", "rows": before + after})
    assert station_series(tmp_path / "rivers" / "3").first_valid_index() > pd.Timestamp(start) + pd.Timedelta(days=30)


def test_targets_and_lags_never_reach_across_a_gap(raw):
    # Station 1 loses two days in the middle; rows next to the gap must not compare readings two days apart.
    stations = json.loads((raw / "stations.json").read_text(encoding="utf-8"))
    levels = (5 + 0.01 * np.arange(24 * 70)).tolist()
    for i in range(24 * 35, 24 * 37):
        levels[i] = None
    stations[0] = _station(raw, "1", levels)
    (raw / "stations.json").write_text(json.dumps(stations), encoding="utf-8")
    assert station_series(raw / "rivers" / "1").isna().sum() == 48  # a 2-day gap is not partly filled in
    one = river_table(raw).query("station_id == '1'").set_index("time")
    # Every y6 and d24h left is exactly 6 or 24 hours of the 1 cm/hour rise.
    assert one["y6"].dropna().round(6).eq(0.06).all() and one["d24h"].round(6).eq(0.24).all()
    assert one["d6h"].dropna().round(6).eq(0.06).all()


def test_river_chunks_are_calendar_quarters_whatever_the_start_day():
    from datetime import date

    from risk_models.water.download import river_chunks

    a, b = river_chunks(date(2021, 11, 5), date(2026, 11, 5)), river_chunks(date(2021, 11, 9), date(2026, 11, 9))
    assert a[0] == (date(2021, 10, 1), date(2022, 1, 1)) and [c[0] for c in a] == [c[0] for c in b]
    assert a[-1] == (date(2026, 10, 1), date(2026, 11, 5)) and all(x[1] == y[0] for x, y in zip(a, a[1:]))


def test_the_fall_after_a_day_long_flood_is_kept(tmp_path):
    # Up 7.5 m, 30 hours there, then down 1.5 m an hour: after the fall the level sits far below the median of the
    # last 2 days (still full of flood hours), but it got there smoothly, so it is water.
    flood = [5.0 + 1.5 * k for k in range(1, 6)] + [12.5] * 30 + [12.5 - 1.5 * k for k in range(1, 6)]
    _station(tmp_path, "2", [5.0] * 72 + flood + [5.0] * 72)
    assert station_series(tmp_path / "rivers" / "2").isna().sum() == 0
    # The same drop in one hour is a bad run.
    _station(tmp_path, "1", [5.0] * 72 + [-3.0] * 6 + [5.0] * 72)
    assert station_series(tmp_path / "rivers" / "1").min() > -3.0


def test_cleaning_an_hour_never_uses_later_readings(raw):
    # Live, the hours after the latest reading are not known: cleaning the history up to any hour must give the same
    # value for that hour as cleaning the whole history (a flood rising 6 m and a bad 20 m run included).
    from risk_models.water.dataset import clean_level

    start = datetime(2024, 1, 1)
    levels = [5.0] * 72 + [5.0 + 1.5 * k for k in range(1, 5)] + [11.0] * 30 + [11.0 - 2.0 * k for k in range(1, 4)]
    levels += [5.0] * 40 + [25.0] * 6 + [5.0] * 30
    rows = [[(start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M"), v, None] for i, v in enumerate(levels)]
    whole = clean_level(rows)
    for n in range(80, len(rows), 7):
        part = clean_level(rows[:n])
        assert part.iloc[-1] == pytest.approx(whole.loc[part.index[-1]]) or (np.isnan(part.iloc[-1]) and np.isnan(whole.loc[part.index[-1]])), n


def test_an_unconfirmed_jump_in_the_newest_reading_is_not_used(tmp_path):
    from risk_models.water.dataset import clean_level

    start = datetime(2024, 1, 1)
    rows = [[(start + timedelta(minutes=10 * i)).strftime("%Y-%m-%d %H:%M"), 5.0, None] for i in range(6 * 72)]
    rows.append([(start + timedelta(minutes=10 * len(rows))).strftime("%Y-%m-%d %H:%M"), 8.0, None])
    assert clean_level(rows).iloc[-1] == pytest.approx(5.0)  # a 3 m glitch in the latest reading is left out
    rows.append([(start + timedelta(minutes=10 * len(rows))).strftime("%Y-%m-%d %H:%M"), 8.1, None])
    assert clean_level(rows).iloc[-1] > 7.0  # confirmed by the next reading: it is water


def test_a_step_out_of_the_bank_range_is_a_datum_change_even_if_the_flow_follows(tmp_path):
    # Flow is often computed from the level, so it can jump with a new datum: 5 m -> 70 m beside a 10 m bank.
    start = datetime(2024, 1, 1)
    before = [[(start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M"), 5.0, 10.0] for i in range(24 * 30)]
    after = [[(start + timedelta(hours=24 * 30 + 20 + i)).strftime("%Y-%m-%d %H:%M"), 70.0, 500.0] for i in range(24 * 8)]
    _gz(tmp_path / "rivers" / "4" / "2024-01-01.json.gz", {"station_id": "4", "rows": before + after})
    assert station_series(tmp_path / "rivers" / "4", bank=10.0).first_valid_index() > pd.Timestamp(start)
    assert station_series(tmp_path / "rivers" / "4").first_valid_index() == pd.Timestamp(start)  # no bank: flow decides


def test_a_station_without_a_basin_gets_no_basin_signal(raw):
    stations = json.loads((raw / "stations.json").read_text(encoding="utf-8"))
    for s in stations:
        s["basin_th"] = None
    (raw / "stations.json").write_text(json.dumps(stations), encoding="utf-8")
    table = river_table(raw)
    assert table["basin_d6h"].isna().all() and table["basin_d24h"].isna().all()


def test_rain_sums_are_missing_where_rain_is_missing(raw):
    # ERA5 stops a few days before today: the last hours have no rain, which must not read as 0 mm.
    hours = pd.date_range("2024-01-01", periods=24 * 60, freq="h")
    _gz(raw / "rain" / "15.00_100.00.json.gz", {"lat": 15.0, "lon": 100.0, "time": [t.strftime("%Y-%m-%dT%H:%M") for t in hours],
                                                 "precipitation": [1.0] * len(hours)})
    one = river_table(raw, station_rain(raw)).query("station_id == '1'").set_index("time")
    assert one.loc["2024-02-01 12:00", "rain_72h"] == pytest.approx(72.0)
    late = one.loc[one.index > hours[-1]]
    assert len(late) and late["rain_6h"].isna().all() and late["basin_rain_24h"].isna().all()


def test_saved_chunks_of_any_shape_cover_a_quarter():
    from datetime import date

    from risk_models.water.download import covered

    old = [("2021-11-06", "2022-02-04"), ("2022-02-04", "2022-05-05")]  # 90-day chunks of the first version
    assert covered(date(2022, 1, 1), date(2022, 4, 1), old)
    assert not covered(date(2021, 10, 1), date(2022, 1, 1), old)  # October 2021 was never fetched
    assert not covered(date(2022, 4, 1), date(2022, 7, 1), old)


class _FakeClient:
    """Answers with the queued replies in order; an exception in the queue is raised."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def get(self, url, params=None, timeout=None):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_downloads_survive_a_timeout_and_never_save_an_empty_reply(tmp_path, monkeypatch):
    from datetime import date

    import httpx

    from risk_models.water import download

    monkeypatch.setattr(download.time, "sleep", lambda s: None)
    request = httpx.Request("GET", "https://example.test")
    rain = httpx.Response(200, json={"hourly": {"time": ["2024-01-01T00:00"], "precipitation": [0.5]}}, request=request)
    station = {"id": "9", "lat": 15.0, "lon": 100.0}
    counts = download.download_rain(tmp_path, [station], date(2024, 1, 1), date(2024, 1, 2),
                                    _FakeClient(httpx.ReadTimeout("slow"), rain))
    assert counts["fetched"] == 1 and (tmp_path / "rain" / "15.00_100.00.json.gz").exists()
    empty = httpx.Response(200, json={"result": "OK", "data": {"graph_data": []}}, request=request)
    counts = download.download_rivers(tmp_path, [station], date(2024, 1, 1), date(2024, 2, 1), _FakeClient(empty))
    assert counts["empty"] == 1 and not (tmp_path / "rivers" / "9").exists()


def test_splits_leave_out_rows_whose_target_reaches_the_next_period():
    from risk_models.water.model import _before

    rows = pd.DataFrame({"time": pd.date_range("2024-12-31 12:00", periods=24, freq="h")})
    kept = _before(rows, pd.Timestamp("2025-01-01"), pd.Timedelta(hours=6))
    assert kept["time"].max() == pd.Timestamp("2024-12-31 17:00")


def test_live_features_match_the_training_rows(raw):
    # The live forecast must see exactly what training saw for the same hour: same cleaning, same features.
    from risk_models.water.dataset import LIVE_RIVER_FEATURES, _read, live_features

    table = river_table(raw).query("station_id == '1'").set_index("time")
    rows = _read(raw / "rivers" / "1" / "2024-01-01.json.gz")["rows"]
    t = table.index[-1]
    row = live_features(rows, bank=10.0, now=t + pd.Timedelta(minutes=30))
    assert row["time"] == t
    for name in LIVE_RIVER_FEATURES:
        assert row[name] == pytest.approx(table.loc[t, name]), name
    assert np.isnan(row["basin_d6h"]) and np.isnan(row["rain_24h"])  # not known live
    # Readings that stopped more than 3 hours before now give no row.
    assert live_features(rows, bank=10.0, now=t + pd.Timedelta(hours=4)) is None


def test_forecast_change_only_at_chosen_horizons():
    from risk_models.water.dataset import LIVE_RIVER_FEATURES
    from risk_models.water.model import WaterModels, forecast_change

    class Const:
        def __init__(self, v):
            self.v = v

        def predict(self, x):
            return np.full(len(x), self.v)

    models = WaterModels(river={}, river_features=[], trend_damping={}, dam=None, dam_features=[], dam_scale={},
                         version="t", river_live={6: Const(0.1), 12: Const(0.2)},
                         river_live_features=list(LIVE_RIVER_FEATURES), live_stations={6: ["A"], 12: ["B"]})
    row = pd.Series({name: 0.0 for name in LIVE_RIVER_FEATURES})
    assert forecast_change(models, "A", row) == {6: pytest.approx(0.1)}
    assert forecast_change(models, "C", row) == {}


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
    for series in ("dam_inflow", "dam_released"):
        _gz(tmp_path / "dams" / f"7_{series}.json.gz", {"raw": {"data": {"graph_data": rows}}})
    # Storage peaks at 90 in the test years (2010) and 9 before: the capacity must come from the training years.
    storage = [{"year": y, "data": [{"date": str(d.date()), "value": v * (10 if y == 2010 else 1)}
                                    for d, v in zip(days, inflow) if d.year == y]} for y in range(2004, 2011)]
    _gz(tmp_path / "dams" / "7_dam_storage.json.gz", {"raw": {"data": {"graph_data": storage}}})
    (tmp_path / "dams.json").write_text(json.dumps([{"id": 7, "name_th": "ทดสอบ"}]), encoding="utf-8")
    fitted = dam_table(tmp_path, fit_end=pd.Timestamp("2008-01-01")).set_index("time")
    assert fitted["storage_pct"].loc[:"2007-12-31"].max() == pytest.approx(100.0)
    table = dam_table(tmp_path)
    row = table.set_index("time").loc["2005-03-01"]
    expected = sum(inflow[days.get_loc(pd.Timestamp("2005-03-01")) + k] for k in range(1, 8))
    assert row["y7"] == pytest.approx(expected)
    with_clim = add_dam_climatology(table, pd.Timestamp("2008-01-01"))
    assert with_clim["clim_next7"].notna().all()
