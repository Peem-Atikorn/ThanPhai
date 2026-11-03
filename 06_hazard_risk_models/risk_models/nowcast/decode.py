import struct
import zlib
import numpy as np


def decode_png_radar(png_bytes: bytes) -> np.ndarray:
    """Decode 256x256 RGBA/palette PNG radar tile into 2D float array (reflectivity dBZ).
    
    Uses standard library zlib and numpy with zero external C-dependencies.
    Returns 256x256 array with values 0.0 to 65.0 dBZ.
    """
    if len(png_bytes) < 8 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return np.zeros((256, 256), dtype=float)

    pos = 8
    idat: list[bytes] = []
    width = height = 256
    color_type = 6  # RGBA default

    while pos + 8 <= len(png_bytes):
        length, = struct.unpack(">I", png_bytes[pos : pos + 4])
        chunk_type = png_bytes[pos + 4 : pos + 8]
        if chunk_type == b"IHDR":
            width, height, _, color_type = struct.unpack(">IIBB", png_bytes[pos + 8 : pos + 18])
        elif chunk_type == b"IDAT":
            idat.append(png_bytes[pos + 8 : pos + 8 + length])
        pos += 12 + length

    if not idat:
        return np.zeros((height, width), dtype=float)

    try:
        decompressed = zlib.decompress(b"".join(idat))
    except Exception:
        return np.zeros((height, width), dtype=float)

    bytes_per_pixel = 4 if color_type == 6 else (3 if color_type == 2 else 1)
    row_bytes = width * bytes_per_pixel + 1
    if len(decompressed) != height * row_bytes:
        return np.zeros((height, width), dtype=float)

    # Strip the 1-byte PNG scanline filter at the start of each row
    raw_rows = np.frombuffer(decompressed, dtype=np.uint8).reshape((height, row_bytes))[:, 1:]
    if bytes_per_pixel == 4:
        # RGBA: Alpha channel (channel 3) indicates rain presence and intensity
        rgba = raw_rows.reshape((height, width, 4))
        alpha = rgba[:, :, 3].astype(float)
        # Map alpha (0..255) to approximate radar reflectivity (0..65 dBZ)
        return (alpha / 255.0) * 65.0
    return raw_rows.astype(float)
