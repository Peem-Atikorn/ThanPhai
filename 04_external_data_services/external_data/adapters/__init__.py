from external_data.adapters.air4thai import Air4ThaiPm25
from external_data.adapters.firms import FirmsHotspots
from external_data.adapters.open_meteo import OpenMeteoAirQuality, OpenMeteoForecast, OpenMeteoRiverDischarge
from external_data.adapters.thaiwater import ThaiWaterRain24h, ThaiWaterWaterLevel
from external_data.adapters.tmd import TmdWarnings, TmdWeather3Hours
from external_data.adapters.tmd_nwp import TmdNwpDailyForecast

__all__ = [
    "Air4ThaiPm25",
    "FirmsHotspots",
    "OpenMeteoAirQuality",
    "OpenMeteoForecast",
    "OpenMeteoRiverDischarge",
    "ThaiWaterRain24h",
    "ThaiWaterWaterLevel",
    "TmdNwpDailyForecast",
    "TmdWarnings",
    "TmdWeather3Hours",
]
