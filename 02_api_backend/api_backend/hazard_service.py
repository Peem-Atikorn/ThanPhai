"""Builds and caches per-district hazard decisions from live data (modules 04 -> 05 -> 07).

Modules 04, 05, and 07 are called in-process as libraries for now. When they run as separate
services, only this file changes: the HTTP calls replace the library calls.
"""

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import httpx

from decision_engine import DecisionEngine, DecisionInput
from external_data.adapters import FirmsHotspots, ThaiWaterRain24h, ThaiWaterWaterLevel, TmdNwpDailyForecast, TmdWarnings
from external_data.adapters.gistda import GistdaFloodCells
from external_data.adapters.thaiwater_dams import ThaiWaterDams
from external_data.adapters.thaiwater_graphs import fetch_dam_year, fetch_station_graph
from external_data.adapters.thaiwater_media import parse_media
from geo_integration import Gazetteer
from geo_integration.area_mapper import AreaMapper
from geo_integration.features import fire_features, flood_features, rain_features
from thanphai_shared import AlertLevel, HazardType

BANGKOK = timezone(timedelta(hours=7), "Asia/Bangkok")
RETRY_AFTER_FAILURE = timedelta(minutes=10)
# Vegetation-based drought: NDVI must be this far below the month's normal to count, and districts whose
# greenest month never reaches URBAN_NDVI_MAX are mostly built-up, where NDVI says nothing about drought.
MIN_NDVI_DEFICIT = 0.05
URBAN_NDVI_MAX = 0.6

# Same thresholds as the flood rules (rules.yaml FLOOD_ABOVE_BANK / FLOOD_NEAR_BANK), so the river view
# and the flood map never disagree about a station.
NEAR_BANK_M = -0.3
# Chao Phraya main stem, upstream to downstream, as RID's hourly report (hyd-app.rid.go.th/hydro5h.html)
# orders it: Ping and Nan inflows, Nakhon Sawan, below the Chao Phraya Dam, then down to Ayutthaya.
CHAO_PHRAYA_CHAIN = ["P.17", "N.67", "C.2", "C.13", "C.3", "C.7A", "C.35", "C.36", "C.29B"]
# Water-level readings older than this are dropped: a gauge that stopped reporting (seen live: BKC002,
# last reading the day before, still "over the bank") must not keep a district at WARNING.
MAX_LEVEL_AGE = timedelta(hours=6)
GRAPH_TTL = timedelta(minutes=15)
DAM_HISTORY_TTL = timedelta(hours=6)
FAILURE_TTL = timedelta(seconds=60)
USER_AGENT = {"User-Agent": "ThanPhai/0.1 (student project)"}

log = logging.getLogger("hazard_service")
SOURCES = {
    HazardType.FLOOD: ["ThaiWater (สสน.)"],
    HazardType.RAIN_STORM: ["ThaiWater (สสน.)"],
    HazardType.FIRE: ["NASA FIRMS"],
    HazardType.DROUGHT: [],
}
# Map layer: coarser than the 50 m web layer, enough for a whole-country view.
MAP_SIMPLIFY_DEGREES = 0.004
# 4 decimals ≈ 11 m. Full precision (15 digits) made the layer 2.7 MB and kept the map blank for
# ~8 s while MapLibre's worker parsed it.
MAP_COORD_DECIMALS = 4


def _round_coords(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], MAP_COORD_DECIMALS), round(coords[1], MAP_COORD_DECIMALS)]
    return [_round_coords(c) for c in coords]


def _clean(value) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


