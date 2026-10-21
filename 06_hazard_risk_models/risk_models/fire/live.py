"""Live fire-risk prediction: last 30 days of FIRMS NRT hotspots -> features for today -> model level."""

import asyncio
import io
from datetime import date, timedelta

import httpx
import numpy as np
import pandas as pd

from risk_models.fire.dataset import FEATURES, HISTORY_DAYS, daily_grid, features, to_table

NRT_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NRT/97.3,5.6,105.7,20.5/5/{day}"


async def fetch_recent(key: str, today: date, days: int = HISTORY_DAYS) -> pd.DataFrame:
    """Hotspots for the last `days` days (5 days per call, 6 calls for 30 days)."""
    starts = [today - timedelta(days=days - 1) + timedelta(days=5 * i) for i in range((days + 4) // 5)]
    async with httpx.AsyncClient() as client:
        replies = await asyncio.gather(*(client.get(NRT_URL.format(key=key, day=d.isoformat()), timeout=120) for d in starts))
    frames = []
    for r in replies:
        r.raise_for_status()
        if not r.text.startswith("latitude"):
            raise RuntimeError(f"FIRMS NRT error: {r.text[:80]}")
        frame = pd.read_csv(io.StringIO(r.text))
        if len(frame):
            frames.append(frame)
    cols = ["latitude", "longitude", "acq_date", "confidence", "frp"]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    df = df[df["confidence"].astype(str).str.lower() != "l"].copy()
    df["acq_date"] = pd.to_datetime(df["acq_date"]).dt.date
    return df.reset_index(drop=True)


def predict_today(
    model,
    points: pd.DataFrame,
    amphoe_of_point: pd.Series,
    today: date,
    ndvi: np.ndarray | None = None,
    humidity: np.ndarray | None = None,
) -> dict[str, dict]:
    """Probability and level per district for the next 7 days, from hotspots up to `today`."""
    start = today - timedelta(days=HISTORY_DAYS - 1)
    grid = daily_grid(points, amphoe_of_point, model.amphoe_codes, start, today)

    expected_names = getattr(model, "feature_names", FEATURES)
    grid_ndvi = None
    if "ndvi" in expected_names:
        grid_ndvi = ndvi if ndvi is not None else np.full(grid.counts.shape, np.nan, dtype=np.float32)

    grid_hum = None
    if "rel_humidity" in expected_names:
        grid_hum = humidity if humidity is not None else np.full(grid.counts.shape, np.nan, dtype=np.float32)

    feats = features(
        grid,
        [c[:4] for c in model.amphoe_codes],
        model.clim,
        ndvi=grid_ndvi,
        humidity=grid_hum,
    )
    table = to_table(grid, feats)  # exactly one day (today) per district after the 30-day history
    probs = model.predict_proba(table)
    return {
        code: {"probability": round(float(p), 4), "level": model.level(float(p)), "hotspots_7d": int(c7)}
        for code, p, c7 in zip(table["amphoe"], probs, table["c7"])
    }


def empty_points() -> pd.DataFrame:
    return pd.DataFrame({"latitude": np.array([], float), "longitude": np.array([], float),
                         "acq_date": [], "confidence": [], "frp": np.array([], float)})
