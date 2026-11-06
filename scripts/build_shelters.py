"""Temporary disaster shelters (ศูนย์พักพิงชั่วคราว) from DDPM's national list on data.go.th (Open Data Common).

Writes data/processed/shelters.json for the API's map layer. Only the place, its district and capacity are kept:
the file also lists each shelter's coordinator and phone number, which the app does not show.
Bangkok is not in DDPM's list. DDPM last updated the file in 2024, so re-run when data.go.th has a newer one.

Run from the repo root:
    .venv/Scripts/python scripts/build_shelters.py
"""

import csv
import io
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

from geo_integration.area_mapper import AreaMapper
from geo_integration.gazetteer import Gazetteer

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "shelters"
PROCESSED = ROOT / "data" / "processed"
DATASET = "https://data.go.th/api/3/action/package_show?id=gdpublish-dsc_11_01"
LAT_RANGE, LON_RANGE = (5.5, 20.5), (97.3, 105.7)
LIST_NUMBER = re.compile(r"^\d{1,3}[.)]\s*")  # some provinces numbered their list inside the name ("1. โรงเรียน…")


def download() -> tuple[str, str]:
    """The CSV text and the date DDPM last changed it."""
    RAW.mkdir(parents=True, exist_ok=True)
    meta = httpx.get(DATASET, timeout=60).json()["result"]
    resource = next(r for r in meta["resources"] if (r.get("format") or "").upper() == "CSV")
    updated = (resource.get("last_modified") or resource.get("created") or "")[:10]
    path = RAW / "ddpm_shelters.csv"
    if not path.exists():
        response = httpx.get(resource["url"], timeout=300, follow_redirects=True)
        response.raise_for_status()
        path.write_bytes(response.content)
    return path.read_bytes().decode("utf-8-sig"), updated


def number(value: str | None) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def coordinates(row: dict) -> tuple[float, float] | None:
    lat, lon = number(row.get("ละติจูด")), number(row.get("ลองจิจูด"))
    if lat is None or lon is None:
        return None
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
        lat, lon = lon, lat  # a few rows have latitude and longitude swapped
    if LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]:
        return lat, lon
    return None


def has(value: str | None) -> bool | None:
    text = (value or "").strip()
    if not text:
        return None
    return text != "ไม่มี" and text != "0"


def main() -> None:
    text, updated = download()
    rows = list(csv.DictReader(io.StringIO(text)))
    located = [(r, c) for r in rows if (c := coordinates(r))]
    mapper = AreaMapper.from_gpkg(PROCESSED / "admin_areas.gpkg")
    mapped = mapper.assign_points([c[0] for _, c in located], [c[1] for _, c in located])
    gazetteer = Gazetteer.load(PROCESSED / "gazetteer.json")

    shelters = []
    for (row, (lat, lon)), amphoe in zip(located, mapped["amphoe_code"]):
        if not isinstance(amphoe, str):
            continue  # offshore or across the border
        listed = (row.get("อำเภอ") or "").strip()
        responsible = (row.get("สถานที่รับผิดชอบอปท.") or "").strip()
        capacity = number(row.get("รองรับ"))
        toilets = number(row.get("สุขา"))
        shelters.append({
            # "สถานที่" is the shelter; "สถานที่รับผิดชอบอปท." the local authority (or school) that runs it.
            "name": LIST_NUMBER.sub("", (row.get("สถานที่") or "").strip()) or responsible,
            "responsible": responsible if responsible not in ("อปท.", "อปท") else "",
            "village": (row.get("หมู่บ้าน/ชุมชน") or "").strip(),
            "tambon": (row.get("ตำบล") or "").strip(),
            "amphoe": listed,
            "province": (row.get("จังหวัด") or "").strip(),
            "amphoe_code": amphoe,
            "capacity": int(capacity) if capacity else None,
            "electricity": has(row.get("ไฟฟ้า")),
            "water": has(row.get("ประปา")),
            "toilets": int(toilets) if toilets else None,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
        })
    # The same shelter is sometimes listed twice (same name, place and point; some under two provinces): keep one.
    unique = {(s["name"], s["tambon"], s["amphoe_code"], s["lat"], s["lon"]): s for s in reversed(shelters)}
    duplicates = len(shelters) - len(unique)
    shelters = list(reversed(unique.values()))
    # Many shelters share one point (a sub-district's centre rather than the building): mark those as approximate.
    shared = Counter((s["lat"], s["lon"]) for s in shelters)
    for s in shelters:
        s["approximate"] = shared[(s["lat"], s["lon"])] > 1
    district = {s["amphoe_code"]: gazetteer.by_code[s["amphoe_code"]].name_th for s in shelters if s["amphoe_code"] in gazetteer.by_code}
    agree = sum(1 for s in shelters if s["amphoe"] and district.get(s["amphoe_code"])
                and (s["amphoe"] in district[s["amphoe_code"]] or district[s["amphoe_code"]] in s["amphoe"]))

    print(f"{len(rows)} shelters listed, {len(located)} with coordinates in Thailand, {duplicates} listed twice, "
          f"{len(shelters)} inside a district, "
          f"{agree / max(len(shelters), 1):.0%} in a district with the name DDPM gave, "
          f"{sum(s['approximate'] for s in shelters)} on a shared (approximate) point")
    out = {"source": "กรมป้องกันและบรรเทาสาธารณภัย: ข้อมูลศูนย์พักพิง (data.go.th)",
           "source_url": "https://data.go.th/dataset/gdpublish-dsc_11_01",
           "updated": updated, "built": date.today().isoformat(), "shelters": shelters}
    PROCESSED.mkdir(parents=True, exist_ok=True)
    (PROCESSED / "shelters.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
