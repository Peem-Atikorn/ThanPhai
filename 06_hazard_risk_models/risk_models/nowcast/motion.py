import math
import numpy as np

from . import MotionVector

COMPASS_DIRECTIONS = [
    (22.5, "เหนือ"),
    (67.5, "ตะวันออกเฉียงเหนือ"),
    (112.5, "ตะวันออก"),
    (157.5, "ตะวันออกเฉียงใต้"),
    (202.5, "ใต้"),
    (247.5, "ตะวันตกเฉียงใต้"),
    (292.5, "ตะวันตก"),
    (337.5, "ตะวันตกเฉียงเหนือ"),
]


def direction_degrees_to_thai(deg: float) -> str:
    """Convert meteorological/compass heading (0=North, 90=East) to Thai name."""
    normalized = deg % 360.0
    for threshold, name in COMPASS_DIRECTIONS:
        if normalized < threshold:
            return name
    return "เหนือ"


def estimate_motion(
    frame1: np.ndarray,
    frame2: np.ndarray,
    pixel_size_km: float = 1.0,
    interval_min: float = 10.0,
) -> MotionVector:
    """Estimate bulk motion displacement (dx, dy) between two frames using phase cross-correlation.
    
    In image coordinates:
      dx > 0 means moving right (East)
      dy > 0 means moving down (South)
    """
    if frame1.shape != frame2.shape or frame1.size == 0:
        return MotionVector(dx=0.0, dy=0.0, speed_kmh=0.0, direction_deg=0.0, direction_th="ทิศทางไม่แน่นอน")

    # If both frames are nearly empty (no rain)
    if np.max(frame1) < 5.0 and np.max(frame2) < 5.0:
        return MotionVector(dx=0.0, dy=0.0, speed_kmh=0.0, direction_deg=0.0, direction_th="ไม่มีกลุ่มฝน")

    f1 = np.fft.fft2(frame1)
    f2 = np.fft.fft2(frame2)

    cross_power = f2 * np.conj(f1)
    abs_cp = np.abs(cross_power)
    abs_cp[abs_cp == 0] = 1e-9
    cross_power /= abs_cp

    corr = np.real(np.fft.ifft2(cross_power))

    # Shift zero-frequency component to the center of the spectrum
    h, w = frame1.shape
    max_idx = np.unravel_index(np.argmax(corr), corr.shape)

    dy = float(max_idx[0])
    dx = float(max_idx[1])

    if dy > h // 2:
        dy -= h
    if dx > w // 2:
        dx -= w

    distance_km = math.hypot(dx, dy) * pixel_size_km
    speed_kmh = distance_km / (interval_min / 60.0) if interval_min > 0 else 0.0

    # Image coordinates: +dx is East (90 deg), -dy is North (0 deg), +dy is South (180 deg)
    # math.atan2(dx, -dy) gives angle from North (0 deg) clockwise
    angle_rad = math.atan2(dx, -dy)
    angle_deg = (math.degrees(angle_rad) + 360.0) % 360.0

    direction_th = direction_degrees_to_thai(angle_deg) if distance_km > 0.1 else "อยู่กับที่"

    return MotionVector(
        dx=dx,
        dy=dy,
        speed_kmh=speed_kmh,
        direction_deg=angle_deg,
        direction_th=direction_th,
    )
