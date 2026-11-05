"""Train the river-level and dam-inflow models from data/raw/water_history and score them on the last year.

Run from the repo root after scripts/download_water_history.py:
    .venv/Scripts/python scripts/train_water_model.py [--part rivers|dams|all] [--data-dir data]
Writes data/processed/models/water_model.joblib and 06_hazard_risk_models/reports/water_model_metrics.json.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd

from risk_models.water.dataset import (DAM_FEATURES, LIVE_RIVER_FEATURES, RIVER_FEATURES, add_dam_climatology, dam_table,
                                       river_table, station_rain)
from risk_models.water.model import WaterModels, train_dams, train_rivers

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", choices=("rivers", "dams", "all"), default="all")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--no-rain", action="store_true", help="train without rain even if rain was downloaded")
    args = parser.parse_args()
    raw = args.data_dir / "raw" / "water_history"
    out_models = args.data_dir / "processed" / "models"
    out_models.mkdir(parents=True, exist_ok=True)
    metrics_path = ROOT / "06_hazard_risk_models" / "reports" / "water_model_metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    model_path = out_models / "water_model.joblib"
    saved = joblib.load(model_path) if model_path.exists() else None
    river_models, damping = (saved.river, saved.trend_damping) if saved else ({}, {})
    dam_model, dam_scale = (saved.dam, saved.dam_scale) if saved else (None, {})
    live_models = getattr(saved, "river_live", {}) if saved else {}
    live_stations = getattr(saved, "live_stations", {}) if saved else {}

    if args.part in ("dams", "all"):
        started = time.time()
        # Train up to 2022, choose nothing on 2023-2024 except reporting, test on 2025 onward.
        valid_start, test_start = pd.Timestamp("2023-01-01"), pd.Timestamp("2025-01-01")
        table = dam_table(raw, fit_end=valid_start)
        table = add_dam_climatology(table, valid_start)
        dam_model, dam_scale, m = train_dams(table, valid_start, test_start)
        m.update({"dams": int(table["dam_id"].nunique()), "from": str(table["time"].min().date()),
                  "to": str(table["time"].max().date()), "valid_start": str(valid_start.date()),
                  "test_start": str(test_start.date()), "seconds": round(time.time() - started)})
        metrics["dams_7d_inflow"] = m
        print(json.dumps(m, ensure_ascii=False, indent=1), flush=True)

    if args.part in ("rivers", "all"):
        started = time.time()
        rain = None if args.no_rain else station_rain(raw)
        table = river_table(raw, rain)
        end = table["time"].max()
        test_start = (end - pd.DateOffset(years=1)).normalize()
        valid_start = (test_start - pd.DateOffset(years=1)).normalize()
        river_models, damping, m = train_rivers(table, valid_start, test_start)
        m.update({"stations": int(table["station_id"].nunique()), "rows": int(len(table)),
                  "from": str(table["time"].min().date()), "to": str(end.date()), "valid_start": str(valid_start.date()),
                  "test_start": str(test_start.date()), "rain_stations": 0 if rain is None else int(rain.shape[1]),
                  "seconds": round(time.time() - started)})
        metrics["rivers_level_change"] = m
        print(json.dumps(m, ensure_ascii=False, indent=1), flush=True)
        # The model the app serves: the same split and settings, on what one station's own readings give live.
        started = time.time()
        live_models, _, lm = train_rivers(table, valid_start, test_start, features=LIVE_RIVER_FEATURES)
        live_stations = {int(k[:-1]): set(v["stations_better_on_valid"]) for k, v in lm.items() if k.endswith("h")}
        lm["seconds"] = round(time.time() - started)
        metrics["rivers_live"] = lm
        print(json.dumps({k: v["test"]["model"] for k, v in lm.items() if k.endswith("h")}, indent=1), flush=True)

    river_features = metrics.get("rivers_level_change", {}).get("features", list(RIVER_FEATURES))
    models = WaterModels(river=river_models, river_features=river_features, trend_damping=damping, dam=dam_model,
                         dam_features=list(DAM_FEATURES), dam_scale=dam_scale,
                         version=datetime.now().strftime("%Y%m%d-%H%M"), metrics=metrics, river_live=live_models,
                         river_live_features=metrics.get("rivers_live", {}).get("features", list(LIVE_RIVER_FEATURES)),
                         live_stations=live_stations)
    joblib.dump(models, model_path)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {model_path}", flush=True)
    sys.exit(0)
