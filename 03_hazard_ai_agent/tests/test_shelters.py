"""Shelter questions: answered only from DDPM's list, never rewritten, always with the check-before-you-go line."""

from datetime import datetime, timedelta, timezone

import pytest

from geo_integration import Area, Gazetteer
from hazard_agent import HazardAgent, understand
from hazard_agent.agent import SHELTER_CHECK, SHELTER_ROUTE_HINT

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=timezone(timedelta(hours=7)))
GAZ = Gazetteer([
    Area("TH14", "province", "พระนครศรีอยุธยา", "Phra Nakhon Si Ayutthaya", None, 14.3, 100.5, 2500),
    Area("TH1406", "amphoe", "เสนา", "Sena", "TH14", 14.3, 100.4, 200),
    Area("TH140602", "tambon", "บ้านแพน", "Ban Phaen", "TH1406", 14.3, 100.4, 10),
    Area("TH1401", "amphoe", "พระนครศรีอยุธยา", "Phra Nakhon Si Ayutthaya", "TH14", 14.35, 100.57, 120),
    Area("TH10", "province", "กรุงเทพมหานคร", "Bangkok", None, 13.75, 100.5, 1500),
])
SHELTERS = [
    {"name": "โรงเรียนวัดเสนา", "tambon": "เสนา", "amphoe": "เสนา", "amphoe_code": "TH1406", "capacity": 300, "approximate": False},
    {"name": "วัดบ้านแพน", "tambon": "บ้านแพน", "amphoe": "เสนา", "amphoe_code": "TH1406", "capacity": 80, "approximate": True},
    {"name": "ศาลากลางจังหวัด", "tambon": "ประตูชัย", "amphoe": "พระนครศรีอยุธยา", "amphoe_code": "TH1401", "capacity": 1000,
     "approximate": False},
]


class Tools:
    def get_shelters(self, area_code=None):
        if not area_code:
            return SHELTERS
        area = GAZ.by_code[area_code]
        prefix = area.parent_code if area.level == "tambon" else area_code
        return [s for s in SHELTERS if s["amphoe_code"].startswith(prefix)]

    def shelter_source(self):
        return {"source": "กรมป้องกันและบรรเทาสาธารณภัย", "source_url": "https://data.go.th/x", "updated": "2024-08-09"}


def rewrite_everything(question, facts):
    return "ไปวัดใกล้บ้านได้เลย"  # an LLM that invents a place must never reach the answer


agent = HazardAgent(GAZ, Tools(), explainer=rewrite_everything)


@pytest.mark.parametrize("question", ["ศูนย์พักพิงอำเภอเสนาอยู่ที่ไหน", "ถ้าน้ำท่วมเสนาต้องอพยพไปที่ไหน",
                                      "หนีน้ำไปไหนดีที่อำเภอเสนา", "shelters in Sena", "ที่พักพิงชั่วคราวเสนา"])
def test_shelter_words_are_shelter_questions(question):
    assert understand(question, GAZ, NOW).intent == "shelter"


def test_how_to_evacuate_stays_a_preparation_question():
    assert understand("น้ำท่วมสูงมาก ต้องอพยพอย่างไร", GAZ, NOW).intent == "prepare"


def test_district_lists_its_shelters_biggest_first_and_is_never_rewritten():
    answer = agent.ask("ศูนย์พักพิงอำเภอเสนาอยู่ที่ไหน", NOW)
    assert answer.intent == "shelter" and not answer.llm_used
    lines = answer.text.splitlines()
    assert "2 แห่ง" in lines[0] and "380 คน" in lines[0]
    assert lines[1].startswith("• โรงเรียนวัดเสนา") and "300 คน" in lines[1]
    assert "วัดบ้านแพน" in lines[2] and "ตำแหน่งโดยประมาณ" in lines[2]
    assert SHELTER_CHECK in lines and SHELTER_ROUTE_HINT in lines and lines[-1].startswith("ที่มา: กรมป้องกันและบรรเทาสาธารณภัย")
    assert "9 ส.ค. 2567" in lines[-1] and answer.area["code"] == "TH1406"


def test_sub_district_gets_its_district_list_and_says_so():
    text = agent.ask("ศูนย์พักพิงตำบลบ้านแพน", NOW).text
    assert text.startswith("ต.บ้านแพน") and "อ.เสนา" in text.splitlines()[0] and "2 แห่ง" in text


def test_province_names_its_busiest_districts():
    text = agent.ask("ศูนย์พักพิงจังหวัดพระนครศรีอยุธยามีกี่แห่ง", NOW).text
    assert "3 แห่ง" in text and "อำเภอที่มีศูนย์มากที่สุด: เสนา 2" in text
    assert text.index("ศาลากลางจังหวัด") < text.index("โรงเรียนวัดเสนา")


def test_open_area_is_used_when_the_question_names_none():
    assert "อ.เสนา" in agent.ask("มีศูนย์อพยพไหม", NOW, area_code="TH1406").text


def test_no_area_asks_where_and_bangkok_points_to_the_city():
    asked = agent.ask("ศูนย์พักพิงอยู่ที่ไหน", NOW)
    assert "3 แห่งทั่วประเทศ" in asked.text and "หาศูนย์ใกล้ฉัน" in asked.text and asked.area is None
    bangkok = agent.ask("ศูนย์พักพิงกรุงเทพ", NOW).text
    assert "ยังไม่มีกรุงเทพฯ" in bangkok and "1555" in bangkok


def test_without_the_list_it_says_so_and_names_no_place():
    class NoList:
        def shelter_source(self):
            return None
    answer = HazardAgent(GAZ, NoList()).ask("ศูนย์พักพิงอำเภอเสนา", NOW)
    assert "ยังไม่มีรายชื่อศูนย์พักพิง" in answer.text and SHELTER_CHECK in answer.text
