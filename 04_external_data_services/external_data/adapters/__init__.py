from external_data.adapters.firms import FirmsHotspots
from external_data.adapters.open_meteo import OpenMeteoForecast, OpenMeteoRiverDischarge
from external_data.adapters.thaiwater import ThaiWaterRain24h, ThaiWaterWaterLevel
from external_data.adapters.tmd import TmdWarnings, TmdWeather3Hours
from external_data.adapters.tmd_nwp import TmdNwpDailyForecast

__all__ = [
    "FirmsHotspots",
    "OpenMeteoForecast",
    "OpenMeteoRiverDischarge",
    "ThaiWaterRain24h",
    "ThaiWaterWaterLevel",
    "TmdNwpDailyForecast",
    "TmdWarnings",
    "TmdWeather3Hours",
]
