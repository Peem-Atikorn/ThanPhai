"""Map point records (stations, hotspots) to tambon / amphoe / province codes."""

from pathlib import Path

import geopandas as gpd
import pandas as pd

from thanphai_shared import SourceRecord

METRIC_CRS = "EPSG:32647"
# Coastal and river-mouth stations can sit just outside the land polygon.
NEAREST_MAX_METERS = 2000
RECORD_COLUMNS = ["source", "variable", "value", "station_id", "observed_at", "lat", "lon",
                  "tambon_code", "amphoe_code", "province_code", "match"]


class AreaMapper:
    def __init__(self, tambon: gpd.GeoDataFrame):
        self.tambon = tambon[["code", "parent_code", "geometry"]].rename(columns={"code": "tambon_code", "parent_code": "amphoe_code"})
        self.tambon_metric = self.tambon.to_crs(METRIC_CRS)
        self.tambon_parent = dict(zip(self.tambon["tambon_code"], self.tambon["amphoe_code"]))

    @classmethod
    def from_gpkg(cls, gpkg: Path) -> "AreaMapper":
        return cls(gpd.read_file(gpkg, layer="tambon"))

    def assign_points(self, lats: list[float], lons: list[float], nearest: bool = True) -> pd.DataFrame:
        """Return one row per input point with tambon_code, amphoe_code, province_code, and match type.

        nearest=False skips the ≤2 km nearest search for points outside every polygon. Use it for bulk
        satellite points, where most outside points are in neighbouring countries and the search is slow.
        """
        points = gpd.GeoDataFrame(
            {"idx": range(len(lats))}, geometry=gpd.points_from_xy(lons, lats), crs="EPSG:4326"
        )
        inside = gpd.sjoin(points, self.tambon, how="left", predicate="within").drop_duplicates("idx")
        inside["match"] = inside["tambon_code"].notna().map({True: "within", False: None})

        missing = inside[inside["tambon_code"].isna()]
        if nearest and not missing.empty:
            near = gpd.sjoin_nearest(
                points.loc[missing.index].to_crs(METRIC_CRS),
                self.tambon_metric,
                how="left",
                max_distance=NEAREST_MAX_METERS,
                distance_col="distance_m",
            ).drop_duplicates("idx")
            near = near.set_index("idx")
            for i in missing["idx"]:
                if i in near.index and pd.notna(near.at[i, "tambon_code"]):
                    inside.loc[inside["idx"] == i, ["tambon_code", "amphoe_code"]] = (
                        near.at[i, "tambon_code"], near.at[i, "amphoe_code"],
                    )
                    inside.loc[inside["idx"] == i, "match"] = "nearest"

        out = inside.sort_values("idx")[["idx", "tambon_code", "amphoe_code", "match"]].reset_index(drop=True)
        out["province_code"] = out["amphoe_code"].str[:4]
        out["match"] = out["match"].fillna("outside")
        return out

    def assign_records(self, records: list[SourceRecord]) -> pd.DataFrame:
        """Map records to areas. A provider tambon code (extra["tambon_code"]) that exists in the
        boundaries wins over the point-in-polygon result; records without coordinates are kept
        with match = 'no_location'."""
        located = [r for r in records if r.lat is not None and r.lon is not None]
        mapped = self.assign_points([r.lat for r in located], [r.lon for r in located]) if located else pd.DataFrame()
        rows = []
        for i, record in enumerate(located):
            m = mapped.iloc[i]
            provider = record.extra.get("tambon_code")
            if provider in self.tambon_parent:
                tambon, amphoe, match = provider, self.tambon_parent[provider], "provider"
            else:
                tambon, amphoe, match = m.tambon_code, m.amphoe_code, m.match
            rows.append({**_record_row(record), "tambon_code": tambon, "amphoe_code": amphoe,
                         "province_code": amphoe[:4] if isinstance(amphoe, str) else None, "match": match})
        for record in records:
            if record.lat is None or record.lon is None:
                rows.append({**_record_row(record), "tambon_code": None, "amphoe_code": None,
                             "province_code": f"TH{record.province_code}" if record.province_code else None, "match": "no_location"})
        # Zero records is normal (no fire hotspots in the rainy season); keep the columns so callers still work.
        return pd.DataFrame(rows, columns=None if rows else RECORD_COLUMNS)


def _record_row(record: SourceRecord) -> dict:
    return {
        "source": record.source,
        "variable": record.variable,
        "value": record.value,
        "station_id": record.station_id,
        "observed_at": record.observed_at,
        "lat": record.lat,
        "lon": record.lon,
        **{f"extra_{k}": v for k, v in record.extra.items() if isinstance(v, (int, float, str)) or v is None},
    }
