"""Build ThanPhai's admin-area layers and gazetteer from the OCHA HDX `cod-ab-tha` geodatabase.

Source: https://data.humdata.org/dataset/cod-ab-tha (CC BY-IGO). P-codes are "TH" + the DOPA code,
for example TH72 = Suphan Buri, TH7207 = Song Phi Nong, TH720703 = Bang Ta Then.
"""

import json
from pathlib import Path

import geopandas as gpd
import pyogrio

LEVELS = {
    "province": ("tha_admin1", "adm1"),
    "amphoe": ("tha_admin2", "adm2"),
    "tambon": ("tha_admin3", "adm3"),
}
PARENT = {"province": None, "amphoe": "adm1", "tambon": "adm2"}
# ~50 m tolerance for the web map; mapping uses full-resolution geometry.
WEB_SIMPLIFY_DEGREES = 0.0005
# Metric CRS for Thailand (UTM 47N) for areas and centroids.
METRIC_CRS = "EPSG:32647"


def read_level(gdb: Path, level: str) -> gpd.GeoDataFrame:
    layer, prefix = LEVELS[level]
    columns = [f"{prefix}_pcode", f"{prefix}_name", f"{prefix}_name1"]
    if PARENT[level]:
        columns.append(f"{PARENT[level]}_pcode")
    gdf = pyogrio.read_dataframe(gdb, layer=layer, columns=columns)
    rename = {f"{prefix}_pcode": "code", f"{prefix}_name": "name_en", f"{prefix}_name1": "name_th"}
    if PARENT[level]:
        rename[f"{PARENT[level]}_pcode"] = "parent_code"
    gdf = gdf.rename(columns=rename)
    if "parent_code" not in gdf:
        gdf["parent_code"] = None
    gdf["level"] = level
    gdf["geometry"] = gdf.geometry.make_valid()
    return gdf[["code", "level", "name_th", "name_en", "parent_code", "geometry"]].to_crs("EPSG:4326")


def build(gdb: Path, out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    gpkg = out_dir / "admin_areas.gpkg"
    web_gpkg = out_dir / "admin_areas_web.gpkg"
    gazetteer = []
    counts = {}
    for level in LEVELS:
        gdf = read_level(gdb, level)
        counts[level] = len(gdf)
        gdf.to_file(gpkg, layer=level, driver="GPKG")
        web = gdf.copy()
        web["geometry"] = web.geometry.simplify(WEB_SIMPLIFY_DEGREES, preserve_topology=True)
        web.to_file(web_gpkg, layer=level, driver="GPKG")

        metric = gdf.to_crs(METRIC_CRS)
        # representative_point is always inside the polygon, unlike centroid for odd shapes.
        points = metric.geometry.representative_point().to_crs("EPSG:4326")
        areas = metric.geometry.area / 1e6
        for row, point, area in zip(gdf.itertuples(), points, areas):
            gazetteer.append(
                {
                    "code": row.code,
                    "level": level,
                    "name_th": row.name_th,
                    "name_en": row.name_en,
                    "parent_code": row.parent_code,
                    "lat": round(point.y, 5),
                    "lon": round(point.x, 5),
                    "area_km2": round(float(area), 2),
                }
            )
    (out_dir / "gazetteer.json").write_text(json.dumps(gazetteer, ensure_ascii=False), encoding="utf-8")
    return counts
