"""Count past landslides per district from DMR's landslide inventory (ร่องรอยแผ่นดินถล่ม, 1989-2019, CC BY).

Writes data/processed/landslide_districts.json for the API: districts with many past landslides are where heavy
rain is checked for landslides. DMR updates the inventory every ~5 years, so re-run rarely.

Run from the repo root:
    .venv/Scripts/python scripts/build_landslide_districts.py
"""

import csv
import io
import json
from collections import Counter
from datetime import date
from pathlib import Path

import geopandas as gpd
import httpx

from geo_integration.area_mapper import AreaMapper
from geo_integration.gazetteer import Gazetteer

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "landslide"
PROCESSED = ROOT / "data" / "processed"
INVENTORY_URL = ("https://data.dmr.go.th/dataset/258c96e9-67cc-4605-8def-9e5cb4e23da3/resource/"
                 "5d2eab9a-90b3-4816-a19c-2fdedf41100a/download/data_05010102-.csv")
# The inventory gives UTM zone 47N coordinates for the whole country, including the far east (Trat, Ubon).
UTM_CRS = "EPSG:32647"


def download() -> str:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / "landslide_inventory.csv"
    if not path.exists():
        response = httpx.get(INVENTORY_URL, timeout=300, follow_redirects=True)
        response.raise_for_status()
        path.write_bytes(response.content)
    return path.read_bytes().decode("utf-8-sig")


def main() -> None:
    rows = [r for r in csv.DictReader(io.StringIO(download())) if r["utm_e"] and r["utm_n"]]
    points = gpd.GeoSeries(gpd.points_from_xy([float(r["utm_e"]) for r in rows], [float(r["utm_n"]) for r in rows]),
                           crs=UTM_CRS).to_crs("EPSG:4326")
    mapper = AreaMapper.from_gpkg(PROCESSED / "admin_areas.gpkg")
    mapped = mapper.assign_points(points.y.tolist(), points.x.tolist())
    gazetteer = Gazetteer.load(PROCESSED / "gazetteer.json")

    # Check the coordinates against the district names DMR wrote next to them.
    names = [gazetteer.by_code[c].name_th if isinstance(c, str) and c in gazetteer.by_code else None for c in mapped["amphoe_code"]]
    agree = sum(1 for r, n in zip(rows, names) if n and r["district"] and (r["district"] in n or n in r["district"]))
    print(f"{len(rows)} landslide traces, {mapped['amphoe_code'].notna().sum()} inside Thailand, "
          f"{agree / len(rows):.0%} in a district with the same name as DMR's")

    counts = Counter(c for c in mapped["amphoe_code"] if isinstance(c, str))
    out = {"source": "กรมทรัพยากรธรณี: ร่องรอยแผ่นดินถล่ม (พ.ศ. 2532-2562)", "built": date.today().isoformat(),
           "districts": dict(sorted(counts.items()))}
    PROCESSED.mkdir(parents=True, exist_ok=True)
    (PROCESSED / "landslide_districts.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    top = counts.most_common(5)
    print(f"{len(counts)} districts with traces; most: " + ", ".join(f"{gazetteer.by_code[c].name_th} {n}" for c, n in top))


if __name__ == "__main__":
    main()
