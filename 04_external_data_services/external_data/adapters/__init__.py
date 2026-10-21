from external_data.adapters.air4thai import Air4ThaiPm25
from external_data.adapters.earthquakes import TmdEarthquakes, UsgsEarthquakes, merge_quakes
from external_data.adapters.gdacs import GdacsEvents
from external_data.adapters.firms import FirmsHotspots
from external_data.adapters.open_meteo import (OpenMeteoAirQuality, OpenMeteoForecast, OpenMeteoHeatIndex, OpenMeteoMarine,
                                              OpenMeteoRiverDischarge)
from external_data.adapters.thaiwater import ThaiWaterRain24h, ThaiWaterWaterLevel
from external_data.adapters.tmd import TmdWarnings, TmdWeather3Hours
from external_data.adapters.tmd_nwp import TmdNwpDailyForecast

__all__ = [
    "Air4ThaiPm25",
    "FirmsHotspots",
    "GdacsEvents",
    "OpenMeteoAirQuality",
    "OpenMeteoForecast",
    "OpenMeteoHeatIndex",
    "OpenMeteoMarine",
    "OpenMeteoRiverDischarge",
    "ThaiWaterRain24h",
    "ThaiWaterWaterLevel",
    "TmdNwpDailyForecast",
    "TmdEarthquakes",
    "TmdWarnings",
    "TmdWeather3Hours",
    "UsgsEarthquakes",
    "merge_quakes",
]
