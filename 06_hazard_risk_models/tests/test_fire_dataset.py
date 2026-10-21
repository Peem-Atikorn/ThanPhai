from datetime import date, timedelta

import numpy as np
import pandas as pd

from risk_models.fire.dataset import DailyGrid, _window_sum, climatology, daily_grid, features, labels, to_table
from risk_models.fire.model import thresholds


def grid_from(counts: np.ndarray, start=date(2024, 1, 1)) -> DailyGrid:
    dates = [start + timedelta(days=i) for i in range(len(counts))]
    return DailyGrid(dates, [f"TH10{j:02d}" for j in range(counts.shape[1])], counts.astype(np.float32), counts.astype(np.float32) * 2)


def test_window_sum_includes_today_and_looks_back_only():
    x = np.arange(1, 6, dtype=np.float32)[:, None]  # 1..5
    assert _window_sum(x, 3).ravel().tolist() == [1, 3, 6, 9, 12]


def test_labels_look_forward_seven_days_and_stop_at_the_end():
    counts = np.zeros((20, 1))
    counts[5] = 3  # three hotspots on day 5
    y = labels(grid_from(counts)).ravel()
    # Window after t=0 is days 1..7 (contains day 5); after t=5 it is days 6..12 (the event day itself is excluded).
    assert y[0] == 1 and y[4] == 1 and y[5] == 0
    assert np.isnan(y[-7:]).all() and not np.isnan(y[:-7]).any()


def test_label_window_is_exactly_days_t_plus_1_to_t_plus_7():
    counts = np.zeros((30, 1))
    counts[10] = 3
    y = labels(grid_from(counts)).ravel()
    assert [t for t in range(23) if y[t] == 1] == [3, 4, 5, 6, 7, 8, 9]  # t+1..t+7 contains day 10


def test_features_never_use_the_future():
    counts = np.zeros((40, 2))
    counts[35, 0] = 50
    f = features(grid_from(counts), ["TH10", "TH10"], np.zeros((12, 2), np.float32))
    for name in ("c1", "c3", "c7", "c30", "frp7", "prov7"):
        assert f[name][:35].sum() == 0, name  # nothing before the event day sees it


def test_province_feature_sums_districts():
    counts = np.zeros((10, 3))
    counts[9] = [1, 2, 4]
    f = features(grid_from(counts), ["TH10", "TH10", "TH20"], np.zeros((12, 3), np.float32))
    assert f["prov7"][9].tolist() == [3, 3, 4]


def test_climatology_uses_only_given_years():
    counts = np.zeros((800, 1))
    g = grid_from(counts, start=date(2023, 1, 1))
    counts_2024_jan = [i for i, d in enumerate(g.dates) if d.year == 2024 and d.month == 1]
    g.counts[counts_2024_jan] = 10
    assert climatology(g, {2023})[0, 0] == 0
    assert climatology(g, {2024})[0, 0] == 10


def test_daily_grid_counts_points_by_day_and_district():
    pts = pd.DataFrame({"acq_date": [date(2024, 1, 2)] * 3 + [date(2024, 1, 3)], "frp": [1.0, 2.0, 3.0, 4.0]})
    amphoe = pd.Series(["TH1001", "TH1001", None, "TH1002"])
    g = daily_grid(pts, amphoe, ["TH1001", "TH1002"], date(2024, 1, 1), date(2024, 1, 3))
    assert g.counts.tolist() == [[0, 0], [2, 0], [0, 1]]
    assert g.frp[1, 0] == 3.0


def test_to_table_skips_incomplete_history():
    g = grid_from(np.zeros((40, 2)))
    t = to_table(g, features(g, ["TH10", "TH10"], np.zeros((12, 2), np.float32)), labels(g))
    assert len(t) == (40 - 29) * 2 and set(t["amphoe"]) == {"TH1000", "TH1001"}


