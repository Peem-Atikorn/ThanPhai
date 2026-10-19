import os
from datetime import datetime
from typing import Any

import httpx

from external_data.adapters.base import Adapter, parse_local, to_float
from thanphai_shared import SourceRecord

BASE_URL = os.getenv("THAIWATER_BASE_URL", "https://api-v3.thaiwater.net/api/v1/thaiwater30/public")
ATTRIBUTION = "ที่มา: คลังข้อมูลน้ำแห่งชาติ (สสน.) และหน่วยงานเครือข่าย"
TIME_FORMAT = "%Y-%m-%d %H:%M"


OVERFLOW_TEXT = "ล้นตลิ่ง"
MAX_PLAUSIBLE_BANK_DIFF_M = 10.0


def signed_bank_diff(row: dict) -> float | None:
    """Water level relative to the lowest bank in metres: positive = overflowing, negative = below bank.

    ThaiWater always sends diff_wl_bank as a positive number and puts the direction only in
    diff_wl_bank_text ("ล้นตลิ่ง" or "ต่ำกว่าตลิ่ง"), so the sign has to be rebuilt here.
    """
    # Stations without a configured bank have min_bank = 0, and ThaiWater then reports the whole
    # MSL level as overflow (seen live: "+331 m"). Treat the bank as unknown instead.
    min_bank = to_float((row.get("station") or {}).get("min_bank"))
    if min_bank is None or min_bank == 0:
        return None
    diff = to_float(row.get("diff_wl_bank"))
    text = row.get("diff_wl_bank_text") or ""
    if diff is not None and text:
        signed = abs(diff) if OVERFLOW_TEXT in text else -abs(diff)
    else:
        level = to_float(row.get("waterlevel_msl"))
        if level is None:
            return None
        signed = round(level - min_bank, 3)
    # No river in Thailand overflows its bank by more than this; larger values are datum errors.
    return signed if abs(signed) <= MAX_PLAUSIBLE_BANK_DIFF_M else None


def bank_quality_flag(row: dict) -> str:
    min_bank = to_float((row.get("station") or {}).get("min_bank"))
    if min_bank is None or min_bank == 0:
        return "bank_unknown"
    if signed_bank_diff(row) is None and to_float(row.get("waterlevel_msl")) is not None:
        return "implausible"
    return "ok"


def provider_tambon_code(geocode: dict) -> str | None:
    """Official tambon P-code (TH + province + amphoe + tambon) from ThaiWater's geocode, if complete.

    River stations often sit exactly on a provincial border, so the station's registered area is more
    reliable than a point-in-polygon test.
    """
    parts = (geocode.get("province_code"), geocode.get("amphoe_code"), geocode.get("tumbon_code"))
    if not all(parts):
        return None
    province, amphoe, tambon = parts
    return f"TH{province}{amphoe}{tambon}"


STATION_CODE_PREFIXES = ("ridhydro_",)


def station_code(oldcode: str | None) -> str | None:
    """Station code people know (C.13), without ThaiWater's per-feed prefix (ridhydro_C.13)."""
    if not oldcode:
        return None
    for prefix in STATION_CODE_PREFIXES:
        if oldcode.startswith(prefix):
            return oldcode[len(prefix):]
    return oldcode


def positive(value) -> float | None:
    number = to_float(value)
    return number if number is not None and number > 0 else None


def river_flow(row: dict) -> float | None:
    """River discharge in m³/s. Zero is how stations without a flow gauge report, so it counts as unknown."""
    return positive(row.get("discharge")) or positive(row.get("flow_rate"))


class _ThaiWater(Adapter):
    endpoint: str
    attribution = ATTRIBUTION
    # Payloads are several MB, so allow more time than the default.
    timeout_seconds = 60.0

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        response = await client.get(f"{BASE_URL}/{self.endpoint}", timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()


class ThaiWaterRain24h(_ThaiWater):
    name = "thaiwater_rain_24h"
    endpoint = "rain_24h"

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        records = []
        for row in raw.get("data", []):
            station = row.get("station") or {}
            geocode = row.get("geocode") or {}
            records.append(
                SourceRecord(
                    source=self.name,
                    variable="rain_24h",
                    value=to_float(row.get("rain_24h")),
                    unit="mm",
                    lat=to_float(station.get("tele_station_lat")),
                    lon=to_float(station.get("tele_station_long")),
                    station_id=str(station.get("id")) if station.get("id") is not None else None,
                    province_code=geocode.get("province_code"),
                    observed_at=parse_local(row.get("rainfall_datetime"), TIME_FORMAT),
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                    extra={
                        "rain_1h": to_float(row.get("rain_1h")),
                        "amphoe_en": (geocode.get("amphoe_name") or {}).get("en"),
                        "tumbon_en": (geocode.get("tumbon_name") or {}).get("en"),
                    },
                )
            )
        return records


class ThaiWaterWaterLevel(_ThaiWater):
    name = "thaiwater_waterlevel"
    endpoint = "waterlevel_load"

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        records = []
        for row in raw.get("waterlevel_data", {}).get("data", []):
            station = row.get("station") or {}
            geocode = row.get("geocode") or {}
            records.append(
                SourceRecord(
                    source=self.name,
                    variable="waterlevel_msl",
                    value=to_float(row.get("waterlevel_msl")),
                    unit="m MSL",
                    lat=to_float(station.get("tele_station_lat")),
                    lon=to_float(station.get("tele_station_long")),
                    station_id=str(station.get("id")) if station.get("id") is not None else None,
                    province_code=geocode.get("province_code"),
                    observed_at=parse_local(row.get("waterlevel_datetime"), TIME_FORMAT),
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                    extra={
                        "previous_msl": to_float(row.get("waterlevel_msl_previous")),
                        "storage_percent": to_float(row.get("storage_percent")),
                        "above_bank_m": signed_bank_diff(row),
                        "bank_quality": bank_quality_flag(row),
                        "min_bank": to_float(station.get("min_bank")),
                        "situation_level": row.get("situation_level"),
                        "river_name": row.get("river_name"),
                        "tambon_code": provider_tambon_code(geocode),
                        # For the river view: one physical station can appear twice (e.g. "C.13" and
                        # "ridhydro_C.13"), with the flow in `discharge` on one row and `flow_rate` on the other.
                        "name_th": (station.get("tele_station_name") or {}).get("th"),
                        "station_code": station_code(station.get("tele_station_oldcode")),
                        "discharge_m3s": river_flow(row),
                        "qmax_m3s": positive(station.get("qmax")),
                        "is_key_station": bool(station.get("is_key_station")),
                        "agency_en": ((row.get("agency") or {}).get("agency_shortname") or {}).get("en"),
                        "basin_th": ((row.get("basin") or {}).get("basin_name") or {}).get("th"),
                        "province_th": (geocode.get("province_name") or {}).get("th"),
                        "amphoe_th": (geocode.get("amphoe_name") or {}).get("th"),
                    },
                )
            )
        return records
