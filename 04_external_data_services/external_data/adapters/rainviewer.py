from typing import Any

import httpx

RAINVIEWER_MAPS_URL = "https://api.rainviewer.com/public/weather-maps.json"


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
                    "tile_url": build_tile_url(host, p),
                }
            )
    return frames


async def fetch_radar_maps(client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """Fetch latest weather maps from RainViewer."""
    async with (client or httpx.AsyncClient(timeout=10.0)) as c:
        resp = await c.get(RAINVIEWER_MAPS_URL)
        resp.raise_for_status()
        data = resp.json()
        return parse_radar_frames(data)
