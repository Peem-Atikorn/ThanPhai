import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from geo_integration import Area, Gazetteer
from hazard_agent import HazardAgent, understand

NOW = datetime(2026, 11, 4, 3, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def gazetteer():
    fixture = Path(__file__).resolve().parents[2] / "05_geo_data_integration/tests/fixtures/question_areas.json"
    rows = json.loads(fixture.read_text(encoding="utf-8"))
    return Gazetteer([Area(*row, 13.9, 100.4, 100) for row in rows])


class Tools:
    def __init__(self):
        self.calls = []

    def get_assessments(self, code):
        self.calls.append(code)
        return [{"hazard": "flood", "level": "WATCH", "sources": ["fixture"]}]

    def get_all_assessments(self):
        self.calls.append("get_all_assessments()")
        return [{"area_code": "TH5001", "hazard": "fire", "level": "WATCH"},
                {"area_code": "TH5701", "hazard": "fire", "level": "NORMAL"}]

    def get_official_warnings(self):
        return []


@pytest.mark.parametrize(("question", "intent", "codes"), [
    ("ภาคเหนือมีไฟป่ากี่จุด", "overview", {"TH50", "TH57"}),
    ("สรุปสถานการณ์ภัยทั้งประเทศวันนี้", "overview", set()),
    ("อำเภอเมืองอันตรายไหม", "clarify", set()),
    ("เขื่อนภูมิพลเหลือน้ำกี่เปอร์เซ็นต์", "dam", set()),
    ("เขื่อนสิริกิติ์ปล่อยน้ำเท่าไหร่", "dam", set()),
])
def test_issue_examples_in_agent_understanding(gazetteer, question, intent, codes):
    u = understand(question, gazetteer, NOW)
    assert u.intent == intent and {a.code for a in u.areas} == codes


def test_generic_mueang_asks_for_province_before_assessment(gazetteer):
    tools = Tools()
    answer = HazardAgent(gazetteer, tools).ask("อำเภอเมืองอันตรายไหม", NOW)
    assert answer.intent == "clarify" and "จังหวัด" in answer.text
    assert answer.area is None and tools.calls == []


def test_mueang_with_province_assesses_the_city_district(gazetteer):
    tools = Tools()
    answer = HazardAgent(gazetteer, tools).ask("อำเภอเมือง จังหวัดเชียงใหม่น้ำท่วมไหม", NOW)
    assert answer.intent == "risk" and answer.area["code"] == "TH5001"
    assert tools.calls == ["TH5001"]


@pytest.mark.parametrize(("question", "area_code"), [
    ("สรุปน้ำท่วมอำเภอเสนา", "TH1412"),
    ("สรุปสถานการณ์จังหวัดเชียงใหม่", "TH50"),
    ("อำเภอเสนาฝนตกมากที่สุดเท่าไหร่", "TH1412"),
])
def test_named_area_takes_priority_over_national_intent_words(gazetteer, question, area_code):
    tools = Tools()
    answer = HazardAgent(gazetteer, tools).ask(question, NOW)

    assert answer.intent == "risk" and answer.area["code"] == area_code
    assert tools.calls == [area_code]


def test_future_national_question_asks_for_a_specific_area(gazetteer):
    tools = Tools()
    u = understand("พรุ่งนี้ฝนตกทั่วประเทศไหม", gazetteer, NOW)
    answer = HazardAgent(gazetteer, tools).ask("พรุ่งนี้ฝนตกทั่วประเทศไหม", NOW)

    assert u.future and u.intent == "risk"
    assert answer.intent == "clarify" and answer.area is None
    assert "ระบุพื้นที่" in answer.text or "ไม่พบชื่อพื้นที่" in answer.text
    assert tools.calls == []


def test_region_uses_aggregate_assessments(gazetteer):
    tools = Tools()
    u = understand("ภาคเหนือมีไฟป่ากี่จุด", gazetteer, NOW)
    assert u.regions == ["ภาคเหนือ"] and "ambiguous area" not in u.notes
    answer = HazardAgent(gazetteer, tools).ask("ภาคเหนือมีไฟป่ากี่จุด", NOW)
    assert answer.intent == "overview" and "ภาพรวมภาคเหนือ" in answer.text
    assert answer.area is None and answer.tool_calls == ["get_all_assessments()"]


def test_explicit_province_takes_priority_over_region(gazetteer):
    u = understand("ภาคเหนือ จังหวัดเชียงใหม่น้ำท่วมไหม", gazetteer, NOW)
    assert u.regions == [] and [a.code for a in u.areas] == ["TH50"]


def test_region_emergency_keeps_emergency_response(gazetteer):
    tools = Tools()
    answer = HazardAgent(gazetteer, tools).ask("ช่วยด้วย น้ำท่วมภาคเหนือ", NOW)
    assert answer.intent == "emergency" and tools.calls == []
    assert "1784" in answer.text and "1669" in answer.text
