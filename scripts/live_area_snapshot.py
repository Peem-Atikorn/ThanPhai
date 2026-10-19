"""Live end-to-end check per district: 04 adapters -> 05 area mapping + features -> 07 decision engine.

Run from the repo root (after scripts/build_admin_areas.py):
    .venv/Scripts/python scripts/live_area_snapshot.py
"""

import asyncio
import collections
import json
import sys
from pathlib import Path

import httpx

from decision_engine import DecisionEngine, DecisionInput
from external_data.adapters import FirmsHotspots, ThaiWaterRain24h, ThaiWaterWaterLevel, TmdWarnings
from geo_integration import Gazetteer
from geo_integration.area_mapper import AreaMapper
from geo_integration.features import fire_features, flood_features, rain_features
from thanphai_shared import HazardType

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
OUT = PROCESSED / "area_snapshot.json"


async def fetch_all():
    async with httpx.AsyncClient(headers={"User-Agent": "ThanPhai/0.1 (student project)"}) as client:
        return await asyncio.gather(
            ThaiWaterWaterLevel().run(client),
            ThaiWaterRain24h().run(client),
            FirmsHotspots(map_key="").run(client),
            TmdWarnings().run_warnings(client),
        )


def check_mapping(name: str, mapped, records) -> None:
    counts = collections.Counter(mapped["match"])
    # ThaiWater sends its own province code; compare it with our polygon result.
    with_code = [(r.province_code, m) for r, m in zip(records, mapped["province_code"]) if r.province_code and isinstance(m, str)]
    agree = sum(1 for given, ours in with_code if f"TH{given}" == ours)
    agreement = f", province agrees with provider {agree}/{len(with_code)}" if with_code else ""
    print(f"  {name:<12} {dict(counts)}{agreement}")


def main() -> None:
    levels, rain, hotspots, warnings = asyncio.run(fetch_all())
    gaz = Gazetteer.load(PROCESSED / "gazetteer.json")
    mapper = AreaMapper.from_gpkg(PROCESSED / "admin_areas.gpkg")
    amphoe_codes = [a.code for a in gaz.areas if a.level == "amphoe"]

    print("Mapping to tambon/amphoe:")
    wl_rows = mapper.assign_records(levels)
    rain_rows = mapper.assign_records(rain)
    fire_rows = mapper.assign_records(hotspots)
    check_mapping("water level", wl_rows, levels)
    check_mapping("rain", rain_rows, rain)
    check_mapping("hotspots", fire_rows, hotspots)

    flood = flood_features(wl_rows).set_index("area_code")
    rainf = rain_features(rain_rows).set_index("area_code")
    fire = fire_features(fire_rows, amphoe_codes).set_index("area_code")

    engine = DecisionEngine()
    results = []
    for code in amphoe_codes:
        area = gaz.by_code[code]
        obs = {
            HazardType.FLOOD: {"above_bank_m": flood["above_bank_m"].get(code)},
            HazardType.RAIN_STORM: {"rain_24h": rainf["rain_24h"].get(code)},
            HazardType.FIRE: {"hotspots_24h": fire["hotspots_24h"].get(code)},
        }
        for hazard, observations in obs.items():
            clean = {k: (None if v is None or v != v else float(v)) for k, v in observations.items()}  # NaN -> None
            d = engine.decide(DecisionInput(hazard=hazard, observations=clean))
            results.append({
                "area_code": code, "name_th": area.name_th, "province_th": gaz.by_code[area.parent_code].name_th,
                "hazard": hazard.value, "level": d.level.name if d.level is not None else None,
                "confidence": d.confidence, "rule_ids": d.rule_ids, "observations": clean,
            })

    print(f"\nTMD warnings: {[w.title[:60] for w in warnings]}")
    print(f"\nDistricts: {len(amphoe_codes)}  rules {engine.version}")
    for hazard in ("flood", "rain_storm", "fire"):
        c = collections.Counter(r["level"] or "NO_DATA" for r in results if r["hazard"] == hazard)
        print(f"  {hazard:<10} " + "  ".join(f"{k}={c[k]}" for k in ("CRITICAL", "WARNING", "WATCH", "NORMAL", "NO_DATA")))

    print("\nDistricts at WARNING or above:")
    rank = {"CRITICAL": 3, "WARNING": 2}
    top = sorted((r for r in results if r["level"] in rank), key=lambda r: (-rank[r["level"]], r["hazard"]))
    for r in top[:15]:
        print(f"  {r['level']:<8} {r['hazard']:<10} อ.{r['name_th']} จ.{r['province_th']}  {r['observations']}")

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nSaved {len(results)} decisions to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
