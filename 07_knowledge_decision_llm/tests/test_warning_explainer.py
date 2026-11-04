import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from decision_engine.warning_explainer import (
    WarningAuditor,
    WarningExplainer,
    WarningAuditResult,
    WarningSummaryResult,
    load_strong_words,
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
    rain_days = [
        {
            "day": "2026-11-05",
            "very_heavy": False,
            "provinces": ["ชุมพร", "สุราษฎร์ธานี", "นครศรีธรรมราช", "สงขลา", "ปัตตานี", "นราธิวาส"],
        },
        {
            "day": "2026-11-06",
            "very_heavy": False,
            "provinces": ["เพชรบุรี", "ประจวบคีรีขันธ์", "สุราษฎร์ธานี", "นครศรีธรรมราช"],
        },
        {
            "day": "2026-11-07",
            "very_heavy": False,
            "provinces": ["ชุมพร", "สุราษฎร์ธานี", "นครศรีธรรมราช", "พัทลุง", "สงขลา", "ปัตตานี", "ยะลา", "นราธิวาส"],
        },
        {
            "day": "2026-11-09",
            "very_heavy": False,
            "provinces": ["เพชรบุรี", "ประจวบคีรีขันธ์", "ชุมพร", "ยะลา", "ระนอง", "พังงา", "ภูเก็ต", "กระบี่", "ตรัง", "สตูล"],
        },
    ]
    return {
        "title": raw["title"],
        "body": raw["body"],
        "issue_no": raw.get("issue_no", "5"),
        "by_region": by_region,
        "rain_days": rain_days,
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
        rain_days=bulletin_5_data["rain_days"],
    )
    assert problems == []


def test_validate_catches_dropped_bang_haeng(bulletin_5_data):
    # Review Case 1: ตัดคำว่า "บางแห่ง" ทิ้ง ทำให้ความแรงเพิ่มขึ้น
    bad_summary = (
        "ประเทศไทยตอนบนมีฝนตกหนักวันที่ 5-7 พ.ย. ส่วนภาคใต้มีฝนตกหนักถึงหนักมากวันที่ 7-9 พ.ย."
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
        rain_days=bulletin_5_data["rain_days"],
    )
    assert any("บางแห่ง" in p or "ความแรง" in p for p in problems)


def test_validate_catches_dropped_bang_haeng_in_south(bulletin_5_data):
    # Review 2 Case 1: ตอนบนมีบางแห่งถูกต้อง แต่ภาคใต้มี "หนักมาก" แล้วตัด "บางแห่ง" ออก
    bad_summary = (
        "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีสภาพอากาศแปรปรวน และมีฝนตกหนักบางแห่ง "
        "ส่วนภาคใต้มีฝนตกหนักถึงหนักมากวันที่ 7-9 พ.ย."
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
        rain_days=bulletin_5_data["rain_days"],
    )
    assert any("บางแห่ง" in p or "ความแรง" in p for p in problems)


def test_validate_catches_fon_nak_dropping_bang_haeng(bulletin_5_data):
    # Review 2 Case 2: เขียน "ฝนหนัก" แทน "ฝนตกหนัก" แล้วตัด "บางแห่ง"
    bad_summary = (
        "ประเทศไทยตอนบนมีฝนหนักวันที่ 5-7 พ.ย. "
        "ส่วนภาคใต้มีฝนตกหนักถึงหนักมากบางแห่งวันที่ 7-9 พ.ย."
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
        rain_days=bulletin_5_data["rain_days"],
    )
    assert any("บางแห่ง" in p or "ความแรง" in p for p in problems)


def test_validate_catches_hazard_clause_inheriting_region_without_flood(bulletin_5_data):
    # Review 2 Case 3: ประโยคที่ไม่มีภาคและไม่มีวันที่ (ขึ้นบรรทัดใหม่) คนอ่านจะเข้าใจว่าเป็นของภาคก่อนหน้า
    bad_summary = (
        "ประเทศไทยตอนบนมีฝนตกหนักบางแห่งวันที่ 5-7 พ.ย.\n"
        "อาจเกิดน้ำท่วมฉับพลันและน้ำป่าไหลหลาก\n"
        "ส่วนภาคใต้ช่วงวันที่ 7–9 พ.ย. มีฝนตกหนักบางแห่ง"
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
        rain_days=bulletin_5_data["rain_days"],
    )
    assert any("flood" in p or "น้ำท่วม" in p or "ภัย" in p for p in problems)


def test_validate_catches_province_without_date_against_provinces_by_hazard(bulletin_5_data):
    # Review 2 Case 4: จังหวัดภูเก็ตอาจเกิดน้ำท่วมฉับพลันและน้ำป่าไหลหลาก (ไม่มีวันที่)
    # แต่ประกาศเตือนน้ำท่วมเฉพาะนครศรีธรรมราชกับสงขลา
    bad_summary = (
        "ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง 5-7 พ.ย. "
        "ส่วนจังหวัดภูเก็ตอาจเกิดน้ำท่วมฉับพลันและน้ำป่าไหลหลาก"
    )
    provinces_by_hazard = {"flood": ["TH80", "TH90"]}  # Only Nakhon Si Thammarat & Songkhla
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
        rain_days=bulletin_5_data["rain_days"],
        provinces_by_hazard=provinces_by_hazard,
    )
    assert any("flood" in p or "ภูเก็ต" in p or "ภัย" in p for p in problems)


