"""Live end-to-end check: ERA5 rainfall history -> SPI-3 (module 06) -> decision engine (module 07).

Run from the repo root:  .venv/Scripts/python scripts/live_drought_snapshot.py
"""

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

from decision_engine import DecisionEngine, DecisionInput
from risk_models.drought import classify_spi, compute_spi
from risk_models.drought.history import fetch_monthly_rain
from thanphai_shared import HazardType

POINTS = {
    "Chiang Mai": (18.79, 98.98),
    "Khon Kaen": (16.43, 102.83),
    "Nakhon Ratchasima": (14.97, 102.10),
    "Ayutthaya": (14.35, 100.57),
}
REFERENCE = (1991, 2020)  # WMO standard normal period
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


async def main() -> None:
    engine = DecisionEngine()
    end = date.today() - timedelta(days=7)  # ERA5 lags real time by about 5 days
    async with httpx.AsyncClient(headers={"User-Agent": "ThanPhai/0.1 (student project)"}) as client:
        for name, (lat, lon) in POINTS.items():
            monthly = await fetch_monthly_rain(client, lat, lon, date(1991, 1, 1), end, cache_dir=CACHE_DIR)
            spi = compute_spi(monthly, scale=3, reference=REFERENCE)
            last = spi[-1]
            decision = engine.decide(DecisionInput(hazard=HazardType.DROUGHT, observations={"spi_3": last.spi}))
            level = decision.level.name if decision.level is not None else "NO_DATA"
            print(
                f"{name:<18} {last.month:%Y-%m}  3-month rain {last.total_mm:7.1f} mm  "
                f"SPI-3 {last.spi:+.2f}  {classify_spi(last.spi):<17} -> {level} {decision.rule_ids}"
            )
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
