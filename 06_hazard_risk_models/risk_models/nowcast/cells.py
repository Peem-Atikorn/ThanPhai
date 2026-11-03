import numpy as np
from scipy import ndimage

from . import RainCell


def detect_rain_cells(
    frame: np.ndarray,
    threshold: float = 20.0,
    min_pixels: int = 4,
    origin_lat: float = 20.5,
    origin_lon: float = 97.3,
    pixel_deg_lat: float = 0.03,
    pixel_deg_lon: float = 0.03,
) -> list[RainCell]:
    """Segment frame into rain cells using connected components.
    
    threshold: dBZ or mm/h threshold to consider as rain cell
    min_pixels: minimum cell area
    """
    binary_mask = frame >= threshold
    labeled, num_features = ndimage.label(binary_mask)
    if num_features == 0:
        return []

    cells: list[RainCell] = []
    # Calculate centers of mass for all labels
    centroids = ndimage.center_of_mass(frame, labeled, range(1, num_features + 1))
    if isinstance(centroids, tuple):
        centroids = [centroids]

    for label_idx in range(1, num_features + 1):
        mask = labeled == label_idx
        area = int(np.sum(mask))
        if area < min_pixels:
            continue

        cy, cx = centroids[label_idx - 1]
        peak = float(np.max(frame[mask]))
        mean = float(np.mean(frame[mask]))

        # Approximate lat/lon from top-left origin (lat decreases downward)
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
