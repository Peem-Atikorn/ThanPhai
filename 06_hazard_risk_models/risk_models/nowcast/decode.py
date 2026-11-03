import io
import numpy as np
from PIL import Image

# RainViewer Universal Blue color scheme lookup table (packed 0xRRGGBB -> dBZ).
# Extracted from official RainViewer API colors table.
UNIVERSAL_BLUE_LUT: dict[int, float] = {
    0x636159: -10.0, 0x66635a: -9.0, 0x69665c: -8.0, 0x6c685d: -7.0, 0x6f6b5f: -6.0,
    0x726e61: -5.0, 0x757062: -4.0, 0x787364: -3.0, 0x7c7565: -2.0, 0x7f7867: -1.0,
    0x827b69: 0.0, 0x857d6a: 1.0, 0x88806c: 2.0, 0x8b826d: 3.0, 0x8e856f: 4.0,
    0x928871: 5.0, 0x9e9375: 6.0, 0xaa9e79: 7.0, 0xb6a97e: 8.0, 0xc2b482: 9.0,
    0xcec087: 10.0, 0xd2c48b: 11.0, 0xd6c88f: 12.0, 0xdacc93: 13.0, 0xded097: 14.0,
    0x88ddee: 15.0, 0x6cd1eb: 16.0, 0x51c5e8: 17.0, 0x36bae5: 18.0, 0x1baee2: 19.0,
    0x00a3e0: 20.0, 0x009ad5: 21.0, 0x0091ca: 22.0, 0x0088bf: 23.0, 0x007fb4: 24.0,
    0x0077aa: 25.0, 0x0070a3: 26.0, 0x00699c: 27.0, 0x006295: 28.0, 0x005b8e: 29.0,
    0x005588: 30.0, 0x005180: 31.0, 0x004e78: 32.0, 0x004a70: 33.0, 0x004768: 34.0,
    0xffee00: 35.0, 0xffe000: 36.0, 0xffd200: 37.0, 0xffc500: 38.0, 0xffb700: 39.0,
    0xffaa00: 40.0, 0xff9f00: 41.0, 0xff9500: 42.0, 0xff8b00: 43.0, 0xff8100: 44.0,
    0xff4400: 45.0, 0xf23600: 46.0, 0xe62800: 47.0, 0xd91b00: 48.0, 0xcd0d00: 49.0,
    0xc10000: 50.0, 0xa80000: 51.0, 0x8f0000: 52.0, 0x760000: 53.0, 0x5d0000: 54.0,
    0xffaaff: 55.0, 0xff9fff: 56.0, 0xff95ff: 57.0, 0xff8bff: 58.0, 0xff81ff: 59.0,
    0xff77ff: 60.0, 0xff6cff: 61.0, 0xff62ff: 62.0, 0xff58ff: 63.0, 0xff4eff: 64.0,
    0xffffff: 65.0,
}


def decode_png_radar(png_bytes: bytes) -> np.ndarray:
    """Decode 256x256 radar PNG tile into 2D float array (reflectivity dBZ).

    Uses Pillow to decode PNG data, then maps pixel colors to radar reflectivity (dBZ)
    using the official RainViewer Universal Blue color palette and Black & White scheme.
    Returns a 2D float array of reflectivity dBZ (0.0 to 65.0).
    """
    if len(png_bytes) < 8 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return np.zeros((256, 256), dtype=float)

    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        rgba = np.array(img, dtype=np.uint8)
    except Exception:
        return np.zeros((256, 256), dtype=float)

    if rgba.ndim != 3 or rgba.shape[2] != 4:
        return np.zeros((256, 256), dtype=float)

    r = rgba[:, :, 0]
    g = rgba[:, :, 1]
    b = rgba[:, :, 2]
    a = rgba[:, :, 3]

    dbz = np.zeros(rgba.shape[:2], dtype=float)

    # Check for Black and White scheme: R == G == B and A > 0 -> dBZ = R - 32
    bw_mask = (a > 0) & (r == g) & (g == b)
    if bw_mask.sum() > 0 and (bw_mask.sum() / max(1, (a > 0).sum())) > 0.8:
        valid_bw = bw_mask & (r >= 32)
        dbz[valid_bw] = (r[valid_bw].astype(float) - 32.0)
        return np.clip(dbz, 0.0, 65.0)

    # Universal Blue scheme: map packed 0xRRGGBB to dBZ
    packed_rgb = (r.astype(np.uint32) << 16) | (g.astype(np.uint32) << 8) | b.astype(np.uint32)
    has_alpha = a > 0

    for color_key, val in UNIVERSAL_BLUE_LUT.items():
        if val > 0:
            match = has_alpha & (packed_rgb == color_key)
            dbz[match] = val

    return np.clip(dbz, 0.0, 65.0)
