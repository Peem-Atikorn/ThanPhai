import math
import numpy as np
from scipy import ndimage

from . import RainCell


def pixel_to_latlon_mercator(cy: float, cx: float, z: int, tx: int, ty: int, tile_size: int = 256) -> tuple[float, float]:
    """Inverse Web Mercator projection (EPSG:3857) converting tile pixel (cy, cx) to (lat, lon)."""
    w_pixels = tile_size * (2 ** z)
    gx = tx * tile_size + cx
    gy = ty * tile_size + cy
    lon = (gx / w_pixels) * 360.0 - 180.0
    n = math.pi - (2.0 * math.pi * gy) / w_pixels
    lat = math.degrees(math.atan(math.sinh(n)))
    return lat, lon


def detect_rain_cells(
    frame: np.ndarray,
    threshold: float = 20.0,
    min_pixels: int = 4,
    origin_lat: float = 20.5,
    origin_lon: float = 97.3,
    pixel_deg_lat: float = 0.03,
    pixel_deg_lon: float = 0.03,
    z: int | None = None,
    tx: int | None = None,
    ty: int | None = None,
    valid_mask: np.ndarray | None = None,
) -> list[RainCell]:
    """Segment frame into rain cells using connected components.

    threshold: dBZ or mm/h threshold to consider as rain cell
    min_pixels: minimum cell area
    z, tx, ty: Web Mercator tile zoom and coordinates for exact inverse Mercator projection.
    valid_mask: optional 2D boolean mask (e.g. Thailand territory). If provided, cells completely
                outside the mask are ignored, and centroids/areas are computed for the region
                overlapping the mask.
    """
    binary_mask = frame >= threshold
    labeled, num_features = ndimage.label(binary_mask)
    if num_features == 0:
        return []

    cells: list[RainCell] = []
    centroids = ndimage.center_of_mass(frame, labeled, range(1, num_features + 1))
    if isinstance(centroids, tuple):
        centroids = [centroids]

    for label_idx in range(1, num_features + 1):
        mask = labeled == label_idx
        if valid_mask is not None:
            active_mask = mask & valid_mask
            area = int(np.sum(active_mask))
            if area < min_pixels:
                continue
            cy, cx = ndimage.center_of_mass(frame, active_mask)
            peak = float(np.max(frame[active_mask]))
            mean = float(np.mean(frame[active_mask]))
        else:
            area = int(np.sum(mask))
            if area < min_pixels:
                continue
            cy, cx = centroids[label_idx - 1]
            peak = float(np.max(frame[mask]))
            mean = float(np.mean(frame[mask]))

        if z is not None and tx is not None and ty is not None:
            cell_lat, cell_lon = pixel_to_latlon_mercator(cy, cx, z=z, tx=tx, ty=ty, tile_size=frame.shape[0])
        else:
            # Approximate linear offset from top-left origin (fallback/testing)
            cell_lat = origin_lat - (cy * pixel_deg_lat)
            cell_lon = origin_lon + (cx * pixel_deg_lon)

        cells.append(
            RainCell(
                id=label_idx,
                centroid_y=float(cy),
                centroid_x=float(cx),
                centroid_lat=float(cell_lat),
                centroid_lon=float(cell_lon),
                peak_intensity=peak,
                mean_intensity=mean,
                area_pixels=area,
            )
        )

    # Sort by peak intensity descending
    cells.sort(key=lambda c: c.peak_intensity, reverse=True)
    return cells
