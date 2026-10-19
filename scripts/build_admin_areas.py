"""Build data/processed/admin_areas.gpkg, admin_areas_web.gpkg, and gazetteer.json from the HDX download.

Run from the repo root:  .venv/Scripts/python scripts/build_admin_areas.py
"""

import sys
import time
from pathlib import Path

from geo_integration.boundaries import build

ROOT = Path(__file__).resolve().parent.parent
GDB = ROOT / "data" / "raw" / "hdx" / "gdb" / "tha_admin_boundaries.gdb"
OUT = ROOT / "data" / "processed"

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if not GDB.exists():
        sys.exit(f"Missing {GDB}. Download tha_admin_boundaries.gdb.zip from https://data.humdata.org/dataset/cod-ab-tha")
    start = time.time()
    counts = build(GDB, OUT)
    print(f"built in {time.time() - start:.0f}s: {counts}")
    for f in sorted(OUT.iterdir()):
        print(f"  {f.name:<24} {f.stat().st_size / 1e6:6.1f} MB")
