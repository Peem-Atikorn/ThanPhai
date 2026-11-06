"""Builds and caches per-district hazard decisions from live data (modules 04 -> 05 -> 07).

Modules 04, 05, and 07 are called in-process as libraries for now. When they run as separate
services, only this file changes: the HTTP calls replace the library calls.
"""

import asyncio
import json
import logging
import math
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import time
import numpy as np

import geopandas as gpd
from shapely.geometry import Point, mapping, shape as shapely_shape
from shapely.ops import unary_union
import httpx

from decision_engine import DecisionEngine, DecisionInput
from external_data.adapters import (Air4ThaiPm25, FirmsHotspots, GdacsCyclones, GdacsEvents, OpenMeteoAirQuality, OpenMeteoHeatIndex, OpenMeteoMarine,
                                    OpenMeteoWaveField, ThaiWaterRain24h, ThaiWaterStorms, ThaiWaterWaterLevel, ThaiWaterWaterQuality,
                                    TmdEarthquakes, TmdNwpDailyForecast, TmdWarnings, UsgsEarthquakes, merge_quakes, sea_grid)
from external_data.adapters.base import REGION_BBOX, in_thailand_bbox
from external_data.adapters.gistda import GistdaFloodCells, fetch_flood_cells
from external_data.adapters.openrouteservice import ATTRIBUTION as ROUTE_ATTRIBUTION, RouteError, fetch_route
from external_data.adapters.thaiwater_dams import ThaiWaterDams
from external_data.adapters.thaiwater_graphs import DAM_SERIES, fetch_dam_year, fetch_dam_year_sync, fetch_station_graph
from external_data.adapters.thaiwater_media import parse_media
from external_data.adapters.tmd import THAI_MONTHS, classify_hazards, impact_window, latest_issues, read_days
from geo_integration import Gazetteer, hazard_areas, table_provinces, warning_provinces, warning_statements
from geo_integration.warning_areas import REGIONS
from geo_integration.area_mapper import AreaMapper
from geo_integration.features import fire_features, flood_features, pm25_features, rain_features
from thanphai_shared import AlertLevel, HazardType, SourceRecord

BANGKOK = timezone(timedelta(hours=7), "Asia/Bangkok")
RETRY_AFTER_FAILURE = timedelta(minutes=10)
# Shelter routes: GISTDA water in a box around both ends, routed around when openrouteservice accepts the area.
ROUTE_MAX_KM = 50           # straight line; farther than this, call 1784 rather than drive across a flood
ROUTE_FLOOD_PAD_DEG = 0.02  # ~2 km around the two ends
ROUTE_FLOOD_MAX_CELLS = 4000
ROUTE_FLOOD_TTL_S = 1800
ROUTE_END_CLEAR_DEG = 0.003  # ~300 m kept open around the start and the shelter
ROUTE_SIMPLIFY_DEG = 0.0005  # ~50 m: fewer points for openrouteservice
ROUTE_CROSSING_MIN_KM = 0.05
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
# Air4Thai stations that stopped reporting (seen live: 12 of 173 stuck 8 h behind) are dropped the same way.
MAX_PM25_AGE = timedelta(hours=6)
# Fires in Myanmar, Laos and Cambodia within this distance of a district centre are shown with its PM2.5:
# context for where the smoke may come from, not a rule input.
CROSS_BORDER_KM = 100
# Earthquakes count for district levels for a day; the list endpoint shows a week, the assistant up to 62 days.
QUAKE_WINDOW = timedelta(hours=24)
QUAKE_LIST_WINDOW = timedelta(days=7)
# TMD's RSS feed holds only its last 10 events (about a day when Laos/Myanmar are active), so every event seen
# is kept this long, and saved to QUAKE_HISTORY_FILE (if set) to survive restarts. 62 days so that "last month"
# (up to 61 days back on the last day of a month) is always covered; USGS is fetched for the same span.
QUAKE_HISTORY_WINDOW = timedelta(days=62)
NEAR_QUAKE_KM = 30  # an epicentre this close to a district centre is reported there, however weak the shaking
# Observation that decides which district speaks for a province when several share its worst level.
PROVINCE_TIEBREAK = {"earthquake": "quake_mmi_24h", "rain_storm": "rain_24h", "flood": "above_bank_m",
                     "air_quality": "pm25_24h", "heat": "heat_index_max_24h", "marine": "wave_height_max_24h", "fire": "hotspots_24h"}
# A cyclone this close (current position or forecast track) is mentioned on the district card even when its
# wind areas do not reach the district.
CYCLONE_NOTICE_KM = 800
# Wave arrows on the map: the Andaman Sea and the Gulf of Thailand every 0.5° (486 points, 396 at sea on 2026-10-22).
# Only sea points are asked between daily full-grid checks: ~1,700 of Open-Meteo's free 10,000 a day.
WAVE_BBOX = (92.5, 5.5, 105.5, 14.0)
WAVE_GRID_DEGREES = 0.5
WAVE_TTL = timedelta(hours=6)
WAVE_FULL_GRID_EVERY = timedelta(days=1)
# Cyclone wind areas on the map are drawn at country zoom; ~1 km detail is plenty.
CYCLONE_SIMPLIFY_DEGREES = 0.01
# Districts with at least this many past landslides (DMR inventory 1989-2019) get landslide checks.
LANDSLIDE_MIN_TRACES = 10
GRAPH_TTL = timedelta(minutes=15)
DAM_HISTORY_TTL = timedelta(hours=6)
FAILURE_TTL = timedelta(seconds=60)
USER_AGENT = {"User-Agent": "ThanPhai/0.1 (student project)"}