def test_static_source_mask_drops_kiln_points_and_keeps_forest():
    from risk_models.fire import static_sources

    kiln = pd.DataFrame({"latitude": [14.5501] * 4, "longitude": [101.0801] * 4, "type": [2] * 4})
    once = pd.DataFrame({"latitude": [18.0], "longitude": [99.0], "type": [2]})  # a single type-2 label
    cells = static_sources.build(pd.concat([kiln, once]))
    assert len(cells) == 1  # one-off labels are ignored
    live = pd.DataFrame({"latitude": [14.5512, 14.556, 18.0, 19.5], "longitude": [101.0795, 101.085, 99.0, 98.9]})
    kept = static_sources.drop_static(live, cells)
    # Same cell and the neighbouring cell are dropped; the one-off and the forest point stay.
    assert kept["latitude"].tolist() == [18.0, 19.5]


def test_static_mask_roundtrip(tmp_path):
    from risk_models.fire import static_sources

    static_sources.save({"1455_10108"}, tmp_path / "m.json")
    assert static_sources.load(tmp_path / "m.json") == {"1455_10108"}
    assert static_sources.load(tmp_path / "missing.json") == set()


def test_thresholds_hit_targets():
    rng = np.random.default_rng(0)
    y = rng.random(5000) < 0.1
    p = np.clip(y * 0.6 + rng.random(5000) * 0.5, 0, 1)
    watch, warning = thresholds(y, p)
    assert warning >= watch
    assert ((p >= watch) & y).sum() / y.sum() >= 0.8
    pred = p >= warning
    assert (pred & y).sum() / pred.sum() >= 0.7


def test_thresholds_match_brute_force():
    # The fast version must pick the same cut-offs as scoring every candidate.
    rng = np.random.default_rng(1)
    y = rng.random(2000) < 0.2
    p = np.round(np.clip(y * 0.5 + rng.random(2000) * 0.6, 0, 1), 3)
    cands = np.unique(p)
    rec = lambda t: ((p >= t) & y).sum() / y.sum()  # noqa: E731
    prec = lambda t: ((p >= t) & y).sum() / max((p >= t).sum(), 1)  # noqa: E731
    watch_bf = max(t for t in cands if rec(t) >= 0.8)
    warning_bf = min(t for t in cands if t >= watch_bf and prec(t) >= 0.7)
    assert thresholds(y, p) == (watch_bf, warning_bf)


def test_extended_features_support_ndvi_and_humidity():
    from risk_models.fire.dataset import EXTENDED_FEATURES

    g = grid_from(np.zeros((40, 2)))
    ndvi_fake = np.full(g.counts.shape, 0.45, dtype=np.float32)
    hum_fake = np.full(g.counts.shape, 55.0, dtype=np.float32)
    f = features(g, ["TH10", "TH10"], np.zeros((12, 2), np.float32), ndvi=ndvi_fake, humidity=hum_fake)
    assert "ndvi" in f and "rel_humidity" in f
    assert f["ndvi"][0, 0] == 0.45
    assert f["rel_humidity"][0, 0] == 55.0

    table = to_table(g, f)
    for col in EXTENDED_FEATURES:
        assert col in table.columns


def test_fire_model_predict_proba_backward_compatibility_without_feature_names():
    from risk_models.fire.model import FireModel, fit

    g = grid_from(np.zeros((40, 2)))
    f = features(g, ["TH10", "TH10"], np.zeros((12, 2), np.float32))
    table = to_table(g, f, labels(g))
    valid = table.dropna()
    estimator = fit(valid)
    m = FireModel(estimator=estimator, watch_threshold=0.2, warning_threshold=0.5, trained_years=[2023], version="v1")
    # Simulate old unpickled model without feature_names attribute
    del m.feature_names
    assert not hasattr(m, "feature_names")
    proba = m.predict_proba(valid)
    assert len(proba) == len(valid)
    assert (proba >= 0.0).all() and (proba <= 1.0).all()
