import asyncio
import gzip
import json
import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

from api_backend.hazard_service import ROUTE_MAX_KM, HazardService
from external_data.adapters.openrouteservice import RouteError
from external_data.adapters.rainviewer import RADAR_FRAME_LIMIT, fetch_radar_maps, recent_radar_frames
from geo_integration.gazetteer import LEVEL_RANK, normalize, split_prefix
from decision_engine.explainer import create_explainer_from_env
from hazard_agent import HazardAgent
from thanphai_shared import HazardType, redact

logging.basicConfig(level=logging.INFO)
redact.install()  # FIRMS keys live in URL paths; never let them reach the logs
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data" / "processed"))
service: HazardService | None = None
agent: HazardAgent | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global service, agent
    service = HazardService(DATA_DIR)
    # Explainer rewrites answers when GEMINI_API_KEY is present or LLM_PROVIDER=qwen is configured; otherwise answers stay rule-based.
    explainer = create_explainer_from_env()
    logging.getLogger("api").info("LLM explainer: %s", explainer.config.model if explainer else "off (no LLM provider configured)")
    agent = HazardAgent(service.gazetteer, service, explainer=explainer)
    # Warm the cache in the background so startup does not wait for the providers.
    service.refresh_in_background()
    service.refresh_forecast_in_background()
    service.refresh_satellite_in_background()
    service.refresh_fire_in_background()
    # Open-Meteo district forecasts are paced (~2-3 min for all three); start now, not on the first page view.
    service.refresh_grids_in_background()
    yield


app = FastAPI(title="ThanPhai API", version="0.1.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2048)  # map layers are ~1 MB of JSON
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    return response


async def fresh_service() -> HazardService:
    if service is None:
        raise HTTPException(503, "starting")
    if not service.decisions:
        # Nothing to show yet (first start): this request has to wait for the first refresh.
        try:
            await service.refresh()
        except Exception:
            logging.getLogger("api").exception("first refresh failed")
        if not service.decisions:
            raise HTTPException(503, "no data yet; all sources failed")
    elif service.stale:
        # Answer with the current data now and refresh behind it; data_updated_at shows its age.
        service.refresh_in_background()
    if service.forecast_stale:
        service.refresh_forecast_in_background()
    if service.satellite_stale:
        service.refresh_satellite_in_background()
    if service.fire_stale:
        service.refresh_fire_in_background()
    service.refresh_grids_in_background()
    service.refresh_waves_in_background()
    return service


def meta(svc: HazardService) -> dict:
    degraded = [name for name, status in svc.source_status.items() if not status.get("ok")]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
        "degraded_services": degraded,
        # First load after a restart: satellite/forecast/fire still arriving; the web app polls until empty.
        "inputs_pending": svc.inputs_pending,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    if service is None or not service.decisions:
        raise HTTPException(503, "warming up")
    return {"status": "ready", "updated_at": service.updated_at.isoformat()}


@app.get("/v1/areas/search")
def search_areas(q: str = Query(..., min_length=1, max_length=100)) -> list[dict]:
    if service is None:
        raise HTTPException(503, "starting")
    gaz = service.gazetteer
    found = gaz.resolve_phrase(q) or gaz.find_in_text(q)
    level, term = split_prefix(q)
    needle = normalize(term)
    if len(needle) >= 2:
        candidates = [a for a in gaz.areas if (level is None or a.level == level)
                      and any(needle in normalize(name) for name in (a.name_th, a.name_en))]
        candidates.sort(key=lambda a: (not any(normalize(name).startswith(needle) for name in (a.name_th, a.name_en)),
                                       LEVEL_RANK[a.level], a.name_th, a.code))
        seen = {a.code for a in found}
        found = [*found, *(a for a in candidates if a.code not in seen)]
    return [{"code": a.code, "level": a.level, "name_th": a.name_th, "name_en": a.name_en,
             "parents": [p.name_th for p in gaz.parents(a)], "lat": a.lat, "lon": a.lon} for a in found[:20]]


@app.get("/v1/areas/locate")
async def locate_area(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)) -> dict:
    if service is None:
        raise HTTPException(503, "starting")
    svc = service
    matched = await asyncio.to_thread(svc.mapper.assign_points, [lat], [lon], nearest=True)
    area = svc.gazetteer.by_code.get(matched.iloc[0]["tambon_code"])
    if area is None:
        raise HTTPException(404, "location outside covered administrative boundaries")
    return {"code": area.code, "level": area.level, "name_th": area.name_th, "name_en": area.name_en,
            "parents": [p.name_th for p in svc.gazetteer.parents(area)], "lat": lat, "lon": lon}


