"""Points at sea next to each district, for marine forecasts (district centres are on land)."""

import geopandas as gpd

METRIC_CRS = "EPSG:32647"
# Sample the sea 5-20 km out: far enough that a ~9 km wave-model cell is water, close enough to be this district's sea.
NEAR_SHORE_M = 5000
OFFSHORE_M = 20000
SIMPLIFY_M = 200  # outlines only need to be rough here; full detail made the union take ~35 s
MAX_POINTS_PER_AREA = 3


def offshore_points(areas: gpd.GeoDataFrame, code_column: str = "code") -> list[tuple[str, float, float]]:
    """(code, lat, lon) points just outside the country next to each area, up to 3 per area.

    Districts on a land border also get points (in the neighbouring country); a marine model returns no data
    there, which is how callers tell the coast from a land border.
    """
    metric = areas.to_crs(METRIC_CRS)
    shapes = metric.geometry.simplify(SIMPLIFY_M)
    land = shapes.union_all().buffer(NEAR_SHORE_M)
    out: list[tuple[str, object]] = []
    for code, shape in zip(metric[code_column], shapes):
        band = shape.buffer(OFFSHORE_M).difference(land)
        if band.is_empty:
            continue
        parts = sorted(getattr(band, "geoms", [band]), key=lambda g: -g.area)[:MAX_POINTS_PER_AREA]
        out += [(code, part.representative_point()) for part in parts]
    if not out:
        return []
    points = gpd.GeoSeries([p for _, p in out], crs=METRIC_CRS).to_crs("EPSG:4326")
    return [(code, round(p.y, 4), round(p.x, 4)) for (code, _), p in zip(out, points)]
