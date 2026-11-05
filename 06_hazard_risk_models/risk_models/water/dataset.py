"""Hourly river-level and daily dam-inflow tables from the downloaded ThaiWater history.

Rivers: every station is resampled to hourly means. Readings that jump more than MAX_JUMP_M from both neighbours
(sensor spikes) are dropped; after a step of more than MAX_STEP_M that lasts a week (a datum change: some stations jump
30 m in an hour) only the later part is kept; readings outside bank-40 m .. bank+15 m are dropped. The target is the
level change h hours ahead (not the level), so one model serves stations at any height.

Dams: daily inflow (million m³) from 2004; the target is the total inflow of the next 7 days. Years before 2018
have many missing days; rows whose 7-day target has a gap are not used.
"""

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

HORIZONS_H = (6, 12, 24)
LAGS_H = (1, 3, 6, 12, 24, 48, 72)
MAX_JUMP_M = 1.5
MAX_STEP_M = 3.0
STEP_WINDOW = np.timedelta64(7, "D")
FLOW_STEP_RATIO = 2.0
BANK_BELOW_M, BANK_ABOVE_M = 40.0, 15.0  # readings outside bank-40 m .. bank+15 m are not water
MAX_RUN_M = 5.0
RAIN_CELL_DEG = 0.5
# Some stations report 9999.9 (or similar) for "no reading".
SENTINEL_M = 999.0
DAM_HORIZON_D = 7
# 2002-2003 hold running totals since 1 January instead of daily values (e.g. Bhumibol 3,946 -> 4,005 million m³).
DAM_START = "2004-01-01"
DAM_SPIKE_FACTOR = 5

RIVER_FEATURES = [
    *(f"d{k}h" for k in LAGS_H),        # level now minus level k hours ago (m)
    "to_bank_m",                         # level minus bank (m); > 0 is over the bank
    "std24h",                            # how much the level moved in the last day
    "basin_d6h", "basin_d24h",           # mean change of the other stations in the same basin
    "rain_6h", "rain_24h", "rain_72h",   # rain at the station (mm, ERA5 0.5° cell); NaN when not downloaded
    "basin_rain_24h", "basin_rain_72h",  # mean rain over the basin's stations: water still on its way
    "doy_sin", "doy_cos", "hour_sin", "hour_cos",
]
# What the live forecast knows: one station's own recent readings. Other stations would take a request each and
# rain a second source, so the live model is trained on these alone (see the report for what that costs).
LIVE_RIVER_FEATURES = [*(f"d{k}h" for k in LAGS_H), "to_bank_m", "std24h", "doy_sin", "doy_cos", "hour_sin", "hour_cos"]
DAM_FEATURES = [
    "inflow_d0", "inflow_d1", "inflow_d2", "inflow_mean7", "inflow_mean30", "inflow_trend7",
    "released_mean7", "storage_pct", "storage_change7", "clim_next7", "doy_sin", "doy_cos",
]


