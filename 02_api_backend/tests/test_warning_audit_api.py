from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from api_backend.hazard_service import HazardService
from api_backend.main import app


@pytest.fixture
def fake_svc():
    svc = HazardService.__new__(HazardService)
    svc.updated_at = datetime.now(timezone.utc)
    svc.source_status = {}
    svc._inputs_changed = False
    svc._jobs = {}
    return svc


def test_warnings_api_disabled_by_default(fake_svc, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    fake_svc.warnings = [
        {
            "title": "ประกาศเตือนภัย",
            "body": "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง",
            "hazards": ["rain_storm"],
            "by_region": [
                {
                    "from": "2026-11-05",
                    "to": "2026-11-07",
                    "level": "WATCH",
                    "hazards": ["rain_storm"],
                    "areas": ["ประเทศไทยตอนบน"],
                }
            ],
            "ai_summary": None,
            "ai_attribution": None,
        }
    ]

    import api_backend.main as main_mod

    async def fake_fresh():
        return fake_svc

    monkeypatch.setattr(main_mod, "fresh_service", fake_fresh)
    monkeypatch.setattr(HazardService, "inputs_pending", property(lambda self: []))

    client = TestClient(app)
    resp = client.get("/v1/warnings")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["warnings"]) == 1
    assert data["warnings"][0]["ai_summary"] is None


def test_hazard_service_audit_and_summarize_warning_with_qwen(fake_svc, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")

    import decision_engine.warning_explainer as we_mod

    valid_text = "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง"

    class FakeExplainer:
        def summarize(self, title, body, by_region):
            return we_mod.WarningSummaryResult(
                summary_text=valid_text,
                attribution="สรุปโดย AI จากประกาศกรมอุตุนิยมวิทยา · ดูประกาศฉบับจริง",
                valid=True,
            )

    class FakeAuditor:
        def audit(self, title, body, by_region):
            return we_mod.WarningAuditResult(matched=True, discrepancies=[])

    monkeypatch.setattr(we_mod, "WarningExplainer", FakeExplainer)
    monkeypatch.setattr(we_mod, "WarningAuditor", FakeAuditor)

    w_dict = {
        "title": "ประกาศเตือนภัย",
        "body": "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง",
        "by_region": [
            {
                "from": "2026-11-05",
                "to": "2026-11-07",
                "level": "WATCH",
                "hazards": ["rain_storm"],
                "areas": ["ประเทศไทยตอนบน"],
            }
        ],
    }

    ai_summary, ai_attrib = fake_svc._audit_and_summarize_warning(w_dict)
    assert ai_summary == valid_text
    assert "สรุปโดย AI จากประกาศกรมอุตุนิยมวิทยา" in ai_attrib


def test_hazard_service_audit_and_summarize_warning_fallback(fake_svc, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")

    import decision_engine.warning_explainer as we_mod

    class FakeExplainer:
        def summarize(self, title, body, by_region):
            return None  # Validation failed

    class FakeAuditor:
        def audit(self, title, body, by_region):
            return we_mod.WarningAuditResult(matched=False, discrepancies=[])

    monkeypatch.setattr(we_mod, "WarningExplainer", FakeExplainer)
    monkeypatch.setattr(we_mod, "WarningAuditor", FakeAuditor)

    w_dict = {
        "title": "ประกาศเตือนภัย",
        "body": "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง",
        "by_region": [],
    }

    ai_summary, ai_attrib = fake_svc._audit_and_summarize_warning(w_dict)
    assert ai_summary is None
    assert ai_attrib is None
