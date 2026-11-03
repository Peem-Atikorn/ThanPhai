from hazard_agent import HazardAgent, understand
from hazard_agent.agent import HazardTools

from test_area_understanding import NOW, gazetteer


class NationalTools:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_all_assessments(self):
        self.calls.append("all")
        return self.rows

    def get_assessments(self, code):
        raise AssertionError("national questions must use cached all-area decisions")

    def get_official_warnings(self):
        return []


def test_overview_counts_each_district_in_its_own_level(gazetteer):
    tools = NationalTools([
        {"area_code": "TH5001", "hazard": "flood", "level": "WARNING"},
        {"area_code": "TH5002", "hazard": "flood", "level": "NORMAL"},
        {"area_code": "TH5701", "hazard": "flood", "level": "WATCH"},
    ])
    answer = HazardAgent(gazetteer, tools).ask("สรุปน้ำท่วมทั้งประเทศ", NOW)
    assert answer.intent == "overview"
    assert "ปกติ 1 อำเภอ" in answer.text and "เฝ้าระวัง 1 อำเภอ" in answer.text
    assert "เตือนภัย 1 อำเภอ" in answer.text and tools.calls == ["all"]


def test_ranking_orders_provinces_by_worst_district_level(gazetteer):
    tools = NationalTools([
        {"area_code": "TH5001", "hazard": "flood", "level": "WARNING"},
        {"area_code": "TH5002", "hazard": "flood", "level": "CRITICAL"},
        {"area_code": "TH5701", "hazard": "flood", "level": "WARNING"},
        {"area_code": "TH5702", "hazard": "flood", "level": "WARNING"},
    ])
    answer = HazardAgent(gazetteer, tools).ask("จังหวัดไหนเสี่ยงน้ำท่วมมากที่สุด", NOW)
    assert answer.intent == "ranking"
    assert answer.text.index("จ.เชียงใหม่") < answer.text.index("จ.เชียงราย")


def test_region_hazard_question_uses_region_filter(gazetteer):
    u = understand("ภาคใต้มีคลื่นลมแรงไหม", gazetteer, NOW)
    assert u.intent == "overview" and u.regions == ["ภาคใต้"]
    tools = NationalTools([{"area_code": "TH8001", "hazard": "marine", "level": "WATCH"},
                           {"area_code": "TH5001", "hazard": "marine", "level": "WARNING"}])
    answer = HazardAgent(gazetteer, tools).ask("ภาคใต้มีคลื่นลมแรงไหม", NOW)
    assert "ภาพรวมภาคใต้" in answer.text and "เตือนภัย 0 อำเภอ" in answer.text


def test_empty_decisions_return_a_clear_no_data_answer(gazetteer):
    answer = HazardAgent(gazetteer, NationalTools([])).ask("สรุปน้ำท่วมทั้งประเทศ", NOW)
    assert "ยังไม่มีผลประเมิน" in answer.text


def test_overview_distinguishes_missing_hazard_data_from_zero_risk(gazetteer):
    tools = NationalTools([
        {"area_code": "TH5001", "hazard": "flood", "level": "WATCH"},
    ])
    answer = HazardAgent(gazetteer, tools).ask("ภาพรวมทั้งประเทศวันนี้", NOW)

    assert "น้ำท่วม: ปกติ 0 อำเภอ" in answer.text
    assert "ฝุ่น PM2.5: ไม่มีผลประเมินรายอำเภอ" in answer.text
    assert "ฝุ่น PM2.5: ปกติ 0 อำเภอ" not in answer.text