def test_validate_catches_very_heavy_escalation_in_rain_days(bulletin_5_data):
    # Review 2 Case 5: ภูเก็ตในตารางวันที่ 9 พ.ย. อยู่ในแถวฝนตกหนัก (very_heavy: false)
    # แต่สรุปเขียน "ฝนตกหนักถึงหนักมาก"
    bad_summary = (
        "ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง 5-7 พ.ย. "
        "ส่วนจังหวัดภูเก็ตฝนตกหนักถึงหนักมากวันที่ 9 พ.ย."
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
        rain_days=bulletin_5_data["rain_days"],
    )
    assert any("หนักมาก" in p or "very_heavy" in p or "ความแรง" in p for p in problems)


def test_validate_does_not_falsely_match_ambiguous_names(bulletin_5_data):
    # "ควรติดตามประกาศเลย" ต้องไม่นับ "เลย" เป็นจังหวัดเลย
    valid_summary = (
        "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง "
        "ส่วนภาคใต้ช่วงวันที่ 7–9 พ.ย. มีฝนตกหนักบางแห่ง ประชาชนควรติดตามประกาศเลย"
    )
    problems = validate_warning_summary(
        summary=valid_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
        rain_days=bulletin_5_data["rain_days"],
    )
    assert problems == []


def test_validate_catches_invalid_province_and_date(bulletin_5_data):
    # Review Case 2: ภูเก็ตระบุวันที่ 5 พ.ย. ซึ่งตารางฝนระบุวันที่ 9 พ.ย. และภาคใต้ช่วง 5–7 ไม่มีน้ำท่วม
    bad_summary = (
        "ประเทศไทยตอนบนมีฝนตกหนักบางแห่ง 5-7 พ.ย. "
        "ส่วนจังหวัดภูเก็ตอาจเกิดน้ำท่วมฉับพลันวันที่ 5 พ.ย."
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
        rain_days=bulletin_5_data["rain_days"],
    )
    assert any("ภูเก็ต" in p or "date" in p.lower() or "วัน" in p for p in problems)


def test_validate_catches_dropped_region(bulletin_5_data):
    # Review Case 3: ตัดภาคใต้ทิ้งทั้งหมด
    bad_summary = (
        "ช่วงวันที่ 5–7 พ.ย. ประเทศไทยตอนบนมีสภาพอากาศแปรปรวน ฝนฟ้าคะนอง และมีฝนตกหนักบางแห่ง"
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
        rain_days=bulletin_5_data["rain_days"],
    )
    assert any("Missing required region" in p for p in problems)


def test_validate_catches_orphan_statement_wrong_hazard(bulletin_5_data):
    # Review Case 4: ใส่ภัยโดยไม่ระบุภาค (5-7 พ.ย. ตอนบนไม่มีน้ำท่วม)
    bad_summary = (
        "ฝนตกหนักบางแห่งช่วง 5-7 พ.ย. อาจเกิดน้ำท่วมฉับพลันและน้ำป่าไหลหลาก "
        "ส่วนภาคใต้ช่วง 7-9 พ.ย. มีฝนตกหนักบางแห่ง"
    )
    problems = validate_warning_summary(
        summary=bad_summary,
        original_text=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
        rain_days=bulletin_5_data["rain_days"],
    )
    assert any("Hazard" in p or "not allowed" in p or "ภัย" in p for p in problems)


def test_validate_cross_month_dates():
    # Review Case 5: ช่วงที่ข้ามเดือน (30 พ.ย. ถึง 2 ธ.ค.)
    cross_month_region = [
        {
            "from": "2026-11-30",
            "to": "2026-12-02",
            "level": "WATCH",
            "hazards": ["rain_storm"],
            "areas": ["ภาคเหนือ"],
        }
    ]
    orig_text = "ในช่วงวันที่ 30 พฤศจิกายน ถึง 2 ธันวาคม 2569 ภาคเหนือมีฝนตกหนักบางแห่ง"
    valid_summary = "ภาคเหนือมีฝนตกหนักบางแห่งช่วง 30 พ.ย. ถึง 2 ธ.ค."
    assert validate_warning_summary(valid_summary, orig_text, cross_month_region) == []

    # If mentions day 5 (outside range 30, 1, 2)
    bad_summary = "ภาคเหนือมีฝนตกหนักบางแห่งวันที่ 5 ธ.ค."
    problems = validate_warning_summary(bad_summary, orig_text, cross_month_region)
    assert any("Date mismatch" in p or "วัน" in p for p in problems)


def test_load_strong_words_matches_rules_yaml():
    # Review Case 7: strong_words อ่านมาจาก rules.yaml ตรงกัน
    words = load_strong_words()
    assert "หนักมาก" in words
    assert "หลายพื้นที่" in words
    assert "น้ำท่วมฉับพลัน" in words
    assert "น้ำป่าไหลหลาก" in words


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
        "ส่วนภาคใต้ช่วงวันที่ 7–9 พ.ย. มีฝนตกหนักมากบางแห่ง"
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
        rain_days=bulletin_5_data["rain_days"],
    )
    assert isinstance(res, WarningAuditResult)
    assert res.matched is True
    assert res.discrepancies == []


def test_warning_auditor_detects_and_logs_discrepancy(bulletin_5_data, caplog):
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
        rain_days=bulletin_5_data["rain_days"],
    )
    assert res is not None
    assert isinstance(res, WarningSummaryResult)
    assert res.valid is True
    assert res.summary_text == valid_text
    assert "สรุปโดย AI จากประกาศกรมอุตุนิยมวิทยา" in res.attribution


def test_warning_explainer_fallback_on_validation_failure(bulletin_5_data):
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
    explainer = WarningExplainer()
    res = explainer.summarize(
        title=bulletin_5_data["title"],
        body=bulletin_5_data["body"],
        by_region=bulletin_5_data["by_region"],
    )
    assert res is None
