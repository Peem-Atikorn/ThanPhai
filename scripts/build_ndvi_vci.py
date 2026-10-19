"""Build district NDVI and the drought VCI from MODIS MOD13A3 (monthly, 1 km).

Downloads the latest month and the same calendar month of the previous 10 years (5 tiles each, ~55 MB per
month), computes district means, and writes data/processed/drought_vci.json for the API.
Re-run monthly (MOD13A3 appears ~2-6 weeks after the month ends). Cached downloads are reused.

Run from the repo root (needs EARTHDATA_TOKEN in .env, and `pip install pyhdf`):
    .venv/Scripts/python scripts/build_ndvi_vci.py
"""

import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path

import geopandas as gpd
import httpx
import numpy as np

from risk_models.drought.ndvi import THAILAND_TILES, district_index, district_means, read_ndvi, vci

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "ndvi"
PROCESSED = ROOT / "data" / "processed"
CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
HISTORY_YEARS = 10
MAX_CONCURRENT = 4


def token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("EARTHDATA_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("EARTHDATA_TOKEN is not set in .env")


def latest_month(client: httpx.Client) -> date:
    entry = client.get(CMR, params={"short_name": "MOD13A3", "version": "061", "sort_key": "-start_date",
                                    "bounding_box": "97.3,5.6,105.7,20.5", "page_size": 1}).json()["feed"]["entry"][0]
    return date.fromisoformat(entry["time_start"][:10])


def granule_urls(client: httpx.Client, month: date) -> dict[str, str]:
    """{tile: https url} for one month over Thailand."""
    end = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    entries = client.get(CMR, params={"short_name": "MOD13A3", "version": "061", "page_size": 50,
                                      "bounding_box": "97.3,5.6,105.7,20.5",
                                      "temporal": f"{month.isoformat()}T00:00:00Z,{end.isoformat()}T00:00:00Z"}).json()["feed"]["entry"]
    urls = {}
    for e in entries:
        tile = next((t for t in THAILAND_TILES if f".{t}." in e["title"]), None)
        href = next((l["href"] for l in e["links"] if l["href"].startswith("https") and l["href"].endswith(".hdf")), None)
        if tile and href and e["time_start"].startswith(month.isoformat()[:7]):
            urls[tile] = href
    return urls


async def download(urls: list[str], bearer: str) -> list[Path]:
    RAW.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def one(client: httpx.AsyncClient, url: str) -> Path:
        path = RAW / url.rsplit("/", 1)[-1]
        if path.exists():
            return path
        async with semaphore:
            r = await client.get(url, headers={"Authorization": f"Bearer {bearer}"}, follow_redirects=True, timeout=600)
        r.raise_for_status()
        if not r.content.startswith(b"\x0e\x03\x13\x01"):  # HDF4 magic number; a login page means a bad token
            raise RuntimeError(f"{path.name}: not an HDF file (token expired?)")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(path)
        return path

    async with httpx.AsyncClient() as client:
        return list(await asyncio.gather(*(one(client, u) for u in urls)))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    bearer = token()
    amphoe = gpd.read_file(PROCESSED / "admin_areas.gpkg", layer="amphoe")
    codes = amphoe["code"].tolist()

    index_file = PROCESSED / "ndvi_district_index.npz"
    if index_file.exists():
        indexes = dict(np.load(index_file))
    else:
        indexes = {tile: district_index(amphoe, tile) for tile in THAILAND_TILES}
        np.savez_compressed(index_file, **indexes)
    covered = len({int(i) for arr in indexes.values() for i in np.unique(arr) if i >= 0})
    print(f"[{time.time() - t0:4.0f}s] district index ready ({covered}/{len(codes)} districts on the grid)")

    with httpx.Client(timeout=120) as client:
        now = latest_month(client)
        months = [date(now.year - k, now.month, 1) for k in range(HISTORY_YEARS + 1)]
        plan = {m: granule_urls(client, m) for m in months}
    missing = {m.isoformat()[:7]: sorted(set(THAILAND_TILES) - set(u)) for m, u in plan.items() if set(u) != set(THAILAND_TILES)}
    print(f"[{time.time() - t0:4.0f}s] latest month {now:%Y-%m}; {sum(len(u) for u in plan.values())} tiles to use; missing: {missing or 'none'}")

    paths = asyncio.run(download([u for urls in plan.values() for u in urls.values()], bearer))
    print(f"[{time.time() - t0:4.0f}s] downloads ready ({sum(p.stat().st_size for p in paths) / 1e6:.0f} MB cached)")

    by_month = {}
    for month, urls in plan.items():
        tiles = {tile: read_ndvi(RAW / url.rsplit("/", 1)[-1]) for tile, url in urls.items()}
        by_month[month] = district_means(tiles, indexes, len(codes))
    history = np.vstack([by_month[m] for m in months[1:]])
    result = vci(by_month[now], history)
    print(f"[{time.time() - t0:4.0f}s] VCI for {np.isfinite(result.vci).sum()} districts")

    out = {
        "month": now.isoformat()[:7],
        "history_years": f"{months[-1].year}-{months[1].year}",
        "source": "NASA MODIS MOD13A3 v061 (Terra, monthly 1 km NDVI)",
        "districts": {
            code: {"vci": round(float(v), 1), "ndvi": round(float(n), 3), "ndvi_min": round(float(lo), 3),
                   "ndvi_max": round(float(hi), 3), "ndvi_mean": round(float(mean), 3), "ndvi_deficit": round(float(dfc), 3)}
            for code, v, n, lo, hi, mean, dfc in zip(codes, result.vci, result.ndvi_now, result.ndvi_min, result.ndvi_max,
                                                     result.ndvi_mean, result.deficit)
            if np.isfinite(v)
        },
    }
    (PROCESSED / "drought_vci.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    values = np.array([d["vci"] for d in out["districts"].values()])
    bands = {"<10": int((values < 10).sum()), "10-20": int(((values >= 10) & (values < 20)).sum()),
             "20-35": int(((values >= 20) & (values < 35)).sum()), ">=35": int((values >= 35).sum())}
    print(f"[{time.time() - t0:4.0f}s] wrote drought_vci.json for {out['month']} — VCI bands: {bands}")


if __name__ == "__main__":
    main()
