import numpy as np
import pytest

from risk_models.nowcast import (
    RainCell,
    detect_rain_cells,
    direction_degrees_to_thai,
    estimate_motion,
    extrapolate_frames,
    track_cells_to_districts,
)


def test_direction_degrees_to_thai():
    # 0 is North, 90 is East, 180 is South, 270 is West
    assert direction_degrees_to_thai(0) == "เหนือ"
    assert direction_degrees_to_thai(45) == "ตะวันออกเฉียงเหนือ"
    assert direction_degrees_to_thai(90) == "ตะวันออก"
    assert direction_degrees_to_thai(135) == "ตะวันออกเฉียงใต้"
    assert direction_degrees_to_thai(180) == "ใต้"
    assert direction_degrees_to_thai(225) == "ตะวันตกเฉียงใต้"
    assert direction_degrees_to_thai(270) == "ตะวันตก"
    assert direction_degrees_to_thai(315) == "ตะวันตกเฉียงเหนือ"


def test_estimate_motion_synthetic_shift():
    # Grid: 100 x 100 pixels, 1 pixel = 1 km
    frame1 = np.zeros((100, 100), dtype=float)
    # Blob centered at (y=40, x=40)
    frame1[35:45, 35:45] = 40.0

    # In 10 minutes, blob moves +5 pixels in x (East) and +5 pixels in y (South in image coordinates)
    frame2 = np.zeros((100, 100), dtype=float)
    frame2[40:50, 40:50] = 40.0

    motion = estimate_motion(frame1, frame2, pixel_size_km=1.0, interval_min=10.0)
    assert motion.dx == pytest.approx(5.0, abs=1.0)
    assert motion.dy == pytest.approx(5.0, abs=1.0)
    # Distance = sqrt(5^2 + 5^2) = 7.07 km in 10 min -> 42.4 km/h
    assert motion.speed_kmh == pytest.approx(42.4, abs=5.0)
    # Moving Southeast (increasing x and increasing y in image coordinates)
    assert motion.direction_th == "ตะวันออกเฉียงใต้"


def test_extrapolate_frames():
    frame = np.zeros((50, 50), dtype=float)
    frame[10:15, 10:15] = 50.0

    # dx = 2, dy = 0 (moving East)
    forecasts = extrapolate_frames(frame, dx=2.0, dy=0.0, steps=3)
    assert len(forecasts) == 3

    # Step 1 (+10 min): x shifted by +2
    assert forecasts[0][10:15, 12:17].mean() == pytest.approx(50.0)
    # Step 2 (+20 min): x shifted by +4
    assert forecasts[1][10:15, 14:19].mean() == pytest.approx(50.0)
    # Step 3 (+30 min): x shifted by +6
    assert forecasts[2][10:15, 16:21].mean() == pytest.approx(50.0)


def test_detect_rain_cells():
    frame = np.zeros((60, 60), dtype=float)
    # Cell 1: moderate rain
    frame[10:20, 10:20] = 30.0
    # Cell 2: heavy rain
    frame[40:55, 40:55] = 55.0

    cells = detect_rain_cells(frame, threshold=20.0, min_pixels=10)
    assert len(cells) == 2

    # Verify attributes
    c_heavy = max(cells, key=lambda c: c.peak_intensity)
    assert c_heavy.peak_intensity == 55.0
    assert c_heavy.area_pixels >= 100
    assert 40 <= c_heavy.centroid_y <= 55
    assert 40 <= c_heavy.centroid_x <= 55


def test_track_cells_to_districts():
    cell = RainCell(
        id=1,
        centroid_y=20.0,
        centroid_x=20.0,
        centroid_lat=13.75,
        centroid_lon=100.50,
        peak_intensity=45.0,
        mean_intensity=35.0,
        area_pixels=50,
    )

    # Districts with lat/lon: Bang Na is South-East of centroid at ~13.66, 100.60
    districts = [
        {"name_th": "บางนา", "lat": 13.66, "lon": 100.60},
        {"name_th": "ดอนเมือง", "lat": 13.91, "lon": 100.59},  # North, shouldn't hit
    ]

    # Moving South-East at 30 km/h:
    # vector (dlat=-0.1, dlon=+0.1) per hour
    targets = track_cells_to_districts(
        cells=[cell],
        speed_kmh=30.0,
        direction_deg=135.0,
        districts=districts,
        max_horizon_min=60,
    )

    assert len(targets) >= 1
    hit = next((t for t in targets if t["amphoe_th"] == "บางนา"), None)
    assert hit is not None
    assert 5 <= hit["eta_min"] <= 60


def test_decode_png_radar():
    import struct
    import zlib
    from risk_models.nowcast import decode_png_radar

    # Create synthetic 256x256 RGBA PNG
    raw_rgba = np.zeros((256, 256 * 4 + 1), dtype=np.uint8)
    raw_rgba[50:60, 51:101] = 255  # 50 dBZ rain
    compressed = zlib.compress(raw_rgba.tobytes())
    fake_png = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I4sIIBBBBB", 13, b"IHDR", 256, 256, 8, 6, 0, 0, 0)
        + b"crc1"
        + struct.pack(">I4s", len(compressed), b"IDAT")
        + compressed
        + b"crc2"
    )

    arr = decode_png_radar(fake_png)
    assert arr.shape == (256, 256)
    assert arr[55, 20] == pytest.approx(65.0, abs=1.0)
    assert arr[0, 0] == 0.0

    # Corrupt or invalid PNG returns zeroes without crashing
    bad_arr = decode_png_radar(b"not a png")
    assert bad_arr.shape == (256, 256)
    assert bad_arr.max() == 0.0
