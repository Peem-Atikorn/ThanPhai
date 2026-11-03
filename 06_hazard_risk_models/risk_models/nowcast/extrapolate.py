import numpy as np
from scipy import ndimage


def extrapolate_frames(
    frame: np.ndarray,
    dx: float,
    dy: float,
    steps: int = 6,
) -> list[np.ndarray]:
    """Extrapolate radar frames forward in time by applying continuous spatial shifts.
    
    steps=6 typically corresponds to +10, +20, +30, +40, +50, +60 minutes.
    """
    forecasts: list[np.ndarray] = []
    for step in range(1, steps + 1):
        shift_y = dy * step
        shift_x = dx * step
        shifted = ndimage.shift(frame, shift=[shift_y, shift_x], mode="constant", cval=0.0, order=1)
        forecasts.append(shifted)
    return forecasts
