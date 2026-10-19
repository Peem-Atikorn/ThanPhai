"""Aggregate mapped records into per-area features for the risk models and decision engine."""

import pandas as pd

AREA_COLUMN = {"tambon": "tambon_code", "amphoe": "amphoe_code", "province": "province_code"}


def _by_area(mapped: pd.DataFrame, level: str) -> pd.DataFrame:
    column = AREA_COLUMN[level]
    return mapped[mapped[column].notna()].rename(columns={column: "area_code"})


def flood_features(water_levels: pd.DataFrame, level: str = "amphoe") -> pd.DataFrame:
    """Worst station per area. Stations with an unknown bank are counted but never drive above_bank_m."""
    df = _by_area(water_levels, level)
    if df.empty:
        return pd.DataFrame(columns=["area_code", "above_bank_m", "stations", "stations_with_bank"])
    return (
        df.groupby("area_code")
        .agg(
            above_bank_m=("extra_above_bank_m", "max"),
            stations=("station_id", "nunique"),
            stations_with_bank=("extra_above_bank_m", "count"),
        )
        .reset_index()
    )


def rain_features(rain: pd.DataFrame, level: str = "amphoe") -> pd.DataFrame:
    df = _by_area(rain, level)
    if df.empty:
        return pd.DataFrame(columns=["area_code", "rain_24h", "rain_24h_mean", "stations"])
    return (
        df.groupby("area_code")
        .agg(rain_24h=("value", "max"), rain_24h_mean=("value", "mean"), stations=("station_id", "nunique"))
        .reset_index()
    )


def fire_features(hotspots: pd.DataFrame, area_codes: list[str], level: str = "amphoe") -> pd.DataFrame:
    """Hotspot count per area, with 0 for areas that have none (absence of hotspots is real data)."""
    df = _by_area(hotspots, level)
    counts = df.groupby("area_code").size().rename("hotspots_24h")
    frp = df.groupby("area_code")["value"].sum().rename("frp_sum_mw")
    out = pd.DataFrame({"area_code": area_codes}).set_index("area_code")
    out = out.join(counts).join(frp).fillna({"hotspots_24h": 0, "frp_sum_mw": 0.0})
    out["hotspots_24h"] = out["hotspots_24h"].astype(int)
    return out.reset_index()