def _read(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _flow_follows(before: np.ndarray, after: np.ndarray, level_rose: bool) -> bool:
    """True when the flow at least doubled with a rise (or halved with a fall) across a level step.

    A datum change moves the level, not the water: in this data flow stays about the same (บ้านนาสีทอง 19.6 -> 18.2
    m³/s across a 33 m step) or goes the other way, while floods take it up 3-30 times (K.37 456 -> 1,460 m³/s).
    """
    before, after = before[~np.isnan(before)], after[~np.isnan(after)]
    if len(before) < 2 or len(after) < 2:
        return False
    ratio = np.median(after) / np.median(before)
    return ratio >= FLOW_STEP_RATIO if level_rose else ratio <= 1 / FLOW_STEP_RATIO


def plausible(level, bank: float | None):
    """True where a level can be water at a station with this bank (bank-40 m .. bank+15 m); everywhere if no bank."""
    if bank is None:
        return np.ones_like(np.asarray(level, dtype=float), dtype=bool) if np.ndim(level) else True
    return (level > bank - BANK_BELOW_M) & (level < bank + BANK_ABOVE_M)


def station_series(folder: Path, bank: float | None = None) -> pd.Series:
    """One station's hourly level (m MSL), indexed by Thai local time, cleaned of spikes.

    `bank` (m MSL) is used only to tell a flood from a datum change: a flood stays within the plausible range.
    """
    rows = []
    for path in sorted(folder.glob("*.json.gz")):
        rows.extend(_read(path)["rows"])
    return clean_level(rows, bank)


def clean_level(rows: list, bank: float | None = None) -> pd.Series:
    """Hourly level (m MSL) from [time, level, flow] rows in Thai local time, cleaned as described in station_series.

    The live forecast cleans a station's last days of readings with this same function, so it sees what the model
    was trained on.
    """
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["time", "level", "flow"])
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["level"] = pd.to_numeric(df["level"], errors="coerce")
    df = df.dropna(subset=["time", "level"]).drop_duplicates("time").set_index("time").sort_index()
    level = df["level"][df["level"].abs() < SENTINEL_M]
    # A single reading far from both neighbours is a spike, not water. The newest reading has no neighbour after it
    # yet: it is kept only if it is close to the one before (live, a real jump is used once the next reading confirms
    # it; until then the forecast starts from the reading before).
    prev, nxt = level.shift(1), level.shift(-1)
    spike = ((level - prev).abs() > MAX_JUMP_M) & ((level - nxt).abs() > MAX_JUMP_M).where(nxt.notna(), True)
    level = level[~spike]
    # A lasting step of several metres between two readings is a new datum or a moved sensor, not water: keep only
    # the readings after the last step, which share today's datum and bank. It counts only when the week after it
    # sits MAX_STEP_M away from the week before, measured in time, not in readings: 24 readings are only 4-6 hours
    # at stations that report every 10-15 minutes, and a flash flood that stays up for a day is water. Steps across
    # a gap in the readings still count: in this data they are mostly sensors reinstalled on a new datum
    # (อ.นครชัยศรี went from -9.1 to 0.7 m after 15 hours off).
    flow = pd.to_numeric(df["flow"], errors="coerce").reindex(level.index)
    flow = flow.where(flow > 0).to_numpy()  # 0 and -9999 mean "no flow reading"
    values, times = level.to_numpy(), level.index.to_numpy()
    for i in reversed(np.flatnonzero(np.abs(np.diff(values)) > MAX_STEP_M) + 1):
        start, stop = times.searchsorted(times[i] - STEP_WINDOW), times.searchsorted(times[i] + STEP_WINDOW)
        before, after = values[start:i], values[i:stop]
        if not (len(before) and len(after) and abs(np.median(after) - np.median(before)) > MAX_STEP_M):
            continue
        # The flow moved with the level: a flood (or its end) that rose while the sensor was off. Flow is often
        # computed from the level (a rating curve), so it can follow a datum change too; a flood also has to stay
        # within the bank range on both sides (บ้านท่าบุญมี "rose" 15 -> 79 m beside an 18 m bank, flow 1.4 -> 71).
        if (_flow_follows(flow[start:i], flow[i:stop], np.median(after) > np.median(before))
                and plausible(np.median(before), bank) and plausible(np.median(after), bank)):
            continue
        level = level.iloc[i:]
        break
    hourly = level.resample("1h").mean()
    # Runs of bad readings survive the checks above (Ct.4 sat 20 m low for 12 hours on 2023-04-04): hours more than
    # MAX_RUN_M from the median of the 49 hours up to them. A run is dropped when the level jumped into it by more than
    # MAX_STEP_M in an hour; a river that got there smoothly is water (a flash flood rising 5-8 m in a few hours, or
    # the fall after a long flood, which sits below a median still full of flood hours). Only the past is used, so
    # an hour is cleaned the same way in training and live, where the hours after it are not known yet.
    median = hourly.rolling(49, min_periods=12).median()
    far = (hourly - median).abs() > MAX_RUN_M
    if far.any():
        jump_in = (hourly - hourly.ffill().shift(1)).abs()
        run = (far != far.shift()).cumsum()[far]
        entered = jump_in[far].groupby(run).first()
        kept = entered.index[entered.fillna(0) <= MAX_STEP_M]
        hourly = hourly.where(~far | run.reindex(hourly.index).isin(kept))
    # Fill gaps of up to 3 hours only; longer gaps stay missing and their rows are not used. (interpolate(limit=3)
    # alone would also fill the first 3 hours of every longer gap, e.g. at stations that report once a day.)
    gap_hours = hourly.isna().groupby(hourly.notna().cumsum()).transform("sum")
    return hourly.interpolate(limit_area="inside").where(hourly.notna() | (gap_hours <= 3))


