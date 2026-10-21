import asyncio
from datetime import datetime, timedelta
from typing import Any

import httpx

from external_data.adapters.base import BANGKOK, Adapter
from thanphai_shared import SourceRecord

UNITS = {"precipitation": "mm", "relative_humidity_2m": "%", "wind_speed_10m": "km/h", "river_discharge": "m3/s"}


class OpenMeteoForecast(Adapter):
    """Hourly forecast for one point. Fallback when the TMD NWP token is missing."""

    name = "open_meteo_forecast"
    attribution = "Weather data by Open-Meteo.com (CC BY 4.0)"
    base_url = "https://api.open-meteo.com/v1/forecast"
    variables = ("precipitation", "relative_humidity_2m", "wind_speed_10m")

    def __init__(self, lat: float, lon: float, forecast_days: int = 3):
        self.lat, self.lon, self.forecast_days = lat, lon, forecast_days

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        params = {
            "latitude": self.lat,
            "longitude": self.lon,
            "hourly": ",".join(self.variables),
            "forecast_days": self.forecast_days,
            "timezone": "Asia/Bangkok",
        }
        response = await client.get(self.base_url, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        hourly = raw["hourly"]
        records = []
        for i, time in enumerate(hourly["time"]):
            valid_time = datetime.fromisoformat(time).replace(tzinfo=BANGKOK)
            for variable in self.variables:
                records.append(
                    SourceRecord(
                        source=self.name,
                        variable=variable,
                        value=hourly[variable][i],
                        unit=UNITS[variable],
                        lat=raw["latitude"],
                        lon=raw["longitude"],
                        # Open-Meteo does not report the model run time, so the fetch time stands in for issued_at.
                        issued_at=fetched_at,
                        valid_time=valid_time,
                        fetched_at=fetched_at,
                        attribution=self.attribution,
                    )
                )
        return records


# All grid adapters share this, so together they stay under Open-Meteo's 600 calls a minute
# (one batch of up to 100 points, then a pause, whichever adapter sends it).
_PACE = asyncio.Lock()
PACE_SECONDS = 12.0


class OpenMeteoGrid(Adapter):
    """Hourly values for many points (district centres), summarised over the next 24 hours.

    Open-Meteo takes many coordinates in one call but counts each one against the free quota
    (600 a minute, 10,000 a day, non-commercial). All 928 districts at once got 429 on 2026-10-22, so
    points are snapped to a 0.4° grid (306 cells cover every district) and batches are paced.
    Subclasses set the endpoint, the hourly variables, and `summarise`.
    """

    base_url: str
    hourly: tuple[str, ...]
    variable: str
    unit: str
    grid_degrees = 0.4
    batch_size = 100
    timeout_seconds = 60.0

    def __init__(self, points: list[tuple[str, float, float]]):
        """points: (area_code, lat, lon)."""
        self.points = points
        g = self.grid_degrees
        self.cell_of = [(round(round(lat / g) * g, 4), round(round(lon / g) * g, 4)) for _, lat, lon in points]
        self.cells = list(dict.fromkeys(self.cell_of))

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        batches = []
        for start in range(0, len(self.cells), self.batch_size):
            batch = self.cells[start:start + self.batch_size]
            params = {
                "latitude": ",".join(str(lat) for lat, _ in batch),
                "longitude": ",".join(str(lon) for _, lon in batch),
                "hourly": ",".join(self.hourly),
                "forecast_days": 2,
                "timezone": "Asia/Bangkok",
            }
            async with _PACE:
                response = await client.get(self.base_url, params=params, timeout=self.timeout_seconds)
                response.raise_for_status()
                body = response.json()
                await asyncio.sleep(PACE_SECONDS)  # background jobs only, so the wait costs nobody
            # One coordinate returns an object, several return a list in the same order.
            batches.append(body if isinstance(body, list) else [body])
        return batches

    def summarise(self, hours: list[dict]) -> tuple[float, dict] | None:
        """(value, extra) from the next 24 hourly rows ({"time": datetime, <variable>: value}), or None."""
        raise NotImplementedError

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        start = fetched_at.replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=24)
        per_cell = {}
        for cell, loc in zip(self.cells, (loc for batch in raw for loc in batch)):
            hourly = loc["hourly"]
            rows = [{"time": datetime.fromisoformat(t).replace(tzinfo=BANGKOK),
                     **{name: hourly[name][i] for name in self.hourly}} for i, t in enumerate(hourly["time"])]
            per_cell[cell] = self.summarise([r for r in rows if start <= r["time"] < end])
        records = []
        for (code, lat, lon), cell in zip(self.points, self.cell_of):
            summary = per_cell.get(cell)
            if summary is None:
                continue
            value, extra = summary
            records.append(
                SourceRecord(
                    source=self.name,
                    variable=self.variable,
                    value=value,
                    unit=self.unit,
                    lat=lat,
                    lon=lon,
                    issued_at=fetched_at,
                    valid_time=fetched_at,
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                    extra={"area_code": code, **extra},
                )
            )
        return records