log = logging.getLogger("hazard_service")
# A new radar frame waits at most this long for Qwen; after that the template answers and Qwen's text replaces it in the cache.
NOWCAST_EXPLAIN_WAIT_S = 4.0
SOURCES = {
    HazardType.FLOOD: ["ThaiWater (สสน.)"],
    HazardType.RAIN_STORM: ["ThaiWater (สสน.)"],
    HazardType.FIRE: ["NASA FIRMS"],
    HazardType.DROUGHT: [],
    HazardType.AIR_QUALITY: ["กรมควบคุมมลพิษ (Air4Thai)"],
    HazardType.HEAT: ["Open-Meteo (แบบจำลองพยากรณ์อากาศ)"],
    HazardType.EARTHQUAKE: ["กรมอุตุนิยมวิทยา (กองเฝ้าระวังแผ่นดินไหว)", "USGS"],
    HazardType.MARINE: ["Open-Meteo Marine (แบบจำลองคลื่น)"],
    HazardType.TROPICAL_CYCLONE: ["GDACS (UN/EU)"],
    HazardType.LANDSLIDE: ["กรมทรัพยากรธรณี (ร่องรอยดินถล่ม)", "ThaiWater (สสน.)"],
}
# Open-Meteo forecasts per district, each on its own clock: (adapter, refresh interval, where to sample).
# "centres" = district centres; "offshore" = points at sea next to each district (see geo_integration.coast).
# Each refresh costs ~300 calls of Open-Meteo's free 10,000 a day (see OpenMeteoGrid): ~4,800 a day in all.
GRID_MODELS = {
    # CAMS runs twice a day.
    "air_model": (OpenMeteoAirQuality, timedelta(hours=6), "centres"),
    "heat_model": (OpenMeteoHeatIndex, timedelta(hours=3), "centres"),
    "marine_model": (OpenMeteoMarine, timedelta(hours=6), "offshore"),
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


def thai_date(day: date) -> str:
    """"5 พฤศจิกายน 2569", as TMD writes dates."""
    return f"{day.day} {list(THAI_MONTHS)[day.month - 1]} {day.year + 543}"


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
        self._radar_nowcast_lock = asyncio.Lock()
        self._radar_nowcast_cache: tuple[int, dict, float] | None = None
        self._thai_radar_mask: np.ndarray | None = None
        self._warning_ai_cache: dict[tuple, tuple[str | None, str | None]] = {}
        self._background: asyncio.Task | None = None
        # Set when slower inputs (satellite, fire model) change, so decisions are rebuilt without
        # pretending the live data is missing (clearing updated_at showed "data time: —" in the UI).
        self._inputs_changed = False
        # Slow inputs that had arrived when the current decisions were built (see inputs_pending).
        self._built_with: set[str] = set()
        self._drought: dict | None = None
        self.dams: list[dict] = []  # 35 large dams, refreshed with the live data
        self.rivers: list[dict] = []  # river stations with a flow gauge or over their bank
        self.water_quality: list[dict] = []  # fresh readings of the automatic water-quality stations, saltiest first
        self.storms: list[dict] = []  # active tropical storms (HII), one row per storm with its track
        self.media: dict | None = None  # radar, rain-forecast, storm and wave images
        self.quakes: list[dict] = []  # TMD + USGS, last QUAKE_HISTORY_WINDOW, newest first
        self.pm25_stations: list[dict] = []  # fresh Air4Thai readings, for station points on the map
        self.regional_events: list[dict] = []  # GDACS events around South-East Asia
        self.cyclone_records: list | None = None  # GDACS wind areas and track points; None until GDACS answers
        self.cyclone_updated_at: datetime | None = None  # last time GDACS answered, not the last refresh
        self._cyclone_features: tuple[list | None, list[dict]] = (None, [])  # (records, overlay features) built from them
        self.wave_points: list[dict] = []  # sea grid for the wave arrows (map overlay)
        self.wave_updated_at: datetime | None = None
        self._wave_grid: list[tuple[str, float, float]] | None = None  # sea points only, from the last full-grid check
        self._wave_grid_checked_at: datetime | None = None
        self._graph_cache: dict[tuple, tuple[datetime, dict]] = {}
        self._agent_history_lock = threading.Lock()
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
        # River-level forecast (module 06): trained offline by scripts/train_water_model.py; optional, display only.
        self.water_model = self._load_water_model()
        # Known industrial heat sources (cement kilns, refineries); the live FIRMS feed cannot tell them apart.
        from risk_models.fire import static_sources

        self.static_cells = static_sources.load(data_dir / "firms_static_cells.json")
        self.fire_ttl = timedelta(hours=3)
        self.fire_predictions: dict[str, dict] = {}
        self.fire_updated_at: datetime | None = None
        # GRID_MODELS results: name -> area code -> record (value and extra).
        self.grid: dict[str, dict] = {name: {} for name in GRID_MODELS}
        self.grid_updated_at: dict[str, datetime | None] = {name: None for name in GRID_MODELS}
        self._offshore: list[tuple[str, float, float]] | None = None  # built on the first marine refresh
        self._last_quakes: list[dict] | None = None
        self._quake_file = Path(os.environ["QUAKE_HISTORY_FILE"]) if os.getenv("QUAKE_HISTORY_FILE") else None
        # TMD events seen, when the feed last answered, and the spans it may have missed (see _remember_tmd_quakes).
        self._tmd_quakes: dict[str, SourceRecord] = {}
        self._tmd_last_ok: datetime | None = None
        self._tmd_gaps: list[tuple[datetime, datetime]] = []
        self._load_quake_history()
        self._last_pm25: list[dict] | None = None
        # Past landslides per district, from scripts/build_landslide_districts.py; optional.
        self.landslide_traces = self._load_landslide_traces()
        # DDPM's temporary shelters, from scripts/build_shelters.py; optional (a fixed list, read once).
        self.shelter_data = self._load_shelters()
        self._flood_shapes: dict[tuple, tuple[float, dict]] = {}  # GISTDA water per route box, for ROUTE_FLOOD_TTL_S
        self._geometry = self._load_map_geometry()

    def _load_map_geometry(self) -> dict[str, dict]:
        amphoe = gpd.read_file(self.data_dir / "admin_areas_web.gpkg", layer="amphoe")
        amphoe["geometry"] = amphoe.geometry.simplify(MAP_SIMPLIFY_DEGREES, preserve_topology=True)
        features = json.loads(amphoe[["code", "geometry"]].to_json(drop_id=True))["features"]
        return {f["properties"]["code"]: {**f["geometry"], "coordinates": _round_coords(f["geometry"]["coordinates"])}
                for f in features}

    def _slow_inputs(self) -> dict[str, datetime | None]:
        return {"gistda_flood": self.satellite_updated_at, "tmd_nwp_daily": self.forecast_updated_at, "fire_model": self.fire_updated_at,
                **self.grid_updated_at}

    @property
    def inputs_pending(self) -> list[str]:
        """Slow inputs still loading for the first time (after a restart), so clients can check back soon.

        An input that has arrived stays pending until the decisions are rebuilt with it: otherwise a client
        polling in between got "not pending" with the old, empty values and stopped polling."""
        return [name for name, updated in self._slow_inputs().items()
                if (updated is None and name in self._jobs and not self._jobs[name].done())
                or (updated is not None and name not in self._built_with)]

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
            built_with = {name for name, updated in self._slow_inputs().items() if updated is not None}
            results, warnings_result = await self._fetch()
            media = results.pop("thaiwater_media", None)
            raw = (results, warnings_result)
            # Mapping ~5,000 points and deciding 928 x 5 hazards is CPU work; run it off the event loop
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
            quality = results.get("thaiwater_waterquality")
            if quality is not None and not isinstance(quality, Exception):
                self.water_quality = self._water_quality_rows(self.fresh_levels(quality))
            storms = results.get("thaiwater_storms")
            if storms is not None and not isinstance(storms, Exception):
                self.storms = self._storm_rows(storms)
            gdacs = results.get("gdacs")
            if gdacs is not None and not isinstance(gdacs, Exception):
                self.regional_events = self._regional_rows(gdacs)
            cyclones = results.get("gdacs_cyclones")
            if cyclones is not None and not isinstance(cyclones, Exception):
                self.cyclone_records = cyclones  # keep the last good tracks if GDACS failed
                self.cyclone_updated_at = datetime.now(timezone.utc)
            # Swap everything at once so readers never see a half-updated state.
            self.decisions = decisions
            if self._last_quakes is not None:
                self.quakes = self._last_quakes
            if self._last_pm25 is not None:
                self.pm25_stations = self._last_pm25
            if warnings is not None:
                self.warnings = warnings  # keep the last good warnings if TMD failed this time
            # Merge, not replace: the forecast refresh writes its own entry on a different schedule.
            self.source_status = {**self.source_status, **status}
            self._built_with = built_with
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
            # One regional download: hotspots in Thailand drive the fire rules, the rest is PM2.5 context.
            "nasa_firms": FirmsHotspots(bbox=REGION_BBOX),
            "air4thai": Air4ThaiPm25(),
            "tmd_earthquakes": TmdEarthquakes(),
            "usgs_earthquakes": UsgsEarthquakes(days=QUAKE_HISTORY_WINDOW.days),
            "gdacs": GdacsEvents(),
            "gdacs_cyclones": GdacsCyclones(),
            "thaiwater_storms": ThaiWaterStorms(),
        }
        async with httpx.AsyncClient(headers=USER_AGENT, follow_redirects=True) as client:
            results = await asyncio.gather(*(a.run(client) for a in adapters.values()), self._fetch_main_bundle(client),
                                           TmdWarnings().run_warnings(client), return_exceptions=True)
        out = dict(zip(adapters, results[:-2]))
        bundle = results[-2]
        # thailand_main is ~12 MB; download it once for the dams, the water-quality stations and the weather images.
        out["thaiwater_dams"], out["thaiwater_waterquality"], out["thaiwater_media"] = (bundle,) * 3 if isinstance(bundle, Exception) else bundle
        return out, results[-1]

    @staticmethod
    async def _fetch_main_bundle(client: httpx.AsyncClient) -> tuple[list, list, dict]:
        adapter = ThaiWaterDams()
        fetched_at = datetime.now(timezone.utc)
        raw = await adapter.fetch(client)
        return adapter.parse(raw, fetched_at), ThaiWaterWaterQuality().parse(raw, fetched_at), parse_media(raw, fetched_at)

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
            warnings = []
            for w in latest_issues(warnings_result):
                text = f"{w.title} {w.body or w.headline or ''}"
                issued = w.announced_at.astimezone(BANGKOK).date()
                # The PDF body has every region's statement with its own days; the title's "(มีผลกระทบ 5-9)" would
                # give the south 5-9 Nov where the body says 7-9. Without a body: the title and the headline.
                texts = [w.body] if w.body else [w.title, w.headline or ""]
                statements = warning_statements(self.gazetteer, texts, classify_hazards, lambda part: read_days(part, issued))
                areas = hazard_areas(self.gazetteer, texts, classify_hazards, lambda part: read_days(part, issued))
                window = impact_window(w.title, issued)
                periods, rain_days = self._periods(statements, w.rain_days, window)
                provinces = {hazard.value: sorted(codes) for hazard, codes in areas.items()}
                for hazard, codes in periods.items():  # daily tables can name provinces the statements reach only by region
                    provinces[hazard] = sorted(set(provinces.get(hazard, [])) | set(codes))
                w_dict = {
                    **w.model_dump(mode="json"),
                    "provinces": warning_provinces(self.gazetteer, text),
                    "provinces_by_hazard": provinces,
                    "periods_by_hazard": periods,
                    "by_region": self._by_region(statements, window, tables=bool(w.rain_days)),
                    "rain_days": rain_days,
                    "impact_from": window[0].isoformat() if window else None,
                    "impact_to": window[1].isoformat() if window else None,
                    # Days a statement gives for its own provinces ("5–7 พ.ย. ประเทศไทยตอนบน"); others use impact_from/to.
                    "impact_by_hazard": {
                        hazard.value: {code: [days[0].isoformat(), days[1].isoformat()] for code, days in codes.items() if days}
                        for hazard, codes in areas.items()
                    },
                }
                ai_summary, ai_attribution = self._audit_and_summarize_warning(w_dict)
                w_dict["ai_summary"] = ai_summary
                w_dict["ai_attribution"] = ai_attribution
                warnings.append(w_dict)
        # Every warning in TMD's current feed is in force: its EffectEndDate is when the next issue is due, not when
        # the hazard ends. If TMD failed this time, the last good warnings still apply.
        official = self._official_by_province(warnings if warnings is not None else self.warnings, self._today(),
                                              self.engine.rules["official_warning"]["lead_days"])

        if records["thaiwater_waterlevel"]:
            fresh = self.fresh_levels(records["thaiwater_waterlevel"])
            dropped = len(records["thaiwater_waterlevel"]) - len(fresh)
            status["thaiwater_waterlevel"]["stale_dropped"] = dropped
            # Every reading old means the feed itself stopped: say so instead of showing "no flood".
            records["thaiwater_waterlevel"] = fresh or None
            if not fresh:
                status["thaiwater_waterlevel"] = {"ok": False, "error": "stale", "checked_at": now}
        if records["air4thai"]:
            fresh_pm25 = self.fresh_levels(records["air4thai"], max_age=MAX_PM25_AGE)
            status["air4thai"]["stale_dropped"] = len(records["air4thai"]) - len(fresh_pm25)
            records["air4thai"] = fresh_pm25 or None
            if not fresh_pm25:
                status["air4thai"] = {"ok": False, "error": "stale", "checked_at": now}
        degraded = {
            HazardType.FLOOD: [] if records["thaiwater_waterlevel"] is not None else ["ThaiWater water level"],
            HazardType.RAIN_STORM: [] if records["thaiwater_rain_24h"] is not None else ["ThaiWater rain"],
            HazardType.FIRE: [] if records["nasa_firms"] is not None else ["NASA FIRMS"],
            HazardType.AIR_QUALITY: [] if records["air4thai"] is not None else ["Air4Thai"],
            HazardType.EARTHQUAKE: [name for name, source in (("TMD earthquakes", "tmd_earthquakes"), ("USGS", "usgs_earthquakes"))
                                    if records.get(source) is None],
            HazardType.TROPICAL_CYCLONE: [] if records.get("gdacs_cyclones") is not None else ["GDACS"],
            HazardType.LANDSLIDE: [] if records["thaiwater_rain_24h"] is not None else ["ThaiWater rain"],
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
        cross_border: dict[str, int] = {}
        if records["nasa_firms"] is not None:
            regional = records["nasa_firms"]
            records["nasa_firms"] = [r for r in regional if in_thailand_bbox(r.lat, r.lon)]
            status["nasa_firms"]["records"] = len(records["nasa_firms"])
            try:
                cross_border = self._cross_border_counts(regional)
                status["nasa_firms"]["districts_near_cross_border_fires"] = len(cross_border)
            except Exception:
                log.exception("cross-border hotspot counts failed")
        if records["nasa_firms"] and self.static_cells:
            records["nasa_firms"] = self._drop_static_records(records["nasa_firms"])
        fire = build("nasa_firms", HazardType.FIRE, lambda rows: fire_features(rows, self.amphoe_codes))
        pm25 = build("air4thai", HazardType.AIR_QUALITY, pm25_features)
        self._last_pm25 = None if records["air4thai"] is None else sorted((
            {"id": r.station_id, "name_th": r.extra.get("name_th"), "area_th": r.extra.get("area_th"), "lat": r.lat, "lon": r.lon,
             "pm25": r.value, "aqi": r.extra.get("aqi"), "time": r.observed_at.isoformat()}
            for r in records["air4thai"] if r.lat is not None and r.lon is not None), key=lambda s: -s["pm25"])
        quakes = self._quake_shaking(records.get("tmd_earthquakes"), records.get("usgs_earthquakes"))
        cyclones = self._cyclone_exposure(records.get("gdacs_cyclones"))

        def value(frame, column: str, code: str) -> float | None:
            return _clean(frame[column].get(code)) if frame is not None and not frame.empty else None

        # Snapshots: background refreshes may swap these dicts while decisions are built.
        satellite = dict(self.satellite_cells)
        fire_pred = dict(self.fire_predictions)
        forecasts = dict(self.forecasts)
        air_model = {code: r.value for code, r in self.grid["air_model"].items()}
        heat = dict(self.grid["heat_model"])
        this_hour = datetime.now(BANGKOK).replace(minute=0, second=0, microsecond=0).isoformat()
        marine = dict(self.grid["marine_model"])
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
            pm25_station = value(pm25, "pm25_24h", code)
            observations = {
                HazardType.FLOOD: {"above_bank_m": above_bank,
                                   "satellite_flood_cells_7d": float(sat["cells"]) if sat else None},
                # Chance of rain is shown only: rules.yaml does not read it, so it never sets the level.
                HazardType.RAIN_STORM: {"rain_24h": value(rain, "rain_24h", code),
                                        **self._rain_chance(heat.get(code), this_hour)},
                HazardType.FIRE: {"hotspots_24h": value(fire, "hotspots_24h", code)},
                HazardType.DROUGHT: drought_obs,
                # A station reading wins; the model fills only districts without one.
                HazardType.AIR_QUALITY: {"pm25_24h": pm25_station,
                                         "pm25_model_24h": air_model.get(code) if pm25_station is None else None,
                                         # None when FIRMS failed, 0 when it worked and saw nothing nearby.
                                         "cross_border_hotspots_24h": (float(cross_border.get(code, 0))
                                                                       if records["nasa_firms"] is not None else None)},
                # The level stays on the 24-h peak (rules.yaml); "now" is the forecast hour we are in, for display.
                HazardType.HEAT: {"heat_index_max_24h": heat[code].value if code in heat else None,
                                  "heat_index_now": (heat[code].extra.get("hourly", {}).get(this_hour)
                                                     if code in heat else None)},
                # Inland districts have no sea: no value, and the card says why.
                HazardType.MARINE: {"wave_height_max_24h": marine[code].value if code in marine else None},
                HazardType.LANDSLIDE: self._landslide_observations(code, value(rain, "rain_24h", code), forecasts),
                # 0 = GDACS works and no storm wind area reaches the district; None = GDACS failed.
                HazardType.TROPICAL_CYCLONE: ({"cyclone_wind_kmh": None} if cyclones is None else
                                              {k: v for k, v in cyclones.get(code, {"cyclone_wind_kmh": 0.0}).items()
                                               if not k.startswith("_")}),
                # No earthquake in the last day = not felt (MMI 1); None only when both feeds failed.
                HazardType.EARTHQUAKE: ({"quake_mmi_24h": None} if quakes is None else
                                        {k: v for k, v in quakes[code].items() if not k.startswith("_")} if code in quakes
                                        else {"quake_mmi_24h": 1.0}),
            }
            items = []
            for hazard, obs in observations.items():
                # A TMD warning applies to districts in the provinces it names; rules.yaml sets the level it gives.
                warned = official.get(code[:4], {}).get(hazard, [])
                model_level = model_conf = None
                if hazard == HazardType.FIRE and pred:
                    # The model's level is one input; rules can still raise it, and it never lowers them.
                    p = pred["probability"]
                    model_level = AlertLevel[pred["level"]]
                    # Confidence in the stated level: p for a fire level, 1 - p for NORMAL.
                    model_conf = p if model_level != AlertLevel.NORMAL else 1 - p
                    obs = {**obs, "fire_probability_7d": pred["probability"]}
                active = [kinds for _, start, kinds in warned if start is None]
                d = self.engine.decide(DecisionInput(hazard=hazard, observations=obs, degraded_sources=degraded.get(hazard, []),
                                                     model_level=model_level, model_confidence=model_conf,
                                                     official_warning_active=bool(active),
                                                     official_warning_upcoming=any(start is not None for _, start, _ in warned),
                                                     official_warning_kinds=[k for kinds in active for k in kinds]))
                notices = [f"กรมอุตุนิยมวิทยาประกาศเตือน: {title}" if start is None else
                           f"กรมอุตุนิยมวิทยาประกาศเตือนล่วงหน้า (คาดว่าเริ่ม {thai_date(start)}): {title}"
                           for title, start, _ in warned] + list(d.notices)
                sources = list(SOURCES[hazard]) + (["กรมอุตุนิยมวิทยา (ประกาศเตือน)"] if warned else [])
                if obs.get("satellite_flood_cells_7d") is not None:
                    sources.append("GISTDA (ดาวเทียม)")
                if hazard == HazardType.DROUGHT and drought:
                    sources.append(f"NASA MODIS NDVI ({drought['month']})")
                    if drought_note:
                        notices.append(drought_note)
                if hazard == HazardType.HEAT and code in heat:
                    peak = heat[code].extra
                    notices.append(f"ค่าคาดการณ์จากแบบจำลอง ช่วงร้อนสุด {peak['peak_time'][11:16]} น. "
                                   f"อุณหภูมิ {peak['temperature_c']:.0f}°C ความชื้น {peak['humidity_pct']:.0f}%")
                if hazard == HazardType.RAIN_STORM and code in heat and heat[code].extra.get("rain_chance"):
                    chance = heat[code].extra["rain_chance"]
                    notices.append(f"แบบจำลอง Open-Meteo คาดโอกาสฝนตกสูงสุด {chance['max_pct']:.0f}% "
                                   f"ราว {chance['peak_time'][11:16]} น. (ใน 24 ชม. ข้างหน้า)")
                    sources.append("Open-Meteo (แบบจำลองโอกาสฝน)")
                if hazard == HazardType.MARINE:
                    if code in marine:
                        notices.append(f"คลื่นสูงสุดคาดว่าราว {marine[code].extra['peak_time'][11:16]} น. (ค่าจากแบบจำลอง)")
                    elif self.grid_updated_at["marine_model"] is not None:
                        notices = ["อำเภอนี้ไม่ติดทะเล"]
                if hazard == HazardType.LANDSLIDE:
                    traces = self.landslide_traces.get(code, 0) if self.landslide_traces is not None else None
                    if traces is None:
                        notices.append("ยังไม่มีข้อมูลพื้นที่เสี่ยงดินถล่ม (รัน scripts/build_landslide_districts.py)")
                    elif traces < LANDSLIDE_MIN_TRACES:
                        notices = [f"ไม่ใช่พื้นที่ที่เคยเกิดดินถล่มบ่อย (พบร่องรอย {traces} จุด) จึงไม่ประเมินดินถล่ม"]
                    elif obs.get("landslide_forecast_rain_24h") is not None:
                        sources.append("กรมอุตุนิยมวิทยา (พยากรณ์ฝน)")
                if hazard == HazardType.TROPICAL_CYCLONE and cyclones and code in cyclones:
                    notices.append(self._cyclone_notice(cyclones[code]))
                if hazard == HazardType.EARTHQUAKE and quakes and code in quakes:
                    notices.append(self._quake_notice(quakes[code]))
                if obs.get("pm25_model_24h") is not None:
                    sources = ["Open-Meteo / CAMS (แบบจำลอง)"]
                    notices.append("ไม่มีสถานีวัดฝุ่นในอำเภอนี้ ใช้ค่าคาดการณ์จากแบบจำลองเฉลี่ย 24 ชม. ข้างหน้า")
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
    def _today() -> date:
        return datetime.now(BANGKOK).date()

    @staticmethod
    def _official_by_province(warnings: list[dict], today: date, lead_days: int) -> dict[str, dict[HazardType, list[tuple[str, date | None, list[str]]]]]:
        """Province code -> hazard -> (title, first impact day if still more than `lead_days` ahead, else None, kinds of
        the periods in force: decision_engine OFFICIAL_KINDS) of the TMD warnings that name the province for that
        hazard. Each period counts on its own days (one without days: the warning's), from `lead_days` before its first
        day to its last; the province is left out once all have passed."""
        found: dict[str, dict[HazardType, list[tuple[str, date | None, list[str]]]]] = {}
        for warning in warnings:
            whole = (warning.get("impact_from"), warning.get("impact_to"))
            for hazard, provinces in warning.get("provinces_by_hazard", {}).items():
                own = warning.get("impact_by_hazard", {}).get(hazard, {})
                for province in provinces:
                    if "periods_by_hazard" in warning:
                        periods = warning["periods_by_hazard"].get(hazard, {}).get(province) or [[None, None, "named"]]
                    else:  # cached before periods existed: one period, min_level, as it was then
                        periods = [[*own.get(province, whole), "named"]]
                    active, upcoming = [], []
                    for start, end, kind in periods:
                        if isinstance(kind, bool):  # cached when a period held only "sets min_level"
                            kind = "named" if kind else "regional"
                        first, last = (date.fromisoformat(d) if d else None
                                       for d in ((start, end) if start or end else whole))
                        if last is not None and today > last:
                            continue
                        if first is not None and today < first - timedelta(days=lead_days):
                            upcoming.append(first)
                        else:
                            active.append(kind)
                    if active:
                        entry = (warning["title"], None, active)
                    elif upcoming:
                        entry = (warning["title"], min(upcoming), [])
                    else:
                        continue
                    found.setdefault(province, {}).setdefault(HazardType(hazard), []).append(entry)
        return found

    def _periods(self, statements: list, rain_days: list, window: tuple[date, date] | None) -> tuple[dict, list[dict]]:
        """Hazard -> province -> [first day, last day, kind] periods of a warning, and its daily tables for display.

        Statements: "named" for a province named by name, or for a region whose statement uses strong_words when the
        bulletin has no daily tables; otherwise "regional". A region WATCH on 5-7 Nov and WARNING on 8-9 Nov is a WATCH
        on 5 Nov, as each period counts only on its own days.

        Daily tables (issue 288/2569): a province listed for heavy rain gets "heavy_rain", for very heavy rain
        "very_heavy_rain", from its first listed day of that kind to the end of the bulletin's period for it, not the
        listed day alone: เชียงใหม่ listed on 6 Nov stays on through upper Thailand's 5-7 Nov. Flood: very heavy rain
        only where a statement warns that province of flood; the flood statement's own provinces stay "regional".
        """
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        periods: dict[str, dict[str, list]] = {}
        ends: dict[str, dict[str, date | None]] = {}  # hazard -> province -> last statement day (None: the window's)
        for s in statements:
            first, last = s.days or (None, None)
            kind = "named" if not rain_days and self._strong(s.wording) else "regional"
            for hazard in s.hazards:
                for code in sorted(s.provinces):
                    periods.setdefault(hazard.value, {}).setdefault(code, []).append(
                        [iso(first), iso(last), "named" if code in s.named else kind])
                    end = last or (window[1] if window else None)
                    old = ends.setdefault(hazard.value, {}).get(code)
                    ends[hazard.value][code] = end if old is None or (end and end > old) else old

        listed: dict[str, dict[bool, list[date]]] = {}  # province -> very heavy? -> days
        shown = []
        for row in rain_days:
            codes = table_provinces(self.gazetteer, row.areas)
            for code in codes:
                listed.setdefault(code, {}).setdefault(row.very_heavy, []).append(row.day)
            shown.append({"day": row.day.isoformat(), "very_heavy": row.very_heavy,
                          "provinces": [self.gazetteer.by_code[c].name_th for c in codes if c in self.gazetteer.by_code]})
        for code, by_kind in listed.items():
            last_listed = max(d for days in by_kind.values() for d in days)
            for hazard, kind_of in (("rain_storm", None), ("flood", "flood")):
                if kind_of and code not in ends.get("flood", {}):
                    continue  # no flood statement for this province: tables are rain only
                statement_end = ends.get(hazard, {}).get(code) or (window[1] if window else None)
                end = max(last_listed, statement_end) if statement_end else last_listed
                for very, days in sorted(by_kind.items()):
                    if hazard == "flood" and not very:
                        continue
                    periods.setdefault(hazard, {}).setdefault(code, []).append(
                        [min(days).isoformat(), end.isoformat(), "very_heavy_rain" if very else "heavy_rain"])
        return periods, shown

    def _strong(self, wording: str) -> bool:
        """The statement says heavy impact is likely (rules.yaml strong_words), so a region keeps min_level."""
        compact = re.sub(r"\s+", "", wording)
        return any(word in compact for word in self.engine.rules["official_warning"]["strong_words"])

    def _by_region(self, statements: list, window: tuple[date, date] | None, tables: bool = False) -> list[dict]:
        """The warning as the overview shows it: one entry per days, level and areas, with the hazards for exactly those
        areas. Issue 288/2569: upper Thailand 5-7 Nov WATCH (rain), the south 7-9 Nov WARNING (rain, flood).

        Statements with the same days, level and hazards are joined first, and a region whose provinces all lie in
        another region of the group is left out ("ส่วนภาคเหนือ ภาคกลาง … ในระยะถัดไป" inside "ประเทศไทยตอนบน").
        Then groups for the same areas join their hazards. Rain in the north and flood in the south stay apart.
        With daily tables, a region's level is regional_level: the tables (shown apart) say where it is heavier.
        """
        rules = self.engine.rules["official_warning"]
        groups: dict[tuple, list[str]] = {}

        def add(days, level, hazards, labels):
            first, last = days or window or (None, None)
            areas = groups.setdefault((first, last, level, tuple(h.value for h in hazards)), [])
            areas += [label for label in labels if label not in areas]

        for s in statements:
            level = rules["min_level"] if not tables and self._strong(s.wording) else rules["regional_level"]
            if s.regions:
                add(s.days, level, s.hazards, s.regions)
            if s.named:
                names = [self.gazetteer.by_code[c].name_th for c in sorted(s.named) if c in self.gazetteer.by_code]
                add(s.days, rules["min_level"], s.hazards, [f"จ.{n}" for n in names])
        entries: dict[tuple, dict] = {}
        for (first, last, level, hazards), areas in groups.items():
            regions = [a for a in areas if a in REGIONS]
            areas = [a for a in areas if a not in REGIONS or not any(b != a and set(REGIONS[a]) < set(REGIONS[b]) for b in regions)]
            entry = entries.setdefault((first, last, level, tuple(areas)), {
                "from": first.isoformat() if first else None, "to": last.isoformat() if last else None,
                "level": level, "hazards": [], "areas": areas})
            entry["hazards"] += [h for h in hazards if h not in entry["hazards"]]
        # By first day, then the higher level first.
        return sorted(entries.values(), key=lambda e: (e["from"] or "", e["level"] != rules["min_level"]))

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

    def _quake_shaking(self, tmd: list | None, usgs: list | None) -> dict[str, dict] | None:
        """Strongest expected shaking per district from the last day's earthquakes; None when both feeds failed.

        Also keeps the month's merged list (with TMD events seen in earlier refreshes) for /v1/earthquakes and the
        assistant (swapped in by refresh()).
        """
        from risk_models.earthquake import mmi_label_th, shaking_at

        self._last_quakes = None
        if tmd is None and usgs is None:
            return None
        now = datetime.now(timezone.utc)
        if tmd is not None:
            tmd = self._remember_tmd_quakes(tmd, now)
        else:
            tmd = list(self._tmd_quakes.values())
        merged = sorted(merge_quakes(tmd or [], usgs or []), key=lambda r: r.observed_at, reverse=True)
        week = [r for r in merged if now - r.observed_at <= QUAKE_HISTORY_WINDOW]
        self._last_quakes = [{
            "id": r.station_id, "time": r.observed_at.isoformat(), "magnitude": r.value, "lat": r.lat, "lon": r.lon,
            "depth_km": r.extra.get("depth_km"), "place_th": r.extra.get("place_th"), "where_th": r.extra.get("where_th"),
            "place": r.extra.get("place"), "url": r.extra.get("url"), "tsunami": r.extra.get("tsunami", False),
            "source": "กรมอุตุนิยมวิทยา" if r.source == "tmd_earthquakes" else "USGS",
        } for r in week]
        recent = [r for r in week if now - r.observed_at <= QUAKE_WINDOW]
        out: dict[str, dict] = {}
        for code in self.amphoe_codes:
            area = self.gazetteer.by_code[code]
            best = None
            for r in recent:
                value, km = shaking_at(area.lat, area.lon, r.lat, r.lon, r.value, r.extra.get("depth_km"))
                # Far-away small quakes would clutter answers, but one centred in or next to the district is news
                # there even when the estimate at the district centre is below MMI 2 (Pong, Phayao, M2.3 on 4 Nov
                # 2026: felt by villagers, MMI 1.4 at the centroid 13 km away). The level still comes from the MMI
                # rules. Skip the unfelt far ones before choosing, so a weak far quake cannot push out a near one.
                if value < 2 and km > NEAR_QUAKE_KM:
                    continue
                # Strongest shaking first; on a tie (small quakes all floor at MMI 1) the closest epicentre.
                if best is None or (value, -km) > (best[0], -best[1]):
                    best = (value, km, r)
            if best is None:
                continue
            value, km, r = best
            out[code] = {"quake_mmi_24h": round(value, 1), "quake_magnitude": r.value, "quake_distance_km": round(km),
                         "_label": mmi_label_th(value), "_place": r.extra.get("place_th") or r.extra.get("place") or "",
                         "_time": r.observed_at,
                         "_epicentre_mmi": shaking_at(r.lat, r.lon, r.lat, r.lon, r.value, r.extra.get("depth_km"))[0]}
        return out

    @staticmethod
    def _quake_key(r: SourceRecord) -> str:
        return r.station_id or f"{r.observed_at.isoformat()}:{r.lat}:{r.lon}"

    def _remember_tmd_quakes(self, tmd: list[SourceRecord], now: datetime) -> list[SourceRecord]:
        """Add this feed's events to those seen before, drop ones older than the window, save; return them all.

        The feed lists only its last 10 events. When even the oldest of them is newer than the previous answer
        (TMD or the API was down, or many quakes in between), events in that span may be missing: it is kept as a
        gap, so a count over a period that overlaps it is reported as partial. An empty feed counts as no answer.
        """
        if tmd:
            oldest = min(r.observed_at for r in tmd)
            if self._tmd_last_ok is not None and oldest > self._tmd_last_ok:
                start = self._tmd_last_ok
                if self._tmd_gaps and self._tmd_gaps[-1][1] >= start:  # joins the previous gap
                    start = self._tmd_gaps.pop()[0]
                self._tmd_gaps.append((start, oldest))
            self._tmd_last_ok = now
        self._tmd_quakes.update((self._quake_key(r), r) for r in tmd)
        self._tmd_quakes = {k: r for k, r in self._tmd_quakes.items() if now - r.observed_at <= QUAKE_HISTORY_WINDOW}
        self._tmd_gaps = [(s, e) for s, e in self._tmd_gaps if now - e <= QUAKE_HISTORY_WINDOW]
        if self._quake_file is not None and tmd:
            try:
                self._quake_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._quake_file.with_suffix(".tmp")
                state = {"events": [r.model_dump(mode="json") for r in self._tmd_quakes.values()],
                         "last_ok": self._tmd_last_ok.isoformat() if self._tmd_last_ok else None,
                         "gaps": [[s.isoformat(), e.isoformat()] for s, e in self._tmd_gaps]}
                tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self._quake_file)
            except OSError:
                log.exception("could not save earthquake history to %s", self._quake_file)
        return list(self._tmd_quakes.values())

    def _load_quake_history(self) -> dict[str, SourceRecord]:
        """Load the saved TMD events, last answer time and gaps; returns the events."""
        if self._quake_file is None or not self._quake_file.exists():
            return self._tmd_quakes
        try:
            state = json.loads(self._quake_file.read_text(encoding="utf-8"))
            if isinstance(state, list):  # first format: events only
                state = {"events": state}
            rows = [SourceRecord.model_validate(row) for row in state.get("events", [])]
            last_ok = datetime.fromisoformat(state["last_ok"]) if state.get("last_ok") else None
            gaps = [(datetime.fromisoformat(s), datetime.fromisoformat(e)) for s, e in state.get("gaps", [])]
        except (OSError, ValueError, TypeError, AttributeError):
            log.exception("could not read earthquake history %s; starting empty", self._quake_file)
            return self._tmd_quakes
        self._tmd_quakes = {self._quake_key(r): r for r in rows}
        # Without a saved answer time (first format), the newest kept event is the latest point known to be covered.
        self._tmd_last_ok = last_ok or max((r.observed_at for r in rows), default=None)
        self._tmd_gaps = gaps
        return self._tmd_quakes

    def get_quake_gaps(self) -> list[dict]:
        """Spans in which TMD events may be missing from the kept history (the agent marks such counts partial)."""
        return [{"start": s.isoformat(), "end": e.isoformat()} for s, e in getattr(self, "_tmd_gaps", ())]

    def _load_landslide_traces(self) -> dict[str, int] | None:
        path = self.data_dir / "landslide_districts.json"
        if not path.exists():
            log.info("no %s; landslide checks are off", path)
            return None
        return json.loads(path.read_text(encoding="utf-8"))["districts"]

    def _load_shelters(self) -> dict | None:
        path = self.data_dir / "shelters.json"
        if not path.exists():
            log.info("no %s; the shelter layer is off", path)
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def shelter_source(self) -> dict | None:
        """Who publishes the shelter list and when DDPM last updated it; None without the built file."""
        if self.shelter_data is None:
            return None
        return {k: self.shelter_data[k] for k in ("source", "source_url", "updated")}

    def get_shelters(self, area_code: str | None = None) -> list[dict]:
        """Shelters in a province or district; a sub-district gets its district's, like its risk level."""
        shelters = (self.shelter_data or {}).get("shelters", [])
        if not area_code:
            return shelters
        area = self.gazetteer.by_code.get(area_code)
        prefix = area.parent_code if area is not None and area.level == "tambon" else area_code
        return [s for s in shelters if s["amphoe_code"].startswith(prefix)]

    def nearest_shelters(self, lat: float, lon: float, limit: int = 5) -> list[dict]:
        """The closest shelters by straight-line distance (km), nearest first."""
        def km(s: dict) -> float:
            p1, p2 = math.radians(lat), math.radians(s["lat"])
            a = (math.sin((p2 - p1) / 2) ** 2
                 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(s["lon"] - lon) / 2) ** 2)
            return 6371 * 2 * math.asin(math.sqrt(a))
        ranked = sorted(((km(s), s) for s in self.get_shelters()), key=lambda pair: pair[0])[:limit]
        return [{**s, "distance_km": round(d, 1)} for d, s in ranked]

    def find_shelter(self, lat: float, lon: float) -> dict | None:
        """The listed shelter at this point (routes go only to DDPM shelters, so the API is not a free router)."""
        return next((s for s in self.get_shelters() if abs(s["lat"] - lat) < 1e-4 and abs(s["lon"] - lon) < 1e-4), None)

    async def _flood_near(self, client: httpx.AsyncClient, bbox: tuple[float, float, float, float]):
        """GISTDA's flood water (7 days) in a box, merged into shapes; cached ~30 min per box. None without a key."""
        if not os.getenv("GISTDA_API_KEY"):
            return None
        key = tuple(round(v, 2) for v in bbox)
        cached = self._flood_shapes.get(key)
        if cached and time.monotonic() - cached[0] < ROUTE_FLOOD_TTL_S:
            return cached[1]
        cells, total = await fetch_flood_cells(client, key, max_cells=ROUTE_FLOOD_MAX_CELLS)
        shape = unary_union([shapely_shape(g) for g in cells]).buffer(0) if cells else None
        result = {"shape": shape, "cells": len(cells), "partial": total > len(cells)}
        if len(self._flood_shapes) > 200:
            self._flood_shapes.clear()
        self._flood_shapes[key] = (time.monotonic(), result)
        return result

    async def route_to_shelter(self, start: tuple[float, float], shelter: dict, profile: str = "driving-car") -> dict:
        """A road route to a listed shelter that keeps out of GISTDA's flood water when it can.

        `flood.status`: "clear" (no flood water seen on the way), "avoided" (routed around it), "crosses" (the route
        still passes flood water: no way around, or openrouteservice refused the area), "unchecked" (no GISTDA data).
        Satellite water is days old and says nothing about closed roads, so no answer calls a route safe.
        """
        end = (shelter["lat"], shelter["lon"])
        pad = ROUTE_FLOOD_PAD_DEG
        bbox = (min(start[1], end[1]) - pad, min(start[0], end[0]) - pad, max(start[1], end[1]) + pad, max(start[0], end[0]) + pad)
        async with httpx.AsyncClient(headers={"User-Agent": "ThanPhai/0.1 (student project)"}) as client:
            flood = None
            try:
                flood = await self._flood_near(client, bbox)
            except (httpx.HTTPError, PermissionError, ValueError) as error:
                log.warning("flood cells for a route failed: %s", type(error).__name__)
            avoid = None
            if flood and flood["shape"] is not None:
                # Leave the two ends out of the area, or no route could start or finish there.
                ends = unary_union([Point(start[1], start[0]).buffer(ROUTE_END_CLEAR_DEG), Point(end[1], end[0]).buffer(ROUTE_END_CLEAR_DEG)])
                area = flood["shape"].difference(ends).simplify(ROUTE_SIMPLIFY_DEG)
                if not area.is_empty:
                    avoid = mapping(area if area.geom_type in ("Polygon", "MultiPolygon") else area.buffer(0))
            refused = None
            try:
                route = await fetch_route(client, start, end, profile, avoid)
            except RouteError as error:
                if avoid is None or error.status not in (400, 413, 500):
                    raise
                refused = error.code  # e.g. the area is too large or complex for ORS: route without it, then warn
                route = await fetch_route(client, start, end, profile, None)
        line = shapely_shape(route["geometry"])
        crossing_km = 0.0
        if flood and flood["shape"] is not None:
            crossing = line.intersection(flood["shape"])
            crossing_km = round(crossing.length * 111.0 * math.cos(math.radians(start[0])), 2)
        if not flood:
            status = "unchecked"
        elif crossing_km > ROUTE_CROSSING_MIN_KM:
            status = "crosses"
        elif avoid and refused is None:
            status = "avoided" if flood["cells"] else "clear"
        else:
            status = "clear"
        return {**route, "profile": profile, "shelter": shelter, "attribution": ROUTE_ATTRIBUTION,
                "flood": {"status": status, "crossing_km": crossing_km, "cells": flood["cells"] if flood else 0,
                          "partial": bool(flood and flood["partial"]), "avoid_refused": refused is not None,
                          # The water the route was checked against, to draw under it (the district overlay is coarser).
                          "area": mapping(flood["shape"].simplify(ROUTE_SIMPLIFY_DEG)) if flood and flood["shape"] is not None else None,
                          "window": "7days", "source": "GISTDA (ดาวเทียม Sentinel-1)"}}

    def _landslide_observations(self, code: str, rain_24h: float | None, forecasts: dict) -> dict:
        """Rain inputs for landslide rules, only in districts with enough past landslides (else no level)."""
        traces = (self.landslide_traces or {}).get(code, 0)
        if traces < LANDSLIDE_MIN_TRACES:
            return {"landslide_rain_24h": None, "landslide_forecast_rain_24h": None}
        # TMD forecasts are per province; the wettest of the next days counts.
        days = forecasts.get(code) or forecasts.get(code[:4]) or []
        rain_ahead = max((d["rain_mm"] for d in days if d.get("rain_mm") is not None), default=None)
        return {"landslide_rain_24h": rain_24h, "landslide_forecast_rain_24h": rain_ahead, "landslide_traces": float(traces)}

    def _cyclone_exposure(self, records: list | None) -> dict[str, dict] | None:
        """Per district: strongest GDACS wind area over its centre and distance to the nearest track point now or ahead.

        Districts with neither (no storm within CYCLONE_NOTICE_KM) are left out; None when GDACS failed.
        """
        from shapely.geometry import Point, shape
        from shapely.prepared import prep

        from risk_models.earthquake import distance_km

        if records is None:
            return None
        now = datetime.now(timezone.utc)
        areas = [(r.value, r.valid_time, r.extra["event"], prep(shape(r.extra["geometry"])))
                 for r in records if r.variable == "cyclone_wind_area"]
        # The latest analysed position (6 h old at most) and every forecast point.
        track = [r for r in records if r.variable == "cyclone_track_point" and r.valid_time and r.valid_time >= now - timedelta(hours=6)]
        out: dict[str, dict] = {}
        for code in self.amphoe_codes:
            area = self.gazetteer.by_code[code]
            centre = Point(area.lon, area.lat)
            hits = [(wind, when, event) for wind, when, event, geom in areas if geom.contains(centre)]
            near = min(((distance_km(area.lat, area.lon, r.lat, r.lon), r) for r in track), default=None, key=lambda x: x[0])
            if not hits and (near is None or near[0] > CYCLONE_NOTICE_KM):
                continue
            row: dict = {"cyclone_wind_kmh": 0.0}
            if hits:
                wind, when, event = max(hits, key=lambda h: (h[0], -(h[1].timestamp() if h[1] else 0)))
                # Earliest time a wind area of that strength reaches the district.
                first = min((h[1] for h in hits if h[0] == wind and h[1]), default=None)
                row.update({"cyclone_wind_kmh": wind, "_event": event, "_time": first})
            if near is not None:
                row.update({"cyclone_distance_km": round(near[0]), "_near_event": near[1].extra["event"],
                            "_near_time": near[1].valid_time})
            out[code] = row
        return out

    @staticmethod
    def _cyclone_notice(c: dict) -> str:
        if c["cyclone_wind_kmh"] > 0:
            when = f" ราว {c['_time'].astimezone(BANGKOK).strftime('%d/%m %H:%M')} น." if c.get("_time") else ""
            return f"พายุ {c['_event']}: อยู่ในรัศมีลมแรง {c['cyclone_wind_kmh']:.0f} กม./ชม. ขึ้นไป{when} (GDACS รวมเส้นทางคาดการณ์)"
        when = c["_near_time"].astimezone(BANGKOK).strftime("%d/%m %H:%M")
        return f"พายุ {c['_near_event']} เข้าใกล้ที่สุดราว {c['cyclone_distance_km']} กม. ช่วง {when} น. (เส้นทางคาดการณ์ของ GDACS)"

    @staticmethod
    def _quake_notice(q: dict) -> str:
        when = q["_time"].astimezone(BANGKOK).strftime("%d/%m %H:%M")
        text = (f"แผ่นดินไหวขนาด {q['quake_magnitude']:.1f} {q['_place']} ห่างราว {q['quake_distance_km']} กม. "
                f"เมื่อ {when} น. ประเมินแรงสั่นที่นี่ระดับ {q['quake_mmi_24h']:.0f} ({q['_label']})")
        from risk_models.earthquake import mmi_label_th

        epicentre = q.get("_epicentre_mmi")
        if epicentre is not None and round(epicentre) > round(q["quake_mmi_24h"]):
            # A small quake nearby: weak at the district centre, but people close to the epicentre may feel it.
            text += f" · ใกล้จุดศูนย์กลางประเมินระดับ {epicentre:.0f} ({mmi_label_th(epicentre)})"
        return text

    @staticmethod
    def _regional_rows(records) -> list[dict]:
        rows = [{"id": r.station_id, "type": r.extra["event_type"], "type_th": r.extra["type_th"], "name": r.extra["name"],
                 "alert": r.extra["alert"], "alert_th": r.extra["alert_th"], "countries": r.extra["countries"],
                 "severity_text": r.extra["severity_text"], "from": r.extra["from"], "to": r.observed_at.isoformat(),
                 "current": r.extra["current"], "lat": r.lat, "lon": r.lon, "url": r.extra["url"]} for r in records]
        order = {"Red": 0, "Orange": 1, "Green": 2}
        rows.sort(key=lambda e: e["to"], reverse=True)  # newest first within each group below
        return sorted(rows, key=lambda e: (not e["current"], order.get(e["alert"], 3)))

    def _offshore_points(self) -> list[tuple[str, float, float]]:
        from geo_integration.coast import offshore_points

        return offshore_points(gpd.read_file(self.data_dir / "admin_areas_web.gpkg", layer="amphoe"))

    def _cross_border_counts(self, records: list) -> dict[str, int]:
        """Hotspots outside Thailand within CROSS_BORDER_KM of each district centre (districts with none are left out)."""
        import numpy as np

        if not records:
            return {}
        mapped = self.mapper.assign_points([r.lat for r in records], [r.lon for r in records], nearest=False)
        outside = [r for r, match in zip(records, mapped["match"]) if match == "outside"]
        if not outside:
            return {}
        lat = np.radians([r.lat for r in outside])
        lon = np.radians([r.lon for r in outside])
        counts = {}
        for code in self.amphoe_codes:
            area = self.gazetteer.by_code[code]
            alat, alon = math.radians(area.lat), math.radians(area.lon)
            # Haversine distance, km.
            h = np.sin((lat - alat) / 2) ** 2 + math.cos(alat) * np.cos(lat) * np.sin((lon - alon) / 2) ** 2
            n = int((2 * 6371 * np.arcsin(np.sqrt(h)) <= CROSS_BORDER_KM).sum())
            if n:
                counts[code] = n
        return counts

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

    # --- Open-Meteo forecasts per district (GRID_MODELS) -------------------------------------

    def grid_stale(self, name: str) -> bool:
        updated = self.grid_updated_at[name]
        return updated is None or datetime.now(timezone.utc) - updated > GRID_MODELS[name][1]

    def refresh_grids_in_background(self) -> None:
        for name in GRID_MODELS:
            self._start_job(name, lambda name=name: self.refresh_grid(name), self.grid_stale(name))

    async def refresh_grid(self, name: str) -> None:
        adapter_class, _, where = GRID_MODELS[name]
        if where == "offshore":
            if self._offshore is None:
                self._offshore = await asyncio.to_thread(self._offshore_points)
            points = self._offshore
        else:
            points = [(a.code, a.lat, a.lon) for a in self.gazetteer.areas if a.level == "amphoe"]
        async with httpx.AsyncClient(headers=USER_AGENT) as client:
            records = await adapter_class(points).run(client)
        # A district can have several points (offshore): keep its highest value.
        best: dict[str, object] = {}
        for r in records:
            code = r.extra["area_code"]
            if code not in best or r.value > best[code].value:
                best[code] = r
        self.grid[name] = best
        self.grid_updated_at[name] = now = datetime.now(timezone.utc)
        self.source_status[name] = {"ok": True, "records": len(records), "checked_at": now.isoformat()}
        self._inputs_changed = True  # rebuild decisions with the new forecast on the next request

    # --- wave arrows (map overlay) ----------------------------------------------------------

    @property
    def waves_stale(self) -> bool:
        return self.wave_updated_at is None or datetime.now(timezone.utc) - self.wave_updated_at > WAVE_TTL

    def refresh_waves_in_background(self) -> None:
        # The arrows share Open-Meteo's paced queue with the district forecasts; let those load first after a restart.
        if not set(self.grid_updated_at) & set(self.inputs_pending):
            self._start_job("open_meteo_waves", self.refresh_waves, self.waves_stale)

    async def refresh_waves(self) -> None:
        # Land points come back empty but still count against the quota (~90 of 486), so between full checks only the
        # sea points are asked. A sea point can also come back empty for a while; the daily full check brings it back.
        started = datetime.now(timezone.utc)
        full = self._wave_grid is None or self._wave_grid_checked_at is None or started - self._wave_grid_checked_at > WAVE_FULL_GRID_EVERY
        grid = sea_grid(WAVE_BBOX, WAVE_GRID_DEGREES) if full else self._wave_grid
        async with httpx.AsyncClient(headers=USER_AGENT) as client:
            records = await OpenMeteoWaveField(grid).run(client)
        if full and records:
            sea = {r.extra["area_code"] for r in records}
            self._wave_grid = [p for p in grid if p[0] in sea]
            self._wave_grid_checked_at = started
        points = []
        for r in records:
            # The same rules as the coastal districts (rules.yaml marine), so arrows and districts agree.
            level = self.engine.decide(DecisionInput(hazard=HazardType.MARINE, observations={"wave_height_max_24h": r.value})).level
            points.append({"lat": r.lat, "lon": r.lon, "height_m": r.value, "level": level.name if level is not None else None,
                           "direction_from_deg": r.extra["direction_from_deg"],
                           "direction_to_deg": (r.extra["direction_from_deg"] + 180) % 360,
                           "period_s": r.extra["period_s"], "peak_time": r.extra["peak_time"]})
        self.wave_points = points
        self.wave_updated_at = now = datetime.now(timezone.utc)
        self.source_status["open_meteo_waves"] = {"ok": True, "records": len(points), "checked_at": now.isoformat()}

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

    # --- river-level forecast (module 06) ---------------------------------------------------

    def _load_water_model(self):
        path = self.data_dir / "models" / "water_model.joblib"
        if not path.exists():
            log.info("no water model at %s; rivers show readings only", path)
            return None
        try:
            import joblib

            model = joblib.load(path)
            # A model saved before the live forecast existed has none of these: it is not used (retrain it).
            if not getattr(model, "river_live", None) or not getattr(model, "live_stations", None):
                log.warning("water model %s has no live river forecast; retrain it", path)
                return None
            model.live_stations = {h: set(ids) for h, ids in model.live_stations.items()}
            log.info("water model %s loaded (live forecast at %d stations)", model.version,
                     len(set().union(*model.live_stations.values())))
            return model
        except Exception as exc:  # a bad model file must not stop the API
            log.warning("water model could not be loaded: %s", exc)
            return None

    async def river_forecast(self, station_id: str) -> dict | None:
        """Model forecast of a station's level at +6/+12/+24 h from its own last 3 days (the graph's cached request).

        Display only: it never sets an alert level or a river status. A horizon is given only where the model beat
        "no change" at this station in the validation year. None for a station not in the river list.
        """
        river = next((r for r in self.rivers if r["id"] == station_id), None) if station_id.isdigit() else None
        if river is None:
            return None
        base = {"station_id": station_id, "available": False}
        model = self.water_model
        if model is None:
            return {**base, "reason": "no_model"}
        if not any(station_id in ids for ids in model.live_stations.values()):
            return {**base, "reason": "not_chosen"}
        bank = river.get("bank_msl")
        if bank is None:
            return {**base, "reason": "no_bank"}
        graph = await self.river_graph(station_id, 3)
        if graph is None:
            return None
        import pandas as pd
        from risk_models.water.dataset import live_features
        from risk_models.water.model import forecast_change

        rows = [[p["time"], p["level_msl"], p["discharge_m3s"]] for p in graph["points"]]
        now = pd.Timestamp(datetime.now(BANGKOK).replace(tzinfo=None))
        row = await asyncio.to_thread(live_features, rows, bank, now)
        if row is None:
            return {**base, "reason": "no_recent_readings"}
        changes = forecast_change(model, station_id, row)
        if not changes:
            return {**base, "reason": "not_chosen"}
        start, level = row["time"], float(row["level"])
        skill = (model.metrics or {}).get("rivers_live", {})
        points = []
        for h, change in changes.items():
            test = skill.get(f"{h}h", {}).get("test_chosen", {})
            points.append({
                # The hourly mean the model forecasts, labelled by the start of its hour (the page shows a clock time).
                "hours": h, "time": (start + pd.Timedelta(hours=h)).strftime("%Y-%m-%d %H:%M"),
                "level_msl": round(level + change, 2), "change_m": round(change, 2),
                "above_bank_m": round(level + change - bank, 2),
                # How this forecast did in the test year at the stations it is shown for, so the page can say how far
                # to trust it.
                "test_mae_m": round(test["mae_m"], 3) if test.get("mae_m") is not None else None,
            })
        return {**base, "available": True, "from_time": start.strftime("%Y-%m-%d %H:%M"), "level_msl": round(level, 2),
                "bank_msl": bank, "points": points, "model_version": model.version,
                "source": "แบบจำลองทันภัย (gradient boosting) จากระดับน้ำย้อนหลังของสถานี · ThaiWater"}

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

        def rank(item: dict) -> tuple:
            # Same level in several districts: the one with the strongest measured value speaks for the province,
            # so an M2.3 next to อ.ปง is not hidden behind the first district of จ.พะเยา.
            value = item.get("observations", {}).get(PROVINCE_TIEBREAK.get(item["hazard"], ""))
            return order[item["level"]], value if isinstance(value, (int, float)) else float("-inf")

        worst: dict[str, dict] = {}
        for code in codes:
            for item in self.decisions.get(code, []):
                current = worst.get(item["hazard"])
                if current is None or rank(item) > rank(current):
                    worst[item["hazard"]] = {**item, "worst_area": code}
        return list(worst.values())

    def get_all_assessments(self) -> list[dict]:
        """Return the existing per-district decisions for national summaries."""
        return [{"area_code": code, **item} for code in self.amphoe_codes for item in self.decisions.get(code, [])]

    @staticmethod
    def _dam_rows(records) -> list[dict]:
        rows = [{"id": r.station_id, "storage_percent": r.value, "lat": r.lat, "lon": r.lon,
                 "province_code": f"TH{r.province_code}" if r.province_code else None,
                 "date": r.observed_at.date().isoformat() if r.observed_at else None, **r.extra} for r in records]
        return sorted(rows, key=lambda d: -(d["storage_percent"] or 0))

    @staticmethod
    def _water_quality_rows(records) -> list[dict]:
        rows = [{"id": r.station_id, "salinity_gl": r.value, "lat": r.lat, "lon": r.lon,
                 "province_code": f"TH{r.province_code}" if r.province_code else None,
                 "time": r.observed_at.isoformat(), **r.extra} for r in records]
        return sorted(rows, key=lambda d: -(d["salinity_gl"] or 0))

    @staticmethod
    def _storm_rows(records) -> list[dict]:
        """One row per storm: its newest position and category, and the track (oldest first)."""
        storms: dict[str, dict] = {}
        for r in records:
            storm = storms.setdefault(r.extra["name"], {"name": r.extra["name"], "track": []})
            storm["track"].append({"time": r.observed_at.isoformat(), "lat": r.lat, "lon": r.lon, "wind_kt": r.value})
            if r.extra["latest"]:
                storm.update({"time": r.observed_at.isoformat(), "lat": r.lat, "lon": r.lon, "wind_kt": r.value,
                              "wind_kmh": r.extra["wind_kmh"], "pressure_hpa": r.extra["pressure_hpa"], "category": r.extra["category"]})
        return sorted(storms.values(), key=lambda s: -(s.get("wind_kt") or 0))

    @staticmethod
    def fresh_levels(records, now: datetime | None = None, max_age: timedelta = MAX_LEVEL_AGE) -> list:
        now = now or datetime.now(timezone.utc)
        return [r for r in records if r.observed_at is not None and now - r.observed_at <= max_age]

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

    def get_dam_history(self, dam_id: str, series: str = "storage") -> dict | None:
        """Synchronous tool for HazardAgent, sharing the graph endpoint's cache."""
        if not dam_id.isdigit() or not any(d["id"] == dam_id for d in self.dams):
            return None
        if series not in DAM_SERIES:
            raise ValueError(f"unknown dam series {series}")
        year = datetime.now(BANGKOK).year
        key = ("dam", dam_id, series, year)
        now = datetime.now(timezone.utc)
        with self._agent_history_lock:
            cached = self._graph_cache.get(key)
            if cached and now - cached[0] < (FAILURE_TTL if isinstance(cached[1], Exception) else DAM_HISTORY_TTL):
                if isinstance(cached[1], Exception):
                    raise cached[1]
                return cached[1]
            try:
                with httpx.Client(headers=USER_AGENT, follow_redirects=True) as client:
                    history = fetch_dam_year_sync(client, int(dam_id), series, [year - 1, year])
            except httpx.HTTPError as exc:
                self._graph_cache[key] = (now, exc)
                raise
            self._graph_cache[key] = (now, history)
            return history

    def get_earthquakes(self, days: int | None = None) -> list[dict]:
        """Merged TMD + USGS list, newest first: all 62 kept days, or the last `days` days."""
        if days is None:
            return self.quakes
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return [q for q in self.quakes if datetime.fromisoformat(q["time"]) >= since]

    def get_storms(self) -> list[dict]:
        return self.storms

    def get_cyclones(self) -> list[dict]:
        """One row per GDACS cyclone; wind bands are thresholds over analysed and forecast areas."""
        grouped: dict[str, dict] = {}
        for feature in self.cyclone_overlay()["features"]:
            props = feature.get("properties", {})
            if props.get("kind") not in {"wind_area", "track", "forecast", "track_point"}:
                continue
            event_id = props.get("event_id")
            row = grouped.setdefault(event_id, {"event_id": event_id, "name": props.get("name"),
                                                 "event": props.get("event"), "wind_threshold_kmh": None})
            wind = props.get("wind_kmh")
            if wind is not None:
                row["wind_threshold_kmh"] = max(row["wind_threshold_kmh"] or 0, wind)
        timestamp = self.cyclone_updated_at.isoformat() if self.cyclone_updated_at else None
        return [{**row, "time": timestamp} for row in grouped.values()]

    def get_waves(self) -> list[dict]:
        return self.wave_points

    def get_water_quality(self) -> list[dict]:
        return self.water_quality

    def get_pm25_stations(self) -> list[dict]:
        return self.pm25_stations

    def get_fire_hotspots(self) -> list[dict]:
        """District FIRMS counts for 24 hours, with the latest completed decision refresh time."""
        rows = []
        for code, assessments in self.decisions.items():
            fire = next((item for item in assessments if item.get("hazard") == "fire"), None)
            if fire and fire.get("observations", {}).get("hotspots_24h") is not None:
                rows.append({"area_code": code, "hotspots_24h": fire["observations"]["hotspots_24h"],
                             "time": self.updated_at.isoformat() if self.updated_at else None})
        return rows

    def get_media(self) -> dict:
        return {**(self.media or {}), "data_updated_at": self.updated_at.isoformat() if self.updated_at else None,
                "source": "คลังข้อมูลน้ำแห่งชาติ (สสน.)"}

    def get_dams(self, area_code: str | None = None) -> list[dict]:
        """All large dams, or those in the province of `area_code` (dams sit on province land, not districts)."""
        if area_code is None:
            return self.dams
        return [d for d in self.dams if d["province_code"] == area_code[:4]]

    def _audit_and_summarize_warning(self, w_dict: dict) -> tuple[str | None, str | None]:
        """Optionally audit TMD warning and generate AI summary with Qwen if LLM_PROVIDER=qwen."""
        if os.getenv("LLM_PROVIDER", "").lower() != "qwen":
            return None, None

        cache_key = (
            w_dict.get("issue_no") or w_dict.get("title", ""),
            str(w_dict.get("announced_at", "")),
        )
        cache = getattr(self, "_warning_ai_cache", None)
        if cache is not None and cache_key in cache:
            return cache[cache_key]

        try:
            from decision_engine.warning_explainer import WarningAuditor, WarningExplainer

            title = w_dict.get("title", "")
            body = w_dict.get("body") or w_dict.get("headline") or ""
            by_region = w_dict.get("by_region", [])
            rain_days = w_dict.get("rain_days")
            provinces_by_hazard = w_dict.get("provinces_by_hazard")
            periods_by_hazard = w_dict.get("periods_by_hazard")

            auditor = WarningAuditor()
            auditor.audit(
                title=title,
                body=body,
                by_region=by_region,
                rain_days=rain_days,
                provinces_by_hazard=provinces_by_hazard,
                periods_by_hazard=periods_by_hazard,
            )

            explainer = WarningExplainer()
            summary_res = explainer.summarize(
                title=title,
                body=body,
                by_region=by_region,
                rain_days=rain_days,
                provinces_by_hazard=provinces_by_hazard,
                periods_by_hazard=periods_by_hazard,
            )
            if summary_res and summary_res.valid:
                res = (summary_res.summary_text, summary_res.attribution)
                if cache is not None:
                    cache[cache_key] = res
                return res
            elif getattr(explainer, "validation_failed", False):
                # Qwen responded, but validation failed (safe fallback) -> cache (None, None) so we don't re-query
                if cache is not None:
                    cache[cache_key] = (None, None)
                return None, None
            else:
                # Timeout, exception, or provider disabled -> do NOT cache so next refresh can retry
                return None, None
        except Exception as exc:
            log.warning("Warning audit/summary failed: %s", exc)
            return None, None

    def get_official_warnings(self) -> list[dict]:
        return self.warnings

    def get_sources(self) -> dict[str, dict]:
        return self.source_status

    def overview_layer(self) -> dict:
        """Each district's worst level over all hazards, which hazard it is, and how many hazards are WATCH or above."""
        order = {None: -1, "NORMAL": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}
        features = []
        for code, geometry in self._geometry.items():
            items = self.decisions.get(code, [])
            worst = max(items, key=lambda i: order[i["level"]], default=None)
            area = self.gazetteer.by_code[code]
            features.append({"type": "Feature", "geometry": geometry, "properties": {
                "code": code, "name_th": area.name_th, "province_th": self.gazetteer.by_code[area.parent_code].name_th,
                "level": worst["level"] if worst else None,
                "worst_hazard": worst["hazard"] if worst and worst["level"] not in (None, "NORMAL") else None,
                "hazards_at_risk": sum(1 for i in items if order[i["level"]] >= 1),
            }})
        return {"type": "FeatureCollection", "features": features}

    # --- map overlays: drawn on top of the district layer --------------------------------------

    def _overlay_status(self, updated_at: datetime | None, job: str) -> str:
        """ok once data arrived; error while nothing arrived and the last try failed (retried after
        RETRY_AFTER_FAILURE); pending while the first try is still running."""
        if updated_at is not None:
            return "ok"
        return "error" if self.source_status.get(job, {}).get("ok") is False else "pending"

    def flood_overlay(self) -> dict:
        """Districts where GISTDA's satellites saw flood water in the last 7 days, with the flooded area.

        District outlines, not the flood edge itself: GISTDA sends the edge as ~80,000 small cells per week.
        """
        features = []
        for code, sat in self.satellite_cells.items():
            if sat["cells"] <= 0 or code not in self._geometry:
                continue
            area = self.gazetteer.by_code[code]
            features.append({"type": "Feature", "geometry": self._geometry[code], "properties": {
                "code": code, "name_th": area.name_th, "province_th": self.gazetteer.by_code[area.parent_code].name_th,
                "flood_km2": sat["approx_km2"], "flood_share": round(min(sat["approx_km2"] / area.area_km2, 1.0), 3) if area.area_km2 else None,
            }})
        features.sort(key=lambda f: -f["properties"]["flood_km2"])
        status = self._overlay_status(self.satellite_updated_at, "gistda_flood") if os.getenv("GISTDA_API_KEY") or self.satellite_updated_at else "unavailable"
        return {"type": "FeatureCollection", "features": features, "overlay": {
            "status": status, "window": "7days", "updated_at": self.satellite_updated_at.isoformat() if self.satellite_updated_at else None,
            "source": "GISTDA (ดาวเทียม Sentinel-1)"}}

    def cyclone_overlay(self) -> dict:
        """Tropical cyclones near Thailand (GDACS): track lines, track points and wind areas.

        Per event: "track" (analysed positions), "forecast" (the latest position onward), one point per position,
        and one "wind_area" per wind class, merged over the whole track (forecast included), with its rules level.
        """
        records = self.cyclone_records
        if self._cyclone_features[0] is not records:  # rebuild only when GDACS sent new records
            self._cyclone_features = (records, self._cyclone_features_from(records or []))
        features = self._cyclone_features[1]
        return {"type": "FeatureCollection", "features": features, "overlay": {
            "status": self._overlay_status(self.cyclone_updated_at, "gdacs_cyclones"),
            "storms": len({f["properties"]["event_id"] for f in features}),
            "updated_at": self.cyclone_updated_at.isoformat() if self.cyclone_updated_at else None,
            "source": "GDACS (UN/EU)"}}

    def _cyclone_features_from(self, records: list) -> list[dict]:
        from shapely.geometry import LineString, mapping, shape
        from shapely.ops import unary_union

        def geojson(geom) -> dict:
            plain = json.loads(json.dumps(mapping(geom)))  # shapely gives tuples
            return {"type": plain["type"], "coordinates": _round_coords(plain["coordinates"])}

        events: dict[str, dict] = {}
        for r in records:
            e = events.setdefault(r.extra["event_id"], {"event": r.extra["event"], "name": r.extra.get("name"), "points": [], "areas": {}})
            if r.variable == "cyclone_track_point" and r.valid_time:
                e["points"].append(r)
            elif r.variable == "cyclone_wind_area":
                e["areas"].setdefault(r.value, []).append(shape(r.extra["geometry"]))
        features = []
        for event_id, e in events.items():
            common = {"event_id": event_id, "event": e["event"], "name": e["name"]}
            for wind, shapes in sorted(e["areas"].items()):
                merged = unary_union(shapes).simplify(CYCLONE_SIMPLIFY_DEGREES, preserve_topology=True)
                # The same rules as the districts inside the area (rules.yaml tropical_cyclone).
                level = self.engine.decide(DecisionInput(hazard=HazardType.TROPICAL_CYCLONE, observations={"cyclone_wind_kmh": wind})).level
                features.append({"type": "Feature", "geometry": geojson(merged), "properties": {
                    **common, "kind": "wind_area", "wind_kmh": wind, "level": level.name if level is not None else None}})
            points = sorted(e["points"], key=lambda r: r.valid_time)
            analysed = [p for p in points if not p.extra["forecast"]]
            ahead = [p for p in points if p.extra["forecast"]]
            for kind, line in (("track", analysed), ("forecast", analysed[-1:] + ahead)):
                if len(line) >= 2:
                    features.append({"type": "Feature", "geometry": geojson(LineString([(p.lon, p.lat) for p in line])),
                                     "properties": {**common, "kind": kind}})
            for p in points:
                features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]}, "properties": {
                    **common, "kind": "track_point", "time": p.valid_time.isoformat(), "forecast": p.extra["forecast"],
                    "latest": bool(analysed) and p is analysed[-1]}})
        return features

    def wave_overlay(self) -> dict:
        """Wave arrows: the highest wave in the next 24 h at each sea point, where it comes from and its rules level."""
        features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                     "properties": {k: v for k, v in p.items() if k not in ("lat", "lon")}} for p in self.wave_points]
        return {"type": "FeatureCollection", "features": features, "overlay": {
            "status": self._overlay_status(self.wave_updated_at, "open_meteo_waves"), "grid_degrees": WAVE_GRID_DEGREES,
            "updated_at": self.wave_updated_at.isoformat() if self.wave_updated_at else None,
            "source": "Open-Meteo Marine (แบบจำลองคลื่น)"}}

    @staticmethod
    def _rain_chance(record, this_hour: str) -> dict:
        """Open-Meteo chance of rain for a district (it comes with the heat forecast): now, the 24-h peak and the
        Thai hour of that peak (0-23, so the map layer and the card can say "ราว 18:00 น.")."""
        chance = record.extra.get("rain_chance") if record is not None else None
        if not chance:
            return {"rain_chance_now": None, "rain_chance_max_24h": None, "rain_chance_peak_hour": None}
        return {"rain_chance_now": chance["hourly"].get(this_hour), "rain_chance_max_24h": chance["max_pct"],
                "rain_chance_peak_hour": float(chance["peak_time"][11:13])}

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

    def _get_thai_radar_mask(self) -> np.ndarray:
        """Return 256x256 boolean mask of Thailand territory on tile 4/12/7.

        A pixel is True if it lies within 35 km of at least one Thai amphoe centroid.
        """
        if self._thai_radar_mask is not None:
            return self._thai_radar_mask

        amphoes = [a for a in self.gazetteer.areas if a.level == "amphoe"]
        if not amphoes:
            mask = np.ones((256, 256), dtype=bool)
            self._thai_radar_mask = mask
            return mask

        from scipy.spatial import cKDTree

        r_earth = 6371.0
        def to_xyz(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
            lat_r, lon_r = np.radians(lats), np.radians(lons)
            return np.column_stack([
                r_earth * np.cos(lat_r) * np.cos(lon_r),
                r_earth * np.cos(lat_r) * np.sin(lon_r),
                r_earth * np.sin(lat_r),
            ])

        n = 16.0  # 2 ** 4
        y_idx, x_idx = np.indices((256, 256))
        lons = (12.0 + x_idx / 256.0) / n * 360.0 - 180.0
        y_tot = (7.0 + y_idx / 256.0) / n
        lats = np.degrees(np.arctan(np.sinh(np.pi * (1.0 - 2.0 * y_tot))))

        grid_xyz = to_xyz(lats.ravel(), lons.ravel())
        amp_lats = np.array([a.lat for a in amphoes])
        amp_lons = np.array([a.lon for a in amphoes])
        amp_xyz = to_xyz(amp_lats, amp_lons)

        tree = cKDTree(amp_xyz)
        dists, _ = tree.query(grid_xyz, distance_upper_bound=35.0)
        mask = (dists <= 35.0).reshape((256, 256))
        self._thai_radar_mask = mask
        return mask

    async def get_radar_nowcast(self) -> dict:
        """Return 1-hour radar nowcast calculated from real radar frames with Qwen explanation."""
        from external_data.adapters.rainviewer import fetch_radar_maps, fetch_tile_bytes, recent_radar_frames
        from risk_models.nowcast import decode_png_radar, detect_rain_cells, estimate_motion, track_cells_to_districts
        from risk_models.nowcast.cells import pixel_to_latlon_mercator
        from decision_engine.nowcast_explainer import NowcastExplainer, NowcastSummary, RainCellTarget, build_template_answer

        now = time.monotonic()
        try:
            frames = await fetch_radar_maps()
        except Exception as exc:
            log.warning("Failed to fetch radar maps from RainViewer: %s", exc)
            frames = []

        past_frames = recent_radar_frames(frames, now=time.time(), limit=6)
        curr_time = past_frames[-1]["time"] if past_frames else 0

        # Fast path check outside lock
        if self._radar_nowcast_cache is not None:
            cached_time, cached_result, expire_at = self._radar_nowcast_cache
            if cached_time == curr_time and now < expire_at:
                return cached_result

        # Acquire lock to serialize calculation and prevent duplicate work when a new frame arrives
        async with self._radar_nowcast_lock:
            # Double check inside lock
            now = time.monotonic()
            if self._radar_nowcast_cache is not None:
                cached_time, cached_result, expire_at = self._radar_nowcast_cache
                if cached_time == curr_time and now < expire_at:
                    return cached_result

            if len(past_frames) < 2:
                unavailable_res = {
                    "status": "unavailable",
                    "past_frames": past_frames,
                    "forecast_frames": [],
                    "motion": None,
                    "rain_cells": [],
                    "explanation": "ไม่สามารถดึงข้อมูลเรดาร์ฝนได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง",
                    "explanation_source": "template",
                    "source": "RainViewer · ข้อมูลเรดาร์ฝนยังไม่พร้อมใช้งาน",
                }
                self._radar_nowcast_cache = (curr_time, unavailable_res, now + 30.0)
                return unavailable_res

            prev_frame_meta = past_frames[-2]
            curr_frame_meta = past_frames[-1]
            host = curr_frame_meta.get("host") or prev_frame_meta.get("host") or "https://tilecache.rainviewer.com"

            tile_url_prev = f"{host}{prev_frame_meta['path']}/256/4/12/7/2/1_1.png"
            tile_url_curr = f"{host}{curr_frame_meta['path']}/256/4/12/7/2/1_1.png"

            try:
                bytes_prev, bytes_curr = await asyncio.gather(
                    fetch_tile_bytes(tile_url_prev),
                    fetch_tile_bytes(tile_url_curr),
                )
            except Exception as exc:
                log.warning("Failed to fetch radar tile bytes: %s", exc)
                unavailable_res = {
                    "status": "unavailable",
                    "past_frames": past_frames,
                    "forecast_frames": [],
                    "motion": None,
                    "rain_cells": [],
                    "explanation": "ไม่สามารถดึงภาพเรดาร์ฝนได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง",
                    "explanation_source": "template",
                    "source": "RainViewer · เกิดข้อผิดพลาดในการโหลดภาพเรดาร์",
                }
                self._radar_nowcast_cache = (curr_time, unavailable_res, now + 30.0)
                return unavailable_res

            interval_min = max(5.0, (curr_frame_meta["time"] - prev_frame_meta["time"]) / 60.0)

            # Amphoe list for boundary filtering and tracking
            amphoe_list = [
                {"code": a.code, "name_th": a.name_th, "lat": a.lat, "lon": a.lon, "parent_code": a.parent_code}
                for a in self.gazetteer.areas
                if a.level == "amphoe"
            ]
            thai_mask = self._get_thai_radar_mask()

            def _compute_nowcast_sync() -> tuple[dict | None, list[dict], NowcastSummary, list[dict]]:
                arr_prev = decode_png_radar(bytes_prev)
                arr_curr = decode_png_radar(bytes_curr)

                # Estimate bulk motion using spatial cross-correlation with sub-pixel refinement
                motion = estimate_motion(arr_prev, arr_curr, pixel_size_km=9.5, interval_min=interval_min)

                # Detect rain cells with exact inverse Web Mercator projection and raster mask of Thailand
                cells = detect_rain_cells(
                    arr_curr,
                    threshold=15.0,
                    min_pixels=4,
                    z=4,
                    tx=12,
                    ty=7,
                    valid_mask=thai_mask,
                )

                is_undetermined = motion.direction_th == "ทิศทางไม่แน่นอน"

                if not cells:
                    summary = NowcastSummary(
                        location_desc="",
                        direction_th="ไม่มีกลุ่มฝน",
                        speed_kmh=0.0,
                        intensity_desc="ไม่มีฝน",
                        targets=[],
                    )
                    return None, [], summary, []

                lead_cell = cells[0]
                closest_amphoe = min(
                    amphoe_list,
                    key=lambda a: (a["lat"] - lead_cell.centroid_lat) ** 2 + (a["lon"] - lead_cell.centroid_lon) ** 2,
                    default=None,
                ) if amphoe_list else None
                prov_parent = self.gazetteer.by_code.get(closest_amphoe.get("parent_code")) if closest_amphoe else None
                loc_desc = (
                    f"บริเวณจังหวัด{prov_parent.name_th}"
                    if prov_parent
                    else f"บริเวณพิกัด {lead_cell.centroid_lat:.2f}, {lead_cell.centroid_lon:.2f}"
                )

                intensity_th = (
                    "หนัก"
                    if lead_cell.peak_intensity >= 45.0
                    else ("ปานกลาง" if lead_cell.peak_intensity >= 25.0 else "กำลังอ่อน")
                )

                # Check if motion cannot be determined reliably (e.g. noise spikes > 120 km/h)
                if is_undetermined:
                    rain_cells_data = [
                        {
                            "id": c.id,
                            "centroid_lat": round(c.centroid_lat, 3),
                            "centroid_lon": round(c.centroid_lon, 3),
                            "peak_intensity_dbz": round(c.peak_intensity, 1),
                            "target_districts": [],
                        }
                        for c in cells[:5]
                    ]
                    summary = NowcastSummary(
                        location_desc=loc_desc,
                        direction_th="ยังประเมินทิศทางไม่ได้",
                        speed_kmh=0.0,
                        intensity_desc=intensity_th,
                        targets=[],
                    )
                    return None, rain_cells_data, summary, []

                is_moving = motion.speed_kmh >= 2.0
                motion_data = {
                    "speed_kmh": round(motion.speed_kmh, 1),
                    "direction_th": motion.direction_th if is_moving else "เกือบอยู่กับที่",
                    "direction_deg": round(motion.direction_deg, 1),
                    "is_moving": is_moving,
                }
                # +10 to +60 minutes: each cell moved by the measured motion, projected on the same tile the cells came from,
                # so the map draws exactly these positions.
                forecast_frames = []
                for step in range(1, 7):
                    positions = []
                    for c in cells[:5]:
                        lat, lon = pixel_to_latlon_mercator(
                            c.centroid_y + motion.dy * step, c.centroid_x + motion.dx * step,
                            z=4, tx=12, ty=7, tile_size=arr_curr.shape[0],
                        )
                        positions.append({"id": c.id, "lat": round(lat, 3), "lon": round(lon, 3)})
                    forecast_frames.append({
                        "time": curr_frame_meta["time"] + step * int(interval_min * 60),
                        "horizon_min": step * int(interval_min),
                        "dx_pixels": round(motion.dx * step, 1),
                        "dy_pixels": round(motion.dy * step, 1),
                        "cells": positions,
                    })

                target_hits = track_cells_to_districts(
                    cells=cells[:5],
                    speed_kmh=motion.speed_kmh,
                    direction_deg=motion.direction_deg,
                    districts=amphoe_list,
                    max_horizon_min=60,
                )

                # Target districts for each cell
                rain_cells_data = []
                for c in cells[:5]:
                    c_targets = [h for h in target_hits if h["cell_id"] == c.id]
                    c_targets.sort(key=lambda h: h["eta_min"])
                    rain_cells_data.append({
                        "id": c.id,
                        "centroid_lat": round(c.centroid_lat, 3),
                        "centroid_lon": round(c.centroid_lon, 3),
                        "peak_intensity_dbz": round(c.peak_intensity, 1),
                        "target_districts": [
                            {"amphoe_th": h["amphoe_th"], "eta_min": h["eta_min"]}
                            for h in c_targets[:3]
                        ],
                    })

                # Summary targets must ONLY include targets from the lead cell (cell_id == lead_cell.id)
                lead_hits = [h for h in target_hits if h["cell_id"] == lead_cell.id]
                lead_hits.sort(key=lambda h: h["eta_min"])
                targets_summary = [
                    RainCellTarget(amphoe_th=h["amphoe_th"], eta_min=h["eta_min"])
                    for h in lead_hits[:3]
                ]

                summary = NowcastSummary(
                    location_desc=loc_desc,
                    direction_th=motion_data["direction_th"],
                    speed_kmh=motion.speed_kmh,
                    intensity_desc=intensity_th,
                    targets=targets_summary,
                )
                return motion_data, rain_cells_data, summary, forecast_frames

            # Execute heavy calculation off event loop
            motion_data, rain_cells_data, summary, forecast_frames = await asyncio.to_thread(_compute_nowcast_sync)

            explainer = NowcastExplainer()
            explain_task = asyncio.ensure_future(asyncio.to_thread(explainer.explain_with_source, summary, self.gazetteer))
            # True only while Qwen is still writing; the page then asks once more for its text.
            explanation_pending = False
            try:
                explanation, explanation_source = await asyncio.wait_for(asyncio.shield(explain_task), NOWCAST_EXPLAIN_WAIT_S)
            except asyncio.TimeoutError:
                # Show the radar now; a validated Qwen text replaces the template for later readers of this frame.
                explanation, explanation_source = build_template_answer(summary), "template"
                explanation_pending = True

                def _use_late_explanation(task: asyncio.Future) -> None:
                    cache = self._radar_nowcast_cache
                    if cache is None or cache[0] != curr_time:
                        return
                    update = {"explanation_pending": False}
                    if not task.cancelled() and task.exception() is None:
                        text, source = task.result()
                        update |= {"explanation": text, "explanation_source": source}
                    self._radar_nowcast_cache = (cache[0], {**cache[1], **update}, cache[2])

                explain_task.add_done_callback(_use_late_explanation)

            result = {
                "status": "ok",
                "past_frames": past_frames,
                "forecast_frames": forecast_frames,
                "motion": motion_data,
                "rain_cells": rain_cells_data,
                "explanation": explanation,
                "explanation_source": explanation_source,
                "explanation_pending": explanation_pending,
                "source": "RainViewer · คำนวณ Nowcast 1 ชม. จากภาพเรดาร์จริง",
            }
            self._radar_nowcast_cache = (curr_time, result, now + 600.0)
            return result