def rain_cell(lat: float, lon: float) -> tuple[float, float]:
    """The 0.5° cell centre a station's rain is read from (ERA5 itself is 0.25°)."""
    return round(round(lat / RAIN_CELL_DEG) * RAIN_CELL_DEG, 2), round(round(lon / RAIN_CELL_DEG) * RAIN_CELL_DEG, 2)


def station_rain(raw_dir: Path) -> pd.DataFrame | None:
    """Hourly rain (mm) per station, read from its 0.5° cell file; None when no rain was downloaded."""
    folder = raw_dir / "rain"
    if not folder.exists():
        return None
    cells = {}
    for path in folder.glob("*.json.gz"):
        d = _read(path)
        cells[(d["lat"], d["lon"])] = pd.Series(pd.to_numeric(pd.Series(d["precipitation"]), errors="coerce").to_numpy(),
                                                index=pd.to_datetime(d["time"]), dtype=float)
    stations = json.loads((raw_dir / "stations.json").read_text(encoding="utf-8"))
    columns = {s["id"]: cells[c] for s in stations if (c := rain_cell(s["lat"], s["lon"])) in cells}
    return pd.DataFrame(columns) if columns else None


def own_features(lv: pd.Series, bank: float) -> pd.DataFrame:
    """The features a station gives by itself, from its hourly level: past changes, distance to the bank, how much it
    moved in the last day, and the time of year and day. Shared by training and the live forecast."""
    df = pd.DataFrame({"level": lv})
    for k in LAGS_H:
        df[f"d{k}h"] = lv - lv.shift(k)
    df["to_bank_m"] = lv - bank
    df["std24h"] = lv.rolling(24, min_periods=12).std()
    doy = df.index.dayofyear.to_numpy() * 2 * np.pi / 365.25
    hour = df.index.hour.to_numpy() * 2 * np.pi / 24
    df["doy_sin"], df["doy_cos"] = np.sin(doy), np.cos(doy)
    df["hour_sin"], df["hour_cos"] = np.sin(hour), np.cos(hour)
    return df


def live_features(rows: list, bank: float, now: pd.Timestamp, max_age_h: int = 3) -> pd.Series | None:
    """The feature row of a station's latest hour, from its recent [time, level, flow] rows (Thai local time).

    None when the latest cleaned hour is older than `max_age_h` before `now` (naive Thai time), or when the 1-hour or
    24-hour change cannot be computed: training never used such rows either. Other stations and rain are not known
    live, so their features are left empty.
    """
    lv = clean_level(rows, bank)
    if lv.empty:
        return None
    lv = lv.where(plausible(lv, bank))
    df = own_features(lv, bank)
    df = df.dropna(subset=["level", "d1h", "d24h"])
    if df.empty or now - df.index[-1] > pd.Timedelta(hours=max_age_h):
        return None
    row = df.iloc[-1].copy()
    for name in RIVER_FEATURES:
        row[name] = row.get(name, np.nan)
    row["time"] = df.index[-1]
    return row


