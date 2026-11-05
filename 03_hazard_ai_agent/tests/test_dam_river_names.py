"""Dams named without "เขื่อน", and rivers named in the question."""
import pytest

from geo_integration import Area, Gazetteer
from hazard_agent import HazardAgent, understand
from test_agent import DAMS, GAZ, NOW, RIVERS, RiverTools

GAZ_TH = Gazetteer([*GAZ.areas,
                    Area("TH65", "province", "พิษณุโลก", "Phitsanulok", None, 16.8, 100.3, 10800),
                    Area("TH34", "province", "อุบลราชธานี", "Ubon Ratchathani", None, 15.2, 104.9, 15700)])
EXTRA = [
    {"id": "1", "code": "N.1", "name_th": "สะพานเอกาทศรถ", "river_th": "แม่น้ำน่าน", "province_code": "TH65", "province_th": "พิษณุโลก",
     "discharge_m3s": 300.0, "above_bank_m": -3.2, "status": "NORMAL", "agency": "RID", "time": "2026-10-19T17:00:00+07:00", "lat": 16.8, "lon": 100.3},
    {"id": "2", "code": "N.5A", "name_th": "บ้านท่าอิฐ", "river_th": "แม่น้ำน่าน", "province_code": "TH53", "province_th": "อุตรดิตถ์",
     "discharge_m3s": 250.0, "above_bank_m": -1.0, "status": "NORMAL", "agency": "RID", "time": "2026-10-19T17:00:00+07:00", "lat": 17.6, "lon": 100.1},
    {"id": "3", "code": "M.7", "name_th": "บ้านท่าบ่อ", "river_th": "แม่น้ำมูล", "province_code": "TH34", "province_th": "อุบลราชธานี",
     "discharge_m3s": 900.0, "above_bank_m": -0.4, "status": "NEAR_BANK", "agency": "RID", "time": "2026-10-19T17:00:00+07:00", "lat": 15.2, "lon": 104.9},
    {"id": "4", "code": "M.9", "name_th": "บ้านสะพานบ้านหลวง", "river_th": "แม่น้ำมูล", "province_code": "TH34", "province_th": "อุบลราชธานี",
     "discharge_m3s": 500.0, "above_bank_m": -2.5, "status": "NORMAL", "agency": "RID", "time": "2026-10-19T17:00:00+07:00", "lat": 15.3, "lon": 104.8},
]


class Tools(RiverTools):
    def get_rivers(self):
        return [*RIVERS, *EXTRA]


agent = HazardAgent(GAZ_TH, Tools())


def test_a_river_name_lists_only_that_rivers_stations_closest_to_the_bank_first():
    a = agent.ask("แม่น้ำมูลล้นตลิ่งไหม", NOW)
    assert a.intent == "river" and a.text.startswith("แม่น้ำมูล: สถานีวัดน้ำ 2 แห่ง")
    assert "M.7" in a.text and a.text.index("M.7") < a.text.index("M.9")
    assert "C.36" not in a.text  # the overflowing station on another river is not this river's answer


def test_a_river_and_a_province_give_that_rivers_stations_in_the_province():
    u = understand("แม่น้ำน่านที่พิษณุโลก", GAZ_TH, NOW)
    assert u.intent == "river"
    a = agent.ask("แม่น้ำน่านที่พิษณุโลก", NOW)
    assert a.text.startswith("แม่น้ำน่านในจ.พิษณุโลก: สถานีวัดน้ำ 1 แห่ง") and "N.1" in a.text and "N.5A" not in a.text


def test_a_river_with_no_station_in_the_province_says_so_and_lists_the_river():
    a = agent.ask("แม่น้ำมูลที่พิษณุโลก", NOW)
    assert "ไม่มีสถานีวัดน้ำบนแม่น้ำมูลในจ.พิษณุโลก" in a.text and "M.7" in a.text


def test_chao_phraya_alone_keeps_the_main_stem_overview():
    assert agent.ask("แม่น้ำเจ้าพระยาตอนนี้เป็นยังไง", NOW).text.startswith("แม่น้ำเจ้าพระยาและสาขา")


def test_a_flood_question_naming_a_river_and_a_place_stays_a_flood_question():
    assert understand("แม่น้ำน่านท่วมพิษณุโลกไหม", GAZ_TH, NOW).intent == "risk"


@pytest.mark.parametrize("question", ["หนองปลาไหล", "ป่าสักชลสิทธิ์ตอนนี้น้ำเท่าไหร่"])
def test_a_dam_name_without_the_word_dam_is_a_dam_question(question):
    a = agent.ask(question, NOW)
    name = next(d["name_th"] for d in DAMS if d["name_th"] in question)
    assert a.intent == "dam" and name in a.text


def test_a_dam_name_with_a_hazard_word_is_not_a_dam_question():
    assert agent.ask("ป่าสักชลสิทธิ์ฝนตกไหม", NOW).intent != "dam"
