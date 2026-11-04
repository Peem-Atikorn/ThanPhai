import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from decision_engine.warning_explainer import (
    WarningAuditor,
    WarningExplainer,
    WarningAuditResult,
    WarningSummaryResult,
    validate_warning_summary,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "05_geo_data_integration"
    / "tests"
    / "fixtures"
    / "tmd_bulletin_5.json"
)


@pytest.fixture
def bulletin_5_data():
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    by_region = [
        {
            "from": "2026-11-05",
            "to": "2026-11-07",
            "level": "WATCH",
            "hazards": ["rain_storm"],
            "areas": ["ประเทศไทยตอนบน"],
        },
        {
            "from": "2026-11-07",
            "to": "2026-11-09",
            "level": "WARNING",
            "hazards": ["rain_storm", "flood"],
            "areas": ["ภาคใต้"],
        },
    ]
    return {
        "title": raw["title"],
        "body": raw["body"],
        "issue_no": raw.get("issue_no", "5"),
        "by_region": by_region,
    }


def test_valid_warning_summary_passes(bulletin_5_data):
    summary = (
        "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีสภาพอากาศแปรปรวน ฝนฟ้าคะนอง และมีฝนตกหนักบางแห่ง "
        "ส่วนช่วงวันที่ 7–9 พ.ย. ภาคใต้จะมีฝนเพิ่มขึ้นและมีฝนตกหนักหลายพื้นที่ และมีฝนตกหนักมากบางแห่ง "
        "ระวังอันตรายจากฝนตกสะสม ซึ่งอาจทำให้เกิดน้ำท่วมฉับพลันและน้ำป่าไหลหลาก"
    )
    problems = validate_warning_summary(
        summary=summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert problems == []


def test_validate_catches_increased_word_strength(bulletin_5_data):
    # Rule 3: ภาคเหนืออยู่ในประเทศไทยตอนบนซึ่งมีแค่ "ฝนตกหนักบางแห่ง" ห้ามเพิ่มเป็น "หลายพื้นที่"
    bad_summary = (
        "ในช่วงวันที่ 5–7 พ.ย. ภาคเหนือฝนตกหนักหลายพื้นที่ และลมกระโชกแรง "
        "ส่วนภาคใต้ช่วง 7–9 พ.ย. มีฝนตกหนักและระวังน้ำท่วมฉับพลัน"
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert any("word strength" in p.lower() or "ความแรง" in p for p in problems)


def test_validate_catches_wrong_dates(bulletin_5_data):
    # Rule 2: ภาคใต้ระบุวันที่ 5–9 พ.ย. (ที่ถูกต้องคือ 7–9 พ.ย.)
    bad_summary = (
        "ประเทศไทยตอนบนมีฝนฟ้าคะนอง 5-7 พ.ย. "
        "ส่วนภาคใต้ 5–9 พ.ย. มีฝนตกหนักและเสี่ยงน้ำท่วมฉับพลัน"
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert any("date" in p.lower() or "วัน" in p for p in problems)


def test_validate_catches_hazard_swapped_to_wrong_region(bulletin_5_data):
    # Rule 2: น้ำท่วมเกิดเฉพาะภาคใต้ ห้ามเอาไปใส่ภาคเหนือ
    bad_summary = (
        "ในช่วงวันที่ 5–7 พ.ย. น้ำท่วมภาคเหนือ และมีฝนฟ้าคะนอง "
        "ส่วนภาคใต้ช่วงวันที่ 7–9 พ.ย. มีฝนตกหนัก"
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert any("hazard" in p.lower() or "ภัย" in p or "น้ำท่วม" in p for p in problems)


def test_validate_catches_unconfirmed_numbers(bulletin_5_data):
    # Rule 4: มีตัวเลข 200 และ 4 ซึ่งไม่มีในประกาศต้นฉบับ
    bad_summary = (
        "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง ปริมาณฝน 200 มม. "
        "ส่วนช่วง 7–9 พ.ย. ภาคใต้มีคลื่นสูง 4 เมตร"
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert any("number" in p.lower() or "ตัวเลข" in p for p in problems)


def test_validate_catches_unauthorized_advice(bulletin_5_data):
    # Rule 5: แต่งคำแนะนำ "ให้อพยพไปยังศูนย์พักพิง" ซึ่งไม่มีในประกาศ
    bad_summary = (
        "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง "
        "ส่วนช่วงวันที่ 7–9 พ.ย. ภาคใต้มีฝนตกหนัก ขอให้ประชาชนเตรียมอพยพไปยังศูนย์พักพิง"
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert any("advice" in p.lower() or "คำแนะนำ" in p or "อพยพ" in p for p in problems)


def test_validate_catches_tampered_alert_levels(bulletin_5_data):
    # Rule 1: ห้ามแต่งระดับวิกฤต หรือเปลี่ยนระดับเอง
    bad_summary = (
        "กรมอุตุนิยมวิทยาประกาศยกระดับเตือนภัยเป็นระดับวิกฤต ในพื้นที่ภาคใต้ช่วง 7–9 พ.ย."
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert any("level" in p.lower() or "ระดับ" in p for p in problems)


def test_validate_catches_wrapped_quotation_marks(bulletin_5_data):
    # Rule 7: ห้ามใส่เครื่องหมายคำพูดครอบข้อความที่เรียบเรียงเอง
    bad_summary = (
        "\"ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง "
        "ส่วนช่วงวันที่ 7–9 พ.ย. ภาคใต้มีฝนตกหนักมากบางแห่ง\""
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert any("quote" in p.lower() or "เครื่องหมายคำพูด" in p for p in problems)


def test_warning_auditor_matches_correct_extraction(bulletin_5_data):
    # Mock Qwen returning accurate JSON
    mock_json = json.dumps({
        "items": [
            {
                "areas": ["ประเทศไทยตอนบน"],
                "from": "2026-11-05",
                "to": "2026-11-07",
                "hazards": ["rain_storm"],
            },
            {
                "areas": ["ภาคใต้"],
                "from": "2026-11-07",
                "to": "2026-11-09",
                "hazards": ["rain_storm", "flood"],
            },
        ]
    })
    auditor = WarningAuditor(requester=lambda prompt: mock_json)
    res = auditor.audit(
        title=bulletin_5_data["title"],
        body=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert isinstance(res, WarningAuditResult)
    assert res.matched is True
    assert res.discrepancies == []


def test_warning_auditor_detects_and_logs_discrepancy(bulletin_5_data, caplog):
    # Mock Qwen returning mismatched items (flood in north, dates 5-9 in south)
    mock_json = json.dumps({
        "items": [
            {
                "areas": ["ภาคเหนือ"],
                "from": "2026-11-05",
                "to": "2026-11-07",
                "hazards": ["rain_storm", "flood"],
            },
            {
                "areas": ["ภาคใต้"],
                "from": "2026-11-05",
                "to": "2026-11-09",
                "hazards": ["rain_storm"],
            },
        ]
    })
    auditor = WarningAuditor(requester=lambda prompt: mock_json)
    res = auditor.audit(
        title=bulletin_5_data["title"],
        body=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert res.matched is False
    assert len(res.discrepancies) > 0
    # Must log discrepancy
    assert any("discrepancy" in record.message.lower() for record in caplog.records)


def test_warning_explainer_summarize_success(bulletin_5_data):
    valid_text = (
        "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีสภาพอากาศแปรปรวน ฝนฟ้าคะนอง และมีฝนตกหนักบางแห่ง "
        "ส่วนช่วงวันที่ 7–9 พ.ย. ภาคใต้จะมีฝนเพิ่มขึ้นและมีฝนตกหนักหลายพื้นที่ และมีฝนตกหนักมากบางแห่ง "
        "ระวังอันตรายจากฝนตกสะสม ซึ่งอาจทำให้เกิดน้ำท่วมฉับพลันและน้ำป่าไหลหลาก"
    )
    explainer = WarningExplainer(requester=lambda prompt: valid_text)
    res = explainer.summarize(
        title=bulletin_5_data["title"],
        body=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert res is not None
    assert isinstance(res, WarningSummaryResult)
    assert res.valid is True
    assert res.summary_text == valid_text
    assert "สรุปโดย AI จากประกาศกรมอุตุนิยมวิทยา" in res.attribution


def test_warning_explainer_fallback_on_validation_failure(bulletin_5_data):
    # When Qwen outputs invalid text (e.g. wrong dates), summarize() must return None (safe fallback)
    bad_text = "ภาคใต้มีฝนตกหนัก 5–9 พ.ย. ระวังน้ำท่วม"
    explainer = WarningExplainer(requester=lambda prompt: bad_text)
    res = explainer.summarize(
        title=bulletin_5_data["title"],
        body=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert res is None


def test_warning_explainer_fallback_on_network_error(bulletin_5_data):
    def error_requester(prompt):
        raise ConnectionError("Ollama host unreachable")

    explainer = WarningExplainer(requester=error_requester)
    res = explainer.summarize(
        title=bulletin_5_data["title"],
        body=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert res is None


def test_warning_explainer_disabled_by_default_without_qwen(bulletin_5_data, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    explainer = WarningExplainer()  # no requester injected
    res = explainer.summarize(
        title=bulletin_5_data["title"],
        body=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert res is None
