from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from thanphai_shared import SourceRecord

# Thailand has no daylight saving time, so a fixed offset is exact and needs no tzdata.
BANGKOK = timezone(timedelta(hours=7), "Asia/Bangkok")

# Thailand bounding box: west, south, east, north. Use the country polygon in module 05 for exact clipping.
THAILAND_BBOX = (97.3, 5.6, 105.7, 20.5)
# Thailand with Myanmar, Laos and Cambodia, where smoke that reaches Thailand often starts.
REGION_BBOX = (92.0, 5.6, 108.0, 28.6)


def in_thailand_bbox(lat: float, lon: float, bbox: tuple = THAILAND_BBOX) -> bool:
    west, south, east, north = bbox
    return south <= lat <= north and west <= lon <= east


def parse_local(value: str | None, fmt: str) -> datetime | None:
    """Parse a provider timestamp given in Thai local time."""
    if not value:
        return None
    return datetime.strptime(value, fmt).replace(tzinfo=BANGKOK)


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Adapter(ABC):
    name: str
    attribution: str
    timeout_seconds: float = 30.0

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient) -> Any:
        """Call the provider and return the raw payload."""

    @abstractmethod
    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        """Turn the raw payload into SourceRecords. Must not call the network."""

    async def run(self, client: httpx.AsyncClient) -> list[SourceRecord]:
        fetched_at = datetime.now(timezone.utc)
        raw = await self.fetch(client)
        return self.parse(raw, fetched_at)
