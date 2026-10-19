"""On-demand time series from ThaiWater, fetched when someone opens a station or a dam.

- `public/waterlevel_graph`: hourly water level (m MSL) and discharge (m³/s) of one station, plus its bank
  and channel capacity (qmax). Needs station_type and a date range; without them the server panics.
- `analyst/dam_yearly_graph`: one value per day for whole years, plus the dam's upper/lower rule curves
  (the operating band RID keeps storage in). Rule curves come dated 2020 and are a day-of-year template.
"""

import ast
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from external_data.adapters.base import to_float
from external_data.adapters.thaiwater import BASE_URL

ROOT = BASE_URL.rsplit("/", 1)[0]
DAM_SERIES = {"storage": "dam_storage", "released": "dam_released", "inflow": "dam_inflow"}
MAX_GRAPH_DAYS = 7


def _curve(value: Any) -> list[dict]:
    """Rule curves are lists, or (seen live) the repr of a Python list as a string."""
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return []
    return value if isinstance(value, list) else []


def parse_station_graph(raw: Any) -> dict:
    data = (raw or {}).get("data") or {}
    points = []
    for row in data.get("graph_data") or []:
        level, flow = to_float(row.get("value")), to_float(row.get("discharge"))
        if level is None and flow is None:
            continue
        points.append({"time": row.get("datetime"), "level_msl": level, "discharge_m3s": flow if flow and flow > 0 else None})
    return {
        "points": points,
        "bank_msl": to_float(data.get("min_bank")),
        "ground_msl": to_float(data.get("ground_level")),
        "qmax_m3s": to_float(data.get("qmax")),
    }


async def fetch_station_graph(client: httpx.AsyncClient, station_id: int, days: int = 3, today: date | None = None) -> dict:
    days = max(1, min(days, MAX_GRAPH_DAYS))
    end = today or date.today()
    params = {"station_type": "tele_waterlevel", "station_id": station_id,
              "start_date": (end - timedelta(days=days)).isoformat(), "end_date": end.isoformat()}
    response = await client.get(f"{ROOT}/public/waterlevel_graph", params=params, timeout=30)
    response.raise_for_status()
    return parse_station_graph(response.json())


def parse_dam_year(raw: Any) -> dict:
    data = (raw or {}).get("data") or {}
    series = []
    for item in data.get("graph_data") or []:
        values = []
        for row in item.get("data") or []:
            value = to_float(row.get("value"))
            if value is not None:
                values.append([str(row.get("date", ""))[:10], value])
        series.append({"year": item.get("year"), "values": values})

    def curve(key: str) -> list:
        # Day-of-year template: keep "MM-DD" so it can be drawn over any year.
        return [[str(r.get("date", ""))[5:10], to_float(r.get("value"))] for r in _curve(data.get(key))
                if to_float(r.get("value")) is not None]

    return {
        "series": series,
        "upper_rule_curve": curve("upper_rule_curve"),
        "lower_rule_curve": curve("lower_rule_curve"),
        "normal_storage_mcm": to_float(data.get("normal_bound")),
        "max_storage_mcm": to_float(data.get("upper_bound")),
        "min_storage_mcm": to_float(data.get("lower_bound")),
    }


async def fetch_dam_year(client: httpx.AsyncClient, dam_id: int, series: str, years: list[int] | None = None) -> dict:
    if series not in DAM_SERIES:
        raise ValueError(f"unknown series {series}")
    years = years or [datetime.now().year - 1, datetime.now().year]
    params = {"data_type": DAM_SERIES[series], "dam_id": dam_id, "year": ",".join(map(str, years))}
    response = await client.get(f"{ROOT}/analyst/dam_yearly_graph", params=params, timeout=30)
    response.raise_for_status()
    return parse_dam_year(response.json())
