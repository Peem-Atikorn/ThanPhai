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
    """Estimate bulk motion displacement (dx, dy) between two frames using 2D spatial cross-correlation via FFT.

    Uses spatial cross-correlation with 2D parabolic peak interpolation around the correlation maximum
    to achieve sub-pixel displacement precision. This avoids the delta-spike and sinc-fringe artefacts
    of normalized phase correlation, allowing smooth quadratic interpolation down to fractional pixel shifts.
    
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
    corr = np.real(np.fft.ifft2(cross_power))

    # Shift zero-frequency component to the center of the spectrum
    h, w = frame1.shape
    max_idx = np.unravel_index(np.argmax(corr), corr.shape)

    r_idx, c_idx = int(max_idx[0]), int(max_idx[1])
    dy = float(r_idx)
    dx = float(c_idx)

    if dy > h // 2:
        dy -= h
    if dx > w // 2:
        dx -= w

    # Parabolic sub-pixel peak refinement for fractional displacement
    c_00 = corr[r_idx, c_idx]
    c_xm = corr[r_idx, (c_idx - 1) % w]
    c_xp = corr[r_idx, (c_idx + 1) % w]
    c_ym = corr[(r_idx - 1) % h, c_idx]
    c_yp = corr[(r_idx + 1) % h, c_idx]

    denom_x = c_xm - 2.0 * c_00 + c_xp
    delta_x = 0.5 * (c_xm - c_xp) / denom_x if abs(denom_x) > 1e-9 else 0.0

    denom_y = c_ym - 2.0 * c_00 + c_yp
    delta_y = 0.5 * (c_ym - c_yp) / denom_y if abs(denom_y) > 1e-9 else 0.0

    delta_x = max(-0.5, min(0.5, delta_x))
    delta_y = max(-0.5, min(0.5, delta_y))

    dx = float(dx + delta_x)
    dy = float(dy + delta_y)

    distance_km = math.hypot(dx, dy) * pixel_size_km
    speed_kmh = distance_km / (interval_min / 60.0) if interval_min > 0 else 0.0

    # Discard speeds exceeding ~120 km/h (unrealistic noise spikes)
    if speed_kmh > 120.0:
        return MotionVector(
            dx=0.0,
            dy=0.0,
            speed_kmh=0.0,
            direction_deg=0.0,
            direction_th="ทิศทางไม่แน่นอน",
        )

    # Image coordinates: +dx is East (90 deg), -dy is North (0 deg), +dy is South (180 deg)
    # math.atan2(dx, -dy) gives angle from North (0 deg) clockwise
    angle_rad = math.atan2(dx, -dy)
    angle_deg = (math.degrees(angle_rad) + 360.0) % 360.0

    direction_th = direction_degrees_to_thai(angle_deg) if speed_kmh >= 2.0 else "เกือบอยู่กับที่"

    return MotionVector(
        dx=dx,
        dy=dy,
        speed_kmh=speed_kmh,
        direction_deg=angle_deg,
        direction_th=direction_th,
    )
