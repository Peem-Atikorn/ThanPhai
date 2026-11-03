"""Rain radar nowcasting module.

Calculates radar rain-cell motion using 2D spatial cross-correlation via FFT,
extrapolates radar frames +10 to +60 minutes ahead, and identifies target districts
in the projected path.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MotionVector:
    dx: float  # pixels per interval
    dy: float  # pixels per interval
    speed_kmh: float
    direction_deg: float
    direction_th: str


@dataclass(frozen=True)
class RainCell:
    id: int
    centroid_y: float
    centroid_x: float
    centroid_lat: float
    centroid_lon: float
    peak_intensity: float
    mean_intensity: float
    area_pixels: int


from .cells import detect_rain_cells
from .decode import decode_png_radar
from .extrapolate import extrapolate_frames
from .motion import direction_degrees_to_thai, estimate_motion
from .tracker import track_cells_to_districts

__all__ = [
    "MotionVector",
    "RainCell",
    "decode_png_radar",
    "detect_rain_cells",
    "direction_degrees_to_thai",
    "estimate_motion",
    "extrapolate_frames",
    "track_cells_to_districts",
]
