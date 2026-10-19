from datetime import datetime
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
