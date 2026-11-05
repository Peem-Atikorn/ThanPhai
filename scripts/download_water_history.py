"""Download ThaiWater river-level and dam history, and Open-Meteo rain, for the water models (resumable).

Run from the repo root:
    .venv/Scripts/python scripts/download_water_history.py [--years 5] [--data-dir data] [--skip-rivers|dams|rain]
Writes data/raw/water_history/{stations.json, dams.json, rivers/<id>/<start>.json.gz, dams/<id>_<series>.json.gz,
rain/<lat>_<lon>.json.gz}. The rain step prints the Open-Meteo calls it used ("cost").
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from risk_models.water.download import client, dam_ids, download_dams, download_rain, download_rivers, river_stations

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=5, help="years of river history (ThaiWater keeps about 5 for RID)")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--skip-rivers", action="store_true")
    parser.add_argument("--skip-dams", action="store_true")
    parser.add_argument("--skip-rain", action="store_true", help="rain uses ~1,700 Open-Meteo calls for 5 years")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    out = args.data_dir / "raw" / "water_history"
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with client() as http:
        if not args.skip_dams:
            dams = dam_ids(http)
            (out / "dams.json").write_text(json.dumps(dams, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{len(dams)} dams", download_dams(out, [d["id"] for d in dams], list(range(1990, date.today().year + 1)), http), flush=True)
        if not args.skip_rivers:
            stations_path = out / "stations.json"
            if stations_path.exists():  # keep the same station list when resuming
                stations = json.loads(stations_path.read_text(encoding="utf-8"))
            else:
                stations = river_stations(http)
                stations_path.write_text(json.dumps(stations, ensure_ascii=False, indent=1), encoding="utf-8")
            end = date.today()
            print(f"{len(stations)} river stations", flush=True)
            print(download_rivers(out, stations, end - timedelta(days=365 * args.years), end, http), flush=True)
        if not args.skip_rain:
            stations = json.loads((out / "stations.json").read_text(encoding="utf-8"))
            end = date.today()
            # ERA5 reaches about 5 days back from today; later hours come back empty and are left missing.
            print("rain", download_rain(out, stations, end - timedelta(days=365 * args.years + 7), end, http), flush=True)
    print(f"done in {(time.time() - started) / 60:.0f} min", flush=True)
    sys.exit(0)
