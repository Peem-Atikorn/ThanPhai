import asyncio
import time
from typing import Any

import httpx

RAINVIEWER_MAPS_URL = "https://api.rainviewer.com/public/weather-maps.json"
RADAR_HISTORY_SECONDS = 3600
RADAR_MAX_FRAME_AGE_SECONDS = 7200
RADAR_FRAME_LIMIT = 7

_CACHE_FRAMES: list[dict[str, Any]] | None = None
_CACHE_TIME: float = 0.0
CACHE_TTL_SECONDS: float = 60.0
RETRY_SECONDS: float = 30.0
_FAIL_TIME: float = 0.0
_FAIL_ERROR: Exception | None = None
_LOCK: asyncio.Lock | None = None
_LOCK_LOOP: asyncio.AbstractEventLoop | None = None


class RetryWait(httpx.HTTPError):
    """RainViewer failed recently; no new request until RETRY_SECONDS have passed.

    An httpx.HTTPError, so callers handle it like the failure it repeats.
    """


def build_tile_url(
    host: str,
    path: str,
    size: int = 256,
    color: int = 2,
    smooth: int = 1,
    snow: int = 1,
) -> str:
    """Generate MapLibre/Leaflet tile URL template for RainViewer radar frame."""
    return f"{host.rstrip('/')}{path}/{size}/{{z}}/{{x}}/{{y}}/{color}/{smooth}_{snow}.png"


def parse_radar_frames(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse weather-maps.json response and attach standard tile URLs."""
    host = data.get("host", "https://tilecache.rainviewer.com")
    past = data.get("radar", {}).get("past", [])
    frames: list[dict[str, Any]] = []
    for item in past:
        t = item.get("time")
        p = item.get("path")
        if t and p:
            frames.append(
                {
                    "time": t,
                    "path": p,
                    "host": host,
                    "tile_url": build_tile_url(host, p),
                }
            )
    return frames


def recent_radar_frames(frames: list[dict[str, Any]], *, now: float | None = None, limit: int = RADAR_FRAME_LIMIT) -> list[dict[str, Any]]:
    """Keep the latest frame fresh and at most an hour of history behind it."""
    current_time = time.time() if now is None else now
    fresh = [frame for frame in frames
             if isinstance(frame.get("time"), (int, float)) and not isinstance(frame["time"], bool)
             and 0 <= current_time - frame["time"] <= RADAR_MAX_FRAME_AGE_SECONDS]
    if not fresh:
        return []
    latest = max(frame["time"] for frame in fresh)
    history = [frame for frame in fresh if 0 <= latest - frame["time"] <= RADAR_HISTORY_SECONDS]
    return history[-limit:]


def _cached() -> list[dict[str, Any]] | None:
    """The cached frames while fresh; raises the last error while waiting to retry."""
    now = time.time()
    if _CACHE_FRAMES is not None and (now - _CACHE_TIME) < CACHE_TTL_SECONDS:
        return _CACHE_FRAMES
    if _FAIL_ERROR is not None and (now - _FAIL_TIME) < RETRY_SECONDS:
        raise RetryWait(f"RainViewer unavailable, retrying after {RETRY_SECONDS:.0f} s") from _FAIL_ERROR
    return None


def _lock() -> asyncio.Lock:
    """One lock per event loop (tests run several loops)."""
    global _LOCK, _LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _LOCK is None or _LOCK_LOOP is not loop:
        _LOCK, _LOCK_LOOP = asyncio.Lock(), loop
    return _LOCK


async def fetch_radar_maps(client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """Fetch latest weather maps from RainViewer with in-memory TTL cache.

    Concurrent callers share one upstream request: the first fetches under a lock, the rest wait
    and take its result. After a failure, callers get an error without a new request for
    RETRY_SECONDS.
    """
    global _CACHE_FRAMES, _CACHE_TIME, _FAIL_TIME, _FAIL_ERROR
    frames = _cached()
    if frames is not None:
        return frames

    async with _lock():
        frames = _cached()
        if frames is not None:
            return frames
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=10.0) as own_client:
                    resp = await own_client.get(RAINVIEWER_MAPS_URL)
                    resp.raise_for_status()
                    data = resp.json()
            else:
                # Do not use 'async with' on client passed by caller to avoid closing it
                resp = await client.get(RAINVIEWER_MAPS_URL)
                resp.raise_for_status()
                data = resp.json()
            frames = parse_radar_frames(data)
        except Exception as exc:
            _FAIL_TIME, _FAIL_ERROR = time.time(), exc
            raise
        _CACHE_FRAMES, _CACHE_TIME = frames, time.time()
        _FAIL_ERROR = None
        return frames


async def fetch_tile_bytes(url: str, client: httpx.AsyncClient | None = None) -> bytes:
    """Fetch raw radar tile bytes from RainViewer tilecache."""
    if client is None:
        async with httpx.AsyncClient(timeout=8.0) as own_client:
            resp = await own_client.get(url)
            resp.raise_for_status()
            return resp.content
    else:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content