def river_table(raw_dir: Path, rain: pd.DataFrame | None = None) -> pd.DataFrame:
    """Rows of (station, hour) with RIVER_FEATURES and targets y{h} = level(t+h) - level(t)."""
    stations = json.loads((raw_dir / "stations.json").read_text(encoding="utf-8"))
    series = {}
    for s in stations:
        bank = s.get("bank_msl")
        if bank is None:
            continue
        lv = station_series(raw_dir / "rivers" / s["id"], bank)
        if lv.empty:
            continue
        # where(), not a filter: every shift() and rolling() below counts rows as hours, so the index must stay hourly.
        lv = lv.where(plausible(lv, bank))
        if lv.count() >= 24 * 60:  # at least ~60 days of readings
            series[s["id"]] = lv
    frames = []
    # Basin signal: mean change of every station in the basin (each station's own change is removed below).
    changes = {sid: pd.DataFrame({"d6": lv - lv.shift(6), "d24": lv - lv.shift(24)}) for sid, lv in series.items()}
    # A station without a basin name has no basin signal (NaN), rather than sharing one with every other such station.
    basin_of = {s["id"]: s.get("basin_th") or None for s in stations}
    basin_sum, basin_n = {}, {}
    for sid, ch in changes.items():
        b = basin_of[sid]
        if b is None:
            continue
        filled = ch.fillna(0.0)
        count = ch.notna().astype(float)
        basin_sum[b] = filled if b not in basin_sum else basin_sum[b].add(filled, fill_value=0.0)
        basin_n[b] = count if b not in basin_n else basin_n[b].add(count, fill_value=0.0)
    meta = {s["id"]: s for s in stations}
    # Hourly basin rain: the mean over the basin's stations (each station's cell), one series per basin.
    basin_rain = {}
    if rain is not None:
        for b in set(basin_of.values()) - {None}:
            cols = [sid for sid in rain.columns if basin_of.get(sid) == b]
            if cols:
                basin_rain[b] = rain[cols].mean(axis=1)
    for sid, lv in series.items():
        s = meta[sid]
        df = own_features(lv, s["bank_msl"])
        b, ch = basin_of[sid], changes[sid]
        if b is None:
            df["basin_d6h"] = df["basin_d24h"] = np.nan
        else:
            others_n = basin_n[b].reindex(df.index) - ch.notna().astype(float).reindex(df.index)
            others_sum = basin_sum[b].reindex(df.index) - ch.fillna(0.0).reindex(df.index)
            df["basin_d6h"] = (others_sum["d6"] / others_n["d6"]).where(others_n["d6"] > 0)
            df["basin_d24h"] = (others_sum["d24"] / others_n["d24"]).where(others_n["d24"] > 0)
        # A sum needs every hour of its window: hours ERA5 has not published yet (the last ~5 days) are missing,
        # and counting them as 0 mm would look like a dry spell.
        if rain is not None and sid in rain.columns:
            r = rain[sid].reindex(df.index)
            df["rain_6h"], df["rain_24h"], df["rain_72h"] = (r.rolling(n, min_periods=n).sum() for n in (6, 24, 72))
            if b in basin_rain:
                basin = basin_rain[b].reindex(df.index)
                df["basin_rain_24h"], df["basin_rain_72h"] = (basin.rolling(n, min_periods=n).sum() for n in (24, 72))
            else:
                df["basin_rain_24h"] = df["basin_rain_72h"] = np.nan
        else:
            df["rain_6h"] = df["rain_24h"] = df["rain_72h"] = df["basin_rain_24h"] = df["basin_rain_72h"] = np.nan
        for h in HORIZONS_H:
            df[f"y{h}"] = lv.shift(-h) - lv
        df["station_id"] = sid
        df["bank_msl"] = s["bank_msl"]
        frames.append(df.dropna(subset=["level", "d1h", "d24h"]).reset_index(names="time"))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _dam_series(raw_dir: Path, dam_id: int, series: str) -> pd.Series:
    path = raw_dir / "dams" / f"{dam_id}_{series}.json.gz"
    if not path.exists():
        return pd.Series(dtype=float)
    data = (_read(path)["raw"] or {}).get("data") or {}
    rows = []
    for item in data.get("graph_data") or []:
        for row in item.get("data") or []:
            rows.append((row.get("date"), row.get("value")))
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["date", "value"])
    # Dates come as "YYYY-MM-DD..." (sometimes with a time and +07:00); the day is all that matters.
    index = pd.to_datetime(df["date"].astype(str).str[:10], format="%Y-%m-%d", errors="coerce")
    s = pd.Series(pd.to_numeric(df["value"], errors="coerce").to_numpy(), index=index, dtype=float)
    s = s[s.index.notna()].sort_index()
    s = s[~s.index.duplicated()]
    # ThaiWater pads the current year to 31 Dec with empty days; stop at the last real value.
    return s.loc[: s.last_valid_index()].asfreq("1D") if s.notna().any() else pd.Series(dtype=float)


