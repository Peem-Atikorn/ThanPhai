"""Train and evaluate the district fire-risk model on FIRMS VIIRS history (2021-2025).

Split by time: train 2021-2023, choose thresholds on 2024, report on 2025 (never seen while fitting or tuning).
Run from the repo root after scripts/download_firms_history.py and scripts/build_admin_areas.py:
    .venv/Scripts/python scripts/train_fire_model.py
"""

import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance

from geo_integration import Gazetteer
from geo_integration.area_mapper import AreaMapper
from risk_models.fire import static_sources
from risk_models.fire.dataset import (
    FEATURES,
    climatology,
    daily_grid,
    features,
    labels,
    load_hotspots,
    to_table,
)
from risk_models.fire.model import FireModel, Report, fit, scores, thresholds

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
MODEL_PATH = PROCESSED / "models" / "fire_model.joblib"
REPORT_DIR = ROOT / "06_hazard_risk_models" / "reports"
TRAIN_YEARS, VAL_YEAR, TEST_YEAR = {2021, 2022, 2023}, 2024, 2025


def _load_optional_grid(name: str, shape: tuple[int, int]) -> np.ndarray | None:
    path = PROCESSED / f"{name}_daily_amphoe.npy"
    if not path.exists():
        return None
    arr = np.load(path)
    if arr.shape != shape:
        raise ValueError(f"{path.name} shape {arr.shape} != {shape}")
    return arr.astype(np.float32)


def log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    gaz = Gazetteer.load(PROCESSED / "gazetteer.json")
    amphoe_codes = [a.code for a in gaz.areas if a.level == "amphoe"]
    province_of = [c[:4] for c in amphoe_codes]

    points = load_hotspots(ROOT / "data" / "raw" / "firms")
    log(f"hotspots (nominal/high confidence): {len(points):,}", t0)
    # Industrial heat (type 2: cement kilns, refineries) is not wildfire. Build the mask the live feed
    # needs (NRT has no type field), then train on vegetation fires with the same mask applied.
    static_cells = static_sources.build(points)
    static_sources.save(static_cells, PROCESSED / "firms_static_cells.json")
    before = len(points)
    points = static_sources.drop_static(points[points["type"] == 0], static_cells)
    log(f"static source cells: {len(static_cells)} | vegetation fires kept: {len(points):,} of {before:,}", t0)
    mapped = AreaMapper.from_gpkg(PROCESSED / "admin_areas.gpkg").assign_points(
        points["latitude"].tolist(), points["longitude"].tolist(), nearest=False)
    log(f"inside Thailand: {mapped['amphoe_code'].notna().sum():,} ({mapped['amphoe_code'].notna().mean():.0%})", t0)

    grid = daily_grid(points, mapped["amphoe_code"], amphoe_codes, date(2021, 1, 1), date(2025, 12, 31))
    clim = climatology(grid, TRAIN_YEARS)  # training years only, so 2024/2025 never leak in
    ndvi_arr = _load_optional_grid("ndvi", grid.counts.shape)
    humidity_arr = _load_optional_grid("humidity", grid.counts.shape)
    names = [*FEATURES] + (["ndvi"] if ndvi_arr is not None else []) + (["rel_humidity"] if humidity_arr is not None else [])
    log(f"features: {names}", t0)
    table = to_table(grid, features(grid, province_of, clim, ndvi=ndvi_arr, humidity=humidity_arr), labels(grid)).dropna(subset=["y"])
    year = pd.to_datetime(table["date"]).dt.year
    train, val, test = table[year.isin(TRAIN_YEARS)], table[year == VAL_YEAR], table[year == TEST_YEAR]
    log(f"rows train {len(train):,} / val {len(val):,} / test {len(test):,}", t0)

    estimator = fit(train, names)
    log("model fitted", t0)
    p_val = estimator.predict_proba(val[names])[:, 1]
    watch, warning = thresholds(val["y"].to_numpy(), p_val)
    model = FireModel(estimator, watch, warning, sorted(TRAIN_YEARS), version=f"fire-hgb-{date.today():%Y%m%d}",
                      amphoe_codes=amphoe_codes, clim=clim, feature_names=names)

    # Baselines get the same treatment: thresholds chosen on 2024, scored on 2025.
    y_val, y_test = val["y"].to_numpy(), test["y"].to_numpy()
    candidates = {
        "model (gradient boosting)": (p_val, model.predict_proba(test)),
        "rule: >=3 hotspots today (current WATCH rule)": (val["c1"].to_numpy(), test["c1"].to_numpy()),
        "rule: >=3 hotspots in last 7 days": (val["c7"].to_numpy(), test["c7"].to_numpy()),
        "seasonal climatology": (val["clim"].to_numpy(), test["clim"].to_numpy()),
    }
    results = {}
    for name, (sv, st) in candidates.items():
        if name.startswith("rule:"):
            results[name] = scores(y_test, st, 3.0)  # fixed rule, no tuning
        else:
            t_watch = watch if name.startswith("model") else thresholds(y_val, sv)[0]
            results[name] = scores(y_test, st, t_watch)
    log("evaluated", t0)

    sample = test.sample(min(100_000, len(test)), random_state=0)
    imp = permutation_importance(estimator, sample[model.feature_names], sample["y"], scoring="average_precision", n_repeats=3, random_state=0)
    importance = dict(sorted(zip(model.feature_names, np.round(imp.importances_mean, 4).tolist()), key=lambda x: -x[1]))

    report = Report(
        rows={"train": len(train), "val": len(val), "test": len(test)},
        positive_rate={k: round(float(v["y"].mean()), 4) for k, v in (("train", train), ("val", val), ("test", test))},
        thresholds={"watch": round(watch, 4), "warning": round(warning, 4)},
        test=results,
        feature_importance=importance,
    )
    model.metrics = report.as_dict()
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.save(MODEL_PATH)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "fire_model_metrics.json").write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"saved {MODEL_PATH.relative_to(ROOT)}", t0)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