class HazardService:
    def __init__(self, data_dir: Path, ttl: timedelta = timedelta(minutes=15)):
        self.data_dir = data_dir
        self.ttl = ttl
        self.gazetteer = Gazetteer.load(data_dir / "gazetteer.json")
        self.mapper = AreaMapper.from_gpkg(data_dir / "admin_areas.gpkg")
        self.engine = DecisionEngine()
        self.amphoe_codes = [a.code for a in self.gazetteer.areas if a.level == "amphoe"]
        self.decisions: dict[str, list[dict]] = {}
        self.warnings: list[dict] = []
        self.source_status: dict[str, dict] = {}
        self.updated_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._background: asyncio.Task | None = None
        # Set when slower inputs (satellite, fire model) change, so decisions are rebuilt without
        # pretending the live data is missing (clearing updated_at showed "data time: —" in the UI).
        self._inputs_changed = False
        self._drought: dict | None = None
        self.dams: list[dict] = []  # 35 large dams, refreshed with the live data
        self.rivers: list[dict] = []  # river stations with a flow gauge or over their bank
        self.media: dict | None = None  # radar, rain-forecast, storm and wave images
        self._graph_cache: dict[tuple, tuple[datetime, dict]] = {}
        self._inflight: dict[tuple, asyncio.Task] = {}
        self._boundaries: dict[str, dict] = {}  # area outlines, read on first request
        self._drought_mtime: float | None = None
        self._jobs: dict[str, asyncio.Task] = {}
        self._job_failed_at: dict[str, datetime] = {}
        # TMD NWP forecast: updated a few times a day, so it refreshes on its own, slower clock.
        self.forecast_ttl = timedelta(hours=3)
        self.forecasts: dict[str, list[dict]] = {}
        self.forecast_updated_at: datetime | None = None
        self._forecast_lock = asyncio.Lock()
        # GISTDA satellite flood cells per district (last 7 days); satellites pass every few days.
        self.satellite_ttl = timedelta(hours=6)
        self.satellite_cells: dict[str, dict] = {}
        self.satellite_updated_at: datetime | None = None
        # Fire model (module 06): trained offline by scripts/train_fire_model.py; optional.
        self.fire_model = self._load_fire_model()
        # Known industrial heat sources (cement kilns, refineries); the live FIRMS feed cannot tell them apart.
        from risk_models.fire import static_sources

        self.static_cells = static_sources.load(data_dir / "firms_static_cells.json")
        self.fire_ttl = timedelta(hours=3)
        self.fire_predictions: dict[str, dict] = {}
        self.fire_updated_at: datetime | None = None
        self._geometry = self._load_map_geometry()

    def _load_map_geometry(self) -> dict[str, dict]:
        amphoe = gpd.read_file(self.data_dir / "admin_areas_web.gpkg", layer="amphoe")
        amphoe["geometry"] = amphoe.geometry.simplify(MAP_SIMPLIFY_DEGREES, preserve_topology=True)
        features = json.loads(amphoe[["code", "geometry"]].to_json(drop_id=True))["features"]
        return {f["properties"]["code"]: {**f["geometry"], "coordinates": _round_coords(f["geometry"]["coordinates"])}
                for f in features}

    @property
    def inputs_pending(self) -> list[str]:
        """Slow inputs still loading for the first time (after a restart), so clients can check back soon."""
        slow = {"gistda_flood": self.satellite_updated_at, "tmd_nwp_daily": self.forecast_updated_at, "fire_model": self.fire_updated_at}
        return [name for name, updated in slow.items()
                if updated is None and name in self._jobs and not self._jobs[name].done()]

    @property
    def stale(self) -> bool:
        if self._inputs_changed:
            return True
        return self.updated_at is None or datetime.now(timezone.utc) - self.updated_at > self.ttl

    async def refresh(self) -> None:
        """Fetch and rebuild now. Concurrent callers share one refresh."""
        async with self._lock:
            if not self.stale and self.decisions:
                return
            # Reset before the snapshot: inputs that change while this refresh runs set it again.
            self._inputs_changed = False
            results, warnings_result = await self._fetch()
            media = results.pop("thaiwater_media", None)
            raw = (results, warnings_result)
            # Mapping ~5,000 points and deciding 928 x 3 hazards is CPU work; run it off the event loop
            # so other requests are not blocked while it runs.
            decisions, warnings, status = await asyncio.to_thread(self._process, *raw)
            dams = raw[0].get("thaiwater_dams")
            if dams is not None and not isinstance(dams, Exception):
                self.dams = self._dam_rows(dams)  # keep the last good list if ThaiWater failed
            levels = results.get("thaiwater_waterlevel")
            if levels is not None and not isinstance(levels, Exception):
                self.rivers = self._river_rows(self.fresh_levels(levels))
            if media is not None and not isinstance(media, Exception):
                self.media = media
            # Swap everything at once so readers never see a half-updated state.
            self.decisions = decisions
            if warnings is not None:
                self.warnings = warnings  # keep the last good warnings if TMD failed this time
            # Merge, not replace: the forecast refresh writes its own entry on a different schedule.
            self.source_status = {**self.source_status, **status}
            self.updated_at = datetime.now(timezone.utc)

    def refresh_in_background(self) -> None:
        """Serve the current data and refresh behind it (stale-while-revalidate)."""
        if self._background is None or self._background.done():
            self._background = asyncio.create_task(self._safe_refresh())

    async def _safe_refresh(self) -> None:
        try:
            await self.refresh()
        except Exception:  # a failed background refresh must not kill the server; old data stays
            log.exception("background refresh failed")

    async def _fetch(self) -> tuple[dict, object]:
        adapters = {
            "thaiwater_waterlevel": ThaiWaterWaterLevel(),
            "thaiwater_rain_24h": ThaiWaterRain24h(),
            "nasa_firms": FirmsHotspots(),
        }
        async with httpx.AsyncClient(headers=USER_AGENT, follow_redirects=True) as client:
            results = await asyncio.gather(*(a.run(client) for a in adapters.values()), self._fetch_main_bundle(client),
                                           TmdWarnings().run_warnings(client), return_exceptions=True)
        out = dict(zip(adapters, results[:-2]))
        bundle = results[-2]
        # thailand_main is ~12 MB; download it once for both the dams and the weather images.
        out["thaiwater_dams"], out["thaiwater_media"] = (bundle, bundle) if isinstance(bundle, Exception) else bundle
        return out, results[-1]

    @staticmethod
    async def _fetch_main_bundle(client: httpx.AsyncClient) -> tuple[list, dict]:
        adapter = ThaiWaterDams()
        fetched_at = datetime.now(timezone.utc)
        raw = await adapter.fetch(client)
        return adapter.parse(raw, fetched_at), parse_media(raw, fetched_at)

    def _process(self, results: dict, warnings_result) -> tuple[dict[str, list[dict]], list[dict] | None, dict[str, dict]]:
        now = datetime.now(timezone.utc).isoformat()
        status: dict[str, dict] = {}
        records: dict[str, list | None] = {}
        for name, result in results.items():
            if isinstance(result, Exception):
                log.warning("source %s failed: %s", name, result)
                status[name] = {"ok": False, "error": type(result).__name__, "checked_at": now}
                records[name] = None
            else:
                status[name] = {"ok": True, "records": len(result), "checked_at": now}
                records[name] = result
        if isinstance(warnings_result, Exception):
            status["tmd_warnings"] = {"ok": False, "error": type(warnings_result).__name__, "checked_at": now}
            warnings = None
        else:
            status["tmd_warnings"] = {"ok": True, "records": len(warnings_result), "checked_at": now}
            warnings = [w.model_dump(mode="json") for w in warnings_result]

        if records["thaiwater_waterlevel"]:
            fresh = self.fresh_levels(records["thaiwater_waterlevel"])
            dropped = len(records["thaiwater_waterlevel"]) - len(fresh)
            status["thaiwater_waterlevel"]["stale_dropped"] = dropped
            # Every reading old means the feed itself stopped: say so instead of showing "no flood".
            records["thaiwater_waterlevel"] = fresh or None
            if not fresh:
                status["thaiwater_waterlevel"] = {"ok": False, "error": "stale", "checked_at": now}
        degraded = {
            HazardType.FLOOD: [] if records["thaiwater_waterlevel"] is not None else ["ThaiWater water level"],
            HazardType.RAIN_STORM: [] if records["thaiwater_rain_24h"] is not None else ["ThaiWater rain"],
            HazardType.FIRE: [] if records["nasa_firms"] is not None else ["NASA FIRMS"],
        }
        def build(source: str, hazard: HazardType, make):
            """One hazard's features. A failure here degrades that hazard only, never the whole refresh."""
            if records[source] is None:
                return None
            try:
                return make(self.mapper.assign_records(records[source])).set_index("area_code")
            except Exception as exc:
                log.exception("features for %s failed", source)
                status[source] = {"ok": False, "error": f"processing: {type(exc).__name__}", "checked_at": now}
                degraded[hazard].append(source)
                return None

        flood = build("thaiwater_waterlevel", HazardType.FLOOD, flood_features)
        rain = build("thaiwater_rain_24h", HazardType.RAIN_STORM, rain_features)
        if records["nasa_firms"] and self.static_cells:
            records["nasa_firms"] = self._drop_static_records(records["nasa_firms"])
        fire = build("nasa_firms", HazardType.FIRE, lambda rows: fire_features(rows, self.amphoe_codes))

        def value(frame, column: str, code: str) -> float | None:
            return _clean(frame[column].get(code)) if frame is not None and not frame.empty else None

        # Snapshots: background refreshes may swap these dicts while decisions are built.
        satellite = dict(self.satellite_cells)
        fire_pred = dict(self.fire_predictions)
        drought = self._current_drought()
        decisions: dict[str, list[dict]] = {}
        for code in self.amphoe_codes:
            sat = satellite.get(code)
            pred = fire_pred.get(code)
            above_bank = value(flood, "above_bank_m", code)
            veg = drought["districts"].get(code) if drought else None
            # Water is dark in near-infrared, so flooded fields have low NDVI and would read as drought.
            flooded = bool(sat and sat["cells"] > 0) or (above_bank is not None and above_bank > 0)
            drought_obs, drought_note = self._vegetation_observations(veg, flooded)
            observations = {
                HazardType.FLOOD: {"above_bank_m": above_bank,
                                   "satellite_flood_cells_7d": float(sat["cells"]) if sat else None},
                HazardType.RAIN_STORM: {"rain_24h": value(rain, "rain_24h", code)},
                HazardType.FIRE: {"hotspots_24h": value(fire, "hotspots_24h", code)},
                HazardType.DROUGHT: drought_obs,
            }
            items = []
            for hazard, obs in observations.items():
                # National TMD warnings are shown as context; they are not applied per district until
                # module 05 can map a warning's text to the provinces it names.
                model_level = model_conf = None
                if hazard == HazardType.FIRE and pred:
                    # The model's level is one input; rules can still raise it, and it never lowers them.
                    p = pred["probability"]
                    model_level = AlertLevel[pred["level"]]
                    # Confidence in the stated level: p for a fire level, 1 - p for NORMAL.
                    model_conf = p if model_level != AlertLevel.NORMAL else 1 - p
                    obs = {**obs, "fire_probability_7d": pred["probability"]}
                d = self.engine.decide(DecisionInput(hazard=hazard, observations=obs, degraded_sources=degraded.get(hazard, []),
                                                     model_level=model_level, model_confidence=model_conf))
                notices = list(d.notices)
                sources = list(SOURCES[hazard])
                if obs.get("satellite_flood_cells_7d") is not None:
                    sources.append("GISTDA (ดาวเทียม)")
                if hazard == HazardType.DROUGHT and drought:
                    sources.append(f"NASA MODIS NDVI ({drought['month']})")
                    if drought_note:
                        notices.append(drought_note)
                items.append({
                    "hazard": hazard.value,
                    "level": d.level.name if d.level is not None else None,
                    "confidence": d.confidence,
                    "rule_ids": d.rule_ids,
                    "notices": notices,
                    "observations": obs,
                    "sources": sources,
                    "rules_version": d.rules_version,
                })
            decisions[code] = items
        return decisions, warnings, status

    @staticmethod
    def _vegetation_observations(veg: dict | None, flooded: bool) -> tuple[dict, str | None]:
        """Drought inputs from district NDVI, with the three cases where low NDVI is not drought.

        Found on the 2026-09 data: 35 % of low-VCI districts were flooded; urban districts (บางพลี, เมืองระยอง)
        had VCI 0 from land turning into buildings; green rainy-season districts had VCI 0 from a 0.001 drop.
        """
        if veg is None:
            return {"vci": None, "ndvi_deficit": None}, None
        if flooded:
            return {"vci": None, "ndvi_deficit": None}, "พื้นที่นี้มีน้ำท่วม ค่า NDVI จึงไม่ใช้ประเมินภัยแล้งในรอบนี้"
        if veg["ndvi_max"] < URBAN_NDVI_MAX:
            return {"vci": None, "ndvi_deficit": None}, "พื้นที่ส่วนใหญ่เป็นเมือง ค่า NDVI ไม่เหมาะใช้ประเมินภัยแล้ง"
        deficit = veg.get("ndvi_deficit")
        if deficit is None:  # file from before ndvi_deficit existed
            return {"vci": None, "ndvi_deficit": None}, None
        real_drop = deficit >= MIN_NDVI_DEFICIT
        return {"vci": veg["vci"] if real_drop else None, "ndvi_deficit": deficit}, None

    def _current_drought(self) -> dict | None:
        """drought_vci.json, built monthly by scripts/build_ndvi_vci.py; reloaded when the file changes."""
        path = self.data_dir / "drought_vci.json"
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return None
        if mtime != self._drought_mtime:
            self._drought = json.loads(path.read_text(encoding="utf-8"))
            self._drought_mtime = mtime
        return self._drought

    def _drop_static_records(self, records: list) -> list:
        """Remove FIRMS records at known industrial heat sources, so the hotspot rule counts fires only."""
        import pandas as pd

        from risk_models.fire.static_sources import drop_static

        frame = pd.DataFrame({"latitude": [r.lat for r in records], "longitude": [r.lon for r in records], "i": range(len(records))})
        keep = drop_static(frame, self.static_cells)["i"].tolist()
        return [records[i] for i in keep]

    # --- slower inputs: one background job each ---------------------------------------------

    def _start_job(self, name: str, run, stale: bool) -> None:
        """Run a slow refresh in the background: one at a time per input, and after a failure wait
        RETRY_AFTER_FAILURE before trying again. Without the wait, every request restarted a failing job
        (77 TMD calls or ~400 GISTDA calls each time) and could get the key banned."""
        if not stale:
            return
        task = self._jobs.get(name)
        if task is not None and not task.done():
            return
        failed_at = self._job_failed_at.get(name)
        if failed_at is not None and datetime.now(timezone.utc) - failed_at < RETRY_AFTER_FAILURE:
            return
        self._jobs[name] = asyncio.create_task(self._run_job(name, run))

    async def _run_job(self, name: str, run) -> None:
        try:
            await run()
            self._job_failed_at.pop(name, None)
        except Exception as exc:
            now = datetime.now(timezone.utc)
            self._job_failed_at[name] = now
            self.source_status[name] = {"ok": False, "error": type(exc).__name__, "checked_at": now.isoformat(),
                                        "next_try_after": (now + RETRY_AFTER_FAILURE).isoformat()}
            log.warning("%s refresh failed, next try in %s: %s: %s", name, RETRY_AFTER_FAILURE, type(exc).__name__, exc)

    # --- forecast (TMD NWP) ---------------------------------------------------------------

    @property
    def forecast_stale(self) -> bool:
        return self.forecast_updated_at is None or datetime.now(timezone.utc) - self.forecast_updated_at > self.forecast_ttl

    def refresh_forecast_in_background(self) -> None:
        if os.getenv("TMD_NWP_TOKEN"):
            self._start_job("tmd_nwp_daily", self.refresh_forecast, self.forecast_stale)

    async def refresh_forecast(self) -> None:
        async with self._forecast_lock:
            if not self.forecast_stale and self.forecasts:
                return
            provinces = [a.name_th for a in self.gazetteer.areas if a.level == "province"]
            adapter = TmdNwpDailyForecast(provinces, days=3)
            async with httpx.AsyncClient(headers={"User-Agent": "ThanPhai/0.1 (student project)"}) as client:
                raw = await adapter.fetch(client)
            records = adapter.parse(raw, datetime.now(timezone.utc))
            self.forecasts = await asyncio.to_thread(self._process_forecast, records)
            self.forecast_updated_at = datetime.now(timezone.utc)
            self.source_status["tmd_nwp_daily"] = {
                "ok": not raw["failed"], "records": len(records), "failed_provinces": raw["failed"],
                "checked_at": self.forecast_updated_at.isoformat(),
            }

    def _process_forecast(self, records) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for r in records:
            d = self.engine.decide(DecisionInput(hazard=HazardType.RAIN_STORM, observations={"forecast_rain_24h": r.value}))
            out.setdefault(r.extra["area_code"], []).append({
                "date": r.valid_time.date().isoformat(),
                "rain_mm": r.value,
                "level": d.level.name if d.level is not None else None,
                "cond_th": r.extra.get("cond_th"),
                "wind_max_kmh": r.extra.get("wind_max_kmh"),
                "source": "กรมอุตุนิยมวิทยา (แบบจำลอง WRF)",
            })
        for days in out.values():
            days.sort(key=lambda x: x["date"])
        return out

    # --- satellite flood (GISTDA) -----------------------------------------------------------

    @property
    def satellite_stale(self) -> bool:
        return self.satellite_updated_at is None or datetime.now(timezone.utc) - self.satellite_updated_at > self.satellite_ttl

    def refresh_satellite_in_background(self) -> None:
        if os.getenv("GISTDA_API_KEY"):
            self._start_job("gistda_flood", self.refresh_satellite, self.satellite_stale)

    async def refresh_satellite(self) -> None:
        provinces = [a.code for a in self.gazetteer.areas if a.level == "province"]
        adapter = GistdaFloodCells(provinces, self.amphoe_codes, window="7days")
        async with httpx.AsyncClient(headers={"User-Agent": "ThanPhai/0.1 (student project)"}) as client:
            raw = await adapter.fetch(client)
        now = datetime.now(timezone.utc)
        self.satellite_cells = {r.extra["area_code"]: {"cells": int(r.value), "approx_km2": r.extra["approx_km2"]}
                                for r in adapter.parse(raw, now)}
        self.satellite_updated_at = now
        self.source_status["gistda_flood"] = {"ok": not raw["failed"], "records": len(self.satellite_cells),
                                              "failed_districts": len(raw["failed"]), "checked_at": now.isoformat()}
        # Decisions read satellite_cells; rebuild them on the next request.
        self._inputs_changed = True

    # --- fire model (module 06) -------------------------------------------------------------

    def _load_fire_model(self):
        path = self.data_dir / "models" / "fire_model.joblib"
        if not path.exists():
            log.info("no fire model at %s; fire uses rules only", path)
            return None
        try:
            from risk_models.fire.dataset import FEATURES
            from risk_models.fire.model import FireModel

            model = FireModel.load(path)
            if model.clim is None or not model.amphoe_codes:
                log.warning("fire model %s lacks climatology; retrain it", path)
                return None
            extra = [n for n in getattr(model, "feature_names", FEATURES) if n not in FEATURES]
            if extra:
                log.warning("fire model %s needs %s, which the live service does not provide yet; fire uses rules only", model.version, extra)
                return None
            log.info("fire model %s loaded (watch>=%.3f, warning>=%.3f)", model.version, model.watch_threshold, model.warning_threshold)
            return model
        except Exception as exc:  # a bad model file must not stop the API
            log.warning("fire model could not be loaded: %s", exc)
            return None

    @property
    def fire_stale(self) -> bool:
        return self.fire_updated_at is None or datetime.now(timezone.utc) - self.fire_updated_at > self.fire_ttl

    def refresh_fire_in_background(self) -> None:
        if self.fire_model is not None and os.getenv("FIRMS_MAP_KEY"):
            self._start_job("fire_model", self.refresh_fire, self.fire_stale)

    async def refresh_fire(self) -> None:
        from risk_models.fire.live import fetch_recent, predict_today

        from risk_models.fire.static_sources import drop_static

        today = datetime.now(BANGKOK).date()
        points = drop_static(await fetch_recent(os.environ["FIRMS_MAP_KEY"], today), self.static_cells)

        def run() -> dict[str, dict]:
            mapped = self.mapper.assign_points(points["latitude"].tolist(), points["longitude"].tolist(), nearest=False)
            return predict_today(self.fire_model, points, mapped["amphoe_code"], today)

        self.fire_predictions = await asyncio.to_thread(run)
        self.fire_updated_at = datetime.now(timezone.utc)
        self.source_status["fire_model"] = {"ok": True, "records": len(self.fire_predictions),
                                            "version": self.fire_model.version, "checked_at": self.fire_updated_at.isoformat()}
        self._inputs_changed = True  # rebuild decisions with the new predictions on the next request

    def get_forecast(self, area_code: str) -> list[dict]:
        """Daily rain forecast for an area: its own entry, or its district's for a tambon."""
        if area_code in self.forecasts:
            return self.forecasts[area_code]
        area = self.gazetteer.by_code.get(area_code)
        if area is not None and area.level == "tambon":
            return self.forecasts.get(area.parent_code, [])
        return []

    # --- queries -------------------------------------------------------------------------

    def amphoe_for(self, code: str) -> list[str]:
        area = self.gazetteer.by_code.get(code)
        if area is None:
            return []
        if area.level == "amphoe":
            return [code]
        if area.level == "tambon":
            return [area.parent_code]
        return [c for c in self.amphoe_codes if c.startswith(code)]

    def get_assessments(self, area_code: str) -> list[dict]:
        """Decisions for an area. For a province, each hazard reports its worst district."""
        codes = self.amphoe_for(area_code)
        if len(codes) == 1:
            return self.decisions.get(codes[0], [])
        order = {None: -1, "NORMAL": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}
        worst: dict[str, dict] = {}
        for code in codes:
            for item in self.decisions.get(code, []):
                current = worst.get(item["hazard"])
                if current is None or order[item["level"]] > order[current["level"]]:
                    worst[item["hazard"]] = {**item, "worst_area": code}
        return list(worst.values())

    @staticmethod
    def _dam_rows(records) -> list[dict]:
        rows = [{"id": r.station_id, "storage_percent": r.value, "lat": r.lat, "lon": r.lon,
                 "province_code": f"TH{r.province_code}" if r.province_code else None,
                 "date": r.observed_at.date().isoformat() if r.observed_at else None, **r.extra} for r in records]
        return sorted(rows, key=lambda d: -(d["storage_percent"] or 0))

    @staticmethod
    def fresh_levels(records, now: datetime | None = None) -> list:
        now = now or datetime.now(timezone.utc)
        return [r for r in records if r.observed_at is not None and now - r.observed_at <= MAX_LEVEL_AGE]

    @staticmethod
    def river_status(above_bank: float | None) -> tuple[str, str]:
        if above_bank is None:
            return "UNKNOWN", "ไม่ทราบระดับตลิ่ง"
        if above_bank > 0:
            return "OVERFLOW", "น้ำล้นตลิ่ง"
        if above_bank >= NEAR_BANK_M:
            return "NEAR_BANK", "น้ำใกล้ล้นตลิ่ง"
        return "NORMAL", "น้ำต่ำกว่าตลิ่ง"

    @classmethod
    def _river_rows(cls, records) -> list[dict]:
        """One row per physical station, for stations with a flow gauge or with water over the bank.

        ThaiWater lists many RID stations twice ("C.13" and "ridhydro_C.13"); the key station carries the
        river name and graph history, the duplicate sometimes the only flow value.
        """
        groups: dict[tuple, list] = {}
        for r in records:
            code = r.extra.get("station_code")
            if not code or r.lat is None or r.lon is None:
                continue
            groups.setdefault((code, r.province_code), []).append(r)
        rows = []
        for (code, province), group in groups.items():
            group.sort(key=lambda r: (not r.extra.get("is_key_station"), r.extra.get("discharge_m3s") is None))
            main = group[0]

            def first(key, group=group):
                return next((r.extra.get(key) for r in group if r.extra.get(key) is not None), None)

            usable_bank = [r for r in group if r.extra.get("bank_quality") == "ok" and r.extra.get("above_bank_m") is not None]
            bank_row = usable_bank[0] if usable_bank else None
            above = bank_row.extra["above_bank_m"] if bank_row else None
            flow, qmax = first("discharge_m3s"), first("qmax_m3s")
            if flow is None and not (above is not None and above > 0):
                continue
            level_row = bank_row or main
            level, previous = level_row.value, level_row.extra.get("previous_msl")
            status, status_th = cls.river_status(above)
            observed = max((r.observed_at for r in group if r.observed_at), default=None)
            rows.append({
                "id": main.station_id, "code": code, "name_th": first("name_th"), "river_th": first("river_name"),
                "province_code": f"TH{province}" if province else None, "province_th": first("province_th"),
                "amphoe_th": first("amphoe_th"), "basin_th": first("basin_th"), "agency": first("agency_en"),
                "lat": main.lat, "lon": main.lon, "time": observed.isoformat() if observed else None,
                "level_msl": level,
                "level_change_m": round(level - previous, 2) if level is not None and previous is not None else None,
                "bank_msl": bank_row.extra.get("min_bank") if bank_row else None, "above_bank_m": above,
                "discharge_m3s": flow, "qmax_m3s": qmax,
                "capacity_percent": round(flow / qmax * 100) if flow and qmax else None,
                "status": status, "status_th": status_th,
                "is_key_station": any(r.extra.get("is_key_station") for r in group),
                "chain_order": CHAO_PHRAYA_CHAIN.index(code) if code in CHAO_PHRAYA_CHAIN else None,
            })
        order = {"OVERFLOW": 0, "NEAR_BANK": 1, "NORMAL": 2, "UNKNOWN": 3}
        return sorted(rows, key=lambda r: (order[r["status"]], -(r["discharge_m3s"] or 0)))

    def boundary(self, code: str) -> dict:
        """GeoJSON Feature of one admin area, simplified for the map. `code` must be a gazetteer code."""
        cached = self._boundaries.get(code)
        if cached is None:
            area = self.gazetteer.by_code[code]
            # `code` is validated against the gazetteer before it reaches this SQL filter.
            frame = gpd.read_file(self.data_dir / "admin_areas_web.gpkg", layer=area.level, where=f"code = '{code}'")
            tolerance = {"province": 0.002, "amphoe": 0.001, "tambon": 0.0003}[area.level]
            geometry = json.loads(frame.geometry.simplify(tolerance, preserve_topology=True).to_json(drop_id=True))["features"][0]["geometry"]
            west, south, east, north = frame.total_bounds
            cached = {"type": "Feature", "geometry": {**geometry, "coordinates": _round_coords(geometry["coordinates"])},
                      "properties": {"code": code, "level": area.level, "name_th": area.name_th},
                      "bbox": [round(float(v), 4) for v in (west, south, east, north)]}
            self._boundaries[code] = cached
        return cached

    def get_rivers(self, area_code: str | None = None) -> list[dict]:
        if area_code is None:
            return self.rivers
        return [r for r in self.rivers if r["province_code"] == area_code[:4]]

    async def _cached(self, key: tuple, ttl: timedelta, load) -> dict:
        """Small TTL cache for on-demand series, so a popular station is fetched once per period.

        Requests for the same key while it loads share one upstream call, and a failure is remembered for
        FAILURE_TTL, so repeated clicks (or many users) on a broken station never hammer ThaiWater.
        """
        now = datetime.now(timezone.utc)
        hit = self._graph_cache.get(key)
        if hit and now - hit[0] < (FAILURE_TTL if isinstance(hit[1], Exception) else ttl):
            if isinstance(hit[1], Exception):
                raise hit[1]
            return hit[1]
        task = self._inflight.get(key)
        if task is None:
            async def run():
                async with httpx.AsyncClient(headers=USER_AGENT, follow_redirects=True) as client:
                    return await load(client)

            task = self._inflight[key] = asyncio.create_task(run())
            task.add_done_callback(lambda _: self._inflight.pop(key, None))
        try:
            value = await asyncio.shield(task)
        except httpx.HTTPError as exc:
            self._graph_cache[key] = (now, exc)
            raise
        if len(self._graph_cache) > 500:
            self._graph_cache.clear()
        self._graph_cache[key] = (now, value)
        return value

    async def river_graph(self, station_id: str, days: int = 3) -> dict | None:
        """Hourly level and flow of a station in the river list; None for anything else (not an open proxy)."""
        if not station_id.isdigit() or not any(r["id"] == station_id for r in self.rivers):
            return None
        today = datetime.now(BANGKOK).date()
        return await self._cached(("river", station_id, days, today), GRAPH_TTL,
                                  lambda c: fetch_station_graph(c, int(station_id), days, today))

    async def dam_history(self, dam_id: str, series: str) -> dict | None:
        """This year and last year, day by day, with the dam's rule curves."""
        if not dam_id.isdigit() or not any(d["id"] == dam_id for d in self.dams):
            return None
        year = datetime.now(BANGKOK).year
        return await self._cached(("dam", dam_id, series, year), DAM_HISTORY_TTL,
                                  lambda c: fetch_dam_year(c, int(dam_id), series, [year - 1, year]))

    def get_dams(self, area_code: str | None = None) -> list[dict]:
        """All large dams, or those in the province of `area_code` (dams sit on province land, not districts)."""
        if area_code is None:
            return self.dams
        return [d for d in self.dams if d["province_code"] == area_code[:4]]

    def get_official_warnings(self) -> list[dict]:
        return self.warnings

    def map_layer(self, hazard: str) -> dict:
        features = []
        for code, geometry in self._geometry.items():
            item = next((i for i in self.decisions.get(code, []) if i["hazard"] == hazard), None)
            area = self.gazetteer.by_code[code]
            features.append({
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "code": code,
                    "name_th": area.name_th,
                    "province_th": self.gazetteer.by_code[area.parent_code].name_th,
                    "level": item["level"] if item else None,
                    **({k: v for k, v in item["observations"].items()} if item else {}),
                },
            })
        return {"type": "FeatureCollection", "features": features}