class OpenMeteoAirQuality(OpenMeteoGrid):
    """CAMS PM2.5 forecast (0.4° global grid), mean of the next 24 hours."""

    name = "open_meteo_air_quality"
    attribution = "Air quality data by Open-Meteo.com / CAMS (CC BY 4.0)"
    base_url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    hourly = ("pm2_5",)
    variable = "pm25_model_24h"
    unit = "µg/m³"

    def summarise(self, hours: list[dict]) -> tuple[float, dict] | None:
        values = [h["pm2_5"] for h in hours if h["pm2_5"] is not None]
        if not values:
            return None
        return round(sum(values) / len(values), 1), {"max_1h": max(values)}


def heat_index_c(temp_c: float, rh: float) -> float:
    """NOAA heat index (Rothfusz regression with NOAA's adjustments), in °C.

    Below 80 °F NOAA uses its simple formula, which stays close to the air temperature.
    """
    t = temp_c * 9 / 5 + 32
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if (simple + t) / 2 < 80:
        hi = simple
    else:
        hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh - 0.00683783 * t * t
              - 0.05481717 * rh * rh + 0.00122874 * t * t * rh + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)
        if rh < 13 and 80 <= t <= 112:
            hi -= ((13 - rh) / 4) * ((17 - abs(t - 95)) / 17) ** 0.5
        elif rh > 85 and 80 <= t <= 87:
            hi += ((rh - 85) / 10) * ((87 - t) / 5)
    return (hi - 32) * 5 / 9


class OpenMeteoHeatIndex(OpenMeteoGrid):
    """Highest heat index in the next 24 hours, from forecast temperature and humidity."""

    name = "open_meteo_heat"
    attribution = "Weather data by Open-Meteo.com (CC BY 4.0)"
    base_url = "https://api.open-meteo.com/v1/forecast"
    hourly = ("temperature_2m", "relative_humidity_2m")
    variable = "heat_index_max_24h"
    unit = "°C"

    def summarise(self, hours: list[dict]) -> tuple[float, dict] | None:
        rows = [h for h in hours if h["temperature_2m"] is not None and h["relative_humidity_2m"] is not None]
        if not rows:
            return None
        peak = max(rows, key=lambda h: heat_index_c(h["temperature_2m"], h["relative_humidity_2m"]))
        value = heat_index_c(peak["temperature_2m"], peak["relative_humidity_2m"])
        return round(value, 1), {"peak_time": peak["time"].isoformat(), "temperature_c": peak["temperature_2m"],
                                 "humidity_pct": peak["relative_humidity_2m"]}


class OpenMeteoMarine(OpenMeteoGrid):
    """Highest significant wave height in the next 24 hours at points at sea (0.1° cells; land cells have no data)."""

    name = "open_meteo_marine"
    attribution = "Marine data by Open-Meteo.com (CC BY 4.0)"
    base_url = "https://marine-api.open-meteo.com/v1/marine"
    hourly = ("wave_height",)
    variable = "wave_height_max_24h"
    unit = "m"
    grid_degrees = 0.1

    def summarise(self, hours: list[dict]) -> tuple[float, dict] | None:
        values = [h["wave_height"] for h in hours if h["wave_height"] is not None]
        if not values:
            return None  # a land cell: this point is not at sea
        peak = max(hours, key=lambda h: h["wave_height"] if h["wave_height"] is not None else -1)
        return round(max(values), 2), {"peak_time": peak["time"].isoformat()}


class OpenMeteoRiverDischarge(Adapter):
    """GloFAS daily river discharge forecast, used as a flood feature."""

    name = "open_meteo_flood"
    attribution = "Flood data by Open-Meteo.com / GloFAS (CC BY 4.0)"
    base_url = "https://flood-api.open-meteo.com/v1/flood"

    def __init__(self, lat: float, lon: float, forecast_days: int = 7):
        self.lat, self.lon, self.forecast_days = lat, lon, forecast_days

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        params = {"latitude": self.lat, "longitude": self.lon, "daily": "river_discharge", "forecast_days": self.forecast_days}
        response = await client.get(self.base_url, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        daily = raw["daily"]
        return [
            SourceRecord(
                source=self.name,
                variable="river_discharge",
                value=value,
                unit=UNITS["river_discharge"],
                lat=raw["latitude"],
                lon=raw["longitude"],
                issued_at=fetched_at,
                # Flood API dates are in GMT.
                valid_time=datetime.fromisoformat(day + "T00:00:00+00:00"),
                fetched_at=fetched_at,
                attribution=self.attribution,
            )
            for day, value in zip(daily["time"], daily["river_discharge"])
        ]
