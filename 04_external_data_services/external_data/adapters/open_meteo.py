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


class OpenMeteoAirQuality(Adapter):
    """CAMS PM2.5 forecast for many points (district centres), averaged over the next 24 hours.

    Open-Meteo takes many coordinates in one call but counts each one against the free quota
    (600 a minute, 10,000 a day, non-commercial). All 928 districts at once got 429 on 2026-10-22, so
    points are snapped to the CAMS global grid (0.4°): 306 cells cover every district.
    """

    name = "open_meteo_air_quality"
    attribution = "Air quality data by Open-Meteo.com / CAMS (CC BY 4.0)"
    base_url = "https://air-quality-api.open-meteo.com/v1/air-quality"
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
                "hourly": "pm2_5",
                "forecast_days": 2,
                "timezone": "Asia/Bangkok",
            }
            response = await client.get(self.base_url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            body = response.json()
            # One coordinate returns an object, several return a list in the same order.
            batches.append(body if isinstance(body, list) else [body])
        return batches

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        start = fetched_at.replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=24)
        per_cell = {}
        for cell, loc in zip(self.cells, (loc for batch in raw for loc in batch)):
            hourly = loc["hourly"]
            per_cell[cell] = [v for t, v in zip(hourly["time"], hourly["pm2_5"])
                              if v is not None and start <= datetime.fromisoformat(t).replace(tzinfo=BANGKOK) < end]
        records = []
        for (code, lat, lon), cell in zip(self.points, self.cell_of):
            values = per_cell.get(cell)
            if not values:
                continue
            records.append(
                SourceRecord(
                    source=self.name,
                    variable="pm25_model_24h",
                    value=round(sum(values) / len(values), 1),
                    unit="µg/m³",
                    lat=lat,
                    lon=lon,
                    issued_at=fetched_at,
                    valid_time=fetched_at,
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                    extra={"area_code": code, "max_1h": max(values)},
                )
            )
        return records


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
