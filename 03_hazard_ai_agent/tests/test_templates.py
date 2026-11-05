from datetime import datetime, timezone

import pytest

from geo_integration import Area, Gazetteer
from hazard_agent import HazardAgent, understand
from hazard_agent.agent import HAZARD_TH

NOW = datetime(2026, 11, 4, 2, tzinfo=timezone.utc)
GAZ = Gazetteer([
    Area("TH14", "province", "พระนครศรีอยุธยา", "Phra Nakhon Si Ayutthaya", None, 14.3, 100.5, 2500),
    Area("TH1412", "amphoe", "เสนา", "Sena", "TH14", 14.3, 100.4, 200),
])


class Tools:
    def __init__(self, sources=None):
        self.sources = sources if sources is not None else {}
        self.calls = []

    def get_sources(self):
        self.calls.append("get_sources")
        return self.sources

    def get_assessments(self, code):
        self.calls.append(code)
        return [{"hazard": "flood", "level": "WATCH", "sources": ["ThaiWater"]}]

    def get_all_assessments(self):
        self.calls.append("get_all_assessments")
        return [{"area_code": "TH1412", "hazard": "flood", "level": "WATCH"}]

    def get_official_warnings(self):
        self.calls.append("get_official_warnings")
        return []


def must_not_rewrite(question, facts):
    raise AssertionError("Template answers must not call the LLM")


def test_out_of_scope_lists_all_hazards_from_current_labels(monkeypatch):
    monkeypatch.setitem(HAZARD_TH, "future_hazard", "ภัยเพิ่มเติมทดสอบ")
    tools = Tools()
    answer = HazardAgent(GAZ, tools, explainer=must_not_rewrite).ask("ราคาทองวันนี้เท่าไหร่", NOW)
    assert answer.intent == "out_of_scope"
    assert "ภัยเพิ่มเติมทดสอบ" in answer.text
    assert "ความร้อนจัด" in answer.text and "ดินถล่ม" in answer.text and "พายุหมุนเขตร้อน" in answer.text
    assert tools.calls == []


@pytest.mark.parametrize("question", ["สวัสดี", "สวัสดีครับ", "สวัสดีค่ะ!", "หวัดดี", "Hello!", "hi"])
def test_greeting_gives_three_question_examples(question):
    tools = Tools()
    answer = HazardAgent(GAZ, tools, explainer=must_not_rewrite).ask(question, NOW)
    assert answer.intent == "greeting" and "สวัสดี" in answer.text
    examples = [line for line in answer.text.splitlines() if line.startswith("• ")]
    assert len(examples) == 3 and len(set(examples)) == 3
    assert answer.area is None and answer.evidence == [] and tools.calls == []


@pytest.mark.parametrize("question", [
    "ทันภัยใช้ข้อมูลจากไหน", "ข้อมูลมาจากไหน", "แหล่งข้อมูลของทันภัยคืออะไร", "What are your data sources?",
])
def test_source_answer_uses_tool_results_and_status(question):
    tools = Tools({"thaiwater_waterlevel": {"ok": True}, "nasa_firms": {"ok": False}, "new_provider": {}})
    answer = HazardAgent(GAZ, tools, explainer=must_not_rewrite).ask(question, NOW)
    assert answer.intent == "sources"
    assert answer.sources == []  # The provider list is already in the answer text.
    assert "ThaiWater" in answer.text and "NASA FIRMS" in answer.text and "new_provider" in answer.text
    assert "ไม่พร้อมใช้งาน" in next(line for line in answer.text.splitlines() if "NASA FIRMS" in line)
    assert "ยังไม่ทราบสถานะ" in next(line for line in answer.text.splitlines() if "new_provider" in line)
    assert "GISTDA" not in answer.text  # Never invent providers absent from the source tool.
    assert {e["source"] for e in answer.evidence} == {"thaiwater_waterlevel", "nasa_firms", "new_provider"}
    assert answer.tool_calls == ["get_sources()"] and tools.calls == ["get_sources"]


def test_no_sources_does_not_guess_provider_names():
    answer = HazardAgent(GAZ, Tools()).ask("ทันภัยใช้ข้อมูลจากไหน", NOW)
    assert answer.intent == "sources" and "ยังไม่มีข้อมูลแหล่งที่มา" in answer.text
    assert answer.sources == [] and answer.evidence == []


@pytest.mark.parametrize("question", ["สวัสดี อำเภอเสนาน้ำท่วมไหม", "hello, will it flood in Sena?"])
def test_greeting_does_not_override_a_hazard_question(question):
    tools = Tools()
    answer = HazardAgent(GAZ, tools).ask(question, NOW)
    assert answer.intent == "risk" and answer.area["code"] == "TH1412"
    assert "TH1412" in tools.calls


def test_emergency_takes_priority_over_source_question():
    tools = Tools()
    answer = HazardAgent(GAZ, tools).ask("ช่วยด้วย น้ำท่วม แหล่งข้อมูลอยู่ไหน", NOW)
    assert answer.intent == "emergency" and "1784" in answer.text and "1669" in answer.text
    assert tools.calls == []


def test_greeting_words_inside_unrelated_text_are_not_greetings():
    assert understand("highlight", GAZ, NOW).intent == "out_of_scope"


@pytest.mark.parametrize(("question", "intent"), [
    ("แหล่งข้อมูลน้ำท่วมอำเภอเสนา", "risk"),
    ("ใช้ข้อมูลจากดาวเทียม แล้วตอนนี้อำเภอเสนาน้ำท่วมไหม", "risk"),
    ("ใช้ข้อมูลจากดาวเทียม น้ำท่วมไหม", "clarify"),  # a hazard with no place: ask where
    ("Using data sources, will it flood in Sena?", "risk"),
])
def test_source_words_do_not_override_hazard_or_area_questions(question, intent):
    tools = Tools()
    answer = HazardAgent(GAZ, tools).ask(question, NOW)
    assert answer.intent == intent
    assert "get_sources" not in tools.calls
    if "เสนา" in question or "Sena" in question:
        assert answer.area["code"] == "TH1412" and "TH1412" in tools.calls
    else:
        assert answer.area is None and "get_all_assessments" not in tools.calls  # asks where, reads nothing


def test_source_words_do_not_override_preparation():
    tools = Tools()
    answer = HazardAgent(GAZ, tools).ask("แหล่งข้อมูลบอกว่ามีน้ำท่วม ต้องเตรียมตัวอย่างไร", NOW)
    assert answer.intent == "prepare" and "เตรียม" in answer.text
    assert "get_sources" not in tools.calls


@pytest.mark.parametrize("question, intent", [
    ("ใช้ข้อมูลจาก สสน. เขื่อนภูมิพลมีน้ำเท่าไหร่", "dam"),
    ("ใช้ข้อมูลจาก สสน. C.13 มีอัตราการไหลเท่าไหร่", "river"),
])
def test_source_words_do_not_override_dam_or_river(question, intent):
    assert understand(question, GAZ, NOW).intent == intent
