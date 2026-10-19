"""Load admin areas into PostGIS (table admin_area), following the canonical schema in 05_geo_data_integration/03_process.txt.

Run from the repo root after `docker compose up -d postgis` and scripts/build_admin_areas.py:
    .venv/Scripts/python scripts/load_postgis.py
"""

import os
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPolygon
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
GPKG = ROOT / "data" / "processed" / "admin_areas.gpkg"


def database_url() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.split("#", 1)[0].strip()
    user = os.getenv("POSTGRES_USER", env.get("POSTGRES_USER", "thanphai"))
    password = os.getenv("POSTGRES_PASSWORD", env.get("POSTGRES_PASSWORD", ""))
    host = os.getenv("POSTGRES_HOST", "localhost")
    return f"postgresql+psycopg://{user}:{password}@{host}:5432/thanphai"


def main() -> None:
    engine = create_engine(database_url())
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text("DROP TABLE IF EXISTS admin_area"))
    frames = [gpd.read_file(GPKG, layer=level) for level in ("province", "amphoe", "tambon")]
    areas = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    # Mixed Polygon / MultiPolygon in one column needs a single type for PostGIS.
    areas["geometry"] = areas.geometry.force_2d().apply(lambda g: MultiPolygon([g]) if g.geom_type == "Polygon" else g)
    bad = areas[areas.geometry.geom_type != "MultiPolygon"]
    if not bad.empty:
        sys.exit(f"unexpected geometry types: {bad.geometry.geom_type.value_counts().to_dict()}")
    areas.to_postgis("admin_area", engine, if_exists="replace", index=False, chunksize=500)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE admin_area ADD PRIMARY KEY (code)"))
        conn.execute(text("CREATE INDEX admin_area_geom_idx ON admin_area USING GIST (geometry)"))
        conn.execute(text("CREATE INDEX admin_area_parent_idx ON admin_area (parent_code)"))
        counts = conn.execute(text("SELECT level, count(*) FROM admin_area GROUP BY level ORDER BY 2")).all()
        hit = conn.execute(text(
            "SELECT code, name_th FROM admin_area WHERE level = 'tambon' "
            "AND ST_Contains(geometry, ST_SetSRID(ST_Point(100.12744, 14.15657), 4326))"
        )).all()
    print("loaded:", dict(counts))
    print("point-in-polygon check (Suphan Buri station TTC06):", hit)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
