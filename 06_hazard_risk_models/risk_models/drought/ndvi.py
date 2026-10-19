"""District NDVI from MODIS MOD13A3 (monthly, 1 km) and the Vegetation Condition Index (VCI) for drought.

VCI (Kogan 1995) = (NDVI_now - NDVI_min) / (NDVI_max - NDVI_min) * 100, where min/max are the same
calendar month in earlier years. Low VCI means vegetation is much browner than usual for the season.

Done on district means, not per pixel, to keep storage tiny (928 numbers per month).
Floods also lower NDVI (water is dark in near-infrared), so callers must not read low VCI as drought
where floods were seen; see HazardService.
"""

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio.features import rasterize
from rasterio.transform import from_origin

# MODIS sinusoidal grid (same for all MODIS land tiles).
SINU = "+proj=sinu +lon_0=0 +x_0=0 +y_0=0 +R=6371007.181 +units=m +no_defs"
TILE_SIZE_M = 1111950.5197665233
PIXELS = 1200  # 1 km product
PIXEL_M = TILE_SIZE_M / PIXELS
X_MIN, Y_MAX = -20015109.355798, 10007554.677899
# Tiles covering Thailand (h27v06 is needed for the far north above ~20° N).
THAILAND_TILES = ("h27v06", "h27v07", "h28v07", "h27v08", "h28v08")
NDVI_SDS, RELIABILITY_SDS = "1 km monthly NDVI", "1 km monthly pixel reliability"
NDVI_SCALE, NDVI_FILL = 10000.0, -3000
GOOD_RELIABILITY = (0, 1)  # 0 good, 1 marginal; 2 snow/ice, 3 cloudy are dropped
MIN_GOOD_PIXELS = 20  # fewer good pixels than this in a district = no value for that month


def tile_transform(tile: str):
    h, v = int(tile[1:3]), int(tile[4:6])
    return from_origin(X_MIN + h * TILE_SIZE_M, Y_MAX - v * TILE_SIZE_M, PIXEL_M, PIXEL_M)


def district_index(amphoe: gpd.GeoDataFrame, tile: str) -> np.ndarray:
    """1200x1200 array: index of the district covering each pixel (-1 outside every district)."""
    shapes = amphoe.to_crs(SINU).geometry
    return rasterize(((geom, i) for i, geom in enumerate(shapes)), out_shape=(PIXELS, PIXELS),
                     transform=tile_transform(tile), fill=-1, dtype="int32")


def read_ndvi(path: Path) -> np.ndarray:
    """NDVI as float32 with NaN for fill values and cloudy/snow pixels."""
    from pyhdf.SD import SD, SDC  # HDF4; only needed when building the data, not in the API

    sd = SD(str(path), SDC.READ)
    try:
        raw = sd.select(NDVI_SDS)[:].astype(np.float32)
        reliability = sd.select(RELIABILITY_SDS)[:]
    finally:
        sd.end()
    ndvi = raw / NDVI_SCALE
    ndvi[(raw == NDVI_FILL) | ~np.isin(reliability, GOOD_RELIABILITY)] = np.nan
    return ndvi


def district_means(tiles: dict[str, np.ndarray], indexes: dict[str, np.ndarray], n_districts: int) -> np.ndarray:
    """Mean NDVI per district over all tiles; NaN where too few good pixels."""
    total = np.zeros(n_districts, dtype=np.float64)
    count = np.zeros(n_districts, dtype=np.int64)
    for tile, ndvi in tiles.items():
        idx = indexes[tile]
        ok = (idx >= 0) & np.isfinite(ndvi)
        total += np.bincount(idx[ok], weights=ndvi[ok], minlength=n_districts)
        count += np.bincount(idx[ok], minlength=n_districts)
    means = np.full(n_districts, np.nan)
    enough = count >= MIN_GOOD_PIXELS
    means[enough] = total[enough] / count[enough]
    return means


@dataclass(frozen=True)
class VciResult:
    vci: np.ndarray  # 0-100, NaN where unknown
    ndvi_now: np.ndarray
    ndvi_min: np.ndarray
    ndvi_max: np.ndarray
    ndvi_mean: np.ndarray
    years: int  # how many past years went into min/max

    @property
    def deficit(self) -> np.ndarray:
        """How far NDVI is below the normal for this month (positive = browner than usual).

        Needed next to VCI: in the rainy season district NDVI barely varies (range ~0.03-0.1), so a drop
        of 0.001 can give VCI 0 while the land is still green (seen 2026-09 in บางขัน: 0.813 vs 0.812)."""
        return self.ndvi_mean - self.ndvi_now


def vci(now: np.ndarray, history: np.ndarray, min_years: int = 5) -> VciResult:
    """history: [years, districts] NDVI for the same calendar month in past years."""
    valid_years = np.isfinite(history).sum(axis=0)
    with np.errstate(all="ignore"):
        lo, hi = np.nanmin(history, axis=0), np.nanmax(history, axis=0)
        # Clip: a month greener or browner than every past year is VCI 100 or 0, not 130 or -20.
        value = np.clip((now - lo) / (hi - lo) * 100.0, 0.0, 100.0)
    value[(valid_years < min_years) | ~np.isfinite(now) | (hi - lo < 0.02)] = np.nan
    with np.errstate(all="ignore"):
        mean = np.nanmean(history, axis=0)
    return VciResult(value, now, lo, hi, mean, int(history.shape[0]))
