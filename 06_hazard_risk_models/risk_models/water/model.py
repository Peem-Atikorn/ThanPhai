"""Gradient boosting for river-level change (+6/+12/+24 h) and 7-day dam inflow, each against simple baselines.

Splits are by time, never random: the test period is the last year, which the models and every choice made
(trend damping, iterations) never see. A model is only worth using where it beats the baselines on that year.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from risk_models.water.dataset import DAM_FEATURES, DAM_HORIZON_D, HORIZONS_H, RIVER_FEATURES

TREND_DAMPING = (0.0, 0.25, 0.5, 0.75, 1.0)
HIGH_WATER_M = -1.0  # "near the bank": within 1 m below it, or above


@dataclass
class WaterModels:
    river: dict[int, HistGradientBoostingRegressor]
    river_features: list[str]
    trend_damping: dict[int, float]
    dam: HistGradientBoostingRegressor | None
    dam_features: list[str]
    dam_scale: dict[int, float]
    version: str
    metrics: dict = field(default_factory=dict)
    # The live forecast: the same model trained only on what one station's own recent readings give (other stations
    # and rain are not fetched live), and per horizon the stations where it beat "no change" in the validation year.
    river_live: dict[int, HistGradientBoostingRegressor] = field(default_factory=dict)
    river_live_features: list[str] = field(default_factory=list)
    live_stations: dict[int, set[str]] = field(default_factory=dict)


def forecast_change(models: WaterModels, station_id: str, row: pd.Series) -> dict[int, float]:
    """Predicted level change (m) per horizon for one live feature row, only at horizons this station is chosen for."""
    x = pd.DataFrame([row[models.river_live_features].astype(float).to_numpy()], columns=models.river_live_features)
    return {h: float(model.predict(x)[0]) for h, model in sorted(models.river_live.items())
            if station_id in models.live_stations.get(h, ())}


def _estimator(loss: str = "squared_error", learning_rate: float = 0.06, max_iter: int = 2000) -> HistGradientBoostingRegressor:
    # Early stopping on a held-out 10% of the training rows decides the real number of trees.
    return HistGradientBoostingRegressor(loss=loss, learning_rate=learning_rate, max_iter=max_iter, max_leaf_nodes=63,
                                         min_samples_leaf=200, l2_regularization=1.0, early_stopping=True,
                                         validation_fraction=0.1, n_iter_no_change=30, random_state=0)


def trend_forecast(df: pd.DataFrame, h: int, damping: float) -> np.ndarray:
    """Carry the last 6 hours' rate forward h hours, scaled by `damping` (0 = no change; also when 6 h ago is missing)."""
    return damping * np.nan_to_num(df["d6h"].to_numpy(dtype=float)) / 6 * h


def river_scores(df: pd.DataFrame, h: int, pred: np.ndarray) -> dict:
    """MAE of the level h hours ahead, overall and near the bank, and how well a rise over the bank is caught."""
    y = df[f"y{h}"].to_numpy()
    err = np.abs(pred - y)
    near = df["to_bank_m"].to_numpy() > HIGH_WATER_M
    below_now = df["to_bank_m"].to_numpy() <= 0
    crosses = below_now & (df["to_bank_m"].to_numpy() + y > 0)
    warned = below_now & (df["to_bank_m"].to_numpy() + pred > 0)
    tp = int((crosses & warned).sum())
    return {
        "mae_m": float(err.mean()),
        "mae_near_bank_m": float(err[near].mean()) if near.any() else None,
        "rows": int(len(y)), "rows_near_bank": int(near.sum()),
        "crossings": int(crosses.sum()), "warned": int(warned.sum()),
        "recall": tp / crosses.sum() if crosses.any() else None,
        "precision": tp / warned.sum() if warned.any() else None,
    }


def _before(df: pd.DataFrame, end: pd.Timestamp, horizon: pd.Timedelta) -> pd.DataFrame:
    """Rows whose whole target window (up to time + horizon) ends before `end`."""
    return df[df["time"] + horizon < end]


