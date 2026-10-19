import logging

from thanphai_shared.redact import RedactFilter, redact

KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


def test_env_secret_is_redacted(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", KEY)
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{KEY}/VIIRS_SNPP_NRT/97.3,5.6,105.7,20.5/5/2026-10-19"
    assert KEY not in redact(f"HTTP Request: GET {url}")
    assert "/api/area/csv/***/VIIRS_SNPP_NRT" in redact(url)


def test_key_query_params_are_redacted():
    text = "links: https://api-gateway.gistda.or.th/x?api_key=SECRETVALUE&limit=1 and ?ukey=abc"
    out = redact(text)
    assert "SECRETVALUE" not in out and "api_key=***&limit=1" in out and "ukey=***" in out


def test_short_values_are_left_alone(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "api")  # too short to be a real secret; would mangle words
    assert redact("TMD api works") == "TMD api works"


def test_log_filter_rewrites_records(monkeypatch):
    monkeypatch.setenv("GISTDA_API_KEY", KEY)
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "failed: %s", (f"url /x/{KEY}",), None)
    RedactFilter().filter(record)
    assert KEY not in record.getMessage()