def dam_table(raw_dir: Path, fit_end: pd.Timestamp | None = None) -> pd.DataFrame:
    """Rows of (dam, day) with DAM_FEATURES and target y7 = inflow of the next 7 days (million m³).

    The spike limit and the storage capacity are taken from days before `fit_end` (the training years), so later
    years cannot shape the training rows; all days when it is None or the dam has no days before it.
    """
    dams = json.loads((raw_dir / "dams.json").read_text(encoding="utf-8"))
    frames = []

    def fit_part(s: pd.Series) -> pd.Series:
        part = s.loc[: fit_end - pd.Timedelta(days=1)] if fit_end is not None else s
        return part if part.notna().any() else s

    for d in dams:
        inflow = _dam_series(raw_dir, d["id"], "dam_inflow").loc[DAM_START:]
        if inflow.count() < 365 * 3:
            continue
        inflow = inflow.where(inflow >= 0)
        # A day far above anything else this dam has recorded is a typing or unit error, not a flood.
        inflow = inflow.where(inflow <= DAM_SPIKE_FACTOR * fit_part(inflow).quantile(0.99))
        released = _dam_series(raw_dir, d["id"], "dam_released").reindex(inflow.index)
        storage = _dam_series(raw_dir, d["id"], "dam_storage").reindex(inflow.index)
        df = pd.DataFrame({"inflow": inflow})
        df["inflow_d0"], df["inflow_d1"], df["inflow_d2"] = inflow, inflow.shift(1), inflow.shift(2)
        df["inflow_mean7"] = inflow.rolling(7, min_periods=5).mean()
        df["inflow_mean30"] = inflow.rolling(30, min_periods=20).mean()
        df["inflow_trend7"] = df["inflow_mean7"] - df["inflow_mean7"].shift(7)
        df["released_mean7"] = released.rolling(7, min_periods=5).mean()
        cap = fit_part(storage).max()
        df["storage_pct"] = storage / cap * 100 if cap and cap > 0 else np.nan
        df["storage_change7"] = storage - storage.shift(7)
        df["y7"] = inflow[::-1].rolling(DAM_HORIZON_D, min_periods=DAM_HORIZON_D).sum()[::-1].shift(-1)
        df["doy"] = df.index.dayofyear
        doy = df["doy"].to_numpy() * 2 * np.pi / 365.25
        df["doy_sin"], df["doy_cos"] = np.sin(doy), np.cos(doy)
        df["dam_id"] = d["id"]
        df["name_th"] = d.get("name_th")
        frames.append(df.dropna(subset=["inflow_d0", "inflow_mean7"]).reset_index(names="time"))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def add_dam_climatology(table: pd.DataFrame, train_end: pd.Timestamp) -> pd.DataFrame:
    """Mean 7-day inflow for each dam and day of year, from training years only (no look at later years)."""
    train = table[table["time"] + pd.Timedelta(days=DAM_HORIZON_D) < train_end]  # whole 7-day target before train_end
    parts = []
    for dam_id, rows in train.groupby("dam_id"):
        by_day = rows.groupby("doy")["y7"].mean().reindex(range(1, 367)).interpolate(limit_direction="both")
        smooth = by_day.rolling(15, center=True, min_periods=1).mean()  # a 15-day window evens out single storms
        parts.append(pd.DataFrame({"dam_id": dam_id, "doy": smooth.index, "clim_next7": smooth.to_numpy()}))
    clim = pd.concat(parts, ignore_index=True)
    return table.drop(columns=["clim_next7"], errors="ignore").merge(clim, on=["dam_id", "doy"], how="left")
