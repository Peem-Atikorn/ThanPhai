"""Download FIRMS VIIRS hotspot history (2021-2025) for the fire model.

Run from the repo root (needs FIRMS_MAP_KEY in .env):
    .venv/Scripts/python scripts/download_firms_history.py
"""

import asyncio
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("FIRMS_MAP_KEY="):
        os.environ.setdefault("FIRMS_MAP_KEY", line.split("=", 1)[1].strip())

from risk_models.fire.download import download  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    start = time.time()
    paths = asyncio.run(download(ROOT / "data" / "raw" / "firms", date(2021, 1, 1), date(2025, 12, 31)))
    size = sum(p.stat().st_size for p in paths) / 1e6
    print(f"{len(paths)} chunks, {size:.1f} MB gzip, {time.time() - start:.0f}s")
    sys.exit(0)