@app.get("/v1/areas/{code}/hazards")
async def area_hazards(code: str) -> dict:
    svc = await fresh_service()
    area = svc.gazetteer.by_code.get(code)
    if area is None:
        raise HTTPException(404, f"unknown area {code}")
    return {
        "area": {"code": area.code, "level": area.level, "name_th": area.name_th, "name_en": area.name_en,
                 "parents": [p.name_th for p in svc.gazetteer.parents(area)]},
        "assessments": svc.get_assessments(code),
        "forecast": svc.get_forecast(code),
        "dams": svc.get_dams(code),
        "rivers": svc.get_rivers(code),
        "forecast_updated_at": svc.forecast_updated_at.isoformat() if svc.forecast_updated_at else None,
        "official_warnings": svc.get_official_warnings(),
        **meta(svc),
    }


@app.get("/v1/areas/{code}/boundary")
async def area_boundary(code: str) -> dict:
    """Outline of a province, district or sub-district, for highlighting the searched area on the map."""
    if service is None:
        raise HTTPException(503, "starting")
    if code not in service.gazetteer.by_code:
        raise HTTPException(404, f"unknown area {code}")
    return await asyncio.to_thread(service.boundary, code)


_layer_cache: dict[tuple, tuple[bytes, bytes]] = {}


