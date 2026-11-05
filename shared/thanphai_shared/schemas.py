from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class HazardType(str, Enum):
    RAIN_STORM = "rain_storm"
    FLOOD = "flood"
    DROUGHT = "drought"
    FIRE = "fire"
    AIR_QUALITY = "air_quality"
    HEAT = "heat"
    EARTHQUAKE = "earthquake"
    MARINE = "marine"
    TROPICAL_CYCLONE = "tropical_cyclone"
    LANDSLIDE = "landslide"


class AlertLevel(int, Enum):
    """Ordered so that max() gives the most severe level."""

    NORMAL = 0
    WATCH = 1
    WARNING = 2
    CRITICAL = 3


class SourceRecord(BaseModel):
    """One parsed value from an external source, before area mapping (module 05)."""

    source: str
    variable: str
    value: float | None
    unit: str
    lat: float | None = None
    lon: float | None = None
    station_id: str | None = None
    province_code: str | None = None
    # Observations set observed_at; forecasts set issued_at and valid_time.
    observed_at: datetime | None = None
    issued_at: datetime | None = None
    valid_time: datetime | None = None
    fetched_at: datetime
    attribution: str
    extra: dict = Field(default_factory=dict)


class RainDay(BaseModel):
    """One row of a bulletin's daily table: provinces TMD expects heavy (35.1-90 mm) or very heavy (> 90 mm) rain."""

    day: date
    very_heavy: bool
    # The row's province text as written ("ภาคใต้: จังหวัดนครศรีธรรมราช และสงขลา"); module 05 reads the provinces.
    areas: str


class OfficialWarning(BaseModel):
    source: str
    issue_no: str
    title: str
    headline: str | None = None
    # The bulletin's statements from its PDF, with each region's own days; the API headline has only the first.
    body: str | None = None
    # The PDF's daily tables (pages 2+ of issue 288/2569); empty when the PDF has none or could not be read.
    rain_days: list[RainDay] = Field(default_factory=list)
    hazards: list[HazardType]
    announced_at: datetime
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    url: str | None = None
    fetched_at: datetime
    attribution: str


class SourceHealth(BaseModel):
    source: str
    ok: bool
    record_count: int = 0
    last_success: datetime | None = None
    last_error: str | None = None
    newest_data_at: datetime | None = None
