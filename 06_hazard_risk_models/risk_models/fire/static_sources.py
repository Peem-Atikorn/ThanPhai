"""Mask of persistent industrial heat sources (cement kilns, refineries, power plants).

FIRMS archive data labels them type 2 ("other static land source"), but the live NRT feed has no type
field. Cells (~1 km) where the archive saw type-2 detections are stored and filtered out of live data.
Found 2026-10-19: without the mask, Saraburi cement districts (แก่งคอย, พระพุทธบาท, บ้านหมอ) and
Si Racha refineries topped the wildfire risk list.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

CELL_DEG = 0.01  # ~1.1 km at Thai latitudes
MIN_DETECTIONS = 3  # ignore one-off type-2 labels


def _cells(lat: np.ndarray, lon: np.ndarray) -> list[str]:
    return [f"{a}_{b}" for a, b in zip(np.floor(lat / CELL_DEG).astype(int), np.floor(lon / CELL_DEG).astype(int))]


def build(points: pd.DataFrame) -> set[str]:
    static = points[points["type"] == 2]
    counts = pd.Series(_cells(static["latitude"].to_numpy(), static["longitude"].to_numpy())).value_counts()
    return set(counts[counts >= MIN_DETECTIONS].index)


def save(cells: set[str], path: Path) -> None:
    path.write_text(json.dumps({"cell_deg": CELL_DEG, "cells": sorted(cells)}), encoding="utf-8")


def load(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("cell_deg") != CELL_DEG:
        raise ValueError("static source mask was built with a different cell size")
    return set(data["cells"])


def drop_static(points: pd.DataFrame, cells: set[str]) -> pd.DataFrame:
    """Remove points that fall in a known static heat-source cell (or one of its 8 neighbours)."""
    if points.empty or not cells:
        return points
    lat_i = np.floor(points["latitude"].to_numpy() / CELL_DEG).astype(int)
    lon_i = np.floor(points["longitude"].to_numpy() / CELL_DEG).astype(int)
    hit = np.zeros(len(points), dtype=bool)
    # Neighbours too: a kiln's detections scatter across cell edges (VIIRS pixels are 375 m).
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            hit |= np.array([f"{a}_{b}" in cells for a, b in zip(lat_i + dy, lon_i + dx)])
    return points[~hit].reset_index(drop=True)
