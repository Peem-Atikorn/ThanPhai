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
    svc._warning_ai_cache = {}
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
    passed_args = {}

    class FakeExplainer:
        def summarize(self, title, body, by_region, rain_days=None, **kwargs):
            passed_args["explainer_rain_days"] = rain_days
            passed_args["explainer_provinces_by_hazard"] = kwargs.get("provinces_by_hazard")
            return we_mod.WarningSummaryResult(
                summary_text=valid_text,
                attribution="สรุปโดย AI จากประกาศกรมอุตุนิยมวิทยา · ดูประกาศฉบับจริง",
                valid=True,
            )

    class FakeAuditor:
        def audit(self, title, body, by_region, rain_days=None, **kwargs):
            passed_args["auditor_rain_days"] = rain_days
            passed_args["auditor_provinces_by_hazard"] = kwargs.get("provinces_by_hazard")
            return we_mod.WarningAuditResult(matched=True, discrepancies=[])

    monkeypatch.setattr(we_mod, "WarningExplainer", FakeExplainer)
    monkeypatch.setattr(we_mod, "WarningAuditor", FakeAuditor)

    rain_days_sample = [{"date": "2026-11-05", "provinces": ["เชียงใหม่"]}]
    provinces_by_hazard = {"flood": ["TH80"]}
    w_dict = {
        "issue_no": "288/2569",
        "announced_at": "2026-11-04T05:00:00",
        "title": "ประกาศเตือนภัย",
        "body": "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง",
        "rain_days": rain_days_sample,
        "provinces_by_hazard": provinces_by_hazard,
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
    assert passed_args["auditor_rain_days"] == rain_days_sample
    assert passed_args["explainer_rain_days"] == rain_days_sample
    assert passed_args["explainer_provinces_by_hazard"] == provinces_by_hazard


def test_hazard_service_audit_and_summarize_warning_caching(fake_svc, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")

    import decision_engine.warning_explainer as we_mod

    call_counts = {"auditor": 0, "explainer": 0}

    class FakeExplainer:
        def summarize(self, title, body, by_region, rain_days=None, **kwargs):
            call_counts["explainer"] += 1
            return we_mod.WarningSummaryResult(
                summary_text="สรุปประกาศ",
                attribution="สรุปโดย AI",
                valid=True,
            )

    class FakeAuditor:
        def audit(self, title, body, by_region, rain_days=None, **kwargs):
            call_counts["auditor"] += 1
            return we_mod.WarningAuditResult(matched=True, discrepancies=[])

    monkeypatch.setattr(we_mod, "WarningExplainer", FakeExplainer)
    monkeypatch.setattr(we_mod, "WarningAuditor", FakeAuditor)

    w_dict = {
        "issue_no": "288/2569",
        "announced_at": "2026-11-04T05:00:00",
        "title": "ประกาศเตือนภัย",
        "body": "เนื้อหา",
        "by_region": [],
    }

    # First call: executes auditor & explainer
    res1 = fake_svc._audit_and_summarize_warning(w_dict)
    assert res1 == ("สรุปประกาศ", "สรุปโดย AI")
    assert call_counts["auditor"] == 1
    assert call_counts["explainer"] == 1

    # Second call with same issue_no and announced_at: returned from cache, no re-execution
    res2 = fake_svc._audit_and_summarize_warning(w_dict)
    assert res2 == ("สรุปประกาศ", "สรุปโดย AI")
    assert call_counts["auditor"] == 1
    assert call_counts["explainer"] == 1

    # Third call with new issue_no: cache miss, executes again
    w_dict2 = dict(w_dict, issue_no="289/2569")
    res3 = fake_svc._audit_and_summarize_warning(w_dict2)
    assert res3 == ("สรุปประกาศ", "สรุปโดย AI")
    assert call_counts["auditor"] == 2
    assert call_counts["explainer"] == 2


def test_hazard_service_caching_does_not_cache_timeouts_or_exceptions(fake_svc, monkeypatch):
    # Review 2 Case 6: ถ้าล้มเหลวจาก exception/timeout ต้องไม่ cache ไว้ถาวร ให้รอบถัดไปลองใหม่ได้
    monkeypatch.setenv("LLM_PROVIDER", "qwen")

    import decision_engine.warning_explainer as we_mod

    attempts = {"count": 0}

    class FlakyExplainer:
        def __init__(self):
            self.validation_failed = False

        def summarize(self, title, body, by_region, rain_days=None, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                # First attempt times out -> returns None with validation_failed=False
                self.validation_failed = False
                return None
            # Second attempt succeeds
            self.validation_failed = False
            return we_mod.WarningSummaryResult(
                summary_text="สรุปประกาศสำเร็จ",
                attribution="สรุปโดย AI",
                valid=True,
            )

    class FakeAuditor:
        def audit(self, title, body, by_region, rain_days=None, **kwargs):
            return we_mod.WarningAuditResult(matched=True, discrepancies=[])

    monkeypatch.setattr(we_mod, "WarningExplainer", FlakyExplainer)
    monkeypatch.setattr(we_mod, "WarningAuditor", FakeAuditor)

    w_dict = {
        "issue_no": "288/2569",
        "announced_at": "2026-11-04T05:00:00",
        "title": "ประกาศเตือนภัย",
        "body": "เนื้อหา",
        "by_region": [],
    }

    # Attempt 1: times out -> returns (None, None) and DOES NOT CACHE
    res1 = fake_svc._audit_and_summarize_warning(w_dict)
    assert res1 == (None, None)
    assert attempts["count"] == 1

    # Attempt 2: retry on refresh -> succeeds because previous failure was not cached
    res2 = fake_svc._audit_and_summarize_warning(w_dict)
    assert res2 == ("สรุปประกาศสำเร็จ", "สรุปโดย AI")
    assert attempts["count"] == 2


def test_hazard_service_caching_caches_validation_rejection(fake_svc, monkeypatch):
    # Review 2 Case 6: ถ้า Qwen ตอบแล้วแต่ไม่ผ่านการตรวจ (hallucination) ต้อง cache (None, None) ไว้
    monkeypatch.setenv("LLM_PROVIDER", "qwen")

    import decision_engine.warning_explainer as we_mod

    attempts = {"count": 0}

    class RejectExplainer:
        def __init__(self):
            self.validation_failed = False

        def summarize(self, title, body, by_region, rain_days=None, **kwargs):
            attempts["count"] += 1
            self.validation_failed = True
            return None

    class FakeAuditor:
        def audit(self, title, body, by_region, rain_days=None, **kwargs):
            return we_mod.WarningAuditResult(matched=True, discrepancies=[])

    monkeypatch.setattr(we_mod, "WarningExplainer", RejectExplainer)
    monkeypatch.setattr(we_mod, "WarningAuditor", FakeAuditor)

    w_dict = {
        "issue_no": "288/2569",
        "announced_at": "2026-11-04T05:00:00",
        "title": "ประกาศเตือนภัย",
        "body": "เนื้อหา",
        "by_region": [],
    }

    # Attempt 1: validation failed -> returns (None, None) and caches
    res1 = fake_svc._audit_and_summarize_warning(w_dict)
    assert res1 == (None, None)
    assert attempts["count"] == 1

    # Attempt 2: returns from cache without calling explainer again
    res2 = fake_svc._audit_and_summarize_warning(w_dict)
    assert res2 == (None, None)
    assert attempts["count"] == 1  # Not called again!


def test_hazard_service_audit_and_summarize_warning_fallback(fake_svc, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")

    import decision_engine.warning_explainer as we_mod

    class FakeExplainer:
        def summarize(self, title, body, by_region, rain_days=None, **kwargs):
            return we_mod.WarningSummaryResult(summary_text="invalid", valid=False, error=None)

    class FakeAuditor:
        def audit(self, title, body, by_region, rain_days=None, **kwargs):
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
