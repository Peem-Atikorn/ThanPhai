import math
from typing import Any

from . import RainCell


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def track_cells_to_districts(
    cells: list[RainCell],
    speed_kmh: float,
    direction_deg: float,
    districts: list[dict[str, Any]],
    max_horizon_min: int = 60,
    impact_radius_km: float = 20.0,
) -> list[dict[str, Any]]:
    """Project rain cells along velocity vector and find intersecting districts."""
    if not cells:
        return []

    is_moving = speed_kmh >= 2.0
    effective_horizon = max_horizon_min if is_moving else 0

    rad = math.radians(direction_deg)
    # Velocity components in km/min
    v_north = (speed_kmh / 60.0) * math.cos(rad) if is_moving else 0.0
    v_east = (speed_kmh / 60.0) * math.sin(rad) if is_moving else 0.0

    hits: list[dict[str, Any]] = []
    seen_cell_districts: set[tuple[int, str]] = set()

    for cell in cells:
        lat0 = cell.centroid_lat
        lon0 = cell.centroid_lon
        cos_lat = max(0.1, math.cos(math.radians(lat0)))

        # Step through 5-minute increments starting at t=0 (current impact) up to horizon
        for t in range(0, effective_horizon + 1, 5):
            dist_north_km = v_north * t
            dist_east_km = v_east * t

            # Convert km offset to degrees
            d_lat = dist_north_km / 111.0
            d_lon = dist_east_km / (111.0 * cos_lat)

            proj_lat = lat0 + d_lat
            proj_lon = lon0 + d_lon

            for d in districts:
                name = d.get("name_th") or d.get("name") or ""
                code = d.get("code") or name
                key = (cell.id, code)
                if key in seen_cell_districts:
                    continue

                d_lat_dist = d["lat"]
                d_lon_dist = d["lon"]

                dist_km = haversine_km(proj_lat, proj_lon, d_lat_dist, d_lon_dist)
                if dist_km <= impact_radius_km:
                    seen_cell_districts.add(key)
                    hits.append(
                        {
                            "cell_id": cell.id,
                            "code": d.get("code", ""),
                            "amphoe_th": name,
                            "eta_min": t,
                            "distance_km": round(dist_km, 1),
                            "peak_intensity": cell.peak_intensity,
                        }
                    )

    # Sort by arrival time ascending
    hits.sort(key=lambda h: h["eta_min"])
    return hits
