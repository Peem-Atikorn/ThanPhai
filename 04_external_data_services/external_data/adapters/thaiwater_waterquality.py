"""Automatic water-quality stations (42 on 2026-10-23: PCD and MWA), from ThaiWater's `thailand_main` bundle.

The bundle is already downloaded for the dams, so this costs no extra call.
Salinity (g/L) is the useful signal: sea water moving up the Chao Phraya, Tha Chin, Bang Pakong and Mae Klong in the
dry season reaches the water-works intakes (MWA Samlae). Thresholds are ThaiWater's own (`waterquality.setting.scale`),
read from the payload, so they follow the provider: salinity > 0.25 / 0.5 / 2 g/L and dissolved oxygen < 3 mg/L.
Other sensor values (pH, turbidity, TDS) are passed on as measured; they are not checked here because the live feed has
implausible readings (pH 2.5 and 10.8 in rivers on 2026-10-23). A dissolved-oxygen reading of exactly 0 is a sensor
fault, not anoxic water, so it is dropped.
"""

from datetime import datetime
from typing import Any

from external_data.adapters.base import parse_local, to_float
from external_data.adapters.thaiwater import ATTRIBUTION, _ThaiWater
from thanphai_shared import SourceRecord

# ThaiWater's scale for these is a clear "over / under" limit; for turbidity it marks every value but 5 NTU red.
CHECKED = ("salinity", "do")
OPS = {">": lambda v, t: v > t, ">=": lambda v, t: v >= t, "<": lambda v, t: v < t, "<=": lambda v, t: v <= t}


def exceeded(scale: dict, name: str, value: float | None) -> dict | None:
    """The first of ThaiWater's out-of-standard bands (`inGraph`) that `value` falls in, worst first as listed."""
    if value is None:
        return None
    for band in scale.get(name, []):
        check = OPS.get(band.get("operator"))
        threshold = to_float(band.get("term"))
        if band.get("inGraph") and check and threshold is not None and check(value, threshold):
            return {"text": band.get("text"), "color": band.get("color")}
    return None


class ThaiWaterWaterQuality(_ThaiWater):
    name = "thaiwater_waterquality"
    endpoint = "thailand_main"
    attribution = ATTRIBUTION

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        block = raw.get("waterquality") or {}
        data = block.get("data") or {}
        rows = data.get("data", []) if isinstance(data, dict) else data
        scale = (block.get("setting") or {}).get("scale") or {}
        records = []
        for row in rows:
            station = row.get("waterquality_station") or {}
            values = {
                "salinity": to_float(row.get("waterquality_salinity")),
                "do": to_float(row.get("waterquality_do")) or None,  # 0 = sensor fault
                "ph": to_float(row.get("waterquality_ph")),
                "turbidity": to_float(row.get("waterquality_turbid")),
                "tds": to_float(row.get("waterquality_tds")),
                "conductivity": to_float(row.get("waterquality_conductivity")) or None,
                "temp": to_float(row.get("waterquality_temp")),
            }
            flags = {name: band for name in CHECKED if (band := exceeded(scale, name, values[name]))}
            records.append(
                SourceRecord(
                    source=self.name,
                    variable="salinity",
                    value=values["salinity"],
                    unit="g/L",
                    lat=to_float(station.get("waterquality_station_lat")),
                    lon=to_float(station.get("waterquality_station_long")),
                    station_id=str(station.get("id")) if station.get("id") is not None else None,
                    province_code=station.get("province_code"),
                    observed_at=parse_local(row.get("waterquality_datetime"), "%Y-%m-%d %H:%M"),
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                    extra={
                        "name_th": (station.get("waterquality_station_name") or {}).get("th"),
                        "river_th": station.get("river_name"),
                        "province_th": (station.get("province_name") or {}).get("th"),
                        "amphoe_th": (station.get("amphoe_name") or {}).get("th"),
                        "agency_th": (station.get("agency_shortname") or {}).get("th"),
                        "do_mgl": values["do"],
                        "ph": values["ph"],
                        "turbidity_ntu": values["turbidity"],
                        "tds_mgl": values["tds"],
                        "conductivity_uscm": values["conductivity"],
                        "temp_c": values["temp"],
                        "exceeds": flags,
                    },
                )
            )
        return records