def train_rivers(table: pd.DataFrame, valid_start: pd.Timestamp, test_start: pd.Timestamp, sample_every: int = 3,
                 features: list[str] | None = None, **settings) -> tuple:
    test = table[table["time"] >= test_start]
    # Neighbouring hours are nearly the same row; every 3rd hour keeps the information at a third of the cost.
    sampled = table[table["time"].dt.hour % sample_every == 0]
    # Rain columns are empty when no rain was downloaded; a column with no values cannot be split on.
    features = [f for f in (features or RIVER_FEATURES) if sampled.loc[sampled["time"] < valid_start, f].notna().any()]
    models, damping, metrics = {}, {}, {"features": features}
    for h in HORIZONS_H:
        # A row's target is the level h hours later, so a row belongs to a period only when that hour does too.
        t = _before(sampled, valid_start, pd.Timedelta(hours=h)).dropna(subset=[f"y{h}"])
        valid = _before(table[table["time"] >= valid_start], test_start, pd.Timedelta(hours=h))
        # Chosen on the validation year: absolute error beat squared error at every horizon (24 h MAE 0.144 vs
        # 0.162 m), because the few remaining bad readings pull a squared-error fit off.
        model = _estimator(**{"loss": "absolute_error", **settings}).fit(t[features], t[f"y{h}"])
        models[h] = model
        v = valid.dropna(subset=[f"y{h}"])
        damping[h] = min(TREND_DAMPING, key=lambda a: np.abs(trend_forecast(v, h, a) - v[f"y{h}"]).mean())
        result, per_station, errors = {}, {}, {}
        for name, part in (("valid", v), ("test", test.dropna(subset=[f"y{h}"]))):
            pred = model.predict(part[features])
            result[name] = {
                "model": river_scores(part, h, pred),
                "no_change": river_scores(part, h, np.zeros(len(part))),
                "trend": river_scores(part, h, trend_forecast(part, h, damping[h])),
            }
            errors[name] = pd.DataFrame({"station": part["station_id"].to_numpy(),
                                         "model": np.abs(pred - part[f"y{h}"].to_numpy()),
                                         "no_change": np.abs(part[f"y{h}"].to_numpy())})
            per_station[name] = errors[name].groupby("station")[["model", "no_change"]].mean()
        # A forecast is only worth showing at a station where it beat "no change" in the validation year;
        # the test year then checks that this choice holds.
        good = per_station["valid"].index[per_station["valid"]["model"] < per_station["valid"]["no_change"]]
        tested = per_station["test"]
        # The test-year error where a forecast would be shown: the stations chosen on the validation year only.
        chosen = errors["test"][errors["test"]["station"].isin(good)]
        result["test_chosen"] = {"mae_m": float(chosen["model"].mean()) if len(chosen) else None,
                                 "no_change_mae_m": float(chosen["no_change"].mean()) if len(chosen) else None,
                                 "rows": int(len(chosen))}
        result["stations_better_on_valid"] = sorted(map(str, good))
        result["stations"] = {
            "valid": int(len(per_station["valid"])),
            "better_on_valid": int(len(good)),
            "test": int(len(tested)),
            "better_on_test": int((tested["model"] < tested["no_change"]).sum()),
            "chosen_still_better_on_test": int((tested.loc[tested.index.intersection(good), "model"]
                                                < tested.loc[tested.index.intersection(good), "no_change"]).sum()),
            "chosen_on_test": int(len(tested.index.intersection(good))),
        }
        result["train_rows"], result["iterations"], result["trend_damping"] = int(len(t)), int(model.n_iter_), damping[h]
        metrics[f"{h}h"] = result
    return models, damping, metrics


def train_dams(table: pd.DataFrame, valid_start: pd.Timestamp, test_start: pd.Timestamp, **settings) -> tuple:
    """Inflow is scaled by each dam's mean daily inflow in the training years, so big and small dams share one model."""
    horizon = pd.Timedelta(days=DAM_HORIZON_D)
    train = _before(table, valid_start, horizon)
    scale = train.groupby("dam_id")["inflow"].mean().clip(lower=0.01).to_dict()
    def scaled(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        s = out["dam_id"].map(scale)
        for col in ("inflow_d0", "inflow_d1", "inflow_d2", "inflow_mean7", "inflow_mean30", "inflow_trend7",
                    "released_mean7", "clim_next7", "y7"):
            out[col] = out[col] / s
        out["storage_change7"] = out["storage_change7"] / s
        return out[out["dam_id"].isin(scale)]
    tr = scaled(train).dropna(subset=["y7"])
    # Chosen on 2023-2024 only: absolute error beat squared error (MAE 8.9 vs 9.9-10.2), and 2,000 trees did no
    # better than 600.
    model = _estimator(**{"loss": "absolute_error", "max_iter": 600, **settings}).fit(tr[DAM_FEATURES], tr["y7"])
    metrics = {"train_rows": int(len(tr)), "iterations": int(model.n_iter_)}
    for name, part in (("valid", _before(table[table["time"] >= valid_start], test_start, horizon)),
                       ("test", table[table["time"] >= test_start])):
        p = scaled(part).dropna(subset=["y7", "clim_next7"])
        s = p["dam_id"].map(scale).to_numpy()
        y = p["y7"].to_numpy() * s
        preds = {"model": model.predict(p[DAM_FEATURES]) * s,
                 "last_7_days": p["inflow_mean7"].to_numpy() * 7 * s,
                 "climatology": p["clim_next7"].to_numpy() * s}
        wet = y > np.nanpercentile(y, 90)
        metrics[name] = {k: {"mae_mcm": float(np.abs(v - y).mean()),
                             "mae_wettest_10pct_mcm": float(np.abs(v[wet] - y[wet]).mean()),
                             "bias_mcm": float((v - y).mean())} for k, v in preds.items()}
        metrics[name]["rows"] = int(len(y))
        metrics[name]["dams"] = int(p["dam_id"].nunique())
    return model, scale, metrics
