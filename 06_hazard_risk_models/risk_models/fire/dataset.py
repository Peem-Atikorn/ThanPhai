"""Daily hotspot counts per district and the features/labels for the fire model.

Target: will the district have >= 3 hotspots in the next 7 days? (the same count as the WATCH rule).
Features use only data up to day t, so training and live prediction see the same inputs.
"""

import gzip
import io
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HORIZON_DAYS = 7
TARGET_MIN_HOTSPOTS = 3
HISTORY_DAYS = 30
FEATURES = ["c1", "c3", "c7", "c30", "frp7", "prov7", "doy_sin", "doy_cos", "clim"]
EXTENDED_FEATURES = [*FEATURES, "ndvi", "rel_humidity"]


def load_hotspots(folder: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(folder.glob("*.csv.gz")):
        text = gzip.decompress(path.read_bytes()).decode()
        if text.strip():
            frames.append(pd.read_csv(io.StringIO(text)))
    df = pd.concat(frames, ignore_index=True).drop_duplicates()
    # VIIRS confidence is l/n/h; low-confidence detections are mostly sun glint and noise.
    df = df[df["confidence"].astype(str).str.lower() != "l"]
    df["acq_date"] = pd.to_datetime(df["acq_date"]).dt.date
    return df.reset_index(drop=True)


@dataclass
class DailyGrid:
    dates: list[date]
    amphoe_codes: list[str]
    counts: np.ndarray  # [days, amphoe]
    frp: np.ndarray  # [days, amphoe] summed fire radiative power (MW)


def daily_grid(points: pd.DataFrame, amphoe_of_point: pd.Series, amphoe_codes: list[str], start: date, end: date) -> DailyGrid:
    dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    day_index = {d: i for i, d in enumerate(dates)}
    col = {c: j for j, c in enumerate(amphoe_codes)}
    counts = np.zeros((len(dates), len(amphoe_codes)), dtype=np.float32)
    frp = np.zeros_like(counts)
    keep = amphoe_of_point.notna() & points["acq_date"].isin(day_index.keys())
    rows = points.loc[keep, "acq_date"].map(day_index).to_numpy()
    cols = amphoe_of_point[keep].map(col).to_numpy()
    valid = ~pd.isna(cols)
    rows, cols = rows[valid].astype(int), cols[valid].astype(int)
    np.add.at(counts, (rows, cols), 1)
    np.add.at(frp, (rows, cols), points.loc[keep, "frp"].fillna(0).to_numpy()[valid])
    return DailyGrid(dates, amphoe_codes, counts, frp)


def _window_sum(x: np.ndarray, days: int) -> np.ndarray:
    """Sum over the last `days` days including today, for every day (zeros before the start)."""
    c = np.cumsum(np.vstack([np.zeros((1, x.shape[1]), x.dtype), x]), axis=0)
    out = c[1:] - c[np.maximum(np.arange(1, len(x) + 1) - days, 0)]
    return out


def climatology(grid: DailyGrid, years: set[int]) -> np.ndarray:
    """Mean daily hotspots per district and calendar month, from the given (training) years only."""
    months = np.array([d.month for d in grid.dates])
    in_years = np.array([d.year in years for d in grid.dates])
    clim = np.zeros((12, grid.counts.shape[1]), dtype=np.float32)
    for m in range(1, 13):
        sel = in_years & (months == m)
        if sel.any():
            clim[m - 1] = grid.counts[sel].mean(axis=0)
    return clim


def features(
    grid: DailyGrid,
    province_of: list[str],
    clim: np.ndarray,
    ndvi: np.ndarray | None = None,
    humidity: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Feature arrays shaped [days, amphoe]."""
    c7 = _window_sum(grid.counts, 7)
    provinces = sorted(set(province_of))
    p_idx = np.array([provinces.index(p) for p in province_of])
    prov7_total = np.zeros((len(grid.dates), len(provinces)), dtype=np.float32)
    np.add.at(prov7_total.T, p_idx, c7.T)
    doy = np.array([d.timetuple().tm_yday for d in grid.dates], dtype=np.float32)
    months = np.array([d.month for d in grid.dates])
    ones = np.ones(grid.counts.shape, dtype=np.float32)
    feats = {
        "c1": grid.counts,
        "c3": _window_sum(grid.counts, 3),
        "c7": c7,
        "c30": _window_sum(grid.counts, HISTORY_DAYS),
        "frp7": _window_sum(grid.frp, 7),
        "prov7": prov7_total[:, p_idx],
        "doy_sin": ones * np.sin(2 * np.pi * doy / 365.25)[:, None],
        "doy_cos": ones * np.cos(2 * np.pi * doy / 365.25)[:, None],
        "clim": clim[months - 1],
    }
    if ndvi is not None:
        feats["ndvi"] = ndvi
    if humidity is not None:
        feats["rel_humidity"] = humidity
    return feats


def labels(grid: DailyGrid) -> np.ndarray:
    """1 if the district has >= TARGET_MIN_HOTSPOTS in days t+1..t+7. NaN where the future is not known."""
    future = np.full(grid.counts.shape, np.nan, dtype=np.float32)
    c = np.cumsum(np.vstack([np.zeros((1, grid.counts.shape[1])), grid.counts]), axis=0)
    n = len(grid.dates)
    for t in range(n - HORIZON_DAYS):
        future[t] = c[t + 1 + HORIZON_DAYS] - c[t + 1]
    y = np.where(np.isnan(future), np.nan, (future >= TARGET_MIN_HOTSPOTS).astype(np.float32))
    return y


def to_table(grid: DailyGrid, feats: dict[str, np.ndarray], y: np.ndarray | None = None) -> pd.DataFrame:
    """Flatten [days, amphoe] arrays into rows, skipping the first 30 days (incomplete history)."""
    n_days, n_amp = grid.counts.shape
    start = HISTORY_DAYS - 1
    data = {k: v[start:].ravel() for k, v in feats.items()}
    data["date"] = np.repeat(np.array(grid.dates[start:], dtype="datetime64[D]"), n_amp)
    data["amphoe"] = np.tile(np.array(grid.amphoe_codes), n_days - start)
    if y is not None:
        data["y"] = y[start:].ravel()
    return pd.DataFrame(data)
