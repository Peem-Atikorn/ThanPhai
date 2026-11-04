import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_agent.py"
spec = importlib.util.spec_from_file_location("smoke_agent", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def answer():
    return {
        "intent": "earthquake", "text": "ขนาด 4.2 วันที่ 4/11/2569\nที่มา: USGS",
        "sources": ["USGS"], "evidence": [{"magnitude": 4.2}],
        "tool_calls": ["get_earthquakes()"], "llm_used": False,
        "generated_at": "2026-11-04T01:00:00+00:00",
        "data_updated_at": "2026-11-04T00:00:00+00:00",
        "degraded_services": [], "inputs_pending": [],
    }


def test_live_smoke_covers_all_eight_groups():
    assert {case[0] for case in smoke.CASES} == {
        "earthquake", "storms", "waves", "water_quality", "pm25_stations",
        "fire_hotspots", "dam_history", "media",
    }


def test_complete_response_passes_contract_only():
    result = smoke.check_answer(smoke.CASES[0], answer())
    assert result == {"status": "contract_pass", "errors": [], "review": []}


@pytest.mark.parametrize("field,value", [
    ("intent", "risk"), ("sources", []), ("sources", [None]), ("sources", [{}]),
    ("text", ""), ("tool_calls", []),
    ("llm_used", True), ("generated_at", "bad date"), ("generated_at", "2026-11-04T01:00:00"),
])
def test_bad_contract_cannot_pass(field, value):
    data = answer()
    data[field] = value
    assert smoke.check_answer(smoke.CASES[0], data)["status"] == "fail"


@pytest.mark.parametrize("field,value", [
    ("evidence", []), ("degraded_services", ["usgs_earthquakes"]),
    ("inputs_pending", ["tmd_nwp_daily"]), ("data_updated_at", None),
    ("text", "ขนาด 4.2 เวลาไม่ระบุ\nที่มา: USGS"),
])
def test_incomplete_data_needs_review(field, value):
    data = answer()
    data[field] = value
    assert smoke.check_answer(smoke.CASES[0], data)["status"] == "review"


def test_api_unavailable_is_blocked_and_does_not_ask_questions(monkeypatch):
    calls = []

    def unavailable(url, timeout, question=None):
        calls.append(url)
        raise OSError("API offline")

    monkeypatch.setattr(smoke, "request_json", unavailable)
    report = smoke.run("http://localhost:8002", 1)
    assert report["status"] == "blocked"
    assert calls == ["http://localhost:8002/ready"]


def test_http_error_for_one_question_does_not_skip_other_groups(monkeypatch):
    calls = []

    def request(url, timeout, question=None):
        if question is None:
            return {"status": "ready"}
        calls.append(question)
        raise OSError("HTTP 503")

    monkeypatch.setattr(smoke, "request_json", request)
    report = smoke.run("http://localhost:8002/", 1)
    assert report["status"] == "fail"
    assert len(report["cases"]) == len(calls) == 8
