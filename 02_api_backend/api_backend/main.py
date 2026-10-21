import asyncio
import gzip
import json
import logging
import os
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

from api_backend.hazard_service import HazardService
from decision_engine.explainer import GeminiExplainer
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
    # Gemini rewrites answers only when GEMINI_API_KEY is set; otherwise answers stay rule-based.
    explainer = GeminiExplainer.from_env()
    logging.getLogger("api").info("LLM explainer: %s", explainer.config.model if explainer else "off (no GEMINI_API_KEY)")
    agent = HazardAgent(service.gazetteer, service, explainer=explainer)
    # Warm the cache in the background so startup does not wait for the providers.
    service.refresh_in_background()
    service.refresh_forecast_in_background()
    service.refresh_satellite_in_background()
    service.refresh_fire_in_background()
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
    return [{"code": a.code, "level": a.level, "name_th": a.name_th, "name_en": a.name_en,
             "parents": [p.name_th for p in gaz.parents(a)], "lat": a.lat, "lon": a.lon} for a in found[:20]]


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
async def map_layer(hazard: HazardType, request: Request) -> Response:
    svc = await fresh_service()
    m = meta(svc)
    key = (hazard.value, m["data_updated_at"], tuple(m["degraded_services"]), tuple(m["inputs_pending"]))
    cached = _layer_cache.get(key)
    if cached is None:
        # ~1.2 MB of GeoJSON. FastAPI's encoder took ~285 ms and GZipMiddleware another ~140 ms on the
        # event loop for every request. Encode and compress once per data version, in a thread.
        layer = {**svc.map_layer(hazard.value), "meta": m}

        def encode() -> tuple[bytes, bytes]:
            raw = json.dumps(layer, ensure_ascii=False).encode()
            return raw, gzip.compress(raw, compresslevel=6)

        cached = await asyncio.to_thread(encode)
        if len(_layer_cache) >= 8:  # old data versions are never asked for again
            _layer_cache.clear()
        _layer_cache[key] = cached
    raw, compressed = cached
    if "gzip" in request.headers.get("accept-encoding", ""):
        # Content-Encoding set here makes GZipMiddleware leave the body alone.
        return Response(content=compressed, media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(content=raw, media_type="application/json")


@app.get("/v1/dams")
async def dams() -> dict:
    """Thailand's 35 large dams, fullest first (ThaiWater; storage % of the normal storage level)."""
    svc = await fresh_service()
    return {"dams": svc.get_dams(), **meta(svc)}


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


@app.get("/v1/media")
async def media() -> dict:
    """Latest rain radar, HII rain forecast, storm track and SWAN wave images (URLs on ThaiWater)."""
    svc = await fresh_service()
    empty = {"radar": [], "rain_forecast": {}, "storm": [], "wave": []}
    return {**(svc.media or empty), **meta(svc)}


@app.get("/v1/warnings")
async def warnings() -> dict:
    svc = await fresh_service()
    return {"warnings": svc.get_official_warnings(), **meta(svc)}


@app.get("/v1/sources")
async def sources() -> dict:
    svc = await fresh_service()
    return {"sources": svc.source_status, **meta(svc)}


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


@app.post("/v1/ask")
async def ask(body: AskRequest) -> dict:
    svc = await fresh_service()
    # ask() may call Gemini (seconds, blocking SDK call); run it in a thread so other requests keep flowing.
    answer = await asyncio.to_thread(agent.ask, body.question, datetime.now(timezone.utc))
    return {**answer.__dict__, **meta(svc)}
