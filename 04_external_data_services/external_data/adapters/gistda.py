"""GISTDA satellite flood extent (Sentinel-1 radar), counted per district.

The flood collections hold one H3 cell (~0.12 km2) per flooded hexagon: about 80,000 cells for 7 days in
October 2026, ~3 KB each with geometry. Instead of downloading them, ask for `limit=1` filtered by area
code and read `numberMatched`: 77 province calls, then district calls only where a province has floods.

Security: GISTDA echoes the API key inside the response links (`?api_key=...`). This adapter never
stores or returns links or raw responses.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any

import httpx

from external_data.adapters.base import Adapter
from thanphai_shared import SourceRecord

log = logging.getLogger("gistda")
BASE = "https://api-gateway.gistda.or.th/api/2.0/resources/features"
WINDOWS = ("1day", "3days", "7days", "30days")
H3_CELL_KM2 = 0.1245  # h3_area in the data: ~124,483 m2 per cell
MAX_CONCURRENT = 6


class GistdaFloodCells(Adapter):
    name = "gistda_flood"
    attribution = "ข้อมูลพื้นที่น้ำท่วมจากดาวเทียม: GISTDA"
    timeout_seconds = 60.0

    def __init__(self, province_codes: list[str], amphoe_codes: list[str], window: str = "7days", api_key: str | None = None):
        if window not in WINDOWS:
            raise ValueError(f"window must be one of {WINDOWS}")
        # Accept our P-codes ("TH14", "TH1412"); GISTDA uses the bare numbers (14, 1412).
        self.province_codes = [c.removeprefix("TH") for c in province_codes]
        self.amphoe_codes = [c.removeprefix("TH") for c in amphoe_codes]
        self.window = window
        self.api_key = api_key if api_key is not None else os.getenv("GISTDA_API_KEY", "")

    async def _count(self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore, field: str, code: str) -> int:
        async with semaphore:
            response = await client.get(
                f"{BASE}/flood/{self.window}", params={field: int(code), "limit": 1},
                headers={"API-Key": self.api_key}, timeout=self.timeout_seconds,
            )
        if response.status_code in (401, 403, 407):
            raise PermissionError(f"GISTDA key rejected ({response.status_code})")
        response.raise_for_status()
        return int(response.json().get("numberMatched") or 0)

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        if not self.api_key:
            raise ValueError("GISTDA_API_KEY is not set")
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        province_counts = await asyncio.gather(*(self._count(client, semaphore, "pv_idn", p) for p in self.province_codes),
                                               return_exceptions=True)
        failed_provinces = set()
        for p, n in zip(self.province_codes, province_counts):
            if isinstance(n, PermissionError):
                raise n
            if isinstance(n, Exception):
                failed_provinces.add(p)  # one timeout must not lose the whole country
        flooded = {p for p, n in zip(self.province_codes, province_counts) if not isinstance(n, Exception) and n > 0}
        targets = [a for a in self.amphoe_codes if a[:2] in flooded]
        amphoe_counts = await asyncio.gather(*(self._count(client, semaphore, "ap_idn", a) for a in targets),
                                             return_exceptions=True)
        # 0 only where the province reported no flood; districts of a failed province stay unknown (absent).
        counts: dict[str, int] = {a: 0 for a in self.amphoe_codes if a[:2] not in flooded and a[:2] not in failed_provinces}
        failed = [a for a in self.amphoe_codes if a[:2] in failed_provinces]
        if failed_provinces and len(failed_provinces) == len(self.province_codes):
            raise RuntimeError("GISTDA failed for every province")
        for a, n in zip(targets, amphoe_counts):
            if isinstance(n, PermissionError):
                raise n
            if isinstance(n, Exception):
                failed.append(a)
            else:
                counts[a] = n
        return {"counts": counts, "failed": failed, "provinces_with_flood": sorted(flooded)}

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        return [
            SourceRecord(
                source=self.name,
                variable=f"flood_cells_{self.window}",
                value=float(n),
                unit="cells",
                province_code=code[:2],
                observed_at=fetched_at,
                fetched_at=fetched_at,
                attribution=self.attribution,
                extra={"area_code": f"TH{code}", "approx_km2": round(n * H3_CELL_KM2, 1)},
            )
            for code, n in raw["counts"].items()
        ]
