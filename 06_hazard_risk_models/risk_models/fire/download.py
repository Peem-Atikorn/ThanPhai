"""Download NASA FIRMS VIIRS (S-NPP) hotspot history over Thailand in 5-day chunks.

The FIRMS area API returns at most 5 days per call and allows 5,000 calls per 10 minutes per MAP_KEY.
Chunks are cached as gzip CSV, so re-running only fetches what is missing.
"""

import asyncio
import gzip
import logging
import os
from datetime import date, timedelta
from pathlib import Path

import httpx

log = logging.getLogger("firms_download")
AREA_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/97.3,5.6,105.7,20.5/5/{day}"
# "type" (archive only): 0 vegetation fire, 1 volcano, 2 other static land source (factories, kilns,
# refineries), 3 offshore. Needed to keep cement plants and refineries out of the fire model.
KEEP = ("latitude", "longitude", "acq_date", "acq_time", "confidence", "frp", "daynight", "type")
MAX_CONCURRENT = 4


def chunk_starts(start: date, end: date) -> list[date]:
    days, out = (end - start).days + 1, []
    for offset in range(0, days, 5):
        out.append(start + timedelta(days=offset))
    return out


def _slim(csv_text: str) -> str:
    """Keep only the columns the model uses (about a third of the size)."""
    lines = csv_text.strip().splitlines()
    if not lines:
        return ""
    header = lines[0].split(",")
    idx = [header.index(c) for c in KEEP if c in header]
    return "\n".join(",".join(row.split(",")[i] for i in idx) for row in lines) + "\n"


async def download(out_dir: Path, start: date, end: date, source: str = "VIIRS_SNPP_SP", key: str | None = None) -> list[Path]:
    key = key or os.getenv("FIRMS_MAP_KEY", "")
    if not key:
        raise ValueError("FIRMS_MAP_KEY is not set")
    out_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def one(client: httpx.AsyncClient, day: date) -> Path:
        path = out_dir / f"{source}_{day:%Y%m%d}.csv.gz"
        if path.exists():
            return path
        async with semaphore:
            for attempt in range(4):
                r = await client.get(AREA_URL.format(key=key, source=source, day=day.isoformat()), timeout=180)
                if r.status_code == 200 and r.text.startswith("latitude"):
                    break
                # FIRMS answers "Invalid MAP_KEY" / transaction limit with 200 text or 4xx/5xx; back off and retry.
                log.warning("FIRMS %s attempt %d: %s %s", day, attempt + 1, r.status_code, r.text[:80])
                await asyncio.sleep(10 * (attempt + 1))
            else:
                raise RuntimeError(f"FIRMS chunk {day} failed")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(_slim(r.text).encode()))
        tmp.replace(path)  # atomic: a crash never leaves a half-written chunk that looks complete
        return path

    async with httpx.AsyncClient() as client:
        return list(await asyncio.gather(*(one(client, d) for d in chunk_starts(start, end))))
