from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from external_data.main import _newest, app
from thanphai_shared import SourceRecord

client = TestClient(app)
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def record(**kwargs) -> SourceRecord:
    return SourceRecord(source="t", variable="v", value=1.0, unit="mm", fetched_at=NOW, attribution="t", **kwargs)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_point_source_needs_coordinates_inside_thailand():
    assert client.get("/v1/sources/open_meteo_forecast").status_code == 422
    assert client.get("/v1/sources/open_meteo_forecast", params={"lat": 30, "lon": 100}).status_code == 422


def test_unknown_source():
    assert client.get("/v1/sources/nope").status_code == 404


def test_freshness_ignores_forecast_valid_time():
    forecast = record(issued_at=NOW, valid_time=NOW + timedelta(days=3))
    assert _newest([forecast]) == NOW


def test_freshness_uses_newest_observation():
    older, newer = record(observed_at=NOW - timedelta(hours=3)), record(observed_at=NOW)
    assert _newest([older, newer]) == NOW
    assert _newest([]) is None