@app.get("/v1/map/layers/{hazard}")
async def map_layer(hazard: str, request: Request) -> Response:
    """One hazard's level per district, or "overview": each district's worst level over all hazards."""
    if hazard != "overview" and hazard not in {h.value for h in HazardType}:
        raise HTTPException(404, "unknown hazard")
    svc = await fresh_service()
    m = meta(svc)
    key = (hazard, m["data_updated_at"], tuple(m["degraded_services"]), tuple(m["inputs_pending"]))
    cached = _layer_cache.get(key)
    if cached is None:
        # ~1.2 MB of GeoJSON. FastAPI's encoder took ~285 ms and GZipMiddleware another ~140 ms on the
        # event loop for every request. Encode and compress once per data version, in a thread.
        layer = {**(svc.overview_layer() if hazard == "overview" else svc.map_layer(hazard)), "meta": m}

        def encode() -> tuple[bytes, bytes]:
            raw = json.dumps(layer, ensure_ascii=False).encode()
            return raw, gzip.compress(raw, compresslevel=6)

        cached = await asyncio.to_thread(encode)
        if len(_layer_cache) >= 24:  # 11 layers per data version; old versions are never asked for again
            _layer_cache.clear()
        _layer_cache[key] = cached
    raw, compressed = cached
    if "gzip" in request.headers.get("accept-encoding", ""):
        # Content-Encoding set here makes GZipMiddleware leave the body alone.
        return Response(content=compressed, media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(content=raw, media_type="application/json")


@app.get("/v1/map/overlays/flood")
async def flood_overlay() -> dict:
    """Districts with satellite-detected flood water in the last 7 days (GISTDA), largest flooded area first."""
    svc = await fresh_service()
    return {**svc.flood_overlay(), **meta(svc)}


@app.get("/v1/map/overlays/cyclones")
async def cyclone_overlay() -> dict:
    """Tropical cyclones near Thailand (GDACS): wind areas, analysed track, forecast track and positions."""
    svc = await fresh_service()
    return {**svc.cyclone_overlay(), **meta(svc)}


@app.get("/v1/map/overlays/waves")
async def wave_overlay() -> dict:
    """Wave arrows over the Andaman Sea and the Gulf: highest wave in the next 24 h, direction and rules level."""
    svc = await fresh_service()
    return {**svc.wave_overlay(), **meta(svc)}


@app.get("/v1/dams")
async def dams() -> dict:
    """Thailand's 35 large dams, fullest first (ThaiWater; storage % of the normal storage level)."""
    svc = await fresh_service()
    return {"dams": svc.get_dams(), **meta(svc)}


@app.get("/v1/water-quality")
async def water_quality() -> dict:
    """Automatic water-quality stations (PCD, MWA via ThaiWater) with a reading in the last 6 h, saltiest first.

    `exceeds` holds the parameters past ThaiWater's own limits (salinity > 0.25 / 0.5 / 2 g/L, dissolved oxygen < 3 mg/L).
    """
    svc = await fresh_service()
    return {"stations": svc.water_quality, "source": "คลังข้อมูลน้ำแห่งชาติ (สสน.): กรมควบคุมมลพิษ, การประปานครหลวง", **meta(svc)}


@app.get("/v1/storms")
async def storms() -> dict:
    """Active tropical storms tracked by HII (ThaiWater), strongest first; empty when there is none. Times are UTC."""
    svc = await fresh_service()
    return {"storms": svc.storms, "source": "คลังข้อมูลน้ำแห่งชาติ (สสน.)", **meta(svc)}


@app.get("/v1/dams/{dam_id}/history")
async def dam_history(dam_id: str, series: Literal["storage", "released", "inflow"] = "storage") -> dict:
    """This year and last year day by day (million m³), with the dam's upper/lower rule curves."""
    svc = await fresh_service()
    try:
        history = await svc.dam_history(dam_id, series)
    except httpx.HTTPError:
        raise HTTPException(502, "ThaiWater did not answer; try again later")
    if history is None:
        raise HTTPException(404, f"unknown dam {dam_id}")
    return {"dam_id": dam_id, "series_type": series, **history}


@app.get("/v1/rivers")
async def rivers() -> dict:
    """River stations with a flow gauge or water over the bank (ThaiWater, hourly). Over-bank first."""
    svc = await fresh_service()
    return {"rivers": svc.get_rivers(), **meta(svc)}


@app.get("/v1/rivers/{station_id}/graph")
async def river_graph(station_id: str, days: int = Query(3, ge=1, le=7)) -> dict:
    """Hourly water level (m MSL) and discharge (m³/s) of one station, with its bank and channel capacity."""
    svc = await fresh_service()
    try:
        graph = await svc.river_graph(station_id, days)
    except httpx.HTTPError:
        raise HTTPException(502, "ThaiWater did not answer; try again later")
    if graph is None:
        raise HTTPException(404, f"unknown station {station_id}")
    return {"station_id": station_id, **graph}


@app.get("/v1/rivers/{station_id}/forecast")
async def river_forecast(station_id: str) -> dict:
    """Model forecast of the water level at +6/+12/+24 h (display only, never an alert level or river status).

    `available` is false with a `reason` when there is no model, the station was not chosen (the model did not beat
    "no change" there in the validation year), it has no bank level, or its latest reading is over 3 hours old.
    """
    svc = await fresh_service()
    try:
        forecast = await svc.river_forecast(station_id)
    except httpx.HTTPError:
        raise HTTPException(502, "ThaiWater did not answer; try again later")
    if forecast is None:
        raise HTTPException(404, f"unknown station {station_id}")
    return forecast


@app.get("/v1/air/stations")
async def air_stations() -> dict:
    """Air4Thai stations with a current PM2.5 reading (24-hour average), worst first."""
    svc = await fresh_service()
    return {"stations": svc.pm25_stations, **meta(svc)}


@app.get("/v1/earthquakes")
async def earthquakes() -> dict:
    """Earthquakes of the last 7 days around Thailand (TMD, plus USGS for events TMD does not list), newest first."""
    svc = await fresh_service()
    return {"earthquakes": svc.get_earthquakes(days=7), **meta(svc)}


@app.get("/v1/regional-events")
async def regional_events() -> dict:
    """GDACS earthquakes, tropical cyclones and floods around South-East Asia (last 7 days), current and red first."""
    svc = await fresh_service()
    return {"events": svc.regional_events, **meta(svc)}


@app.get("/v1/media")
async def media() -> dict:
    """Latest rain radar, HII rain forecast, storm track and SWAN wave images (URLs on ThaiWater)."""
    svc = await fresh_service()
    empty = {"radar": [], "rain_forecast": {}, "storm": [], "wave": []}
    return {**(svc.media or empty), **meta(svc)}


RADAR_TTL_S = 300
RADAR_RETRY_S = 30
_radar_lock = asyncio.Lock()
_radar_retry_at = 0.0
_radar_cache: tuple[float, list[dict] | None] | None = None  # (monotonic time fetched, recent frames)


@app.get("/v1/radar")
async def radar() -> dict:
    """Recent RainViewer radar frames as tile URL templates (`frames` is empty when none are available).

    RainViewer's free tier is for personal/educational use and allows 100 requests/min per IP, so the layer is
    for the team only: it answers 404 unless RADAR_LAYER=internal, and the frame list is fetched once per 5 minutes
    for everyone through the module 04 adapter, which the nowcast shares. Failed refreshes wait 30 seconds before
    retrying; frames older than 1 hour are omitted.
    Concurrent readers can use the cached images while one request refreshes them.
    Login (admin/user) replaces the env flag later.
    """
    global _radar_cache, _radar_retry_at
    if os.getenv("RADAR_LAYER", "off") != "internal":
        raise HTTPException(404, "radar layer is off")
    # Readers with a cached image need not wait for the request refreshing it.
    if _radar_cache is None or _radar_cache[1] is None or not _radar_lock.locked():
        async with _radar_lock:
            # Recheck after waiting: another request may already have refreshed or failed.
            now = time.monotonic()
            if (_radar_cache is None or now - _radar_cache[0] >= RADAR_TTL_S) and now >= _radar_retry_at:
                try:
                    frames = await fetch_radar_maps()
                    recent = [{"tiles": frame["tile_url"], "time": frame["time"]}
                              for frame in recent_radar_frames(frames, now=time.time(), limit=RADAR_FRAME_LIMIT)]
                    _radar_cache = (time.monotonic(), recent)
                    _radar_retry_at = 0.0
                except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                    logging.getLogger("api").warning("RainViewer frame list failed", exc_info=True)
                    _radar_retry_at = time.monotonic() + RADAR_RETRY_S
    if _radar_cache is None:
        raise HTTPException(502, "RainViewer did not answer; try again later")
    return {"frames": recent_radar_frames(_radar_cache[1] or [], now=time.time())}


@app.get("/v1/radar/nowcast")
async def radar_nowcast() -> dict:
    """1-hour radar nowcast with extrapolated frame timestamps and Qwen explanation."""
    if os.getenv("RADAR_LAYER", "off") != "internal":
        raise HTTPException(404, "radar layer is off")
    # Radar needs only the gazetteer, so it does not wait for the first hazard refresh after a restart.
    svc = service
    if svc is None:
        raise HTTPException(503, "starting")
    data = await svc.get_radar_nowcast()
    return {**data, **meta(svc)}


@app.get("/v1/warnings")
async def warnings() -> dict:
    svc = await fresh_service()
    return {"warnings": svc.get_official_warnings(), **meta(svc)}


class RouteRequest(BaseModel):
    """The visitor's position comes in the body, never the URL, and is not logged or kept."""
    lat: float = Field(..., ge=5.5, le=20.5)
    lon: float = Field(..., ge=97.3, le=105.7)
    to_lat: float = Field(..., ge=5.5, le=20.5)
    to_lon: float = Field(..., ge=97.3, le=105.7)
    profile: Literal["driving-car", "foot-walking"] = "driving-car"


ROUTE_LIMIT_PER_MINUTE = int(os.getenv("ROUTE_LIMIT_PER_MINUTE", "6"))
_route_times: dict[str, list[float]] = {}


@app.post("/v1/shelters/route")
async def shelter_route(body: RouteRequest, request: Request) -> dict:
    """A road route to a DDPM shelter, around GISTDA's satellite flood water when possible (see `flood.status`)."""
    shelter_source()
    check_rate(_route_times, ROUTE_LIMIT_PER_MINUTE, client_ip(request), "ขอเส้นทางถี่เกินไป กรุณารอสักครู่")
    shelter = service.find_shelter(body.to_lat, body.to_lon)
    if shelter is None:
        raise HTTPException(404, "routes go only to shelters in DDPM's list")
    km = 6371 * 2 * math.asin(math.sqrt(math.sin(math.radians(body.to_lat - body.lat) / 2) ** 2 + math.cos(math.radians(body.lat))
                                         * math.cos(math.radians(body.to_lat)) * math.sin(math.radians(body.to_lon - body.lon) / 2) ** 2))
    if km > ROUTE_MAX_KM:
        raise HTTPException(422, f"the shelter is {km:.0f} km away; routes are given within {ROUTE_MAX_KM} km")
    if not os.getenv("ORS_API_KEY"):
        raise HTTPException(503, "routing is not configured")
    try:
        return await service.route_to_shelter((body.lat, body.lon), shelter, body.profile)
    except RouteError as error:
        logging.getLogger("api").warning("shelter route failed: %s (code %s)", error, error.code)
        raise HTTPException(502 if error.status != 404 else 404, "no route from openrouteservice") from None


def shelter_source() -> dict:
    """The shelter list's source; the list is fixed, so it needs no refresh (no fresh_service)."""
    if service is None:
        raise HTTPException(503, "starting")
    source = service.shelter_source()
    if source is None:
        raise HTTPException(503, "shelter list not built (run scripts/build_shelters.py)")
    return source


@app.get("/v1/shelters")
async def shelters(response: Response, area: str | None = Query(None, pattern=r"^TH\d{2,6}$")) -> dict:
    """Temporary disaster shelters (DDPM list; no Bangkok). Official places only: the app never suggests one itself.

    `area`: a province or district code; a sub-district code gives its district's shelters.
    `approximate` marks a point shared by several shelters (often a sub-district's centre, not the building).
    """
    source = shelter_source()
    response.headers["Cache-Control"] = "public, max-age=86400"
    return {"shelters": service.get_shelters(area), **source}


@app.get("/v1/shelters/nearest")
async def nearest_shelters(lat: float = Query(..., ge=5.5, le=20.5), lon: float = Query(..., ge=97.3, le=105.7),
                           limit: int = Query(5, ge=1, le=20)) -> dict:
    """The closest shelters to a point in Thailand, by straight-line distance (`distance_km`), nearest first."""
    source = shelter_source()
    return {"shelters": service.nearest_shelters(lat, lon, limit), **source}


@app.get("/v1/sources")
async def sources() -> dict:
    svc = await fresh_service()
    return {"sources": svc.get_sources(), **meta(svc)}


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    # The area open on the page (map selection or the last answer's area); used when the question names none.
    area_code: str | None = Field(None, pattern=r"^TH\d{2,8}$")


# Each question may call the LLM; on a public site, cap how many one visitor can ask.
ASK_LIMIT_PER_MINUTE = int(os.getenv("ASK_LIMIT_PER_MINUTE", "10"))
_ask_times: dict[str, list[float]] = {}


def client_ip(request: Request) -> str:
    """The visitor's address. Behind Cloudflare Tunnel and the Next proxy, request.client is the web container;
    Cloudflare sets CF-Connecting-IP and Next passes it on. The web port is reachable only through the tunnel."""
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
    return forwarded.strip() or (request.client.host if request.client else "unknown")


def check_rate(times: dict[str, list[float]], limit: int, ip: str, message: str, now: float | None = None) -> None:
    """At most `limit` requests a minute per visitor (0: no limit)."""
    if limit <= 0:
        return
    now = time.monotonic() if now is None else now
    recent = [t for t in times.get(ip, []) if now - t < 60]
    if len(recent) >= limit:
        times[ip] = recent
        raise HTTPException(429, message, headers={"Retry-After": "60"})
    recent.append(now)
    times[ip] = recent
    if len(times) > 10_000:  # drop visitors with nothing in the last minute
        for key in [k for k, v in times.items() if not v or now - v[-1] >= 60]:
            del times[key]


def check_ask_limit(ip: str, now: float | None = None) -> None:
    check_rate(_ask_times, ASK_LIMIT_PER_MINUTE, ip, "ถามถี่เกินไป กรุณารอสักครู่แล้วถามใหม่ครับ", now)


@app.post("/v1/ask")
async def ask(body: AskRequest, request: Request) -> dict:
    check_ask_limit(client_ip(request))
    svc = await fresh_service()
    # ask() may call Gemini (seconds, blocking SDK call); run it in a thread so other requests keep flowing.
    answer = await asyncio.to_thread(agent.ask, body.question, datetime.now(timezone.utc), body.area_code)
    return {**answer.__dict__, **meta(svc)}
