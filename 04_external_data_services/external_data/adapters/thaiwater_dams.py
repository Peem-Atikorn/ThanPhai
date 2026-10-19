"""Daily storage of Thailand's 35 large dams, from ThaiWater's `thailand_main` bundle.

Units from the provider: volumes in million m³ (ล้าน ลบ.ม.), inflow and release per day.
`dam_storage_percent` is relative to the normal storage level (รนก.), so it can exceed 100.
Status follows ThaiWater's own published rule (`dam.setting.rule_web`): storage > 80 % = high,
usable water <= 30 % = critically low; labels are worded the way people talk.
Daily inflow/release are volumes for the day (million m³); 1 million m³/day ≈ 11.6 m³/s on average.
"""

from datetime import datetime
from typing import Any

from external_data.adapters.base import parse_local, to_float
from external_data.adapters.thaiwater import ATTRIBUTION, _ThaiWater
from thanphai_shared import SourceRecord

HIGH_STORAGE_PERCENT = 80.0
LOW_USABLE_PERCENT = 30.0
# ThaiWater colour bands for storage percent (setting.scale), shown as plain labels.
# Plain-language labels people use, on ThaiWater's own bands (>100, 81-100, 51-80, 31-50, <=30 %).
BANDS = ((100.0, "น้ำเกินระดับกักเก็บปกติ"), (80.0, "น้ำใกล้เต็มอ่าง"), (50.0, "น้ำพอใช้"), (30.0, "น้ำค่อนข้างน้อย"))


def dam_status(storage_percent: float | None, usable_percent: float | None) -> tuple[str, str]:
    """(code, Thai label). Codes: HIGH, LOW_CRITICAL, NORMAL, UNKNOWN."""
    if storage_percent is None:
        return "UNKNOWN", "ไม่มีข้อมูล"
    if storage_percent > HIGH_STORAGE_PERCENT:
        label = "น้ำเกินระดับกักเก็บปกติ" if storage_percent > 100 else "น้ำใกล้เต็มอ่าง"
        return "HIGH", label
    if usable_percent is not None and usable_percent <= LOW_USABLE_PERCENT:
        return "LOW_CRITICAL", "น้ำใช้การได้เหลือน้อย"
    label = next((text for limit, text in BANDS if storage_percent > limit), "น้ำน้อย")
    return "NORMAL", label


class ThaiWaterDams(_ThaiWater):
    name = "thaiwater_dams"
    endpoint = "thailand_main"
    attribution = ATTRIBUTION

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        data = (raw.get("dam") or {}).get("data") or {}
        rows = data.get("data", []) if isinstance(data, dict) else data
        records = []
        for row in rows:
            dam = row.get("dam") or {}
            geocode = row.get("geocode") or {}
            pct = to_float(row.get("dam_storage_percent"))
            usable = to_float(row.get("dam_uses_water_percent"))
            code, label = dam_status(pct, usable)
            records.append(
                SourceRecord(
                    source=self.name,
                    variable="dam_storage_percent",
                    value=pct,
                    unit="% of normal storage",
                    lat=to_float(dam.get("dam_lat")),
                    lon=to_float(dam.get("dam_long")),
                    station_id=str(dam.get("id")) if dam.get("id") is not None else None,
                    province_code=geocode.get("province_code"),
                    observed_at=parse_local(row.get("dam_date"), "%Y-%m-%d"),
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                    extra={
                        "name_th": (dam.get("dam_name") or {}).get("th"),
                        "name_en": (dam.get("dam_name") or {}).get("en"),
                        "storage_mcm": to_float(row.get("dam_storage")),
                        "normal_storage_mcm": to_float(dam.get("normal_storage")),
                        "max_storage_mcm": to_float(dam.get("max_storage")),
                        "usable_mcm": to_float(row.get("dam_uses_water")),
                        "usable_percent": usable,
                        "inflow_mcm": to_float(row.get("dam_inflow")),
                        "released_mcm": to_float(row.get("dam_released")),
                        "spilled_mcm": to_float(row.get("dam_spilled")),
                        # Totals since 1 January, and the long-term average yearly inflow.
                        "inflow_acc_mcm": to_float(row.get("dam_inflow_acc")),
                        "released_acc_mcm": to_float(row.get("dam_released_acc")),
                        "inflow_avg_year_mcm": to_float(row.get("dam_inflow_avg")),
                        "status": code,
                        "status_th": label,
                        "province_th": (geocode.get("province_name") or {}).get("th"),
                        "basin_th": ((row.get("basin") or {}).get("basin_name") or {}).get("th"),
                        "agency_th": (((row.get("agency") or {}).get("agency_shortname") or {}).get("th")),
                    },
                )
            )
        return records
